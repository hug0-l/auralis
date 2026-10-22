"""
aurserver.batch_formats — delivery targets, chain augmentation and export policy.

One table drives the whole delivery surface: :data:`DELIVERY_TARGETS`.  The
target list the user interface shows, the chain the engine runs and the
metadata written into the delivered file are all read from the same rows, so
they cannot disagree.

Each row of the table carries

``key``              stable identifier used by the API and in file names
``name``             display name
``integrated_lufs``  integrated loudness target in LUFS
``true_peak_dbtp``   true-peak ceiling in dBTP
``codec``            codec the delivery is meant for
``format``           container the workbench writes
``bit_depth``        word length of the delivered master
``kind``             ``"broadcast"`` or ``"lossy"``
``tolerance_lu``     deviation that is accepted before the gain moves
``standard``         core loudness standard that matches the row exactly
``description``      one line of help text

The bit-depth policy follows the delivery kind: a broadcast master is written
at 24 bit, a lossy delivery at 16 bit.

The module is transport-only in the same sense as :mod:`aurserver.presets`:
it never runs a job and it never writes audio by itself, except through the
small export helper :func:`write_delivery`, which reuses the writer of
:mod:`aurserver.audio`.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
from fastapi import APIRouter

from . import audio as au

__all__ = [
    "DELIVERY_TARGETS",
    "DITHER_BIT_DEPTHS",
    "DITHER_MODULE",
    "DeliveryMeasurement",
    "DeliveryTarget",
    "GainPlan",
    "LEVEL_FLOOR_DB",
    "LOUDNESS_MODULE",
    "PEAK_EPSILON_DB",
    "PEAK_MARGIN_DB",
    "UnknownTargetError",
    "augment_chain",
    "catalogue",
    "core_standards",
    "delivery_metadata",
    "delivery_targets",
    "dither_stage",
    "get_target",
    "headroom_line_dbtp",
    "loudness_stage",
    "measure_delivery",
    "measure_loudness",
    "output_filename",
    "output_paths",
    "plan_gain",
    "register",
    "router",
    "safe_stem",
    "write_delivery",
]


#: Module code of the loudness stage.
LOUDNESS_MODULE = "AUR_LOUDNESS"

#: Module code of the dither stage.
DITHER_MODULE = "AUR_DITHER"

#: Word lengths the delivery chain can dither to.  A 32 bit master is written
#: without dither: there is nothing to dither away.
DITHER_BIT_DEPTHS: tuple[int, ...] = (16, 24)

#: Word lengths the exporter can write.
DELIVERY_BIT_DEPTHS: tuple[int, ...] = (16, 24, 32)

#: The level the delivery meter reports for silence.  A peak of zero has no
#: decibel value, so the meter reports the floor instead.
LEVEL_FLOOR_DB = -120.0

#: Slack between a measured true peak and a ceiling, in dB.  The limiter in
#: the core and the delivery meter do not estimate the inter-sample peak in
#: exactly the same way, so a file that sits on the ceiling must not be
#: called a violation because of the last bit of the estimate.
PEAK_EPSILON_DB = 0.05

#: The margin the runner keeps between the delivered true peak and the
#: ceiling of the target, in dB.
#:
#: The margin is a *preference*, not a limit: it is the line the runner aims
#: at, so that the delivered file keeps a hair of headroom under the
#: ceiling.  A file that is under the ceiling is legal whatever the margin
#: says, and the runner reports the excess over the line instead of
#: refusing the file.
PEAK_MARGIN_DB = 0.05


def headroom_line_dbtp(
    true_peak_ceiling_dbtp: float, *, margin_db: float = PEAK_MARGIN_DB
) -> float:
    """Return the peak a delivered file aims at.

    The ceiling of a delivery is the level the delivered file may not go
    over.  The line this function returns is the level the runner aims at:
    the ceiling less the margin, so that the file that lands on disk keeps
    a hair of headroom under the ceiling.  The meter of the core and the
    meter of the delivery do not estimate the inter-sample peak in exactly
    the same way, so a file that sits on the ceiling is not a safe place to
    deliver a master.
    """
    return float(true_peak_ceiling_dbtp) - float(margin_db)


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------
class UnknownTargetError(ValueError):
    """Raised when a delivery target is not in the catalogue.

    The message names the target that was asked for and lists the keys that
    do exist, so a caller never has to guess.
    """


def _unknown_message(text: Any) -> str:
    """Build the message of an :class:`UnknownTargetError`."""
    known = ", ".join(row.key for row in DELIVERY_TARGETS)
    return f"unknown delivery target {text!r}; known targets: {known}"


# ---------------------------------------------------------------------------
# the catalogue
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DeliveryTarget:
    """One row of the delivery catalogue."""

    key: str
    name: str
    integrated_lufs: float
    true_peak_dbtp: float
    codec: str
    format: str
    bit_depth: int
    kind: str = "broadcast"
    tolerance_lu: float = 0.5
    standard: str = ""
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON record of this target.

        The record carries the two delivery stages as well, so a client can
        build the delivery chain from the catalogue alone.  A target whose
        word length needs no dither carries no dither stage.
        """
        record: dict[str, Any] = {
            "key": self.key,
            "name": self.name,
            "integrated_lufs": float(self.integrated_lufs),
            "true_peak_dbtp": float(self.true_peak_dbtp),
            "codec": self.codec,
            "format": self.format,
            "bit_depth": int(self.bit_depth),
            "kind": self.kind,
            "tolerance_lu": float(self.tolerance_lu),
            "standard": self.standard,
            "description": self.description,
            "loudness": loudness_stage(self),
        }
        if int(self.bit_depth) in DITHER_BIT_DEPTHS:
            record["dither"] = dither_stage(self.bit_depth)
        else:
            record["dither"] = None
        return record


