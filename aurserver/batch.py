"""
aurserver.batch — unattended batch processing.

A job runs a chain over a list of files on a single background worker thread
and writes the result to disk.  Every file is processed independently: one bad
file is recorded as a failed result and never stops the rest of the job.

    POST /api/batch        -> {"job_id": "..."}   (job starts in the background)
    GET  /api/batch/{id}   -> {"state", "total", "done", "failed", "results": [...]}

What the job delivers
---------------------

The job measures the file it wrote, not the buffer it went into.  Every
result carries the integrated loudness of the delivered file, the error
against the target level, whether the file is inside the tolerance of the
target, the sample peak and the true peak.

When the job knows which delivery it has to meet — a row of the delivery
catalogue, or a chain that carries a loudness stage — it corrects the file
until it is on the target.  The correction aims at the headroom line of the
target: the true-peak ceiling less a small margin, so that the delivered
file keeps a hair of headroom.  The margin is a preference and not a limit:
a file that is under the ceiling is legal and is delivered, and the excess
over the headroom line is reported as a note.  A file that would clip is
not delivered, and a file whose true peak is still over the ceiling is
refused.  A target that cannot be met is reported as a warning in the
result, never as a silent pass.

Security and safety guards:

* input paths and the output directory must live inside the allowed roots
  (``app.state.batch_roots`` or ``AURALIS_BATCH_ROOTS``, otherwise
  ``<repo>/var``);
* the number of files per job is capped;
* an output never overwrites its own input;
* an output is always written inside the output directory, under a
  temporary name, and moved into place only when it is complete;
* a file that was not fully written is removed instead of being left behind;
* a job can be cancelled while it runs, and a cancelled file is not left
  half written.
"""
from __future__ import annotations

import argparse
import importlib
import inspect
import json
import logging
import math
import os
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import numpy as np
from fastapi import APIRouter, HTTPException, Request

from .batch_formats import (
    DITHER_BIT_DEPTHS,
    DITHER_MODULE,
    LOUDNESS_MODULE,
    PEAK_EPSILON_DB,
    PEAK_MARGIN_DB,
    DeliveryMeasurement,
    UnknownTargetError,
    dither_stage,
    get_target,
    headroom_line_dbtp,
    loudness_stage,
    measure_delivery,
    plan_gain,
    safe_stem,
)

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Maximum number of files one job may contain.
MAX_BATCH_FILES = 200
#: Maximum length of one input file, in seconds.  ``0`` disables the limit.
MAX_FILE_SECONDS = 3600.0
#: Default output bit depth.
DEFAULT_BIT_DEPTH = 24
#: Default loudness target.
DEFAULT_TARGET_LUFS = -23.0
#: The tolerance the plain loudness normalisation accepts, in LU.
DEFAULT_TOLERANCE_LU = 0.5
#: The true-peak ceiling the plain loudness normalisation keeps, in dBTP.
DEFAULT_TRUE_PEAK_CEILING_DBTP = -1.0
#: Sample rate used when the chain is validated before the job starts.
DEFAULT_SAMPLE_RATE = 48000
#: How many correction rounds one file may take.
MAX_CORRECTION_ROUNDS = 3
#: A gain below this is not worth another write.
MIN_CORRECTION_DB = 0.05

_FORMATS = ("wav", "flac")
_BIT_DEPTHS = (16, 24, 32)
_SUBTYPES = {
    "wav": {16: "PCM_16", 24: "PCM_24", 32: "PCM_32"},
    "flac": {16: "PCM_16", 24: "PCM_24"},
}

router = APIRouter(tags=["batch"])


class BatchCancelled(RuntimeError):
    """Raised inside the worker when the job was cancelled.

    The file that was being written is not delivered, and the half written
    file is removed.
    """


# ---------------------------------------------------------------------------
# shared chain builder
# ---------------------------------------------------------------------------
def build_chain_engine(chain: Iterable[dict] | None, sample_rate: int, channels: int = 2):
    """Build an engine from a JSON chain description.

    Thin wrapper around the shared builder in :mod:`aurserver.ws`; the import
    is deferred so that importing this module never pulls in the WebSocket
    machinery at start-up.
    """
    from .ws import build_chain_engine as _build

    return _build(chain, sample_rate, channels)


# ---------------------------------------------------------------------------
# the delivery a job has to meet
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DeliverySpec:
    """The delivery a batch job has to meet.

    ``key`` names the row of the catalogue the delivery comes from,
    ``integrated_lufs`` is the target level, ``tolerance_lu`` the deviation
    that is accepted and ``true_peak_dbtp`` the ceiling the delivered file
    has to stay under.
    """

    key: str
    name: str
    integrated_lufs: float
    tolerance_lu: float
    true_peak_dbtp: float

    @classmethod
    def from_target(cls, target: Any) -> "DeliverySpec":
        """Build the specification of a row of the delivery catalogue."""
        row = get_target(target)
        return cls(
            key=row.key,
            name=row.name,
            integrated_lufs=float(row.integrated_lufs),
            tolerance_lu=float(row.tolerance_lu),
            true_peak_dbtp=float(row.true_peak_dbtp),
        )

    def as_dict(self) -> dict[str, Any]:
        """Return the specification as a JSON record."""
        return {
            "key": self.key,
            "name": self.name,
            "integrated_lufs": self.integrated_lufs,
            "tolerance_lu": self.tolerance_lu,
            "true_peak_dbtp": self.true_peak_dbtp,
        }


