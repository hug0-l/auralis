"""
aurserver.ws — live preview streaming over a WebSocket.

Protocol
--------
The client sends JSON control messages and the server answers with binary
frames of interleaved little-endian int16 PCM.

    client -> {"type": "configure", "asset_id": "...", "chain": [...],
               "start": 0.0, "end": 2.0, "realtime": true,
               "profile": "fast"}
    server -> {"type": "ready", "sample_rate": 48000, "channels": 2, ...}
    server -> {"type": "profile", "profile": "fast", "changed": 2, ...}
    server -> <binary frame>   (512 samples per channel)
    server -> <binary frame>
    server -> {"type": "end", "frames": 96000, "blocks": 94}

The chain may be changed while the stream runs.  The change is taken up at
the next block boundary and is cross-faded in, so the preview does not click
when the user moves a parameter:

    client -> {"type": "param", "index": 0, "name": "Gain", "value": 6.0}
    client -> {"type": "configure", "chain": [...]}
    client -> {"type": "bypass", "index": 0, "value": true}
    server -> {"type": "applied", "kind": "param", "index": 0, ...}

``{"type": "stop"}`` aborts an in-flight stream; the server answers with
``{"type": "stopped"}``.  Errors are reported as
``{"type": "error", "message": "..."}``.  A client that does not read fast
enough is told what it missed: ``{"type": "dropout", "frames": 1024}``.

A ``configure`` that arrives while the stream runs replaces the chain only:
the asset and the range stay as they are.

Each connection owns its own :class:`aurcore.api.Engine`; the session engine
that backs the editor is never touched, so a preview can never disturb the
chain the user is working on.

The two paths
-------------
The product has **two** render paths, and they are not the same path:

* the **file render** uses the *full* profile: the chain exactly as the
  user set it, with the finer transform and the multi-resolution analysis.
  A file has no deadline, so the file gets the fineness;
* the **preview** uses the *fast* profile by default: the same chain with
  the cheap setting of every parameter that only decides *how* the work is
  done.  The artistic parameters are never touched, so the preview does the
  same work as the file; it only does it with a smaller transform.

The preview is the path that has to be honest, because it is not the truth:
the file is.  Every preview therefore says which path it is on and what the
path changed::

    {"type": "ready", "path": "preview", "profile": "fast",
     "profile_label": "preview: fast path",
     "profile_report": {"profile": "fast", "changed": 2, "modules": [...]},
     "latency_samples": 3168, "latency_ms": 66.0, ...}

The ``profile_report`` names every parameter the fast path moved and gives
both values: the one the file gets and the one the preview gets.  The
interface can therefore show "preview: fast path" and, if it wants to, the
knobs that are on their cheap setting.

A client that wants to hear exactly what the file will get asks for the
full profile (``"profile": "full"``); the preview then says so.

The real time rules
-------------------
The DSP runs in a worker thread of its own, so a preview never blocks the
interface, and the rendered audio waits for the socket in a bounded queue, so
a client that stops reading cannot make the server grow.  In ``realtime``
mode the worker holds every block back until the slot it belongs to in the
audio timeline, so the preview arrives at the speed it is played.

Measured on the development machine (48 kHz, stereo, 512 frames per block):
the blocks arrive every 10.7 ms (93.75 blocks a second, the rate the audio
timeline asks for), a parameter change is heard 11.1 ms after the client
sent it, and the audio on the wire is bit identical to the output of the
chain the preview runs.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from fastapi import APIRouter
from starlette.websockets import WebSocket, WebSocketDisconnect

from aurcore import registry
from aurcore.api import Engine, create_engine
from aurcore.serial import typed_value as tv

from . import audio

log = logging.getLogger(__name__)

#: Hard limit on the length of one preview stream.
MAX_STREAM_SECONDS = 30.0
#: The block the preview renders in, per profile.  The fast path uses a
#: small block, so a change is heard in a few milliseconds instead of one
#: block period; the full configuration is expensive, so it is given big
#: blocks, where the cost of an analysis is amortised over a lot of audio.
#: The fast block is the smallest one at which the heaviest product chain
#: — the dialogue chain with the Spectral De-Noise in it — still fits into
#: the realtime budget with room to spare.
PROFILE_BLOCK_SIZE: dict[str, int] = {
    audio.PROFILE_FAST: 512,
    audio.PROFILE_FULL: 1024,
}
#: The block the preview renders in by default (the fast path).
BLOCK_SIZE = PROFILE_BLOCK_SIZE[audio.PROFILE_FAST]
#: The profile the preview uses unless the client asks for another one.
PREVIEW_PROFILE = audio.PROFILE_FAST
#: Wall-clock budget for one stream, including processing time.
WALL_CLOCK_TIMEOUT = 60.0
#: Audio rendered before the stream starts, so filters are already settled.
PRE_ROLL_SECONDS = 1.0
#: How long the server waits for the ``configure`` message.
CONFIG_TIMEOUT = 15.0
#: Default sample rate when the asset store does not say otherwise.
DEFAULT_SAMPLE_RATE = 48000
#: How many frames of audio may wait for the socket.  This is the whole
#: memory a single preview can use for audio that has not been sent yet.
QUEUE_FRAMES = 16384
#: Length of the cross-fade that brings a changed chain in, in samples.
FADE_SAMPLES = 256
#: How much recent audio a new chain hears before it is used, in samples.
WARM_UP_SAMPLES = 4096
#: The name of the worker thread, so a leak is easy to see.
WORKER_THREAD_PREFIX = "auralis-preview"

router = APIRouter(tags=["stream"])


# ---------------------------------------------------------------------------
# chain construction
# ---------------------------------------------------------------------------
_MODULES_LOADED = False


def ensure_modules_loaded() -> None:
    """Make sure the module registry is populated.

    Importing :mod:`aurcore.modules` registers every module class.  The import
    is done defensively: one module that fails to import must not hide all
    the others from the registry.
    """
    global _MODULES_LOADED
    if _MODULES_LOADED:
        return
    _MODULES_LOADED = True
    try:
        import aurcore.modules  # noqa: F401  (importing registers the modules)
        return
    except Exception as exc:
        log.warning("a module failed to import: %s", exc)
    import importlib
    import pkgutil

    import aurcore

    folder = Path(aurcore.__file__).resolve().parent / "modules"
    for _finder, name, _ispkg in pkgutil.iter_modules([str(folder)]):
        if name.startswith("_"):
            continue
        try:
            importlib.import_module(f"aurcore.modules.{name}")
        except Exception as exc:
            log.warning("module %s could not be imported: %s", name, exc)


def build_chain_engine(chain: Iterable[dict] | None, sample_rate: int, channels: int = 2) -> Engine:
    """Build an engine from a JSON chain description.

    Each step is ``{"code": ..., "enabled": bool, "params": {...},
    "state": {...}}``.  Parameter values may be raw numbers or typed-value
    envelopes; both are accepted.

    Raises ``KeyError`` for an unknown module code.
    """
    ensure_modules_loaded()
    engine = create_engine(sample_rate, channels)
    for step in chain or []:
        if not isinstance(step, dict):
            continue
        code = str(step.get("code") or "").strip()
        if not code:
            continue
        if not registry.has(code):
            raise KeyError(f"unknown module code {code!r}")
        handle = engine.add_module(code)
        params = step.get("params") or {}
        if isinstance(params, dict):
            handle.module.params.from_dict(tv.unwrap_all(params))
        state = step.get("state")
        if state is not None:
            blob = tv.unwrap(state, "")
            if isinstance(blob, (bytes, bytearray)):
                handle.module.load_state_base64(
                    tv.unwrap({"Type": "Base64", "Value": blob}, "") or ""
                )
            elif isinstance(blob, str) and blob:
                handle.module.load_state_base64(blob)
        engine.set_bypass(handle, not bool(step.get("enabled", True)))
    return engine


def build_profile_engine(
    chain: Iterable[dict] | None,
    sample_rate: int,
    channels: int = 2,
    profile: str = audio.PROFILE_FULL,
) -> tuple[Engine, dict[str, Any]]:
    """Build an engine in a profile and report what the profile changed.

    The engine is built with the configuration the user set, and the profile
    is applied to it afterwards.  The report names every parameter the
    profile moved and gives both values — the one the file gets and the one
    this path gets — so the interface can say what it is hearing.

    The engine also gets the **mode** of the preview: a preview is played
    while the user waits for it, so every module of it has to keep inside the
    budget of a real time block.  The mode is not the profile: a preview may
    run the full configuration and is still real time.
    """
    engine = build_chain_engine(chain, sample_rate, channels)
    report = audio.apply_profile(engine, profile)
    audio.apply_processing_mode(engine, audio.PATH_PREVIEW)
    return engine, report


def _profile_of(value: Any) -> str:
    """The profile a client asked for, or the default one."""
    if value is None:
        return PREVIEW_PROFILE
    name = str(value).strip().lower()
    if not name:
        return PREVIEW_PROFILE
    if name not in (audio.PROFILE_FAST, audio.PROFILE_FULL):
        raise ValueError(f"unknown profile {name!r}")
    return name


def validate_chain(chain: Any) -> list[dict]:
    """Check the shape of a chain description and return it normalised.

    A chain that cannot be built is refused here, so the client never gets
    dry audio where it asked for a processed one.
    """
    if chain is None:
        return []
    if not isinstance(chain, list):
        raise ValueError("the chain must be a list of steps")
    steps: list[dict] = []
    for position, item in enumerate(chain):
        if isinstance(item, str):
            steps.append({"code": item, "enabled": True, "params": {}, "state": None})
            continue
        if not isinstance(item, dict):
            raise ValueError(f"chain step {position} must be an object")
        code = str(item.get("code") or item.get("module") or "").strip()
        if not code:
            raise ValueError(f"chain step {position} has no module code")
        params = item.get("params") or {}
        if not isinstance(params, dict):
            raise ValueError(f"chain step {position} has unusable parameters")
        steps.append(
            {
                "code": code,
                "enabled": bool(item.get("enabled", True)),
                "params": dict(params),
                "state": item.get("state"),
            }
        )
    return steps


# ---------------------------------------------------------------------------
# asset resolution
# ---------------------------------------------------------------------------
def _coerce_audio(entry: Any, sample_rate: int) -> tuple[np.ndarray, int]:
    """Turn an asset-store entry into ``(samples, sample_rate)``.

    Accepted shapes: an array ``(n, ch)``, a tuple ``(array, sample_rate)``,
    a mapping with ``data``/``sample_rate`` keys, a path to an audio file and
    an asset record that carries ``data``/``sample_rate``/``path`` attributes.
    """
    if isinstance(entry, np.ndarray):
        return _as_2d(entry), sample_rate
    if isinstance(entry, (str, Path)):
        return _read_file(Path(entry))
    if isinstance(entry, dict):
        data = None
        for key in ("data", "samples", "x", "audio", "buffer"):
            if key in entry:
                data = entry[key]
                break
        sr = entry.get("sample_rate", entry.get("sr", sample_rate))
        if data is None:
            path = entry.get("path")
            if path:
                samples, file_sr = _read_file(Path(path))
                return samples, int(sr or file_sr)
            raise ValueError("asset entry has no audio data")
        return _coerce_audio(data, int(sr))
    if isinstance(entry, (tuple, list)) and len(entry) == 2:
        data, sr = entry
        return _coerce_audio(data, int(sr))
    # an asset record: decoded buffer first, the file on disk second
    data = getattr(entry, "data", None)
    sr = getattr(entry, "sample_rate", None) or getattr(entry, "sr", None)
    path = getattr(entry, "path", None)
    if data is not None:
        return _as_2d(data), int(sr or sample_rate)
    if path:
        samples, file_sr = _read_file(Path(path))
        return samples, int(sr or file_sr)
    raise ValueError(f"unsupported asset entry of type {type(entry).__name__}")


def _as_2d(x: np.ndarray) -> np.ndarray:
    arr = np.asarray(x, dtype=np.float64)
    if arr.ndim == 1:
        arr = arr[:, None]
    elif arr.ndim > 2:
        arr = arr.reshape(arr.shape[0], -1)
    return arr


def _read_file(path: Path) -> tuple[np.ndarray, int]:
    import soundfile as sf

    data, sr = sf.read(str(path), always_2d=True, dtype="float64")
    return _as_2d(data), int(sr)


def _store_lookup(store: Any, asset_id: Any) -> Any:
    """Look an asset up in a mapping or in a store with a ``get`` method."""
    try:
        return store[asset_id]
    except (KeyError, TypeError, IndexError):
        pass
    getter = getattr(store, "get", None)
    if callable(getter):
        try:
            return getter(asset_id)
        except (KeyError, TypeError, IndexError):
            return None
    return None


def resolve_audio(app: Any, asset_id: Any) -> tuple[np.ndarray, int]:
    """Resolve an asset id to audio.

    Resolution order:

    1. a resolver callable on the application state — ``audio_resolver``,
       ``get_asset`` or ``resolve_asset`` — called with the asset id;
    2. a store on the application state — ``assets`` or ``asset_store`` — looked
       up by key or through its ``get`` method;
    3. a callable on the application state — ``current_asset`` or
       ``get_current_asset`` — for the asset the session is working on;
    4. the asset id itself, when it points at a readable audio file.

    An entry may be an array ``(n, ch)``, a tuple ``(array, sample_rate)``, a
    mapping with ``data``/``sample_rate`` keys, a path to an audio file or an
    asset record that carries ``data``/``sample_rate``/``path``.
    """
    state = getattr(app, "state", None)
    sample_rate = int(getattr(state, "sample_rate", DEFAULT_SAMPLE_RATE) or DEFAULT_SAMPLE_RATE)

    for attribute in ("audio_resolver", "get_asset", "resolve_asset"):
        resolver = getattr(state, attribute, None) if state is not None else None
        if callable(resolver):
            entry = resolver(asset_id)
            if entry is not None:
                return _coerce_audio(entry, sample_rate)

    for attribute in ("assets", "asset_store"):
        store = getattr(state, attribute, None) if state is not None else None
        if store is None:
            continue
        entry = _store_lookup(store, asset_id)
        if entry is not None:
            return _coerce_audio(entry, sample_rate)

    for attribute in ("current_asset", "get_current_asset"):
        current = getattr(state, attribute, None) if state is not None else None
        if callable(current):
            entry = current()
            if entry is not None:
                return _coerce_audio(entry, sample_rate)

    candidate = Path(str(asset_id))
    if candidate.is_file():
        return _read_file(candidate)

    raise KeyError(f"unknown asset {asset_id!r}")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _fit_length(x: np.ndarray, frames: int) -> np.ndarray:
    """Force a processed block back to the expected frame count."""
    arr = _as_2d(x)
    if arr.shape[0] == frames:
        return arr
    if arr.shape[0] > frames:
        return arr[:frames]
    pad = np.zeros((frames - arr.shape[0], arr.shape[1]), dtype=np.float64)
    return np.concatenate([arr, pad], axis=0)


def to_int16(x: np.ndarray) -> bytes:
    """Convert a float block to interleaved little-endian int16 bytes.

    The negative side of the scale reaches -32768 and the positive side
    32767, so a signal at full scale survives the trip unchanged; the old
    scaling lost one step on the negative side.
    """
    arr = np.array(x, dtype=np.float64, copy=True)
    np.clip(arr, -1.0, 1.0, out=arr)
    scaled = np.where(arr < 0.0, arr * 32768.0, arr * 32767.0)
    return np.ascontiguousarray(np.rint(scaled).astype("<i2")).tobytes()


def _int16_payload(x: np.ndarray) -> tuple[bytes, int]:
    """Convert a block to int16 and report how many samples were clipped."""
    arr = np.asarray(x, dtype=np.float64)
    clipped = 0
    if arr.size:
        clipped = int(np.count_nonzero(np.abs(arr) > 1.0))
    return to_int16(arr), clipped


def _round_range(start: float, end: float, duration: float) -> tuple[float, float]:
    """Clamp a requested time range to the asset and to the stream limit."""
    start = max(0.0, float(start or 0.0))
    if not end or end <= 0.0:
        end = duration
    end = min(float(end), duration)
    if end - start > MAX_STREAM_SECONDS:
        end = start + MAX_STREAM_SECONDS
    return start, end


def _crossfade(old: np.ndarray, new: np.ndarray, start: int, length: int) -> np.ndarray:
    """Mix the old and the new output over a short ramp.

    The ramp runs from 0 to 1 over ``length`` samples, so the first sample is
    the old signal and the last one is the new signal: the change cannot
    click.
    """
    out = np.array(new, dtype=np.float64, copy=True)
    count = min(max(length - start, 0), out.shape[0])
    if count <= 0 or length <= 1:
        return out
    positions = np.arange(start, start + count, dtype=np.float64)
    ramp = np.clip(positions / float(length - 1), 0.0, 1.0).reshape((count, 1))
    out[:count] = old[:count] * (1.0 - ramp) + new[:count] * ramp
    return out


# ---------------------------------------------------------------------------
# the control channel
# ---------------------------------------------------------------------------
class _Control:
    """The hand-off of the control messages from the reader to the worker.

    The reader task runs in the event loop and the render worker runs in a
    thread of its own, so the messages travel through a deque behind a lock.
    The asyncio event is only ever set from the event loop thread.

    The queue is bounded: a client that floods the socket with control
    messages makes the server drop the oldest ones instead of growing.
    """

    #: How many control messages may wait for the worker.
    LIMIT = 64

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._messages: deque[dict] = deque()
        self._wake = asyncio.Event()
        self._dropped = 0

    def push(self, message: dict) -> None:
        """Hand a control message over to the worker."""
        with self._lock:
            self._messages.append(message)
            while len(self._messages) > self.LIMIT:
                self._messages.popleft()
                self._dropped += 1
        self._wake.set()

    @property
    def dropped(self) -> int:
        """How many control messages were dropped because of the bound."""
        with self._lock:
            return self._dropped

    def take(self) -> list[dict]:
        """Take every pending control message (called by the worker)."""
        with self._lock:
            out = list(self._messages)
            self._messages.clear()
        return out

    def first_configure(self) -> dict | None:
        """Take the first ``configure`` message, if there is one."""
        with self._lock:
            for index, message in enumerate(self._messages):
                if str(message.get("type") or "").lower() == "configure":
                    del self._messages[index]
                    return message
        return None

    async def wait(self, timeout: float) -> bool:
        """Wait for a control message to arrive."""
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return False
        self._wake.clear()
        return True


class _Outbox:
    """The bounded hand-off of rendered audio to the sender.

    The bound is on the audio that waits for the socket.  A client that stops
    reading makes the server drop the oldest audio — and tell the client how
    much it missed — instead of growing without limit.
    """

    def __init__(self, max_blocks: int, bytes_per_frame: int) -> None:
        self._limit = max(1, int(max_blocks)) * max(1, int(bytes_per_frame))
        self._items: deque[Any] = deque()
        self._audio_bytes = 0
        self._frames_per_byte = 0
        self._wake = asyncio.Event()
        self._closed = False

    def set_frames_per_byte(self, frames_per_byte: int) -> None:
        """Tell the outbox how many frames one byte of audio carries."""
        self._frames_per_byte = max(1, int(frames_per_byte))

    def put(self, item: Any) -> int:
        """Append an item and return the number of audio bytes dropped."""
        size = len(item) if isinstance(item, bytes) else 0
        dropped = 0
        if size:
            while self._audio_bytes + size > self._limit and self._items:
                victim = self._items.popleft()
                if isinstance(victim, bytes):
                    self._audio_bytes -= len(victim)
                    dropped += len(victim)
                else:
                    # a notice is never dropped: it goes back in front
                    self._items.appendleft(victim)
                    break
        self._items.append(item)
        self._audio_bytes += size
        self._wake.set()
        return dropped

    def dropped_frames(self, byte_count: int) -> int:
        """Turn a number of dropped bytes into a number of dropped frames."""
        return int(byte_count) // max(1, self._frames_per_byte)

    async def get(self) -> Any:
        """Wait for the next item; ``None`` when the outbox is closed."""
        while True:
            if self._items:
                item = self._items.popleft()
                if isinstance(item, bytes):
                    self._audio_bytes -= len(item)
                return item
            if self._closed:
                return None
            self._wake.clear()
            await self._wake.wait()

    def close(self) -> None:
        """Close the outbox; the sender stops once it is empty."""
        self._closed = True
        self._wake.set()


# ---------------------------------------------------------------------------
# one preview stream
# ---------------------------------------------------------------------------
class _Preview:
    """One live preview stream.

    The class owns the engine, the worker thread that runs the DSP and the
    bounded queue that holds the audio on its way to the socket.  Everything
    that touches the engine runs in the worker thread; the event loop only
    moves bytes.

    The engine runs in the **fast** profile unless the client asks for the
    full one, and the class remembers what the profile changed so that the
    client can be told: the preview is not the file, and it says so.
    """

    def __init__(self, websocket: WebSocket, app: Any, config: dict,
                 control: _Control, stop: asyncio.Event) -> None:
        self.websocket = websocket
        self.app = app
        self.control = control
        self.stop = stop
        self.chain = validate_chain(config.get("chain"))
        self.profile = _profile_of(config.get("profile"))
        self.block = PROFILE_BLOCK_SIZE.get(self.profile, BLOCK_SIZE)
        asset_id = config.get("asset_id") or config.get("path")
        self.samples, self.sample_rate = resolve_audio(app, asset_id)
        self.channels = int(self.samples.shape[1])
        try:
            self.engine, self.profile_report = build_profile_engine(
                self.chain, self.sample_rate, self.channels, self.profile
            )
        except KeyError as exc:
            raise ValueError(str(exc.args[0] if exc.args else exc)) from exc

        duration = self.samples.shape[0] / float(self.sample_rate)
        self.start, self.end = _round_range(
            config.get("start", 0.0), config.get("end", 0.0), duration
        )
        self.first = int(round(self.start * self.sample_rate))
        self.last = int(round(self.end * self.sample_rate))
        self.total = max(0, self.last - self.first)
        if self.total <= 0:
            raise ValueError("the requested range is empty")
        self.blocks = (self.total + self.block - 1) // self.block
        self.realtime = bool(config.get("realtime", False))

        self._outbox = _Outbox(
            max(1, QUEUE_FRAMES // self.block), 2 * self.channels * self.block
        )
        self._outbox.set_frames_per_byte(2 * self.channels)
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=WORKER_THREAD_PREFIX
        )
        self._sender: asyncio.Task | None = None
        self._sent = 0
        self._dropped_frames = 0
        self._dropped_blocks = 0
        self._clipped = 0
        # worker state
        self._old_engine: Engine | None = None
        self._fade_start = FADE_SAMPLES
        self._recent: np.ndarray | None = None
        self._warmed = False
        self._paced = False
        self._deadline = time.perf_counter()
        self._block_seconds = self.block / float(self.sample_rate)

    # -- the worker thread ------------------------------------------------
    def _pace(self) -> None:
        """Hold the block back until the slot it belongs to.

        The deadline is a running total: a worker that was interrupted makes
        the next block a little early instead of leaving a hole in the audio.
        """
        now = time.perf_counter()
        if not self._paced:
            self._paced = True
            self._deadline = now
        delay = self._deadline - now
        if delay > 0.0:
            time.sleep(delay)
        self._deadline += self._block_seconds

    def _pre_roll(self) -> None:
        """Settle the module state before the first block is sent."""
        preroll = int(PRE_ROLL_SECONDS * self.sample_rate)
        if preroll <= 0 or self.first <= 0:
            return
        begin = max(0, self.first - preroll)
        if begin >= self.first:
            return
        with contextlib.suppress(Exception):
            self.engine.process(np.array(self.samples[begin:self.first], dtype=np.float64))

    def _switch(self, engine: Engine) -> None:
        """Bring a new chain in without a click.

        The old chain keeps running for the length of the cross-fade, and the
        new one hears the audio it has just missed, so both are in step when
        the fade starts.
        """
        if self._recent is not None and self._recent.shape[0]:
            with contextlib.suppress(Exception):
                engine.process(np.array(self._recent, dtype=np.float64))
        self._old_engine = self.engine
        self.engine = engine
        self._fade_start = 0

    def _rebuild(self) -> Engine:
        """Build the engine again, in the profile of this preview.

        The chain keeps the values the user set — the file renders exactly
        these — and the profile is applied on top of them, so the report
        always says what this path does differently from the file.
        """
        engine, report = build_profile_engine(
            self.chain, self.sample_rate, self.channels, self.profile
        )
        self.profile_report = report
        return engine

    def _path_fields(self) -> dict[str, Any]:
        """What the client has to be told about the path it is hearing.

        The latency and the block size belong here and not only in the
        ``ready`` message: a client that switches the path in place needs
        the numbers of **the path it is now on**, and a number that belongs
        to the other path would be a lie.
        """
        latency = int(self.engine.latency_samples)
        return {
            "path": "preview",
            "profile": self.profile,
            "profile_label": audio.profile_label(self.profile),
            "profile_report": self.profile_report,
            # the mode the chain works in: the preview is real time, and the
            # client is told so
            "processing_mode": self.engine.processing_mode,
            "latency_samples": latency,
            "latency_ms": latency * 1000.0 / float(self.sample_rate),
            "block_size": self.block,
        }

    def _apply_param(self, message: dict) -> dict:
        """Set one parameter of the running chain."""
        index = int(message.get("index", 0))
        name = str(message.get("name") or "")
        if not name:
            raise ValueError("a parameter change needs a name")
        if not (0 <= index < len(self.chain)):
            raise ValueError(f"chain index {index} is out of range")
        step = self.engine.chain[index]
        if not step.module.params.has(name):
            raise ValueError(f"unknown parameter: {name}")
        value = step.module.params.desc(name).coerce(message.get("value"))
        # the running engine is left alone: it is the old side of the
        # cross-fade, so it must keep the value it had
        params = dict(self.chain[index].get("params") or {})
        params[name] = value
        self.chain[index]["params"] = params
        self._switch(self._rebuild())
        return {
            "type": "applied",
            "kind": "param",
            "index": index,
            "name": name,
            "value": value,
            **self._path_fields(),
        }

    def _apply_bypass(self, message: dict) -> dict:
        """Bypass or engage one module of the running chain."""
        index = int(message.get("index", 0))
        if not (0 <= index < len(self.chain)):
            raise ValueError(f"chain index {index} is out of range")
        bypass = bool(message.get("value", message.get("bypass", True)))
        self.chain[index]["enabled"] = not bypass
        self._switch(self._rebuild())
        return {
            "type": "applied",
            "kind": "bypass",
            "index": index,
            "value": bypass,
            **self._path_fields(),
        }

    def _apply_chain(self, message: dict) -> dict:
        """Replace the running chain.

        The message may carry a ``profile``: the client then asks for the
        other path, and the preview says so.
        """
        chain = validate_chain(message.get("chain"))
        if message.get("profile") is not None:
            self.profile = _profile_of(message.get("profile"))
        self.chain = chain
        self._switch(self._rebuild())
        return {
            "type": "applied",
            "kind": "configure",
            "modules": len(chain),
            **self._path_fields(),
        }

    def _apply_changes(self) -> list[dict]:
        """Take up the pending control messages (worker thread)."""
        notices: list[dict] = []
        for message in self.control.take():
            kind = str(message.get("type") or "").lower()
            try:
                if kind == "param":
                    notices.append(self._apply_param(message))
                elif kind == "configure":
                    notices.append(self._apply_chain(message))
                elif kind == "bypass":
                    notices.append(self._apply_bypass(message))
            except Exception as exc:
                detail = str(exc.args[0] if exc.args else exc)
                log.warning("the preview refused a change: %s", detail)
                notices.append({"type": "error", "message": detail})
        return notices

    def _render_block(self, block: np.ndarray) -> tuple[np.ndarray, list[dict]]:
        """Render one block (worker thread)."""
        if not self._warmed:
            self._warmed = True
            self._pre_roll()
        if self.realtime:
            self._pace()
        notices = self._apply_changes()
        data = np.array(block, dtype=np.float64)
        failed = ""
        if self._old_engine is not None:
            old, bad = self._process(self._old_engine, data)
            new, bad2 = self._process(self.engine, data)
            failed = bad or bad2
            if failed:
                self._old_engine = None
                out = new
            else:
                out = _crossfade(old, new, self._fade_start, FADE_SAMPLES)
                self._old_engine = None
        else:
            out, failed = self._process(self.engine, data)
        if failed:
            # a module that fails must not take the preview down, but the
            # client has to hear about it: the block it gets is the dry one
            log.warning("a module failed during the preview: %s", failed)
            notices.append({"type": "warning", "message": f"a module failed: {failed}"})
        self._recent = data[-WARM_UP_SAMPLES:] if data.shape[0] else data
        return out, notices

    def _process(self, engine: Engine, data: np.ndarray) -> tuple[np.ndarray, str]:
        """Run one engine over a block; a failure gives the dry block back."""
        try:
            return _fit_length(engine.process(data.copy()), data.shape[0]), ""
        except Exception as exc:  # pragma: no cover - modules must not raise
            return data, f"{type(exc).__name__}: {exc}"

    # -- the event loop ----------------------------------------------------
    async def _run_block(self, block: np.ndarray) -> tuple[np.ndarray, list[dict]]:
        """Render a block in the worker thread, off the interface's thread."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._render_block, block)

    async def _send_loop(self) -> None:
        """Send the queued audio and notices to the client."""
        try:
            while True:
                item = await self._outbox.get()
                if item is None:
                    break
                if isinstance(item, dict):
                    await _send_json(self.websocket, item)
                else:
                    await self.websocket.send_bytes(item)
        except Exception:
            # the client is gone: the render loop has to stop as well
            self.stop.set()

    async def _render_loop(self) -> None:
        """Produce the audio blocks and hand them to the outbox."""
        position = self.first
        deadline = time.monotonic() + WALL_CLOCK_TIMEOUT
        while position < self.last:
            if self.stop.is_set():
                self._outbox.put({"type": "stopped", "frames": self._sent})
                return
            if time.monotonic() > deadline:
                self._outbox.put(
                    {"type": "error", "message": "the preview exceeded its time budget"}
                )
                return
            block = self.samples[position : min(position + self.block, self.last)]
            processed, notices = await self._run_block(block)
            payload, clipped = _int16_payload(processed)
            self._clipped += clipped
            dropped = self._outbox.put(payload)
            if dropped:
                frames = self._outbox.dropped_frames(dropped)
                self._dropped_frames += frames
                self._dropped_blocks += 1
                self._outbox.put(
                    {"type": "dropout", "frames": frames, "blocks": 1}
                )
            for notice in notices:
                self._outbox.put(notice)
            self._sent += int(block.shape[0])
            position += int(block.shape[0])
        self._outbox.put(
            {
                "type": "end",
                "frames": self._sent,
                "blocks": self.blocks,
                "dropped_frames": self._dropped_frames,
                "clipped": self._clipped,
            }
        )

    async def _send_ready(self) -> None:
        """Tell the client what the stream will look like.

        The message says which path the client is hearing, what the path
        changed and what latency the path has.  A preview that runs a
        cheaper configuration than the file has to say so, and it says so
        here, in the first message of the stream.
        """
        latency = int(self.engine.latency_samples)
        await _send_json(
            self.websocket,
            {
                "type": "ready",
                "sample_rate": self.sample_rate,
                "channels": self.channels,
                "frames": self.total,
                "blocks": self.blocks,
                "duration": self.total / float(self.sample_rate),
                "block_size": self.block,
                "latency_samples": latency,
                "latency_ms": latency * 1000.0 / float(self.sample_rate),
                "start": self.start,
                "end": self.end,
                "realtime": self.realtime,
                **self._path_fields(),
            },
        )

    async def _send_profile(self) -> None:
        """Tell the client which path it is hearing, in its own message.

        The ``ready`` message carries the same information.  This message is
        there for a client that watches the notices: it is the one that
        says "you are hearing the fast path, and these are the knobs that
        are on their cheap setting".
        """
        await _send_json(
            self.websocket,
            {
                "type": "profile",
                "latency_samples": int(self.engine.latency_samples),
                "latency_ms": int(self.engine.latency_samples)
                * 1000.0
                / float(self.sample_rate),
                **self._path_fields(),
            },
        )

    async def run(self) -> None:
        """Run the stream until the end, a stop message or a dead client."""
        await self._send_ready()
        await self._send_profile()
        self._sender = asyncio.create_task(self._send_loop())
        try:
            await self._render_loop()
        finally:
            self._outbox.close()
            if self._sender is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await self._sender

    async def close(self) -> None:
        """Stop the worker and release everything the preview holds."""
        self.stop.set()
        if self._sender is not None:
            self._sender.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._sender
            self._sender = None
        self._executor.shutdown(wait=False, cancel_futures=True)


