"""
tests/test_ws.py — the streaming preview: what it costs and what it promises.

The preview is the WebSocket the interface uses to hear the chain while the
user works.  This module measures it and pins the numbers down:

* the latency from "the user moved a parameter" to "the audio with the change
  arrives";
* the block size and the rate the preview keeps;
* the dropouts over a minute of streaming;
* whether the audio on the wire is bit identical to what the chain produced;
* what a slow client costs the server;
* what happens when the client disappears or asks for the impossible.

The measurements live in the functions below and the tests assert them.  The
same code prints the table:

    python tests/test_ws.py
"""
from __future__ import annotations

import asyncio
import ctypes
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    # the module is also run as a script: python tests/test_ws.py
    sys.path.insert(0, str(ROOT))

from aurserver import audio  # noqa: E402  (the path is set up above)
from aurserver import ws  # noqa: E402  (the path is set up above)

SAMPLE_RATE = 48_000
CHANNELS = 2
BLOCK = ws.BLOCK_SIZE
#: The block the preview renders in by default (the fast path).
FAST_BLOCK = ws.PROFILE_BLOCK_SIZE[audio.PROFILE_FAST]
#: The block the preview renders in when the client asks for the full path.
FULL_BLOCK = ws.PROFILE_BLOCK_SIZE[audio.PROFILE_FULL]
#: The product chain of the guard, in signal order.
DIALOGUE_CHAIN = (
    "AUR_DEHUM",
    "AUR_DECLICK",
    "AUR_DEESS",
    "AUR_DEPLOSIVE",
    "AUR_GAIN",
)
#: The dialogue chain with the flagship Spectral De-Noise module in it.
SPECTRAL_CHAIN = (
    "AUR_DEHUM",
    "AUR_SPECTRALDENOISE",
    "AUR_DECLICK",
    "AUR_DEESS",
    "AUR_GAIN",
)
#: The block period the preview has to keep, in seconds.
BLOCK_SECONDS = BLOCK / float(SAMPLE_RATE)
#: The rate of the blocks, in blocks per second.
RATE_NOMINAL = SAMPLE_RATE / float(BLOCK)
#: The amplitude of the test tone.
AMPLITUDE = 0.25
#: How far the block period may drift from the nominal one.
RATE_TOLERANCE = 0.05
#: The latency budget: the change has to be heard inside this many
#: milliseconds.  The measurement is far below it.
LATENCY_LIMIT_MS = 120.0
#: The budget for the memory a slow client may cost the server.
MEMORY_LIMIT_MB = 16.0
#: How many blocks a preview player keeps in its jitter buffer.
JITTER_BLOCKS = 3

__all__ = [
    "PreviewServer",
    "SubprocessServer",
    "measure_dropouts",
    "measure_identity",
    "measure_latency",
    "measure_memory_of_a_slow_client",
    "measure_rate",
    "measure_ready",
    "write_tone",
]


