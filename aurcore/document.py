"""
aurcore.document — the Auralis session document.

A session document is what the workbench writes when the user saves: the
audio the session works on, the module chain, the state of the view, the
markers the user dropped on the timeline and a summary of the undo history.

.. code-block:: json

    {
      "format": "auralis.document",
      "version": 1,
      "saved": "2026-10-21T09:15:00+00:00",
      "audio": {
        "path": "work/interview.wav",
        "sample_rate": 48000, "channels": 2, "frames": 123456,
        "content_hash": "sha256:6f1e...", "size": 987654, "mtime": 1760000000.0
      },
      "chain": { "format": "auralis.chain", "version": 1, "steps": [ ... ] },
      "view": {
        "zoom": 1.0, "scroll": 0.0,
        "frequency_range": [0.0, 22050.0],
        "selection": {"t0": 1.0, "t1": 2.5, "f0": 80.0, "f1": 400.0},
        "view_mode": "composite", "spectrogram": true
      },
      "markers": [
        {"id": "m1", "name": "Hum", "start": 1.0, "end": 2.5, "kind": "region"}
      ],
      "history": {"depth": 3, "labels": ["Add De-Hum", "Change Sensitivity"]}
    }

The chain block is the ordinary chain document of
:mod:`aurcore.serial.chain_json`, so its parameters use the typed-value
envelope of :mod:`aurcore.serial.typed_value`.

**The identity of a parameter is its name.**  The name is the key of the
``params`` map of a step, so a session written before a parameter was inserted
in the middle of a module still loads onto the right parameter.  Beside the
value an entry carries the id the parameter had when the session was written
and the meaning it had; a load compares the two with the ones the parameter
has now and reports a parameter that moved or that changed its meaning.  The
report is on the document that :func:`load_document` returns, under
``param_report`` (see :func:`document_param_report`).

The audio block carries a *content hash*: a fingerprint of the file the
session was built on.  It lets the workbench notice that the file on disk
changed since the document was written.  Hashing a 500 MB recording in full
on every save would be wasteful, so only the first and the last megabyte are
digested, together with the size and the modification time of the file (see
:func:`content_hash`).

Loading a document runs the migrations of :mod:`aurcore.serial.migrate`, so a
document written by an older build is upgraded on the way in.
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .serial.chain_json import chain_from_dict, chain_to_dict
from .serial.migrate import migrate_document
from .serial.preset_json import empty_report, read_json, write_json

__all__ = [
    "DOCUMENT_FORMAT",
    "DOCUMENT_VERSION",
    "HASH_SAMPLE_BYTES",
    "DEFAULT_VIEW",
    "PARAM_REPORT_KEY",
    "content_hash",
    "file_signature",
    "audio_reference",
    "normalise_view",
    "normalise_markers",
    "build_document",
    "save_document",
    "load_document",
    "document_audio",
    "document_chain",
    "document_view",
    "document_markers",
    "document_history",
    "document_migrations",
    "document_param_report",
    "audio_status",
]

DOCUMENT_FORMAT = "auralis.document"
DOCUMENT_VERSION = 1

#: The key the report of the parameters lives under on a loaded document.
PARAM_REPORT_KEY = "param_report"

#: How many bytes are read at the head and at the tail of the audio file.
HASH_SAMPLE_BYTES = 1 << 20  # 1 MiB

#: The view keys the workbench knows about.
DEFAULT_VIEW: dict[str, Any] = {
    "zoom": 1.0,
    "scroll": 0.0,
    "frequency_range": [0.0, 22050.0],
    "selection": None,
    "view_mode": "composite",
    "spectrogram": True,
}

#: Spellings the user interface may use for a view key.
_VIEW_ALIASES = {
    "frequencyRange": "frequency_range",
    "freqRange": "frequency_range",
    "frequency_range_hz": "frequency_range",
    "viewMode": "view_mode",
    "spectro": "spectrogram",
    "show_spectrogram": "spectrogram",
    "spectrogram_enabled": "spectrogram",
}


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _as_int(value: Any, default: int = 0) -> int:
    """Read an int out of a value that may be a string or ``None``."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    """Read a float out of a value that may be a string or ``None``."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _mapping(value: Any) -> dict[str, Any]:
    """Read a mapping out of a dict or an object with attributes."""
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    out: dict[str, Any] = {}
    for key in dir(value):
        if key.startswith("_"):
            continue
        try:
            out[key] = getattr(value, key)
        except Exception:  # noqa: BLE001 - a property may fail
            continue
    return out