#: The delivery catalogue.  This table is the single source of truth for the
#: delivery surface: the user interface, the chain builder and the exporter
#: all read these rows.
DELIVERY_TARGETS: tuple[DeliveryTarget, ...] = (
    DeliveryTarget(
        key="ebu-r128",
        name="EBU R128",
        integrated_lufs=-23.0,
        true_peak_dbtp=-1.0,
        codec="pcm",
        format="wav",
        bit_depth=24,
        kind="broadcast",
        tolerance_lu=0.5,
        standard="EBU R128",
        description="General broadcast delivery: -23 LUFS integrated, -1 dBTP ceiling.",
    ),
    DeliveryTarget(
        key="ebu-r128-short",
        name="EBU R128 short",
        integrated_lufs=-20.0,
        true_peak_dbtp=-1.0,
        codec="pcm",
        format="wav",
        bit_depth=24,
        kind="broadcast",
        tolerance_lu=1.0,
        standard="",
        description=(
            "Short-form programme (idents, trailers, bumpers): the R128 true-peak "
            "ceiling with a raised integrated target, because a short item has "
            "little integrated loudness to measure."
        ),
    ),
    DeliveryTarget(
        key="atsc-a85",
        name="ATSC A/85",
        integrated_lufs=-24.0,
        true_peak_dbtp=-2.0,
        codec="pcm",
        format="wav",
        bit_depth=24,
        kind="broadcast",
        tolerance_lu=0.5,
        standard="ATSC A/85",
        description="North American broadcast delivery: -24 LUFS, -2 dBTP ceiling.",
    ),
    DeliveryTarget(
        key="arib-tr-b32",
        name="ARIB TR-B32",
        integrated_lufs=-24.0,
        true_peak_dbtp=-2.0,
        codec="pcm",
        format="wav",
        bit_depth=24,
        kind="broadcast",
        tolerance_lu=0.5,
        standard="ARIB TR-B32",
        description="Terrestrial broadcast delivery: -24 LUFS, -2 dBTP ceiling.",
    ),
    DeliveryTarget(
        key="podcast",
        name="Podcast",
        integrated_lufs=-16.0,
        true_peak_dbtp=-1.0,
        codec="aac",
        format="wav",
        bit_depth=16,
        kind="lossy",
        tolerance_lu=1.0,
        standard="",
        description="Spoken podcast delivery: -16 LUFS, mastered at 16 bit for a lossy encode.",
    ),
    DeliveryTarget(
        key="music-stream",
        name="Music streaming",
        integrated_lufs=-14.0,
        true_peak_dbtp=-1.0,
        codec="aac",
        format="wav",
        bit_depth=16,
        kind="lossy",
        tolerance_lu=1.0,
        standard="",
        description="Music delivery: -14 LUFS, mastered at 16 bit for a lossy encode.",
    ),
    DeliveryTarget(
        key="spoken-word",
        name="Spoken word",
        integrated_lufs=-19.0,
        true_peak_dbtp=-1.0,
        codec="aac",
        format="wav",
        bit_depth=16,
        kind="lossy",
        tolerance_lu=1.0,
        standard="",
        description=(
            "Narration and audiobook reads: -19 LUFS, a little under the podcast "
            "target so the delivery platform still has headroom."
        ),
    ),
)