# ---------------------------------------------------------------------------
# websocket route
# ---------------------------------------------------------------------------
async def _reader(websocket: WebSocket, control: _Control, stop: asyncio.Event) -> None:
    """Read control messages until the socket closes."""
    try:
        while True:
            message = await websocket.receive()
            kind = message.get("type")
            if kind == "websocket.disconnect":
                break
            if kind != "websocket.receive":
                continue
            text = message.get("text")
            if text is None:
                # a binary frame is not a control message
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            if str(payload.get("type") or "").lower() == "stop":
                stop.set()
            control.push(payload)
    except WebSocketDisconnect:
        pass
    except Exception:  # pragma: no cover - the socket went away
        pass
    finally:
        stop.set()


async def _send_json(websocket: WebSocket, payload: dict) -> None:
    try:
        await websocket.send_json(payload)
    except Exception:  # pragma: no cover - the socket went away
        pass


async def _await_config(control: _Control, stop: asyncio.Event) -> dict | None:
    """Wait for the ``configure`` message."""
    deadline = time.monotonic() + CONFIG_TIMEOUT
    while not stop.is_set():
        message = control.first_configure()
        if message is not None:
            return message
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            return None
        await control.wait(remaining)
    return None


@router.websocket("/ws/stream")
async def stream_preview(websocket: WebSocket) -> None:
    """Stream a processed preview of an asset over the WebSocket."""
    await websocket.accept()
    control = _Control()
    stop = asyncio.Event()
    reader = asyncio.create_task(_reader(websocket, control, stop))
    preview: _Preview | None = None
    try:
        config = await _await_config(control, stop)
        if config is None:
            if not stop.is_set():
                await _send_json(
                    websocket, {"type": "error", "message": "no configure message received"}
                )
            with contextlib.suppress(Exception):
                await websocket.close(code=1000)
            return
        preview = _Preview(websocket, websocket.app, config, control, stop)
        await preview.run()
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.warning("preview stream failed: %s", exc)
        detail = str(exc.args[0]) if exc.args else str(exc)
        await _send_json(websocket, {"type": "error", "message": detail})
    finally:
        if preview is not None:
            await preview.close()
        stop.set()
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await reader


# ---------------------------------------------------------------------------
# wiring
# ---------------------------------------------------------------------------
def register(app) -> None:
    """Attach the preview WebSocket route to an application."""
    app.include_router(router)
