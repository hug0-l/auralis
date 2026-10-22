#!/usr/bin/env python3
"""
qa/journey/journey.py — the Auralis product journey, end to end.

This is the *user* walk-through, not another unit test: it drives a running
Auralis server the way a person does, in the order a person does it.

    1.  open the workbench          (the server answers)
    2.  load a file                 (upload a synthetic recording)
    3.  look at it                  (spectrogram, frequency axis, envelope)
    4.  look at a part of it        (analyse a selection)
    5.  build a repair chain        (De-Hum + De-Click, reorder, tune, bypass)
    6.  bounce it                   (render, check the WAV, check the hum is gone)
    7.  remember the settings       (save a preset, list, load it back)
    8.  keep the session            (save a session document, load it back)
    9.  take a change back          (undo: the chain is as it was)
    10. compare two chains          (the compare route)
    11. deliver it                  (delivery targets)
    12. run it overnight            (batch job: start, poll, outputs, loudness)

Every step prints one ``[PASS]`` / ``[FAIL]`` line.  Every step *asserts*
something: the assertions are counted, and a step that only calls something and
hopes for the best shows up in the summary with zero assertions.

With ``--json`` the machine-readable summary is printed as well.  It carries
the steps, the pass and fail counts, the number of assertions and the
measurements of every step.  The measurements are the numbers of the run; they
are deterministic, so two runs of the same build produce the same numbers.
The fields that cannot be deterministic are listed under ``volatile``.

Usage
-----
    python qa/journey/journey.py                     # start a server, run, stop it
    python qa/journey/journey.py --base-url http://127.0.0.1:8741
    python qa/journey/journey.py --json --port 8742

Only the standard library and numpy are used.  The undo step reaches into the
core (``aurcore``), because that is where the undo history lives.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

#: The project root (the directory that holds ``aurcore``, ``aurserver`` ...).
ROOT = Path(__file__).resolve().parents[2]

#: The undo step uses the core, so the repository root has to be importable.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: Where the journey writes the files the batch job needs.  The batch
#: endpoint only accepts paths inside ``<repo>/var`` (or whatever roots the
#: server was configured with), so the scratch files live there.
VAR = ROOT / "var"
JOURNEY_DIR = VAR / "journey"
BATCH_OUT_DIR = JOURNEY_DIR / "out"

SAMPLE_RATE = 48_000
CHANNELS = 2
DURATION = 3.0

#: The programme tone.
TONE_HZ = 1_000.0
TONE_AMP = 0.25

#: The mains hum that De-Hum has to remove.
HUM_HZ = 50.0
HUM_AMP = 0.05

#: Where the clicks sit in the recording.  The selection the journey analyses
#: deliberately lies between two of them, so a selection that ignores its
#: bounds still shows a click.
CLICK_POSITIONS = (0.4, 1.3, 2.2)

#: How much the 50 Hz component has to drop for the render to count as a
#: success (in decibels).  A healthy De-Hum removes 25 dB or more.
HUM_REDUCTION_DB = 12.0

#: How much of the programme the repair may cost (in decibels).
PROGRAMME_LOSS_DB = 1.0

#: The preset the journey saves under its own name.
PRESET_NAME = "Journey Hum Killer"
PRESET_CATEGORY = "Journey"

#: The session document the journey writes.
SESSION_NAME = "journey"

#: The session document that carries the undo history.
UNDO_SESSION_NAME = "journey-undo"

#: The selection the journey analyses, in seconds.  It holds the programme
#: tone and no click.
SELECTION_START = 1.5
SELECTION_END = 2.0

#: The smallest distance the frequency axis check accepts, in hertz.
FREQ_TOLERANCE_HZ = 10.0

#: The two files the batch job delivers: the same recording at two levels.
#: The name and the gain are the pair, so the louder file is the first one.
BATCH_SOURCES: tuple[tuple[str, float], ...] = (
    ("journey-loud.wav", 1.0),
    ("journey-quiet.wav", 0.25),
)

#: How far apart the loudness of two deliveries may be, in loudness units,
#: before the journey calls the order wrong.  The two sources differ by
#: 12 dB, so 6 LU is a wide but safe margin.
LOUDNESS_ORDER_MIN_LU = 6.0

#: The seed of the noise generator, so a failure is always reproducible.
SEED = 20_260_121

#: The fields of the summary that cannot be the same in two runs, and why.
VOLATILE_FIELDS: dict[str, str] = {
    "base_url": "the port of the server is a free port, so it differs per run",
    "steps[].detail": (
        "asset ids, job ids and delivered file names are generated per run; "
        "they are identifiers, not measurements"
    ),
    "steps[].measurements": (
        "the measurements are deterministic; see the 'measurements' key of the "
        "summary for the numbers a CI run may compare"
    ),
}


# ---------------------------------------------------------------------------
# the synthetic recording
# ---------------------------------------------------------------------------
def build_signal(seconds: float = DURATION, sample_rate: int = SAMPLE_RATE):
    """Build the test recording: a tone, mains hum, clicks and a little noise.

    Returns ``(samples, sample_rate)`` with ``samples`` shaped
    ``(n_samples, n_channels)`` in float64.
    """
    n = int(seconds * sample_rate)
    t = np.arange(n, dtype=np.float64) / float(sample_rate)

    tone = TONE_AMP * np.sin(2.0 * math.pi * TONE_HZ * t)
    hum = HUM_AMP * np.sin(2.0 * math.pi * HUM_HZ * t)

    clicks = np.zeros(n, dtype=np.float64)
    for position in CLICK_POSITIONS:
        start = int(position * sample_rate)
        clicks[start : start + 30] = 0.9

    rng = np.random.default_rng(SEED)
    noise = 0.002 * rng.standard_normal(n)

    mono = tone + hum + clicks + noise
    # the right channel is a little quieter and slightly detuned, so a
    # channel-agnostic bug cannot pass unnoticed
    mono_r = 0.98 * (tone + hum + clicks + noise)
    return np.stack([mono, mono_r], axis=1), sample_rate


def encode_wav(samples: np.ndarray, sample_rate: int) -> bytes:
    """Encode a float64 signal as a 16-bit PCM WAV file."""
    arr = np.asarray(samples, dtype=np.float64)
    if arr.ndim == 1:
        arr = arr[:, None]
    interleaved = np.clip(np.round(arr.ravel() * 32767.0), -32768, 32767).astype("<i2")
    pcm = interleaved.tobytes()
    channels = int(arr.shape[1])
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(pcm),
        b"WAVE",
        b"fmt ",
        16,
        1,
        channels,
        int(sample_rate),
        int(sample_rate) * channels * 2,
        channels * 2,
        16,
        b"data",
        len(pcm),
    )
    return header + pcm


def decode_wav(data: bytes):
    """Decode a RIFF/WAVE payload into ``(samples, sample_rate, channels)``.

    Handles 8, 16, 24 and 32 bit PCM.  Returns float64 samples shaped
    ``(n_frames, n_channels)``.
    """
    if len(data) < 12 or data[0:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a RIFF/WAVE payload")
    pos = 12
    fmt = None
    payload = None
    while pos + 8 <= len(data):
        cid = data[pos : pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        body = data[pos + 8 : pos + 8 + size]
        if cid == b"fmt ":
            fmt = body
        elif cid == b"data":
            payload = body
            break
        pos += 8 + size + (size & 1)
    if fmt is None or payload is None:
        raise ValueError("the payload has no fmt/data chunk")
    audio_format, channels, sample_rate, _byte_rate, _align, bits = struct.unpack_from(
        "<HHIIHH", fmt, 0
    )
    if audio_format == 0xFFFE and len(fmt) >= 26:
        audio_format = struct.unpack_from("<H", fmt, 24)[0]
    if channels <= 0:
        raise ValueError("the payload declares no channels")
    if bits == 16:
        raw = np.frombuffer(payload, dtype="<i2").astype(np.float64) / 32768.0
    elif bits == 24:
        raw_bytes = np.frombuffer(payload, dtype=np.uint8).reshape(-1, 3)
        values = (
            raw_bytes[:, 0].astype(np.int64)
            | (raw_bytes[:, 1].astype(np.int64) << 8)
            | (raw_bytes[:, 2].astype(np.int64) << 16)
        )
        values = np.where(values & 0x800000, values - 0x1000000, values)
        raw = values.astype(np.float64) / 8388608.0
    elif bits == 32:
        raw = np.frombuffer(payload, dtype="<i4").astype(np.float64) / 2147483648.0
    elif bits == 8:
        raw = (np.frombuffer(payload, dtype=np.uint8).astype(np.float64) - 128.0) / 128.0
    else:
        raise ValueError(f"unsupported bit depth: {bits}")
    usable = (raw.size // channels) * channels
    return raw[:usable].reshape(-1, channels), int(sample_rate), int(channels)


def amplitude_at(samples: np.ndarray, frequency: float, sample_rate: int) -> float:
    """The amplitude of ``frequency`` inside ``samples`` (a plain DFT)."""
    arr = np.asarray(samples, dtype=np.float64)
    if arr.ndim == 1:
        arr = arr[:, None]
    n = np.arange(arr.shape[0], dtype=np.float64)
    value = np.sum(arr[:, 0] * np.exp(-2j * math.pi * frequency * n / float(sample_rate)))
    return float(2.0 * abs(value) / arr.shape[0])


def db(value: float) -> float:
    """``value`` in decibels (with a floor so the log never blows up)."""
    return 20.0 * math.log10(max(float(value), 1e-12))


def unwrap_value(node: Any) -> Any:
    """Unwrap the typed-value envelope the server uses for every scalar."""
    if isinstance(node, dict) and "Type" in node and "Value" in node:
        kind = node["Type"]
        value = node["Value"]
        if kind == "Bool":
            return bool(value)
        if kind in ("Int", "UInt"):
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
        if kind == "Float":
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
        return value
    return node


def same_value(left: Any, right: Any) -> bool:
    """True when two parameter values are the same number (or the same text)."""
    if isinstance(left, bool) or isinstance(right, bool):
        return bool(left) == bool(right)
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) <= 1e-9 * max(1.0, abs(float(left)))
    return left == right


def chain_fingerprint(summary: Any) -> tuple:
    """A comparable picture of a chain: its codes, its bypass flags, its values.

    Two chains have the same fingerprint when they hold the same modules in
    the same order, switched the same way, with the same parameter values.
    """
    out: list[tuple] = []
    for step in summary or []:
        params = {}
        for name, node in (step.get("params") or {}).items():
            params[str(name)] = unwrap_value(node)
        out.append(
            (
                str(step.get("code") or ""),
                bool(step.get("bypass")),
                tuple(sorted(params.items(), key=lambda item: item[0])),
            )
        )
    return tuple(out)


def measurement(value: Any) -> Any:
    """Normalise one measurement into a JSON-safe number."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return round(float(value), 6)
    return str(value)