def _audio_mapping(value: Any) -> dict[str, Any]:
    """Read an audio reference out of a dict or an object with attributes."""
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    out: dict[str, Any] = {}
    for key in (
        "path",
        "file",
        "filename",
        "name",
        "format",
        "sample_rate",
        "channels",
        "frames",
        "duration",
        "asset_id",
    ):
        if hasattr(value, key):
            try:
                out[key] = getattr(value, key)
            except Exception:  # noqa: BLE001 - a property may fail
                continue
    return out


# ---------------------------------------------------------------------------
# the audio fingerprint
# ---------------------------------------------------------------------------
def file_signature(path: str | os.PathLike) -> tuple[int, float]:
    """The size and the modification time of a file (``(0, 0.0)`` when absent)."""
    try:
        stat = Path(path).stat()
    except OSError:
        return 0, 0.0
    return int(stat.st_size), float(stat.st_mtime)


def content_hash(path: str | os.PathLike, sample_bytes: int = HASH_SAMPLE_BYTES) -> str:
    """The fingerprint of an audio file, as ``"sha256:<hex>"``.

    The whole file is not read: the first and the last ``sample_bytes`` are
    digested, together with the size and the modification time of the file.
    A file that was replaced by a different recording of the same length is
    therefore detected as well, and saving a session over a 500 MB recording
    stays cheap.

    An empty string is returned when the file cannot be read.
    """
    target = Path(path)
    try:
        stat = target.stat()
    except OSError:
        return ""
    if not target.is_file():
        return ""

    span = max(1, int(sample_bytes))
    digest = hashlib.sha256()
    digest.update(b"auralis.audio.v1")
    digest.update(str(int(stat.st_size)).encode("ascii"))
    digest.update(str(int(stat.st_mtime_ns)).encode("ascii"))
    try:
        with target.open("rb") as handle:
            digest.update(handle.read(span))
            if stat.st_size > span:
                handle.seek(max(0, stat.st_size - span))
                digest.update(handle.read(span))
    except OSError:
        return ""
    return "sha256:" + digest.hexdigest()


def audio_reference(
    path: str | os.PathLike | None = None,
    sample_rate: int = 0,
    channels: int = 0,
    frames: int = 0,
    **extra: Any,
) -> dict[str, Any]:
    """Build the audio block of a document.

    The block carries the path, the sample rate, the channel count, the frame
    count and the content hash of the file.  Extra fields (the file name, the
    container format, ...) are kept.
    """
    block: dict[str, Any] = {
        "path": str(path or ""),
        "sample_rate": _as_int(sample_rate, 0),
        "channels": _as_int(channels, 0),
        "frames": _as_int(frames, 0),
    }
    for key, value in extra.items():
        block.setdefault(key, value)
    return _with_fingerprint(block)


def _with_fingerprint(block: dict[str, Any]) -> dict[str, Any]:
    """Add the content hash, the size and the modification time to a block."""
    path = str(block.get("path", "") or "")
    block["content_hash"] = content_hash(path) if path else ""
    size, mtime = file_signature(path) if path else (0, 0.0)
    block["size"] = size
    block["mtime"] = mtime
    return block


# ---------------------------------------------------------------------------
# view state and markers
# ---------------------------------------------------------------------------
def normalise_view(view_state: Any) -> dict[str, Any]:
    """Normalise a view state into the document's ``view`` block.

    The known keys get their default when they are missing, the aliases the
    user interface uses (``frequencyRange``, ``viewMode``, ...) are renamed
    and unknown keys are kept untouched, so a newer build can add fields
    without breaking an older one.
    """
    view = dict(DEFAULT_VIEW)
    raw = _mapping(view_state)
    for key, value in raw.items():
        name = _VIEW_ALIASES.get(str(key), str(key))
        view[name] = value

    view["zoom"] = _as_float(view.get("zoom"), 1.0)
    view["scroll"] = _as_float(view.get("scroll"), 0.0)
    view["spectrogram"] = bool(view.get("spectrogram", True))
    view["view_mode"] = str(view.get("view_mode", "composite") or "composite")
    view["frequency_range"] = _frequency_range(view.get("frequency_range"))
    view["selection"] = _selection(view.get("selection"))
    return view


def _frequency_range(value: Any) -> list[float]:
    """A ``[low, high]`` frequency range in hertz."""
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        low = _as_float(value[0], 0.0)
        high = _as_float(value[1], 0.0)
        return [min(low, high), max(low, high)]
    return list(DEFAULT_VIEW["frequency_range"])