def add_delivery_stages(chain: Sequence[dict] | None, target: Any) -> list[dict]:
    """Return the chain with the delivery stages of a target added.

    A stage that the chain already carries is not added twice: the user
    chain keeps its own loudness stage and its own dither stage, and the
    runner only fills in what is missing.
    """
    row = get_target(target)
    steps = [dict(step) for step in (chain or []) if isinstance(step, dict)]
    codes = {str(step.get("code") or "") for step in steps}
    if LOUDNESS_MODULE not in codes:
        steps.append(loudness_stage(row))
    if DITHER_MODULE not in codes and int(row.bit_depth) in DITHER_BIT_DEPTHS:
        steps.append(dither_stage(int(row.bit_depth)))
    return steps


def _chain_params(step: dict) -> dict[str, Any]:
    """Return the parameters of a chain step as plain values."""
    params = step.get("params")
    if not isinstance(params, dict):
        return {}
    try:
        from aurcore.serial.typed_value import unwrap_all

        return {str(key): unwrap_all(params)[key] for key in params}
    except Exception:  # pragma: no cover - the core is always present in practice
        return {str(key): value for key, value in params.items()}


def spec_from_chain(chain: Sequence[dict] | None) -> DeliverySpec | None:
    """Read the delivery out of a chain that carries a loudness stage.

    The loudness stage names the level, the tolerance and the true-peak
    ceiling it works to, so a chain that carries one already says which
    delivery the runner has to meet.  A chain without one says nothing, and
    the runner then only measures.
    """
    for step in chain or []:
        if not isinstance(step, dict):
            continue
        if str(step.get("code") or "") != LOUDNESS_MODULE:
            continue
        params = _chain_params(step)
        try:
            level = float(params.get("Target Loudness"))
        except (TypeError, ValueError):
            return None
        try:
            ceiling = float(params.get("True Peak", DEFAULT_TRUE_PEAK_CEILING_DBTP))
        except (TypeError, ValueError):
            ceiling = DEFAULT_TRUE_PEAK_CEILING_DBTP
        try:
            tolerance = float(params.get("Tolerance", DEFAULT_TOLERANCE_LU))
        except (TypeError, ValueError):
            tolerance = DEFAULT_TOLERANCE_LU
        return DeliverySpec(
            key="chain",
            name="the loudness stage of the chain",
            integrated_lufs=level,
            tolerance_lu=tolerance,
            true_peak_dbtp=ceiling,
        )
    return None


# ---------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------
@dataclass
class BatchJob:
    """The state of one batch job."""

    job_id: str
    state: str = "queued"
    total: int = 0
    done: int = 0
    failed: int = 0
    results: list[dict] = field(default_factory=list)
    out_dir: str = ""
    target: str | None = None
    error: str | None = None
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    cancelled: bool = False
    warnings: list[str] = field(default_factory=list)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def cancel(self) -> None:
        """Ask the job to stop as soon as it can.

        The flag stops the job between two files; the event stops the file
        that is running, so a cancelled job does not have to wait for a long
        file to finish.
        """
        self.cancelled = True
        self.cancel_event.set()

    def cancel_requested(self) -> bool:
        """True when the job was asked to stop."""
        return self.cancelled or self.cancel_event.is_set()

    def to_dict(self) -> dict:
        """Serialise the job for the API."""
        return {
            "job_id": self.job_id,
            "state": self.state,
            "total": self.total,
            "done": self.done,
            "failed": self.failed,
            "results": list(self.results),
            "out_dir": self.out_dir,
            "target": self.target,
            "error": self.error,
            "created": self.created,
            "started": self.started,
            "finished": self.finished,
            "cancelled": self.cancelled,
            "warnings": list(self.warnings),
        }


_JOBS: dict[str, BatchJob] = {}
_JOBS_LOCK = threading.Lock()
_EXECUTOR: ThreadPoolExecutor | None = None
_EXECUTOR_LOCK = threading.Lock()


def _executor() -> ThreadPoolExecutor:
    """Return the shared single-worker executor."""
    global _EXECUTOR
    with _EXECUTOR_LOCK:
        if _EXECUTOR is None:
            _EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="auralis-batch")
        return _EXECUTOR


def get_job(job_id: str) -> BatchJob | None:
    """Return a job by id, or ``None``."""
    with _JOBS_LOCK:
        return _JOBS.get(str(job_id))


# ---------------------------------------------------------------------------
# guards
# ---------------------------------------------------------------------------
def allowed_roots(app: Any = None) -> list[Path]:
    """Return the directories a batch job may read from and write to.

    Configuration comes from ``app.state.batch_roots`` (a path or a list of
    paths) and from the ``AURALIS_BATCH_ROOTS`` environment variable
    (paths separated by ``os.pathsep``).  When neither is set the library
    falls back to ``<repo>/var``.
    """
    roots: list[Path] = []
    state = getattr(app, "state", None)
    configured = getattr(state, "batch_roots", None) if state is not None else None
    if configured:
        items = configured if isinstance(configured, (list, tuple, set)) else [configured]
        roots.extend(Path(str(item)).expanduser() for item in items)
    env = os.environ.get("AURALIS_BATCH_ROOTS")
    if env:
        roots.extend(Path(part).expanduser() for part in env.split(os.pathsep) if part.strip())
    if not roots:
        roots.append(REPO_ROOT / "var")
    resolved: list[Path] = []
    for root in roots:
        try:
            path = root.resolve()
        except OSError:
            continue
        if path not in resolved:
            resolved.append(path)
    return resolved