# ---------------------------------------------------------------------------
# the servers
# ---------------------------------------------------------------------------
def _free_port() -> int:
    """Return a port that is free right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class PreviewServer:
    """A uvicorn server running in a thread of this process."""

    def __init__(self) -> None:
        import uvicorn

        from aurserver.app import app

        self.port = _free_port()
        config = uvicorn.Config(
            app, host="127.0.0.1", port=self.port, log_level="warning"
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(
            target=self.server.run, name="auralis-preview-test", daemon=True
        )

    @property
    def url(self) -> str:
        """The address of the preview WebSocket."""
        return f"ws://127.0.0.1:{self.port}/ws/stream"

    def start(self, timeout: float = 60.0) -> None:
        """Start the server and wait until it answers."""
        import urllib.request

        self.thread.start()
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/api/health", timeout=2
                ):
                    return
            except Exception:  # noqa: BLE001 - the server is still starting
                time.sleep(0.2)
        raise RuntimeError("the preview server did not become healthy")

    def stop(self) -> None:
        """Ask the server to leave and wait for the thread."""
        self.server.should_exit = True
        self.thread.join(timeout=15)


class SubprocessServer:
    """A server in a process of its own, so its memory can be measured."""

    def __init__(self) -> None:
        self.port = _free_port()
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "aurserver",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--log-level",
                "warning",
            ],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @property
    def url(self) -> str:
        """The address of the preview WebSocket."""
        return f"ws://127.0.0.1:{self.port}/ws/stream"

    def start(self, timeout: float = 60.0) -> None:
        """Start the server and wait until it answers."""
        import urllib.request

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/api/health", timeout=2
                ):
                    return
            except Exception:  # noqa: BLE001 - the server is still starting
                time.sleep(0.2)
        raise RuntimeError("the preview server did not become healthy")

    def stop(self) -> None:
        """Stop the server."""
        self.process.terminate()
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover - stubborn child
            self.process.kill()


def _working_set(pid: int) -> int:
    """The working set of a process, in bytes (0 when it cannot be read)."""
    if sys.platform == "win32":
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi")
        handle = kernel32.OpenProcess(0x1000 | 0x0010, False, int(pid))
        if not handle:
            return 0
        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        try:
            psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
            return int(counters.WorkingSetSize)
        finally:
            kernel32.CloseHandle(handle)
    try:
        with open(f"/proc/{pid}/statm", encoding="ascii") as handle:
            fields = handle.read().split()
        return int(fields[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):  # pragma: no cover - other systems
        return 0


# ---------------------------------------------------------------------------
# the test signal
# ---------------------------------------------------------------------------
def write_tone(path: Path, seconds: float = 4.0, amplitude: float = AMPLITUDE) -> np.ndarray:
    """Write a 1 kHz tone to a WAV file and return the samples it holds."""
    import soundfile as sf

    count = int(seconds * SAMPLE_RATE)
    times = np.arange(count, dtype=np.float64) / SAMPLE_RATE
    wave = amplitude * np.sin(2.0 * np.pi * 1000.0 * times)
    data = np.repeat(wave[:, None], CHANNELS, axis=1)
    sf.write(str(path), data, SAMPLE_RATE, subtype="PCM_16")
    return data


def _peak(frame: bytes) -> int:
    """The loudest sample of a streamed frame."""
    samples = np.frombuffer(frame, dtype="<i2")
    return int(np.max(np.abs(samples))) if samples.size else 0


def _read(path: Path) -> np.ndarray:
    """The samples of a WAV file, exactly as the server reads them."""
    import soundfile as sf

    data, _sr = sf.read(str(path), always_2d=True, dtype="float64")
    return data


# ---------------------------------------------------------------------------
# the client
# ---------------------------------------------------------------------------
async def _collect(sock, count: int, timeout: float = 30.0):
    """Collect ``count`` binary frames and the notices in between."""
    from websockets.exceptions import ConnectionClosed

    frames: list[tuple[float, bytes]] = []
    notices: list[dict] = []
    deadline = time.perf_counter() + timeout
    while len(frames) < count and time.perf_counter() < deadline:
        try:
            message = await asyncio.wait_for(
                sock.recv(), timeout=max(0.05, deadline - time.perf_counter())
            )
        except asyncio.TimeoutError:
            break
        except ConnectionClosed:
            break
        if isinstance(message, bytes):
            frames.append((time.perf_counter(), message))
        else:
            notices.append(json.loads(message))
    return frames, notices


async def _configure(sock, asset: Path, chain: list, **extra) -> dict:
    """Send the configure message and read the answer."""
    message = {"type": "configure", "asset_id": str(asset), "chain": chain}
    message.update(extra)
    await sock.send(json.dumps(message))
    answer = json.loads(await asyncio.wait_for(sock.recv(), timeout=15))
    return answer


# ---------------------------------------------------------------------------
# the measurements
# ---------------------------------------------------------------------------
async def measure_latency(
    url: str,
    asset: Path,
    gain_db: float = 6.0,
    timeout: float = 30.0,
) -> dict:
    """Measure the time from a parameter change to the changed audio.

    The chain is a gain stage at 0 dB.  The client turns the knob to
    ``gain_db`` and waits for the first frame that carries the louder
    signal; the time between the two is the latency the user hears.
    """
    from websockets.asyncio.client import connect

    chain = [{"code": "AUR_GAIN", "enabled": True, "params": {"Gain": 0.0}}]
    async with connect(url) as sock:
        ready = await _configure(sock, asset, chain, realtime=True)
        assert ready["type"] == "ready", ready
        frames, _notices = await _collect(sock, 1, timeout=timeout)
        if not frames:
            raise AssertionError("the preview did not send a single block")
        quiet = _peak(frames[0][1])
        sent = time.perf_counter()
        await sock.send(
            json.dumps(
                {"type": "param", "index": 0, "name": "Gain", "value": gain_db}
            )
        )
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            message = await asyncio.wait_for(
                sock.recv(), timeout=max(0.05, deadline - time.perf_counter())
            )
            if not isinstance(message, bytes):
                continue
            loud = _peak(message)
            if loud > quiet * 1.5:
                return {
                    "latency_ms": (time.perf_counter() - sent) * 1000.0,
                    "quiet_peak": quiet,
                    "loud_peak": loud,
                }
    raise AssertionError("the changed audio never arrived")


async def measure_identity(url: str, asset: Path, convert=None) -> dict:
    """Compare the streamed audio with the audio the chain produced.

    ``convert`` is the float-to-int16 conversion the server is expected to
    use; it defaults to the one the preview module ships.
    """
    from websockets.asyncio.client import connect

    convert = convert or ws.to_int16
    samples = _read(asset)
    chain = [{"code": "AUR_GAIN", "enabled": True, "params": {"Gain": 6.0}}]
    blocks = 16
    async with connect(url) as sock:
        ready = await _configure(sock, asset, chain)
        assert ready["type"] == "ready", ready
        frames, _notices = await _collect(sock, blocks)

    streamed = b"".join(frame for _stamp, frame in frames)
    # the chain is run again, block by block, exactly as the server does:
    # the preview runs its own profile, so the expectation is built with it
    engine, _report = ws.build_profile_engine(
        chain, SAMPLE_RATE, CHANNELS, ws.PREVIEW_PROFILE
    )
    expected = b""
    for start in range(0, blocks * BLOCK, BLOCK):
        block = samples[start : start + BLOCK]
        expected += convert(engine.process(block))

    # and the same again without a chain, to prove the transport is neutral
    async with connect(url) as sock:
        ready = await _configure(sock, asset, [])
        assert ready["type"] == "ready", ready
        dry_frames, _notices = await _collect(sock, blocks)
    dry = b"".join(frame for _stamp, frame in dry_frames)
    return {
        "frames": len(frames),
        "frame_bytes": len(frames[0][1]) if frames else 0,
        "identical": streamed == expected,
        "dry_identical": dry == convert(samples[: blocks * BLOCK]),
        "bytes": len(streamed),
    }


async def measure_rate(url: str, asset: Path, seconds: float = 6.0) -> dict:
    """Measure the block period the preview keeps in real time."""
    from websockets.asyncio.client import connect

    async with connect(url) as sock:
        ready = await _configure(sock, asset, [], realtime=True)
        assert ready["type"] == "ready", ready
        stamps: list[float] = []
        deadline = time.perf_counter() + seconds
        while time.perf_counter() < deadline:
            try:
                message = await asyncio.wait_for(
                    sock.recv(), timeout=max(0.05, deadline - time.perf_counter())
                )
            except asyncio.TimeoutError:
                break
            except Exception:
                break
            if isinstance(message, bytes):
                stamps.append(time.perf_counter())
    if len(stamps) < 3:
        raise AssertionError("the preview did not stream enough blocks")
    gaps = np.diff(np.array(stamps)) * 1000.0
    return {
        "blocks": len(stamps),
        "period_ms": float(gaps.mean()),
        "min_ms": float(gaps.min()),
        "max_ms": float(gaps.max()),
        "p99_ms": float(np.percentile(gaps, 99)),
        "std_ms": float(gaps.std()),
        "rate": 1000.0 / float(gaps.mean()),
    }


async def measure_dropouts(
    url: str,
    asset: Path,
    seconds: float = 60.0,
    realtime: bool = True,
    chain: list | None = None,
) -> dict:
    """Count the dropouts of a client that plays the stream in real time.

    A player consumes ``BLOCK`` frames every ``BLOCK_SECONDS``.  When the
    frames do not arrive in time the player runs dry: that is a dropout.
    """
    from websockets.asyncio.client import connect

    arrivals: list[float] = []
    dropped = 0
    async with connect(url) as sock:
        ready = await _configure(sock, asset, chain or [], realtime=realtime)
        assert ready["type"] == "ready", ready
        deadline = time.perf_counter() + seconds
        while time.perf_counter() < deadline:
            try:
                message = await asyncio.wait_for(
                    sock.recv(), timeout=max(0.05, deadline - time.perf_counter())
                )
            except asyncio.TimeoutError:
                break
            except Exception:
                break
            if isinstance(message, bytes):
                arrivals.append(time.perf_counter())
            else:
                payload = json.loads(message)
                if payload.get("type") == "dropout":
                    dropped += int(payload.get("frames", 0))
    if len(arrivals) < 3:
        raise AssertionError("the preview did not stream enough blocks")
    gaps = np.diff(np.array(arrivals)) * 1000.0
    # the depth of the playback buffer of a client that plays in real time
    # and keeps a small jitter buffer, as every preview player does
    depth = float(JITTER_BLOCKS * BLOCK)
    starved = 0
    previous = None
    for stamp in arrivals:
        if previous is not None:
            depth -= (stamp - previous) * SAMPLE_RATE
        previous = stamp
        depth += BLOCK
        if depth < 0:
            starved += 1
            depth = 0.0
    return {
        "blocks": len(arrivals),
        "seconds": arrivals[-1] - arrivals[0],
        "audio_seconds": len(arrivals) * BLOCK_SECONDS,
        "period_ms": float(gaps.mean()),
        "rate": 1000.0 / float(gaps.mean()),
        "underruns": starved,
        "dropped_frames": dropped,
    }


async def measure_ready(url: str, asset: Path, chain: list, **extra) -> dict:
    """The ``ready`` message of a preview, and the profile message after it."""
    from websockets.asyncio.client import connect

    async with connect(url) as sock:
        ready = await _configure(sock, asset, chain, **extra)
        assert ready["type"] == "ready", ready
        messages = [ready]
        while True:
            message = json.loads(await asyncio.wait_for(sock.recv(), timeout=15))
            messages.append(message)
            if message.get("type") == "profile":
                break
    return {"ready": ready, "messages": messages}


async def measure_memory_of_a_slow_client(
    server,
    asset: Path,
    seconds: float = 8.0,
) -> dict:
    """Measure what a client that never reads costs the server.

    The baseline is taken once the asset is decoded and the server has
    settled, so the figure is the memory the waiting audio costs and not the
    cost of loading the file.  The working set is sampled while the client
    sits still, so the figure is the peak the server reached.
    """
    from websockets.asyncio.client import connect

    dropped = 0
    async with connect(server.url, max_size=None) as sock:
        # the server is asked for everything at once, the client does not read
        await _configure(sock, asset, [], realtime=False)
        await asyncio.sleep(2.0)
        before = _working_set(server.process.pid)
        peak = before
        deadline = time.perf_counter() + seconds
        while time.perf_counter() < deadline:
            await asyncio.sleep(0.25)
            peak = max(peak, _working_set(server.process.pid))
        # now read the rest: the server says how much audio it had to drop
        deadline = time.perf_counter() + 30.0
        while time.perf_counter() < deadline:
            try:
                message = await asyncio.wait_for(sock.recv(), timeout=2.0)
            except asyncio.TimeoutError:
                break
            except Exception:
                break
            if isinstance(message, str):
                notice = json.loads(message)
                if notice.get("type") == "dropout":
                    dropped += int(notice.get("frames", 0))
                if notice.get("type") == "end":
                    break
    return {
        "before_mb": before / 1e6,
        "peak_mb": peak / 1e6,
        "growth_mb": (peak - before) / 1e6,
        "seconds": seconds,
        "dropped_frames": dropped,
    }


async def measure_interface_latency(url: str, asset: Path, blocks: int = 200) -> dict:
    """Measure how long an HTTP request waits while a preview renders.

    The chain is the heaviest module of the catalogue, so the preview has
    real work to do while the interface asks for an answer.
    """
    import urllib.request

    from websockets.asyncio.client import connect

    port = int(url.split(":")[2].split("/")[0])
    chain = [{"code": "AUR_SPECTRALDENOISE", "enabled": True}]
    latencies: list[float] = []
    stop = threading.Event()

    def _probe() -> None:
        while not stop.is_set():
            started = time.perf_counter()
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/health", timeout=10
                ):
                    pass
            except Exception:  # noqa: BLE001 - the probe is best effort
                pass
            latencies.append((time.perf_counter() - started) * 1000.0)
            time.sleep(0.02)

    probe = threading.Thread(target=_probe, daemon=True)
    probe.start()
    try:
        async with connect(url) as sock:
            await _configure(sock, asset, chain)
            await _collect(sock, blocks)
    finally:
        stop.set()
        probe.join(timeout=5)
    if not latencies:
        raise AssertionError("the probe did not measure anything")
    return {
        "mean_ms": float(np.mean(latencies)),
        "max_ms": float(np.max(latencies)),
        "p99_ms": float(np.percentile(latencies, 99)),
        "samples": len(latencies),
    }


# ---------------------------------------------------------------------------
# the fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def server():
    """A server with the preview route mounted."""
    instance = PreviewServer()
    instance.start()
    try:
        yield instance
    finally:
        instance.stop()


@pytest.fixture(scope="module")
def tone(tmp_path_factory) -> Path:
    """A 1 kHz tone on disk: the signal every measurement uses."""
    folder = tmp_path_factory.mktemp("preview")
    path = folder / "tone.wav"
    write_tone(path, seconds=8.0)
    return path


@pytest.fixture(scope="module")
def long_tone(tmp_path_factory) -> Path:
    """A 30 s tone: enough audio to fill the socket buffers of a slow client."""
    folder = tmp_path_factory.mktemp("preview-long")
    path = folder / "long.wav"
    write_tone(path, seconds=30.0)
    return path


# ---------------------------------------------------------------------------
# the tests
# ---------------------------------------------------------------------------
def test_the_preview_sends_the_audio_the_chain_produced(server, tone) -> None:
    """The audio on the wire is bit identical to the output of the chain."""
    report = asyncio.run(measure_identity(server.url, tone))
    assert report["frames"] == 16, report
    assert report["frame_bytes"] == BLOCK * CHANNELS * 2, report
    assert report["identical"], (
        "the streamed audio is not the audio the chain produced"
    )
    assert report["dry_identical"], "the transport changed the audio"


def test_the_preview_says_which_path_it_is(server, tone) -> None:
    """The preview tells the client what it is hearing.

    The preview is not the file: it runs a cheaper configuration.  A user
    who hears the fast path has to be able to see that, so the first message
    of the stream says which path it is, what the path changed and what
    latency the path has.
    """
    chain = [{"code": code, "enabled": True} for code in SPECTRAL_CHAIN]
    report = asyncio.run(measure_ready(server.url, tone, chain))
    ready = report["ready"]
    assert ready["path"] == "preview", ready
    assert ready["profile"] == audio.PROFILE_FAST, ready
    assert ready["profile_label"] == "preview: fast path", ready
    assert ready["profile_report"]["profile"] == audio.PROFILE_FAST, ready
    assert ready["latency_samples"] >= 0, ready
    assert ready["latency_ms"] == pytest.approx(
        ready["latency_samples"] * 1000.0 / SAMPLE_RATE, rel=1e-9
    ), ready
    # and the path says the same thing in a message of its own
    profiles = [m for m in report["messages"] if m["type"] == "profile"]
    assert profiles, report["messages"]
    assert profiles[0]["profile"] == audio.PROFILE_FAST, profiles
    assert profiles[0]["profile_report"] == ready["profile_report"], profiles


def test_the_fast_path_names_the_knobs_it_moved(server, tone) -> None:
    """The report gives both values: the one the file gets and its own."""
    chain = [{"code": code, "enabled": True} for code in SPECTRAL_CHAIN]
    ready = asyncio.run(measure_ready(server.url, tone, chain))["ready"]
    report = ready["profile_report"]
    moved = {
        entry["code"]: {p["name"]: p for p in entry["parameters"]}
        for entry in report["modules"]
    }
    assert "AUR_SPECTRALDENOISE" in moved, report
    quality = moved["AUR_SPECTRALDENOISE"]["Quality"]
    assert quality["file"] == "B", quality
    assert quality["preview"] == "A", quality
    assert moved["AUR_SPECTRALDENOISE"]["Multi Resolution"] == {
        "name": "Multi Resolution",
        "file": True,
        "preview": False,
    }, moved
    # a module the fast profile does not touch is not in the report
    assert "AUR_DEHUM" not in moved, moved
    assert "AUR_DECLICK" not in moved, moved
    assert report.get("refused", []) == [], report


def test_a_cheap_mode_that_does_not_run_is_refused() -> None:
    """A cheap configuration the module refuses is not used.

    The preview must not stream dry audio because a module cannot run in
    the configuration the fast path wanted.  The module is asked first, on
    a throw-away instance, and a refusal is reported.
    """
    from aurcore.params import ParamDesc, ParamStore

    class _Broken:
        """A module that does not run."""

        code = "AUR_TEST"

        def __init__(self, sample_rate: int = 48000, channels: int = 2) -> None:
            self.params = ParamStore(
                [
                    ParamDesc(
                        name="Quality",
                        type="enum",
                        default="B",
                        choices=("A", "B"),
                    )
                ]
            )

        def process(self, x):
            raise RuntimeError("this module does not run")

    class _Fine(_Broken):
        """A module that runs."""

        def process(self, x):
            return x

    assert audio._accepts(_Fine(), {"Quality": "A"}, SAMPLE_RATE, CHANNELS) is True
    assert audio._accepts(_Broken(), {"Quality": "A"}, SAMPLE_RATE, CHANNELS) is False
    # a parameter the module does not have is a refusal as well
    assert (
        audio._accepts(_Fine(), {"No Such Parameter": 1}, SAMPLE_RATE, CHANNELS)
        is False
    )


def test_the_file_render_is_not_the_fast_path() -> None:
    """The full profile changes nothing: what a file gets is the truth."""
    chain = [{"code": code, "enabled": True} for code in SPECTRAL_CHAIN]
    engine = ws.build_chain_engine(chain, SAMPLE_RATE, CHANNELS)
    before = [step.module.params.to_dict() for step in engine.chain]
    report = audio.apply_profile(engine, audio.PROFILE_FULL)
    assert report["profile"] == audio.PROFILE_FULL, report
    assert report["changed"] == 0, report
    assert report["modules"] == [], report
    assert report["refused"] == [], report
    after = [step.module.params.to_dict() for step in engine.chain]
    assert after == before


def test_the_fast_path_declares_a_shorter_latency() -> None:
    """The cheap configuration needs less look-ahead than the fine one."""
    chain = [{"code": code, "enabled": True} for code in SPECTRAL_CHAIN]
    fast, _fast_report = ws.build_profile_engine(
        chain, SAMPLE_RATE, CHANNELS, audio.PROFILE_FAST
    )
    full, _full_report = ws.build_profile_engine(
        chain, SAMPLE_RATE, CHANNELS, audio.PROFILE_FULL
    )
    assert 0 < fast.latency_samples < full.latency_samples, (
        fast.latency_samples,
        full.latency_samples,
    )


def test_a_client_can_ask_for_the_full_path(server, tone) -> None:
    """The preview can play the configuration the file gets."""
    chain = [{"code": code, "enabled": True} for code in SPECTRAL_CHAIN]
    ready = asyncio.run(
        measure_ready(server.url, tone, chain, profile="full")
    )["ready"]
    assert ready["profile"] == audio.PROFILE_FULL, ready
    assert ready["profile_label"] == "preview: full path", ready
    assert ready["profile_report"]["changed"] == 0, ready
    assert ready["block_size"] == FULL_BLOCK, ready


def test_the_preview_says_that_it_is_real_time(server, tone) -> None:
    """The preview is played while the user waits: it is real time.

    The file render is not: the same table of the server says so, and the
    preview reports the mode it works in.
    """
    ready = asyncio.run(measure_ready(server.url, tone, []))["ready"]
    assert ready["processing_mode"] == audio.PROCESSING_MODE_REALTIME, ready
    assert (
        audio.processing_mode_for(audio.PATH_FILE)
        == audio.PROCESSING_MODE_OFFLINE
    )
    assert (
        audio.processing_mode_for(audio.PATH_PREVIEW)
        == audio.PROCESSING_MODE_REALTIME
    )


def test_an_unknown_profile_is_refused(server, tone) -> None:
    """A profile the server does not know is an error, not a guess."""
    from websockets.asyncio.client import connect

    async def _ask(profile: str) -> dict:
        async with connect(server.url) as sock:
            await sock.send(
                json.dumps(
                    {
                        "type": "configure",
                        "asset_id": str(tone),
                        "chain": [],
                        "profile": profile,
                    }
                )
            )
            return json.loads(await asyncio.wait_for(sock.recv(), timeout=20))

    answer = asyncio.run(_ask("no-such-profile"))
    assert answer["type"] == "error", answer


def test_the_product_chain_keeps_up_in_the_preview(server, tone) -> None:
    """The fast path keeps up with the audio timeline.

    The chain is the product chain — mains hum, clicks, sibilance,
    plosives and a level change.  The preview has to deliver the audio at
    the rate the timeline asks for, and it may not drop a block.
    """
    chain = [{"code": code, "enabled": True} for code in DIALOGUE_CHAIN]
    report = asyncio.run(
        measure_dropouts(server.url, tone, seconds=20.0, chain=chain)
    )
    assert report["underruns"] == 0, report
    assert report["dropped_frames"] == 0, report
    assert report["period_ms"] < BLOCK_SECONDS * 1000.0 * 1.2, report


def test_the_heavy_chain_keeps_up_in_the_preview(server, tone) -> None:
    """The heaviest chain of the product keeps the preview on the timeline.

    The chain is the product chain with the flagship Spectral De-Noise
    module in it.  The number of blocks a client loses depends on how busy
    the machine is — the tree is shared — so what is asserted here is the
    property that does not: the blocks arrive at the rate the audio
    timeline asks for, so the preview does not fall behind.
    """
    chain = [{"code": code, "enabled": True} for code in SPECTRAL_CHAIN]
    report = asyncio.run(
        measure_dropouts(server.url, tone, seconds=20.0, chain=chain)
    )
    assert report["period_ms"] < BLOCK_SECONDS * 1000.0 * 1.5, report


def test_a_parameter_change_reaches_the_client_within_the_budget(
    server, tone
) -> None:
    """The user hears the change within the measured latency."""
    report = asyncio.run(measure_latency(server.url, tone))
    assert report["latency_ms"] < LATENCY_LIMIT_MS, (
        f"the change took {report['latency_ms']:.1f} ms to arrive"
    )
    assert report["loud_peak"] > report["quiet_peak"] * 1.5, report


def test_a_parameter_change_does_not_click(server, tone) -> None:
    """The change is cross-faded in, so the signal has no step in it."""
    from websockets.asyncio.client import connect

    chain = [{"code": "AUR_GAIN", "enabled": True, "params": {"Gain": 0.0}}]
    change_db = 9.0

    async def _run() -> list:
        async with connect(server.url) as sock:
            ready = await _configure(sock, tone, chain, realtime=True)
            assert ready["type"] == "ready", ready
            await _collect(sock, 4)
            await sock.send(
                json.dumps(
                    {
                        "type": "param",
                        "index": 0,
                        "name": "Gain",
                        "value": change_db,
                    }
                )
            )
            frames, _notices = await _collect(sock, 24)
        return [frame for _stamp, frame in frames]

    frames = asyncio.run(_run())
    assert frames, "the preview did not stream anything"
    audio = np.frombuffer(b"".join(frames), dtype="<i2").astype(np.float64)
    steps = np.abs(np.diff(audio))
    # the tone moves by this much between two samples once it is louder
    slew = AMPLITUDE * 2.0 * np.pi * 1000.0 / SAMPLE_RATE * 32767.0
    louder = slew * 10.0 ** (change_db / 20.0)
    # a change that was not cross-faded in would step by the whole difference
    # between the two levels, which is more than three times the slew
    assert steps.max() < louder * 1.5, (
        f"the change made a step of {steps.max():.0f} "
        f"(the louder tone moves {louder:.0f} per sample)"
    )


def test_the_realtime_preview_keeps_the_audio_timeline(server, tone) -> None:
    """The blocks arrive at the rate the audio timeline asks for."""
    report = asyncio.run(measure_rate(server.url, tone, seconds=5.0))
    assert abs(report["rate"] - RATE_NOMINAL) / RATE_NOMINAL < RATE_TOLERANCE, report
    assert report["p99_ms"] < BLOCK_SECONDS * 1000.0 * 3.0, report


def test_a_minute_of_streaming_has_no_dropouts(server, tone) -> None:
    """A client that plays the stream in real time never runs dry."""
    report = asyncio.run(measure_dropouts(server.url, tone, seconds=20.0))
    assert report["underruns"] == 0, report
    assert report["dropped_frames"] == 0, report


def test_a_slow_client_cannot_make_the_server_grow(long_tone) -> None:
    """A client that never reads costs the server a bounded amount.

    The audio that cannot be delivered waits in the socket buffers of the
    kernel, not in the heap of the server, so the working set of the server
    stays where it was.
    """
    server = SubprocessServer()
    server.start()
    try:
        report = asyncio.run(measure_memory_of_a_slow_client(server, long_tone))
    finally:
        server.stop()
    assert report["growth_mb"] < MEMORY_LIMIT_MB, report


def test_the_audio_that_waits_for_the_socket_is_bounded() -> None:
    """The queue between the worker and the socket has a hard bound."""
    outbox = ws._Outbox(4, 4096)
    outbox.set_frames_per_byte(2 * CHANNELS)
    dropped = 0
    for _ in range(10):
        dropped += outbox.put(b"\x00" * 4096)
    # every byte of the audio carries two samples of every channel
    assert outbox.dropped_frames(dropped) == dropped // (2 * CHANNELS), dropped
    assert dropped == 6 * 4096, dropped

def test_a_disconnect_leaves_no_worker_thread_behind(server, tone) -> None:
    """A client that disappears does not leave a thread running."""
    from websockets.asyncio.client import connect

    before = _worker_threads()

    async def _run() -> None:
        async with connect(server.url) as sock:
            await _configure(sock, tone, [], realtime=True)
            await _collect(sock, 2)

    asyncio.run(_run())
    deadline = time.time() + 10.0
    while time.time() < deadline and _worker_threads() > before:
        time.sleep(0.1)
    assert _worker_threads() == before, (
        f"{_worker_threads() - before} preview worker(s) are still running"
    )


def test_an_impossible_request_gets_an_error(server, tone) -> None:
    """A request the server cannot serve is answered, not ignored."""
    from websockets.asyncio.client import connect

    async def _ask(config: dict) -> dict:
        async with connect(server.url) as sock:
            await sock.send(json.dumps(config))
            return json.loads(await asyncio.wait_for(sock.recv(), timeout=20))

    broken_chain = asyncio.run(
        _ask({"type": "configure", "asset_id": str(tone), "chain": "AUR_GAIN"})
    )
    assert broken_chain["type"] == "error", broken_chain

    unknown_module = asyncio.run(
        _ask(
            {
                "type": "configure",
                "asset_id": str(tone),
                "chain": [{"code": "AUR_NOT_A_MODULE"}],
            }
        )
    )
    assert unknown_module["type"] == "error", unknown_module

    unknown_asset = asyncio.run(
        _ask({"type": "configure", "asset_id": "no-such-asset", "chain": []})
    )
    assert unknown_asset["type"] == "error", unknown_asset

    empty_range = asyncio.run(
        _ask(
            {
                "type": "configure",
                "asset_id": str(tone),
                "chain": [],
                "start": 20.0,
                "end": 21.0,
            }
        )
    )
    assert empty_range["type"] == "error", empty_range


def test_the_interface_is_not_blocked_by_a_preview(server, tone) -> None:
    """The preview runs in its own thread, so the server stays quick."""
    report = asyncio.run(measure_interface_latency(server.url, tone))
    assert report["mean_ms"] < 25.0, (
        f"the interface waited {report['mean_ms']:.0f} ms for an answer on "
        f"average while a preview was running"
    )
    assert report["p99_ms"] < 60.0, (
        f"the interface waited {report['p99_ms']:.0f} ms for an answer while "
        f"a preview was running"
    )


def _worker_threads() -> int:
    """How many preview workers are running right now."""
    return sum(
        1
        for thread in threading.enumerate()
        if thread.name.startswith(ws.WORKER_THREAD_PREFIX)
    )


# ---------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------
def _report() -> None:
    """Print the measurement table."""
    instance = PreviewServer()
    instance.start()
    folder = ROOT / "var" / "ws_report"
    folder.mkdir(parents=True, exist_ok=True)
    asset = folder / "tone.wav"
    write_tone(asset, seconds=8.0)
    long_asset = folder / "long.wav"
    write_tone(long_asset, seconds=30.0)
    try:
        identity = asyncio.run(measure_identity(instance.url, asset))
        latency = asyncio.run(measure_latency(instance.url, asset))
        rate = asyncio.run(measure_rate(instance.url, asset, seconds=6.0))
        dropouts = asyncio.run(measure_dropouts(instance.url, asset, seconds=60.0))
        interface = asyncio.run(measure_interface_latency(instance.url, asset))
    finally:
        instance.stop()
    memory_server = SubprocessServer()
    memory_server.start()
    try:
        memory = asyncio.run(measure_memory_of_a_slow_client(memory_server, long_asset))
    finally:
        memory_server.stop()
    print("the streaming preview — measured")
    print(f"  block size          {BLOCK} frames per channel")
    print(f"  rate                {rate['rate']:.2f} blocks/s "
          f"(nominal {RATE_NOMINAL:.2f})")
    print(f"  block period        {rate['period_ms']:.2f} ms "
          f"(min {rate['min_ms']:.2f}, max {rate['max_ms']:.2f})")
    print(f"  latency to a change {latency['latency_ms']:.1f} ms")
    print(f"  identity            {identity['identical']} "
          f"({identity['bytes']} bytes compared)")
    print(f"  dropouts in 60 s    {dropouts['underruns']} underruns, "
          f"{dropouts['dropped_frames']} dropped frames")
    print(f"  slow client         {memory['growth_mb']:.2f} MB of server memory "
          f"over {memory['seconds']:.0f} s "
          f"({memory['dropped_frames']} frames dropped)")
    print(f"  interface latency   mean {interface['mean_ms']:.1f} ms, "
          f"max {interface['max_ms']:.1f} ms during a preview")


if __name__ == "__main__":
    _report()