def _selection(value: Any) -> dict[str, Any] | None:
    """A time / frequency selection, or ``None``."""
    if not isinstance(value, Mapping):
        return None
    out: dict[str, Any] = {}
    for key in ("t0", "t1", "f0", "f1"):
        if key in value:
            out[key] = _as_float(value[key], 0.0)
    if not out:
        return None
    out.setdefault("t0", 0.0)
    out.setdefault("t1", 0.0)
    out.setdefault("f0", 0.0)
    out.setdefault("f1", 0.0)
    return out


def normalise_markers(markers: Any) -> list[dict[str, Any]]:
    """Normalise the markers / regions of a session.

    Every marker gets an id, a name, a start time, an optional end time and a
    kind (``"marker"`` for a point, ``"region"`` for a range).
    """
    if not markers:
        return []
    if isinstance(markers, Mapping):
        markers = [markers]

    out: list[dict[str, Any]] = []
    for index, raw in enumerate(markers):
        if isinstance(raw, Mapping):
            entry = dict(raw)
        else:
            entry = _mapping(raw)
        marker: dict[str, Any] = {
            "id": str(entry.get("id") or f"m{index + 1}"),
            "name": str(entry.get("name") or f"Marker {index + 1}"),
            "start": _as_float(entry.get("start", entry.get("time", 0.0)), 0.0),
        }
        end = entry.get("end", None)
        marker["end"] = None if end is None else _as_float(end, 0.0)
        if marker["end"] is None:
            marker["kind"] = str(entry.get("kind") or "marker")
        else:
            marker["kind"] = str(entry.get("kind") or "region")
            marker["start"], marker["end"] = sorted((marker["start"], marker["end"]))
        for key, value in entry.items():
            if key not in marker and key not in ("time",):
                marker[key] = value
        out.append(marker)
    return out


# ---------------------------------------------------------------------------
# building and writing the document
# ---------------------------------------------------------------------------
def build_document(
    engine: Any = None,
    audio_ref: Any = None,
    view_state: Any = None,
    markers: Any = None,
    history: Any = None,
) -> dict[str, Any]:
    """Build the session document of an engine, without writing it."""
    chain: dict[str, Any]
    if engine is None:
        chain = {"format": "auralis.chain", "version": 1, "steps": []}
    else:
        chain = chain_to_dict(engine)

    audio = _with_fingerprint(_audio_block(audio_ref))
    return {
        "format": DOCUMENT_FORMAT,
        "version": DOCUMENT_VERSION,
        "saved": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "audio": audio,
        "chain": chain,
        "view": normalise_view(view_state),
        "markers": normalise_markers(markers),
        "history": _history_block(history),
    }


def _audio_block(audio_ref: Any) -> dict[str, Any]:
    """Normalise an audio reference into the document's ``audio`` block."""
    ref = _audio_mapping(audio_ref)
    path = ref.get("path") or ref.get("file") or ref.get("filename") or ""
    block: dict[str, Any] = {
        "path": str(path or ""),
        "sample_rate": _as_int(ref.get("sample_rate", 0), 0),
        "channels": _as_int(ref.get("channels", 0), 0),
        "frames": _as_int(ref.get("frames", 0), 0),
    }
    for key, value in ref.items():
        if key in ("file", "filename", "path"):
            continue
        block.setdefault(key, value)
    return block


def _history_block(history: Any) -> dict[str, Any]:
    """Normalise the undo history summary."""
    if history is None:
        return {"depth": 0, "labels": [], "can_undo": False, "can_redo": False}
    if hasattr(history, "summary"):
        try:
            return dict(history.summary())
        except Exception:  # noqa: BLE001
            return {"depth": 0, "labels": [], "can_undo": False, "can_redo": False}
    if isinstance(history, Mapping):
        return dict(history)
    return {"depth": 0, "labels": [], "can_undo": False, "can_redo": False}


def save_document(
    path: str | os.PathLike,
    engine: Any = None,
    audio_ref: Any = None,
    view_state: Any = None,
    markers: Any = None,
    *,
    history: Any = None,
) -> dict[str, Any]:
    """Write the session document of ``engine`` to ``path``.

    ``audio_ref`` describes the audio the session works on (a path, a
    dictionary or an object with the attributes), ``view_state`` is the state
    of the view and ``markers`` the markers of the timeline.  ``history`` is
    an undo stack (or an already built summary) whose labels are stored with
    the document.

    The written document is returned.
    """
    document = build_document(
        engine=engine,
        audio_ref=audio_ref,
        view_state=view_state,
        markers=markers,
        history=history,
    )
    write_json(path, document)
    return document