def _inside_roots(path: Path, roots: list[Path]) -> bool:
    return any(path == root or path.is_relative_to(root) for root in roots)


def resolve_input_path(raw: Any, roots: list[Path]) -> Path:
    """Resolve one input path and refuse anything outside the allowed roots."""
    text = str(raw if raw is not None else "").strip()
    if not text:
        raise HTTPException(400, "path must not be empty")
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = REPO_ROOT / candidate
    try:
        resolved = candidate.resolve()
    except OSError:
        raise HTTPException(400, f"path cannot be resolved: {text}") from None
    if not _inside_roots(resolved, roots):
        raise HTTPException(400, f"path is outside the allowed roots: {text}")
    return resolved


def resolve_out_dir(app: Any, raw: Any, roots: list[Path]) -> Path:
    """Resolve the output directory and refuse anything outside the roots."""
    text = str(raw if raw is not None else "").strip()
    if not text:
        base = roots[0] / "batch" if roots else REPO_ROOT / "var" / "batch"
    else:
        candidate = Path(text).expanduser()
        if not candidate.is_absolute():
            candidate = REPO_ROOT / candidate
        base = candidate.resolve()
    if not _inside_roots(base, roots):
        raise HTTPException(400, f"out_dir is outside the allowed roots: {text or base}")
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(500, f"cannot create the output directory: {exc}") from None
    return base


# ---------------------------------------------------------------------------
# loudness
# ---------------------------------------------------------------------------
_LOUDNESS_SOURCES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "aurcore.dsp.loudness",
        ("integrated_loudness", "integrated_lufs", "measure_lufs", "loudness_lufs", "measure",
         "lufs"),
    ),
    (
        "aurcore.dsp.loudness.itu",
        ("integrated_loudness", "integrated_lufs", "measure_lufs", "loudness_lufs", "measure",
         "lufs"),
    ),
    (
        "aurcore.dsp.loudness.ebur128",
        ("integrated_loudness", "integrated_lufs", "measure_lufs", "loudness_lufs", "measure",
         "lufs"),
    ),
    (
        "aurcore.modules.loudness",
        ("integrated_loudness", "integrated_lufs", "measure_lufs", "loudness_lufs", "measure",
         "lufs"),
    ),
    (
        "aurcore.api",
        ("integrated_loudness", "integrated_lufs", "measure_lufs", "measure_loudness", "loudness"),
    ),
)

_LOUDNESS_UNSET = object()
_LOUDNESS_CACHE: list[Any] = [_LOUDNESS_UNSET]


def reset_loudness_cache() -> None:
    """Forget the cached loudness measurement.

    The core may gain its loudness module after this process started; calling
    this makes the next job look for it again.
    """
    _LOUDNESS_CACHE[0] = _LOUDNESS_UNSET


def loudness_measure() -> Callable[[np.ndarray, int], float] | None:
    """Return a loudness measurement callable, or ``None`` when unavailable.

    The core is the single source of truth for loudness.  When it does not
    expose a measurement yet, batch processing reports that loudness
    normalisation was skipped instead of guessing a number.
    """
    if _LOUDNESS_CACHE[0] is not _LOUDNESS_UNSET:
        return _LOUDNESS_CACHE[0]
    found: Callable | None = None
    for module_name, attributes in _LOUDNESS_SOURCES:
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue
        for attribute in attributes:
            candidate = getattr(module, attribute, None)
            if callable(candidate):
                found = _wrap_measure(candidate)
                break
        if found:
            break
    _LOUDNESS_CACHE[0] = found
    return found


def _wrap_measure(function: Callable) -> Callable[[np.ndarray, int], float]:
    """Adapt a core loudness function to ``(samples, sample_rate) -> LUFS``."""

    def measure(samples: np.ndarray, sample_rate: int) -> float:
        try:
            signature = inspect.signature(function)
        except (TypeError, ValueError):  # pragma: no cover - builtins
            signature = None
        if signature is not None:
            try:
                signature.bind(samples, sample_rate)
            except TypeError:
                return float(function(samples))
        return float(function(samples, sample_rate))

    return measure


def normalise_loudness(
    samples: np.ndarray, sample_rate: int, target_lufs: float
) -> tuple[np.ndarray, float, str | None]:
    """Normalise ``samples`` to ``target_lufs`` in memory.

    This is the buffer level helper: it measures the buffer it is given.
    The batch job itself measures the file it wrote and corrects that file,
    so the number in the report describes the delivered master.
    """
    measure = loudness_measure()
    if measure is None:
        return samples, 0.0, (
            "loudness normalisation skipped: the core exposes no loudness measurement"
        )
    try:
        measured = float(measure(samples, sample_rate))
    except Exception as exc:
        return samples, 0.0, f"loudness normalisation skipped: {exc}"
    if not math.isfinite(measured):
        return samples, 0.0, "loudness normalisation skipped: the measurement was not finite"

    gain_db = float(target_lufs) - measured
    note: str | None = None
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak > 0.0:
        ceiling = -1.0 - 20.0 * math.log10(peak)
        if gain_db > ceiling:
            gain_db = ceiling
            note = f"gain limited to {gain_db:.2f} dB to keep the peak below -1 dBFS"
    return samples * (10.0 ** (gain_db / 20.0)), gain_db, note


