"""
aurcore.serial.preset_json — the single-module preset format.

A preset is a small, self-describing JSON document:

.. code-block:: json

    {
      "format": "auralis.preset",
      "version": 1,
      "module": {"code": "AUR_DECLICK", "element": "DeClicker"},
      "params": {"Sensitivity": {"Type": "Float", "Value": 3.0, "Id": 1}},
      "state":  {"Type": "Base64", "Value": "<module state blob>"}
    }

Parameter values use the typed-value envelope of :mod:`aurcore.serial.typed_value`,
so an artefact stays self-describing and unknown fields survive a round-trip.

**The identity of a parameter is its name.**  The name is the key of the
``params`` map, so a document written before a parameter was inserted in the
middle of a module still loads onto the right parameter: the value follows the
name, not the position.

Beside the value, an entry carries the two things that say *which* parameter
it is:

``Id``
    the host parameter id the parameter had when the document was written.
    The id is the position of the parameter inside its module, and it is the
    number the host uses.  It is advisory: a reader compares it with the id
    the parameter has now and **reports** a parameter that moved, which says
    that the document was written against an older table.  The value still
    lands on the parameter with that name.

``Sig``
    the meaning of the parameter, in a compact form: its type, its range and
    its unit.  A reader compares it with the meaning the parameter has now and
    **reports** a parameter whose meaning changed.

Both are optional.  A document that carries neither is read by name alone,
which is what every document written before this change is.

Parameters a module does not know about are kept in ``module.extra_params`` and
written back on the next save.  A preset produced by a newer build therefore
survives a load/save cycle in an older build instead of being silently truncated.
A parameter that is unknown is **reported and skipped**: it is never handed to
a neighbour.

This module also hosts the small low-level helpers shared by the rest of the
serial package (:mod:`aurcore.serial.chain_json`, :mod:`aurcore.serial.document`):
``write_json``, ``read_json``, ``plain_value``, ``state_payload``,
``dict_state_payload`` and ``restore_state``.  They live here because
``preset_json`` is the base layer of the format package.
"""
from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .. import registry
from . import typed_value as tv

PRESET_FORMAT = "auralis.preset"
PRESET_VERSION = 1

#: The key that carries the host parameter id beside the parameter name.
PARAM_ID_KEY = "Id"

#: The key that carries the meaning of the parameter beside its id.
PARAM_SIG_KEY = "Sig"

__all__ = [
    "PRESET_FORMAT",
    "PRESET_VERSION",
    "PARAM_ID_KEY",
    "PARAM_SIG_KEY",
    "save_preset",
    "load_preset",
    "apply_preset",
    "module_from_preset",
    "preset_from_module",
    "list_presets",
    "parameter_signature",
    "param_entry",
    "param_entry_id",
    "param_entry_signature",
    "parameter_identity",
    "with_identities",
    "apply_parameters",
    "empty_report",
    "merge_reports",
]


# ---------------------------------------------------------------------------
# shared low-level helpers
# ---------------------------------------------------------------------------
def plain_value(value: Any) -> Any:
    """Return ``value`` as a plain Python scalar (numpy scalars become scalars)."""
    if hasattr(value, "item") and not isinstance(value, (str, bytes, bytearray)):
        try:
            return value.item()
        except Exception:
            return value
    return value


def write_json(path: str | os.PathLike, data: Any) -> None:
    """Write ``data`` as pretty JSON (indent 2, UTF-8) and create parent folders."""
    target = Path(path)
    parent = target.parent
    if str(parent) and not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False)
    target.write_text(text + "\n", encoding="utf-8")


def read_json(path: str | os.PathLike) -> Any:
    """Read a UTF-8 JSON document from ``path``."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def dict_state_payload(module: Any) -> bytes:
    """UTF-8 JSON bytes of a module's dict-form state (empty when unused)."""
    serializer = getattr(module, "serialize", None)
    if not callable(serializer):
        return b""
    try:
        data = serializer()
    except Exception:
        return b""
    if isinstance(data, dict) and data:
        return json.dumps(data, sort_keys=True, default=str).encode("utf-8")
    return b""