# ---------------------------------------------------------------------------
# reading the document
# ---------------------------------------------------------------------------
def load_document(path: str | os.PathLike, engine: Any = None) -> dict[str, Any]:
    """Read a session document from ``path`` and restore it.

    The document is migrated to the current version first.  When an ``engine``
    is given, the chain of the document is restored into it (the chain of the
    engine is replaced).  The document itself is returned, so the caller can
    pick up the view state, the markers and the audio reference.  The
    returned dictionary also remembers which migrations ran (see
    :func:`document_migrations`).

    The report of the parameters the chain carried is on the returned document
    under ``param_report`` (see :func:`document_param_report`).  It says how
    many parameters were applied, and which ones the module does not know any
    more, which ones moved and which ones changed their meaning.
    """
    data = read_json(path)
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a session document")
    fmt = str(data.get("format", "") or "")
    if fmt and fmt != DOCUMENT_FORMAT:
        raise ValueError(f"{path} is not a session document")

    document = migrate_document(data)
    if engine is not None:
        document[PARAM_REPORT_KEY] = chain_from_dict(engine, document.get("chain") or {})
    return document


def document_param_report(document: Any) -> dict[str, Any]:
    """The report of the parameters a document carried.

    The report is empty when the document was not read into an engine, or
    when every parameter of the document was applied where it belonged.
    """
    report = (document or {}).get(PARAM_REPORT_KEY)
    if not isinstance(report, Mapping):
        return empty_report()
    return dict(report)


def document_audio(document: Any) -> dict[str, Any]:
    """The audio block of a document."""
    audio = (document or {}).get("audio")
    if not isinstance(audio, Mapping):
        return {"path": "", "sample_rate": 0, "channels": 0, "frames": 0, "content_hash": ""}
    out = dict(audio)
    out.setdefault("path", "")
    out.setdefault("sample_rate", 0)
    out.setdefault("channels", 0)
    out.setdefault("frames", 0)
    out.setdefault("content_hash", "")
    return out


def document_chain(document: Any) -> dict[str, Any]:
    """The chain block of a document."""
    chain = (document or {}).get("chain")
    if not isinstance(chain, Mapping):
        return {"format": "auralis.chain", "version": 1, "steps": []}
    return dict(chain)


def document_view(document: Any) -> dict[str, Any]:
    """The view block of a document."""
    view = (document or {}).get("view")
    if not isinstance(view, Mapping):
        return normalise_view(None)
    return normalise_view(view)


def document_markers(document: Any) -> list[dict[str, Any]]:
    """The markers of a document."""
    return normalise_markers((document or {}).get("markers"))


def document_history(document: Any) -> dict[str, Any]:
    """The undo history summary of a document."""
    return _history_block((document or {}).get("history"))


def document_migrations(document: Any) -> list[str]:
    """The migrations that ran while the document was loaded.

    The list is only available on the document returned by
    :func:`load_document`; a document that travelled through a file has lost
    it.
    """
    found = getattr(document, "migrations", None)
    return list(found) if found else []


# ---------------------------------------------------------------------------
# did the audio change?
# ---------------------------------------------------------------------------
def audio_status(document: Any, path: str | os.PathLike | None = None) -> dict[str, Any]:
    """Compare the audio of a document with the file on disk.

    The returned dictionary carries the state (``"unchanged"``, ``"changed"``,
    ``"missing"`` or ``"unknown"``), a human readable reason and the two
    hashes that were compared.
    """
    audio = document_audio(document)
    expected = str(audio.get("content_hash") or "")
    target = str(path if path is not None else audio.get("path") or "")

    if not target:
        return {
            "state": "unknown",
            "reason": "the document has no audio reference",
            "path": "",
            "expected_hash": expected,
            "actual_hash": "",
        }
    if not Path(target).exists():
        return {
            "state": "missing",
            "reason": "the audio file is gone",
            "path": target,
            "expected_hash": expected,
            "actual_hash": "",
        }
    actual = content_hash(target)
    if not expected:
        return {
            "state": "unknown",
            "reason": "the document has no content hash",
            "path": target,
            "expected_hash": "",
            "actual_hash": actual,
        }
    if actual == expected:
        return {
            "state": "unchanged",
            "reason": "the audio file is the one the session was built on",
            "path": target,
            "expected_hash": expected,
            "actual_hash": actual,
        }
    return {
        "state": "changed",
        "reason": "the audio file changed since the session was saved",
        "path": target,
        "expected_hash": expected,
        "actual_hash": actual,
    }