# ---------------------------------------------------------------------------
# audio IO
# ---------------------------------------------------------------------------
def read_audio(path: Path) -> tuple[np.ndarray, int]:
    """Read an audio file as float64 ``(n, ch)``."""
    import soundfile as sf

    data, sample_rate = sf.read(str(path), always_2d=True, dtype="float64")
    arr = np.asarray(data, dtype=np.float64)
    if arr.ndim == 1:
        arr = arr[:, None]
    return arr, int(sample_rate)


def _subtype_for(fmt: str, bit_depth: int) -> tuple[str, str]:
    """Return the subtype of a format and a bit depth, plus a note."""
    table = _SUBTYPES[fmt]
    subtype = table.get(int(bit_depth))
    if subtype is None:
        depth = max(table)
        return table[depth], f"{fmt} does not support {bit_depth} bit, wrote {depth} bit"
    return subtype, ""


def stage_audio(
    path: Path, samples: np.ndarray, sample_rate: int, fmt: str, bit_depth: int
) -> tuple[Path, str]:
    """Write the audio next to its destination, under a temporary name.

    The file is not moved into place here: the caller measures it first and
    only then commits it.  A file that was not fully written therefore never
    appears under its own name, and the temporary file is removed when the
    write fails.
    """
    import soundfile as sf

    subtype, note = _subtype_for(fmt, bit_depth)
    arr = np.asarray(samples, dtype=np.float64)
    if arr.ndim == 1:
        arr = arr[:, None]
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.part")
    try:
        sf.write(str(temporary), arr, int(sample_rate), subtype=subtype, format=fmt.upper())
    except BaseException:
        discard_staged(temporary)
        raise
    return temporary, note


def commit_staged(temporary: Path, path: Path) -> None:
    """Move a staged file into its place.

    The move is atomic on every file system the runner is used on, so the
    destination either holds the complete file or the file that was there
    before — never a part of one.
    """
    os.replace(str(temporary), str(path))


def discard_staged(temporary: Path | None) -> None:
    """Remove a staged file that is not going to be delivered."""
    if temporary is None:
        return
    try:
        Path(temporary).unlink()
    except OSError:
        pass


def write_audio(path: Path, samples: np.ndarray, sample_rate: int, fmt: str, bit_depth: int) -> str:
    """Write the processed audio; returns the subtype that was used.

    The audio is written under a temporary name and moved into place, so an
    interrupted write never leaves a part of a file behind.
    """
    temporary, note = stage_audio(path, samples, sample_rate, fmt, bit_depth)
    try:
        commit_staged(temporary, path)
    except BaseException:
        discard_staged(temporary)
        raise
    return note


def output_path_for(
    source: Path, out_dir: Path, fmt: str, *, reserved: Iterable[Path] = ()
) -> Path:
    """Pick an output path inside ``out_dir`` that never overwrites a source.

    The name is derived from the source, but it is sanitised: a source whose
    name would escape the output directory never produces a path outside of
    it.  A path that is one of the sources of the batch is skipped, and an
    existing file is never overwritten either.
    """
    base = Path(out_dir).resolve()
    extension = "wav" if fmt == "wav" else "flac"
    stem = safe_stem(source)
    taken: set[Path] = set()
    for item in list(reserved) + [source]:
        try:
            taken.add(Path(item).resolve())
        except OSError:  # pragma: no cover - defensive
            continue
    counter = 0
    while True:
        name = stem if counter == 0 else f"{stem}_{counter}"
        candidate = base / f"{name}.{extension}"
        try:
            resolved = candidate.resolve()
        except OSError:  # pragma: no cover - defensive
            resolved = candidate
        if resolved not in taken and not candidate.exists():
            return candidate
        counter += 1


# ---------------------------------------------------------------------------
# the delivery check
# ---------------------------------------------------------------------------
def _delivery_score(
    measured: DeliveryMeasurement, spec: DeliverySpec | None
) -> tuple[float, float]:
    """Rank a delivered file against the delivery it has to meet.

    The score is a pair, and the pair is compared from the left.

    The first term is the amount by which the file is over the **ceiling**
    of the delivery.  It is the term that decides whether a file is legal
    at all: a file that clips is worse than a file that is a little off its
    target.  The margin is not part of it — a file that is under the
    ceiling is legal, whatever the last bit of the meter estimate says.

    The second term is the price of the file in the two currencies of the
    delivery: the loudness it misses its target by (beyond the tolerance
    that is accepted) and the headroom it does not keep under the ceiling.
    The margin is a price and not a wall: a correction that buys more
    loudness than the margin is worth is applied even when it lands a hair
    over the headroom line, and one that buys less is not.

    ``(0.0, 0.0)`` means the file is on the target and keeps the margin.
    """
    if spec is None:
        return (0.0, 0.0)
    peak = float(measured.ceiling_peak_dbtp)
    over = peak - spec.true_peak_dbtp
    if over < 0.0:
        over = 0.0
    shortfall = peak - headroom_line_dbtp(spec.true_peak_dbtp)
    if shortfall < 0.0:
        shortfall = 0.0
    loud = measured.loudness_lufs
    outside = 0.0
    if loud is not None:
        outside = abs(float(loud) - spec.integrated_lufs) - spec.tolerance_lu
        if outside < 0.0:
            outside = 0.0
    return (over, outside + shortfall)