def state_payload(module: Any) -> bytes:
    """Raw bytes of a module's non-parameter state.

    Modules keep their internal state either as a binary blob (the
    ``_state_blob`` / ``_put_state_blob`` hooks of
    :class:`aurcore.modules.base.Module`) or as a JSON-safe dict
    (``serialize`` / ``deserialize``).  The blob wins when both are present.
    """
    getter = getattr(module, "state_base64", None)
    if callable(getter):
        try:
            blob = getter() or ""
        except Exception:
            blob = ""
        if isinstance(blob, (bytes, bytearray)) and blob:
            return bytes(blob)
        if isinstance(blob, str) and blob:
            try:
                return base64.b64decode(blob + "=" * (-len(blob) % 4))
            except Exception:
                pass
    return dict_state_payload(module)


def restore_state(module: Any, payload: bytes) -> None:
    """Restore a state payload produced by :func:`state_payload` into ``module``."""
    if not payload:
        return
    loader = getattr(module, "load_state_base64", None)
    if callable(loader):
        try:
            loader(base64.b64encode(bytes(payload)).decode("ascii"))
        except Exception:
            pass
    decoded: Any = None
    try:
        decoded = json.loads(bytes(payload).decode("utf-8"))
    except Exception:
        decoded = None
    if isinstance(decoded, dict):
        setter = getattr(module, "deserialize", None)
        if callable(setter):
            try:
                setter(decoded)
            except Exception:
                pass


def _decode_state(raw: Any) -> bytes:
    """Decode a typed state node into raw bytes (tolerant of legacy shapes)."""
    if raw is None:
        return b""
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw)
    if isinstance(raw, str):
        if not raw:
            return b""
        try:
            return base64.b64decode(raw + "=" * (-len(raw) % 4))
        except Exception:
            return b""
    return b""


# ---------------------------------------------------------------------------
# the identity of a parameter
# ---------------------------------------------------------------------------
# The name of a parameter is its identity; the id is the position the
# parameter has inside its module, and that is the number the host uses.  The
# two travel together in a document, so a reader can tell that a parameter
# moved or changed its meaning, and can still put the value on the parameter
# it belongs to.
def parameter_signature(desc: Any) -> str:
    """The compact meaning of a parameter: its type, its range and its unit.

    Two parameters with the same name but a different signature are not the
    same parameter: a knob that changed from decibels to a ratio means
    something else.  An enum carries no range: its value is the name of the
    choice, so the choice list describes itself.
    """
    kind = str(getattr(desc, "type", "") or "")
    if kind in ("bool", "enum"):
        return kind
    unit = str(getattr(desc, "unit", "") or "")
    try:
        low = float(getattr(desc, "minimum", 0.0))
        high = float(getattr(desc, "maximum", 0.0))
    except (TypeError, ValueError):
        return kind
    if unit:
        return "%s:%g:%g:%s" % (kind, low, high, unit)
    return "%s:%g:%g" % (kind, low, high)


def param_entry(value: Any, param_id: int | None = None, signature: str = "") -> dict:
    """Wrap a value in the typed envelope and record the identity beside it.

    The name of the parameter is the key of the map the entry lives in; the
    id and the meaning travel inside the entry.
    """
    node = tv.wrap(plain_value(value))
    if param_id is not None:
        node[PARAM_ID_KEY] = int(param_id)
    if signature:
        node[PARAM_SIG_KEY] = str(signature)
    return node


def param_entry_id(node: Any) -> int | None:
    """The host parameter id an entry carries (``None`` when it carries none)."""
    if not isinstance(node, Mapping):
        return None
    raw = node.get(PARAM_ID_KEY)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def param_entry_signature(node: Any) -> str:
    """The meaning an entry carries ("" when it carries none)."""
    if not isinstance(node, Mapping):
        return ""
    raw = node.get(PARAM_SIG_KEY)
    return "" if raw is None else str(raw)


def parameter_identity(store: Any) -> dict[str, tuple[int, str]]:
    """The id and the meaning of every parameter of a store.

    The id of a parameter is its position inside its module: that is the
    number the host uses and the number the plug-in layer publishes.
    """
    identity: dict[str, tuple[int, str]] = {}
    descs = getattr(store, "descs", None)
    if not callable(descs):
        return identity
    try:
        listed = descs()
    except Exception:  # noqa: BLE001
        return identity
    for index, desc in enumerate(listed):
        name = str(getattr(desc, "name", "") or "")
        if not name:
            continue
        identity[name] = (index, parameter_signature(desc))
    return identity