def delivery_targets() -> tuple[DeliveryTarget, ...]:
    """Return every delivery target of the catalogue."""
    return DELIVERY_TARGETS


def catalogue() -> list[dict[str, Any]]:
    """Return the catalogue as JSON records, ready to be sent to a client."""
    return [row.to_dict() for row in DELIVERY_TARGETS]


def get_target(target: Any) -> DeliveryTarget:
    """Look a delivery target up.

    Accepts a :class:`DeliveryTarget`, a catalogue key or a display name
    (case insensitive).  Raises :class:`UnknownTargetError` when the target
    is not in the catalogue.
    """
    if isinstance(target, DeliveryTarget):
        return target
    text = "" if target is None else str(target).strip()
    if not text:
        raise UnknownTargetError(_unknown_message(target))
    for row in DELIVERY_TARGETS:
        if text == row.key or text.lower() == row.name.lower():
            return row
    raise UnknownTargetError(_unknown_message(text))


# ---------------------------------------------------------------------------
# the delivery stages
# ---------------------------------------------------------------------------
def core_standards() -> dict[str, tuple[float, float, float, float]]:
    """Return the loudness standards the core exposes.

    The mapping is ``name -> (integrated, momentary, short term, true peak)``.
    The import is deferred and a failure is tolerated, so the catalogue stays
    usable even when the core cannot be imported.
    """
    try:
        from aurcore.modules.loudness_control import STANDARDS
    except Exception:  # pragma: no cover - the core is always present in practice
        return {}
    return {str(name): tuple(float(v) for v in values) for name, values in STANDARDS.items()}


def _standard_matches(row: DeliveryTarget, name: str) -> bool:
    """True when the core standard ``name`` carries exactly the row's values."""
    values = core_standards().get(name)
    if not values:
        return False
    integrated, momentary, short_term, peak = values
    return (
        abs(integrated - row.integrated_lufs) < 1e-9
        and abs(momentary - row.integrated_lufs) < 1e-9
        and abs(short_term - row.integrated_lufs) < 1e-9
        and abs(peak - row.true_peak_dbtp) < 1e-9
    )


def loudness_stage(target: Any, *, standard: str | None = None) -> dict[str, Any]:
    """Return the loudness stage that brings a file onto the target.

    The stage is a module descriptor (``code`` plus ``params``) that can be
    handed to the shared chain builder.  The core standard is only named when
    it carries exactly the values of the row: the loudness module overwrites
    its manual target fields whenever a different standard is selected, so a
    mismatching name would undo the catalogue.
    """
    row = get_target(target)
    params: dict[str, Any] = {
        "Target Loudness": float(row.integrated_lufs),
        "Momentary Loudness": float(row.integrated_lufs),
        "Short Term Loudness": float(row.integrated_lufs),
        "True Peak": float(row.true_peak_dbtp),
        "Tolerance": float(row.tolerance_lu),
    }
    chosen = row.standard if standard is None else str(standard)
    if chosen and _standard_matches(row, chosen):
        params["Standard"] = chosen
    return {"code": LOUDNESS_MODULE, "enabled": True, "params": params}


def dither_stage(bit_depth: int) -> dict[str, Any]:
    """Return the dither stage for one output word length.

    The dither module knows 16 and 24 bit; a 32 bit master needs no dither
    and is therefore not dithered at all.
    """
    depth = int(bit_depth)
    if depth not in DITHER_BIT_DEPTHS:
        raise ValueError(
            f"unsupported dither depth {depth}; use {', '.join(map(str, DITHER_BIT_DEPTHS))}"
        )
    return {
        "code": DITHER_MODULE,
        "enabled": True,
        "params": {
            "Bit Depth": str(depth),
            "Noise Shaping": "Shape 2" if depth <= 16 else "None",
            "Dither Amount": "Medium",
            "Auto Blanking": "If Quantized",
        },
    }


def _copy_step(step: Any) -> dict[str, Any]:
    """Copy one chain step so the caller's chain is never mutated."""
    if not isinstance(step, dict):
        return {"code": str(step), "enabled": True, "params": {}}
    copied = dict(step)
    params = copied.get("params")
    copied["params"] = dict(params) if isinstance(params, dict) else {}
    return copied