def _headroom_note(measured: DeliveryMeasurement, spec: DeliverySpec) -> str | None:
    """Return the note that reports the headroom the delivered file keeps.

    The note is written when the file is closer to the ceiling than the
    margin allows.  The excess over the headroom line is named, so that a
    correction that lands a hair over the line is reported instead of being
    thrown away, and so that a file that sits on the ceiling is not passed
    on in silence.
    """
    peak = float(measured.ceiling_peak_dbtp)
    ceiling = spec.true_peak_dbtp
    line = headroom_line_dbtp(ceiling)
    if peak <= line:
        return None
    if peak > ceiling:
        if peak > ceiling + PEAK_EPSILON_DB:
            # The file is over the ceiling by more than the slack of the
            # meter: the refusal reports it, not this note.
            return None
        return (
            f"the delivered file is {peak - ceiling:.6f} dB over the "
            f"{ceiling:.1f} dBTP ceiling, inside the {PEAK_EPSILON_DB:.2f} dB "
            f"slack of the meter"
        )
    return (
        f"the delivered file keeps {ceiling - peak:.6f} dB of headroom under the "
        f"{ceiling:.1f} dBTP ceiling; the {PEAK_MARGIN_DB:.2f} dB margin is short "
        f"by {peak - line:.6f} dB"
    )


def _format_number(value: Any, digits: int = 2) -> str:
    """Return a number for a message; ``n/a`` when there is none."""
    if value is None:
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not math.isfinite(number):
        return "n/a"
    return f"{number:.{digits}f}"