# ---------------------------------------------------------------------------
# the HTTP client (standard library only)
# ---------------------------------------------------------------------------
class HttpError(RuntimeError):
    """An HTTP request that did not answer with 2xx."""

    def __init__(self, method: str, url: str, status: int, body: str):
        super().__init__(f"{method} {url} -> HTTP {status}: {body[:400]}")
        self.method = method
        self.url = url
        self.status = status
        self.body = body


def request(
    method: str,
    url: str,
    payload: Any = None,
    *,
    raw: bool = False,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 120.0,
):
    """Send one request; return the decoded JSON body (or the raw bytes)."""
    body = data
    head = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        head.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, headers=head, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            content = response.read()
    except urllib.error.HTTPError as exc:
        try:
            text = exc.read().decode("utf-8", "replace")
        except Exception:  # pragma: no cover - defensive
            text = ""
        raise HttpError(method, url, exc.code, text) from None
    if raw:
        return content
    text = content.decode("utf-8", "replace")
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"_raw": text}


def upload(base_url: str, wav: bytes, filename: str = "journey.wav") -> dict:
    """Upload a WAV file as multipart/form-data and return the answer."""
    boundary = "----auralisjourney"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: audio/wav\r\n\r\n"
    ).encode("utf-8") + wav + f"\r\n--{boundary}--\r\n".encode("utf-8")
    return request(
        "POST",
        base_url + "/api/upload",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )


# ---------------------------------------------------------------------------
# the server
# ---------------------------------------------------------------------------
def free_port() -> int:
    """Return a port that is free right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class ServerProcess:
    """A uvicorn server this script owns."""

    def __init__(self, port: int):
        self.port = int(port)
        self.process: subprocess.Popen | None = None
        self.log_handle = None
        self.log_path = Path(tempfile.gettempdir()) / f"auralis-journey-{self.port}.log"

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, timeout: float = 60.0) -> None:
        """Start the server and wait until it answers."""
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT)
        env["PYTHONUNBUFFERED"] = "1"
        # the log goes to a file, not to a pipe: a pipe that nobody reads
        # would block the server once the buffer is full
        self.log_handle = open(self.log_path, "w", encoding="utf-8", errors="replace")
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "aurserver.app:app",
                "--port",
                str(self.port),
                "--log-level",
                "warning",
            ],
            cwd=str(ROOT),
            env=env,
            stdout=self.log_handle,
            stderr=subprocess.STDOUT,
        )
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(
                    "the server exited during start-up:\n" + self._drain()
                )
            try:
                request("GET", self.base_url + "/api/health", timeout=2.0)
                return
            except Exception:  # noqa: BLE001 - the server is still starting
                time.sleep(0.25)
        raise RuntimeError("the server did not become healthy:\n" + self._drain())

    def _drain(self) -> str:
        """The last lines the server wrote."""
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return text[-4000:]

    def stop(self) -> None:
        """Stop the server."""
        if self.process is None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except Exception:  # noqa: BLE001 - the server has to go
            self.process.kill()
        self.process = None
        if self.log_handle is not None:
            try:
                self.log_handle.close()
            except Exception:  # noqa: BLE001 - defensive
                pass
            self.log_handle = None


# ---------------------------------------------------------------------------
# the journey
# ---------------------------------------------------------------------------
class Journey:
    """Runs the steps of the journey and collects the verdicts."""

    def __init__(self, base_url: str, verbose: bool = True, stream=None):
        self.base_url = base_url.rstrip("/")
        self.verbose = verbose
        self.stream = stream if stream is not None else sys.stdout
        self.steps: list[dict[str, Any]] = []
        self._checks = 0
        self._checks_mark = 0

    # -- reporting -----------------------------------------------------------
    def check(self, condition: Any, message: str) -> bool:
        """Count one assertion and raise when it does not hold.

        Every step of the journey goes through this method, so the number of
        assertions a step made is known.  A step that passes without an
        assertion of its own reports zero, and the test module refuses it.
        """
        self._checks += 1
        if not condition:
            raise AssertionError(message)
        return True

    def record(
        self,
        name: str,
        ok: bool,
        detail: str = "",
        measurements: dict[str, Any] | None = None,
    ) -> bool:
        """Record one verdict and print it."""
        assertions = self._checks - self._checks_mark
        self._checks_mark = self._checks
        numbers = {str(k): measurement(v) for k, v in (measurements or {}).items()}
        self.steps.append(
            {
                "name": name,
                "ok": bool(ok),
                "detail": detail,
                "assertions": int(assertions),
                "measurements": numbers,
            }
        )
        if self.verbose:
            line = f"  [{'PASS' if ok else 'FAIL'}] {name}"
            if detail:
                line += f"  — {detail}"
            if numbers:
                line += "  [" + " ".join(f"{k}={v}" for k, v in numbers.items()) + "]"
            print(line, file=self.stream, flush=True)
        return bool(ok)

    def fail(self, name: str, exc: BaseException) -> bool:
        """Record a failure with the exact error.

        A step that failed before it made an assertion of its own still
        established something — the product did not meet a requirement — so
        the failure counts as that one assertion.
        """
        if self._checks == self._checks_mark:
            self._checks += 1
        detail = f"{type(exc).__name__}: {exc}"
        return self.record(name, False, detail)

    # -- helpers -------------------------------------------------------------
    def ask(
        self,
        method: str,
        path: str,
        payload: Any = None,
        *,
        raw: bool = False,
        timeout: float = 120.0,
    ):
        """One request; a transport failure counts as a failed assertion."""
        try:
            return request(
                method,
                self.base_url + path,
                payload,
                raw=raw,
                timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001 - the step reports the failure
            self.check(
                False,
                f"{method} {path} did not answer: {type(exc).__name__}: {exc}",
            )
            raise

    def get(self, path: str) -> Any:
        return self.ask("GET", path)

    def post(self, path: str, payload: Any = None) -> Any:
        return self.ask("POST", path, payload if payload is not None else {})

    def upload(self, wav: bytes, filename: str = "journey.wav") -> dict:
        """Upload a recording; a failure counts as a failed assertion."""
        try:
            return upload(self.base_url, wav, filename)
        except Exception as exc:  # noqa: BLE001 - the step reports the failure
            self.check(
                False,
                f"the upload did not answer: {type(exc).__name__}: {exc}",
            )
            raise

    def summary(self) -> dict[str, Any]:
        """The machine-readable summary of the run."""
        passed = sum(1 for step in self.steps if step["ok"])
        total = len(self.steps)
        return {
            "ok": passed == total and bool(self.steps),
            "base_url": self.base_url,
            "passed": passed,
            "failed": total - passed,
            "total": total,
            "assertions": sum(step["assertions"] for step in self.steps),
            "steps": list(self.steps),
            "measurements": {
                step["name"]: dict(step["measurements"]) for step in self.steps
            },
            "volatile": dict(VOLATILE_FIELDS),
        }


def _step(j: Journey, name: str, action, *args, **kwargs):
    """Run one group of steps; a failure is recorded and the journey goes on.

    Returns ``(ok, value)``: ``ok`` says whether the group ran through, and
    ``value`` is what the group returned.
    """
    try:
        return True, action(j, *args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - the journey reports, it does not raise
        j.fail(name, exc)
        return False, None


# ---------------------------------------------------------------------------
# the steps
# ---------------------------------------------------------------------------
def step_health(j: Journey) -> None:
    """Step: open the workbench."""
    health = j.get("/api/health")
    j.check(health.get("ok") is True, f"the health endpoint answered {health}")
    j.record("the workbench answers", True, j.base_url, measurements={"alive": 1})


def step_catalogue(j: Journey) -> None:
    """Step: the catalogue knows the repair modules."""
    answer = j.get("/api/modules")
    modules = answer.get("modules") or []
    j.check(bool(modules), "the module catalogue is empty")
    codes = {str(m.get("code")) for m in modules}
    for wanted in ("AUR_DEHUM", "AUR_DECLICK"):
        j.check(wanted in codes, f"the catalogue does not know {wanted}")
    j.record(
        "the module catalogue",
        True,
        f"{len(modules)} modules",
        measurements={"modules": len(modules)},
    )


def step_upload(j: Journey, wav: bytes, frames: int) -> str:
    """Step: load a file."""
    answer = j.upload(wav)
    asset_id = str(answer.get("asset_id") or "")
    j.check(bool(asset_id), f"the upload returned no asset_id: {answer}")
    j.check(
        int(answer.get("frames") or 0) == frames,
        f"the upload reports {answer.get('frames')} frames, expected {frames}",
    )
    j.check(
        int(answer.get("channels") or 0) == CHANNELS,
        f"the upload reports {answer.get('channels')} channels",
    )
    j.check(
        int(answer.get("sample_rate") or 0) == SAMPLE_RATE,
        f"the upload reports {answer.get('sample_rate')} Hz",
    )
    duration = float(answer.get("duration") or 0.0)
    j.check(
        abs(duration - DURATION) < 1e-3,
        f"the upload reports a duration of {duration} s, expected {DURATION} s",
    )
    j.record(
        "upload the recording",
        True,
        f"{asset_id} — {frames} frames, {CHANNELS} ch, {SAMPLE_RATE} Hz, "
        f"{duration:.2f} s",
        measurements={
            "frames": frames,
            "channels": CHANNELS,
            "sample_rate": SAMPLE_RATE,
            "duration_s": round(duration, 6),
        },
    )
    return asset_id


def _matrix(j: Journey, answer: Any) -> np.ndarray:
    """The spectrogram of an analysis payload as a matrix."""
    spec = answer.get("spectrogram")
    j.check(
        isinstance(spec, list) and bool(spec),
        "the analysis returned no spectrogram",
    )
    j.check(isinstance(spec[0], list), "the spectrogram is not a matrix of rows")
    return np.asarray(spec, dtype=np.float64)


def _peak_bin(matrix: np.ndarray) -> int:
    """The row of the matrix that carries the loudest band."""
    return int(np.argmax(matrix.mean(axis=1)))


def step_analyse(j: Journey, asset_id: str) -> None:
    """Step: look at it — the spectrogram, the frequency axis and the envelope."""
    answer = j.post("/api/analyze", {"asset_id": asset_id})

    # the spectrogram itself
    try:
        matrix = _matrix(j, answer)
        bins, frames = matrix.shape
        j.check(bins >= 16 and frames >= 16, f"the spectrogram is too small: {bins} x {frames}")
        j.check(
            int(answer.get("freq_bins") or 0) == bins,
            f"freq_bins says {answer.get('freq_bins')} but the matrix has {bins} rows",
        )
        j.check(
            int(answer.get("frames") or 0) == frames,
            f"frames says {answer.get('frames')} but the matrix has {frames} columns",
        )
        values = matrix.astype(np.int64)
        j.check(values.min() >= 0 and values.max() <= 255, "the spectrogram cells are not uint8")
        j.check(values.max() > 0, "the spectrogram is empty (every cell is zero)")
        floor = float(np.percentile(matrix, 90.0))
        peak_bin = _peak_bin(matrix)
        peak_value = float(matrix[peak_bin].mean())
        j.check(
            peak_value > floor,
            "the spectrogram has no dominant frequency (the signal is not visible)",
        )
        bin_hz = float(answer.get("bin_hz") or 0.0)
        j.check(bin_hz > 0.0, "the analysis reports no bin_hz")
        peak_hz = peak_bin * bin_hz
        j.check(
            20.0 <= peak_hz <= 2_000.0,
            f"the loudest bin sits at {peak_hz:.1f} Hz, which is not the programme",
        )
        j.record(
            "analysis: spectrogram",
            True,
            f"{bins} bins x {frames} frames, loudest bin {peak_hz:.0f} Hz, "
            f"range {int(values.min())}..{int(values.max())}",
            measurements={
                "bins": bins,
                "frames": frames,
                "bin_hz": round(bin_hz, 6),
                "peak_row": peak_bin,
                "peak_hz": round(peak_hz, 6),
                "min_cell": int(values.min()),
                "max_cell": int(values.max()),
            },
        )
    except Exception as exc:  # noqa: BLE001
        j.fail("analysis: spectrogram", exc)

    # the frequency axis has to be honest: the programme tone is at 1000 Hz,
    # so the loudest bin has to be reported at 1000 Hz as well
    try:
        matrix = _matrix(j, answer)
        bins = int(matrix.shape[0])
        peak_bin = _peak_bin(matrix)
        bin_hz = float(answer.get("bin_hz") or 0.0)
        j.check(bin_hz > 0.0, "the analysis reports no bin_hz")
        reported = peak_bin * bin_hz
        tolerance = max(2.0 * bin_hz, FREQ_TOLERANCE_HZ)
        j.check(
            abs(reported - TONE_HZ) <= tolerance,
            f"the loudest bin (row {peak_bin}) is reported at {reported:.1f} Hz "
            f"but the programme tone is at {TONE_HZ:.0f} Hz "
            f"(bin_hz {bin_hz} Hz — the axis does not match the matrix)",
        )
        j.record(
            "analysis: the frequency axis",
            True,
            f"the programme tone is at {reported:.1f} Hz",
            measurements={
                "peak_row": peak_bin,
                "reported_hz": round(reported, 6),
                "error_hz": round(abs(reported - TONE_HZ), 6),
                "tolerance_hz": round(tolerance, 6),
            },
        )
    except Exception as exc:  # noqa: BLE001
        j.fail("analysis: the frequency axis", exc)

    # A matrix whose bottom rows are exact copies of each other has been
    # padded, and the frequency axis then lies.  The recording carries a
    # little broadband noise, so genuine rows never repeat exactly.
    try:
        matrix = _matrix(j, answer)
        bins = int(matrix.shape[0])
        tail_run = 0
        last = matrix[-1]
        same = np.all(np.abs(matrix - last) < 1e-9, axis=1)
        for flag in same[::-1]:
            if not flag:
                break
            tail_run += 1
        j.check(
            tail_run <= max(2, bins // 20),
            f"{tail_run} of the {bins} bottom rows are identical to the last one "
            "(the matrix is padded, so the frequency axis lies)",
        )
        j.record(
            "analysis: every row carries its own band",
            True,
            f"{tail_run} of the {bins} bottom rows are identical to the last one",
            measurements={"identical_tail_rows": tail_run, "bins": bins},
        )
    except Exception as exc:  # noqa: BLE001
        j.fail("analysis: every row carries its own band", exc)

    # the waveform envelope: one min/max pair per bucket
    try:
        wave = answer.get("waveform")
        j.check(
            isinstance(wave, list) and len(wave) >= 16,
            f"the waveform envelope has {len(wave) if wave else 0} buckets",
        )
        lows = np.asarray([float(b[0]) for b in wave], dtype=np.float64)
        highs = np.asarray([float(b[1]) for b in wave], dtype=np.float64)
        j.check(not bool(np.any(lows > highs)), "a waveform bucket has min > max")
        peak = float(np.max(np.abs(highs)))
        j.check(peak > 0.0, "the waveform envelope is silent")
        j.record(
            "analysis: waveform envelope",
            True,
            f"{len(wave)} buckets, peak {db(peak):.1f} dBFS",
            measurements={"buckets": len(wave), "peak_dbfs": round(db(peak), 3)},
        )
    except Exception as exc:  # noqa: BLE001
        j.fail("analysis: waveform envelope", exc)


def step_analyse_selection(j: Journey, asset_id: str, whole_frames: int) -> None:
    """Step: look at a part of the recording — a selection, not the whole file."""
    selection = {
        "asset_id": asset_id,
        "start": SELECTION_START,
        "end": SELECTION_END,
    }
    answer = j.post("/api/analyze", selection)

    # the spectrogram of the selection
    try:
        matrix = _matrix(j, answer)
        bins, frames = matrix.shape
        j.check(bins >= 16 and frames >= 16, f"the selection is too small: {bins} x {frames}")
        j.check(
            frames < whole_frames,
            f"the selection has {frames} frames, the whole recording has {whole_frames}",
        )
        j.check(
            int(answer.get("audio_frames") or 0) > 0,
            "the analysis does not describe the recording it was taken from",
        )
        j.record(
            "analysis: a selection is measured",
            True,
            f"{bins} bins x {frames} frames of the {whole_frames} frames of the whole file",
            measurements={"bins": bins, "frames": frames, "whole_frames": whole_frames},
        )
    except Exception as exc:  # noqa: BLE001
        j.fail("analysis: a selection is measured", exc)

    # the selection must not show what lies outside it: the clicks are at
    # 0.4 s, 1.3 s and 2.2 s, so the selection holds none of them
    try:
        wave = answer.get("waveform")
        j.check(isinstance(wave, list) and bool(wave), "the selection has no waveform envelope")
        highs = np.asarray([float(b[1]) for b in wave], dtype=np.float64)
        lows = np.asarray([float(b[0]) for b in wave], dtype=np.float64)
        peak = float(np.max(np.abs(np.concatenate([lows, highs]))))
        j.check(peak > 0.0, "the waveform envelope of the selection is silent")
        j.check(
            peak <= 0.5,
            f"the envelope of the selection reaches {peak:.3f}, so it still shows "
            "the click that lies outside the selection",
        )
        j.record(
            "analysis: the selection leaves the click outside",
            True,
            f"the envelope of the selection peaks at {db(peak):.1f} dBFS",
            measurements={"selection_peak_dbfs": round(db(peak), 3)},
        )
    except Exception as exc:  # noqa: BLE001
        j.fail("analysis: the selection leaves the click outside", exc)

    # the measurement payload of the selection
    try:
        measured = j.post("/api/analysis", selection)
        block = measured.get("selection") or {}
        j.check(bool(block), "the measurement payload has no selection block")
        j.check(
            abs(float(block.get("start", -1.0)) - SELECTION_START) < 1e-6,
            f"the selection starts at {block.get('start')}, expected {SELECTION_START}",
        )
        j.check(
            abs(float(block.get("end", -1.0)) - SELECTION_END) < 1e-6,
            f"the selection ends at {block.get('end')}, expected {SELECTION_END}",
        )
        wanted = int(round((SELECTION_END - SELECTION_START) * SAMPLE_RATE))
        j.check(
            int(block.get("samples") or 0) == wanted,
            f"the selection holds {block.get('samples')} samples, expected {wanted}",
        )
        j.check(block.get("bounded") is True, "the selection is not marked as bounded")
        j.check(
            int(measured.get("frames") or 0) > wanted,
            "the payload does not describe the whole recording",
        )
        loudness = measured.get("loudness") or {}
        integrated = float(loudness.get("integrated_lufs", float("nan")))
        j.check(math.isfinite(integrated), f"the loudness of the selection is {integrated}")
        stats = measured.get("waveform_stats") or {}
        j.check(
            int(stats.get("possibly_clipped") or 0) == 0,
            f"the selection reports {stats.get('possibly_clipped')} clipped samples, "
            "so it still contains a click",
        )
        spectrum = measured.get("spectrum") or {}
        magnitudes = spectrum.get("magnitudes_db") or []
        j.check(bool(magnitudes), "the measurement payload has no spectrum")
        peak_hz = float(spectrum.get("peak_hz") or 0.0)
        j.check(
            abs(peak_hz - TONE_HZ) <= max(2.0 * float(spectrum.get("fft_size") or 2048)
                                         * SAMPLE_RATE / 2048.0, FREQ_TOLERANCE_HZ),
            f"the spectrum of the selection peaks at {peak_hz:.1f} Hz, "
            f"but the programme tone is at {TONE_HZ:.0f} Hz",
        )
        j.record(
            "analysis: the selection is reported",
            True,
            f"{block.get('samples')} samples, {integrated:.1f} LUFS, "
            f"the spectrum peaks at {peak_hz:.0f} Hz",
            measurements={
                "samples": int(block.get("samples") or 0),
                "integrated_lufs": round(integrated, 3),
                "peak_hz": round(peak_hz, 3),
                "clipped_samples": int(stats.get("possibly_clipped") or 0),
            },
        )
    except Exception as exc:  # noqa: BLE001
        j.fail("analysis: the selection is reported", exc)


def step_chain(j: Journey) -> list[dict]:
    """Step: build a repair chain."""
    chain = j.get("/api/chain").get("chain", [])
    j.check(not chain, f"the chain is not empty at the start: {chain}")

    after_hum = j.post("/api/chain", {"code": "AUR_DEHUM"}).get("chain", [])
    j.check(
        len(after_hum) == 1 and after_hum[0].get("code") == "AUR_DEHUM",
        f"adding De-Hum failed: {after_hum}",
    )
    after_click = j.post("/api/chain", {"code": "AUR_DECLICK"}).get("chain", [])
    j.check(len(after_click) == 2, f"adding De-Click failed: {after_click}")
    j.record(
        "chain: add De-Hum and De-Click",
        True,
        " -> ".join(str(s.get("code")) for s in after_click),
        measurements={"modules": len(after_click)},
    )

    reordered = j.post("/api/chain/reorder", {"from": 1, "to": 0}).get("chain", [])
    codes = [str(s.get("code")) for s in reordered]
    j.check(codes[:2] == ["AUR_DECLICK", "AUR_DEHUM"], f"the reorder did not take: {codes}")
    j.record(
        "chain: reorder",
        True,
        " -> ".join(codes),
        measurements={"modules": len(reordered)},
    )

    # tune a parameter of the module that is now first
    index = 0
    params = j.get(f"/api/chain/{index}/params")
    descs = params.get("params") or []
    values = params.get("values") or {}
    j.check(bool(descs), "the module exposes no parameters")
    target = None
    new_value = None
    for desc in descs:
        if desc.get("name") in ("Sensitivity", "Base Frequency", "Gain", "Threshold"):
            low = float(desc.get("minimum", 0.0))
            high = float(desc.get("maximum", 1.0))
            target = str(desc.get("name"))
            new_value = round(low + (high - low) * 0.6, 6)
            break
    j.check(
        target is not None,
        f"no tunable parameter found: {[d.get('name') for d in descs]}",
    )
    answer = j.post(f"/api/chain/{index}/param", {"name": target, "value": new_value})
    j.check(answer.get("ok") is True, f"the server did not accept the parameter: {answer}")
    now = j.get(f"/api/chain/{index}/params").get("values") or {}
    j.check(target in now, f"the parameter {target!r} is not in the value map")
    j.check(
        abs(float(now[target]) - float(new_value)) <= 1e-6,
        f"the parameter did not stick: {target} = {now[target]}, expected {new_value}",
    )
    module_name = params.get("code") or f"module {index}"
    j.record(
        "chain: set a parameter",
        True,
        f"{module_name}: {target} = {now[target]} (was {values.get(target)})",
        measurements={"value": float(now[target]), "previous": float(values.get(target) or 0.0)},
    )

    bypassed = j.post("/api/chain/1/bypass", {"bypass": True}).get("chain", [])
    j.check(bool(bypassed[1].get("bypass")), f"the bypass flag did not take: {bypassed[1]}")
    j.record(
        "chain: bypass De-Hum",
        True,
        "De-Hum is bypassed",
        measurements={"bypassed": 1},
    )

    restored = j.post("/api/chain/1/bypass", {"bypass": False}).get("chain", [])
    j.check(not bool(restored[1].get("bypass")), "the bypass flag was not cleared")
    j.record(
        "chain: engage De-Hum again",
        True,
        "De-Hum is active",
        measurements={"bypassed": 0},
    )
    return restored


def step_render(j: Journey, asset_id: str, frames: int) -> None:
    """Step: bounce it — render, check the WAV and check the hum is gone."""
    chain = j.get("/api/chain").get("chain", [])
    j.check(bool(chain), "the chain is empty")
    answer = j.post("/api/render", {"asset_id": asset_id, "chain": chain})
    rendered_id = str(answer.get("asset_id") or "")
    j.check(bool(rendered_id), f"the render returned no asset_id: {answer}")
    warnings = answer.get("warnings") or []
    j.check(
        not warnings,
        f"the chain raised while it rendered: {'; '.join(str(w) for w in warnings)}",
    )
    j.record(
        "render: the chain runs",
        True,
        f"{rendered_id}" + (f" — warnings: {warnings}" if warnings else ""),
        measurements={"warnings": len(warnings)},
    )

    payload = j.ask("GET", f"/api/audio/{rendered_id}", raw=True)
    j.check(
        payload[:4] == b"RIFF" and payload[8:12] == b"WAVE",
        "the rendered file is not a RIFF/WAVE file",
    )
    rendered, sample_rate, channels = decode_wav(payload)
    j.check(
        rendered.shape[0] == frames,
        f"the render has {rendered.shape[0]} frames, expected {frames}",
    )
    j.check(channels == CHANNELS, f"the render has {channels} channels")
    j.check(sample_rate == SAMPLE_RATE, f"the render is at {sample_rate} Hz")
    j.check(bool(np.all(np.isfinite(rendered))), "the render contains NaN or infinity")
    j.record(
        "render: a valid WAV of the right length",
        True,
        f"{len(payload)} bytes, {rendered.shape[0]} frames, {channels} ch, {sample_rate} Hz",
        measurements={
            "bytes": len(payload),
            "frames": int(rendered.shape[0]),
            "channels": channels,
            "sample_rate": sample_rate,
        },
    )

    # the hum: the very same measurement on the original and on the render
    original_payload = j.ask("GET", f"/api/audio/{asset_id}", raw=True)
    original, _, _ = decode_wav(original_payload)
    before = amplitude_at(original, HUM_HZ, SAMPLE_RATE)
    after = amplitude_at(rendered, HUM_HZ, SAMPLE_RATE)
    reduction = db(before) - db(after)
    j.check(
        reduction >= HUM_REDUCTION_DB,
        f"the hum only dropped {reduction:.1f} dB "
        f"(50 Hz: {db(before):.1f} dB -> {db(after):.1f} dB), "
        f"expected at least {HUM_REDUCTION_DB:.0f} dB",
    )
    j.record(
        "render: the hum is measurably reduced",
        True,
        f"50 Hz {db(before):.1f} dB -> {db(after):.1f} dB ({reduction:.1f} dB)",
        measurements={
            "hum_before_db": round(db(before), 3),
            "hum_after_db": round(db(after), 3),
            "hum_reduction_db": round(reduction, 3),
        },
    )

    # the repair must not eat the programme: the 1 kHz tone has to survive
    tone_before = amplitude_at(original, TONE_HZ, SAMPLE_RATE)
    tone_after = amplitude_at(rendered, TONE_HZ, SAMPLE_RATE)
    loss = db(tone_before) - db(tone_after)
    try:
        j.check(
            loss <= PROGRAMME_LOSS_DB,
            f"the {TONE_HZ:.0f} Hz programme lost {loss:.1f} dB "
            f"({db(tone_before):.1f} dB -> {db(tone_after):.1f} dB) "
            "while the chain was supposed to repair the hum",
        )
    except Exception as exc:  # noqa: BLE001
        j.fail("render: the programme survives", exc)
    else:
        j.record(
            "render: the programme survives",
            True,
            f"{TONE_HZ:.0f} Hz {db(tone_before):.1f} dB -> {db(tone_after):.1f} dB "
            f"({loss:.1f} dB)",
            measurements={
                "tone_before_db": round(db(tone_before), 3),
                "tone_after_db": round(db(tone_after), 3),
                "tone_loss_db": round(loss, 3),
            },
        )


def step_presets(j: Journey) -> None:
    """Step: remember the settings — save, list, load."""
    module_code = "AUR_DEHUM"
    wanted = {
        "Base Frequency": 60.0,
        "Filter Q": 2.5,
        "Number of Harmonics": 4,
    }
    answer = j.post(
        "/api/preset/save",
        {
            "name": PRESET_NAME,
            "category": PRESET_CATEGORY,
            "module_code": module_code,
            "display_name": "De-Hum",
            "params": wanted,
        },
    )
    path = str(answer.get("path") or "")
    j.check(bool(path), f"the preset save returned no path: {answer}")
    j.check(
        path.endswith(f"{PRESET_NAME}.json"),
        f"the preset was saved under an unexpected name: {path}",
    )
    saved = answer.get("preset") or {}
    saved_params = saved.get("params") or {}
    j.check(bool(saved_params), f"the saved preset carries no parameters: {saved}")
    j.record(
        "preset: save",
        True,
        path,
        measurements={"parameters": len(saved_params)},
    )

    listing = j.get("/api/presets").get("presets")
    j.check(isinstance(listing, list), "the preset list is not a list")
    entries = [p for p in listing if p.get("path") == path]
    j.check(len(entries) == 1, f"the saved preset is not in the list exactly once: {path}")
    entry = entries[0]
    j.check(
        str(entry.get("module_code") or "") == module_code,
        f"the listing names the module {entry.get('module_code')!r}, expected {module_code!r}",
    )
    j.check(
        str(entry.get("category") or "") == PRESET_CATEGORY,
        f"the listing puts the preset in {entry.get('category')!r}, "
        f"expected {PRESET_CATEGORY!r}",
    )
    j.check(
        str(entry.get("name") or "") == PRESET_NAME,
        f"the listing names the preset {entry.get('name')!r}, expected {PRESET_NAME!r}",
    )
    j.record(
        "preset: list",
        True,
        "the new preset is listed once, with its module and its category",
        measurements={"listed": 1},
    )

    # the shipped library has to be there as well: the journey's own preset
    # is not the whole library
    shipped = [p for p in listing if p.get("builtin")]
    j.check(bool(shipped), "the library holds no shipped preset")
    j.record(
        "preset: the shipped library is there",
        True,
        "the shipped presets are listed as well",
        measurements={"shipped": 1},
    )

    loaded = j.post("/api/preset/load", {"path": path})
    params = loaded.get("params") or {}
    j.check(bool(params), f"the loaded preset has no parameters: {loaded}")
    for name, value in wanted.items():
        j.check(name in params, f"the loaded preset is missing {name!r}: {params}")
        got = unwrap_value(params[name])
        j.check(
            same_value(got, value),
            f"{name} came back as {got!r}, expected {value!r}",
        )
    module = loaded.get("module") or {}
    j.check(
        str(module.get("code") or "") == module_code,
        f"the loaded preset names the module {module.get('code')!r}, "
        f"expected {module_code!r}",
    )
    j.record(
        "preset: load and round-trip the values",
        True,
        ", ".join(wanted),
        measurements={"parameters": len(wanted)},
    )


def step_session(j: Journey) -> str:
    """Step: keep the session — save a session document with its chain."""
    chain = j.get("/api/chain").get("chain", [])
    j.check(bool(chain), "the chain is empty")
    answer = j.post("/api/session/save", {"name": SESSION_NAME})
    path = str(answer.get("path") or "")
    j.check(bool(path), f"the session save returned no path: {answer}")
    document = answer.get("document") or {}
    steps = (document.get("chain") or {}).get("steps")
    j.check(isinstance(steps, list), f"the saved document has no chain: {document}")
    j.check(
        len(steps) == len(chain),
        f"the saved document holds {len(steps)} steps, the chain holds {len(chain)}",
    )
    total = 0
    for index, step in enumerate(steps):
        live = chain[index]
        j.check(
            str(step.get("code") or "") == str(live.get("code") or ""),
            f"step {index} of the document is {step.get('code')!r}, "
            f"the chain holds {live.get('code')!r}",
        )
        document_params = step.get("params") or {}
        live_params = live.get("params") or {}
        j.check(
            set(live_params).issubset(set(document_params)),
            f"step {index} of the document is missing the parameters "
            f"{sorted(set(live_params) - set(document_params))}",
        )
        for name, node in live_params.items():
            j.check(
                same_value(unwrap_value(document_params.get(name)), unwrap_value(node)),
                f"step {index} of the document holds {name} = "
                f"{unwrap_value(document_params.get(name))!r}, "
                f"the chain holds {unwrap_value(node)!r}",
            )
        total += len(live_params)
    j.record(
        "session: save the document",
        True,
        f"{path} — {len(steps)} steps, {total} parameters",
        measurements={"steps": len(steps), "parameters": total},
    )
    return path


def step_session_list(j: Journey, path: str) -> None:
    """Step: the session document shows up in the list of saved sessions."""
    answer = j.get("/api/session/list")
    sessions = answer.get("sessions")
    j.check(isinstance(sessions, list), "the session list is not a list")
    entries = [s for s in sessions if s.get("path") == path or s.get("name") == SESSION_NAME]
    j.check(len(entries) == 1, f"the saved session is not in the list: {path}")
    chain = j.get("/api/chain").get("chain", [])
    j.check(
        int(entries[0].get("steps") or 0) == len(chain),
        f"the listed session holds {entries[0].get('steps')} steps, "
        f"the chain holds {len(chain)}",
    )
    j.record(
        "session: the document lists in the library",
        True,
        f"{entries[0].get('name')} — {entries[0].get('steps')} steps",
        measurements={"listed": 1},
    )


def step_session_load(j: Journey, path: str) -> None:
    """Step: load the session document — the chain and the parameters come back."""
    loaded = j.post("/api/session/load", {"path": path})
    restored = loaded.get("chain") or (loaded.get("document") or {}).get("chain") or {}
    steps = restored.get("steps")
    j.check(isinstance(steps, list) and bool(steps), f"the loaded document has no chain: {loaded}")
    live = j.get("/api/chain").get("chain", [])
    j.check(
        len(steps) == len(live),
        f"the loaded document holds {len(steps)} steps, the chain holds {len(live)}",
    )
    total = 0
    for index, step in enumerate(steps):
        j.check(
            str(step.get("code") or "") == str(live[index].get("code") or ""),
            f"step {index} of the loaded document is {step.get('code')!r}, "
            f"the chain holds {live[index].get('code')!r}",
        )
        j.check(
            bool(step.get("enabled", True)) != bool(live[index].get("bypass")),
            f"step {index} of the loaded document does not carry the bypass flag",
        )
        document_params = step.get("params") or {}
        live_params = live[index].get("params") or {}
        j.check(
            set(live_params).issubset(set(document_params)),
            f"step {index} of the loaded document is missing the parameters "
            f"{sorted(set(live_params) - set(document_params))}",
        )
        for name, node in live_params.items():
            j.check(
                same_value(unwrap_value(document_params.get(name)), unwrap_value(node)),
                f"step {index} of the loaded document holds {name} = "
                f"{unwrap_value(document_params.get(name))!r}, "
                f"the chain holds {unwrap_value(node)!r}",
            )
        total += len(live_params)
    j.record(
        "session: load the document",
        True,
        " -> ".join(str(s.get("code")) for s in steps),
        measurements={"steps": len(steps), "parameters": total},
    )


def step_undo_history(j: Journey) -> dict:
    """Step: undo — the history reverts a change and the chain is as it was."""
    try:
        from aurcore.api import create_engine
        from aurcore.undo import SetParamCommand, UndoStack
    except Exception as exc:  # noqa: BLE001 - the core has to be importable
        j.check(False, f"the core is not importable: {type(exc).__name__}: {exc}")
        return {}

    engine = create_engine(SAMPLE_RATE, CHANNELS)
    engine.add_module("AUR_DEHUM")
    before = chain_fingerprint(engine.chain_summary())

    info = engine.param_info(0)
    name = None
    old = None
    new = None
    for desc in info:
        current = engine.get_param(0, desc.name)
        if isinstance(current, bool) or not isinstance(current, (int, float)):
            continue
        low = float(desc.minimum)
        high = float(desc.maximum)
        if high <= low:
            continue
        target = round(low + (high - low) * 0.5, 6)
        if abs(target - float(current)) < 1e-9:
            continue
        name = str(desc.name)
        old = current
        new = target
        break
    try:
        j.check(name is not None, "De-Hum exposes no tunable parameter")
        stack = UndoStack()
        stack.execute(SetParamCommand(engine, 0, name, new))
        changed = chain_fingerprint(engine.chain_summary())
        j.check(changed != before, "the change did not reach the chain")
        j.check(
            abs(float(engine.get_param(0, name)) - float(new)) <= 1e-9,
            f"the parameter did not take the new value: {engine.get_param(0, name)}",
        )
        stack.undo()
        j.check(
            chain_fingerprint(engine.chain_summary()) == before,
            "the undo did not bring the chain back as it was",
        )
        j.check(
            abs(float(engine.get_param(0, name)) - float(old)) <= 1e-9,
            f"the undo did not restore the old value: {engine.get_param(0, name)}",
        )
        stack.redo()
        j.check(
            chain_fingerprint(engine.chain_summary()) == changed,
            "the redo did not replay the change",
        )
        summary = stack.summary()
        j.check(
            int(summary.get("depth") or 0) == 1,
            f"the history holds {summary.get('depth')} steps",
        )
        j.check(bool(summary.get("can_undo")), "the history cannot be undone")
        j.record(
            "undo: the change is reverted",
            True,
            f"{name}: {old} -> {new} -> {old}",
            measurements={"undo_depth": int(summary.get("depth") or 0)},
        )
        return summary
    except Exception as exc:  # noqa: BLE001
        j.fail("undo: the change is reverted", exc)
        return {}


def step_undo_document(j: Journey, history: dict) -> None:
    """Step: undo — the history travels into the session document."""
    j.check(bool(history), "there is no history to store")
    answer = j.post(
        "/api/session/save",
        {"name": UNDO_SESSION_NAME, "history": history},
    )
    document = answer.get("document") or {}
    block = document.get("history") or {}
    j.check(bool(block), "the saved document carries no history")
    j.check(
        int(block.get("depth") or 0) == int(history.get("depth") or 0),
        f"the document holds a history of {block.get('depth')} steps, "
        f"the history holds {history.get('depth')}",
    )
    j.check(
        list(block.get("labels") or []) == list(history.get("labels") or []),
        "the document holds other labels than the history",
    )
    loaded = j.post("/api/session/load", {"name": UNDO_SESSION_NAME})
    loaded_block = (loaded.get("document") or {}).get("history") or {}
    j.check(
        int(loaded_block.get("depth") or 0) == int(history.get("depth") or 0),
        f"the loaded document holds a history of {loaded_block.get('depth')} steps",
    )
    j.record(
        "undo: the history is in the document",
        True,
        f"depth {loaded_block.get('depth')}, labels {loaded_block.get('labels')}",
        measurements={"history_depth": int(loaded_block.get("depth") or 0)},
    )


def step_undo_chain(j: Journey) -> None:
    """Step: undo — the chain in the workbench comes back as it was.

    The workbench undoes a change by replaying the inverse operation
    through the ordinary chain routes, so that is what the journey does: it
    changes a parameter, reads the chain back, replays the inverse operation
    and reads the chain once more.  The two readings have to be identical.
    """
    before = j.get("/api/chain").get("chain", [])
    j.check(bool(before), "the chain is empty")
    fingerprint = chain_fingerprint(before)

    index = 0
    info = j.get(f"/api/chain/{index}/params")
    descs = info.get("params") or []
    values = info.get("values") or {}
    name = None
    old = None
    new = None
    for desc in descs:
        current = values.get(str(desc.get("name") or ""))
        if isinstance(current, bool) or not isinstance(current, (int, float)):
            continue
        low = float(desc.get("minimum", current))
        high = float(desc.get("maximum", current))
        if high <= low:
            continue
        target = round(low + (high - low) * 0.5, 6)
        if abs(target - float(current)) < 1e-9:
            continue
        name = str(desc.get("name"))
        old = current
        new = target
        break
    j.check(name is not None, "no parameter of the chain can be changed")

    j.post(f"/api/chain/{index}/param", {"name": name, "value": new})
    changed = j.get("/api/chain").get("chain", [])
    j.check(
        chain_fingerprint(changed) != fingerprint,
        "the change did not reach the chain",
    )
    changed_value = unwrap_value((changed[index].get("params") or {}).get(name))
    j.check(
        changed_value is not None and abs(float(changed_value) - float(new)) <= 1e-9,
        f"the parameter {name!r} did not take the new value: {changed_value!r}",
    )

    # the undo: the inverse operation, through the ordinary route
    j.post(f"/api/chain/{index}/param", {"name": name, "value": old})
    restored = j.get("/api/chain").get("chain", [])
    j.check(
        chain_fingerprint(restored) == fingerprint,
        "the chain did not come back as it was",
    )
    j.record(
        "undo: the chain comes back as it was",
        True,
        f"{name}: {old} -> {new} -> {old}",
        measurements={"value": float(old)},
    )


def step_delivery_targets(j: Journey) -> None:
    """Step: deliver it — the delivery catalogue."""
    answer = j.get("/api/delivery-targets")
    targets = answer.get("targets")
    j.check(
        isinstance(targets, list) and bool(targets),
        f"the delivery catalogue is empty: {answer}",
    )
    first = targets[0]
    for key in ("key", "name"):
        j.check(key in first, f"a delivery target has no {key!r}: {first}")
    keys = [str(t.get("key")) for t in targets]
    j.check(len(set(keys)) == len(keys), "the catalogue holds the same key twice")
    j.record(
        "delivery: the catalogue",
        True,
        f"{len(targets)} targets, first is {first.get('name')!r}",
        measurements={"targets": len(targets)},
    )


def step_compare(j: Journey, asset_id: str, frames: int) -> None:
    """Step: compare two chains — the compare route."""
    answer = j.post(
        "/api/compare",
        {
            "asset_id": asset_id,
            "chain_a": [{"code": "AUR_DEHUM"}],
            "chain_b": [],
        },
    )
    ids = answer.get("asset_ids") or {}
    id_a = str(ids.get("a") or "")
    id_b = str(ids.get("b") or "")
    j.check(bool(id_a) and bool(id_b), f"the compare route returned no two renders: {answer}")

    payload_a = j.ask("GET", f"/api/audio/{id_a}", raw=True)
    payload_b = j.ask("GET", f"/api/audio/{id_b}", raw=True)
    rendered_a, rate_a, channels_a = decode_wav(payload_a)
    rendered_b, rate_b, channels_b = decode_wav(payload_b)
    j.check(
        rendered_a.shape[0] == frames and rendered_b.shape[0] == frames,
        f"the two renders have {rendered_a.shape[0]} and {rendered_b.shape[0]} frames, "
        f"expected {frames}",
    )
    j.check(
        rate_a == SAMPLE_RATE and rate_b == SAMPLE_RATE,
        f"the two renders are at {rate_a} and {rate_b} Hz",
    )
    j.check(
        channels_a == CHANNELS and channels_b == CHANNELS,
        f"the two renders have {channels_a} and {channels_b} channels",
    )
    j.record(
        "compare: the two renders",
        True,
        f"{id_a} and {id_b} — {rendered_a.shape[0]} frames each",
        measurements={"frames": int(rendered_a.shape[0])},
    )

    # the two renders have to differ: the one with De-Hum has lost the hum
    hum_a = amplitude_at(rendered_a, HUM_HZ, SAMPLE_RATE)
    hum_b = amplitude_at(rendered_b, HUM_HZ, SAMPLE_RATE)
    reduction = db(hum_b) - db(hum_a)
    j.check(
        reduction >= HUM_REDUCTION_DB,
        f"the compare renders differ by only {reduction:.1f} dB at {HUM_HZ:.0f} Hz, "
        f"expected at least {HUM_REDUCTION_DB:.0f} dB",
    )
    tone_a = amplitude_at(rendered_a, TONE_HZ, SAMPLE_RATE)
    tone_b = amplitude_at(rendered_b, TONE_HZ, SAMPLE_RATE)
    loss = db(tone_b) - db(tone_a)
    j.check(
        loss <= PROGRAMME_LOSS_DB,
        f"the compare render with De-Hum lost {loss:.1f} dB of the "
        f"{TONE_HZ:.0f} Hz programme",
    )
    j.record(
        "compare: the hum is gone in one of them",
        True,
        f"50 Hz {db(hum_b):.1f} dB -> {db(hum_a):.1f} dB ({reduction:.1f} dB), "
        f"the programme lost {loss:.1f} dB",
        measurements={
            "hum_a_db": round(db(hum_a), 3),
            "hum_b_db": round(db(hum_b), 3),
            "hum_reduction_db": round(reduction, 3),
            "tone_loss_db": round(loss, 3),
        },
    )


def _clean_output_dir() -> int:
    """Remove the files the previous run delivered.

    The runner never overwrites a file, so the second run would deliver
    ``name_1.wav`` instead of ``name.wav``.  Only the files of the journey
    are removed; a file that is still being written (the runner stages it
    under a hidden name) is left alone.
    """
    removed = 0
    if not BATCH_OUT_DIR.is_dir():
        return removed
    for name, _gain in BATCH_SOURCES:
        stem = Path(name).stem
        for path in sorted(BATCH_OUT_DIR.glob(f"{stem}*.wav")):
            if path.is_file():
                path.unlink()
                removed += 1
    return removed


def step_batch(j: Journey, samples: np.ndarray, sample_rate: int) -> None:
    """Step: run it overnight — start a batch job, poll it, check the outputs."""
    JOURNEY_DIR.mkdir(parents=True, exist_ok=True)
    BATCH_OUT_DIR.mkdir(parents=True, exist_ok=True)
    _clean_output_dir()

    sources = []
    for name, gain in BATCH_SOURCES:
        path = JOURNEY_DIR / name
        path.write_bytes(encode_wav(np.asarray(samples) * float(gain), sample_rate))
        sources.append(path)

    answer = j.post(
        "/api/batch",
        {
            "paths": [str(path) for path in sources],
            "out_dir": str(BATCH_OUT_DIR),
            "format": "wav",
            "bit_depth": 24,
            "chain": [{"code": "AUR_DEHUM", "enabled": True, "params": {}}],
        },
    )
    job_id = str(answer.get("job_id") or "")
    j.check(bool(job_id), f"the batch job has no id: {answer}")
    j.record(
        "batch: start the job",
        True,
        f"{job_id} — {len(sources)} files",
        measurements={"files": len(sources)},
    )

    job: dict[str, Any] = {}
    deadline = time.time() + 120.0
    while time.time() < deadline:
        job = j.get(f"/api/batch/{job_id}")
        if str(job.get("state")) in ("done", "error", "cancelled"):
            break
        time.sleep(0.25)
    j.check(
        str(job.get("state")) == "done",
        f"the job ended in state {job.get('state')!r}: {job.get('error')}",
    )
    results = job.get("results") or []
    j.check(len(results) == len(sources), f"the job returned {len(results)} results")
    for result in results:
        j.check(
            not result.get("error"),
            f"the job failed on {result.get('input')}: {result.get('error')}",
        )
        j.check(
            str(result.get("status") or "delivered") == "delivered",
            f"the file was not delivered: {result.get('status')}",
        )
    j.record(
        "batch: the job finished",
        True,
        f"{job.get('done')} done, {job.get('failed')} failed",
        measurements={
            "files": len(results),
            "done": int(job.get("done") or 0),
            "failed": int(job.get("failed") or 0),
        },
    )

    # every delivered file has to be on disk and have the right shape
    expected_frames = int(round(DURATION * SAMPLE_RATE))
    for result in results:
        output = Path(str(result.get("output") or ""))
        j.check(output.is_file(), f"the output file does not exist: {output}")
        processed, rate, channels = decode_wav(output.read_bytes())
        j.check(
            processed.shape[0] == expected_frames,
            f"the batch output has {processed.shape[0]} frames, expected {expected_frames}",
        )
        j.check(rate == SAMPLE_RATE, f"the batch output is at {rate} Hz")
        j.check(channels == CHANNELS, f"the batch output has {channels} channels")
    j.record(
        "batch: the output exists",
        True,
        f"{len(results)} files of {expected_frames} frames",
        measurements={
            "outputs": len(results),
            "frames": expected_frames,
            "channels": CHANNELS,
            "sample_rate": SAMPLE_RATE,
        },
    )

    # every delivered file carries its own loudness
    values: list[float] = []
    for result in results:
        loud = result.get("loudness_lufs")
        j.check(loud is not None, f"the delivered file carries no loudness: {result.get('output')}")
        value = float(loud)
        j.check(math.isfinite(value), f"the loudness of {result.get('output')} is {value}")
        values.append(value)
    j.check(len(values) == len(results), "not every delivered file carries a loudness")
    j.record(
        "batch: every delivered file has a loudness",
        True,
        ", ".join(f"{value:.1f} LUFS" for value in values),
        measurements={
            "files": len(values),
            "lufs_min": round(min(values), 3) if values else 0.0,
            "lufs_max": round(max(values), 3) if values else 0.0,
        },
    )

    # the louder source has to be the louder delivery
    loud_result = None
    quiet_result = None
    for result in results:
        name = Path(str(result.get("input") or "")).name
        if name == BATCH_SOURCES[0][0]:
            loud_result = result
        elif name == BATCH_SOURCES[1][0]:
            quiet_result = result
    j.check(loud_result is not None and quiet_result is not None, "the batch lost a file")
    difference = float(loud_result["loudness_lufs"]) - float(quiet_result["loudness_lufs"])
    j.check(
        difference >= LOUDNESS_ORDER_MIN_LU,
        f"the louder source was delivered {difference:.1f} LU below the quieter one",
    )
    j.record(
        "batch: the loudness follows the input",
        True,
        f"{BATCH_SOURCES[0][0]} {float(loud_result['loudness_lufs']):.1f} LUFS, "
        f"{BATCH_SOURCES[1][0]} {float(quiet_result['loudness_lufs']):.1f} LUFS",
        measurements={"lufs_difference": round(difference, 3)},
    )


# ---------------------------------------------------------------------------
# the runner
# ---------------------------------------------------------------------------
def run(base_url: str, verbose: bool = True, stream=None) -> Journey:
    """Run the whole journey against ``base_url``."""
    j = Journey(base_url, verbose=verbose, stream=stream)
    if verbose:
        print(f"Auralis product journey — {j.base_url}\n", file=j.stream, flush=True)

    samples, sample_rate = build_signal()
    wav = encode_wav(samples, sample_rate)
    frames = int(samples.shape[0])

    # 1. the workbench answers
    ok, _ = _step(j, "the workbench answers", step_health)
    if not ok:
        return j

    # 2. the module catalogue
    ok, _ = _step(j, "the module catalogue", step_catalogue)
    if not ok:
        return j

    # 3. load the file
    asset_id = ""
    try:
        asset_id = step_upload(j, wav, frames)
    except Exception as exc:  # noqa: BLE001
        j.fail("upload the recording", exc)
        return j

    # 4. look at it
    _step(j, "analysis: the analysis of the recording", step_analyse, asset_id)
    _step(
        j,
        "analysis: the analysis of a selection",
        step_analyse_selection,
        asset_id,
        frames,
    )

    # 5. build the chain
    _step(j, "chain: build the chain", step_chain)

    # 6. bounce it
    _step(j, "render: bounce the chain", step_render, asset_id, frames)

    # 7. the presets
    _step(j, "preset: the preset library", step_presets)

    # 8. the session document
    session_path = ""
    try:
        session_path = step_session(j)
    except Exception as exc:  # noqa: BLE001
        j.fail("session: save the document", exc)
    if session_path:
        _step(j, "session: the session library", step_session_list, session_path)
        _step(j, "session: load the document", step_session_load, session_path)

    # 9. undo
    ok, history = _step(j, "undo: the history", step_undo_history)
    _step(j, "undo: the history in the document", step_undo_document, history or {})
    _step(j, "undo: the chain", step_undo_chain)

    # 10. compare two chains
    _step(j, "compare: two chains", step_compare, asset_id, frames)

    # 11. the delivery catalogue
    _step(j, "delivery: the catalogue", step_delivery_targets)

    # 12. the batch job
    _step(j, "batch: the overnight job", step_batch, samples, sample_rate)

    return j


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="the Auralis product journey")
    parser.add_argument(
        "--base-url",
        default="",
        help="the base URL of an already running server (default: start one)",
    )
    parser.add_argument("--port", type=int, default=0, help="the port of the server to start")
    parser.add_argument("--json", action="store_true", help="print the summary as JSON")
    parser.add_argument("--keep", action="store_true", help="leave the server running")
    args = parser.parse_args(argv)

    server: ServerProcess | None = None
    if args.base_url:
        base_url = args.base_url
    else:
        server = ServerProcess(args.port or free_port())
        try:
            server.start()
        except Exception as exc:  # noqa: BLE001
            print(f"could not start the server: {exc}", file=sys.stderr)
            return 2
        base_url = server.base_url

    # with --json the step lines go to stderr, so stdout stays machine readable
    stream = sys.stderr if args.json else sys.stdout
    try:
        journey = run(base_url, stream=stream)
    finally:
        if server is not None and not args.keep:
            server.stop()

    summary = journey.summary()
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print()
        print("=" * 68)
        print(
            f"  {summary['passed']}/{summary['total']} journey steps passed "
            f"({summary['assertions']} assertions)"
        )
        failures = [s for s in journey.steps if not s["ok"]]
        if failures:
            print("  failures:")
            for step in failures:
                print(f"    - {step['name']}: {step['detail']}")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