def with_identities(module: Any, entries: Any) -> dict:
    """Add the id and the meaning of every known parameter to a parameter map.

    An entry that already carries an id keeps it: the document knows more
    about the parameter than this build does.
    """
    if not isinstance(entries, Mapping):
        return entries
    identity = parameter_identity(getattr(module, "params", None))
    out: dict[str, Any] = {}
    for key, node in entries.items():
        name = str(key)
        found = identity.get(name)
        if found is None or not isinstance(node, dict):
            out[name] = node
            continue
        param_id, signature = found
        entry = dict(node)
        entry.setdefault(PARAM_ID_KEY, param_id)
        if signature:
            entry.setdefault(PARAM_SIG_KEY, signature)
        out[name] = entry
    return out


def empty_report() -> dict:
    """The report of a load that placed every parameter."""
    return {"applied": 0, "unknown": [], "moved": [], "changed": [], "positional": []}


def merge_reports(first: dict, second: dict) -> dict:
    """Add the counts of two reports and concatenate their lists."""
    out = empty_report()
    for report in (first or {}, second or {}):
        out["applied"] += int(report.get("applied", 0) or 0)
        for key in ("unknown", "moved", "changed", "positional"):
            out[key].extend(list(report.get(key, []) or []))
    return out


def _position_of(key: str) -> int | None:
    """The position a legacy, id-only entry names (None when it names none)."""
    text = str(key).strip()
    if not text.isdigit():
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _canonical(store: Any, name: str) -> str:
    """The canonical name of a parameter.

    A parameter that was renamed keeps its old name as an alias, so a
    document that carries the old name still loads.  The store knows the
    alias; a store that does not keeps the name as it is.
    """
    canonical = getattr(store, "canonical", None)
    if not callable(canonical):
        return name
    try:
        found = canonical(name)
    except Exception:  # noqa: BLE001
        return name
    return str(found) if found else name


def apply_parameters(module: Any, entries: Any) -> dict:
    """Apply a parameter map to a module and report what happened.

    The rule, in one place:

    * a parameter the module knows is set **by name**.  The name is the
      identity of a parameter, so a parameter that moved since the document
      was written still receives its own value.  A name the parameter had
      before it was renamed is an alias and resolves onto the parameter.
    * a parameter the module does not know any more is **reported and
      skipped**.  It is never handed to a neighbour, and it is kept in
      ``module.extra_params`` so that the next save does not lose it.
    * a parameter whose id moved is **reported**: the document was written
      against an older table.
    * a parameter whose meaning changed is **reported** when the document
      records the meaning.
    * an entry whose key is a number and is not the name of a parameter is
      read as the **position** the parameter had when the document was
      written.  That is the old, id-only format; it is reported, because
      after an insertion it names a different parameter.
    """
    report = empty_report()
    code = str(getattr(module, "code", "") or "")

    if not isinstance(entries, Mapping):
        return report

    store = getattr(module, "params", None)
    if store is None:
        extras = {str(name): tv.unwrap(node) for name, node in entries.items()}
        module.extra_params = extras
        report["unknown"] = sorted(extras)
        return report

    identity = parameter_identity(store)
    order = list(identity)

    extras: dict[str, Any] = {}
    for key, node in entries.items():
        name = str(key)
        value = tv.unwrap(node)

        if store.has(name):
            canonical = _canonical(store, name)
            store.set(name, value)
            report["applied"] += 1

            current_id, current_signature = identity.get(canonical, (None, ""))
            was_id = param_entry_id(node)
            if was_id is not None and current_id is not None and was_id != current_id:
                report["moved"].append(
                    {"module": code, "name": canonical, "was": was_id, "now": current_id}
                )
            was_signature = param_entry_signature(node)
            if was_signature and current_signature and was_signature != current_signature:
                report["changed"].append(
                    {"module": code, "name": canonical, "was": was_signature,
                     "now": current_signature}
                )
            continue

        position = _position_of(name)
        if position is not None and position < len(order):
            # The old, id-only format: the entry names the position the
            # parameter had when the document was written.
            target = order[position]
            store.set(target, value)
            report["applied"] += 1
            report["positional"].append(
                {"module": code, "position": position, "name": target}
            )
            continue

        extras[name] = value
        report["unknown"].append(name)

    module.extra_params = extras
    return report