# ---------------------------------------------------------------------------
# the worker
# ---------------------------------------------------------------------------
def _process_file(
    source: Path,
    chain: list,
    out_dir: Path,
    fmt: str,
    bit_depth: int,
    spec: DeliverySpec | None,
    cancel: threading.Event,
    reserved: Iterable[Path] = (),
) -> dict:
    """Process one file; every failure is reported, never raised.

    The file is written under a temporary name, measured on disk and
    corrected until it meets the delivery the job has to make.  Only then is
    it moved into place, so the output directory never holds a file that was
    not fully written.
    """
    started = time.monotonic()
    result: dict[str, Any] = {
        "input": str(source),
        "output": None,
        "error": None,
        "status": "failed",
        "duration": 0.0,
        "elapsed": 0.0,
        "gain_db": 0.0,
        "loudness_lufs": None,
        "target_lufs": None,
        "loudness_error_lu": None,
        "tolerance_lu": None,
        "within_tolerance": None,
        "peak_dbfs": None,
        "true_peak_dbtp": None,
        "ceiling_peak_dbtp": None,
        "true_peak_ceiling_dbtp": None,
        "before": None,
        "after": None,
        "notes": [],
        "warnings": [],
    }
    notes: list[str] = []
    warnings: list[str] = []
    if spec is not None:
        result["target_lufs"] = spec.integrated_lufs
        result["tolerance_lu"] = spec.tolerance_lu
        result["true_peak_ceiling_dbtp"] = spec.true_peak_dbtp
        result["target"] = spec.key
    best_staged: Path | None = None
    current_staged: Path | None = None
    try:
        if cancel.is_set():
            raise BatchCancelled("the job was cancelled before the file was read")
        samples, sample_rate = read_audio(source)
        result["duration"] = samples.shape[0] / float(sample_rate)
        if MAX_FILE_SECONDS and result["duration"] > MAX_FILE_SECONDS:
            raise ValueError(
                f"file is longer than the {MAX_FILE_SECONDS:.0f} second batch limit"
            )
        if cancel.is_set():
            raise BatchCancelled("the job was cancelled before the file was processed")
        engine = build_chain_engine(chain, sample_rate, samples.shape[1])
        processed = np.asarray(engine.process(samples), dtype=np.float64)
        if processed.ndim == 1:
            processed = processed[:, None]
        if processed.shape[0] != samples.shape[0]:
            raise ValueError(
                f"the chain changed the length of the file ({samples.shape[0]} -> "
                f"{processed.shape[0]})"
            )
        if processed.shape[1] != samples.shape[1]:
            raise ValueError(
                f"the chain changed the channel count of the file "
                f"({samples.shape[1]} -> {processed.shape[1]})"
            )
        if cancel.is_set():
            raise BatchCancelled("the job was cancelled before the file was written")
        target_path = output_path_for(source, out_dir, fmt, reserved=reserved)

        current_staged, note = stage_audio(target_path, processed, sample_rate, fmt, bit_depth)
        if note:
            notes.append(note)
        measurement = measure_delivery(current_staged)
        first_measurement = measurement
        best_staged = current_staged
        best_measurement = measurement
        best_score = _delivery_score(measurement, spec)
        applied = 0.0
        limited_by_peak = False
        wanted_db = 0.0

        # Correct the delivered file until it is on the target.  The gain is
        # bounded by the headroom line of the delivery: the ceiling less the
        # margin, so that the file that lands on disk keeps a hair of
        # headroom.  Every round writes a new file under a temporary name;
        # the best one is kept and moved into place at the end.
        for _ in range(MAX_CORRECTION_ROUNDS):
            if cancel.is_set():
                raise BatchCancelled("the job was cancelled while the file was corrected")
            if spec is None:
                break
            plan = plan_gain(best_measurement, spec.integrated_lufs, spec.true_peak_dbtp)
            wanted_db = plan.wanted_db
            # The ceiling is what limits the gain even when the gain it
            # allows is too small to be applied: the report has to say so, or
            # a file that misses its target has no reason in it.
            limited_by_peak = limited_by_peak or plan.limited_by_peak
            if abs(plan.gain_db) < MIN_CORRECTION_DB:
                break
            processed = processed * (10.0 ** (plan.gain_db / 20.0))
            corrected, note = stage_audio(target_path, processed, sample_rate, fmt, bit_depth)
            if note and note not in notes:
                notes.append(note)
            candidate = measure_delivery(corrected)
            score = _delivery_score(candidate, spec)
            if score < best_score:
                discard_staged(best_staged)
                best_staged = corrected
                best_measurement = candidate
                best_score = score
                # The gain counts only when the file it produced is the file
                # that is delivered: a rejected correction changes nothing.
                applied += plan.gain_db
            else:
                # The correction did not bring the file any closer to the
                # delivery: keep the file that was better.
                discard_staged(corrected)
                break
        if current_staged != best_staged:
            discard_staged(current_staged)
        current_staged = None

        measurement = best_measurement
        result["before"] = first_measurement.as_dict()
        result["after"] = measurement.as_dict()
        result["gain_db"] = applied
        result["loudness_lufs"] = measurement.loudness_lufs
        result["peak_dbfs"] = measurement.peak_dbfs
        result["true_peak_dbtp"] = measurement.true_peak_dbtp
        result["ceiling_peak_dbtp"] = measurement.ceiling_peak_dbtp

        refused = False
        if spec is not None:
            loud = measurement.loudness_lufs
            if loud is None:
                result["within_tolerance"] = None
                warnings.append(
                    f"the delivered file carries no measurable loudness; "
                    f"the {spec.name} target of {spec.integrated_lufs:.1f} LUFS "
                    f"could not be checked"
                )
            else:
                error = float(loud) - spec.integrated_lufs
                result["loudness_error_lu"] = error
                result["within_tolerance"] = abs(error) <= spec.tolerance_lu
                if not result["within_tolerance"]:
                    warnings.append(
                        f"the delivered loudness is {error:+.2f} LU from the "
                        f"{spec.name} target of {spec.integrated_lufs:.1f} LUFS; "
                        f"the tolerance is {spec.tolerance_lu:.1f} LU"
                    )
                    # The report has to say why the file misses its target.
                    if limited_by_peak:
                        line = headroom_line_dbtp(spec.true_peak_dbtp)
                        left = line - float(measurement.ceiling_peak_dbtp)
                        warnings.append(
                            f"the {spec.name} target cannot be met: the true-peak "
                            f"ceiling of {spec.true_peak_dbtp:.1f} dBTP does not "
                            f"allow the gain the target needs ({wanted_db:+.2f} dB); "
                            f"the headroom line of {line:.2f} dBTP leaves "
                            f"{left:+.2f} dB of gain"
                        )
                    else:
                        warnings.append(
                            f"the {spec.name} target cannot be met: the correction "
                            f"could not bring the delivered file onto it"
                        )
            peak = measurement.ceiling_peak_dbtp
            # The headroom the file keeps is reported, so that a correction
            # that lands a hair over the headroom line is not thrown away
            # without a word and a file that sits on the ceiling is not
            # passed on in silence.
            headroom_note = _headroom_note(measurement, spec)
            if headroom_note:
                notes.append(headroom_note)
            if peak > spec.true_peak_dbtp + PEAK_EPSILON_DB:
                # A file that clips is worse than a file that is quiet: the
                # runner does not deliver it.
                discard_staged(best_staged)
                best_staged = None
                refused = True
                result["error"] = (
                    f"the delivered file was refused: its peak of "
                    f"{peak:.2f} dBTP is over the {spec.name} ceiling of "
                    f"{spec.true_peak_dbtp:.1f} dBTP"
                )
                result["status"] = "refused"
                warnings.append(result["error"])
                log.warning("batch file refused: %s (%s)", source, result["error"])

        if not refused:
            if cancel.is_set():
                # The job was cancelled while the file was written: the file
                # is removed instead of being delivered half way.
                raise BatchCancelled("the job was cancelled before the file was delivered")
            commit_staged(best_staged, target_path)
            best_staged = None
            result["output"] = str(target_path)
            result["status"] = "delivered"
    except BatchCancelled as exc:
        result["error"] = str(exc)
        result["status"] = "cancelled"
        log.info("batch file cancelled: %s (%s)", source, exc)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["status"] = "failed"
        log.warning("batch file failed: %s (%s)", source, exc)
    finally:
        discard_staged(current_staged)
        discard_staged(best_staged)
    result["notes"] = notes
    result["warnings"] = warnings
    result["elapsed"] = time.monotonic() - started
    return result