def augment_chain(
    chain: Iterable[Any] | None,
    target: Any,
    *,
    bit_depth: int | None = None,
    include_dither: bool = True,
) -> list[dict[str, Any]]:
    """Return the chain with the delivery stages appended.

    The delivery stages go at the end of the chain: the loudness stage has
    to see the finished programme and the dither has to be the last thing
    that touches the signal.  The input chain is never modified.

    ``bit_depth`` overrides the word length of the target; a 32 bit delivery
    is written without a dither stage.
    """
    row = get_target(target)
    depth = int(bit_depth) if bit_depth is not None else int(row.bit_depth)
    if depth not in DELIVERY_BIT_DEPTHS:
        raise ValueError(
            f"unsupported delivery bit depth {depth}; "
            f"use {', '.join(map(str, DELIVERY_BIT_DEPTHS))}"
        )
    steps = [_copy_step(step) for step in (chain or [])]
    steps.append(loudness_stage(row))
    if include_dither and depth in DITHER_BIT_DEPTHS:
        steps.append(dither_stage(depth))
    return steps


# ---------------------------------------------------------------------------
# export: names
# ---------------------------------------------------------------------------
_ILLEGAL_NAME = re.compile(r"[^A-Za-z0-9._-]+")

#: Longest file stem the exporter writes.
MAX_STEM_LENGTH = 64


def _safe_stem(text: Any) -> str:
    """Turn a source name into a stem that is safe on every file system."""
    stem = Path(str(text)).stem
    stem = _ILLEGAL_NAME.sub("_", stem)
    stem = re.sub(r"_{2,}", "_", stem).strip("._-")
    if not stem:
        stem = "file"
    return stem[:MAX_STEM_LENGTH]


def safe_stem(text: Any) -> str:
    """Return a file stem that is safe on every file system.

    The stem never carries a path separator and never is ``.`` or ``..``,
    so a name built from it can not leave the directory it belongs to.
    """
    return _safe_stem(text)


def _path_key(source: Any) -> str:
    """Return the canonical text a path digest is computed from."""
    text = str(source).strip()
    return os.path.normcase(text).replace("\\", "/")


def _digest(source: Any, length: int = 8) -> str:
    """Return the short digest of a source path."""
    return hashlib.sha1(_path_key(source).encode("utf-8")).hexdigest()[:length]


def output_filename(
    source: Any,
    target: Any,
    *,
    extension: str | None = None,
    digest_length: int = 8,
) -> str:
    """Return the delivery file name of one source file.

    The pattern is ``<stem>__<target key>__<digest>.<extension>``.  The
    digest is derived from the source path, so the same input always yields
    the same name and two different inputs never share one.
    """
    row = get_target(target)
    ext = str(extension or row.format or "wav").lower().lstrip(".")
    stem = _safe_stem(source)
    return f"{stem}__{row.key}__{_digest(source, int(digest_length))}.{ext}"


def output_paths(sources: Iterable[Any], target: Any, out_dir: Any) -> list[Path]:
    """Return the output path of every source, without duplicates.

    The names follow :func:`output_filename`.  Should two sources still end
    up with the same name — a digest clash — the digest is widened before a
    counter is added, so the result is always unique inside one batch.
    """
    row = get_target(target)
    base = Path(str(out_dir))
    used: set[str] = set()
    paths: list[Path] = []
    for source in sources:
        name = ""
        for length in (8, 12, 16, 40):
            name = output_filename(source, row, digest_length=length)
            if name.lower() not in used:
                break
        else:  # pragma: no cover - a 40 character digest clash is not realistic
            stem, _, ext = name.rpartition(".")
            counter = 2
            while f"{stem}_{counter}.{ext}".lower() in used:
                counter += 1
            name = f"{stem}_{counter}.{ext}"
        used.add(name.lower())
        paths.append(base / name)
    return paths


# ---------------------------------------------------------------------------
# export: metadata and writing
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    """Return the current time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def delivery_metadata(
    source: Any,
    target: Any,
    *,
    chain: Sequence[Any] | None = None,
    measured_lufs: float | None = None,
    generated: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the metadata block that describes one delivered file."""
    row = get_target(target)
    metadata: dict[str, Any] = {
        "auralis.delivery.target": row.name,
        "auralis.delivery.key": row.key,
        "auralis.delivery.integrated_lufs": float(row.integrated_lufs),
        "auralis.delivery.true_peak_dbtp": float(row.true_peak_dbtp),
        "auralis.delivery.codec": row.codec,
        "auralis.delivery.format": row.format,
        "auralis.delivery.bit_depth": int(row.bit_depth),
        "auralis.delivery.kind": row.kind,
        "auralis.source": str(source),
        "auralis.chain": [
            str(step.get("code", ""))
            for step in (chain or [])
            if isinstance(step, dict) and step.get("code")
        ],
        "auralis.measured_lufs": None if measured_lufs is None else float(measured_lufs),
        "auralis.generated": generated or _now_iso(),
    }
    if extra:
        metadata.update({str(key): value for key, value in extra.items()})
    return metadata


