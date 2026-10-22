"""
aurcore.serial.document — the session document.

A document bundles the audio reference, the module chain and the view state of
one working session:

.. code-block:: json

    {
      "format": "auralis.document",
      "version": 1,
      "audio": {"path": "...", "sample_rate": 48000, "channels": 2, "frames": 123456},
      "chain": {"format": "auralis.chain", "version": 1, "steps": [...]},
      "view":  {"zoom": 1.0, "scroll": 0.0, "spectrogram": true, "selection": null}
    }

The view state is a free-form dictionary: the four keys above are the known
ones, everything else is kept untouched so a newer build can add fields without
breaking an older one.
"""
from __future__ import annotations

import os
from typing import Any

from . import typed_value as tv
from .chain_json import chain_to_dict
from .preset_json import read_json, write_json

DOCUMENT_FORMAT = "auralis.document"
DOCUMENT_VERSION = 1

#: The key the report of the parameters lives under on a loaded document.
#: It is a report of the load, not part of the document, so it is never
#: written to a file.
PARAM_REPORT_KEY = "param_report"

#: The view keys the user interface knows about.
DEFAULT_VIEW = {
    "zoom": 1.0,
    "scroll": 0.0,
    "spectrogram": True,
    "selection": None,
}

__all__ = [
    "DOCUMENT_FORMAT",
    "DOCUMENT_VERSION",
    "DEFAULT_VIEW",
    "save_document",
    "load_document",
    "document_from_dict",
    "document_audio",
    "document_view",
]


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _audio_dict(audio_ref: Any) -> dict:
    """Normalise the audio reference into the document's ``audio`` block."""
    if audio_ref is None:
        ref: dict[str, Any] = {}
    elif isinstance(audio_ref, dict):
        ref = dict(audio_ref)
    else:
        ref = {}
        for key in ("path", "sample_rate", "channels", "frames"):
            if hasattr(audio_ref, key):
                ref[key] = getattr(audio_ref, key)

    audio = {
        "path": str(ref.get("path", "") or ""),
        "sample_rate": _as_int(ref.get("sample_rate", 0), 0),
        "channels": _as_int(ref.get("channels", 0), 0),
        "frames": _as_int(ref.get("frames", 0), 0),
    }
    for key, value in ref.items():
        if key not in audio:
            audio[key] = value
    return audio


def _view_dict(view_state: Any) -> dict:
    """Merge the caller's view state over the known defaults."""
    view = dict(DEFAULT_VIEW)
    if isinstance(view_state, dict):
        view.update(view_state)
    return view


def document_from_dict(audio_ref: Any, engine: Any, view_state: Any = None) -> dict:
    """Build the session document for ``engine`` and ``view_state``."""
    return {
        "format": DOCUMENT_FORMAT,
        "version": DOCUMENT_VERSION,
        "audio": _audio_dict(audio_ref),
        "chain": chain_to_dict(engine),
        "view": _view_dict(view_state),
    }


def save_document(
    path: str | os.PathLike,
    audio_ref: Any = None,
    engine: Any = None,
    view_state: Any = None,
) -> dict:
    """Write a session document to ``path`` and return the written document.

    ``save_document(path, audio_ref, engine, view_state)`` builds the document
    from its parts; ``save_document(path, document)`` writes an already built
    document (migrated first).
    """
    from .migrate import migrate_document  # local import keeps the import graph acyclic

    if (
        isinstance(audio_ref, dict)
        and str(audio_ref.get("format", "") or "") == DOCUMENT_FORMAT
        and engine is None
    ):
        document = dict(migrate_document(audio_ref))
        # The report of a load is not part of the document.
        document.pop(PARAM_REPORT_KEY, None)
        write_json(path, document)
        return document

    data = document_from_dict(audio_ref, engine, view_state)
    write_json(path, data)
    return data


def load_document(path: str | os.PathLike) -> dict:
    """Read a session document from ``path`` (migrated to the current version)."""
    from .migrate import migrate_document  # local import keeps the import graph acyclic

    data = read_json(path)
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a session document")
    return migrate_document(data)


def document_audio(document: dict) -> dict:
    """Return the plain audio block of a session document."""
    audio = (document or {}).get("audio")
    if not isinstance(audio, dict):
        return {"path": "", "sample_rate": 0, "channels": 0, "frames": 0}
    out: dict[str, Any] = {}
    for key, value in audio.items():
        out[key] = tv.unwrap(value)
    out.setdefault("path", "")
    out.setdefault("sample_rate", 0)
    out.setdefault("channels", 0)
    out.setdefault("frames", 0)
    return out


def document_view(document: dict) -> dict:
    """Return the plain view block of a session document."""
    view = (document or {}).get("view")
    if not isinstance(view, dict):
        return dict(DEFAULT_VIEW)
    out: dict[str, Any] = {}
    for key, value in view.items():
        out[key] = tv.unwrap(value)
    return out