def _run_job(
    job: BatchJob,
    sources: list[Path],
    chain: list,
    out_dir: Path,
    fmt: str,
    bit_depth: int,
    spec: DeliverySpec | None,
) -> None:
    """Run one job over its files on the worker thread."""
    job.state = "running"
    job.started = time.time()
    reserved = frozenset(sources)
    try:
        for source in sources:
            if job.cancel_requested():
                break
            result = _process_file(
                source, chain, out_dir, fmt, bit_depth, spec, job.cancel_event, reserved
            )
            job.results.append(result)
            if result["status"] == "cancelled":
                continue
            if result["error"]:
                job.failed += 1
            else:
                job.done += 1
            for warning in result.get("warnings") or []:
                job.warnings.append(f"{Path(source).name}: {warning}")
    except Exception as exc:  # pragma: no cover - the loop above is guarded
        job.error = f"{type(exc).__name__}: {exc}"
        log.warning("batch job %s failed: %s", job.job_id, exc)
    finally:
        job.cancelled = job.cancel_requested()
        job.state = "cancelled" if job.cancelled else ("error" if job.error else "done")
        job.finished = time.time()


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------
async def _json_body(request: Request) -> dict:
    try:
        raw = await request.body()
    except Exception:
        return {}
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        raise HTTPException(400, "request body must be valid JSON") from None
    if not isinstance(data, dict):
        raise HTTPException(400, "request body must be a JSON object")
    return data


@router.post("/api/batch")
async def api_start_batch(request: Request) -> dict:
    """Start a batch job and return its id."""
    payload = await _json_body(request)

    raw_paths = payload.get("paths")
    if not isinstance(raw_paths, (list, tuple)) or not raw_paths:
        raise HTTPException(400, "paths must be a non-empty list")
    if len(raw_paths) > MAX_BATCH_FILES:
        raise HTTPException(400, f"a batch may contain at most {MAX_BATCH_FILES} files")

    # The delivery the job has to meet.  A target of the catalogue names
    # the level, the tolerance and the true-peak ceiling.
    row = None
    raw_target = payload.get("target")
    if raw_target not in (None, ""):
        try:
            row = get_target(raw_target)
        except UnknownTargetError as exc:
            raise HTTPException(400, str(exc)) from None

    fmt = str(payload.get("format") or (row.format if row is not None else "wav")).lower()
    if fmt not in _FORMATS:
        raise HTTPException(400, f"format must be one of {', '.join(_FORMATS)}")

    raw_depth = payload.get("bit_depth")
    if raw_depth is None:
        bit_depth = int(row.bit_depth) if row is not None else DEFAULT_BIT_DEPTH
    else:
        try:
            bit_depth = int(raw_depth)
        except (TypeError, ValueError):
            raise HTTPException(400, "bit_depth must be a number") from None
    if bit_depth not in _BIT_DEPTHS:
        raise HTTPException(400, f"bit_depth must be one of {', '.join(map(str, _BIT_DEPTHS))}")

    try:
        target_lufs = float(payload.get("target_lufs", DEFAULT_TARGET_LUFS))
    except (TypeError, ValueError):
        raise HTTPException(400, "target_lufs must be a number") from None
    if not math.isfinite(target_lufs):
        raise HTTPException(400, "target_lufs must be a finite number")

    normalise = bool(payload.get("loudness_normalise", False))
    chain = payload.get("chain") or []
    if not isinstance(chain, (list, tuple)):
        raise HTTPException(400, "chain must be a list")

    roots = allowed_roots(request.app)
    sources: list[Path] = []
    for raw in raw_paths:
        sources.append(resolve_input_path(raw, roots))
    out_dir = resolve_out_dir(request.app, payload.get("out_dir"), roots)

    # The delivery the job has to meet, and the chain that reaches it.
    spec: DeliverySpec | None = None
    steps = [step for step in chain if isinstance(step, dict)]
    if row is not None:
        spec = DeliverySpec.from_target(row)
        steps = add_delivery_stages(steps, row)
    elif normalise:
        spec = DeliverySpec(
            key="custom",
            name=f"{target_lufs:.1f} LUFS",
            integrated_lufs=target_lufs,
            tolerance_lu=DEFAULT_TOLERANCE_LU,
            true_peak_dbtp=DEFAULT_TRUE_PEAK_CEILING_DBTP,
        )
    else:
        spec = spec_from_chain(steps)

    # Validate the chain once so a typo is reported now and not once per file.
    try:
        build_chain_engine(steps, DEFAULT_SAMPLE_RATE, 2)
    except KeyError as exc:
        raise HTTPException(400, str(exc).strip("'")) from None
    except Exception as exc:
        raise HTTPException(400, f"the chain cannot be built: {exc}") from None

    job = BatchJob(
        job_id=uuid.uuid4().hex,
        total=len(sources),
        out_dir=str(out_dir),
        target=spec.key if spec is not None else None,
    )
    with _JOBS_LOCK:
        _JOBS[job.job_id] = job
    _executor().submit(
        _run_job, job, sources, steps, out_dir, fmt, bit_depth, spec
    )
    return {"job_id": job.job_id, "state": job.state, "total": job.total}


@router.get("/api/batch/{job_id}")
async def api_batch_status(job_id: str) -> dict:
    """Return the state of one batch job."""
    job = get_job(job_id)
    if job is None:
        raise HTTPException(404, "unknown job")
    return job.to_dict()


@router.post("/api/batch/{job_id}/cancel")
async def api_batch_cancel(job_id: str) -> dict:
    """Ask a running job to stop.

    The job stops between two files, and the file that is running stops as
    well: the file that is being written is removed, so a cancelled job
    never leaves a half written file behind.
    """
    job = get_job(job_id)
    if job is None:
        raise HTTPException(404, "unknown job")
    job.cancel()
    return {"job_id": job.job_id, "state": job.state, "cancelled": True}