def _info_field(chunk_id: bytes, text: str) -> bytes:
    """Build one ``INFO`` sub chunk: an id, a size and a null terminated string."""
    payload = str(text).encode("utf-8") + b"\x00"
    if len(payload) % 2:
        payload += b"\x00"
    return chunk_id + len(payload).to_bytes(4, "little") + payload


def _info_chunk(metadata: dict[str, Any]) -> bytes:
    """Build a ``LIST``/``INFO`` chunk that carries the delivery metadata."""
    body = b"INFO"
    body += _info_field(b"INAM", str(metadata.get("auralis.delivery.target", "")))
    body += _info_field(
        b"ICMT", json.dumps(metadata, indent=2, ensure_ascii=False, sort_keys=True)
    )
    body += _info_field(b"ISFT", "Auralis")
    body += _info_field(b"ICRD", str(metadata.get("auralis.generated", "")))
    return b"LIST" + len(body).to_bytes(4, "little") + body


def _insert_chunk(wav: bytes, chunk: bytes) -> bytes:
    """Insert a chunk into a RIFF/WAVE payload and fix the RIFF size."""
    if len(wav) < 12 or wav[0:4] != b"RIFF" or wav[8:12] != b"WAVE":
        raise ValueError("the payload is not a RIFF/WAVE file")
    position = 12
    insert_at = 12
    while position + 8 <= len(wav):
        chunk_id = wav[position : position + 4]
        size = int.from_bytes(wav[position + 4 : position + 8], "little")
        end = position + 8 + size + (size & 1)
        if chunk_id == b"fmt ":
            insert_at = end
            break
        position = end
    out = wav[:insert_at] + chunk + wav[insert_at:]
    return out[:4] + (len(out) - 8).to_bytes(4, "little") + out[8:]


def _integrated_loudness(samples: Any, sample_rate: int) -> float | None:
    """Measure the gated integrated loudness of a buffer.

    The meter is the one of the core, which owns loudness.  ``None`` means
    the buffer carries no measurable loudness — silence, for example — and
    not that the number is zero.
    """
    try:
        from aurcore.modules.loudness_control import integrated_loudness
    except Exception:  # pragma: no cover - the core is always present in practice
        return None
    try:
        value = integrated_loudness(np.asarray(samples, dtype=np.float64), int(sample_rate))
    except Exception:
        return None
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def measure_loudness(path: Any) -> float | None:
    """Measure the integrated loudness of a delivered file.

    The measurement comes from the core, which owns the loudness meter.  The
    value is ``None`` when the core cannot be reached or the file cannot be
    decoded; a delivery report then simply carries no measurement instead of
    a guessed one.
    """
    try:
        samples, sample_rate = au.decode_upload(Path(str(path)))
    except Exception:
        return None
    return _integrated_loudness(samples, sample_rate)