# ---------------------------------------------------------------------------
# preset documents
# ---------------------------------------------------------------------------
def preset_from_module(module: Any) -> dict:
    """Build the preset document describing ``module``.

    Known parameters come from the module's parameter store, unknown ones from
    ``module.extra_params``.  Every known parameter carries the id it has
    inside the module and the meaning it has, so a reader can tell that the
    parameter moved or changed since the preset was written.  The module state
    is stored as a Base64 typed value.
    """
    params: dict[str, dict] = {}
    store = getattr(module, "params", None)
    identity = parameter_identity(store)
    if store is not None:
        for name, value in store.to_dict().items():
            param_id, signature = identity.get(str(name), (None, ""))
            params[str(name)] = param_entry(value, param_id, signature)
    for name, value in (getattr(module, "extra_params", None) or {}).items():
        params[str(name)] = tv.wrap(plain_value(value))

    payload = state_payload(module)
    return {
        "format": PRESET_FORMAT,
        "version": PRESET_VERSION,
        "module": {
            "code": getattr(module, "code", "") or "",
            "element": getattr(module, "element", "") or "",
        },
        "params": params,
        "state": {"Type": "Base64", "Value": base64.b64encode(payload).decode("ascii")},
    }


def save_preset(module: Any, path: Any = None) -> dict:
    """Write a preset to ``path`` and return the written document.

    Two call styles are supported:

    ``save_preset(module, path)``
        build the preset document from a module instance and write it.

    ``save_preset(path, document)``
        write an already built preset document (it is migrated first, so a
        legacy document is upgraded on the way out).
    """
    from .migrate import migrate_preset  # local import keeps the import graph acyclic

    if isinstance(module, (str, os.PathLike)) and isinstance(path, dict):
        document = migrate_preset(path)
        write_json(module, document)
        return document
    if path is None:
        raise TypeError("save_preset() needs a path")

    data = migrate_preset(preset_from_module(module))
    write_json(path, data)
    return data


def load_preset(path: str | os.PathLike) -> dict:
    """Read a preset document from ``path`` (migrated to the current version)."""
    from .migrate import migrate_preset  # local import keeps the import graph acyclic

    data = read_json(path)
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a preset document")
    return migrate_preset(data)


def apply_preset(module: Any, data: dict) -> dict:
    """Apply a preset document to ``module``.

    Parameters the module does not know about are kept in
    ``module.extra_params`` so that a save round-trip does not lose them.
    A parameter that is unknown is reported and skipped, never handed to a
    neighbour.

    The report of the load is returned: how many parameters were applied, and
    which ones were unknown, moved or changed their meaning.
    """
    from .migrate import migrate_preset  # local import keeps the import graph acyclic

    doc = migrate_preset(data) if isinstance(data, dict) else {}

    report = apply_parameters(module, doc.get("params") or {})

    restore_state(module, _decode_state(tv.unwrap(doc.get("state"), None)))
    return report


def module_from_preset(module: Any, data: dict) -> None:
    """Apply a preset document to ``module`` (alias of :func:`apply_preset`)."""
    apply_preset(module, data)


def _display_name(code: str) -> str:
    try:
        cls = registry.get(code)
    except KeyError:
        return code
    return getattr(cls, "display_name", code) or code


def list_presets(directory: str | os.PathLike) -> list[dict]:
    """List the preset documents stored in ``directory``.

    ``*.json`` and ``*.auralis`` files are inspected; a file counts as a preset
    when its ``format`` field is ``auralis.preset``.  Each entry carries the
    preset name, its path, the module code it belongs to and the modification
    time of the file.  Presets are saved copies, so there is no dirty flag.
    """
    root = Path(directory)
    if not root.is_dir():
        return []

    candidates: list[Path] = []
    seen: set[str] = set()
    for pattern in ("*.json", "*.auralis"):
        for path in sorted(root.glob(pattern), key=lambda p: p.name.lower()):
            key = str(path.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            candidates.append(path)

    found: list[dict] = []
    for path in candidates:
        try:
            data = read_json(path)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        fmt = str(data.get("format", "") or "")
        if fmt != PRESET_FORMAT:
            continue
        module = data.get("module")
        if isinstance(module, dict):
            code = str(module.get("code", "") or "")
        else:
            code = str(module or "")
        try:
            stat = path.stat()
            modified = float(stat.st_mtime)
            size = int(stat.st_size)
        except OSError:
            modified = 0.0
            size = 0
        found.append(
            {
                "name": path.stem,
                "path": str(path),
                "module": code,
                "code": code,
                "display_name": _display_name(code),
                "modified": modified,
                "modified_iso": datetime.fromtimestamp(modified, tz=timezone.utc).isoformat(),
                "size": size,
                "format": fmt,
                "version": int(data.get("version", PRESET_VERSION) or PRESET_VERSION),
            }
        )
    return found