# ---------------------------------------------------------------------------
# the delivery report
# ---------------------------------------------------------------------------
def delivery_report(
    sources: Iterable[Path],
    targets: Iterable[Any] | None = None,
    out_dir: Path | None = None,
    chain: Sequence[dict] | None = None,
) -> list[dict]:
    """Run the delivery chain over the sources for every delivery target.

    The rows are the delivery table: one row per target and source with the
    loudness of the file that was written, the error against the target, the
    sample peak and the true peak.  The measurement is taken on the file on
    disk, so the table describes what the runner delivers.
    """
    from .batch_formats import delivery_targets

    rows: list[dict] = []
    chosen = list(targets) if targets is not None else list(delivery_targets())
    base = Path(out_dir) if out_dir is not None else None
    for target in chosen:
        row = get_target(target)
        spec = DeliverySpec.from_target(row)
        steps = add_delivery_stages(list(chain or []), row)
        folder = (base / row.key) if base is not None else None
        if folder is not None:
            folder.mkdir(parents=True, exist_ok=True)
        for source in sources:
            source_path = Path(source)
            result = _process_file(
                source_path,
                steps,
                folder if folder is not None else Path(tempfile.gettempdir()),
                row.format,
                int(row.bit_depth),
                spec,
                threading.Event(),
                reserved=(),
            )
            rows.append(
                {
                    "target": row.key,
                    "target_name": row.name,
                    "file": source_path.stem,
                    "source": str(source_path),
                    "output": result["output"],
                    "status": result["status"],
                    "error": result["error"],
                    "target_lufs": row.integrated_lufs,
                    "tolerance_lu": row.tolerance_lu,
                    "loudness_lufs": result["loudness_lufs"],
                    "error_lu": result["loudness_error_lu"],
                    "within_tolerance": result["within_tolerance"],
                    "peak_dbfs": result["peak_dbfs"],
                    "true_peak_dbtp": result["true_peak_dbtp"],
                    "ceiling_dbtp": row.true_peak_dbtp,
                    "gain_db": result["gain_db"],
                    "before": result["before"],
                    "after": result["after"],
                    "notes": result["notes"],
                    "warnings": result["warnings"],
                }
            )
    return rows


def format_delivery_table(rows: Sequence[dict]) -> str:
    """Return the delivery table as text."""
    header = (
        f"{'target':<15}{'file':<15}{'loudness':>10}{'error':>9}"
        f"{'in tol':>8}{'peak':>9}{'true peak':>11}{'ceiling':>9}"
    )
    lines = [header, "-" * len(header)]
    for row in rows:
        lines.append(
            f"{str(row.get('target')):<15}"
            f"{str(row.get('file')):<15}"
            f"{_format_number(row.get('loudness_lufs')):>10}"
            f"{_format_number(row.get('error_lu')):>9}"
            f"{str(row.get('within_tolerance')):>8}"
            f"{_format_number(row.get('peak_dbfs')):>9}"
            f"{_format_number(row.get('true_peak_dbtp')):>11}"
            f"{_format_number(row.get('ceiling_dbtp')):>9}"
        )
    return "\n".join(lines)


def _default_corpus() -> Path:
    """Return the directory the corpus test clips live in."""
    return REPO_ROOT / "corpus" / "audio" / "test"


def main(argv: list[str] | None = None) -> int:
    """Run the delivery report over the corpus: ``python -m aurserver.batch``."""
    parser = argparse.ArgumentParser(
        prog="auralis-batch",
        description="Run the delivery chain over a set of files and report what it delivers.",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=None,
        help="the directory the source files live in (default: the corpus test clips)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="the directory the delivered files are written to (default: a temporary directory)",
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        help="write the delivery table as JSON to this file",
    )
    args = parser.parse_args(argv)

    folder = Path(args.corpus) if args.corpus is not None else _default_corpus()
    if not folder.is_dir():
        print(f"auralis-batch: no such directory: {folder}")
        return 2
    sources = sorted(folder.glob("*.wav"))
    if not sources:
        print(f"auralis-batch: no audio files in {folder}")
        return 2

    temporary: tempfile.TemporaryDirectory | None = None
    out_dir = Path(args.out) if args.out is not None else None
    if out_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix="auralis-delivery-")
        out_dir = Path(temporary.name)
    try:
        rows = delivery_report(sources, out_dir=out_dir)
    finally:
        if temporary is not None:
            temporary.cleanup()

    print(format_delivery_table(rows))
    if args.json is not None:
        Path(args.json).write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")
    outside = [row for row in rows if not row["within_tolerance"]]
    print(
        f"\n{len(rows)} delivered file(s); "
        f"{len(outside)} outside the tolerance of their target"
    )
    for row in outside:
        for warning in row.get("warnings") or []:
            print(f"  {row['target']} / {row['file']}: {warning}")
    notes = [
        (row["target"], row["file"], note)
        for row in rows
        for note in row.get("notes") or []
    ]
    if notes:
        print(f"\n{len(notes)} note(s) on the delivered files:")
        for target, name, note in notes:
            print(f"  {target} / {name}: {note}")
    return 0


# ---------------------------------------------------------------------------
# wiring
# ---------------------------------------------------------------------------
def register(app) -> None:
    """Attach the batch routes to an application."""
    app.include_router(router)


if __name__ == "__main__":
    raise SystemExit(main())