@dataclass(frozen=True)
class DeliveryMeasurement:
    """What the delivery meter says about one delivered file.

    ``loudness_lufs`` is the gated integrated loudness, ``peak_dbfs`` the
    sample peak and ``true_peak_dbtp`` the four-times oversampled peak of
    the file on disk.  A value is ``None`` when the meter could not measure
    it; a silent file has no integrated loudness at all.
    """

    loudness_lufs: float | None = None
    peak_dbfs: float | None = None
    true_peak_dbtp: float | None = None
    sample_peak: float | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the measurement as a JSON record."""
        return {
            "loudness_lufs": self.loudness_lufs,
            "peak_dbfs": self.peak_dbfs,
            "true_peak_dbtp": self.true_peak_dbtp,
            "sample_peak": self.sample_peak,
        }

    @property
    def ceiling_peak_dbtp(self) -> float:
        """The peak the true-peak ceiling is measured against.

        The true peak is the number a delivery meter reports, but a file
        whose own samples are above the ceiling is not acceptable either:
        the reconstruction of a hard clipped signal can measure under the
        level the samples sit at.  The larger of the two is the number the
        ceiling applies to.
        """
        values = [
            value
            for value in (self.true_peak_dbtp, self.peak_dbfs)
            if value is not None
        ]
        return max(values) if values else LEVEL_FLOOR_DB


def measure_delivery(path: Any) -> DeliveryMeasurement:
    """Measure a delivered file: loudness, sample peak and true peak.

    The measurement is taken on the file that is on disk, so the numbers
    describe the delivered master and not the buffer that went into it.
    A file that cannot be read carries no measurement at all.
    """
    try:
        samples, sample_rate = au.decode_upload(Path(str(path)))
    except Exception:
        return DeliveryMeasurement()
    arr = np.asarray(samples, dtype=np.float64)
    if arr.ndim == 1:
        arr = arr[:, None]
    if arr.size == 0:
        return DeliveryMeasurement()

    peak = float(np.max(np.abs(arr)))
    peak_dbfs = LEVEL_FLOOR_DB if peak <= 0.0 else 20.0 * math.log10(peak)
    try:
        from aurcore.dsp.loudness import true_peak

        oversampled = float(true_peak(arr, int(sample_rate)))
    except Exception:  # pragma: no cover - the core is always present in practice
        oversampled = float("-inf")
    true_peak_dbtp = LEVEL_FLOOR_DB if not math.isfinite(oversampled) else oversampled

    return DeliveryMeasurement(
        loudness_lufs=_integrated_loudness(arr, sample_rate),
        peak_dbfs=peak_dbfs,
        true_peak_dbtp=true_peak_dbtp,
        sample_peak=peak,
    )


@dataclass(frozen=True)
class GainPlan:
    """The gain that brings a delivered file onto its target.

    ``gain_db`` is the gain to apply, ``wanted_db`` the gain the loudness
    target asks for, ``headroom_db`` the gain the headroom line still
    allows and ``limited_by_peak`` says that the line is what limits the
    gain.
    """

    gain_db: float
    wanted_db: float
    headroom_db: float
    limited_by_peak: bool


def plan_gain(
    measured: DeliveryMeasurement,
    integrated_lufs: float,
    true_peak_ceiling_dbtp: float,
    *,
    margin_db: float = PEAK_MARGIN_DB,
) -> GainPlan:
    """Plan the gain that brings a delivered file onto a target.

    The loudness asks for ``target - measured`` decibels.  The headroom
    line — the ceiling less the margin — allows
    ``ceiling - margin - measured_true_peak`` decibels.  The smaller of the
    two is the gain to apply: a file that clips is worse than a file that
    is quiet.  When the line is what limits the gain, the plan says so, and
    the caller can report that the target cannot be met.

    The line is where the runner aims at, not a wall: the correction lands
    a hair under the ceiling, and the excess over the line that the write
    and the meter leave behind is reported, not thrown away.
    """
    ceiling = float(true_peak_ceiling_dbtp)
    line = headroom_line_dbtp(ceiling, margin_db=margin_db)
    headroom = line - measured.ceiling_peak_dbtp
    loud = measured.loudness_lufs
    if loud is None:
        # No measurable loudness: no gain can bring the file onto the target.
        return GainPlan(0.0, 0.0, headroom, False)
    wanted = float(integrated_lufs) - float(loud)
    if wanted <= headroom:
        return GainPlan(wanted, wanted, headroom, False)
    return GainPlan(headroom, wanted, headroom, True)


def write_delivery(
    path: Any,
    samples: Any,
    sample_rate: int,
    target: Any,
    *,
    source: Any = None,
    chain: Sequence[Any] | None = None,
    measured_lufs: float | None = None,
    generated: str | None = None,
) -> dict[str, Any]:
    """Write one delivered file and return its metadata.

    The audio is written with the shared writer of :mod:`aurserver.audio`; the
    delivery metadata is stored in an ``INFO`` chunk inside the same file, so
    a delivered file carries its own delivery facts.
    """
    row = get_target(target)
    depth = int(row.bit_depth)
    payload = au.encode_wav(samples, int(sample_rate), depth)
    metadata = delivery_metadata(
        source if source is not None else path,
        row,
        chain=chain,
        measured_lufs=measured_lufs,
        generated=generated,
    )
    payload = _insert_chunk(payload, _info_chunk(metadata))
    target_path = Path(str(path))
    if target_path.parent and str(target_path.parent):
        target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(payload)
    return metadata


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------
router = APIRouter(tags=["batch"])


@router.get("/api/delivery-targets")
async def api_delivery_targets() -> dict:
    """Return the delivery catalogue."""
    return {"targets": catalogue()}


def register(app) -> None:
    """Attach the delivery catalogue routes to an application."""
    app.include_router(router)
