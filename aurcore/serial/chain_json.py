"""
aurcore.serial.chain_json — the module-chain document.

A chain document describes the modules of a session in signal order:

.. code-block:: json

    {
      "format": "auralis.chain",
      "version": 1,
      "steps": [
        {"code": "AUR_DECLICK", "enabled": true,
         "params": {"Sensitivity": {"Type": "Float", "Value": 3.0, "Id": 1}},
         "state": {"Type": "String", "Value": "<base64 state>"}}
      ]
    }

The document is the serialised form of an :class:`aurcore.api.Engine`, so
:func:`chain_to_dict` builds on ``Engine.serialize()`` and
:func:`chain_from_dict` restores the engine step by step.  The state of a step
keeps the engine's string-typed base64 so a chain file can also be handed to
``Engine.deserialize()`` directly.

**The identity of a parameter is its name.**  The name is the key of the
``params`` map, so a document written before a parameter was inserted in the
middle of a module still loads onto the right parameter.  Beside the value an
entry carries the id the parameter had when the document was written and the
meaning it had; a reader compares the two with the ones the parameter has now
and reports a parameter that moved or that changed its meaning (see
:func:`aurcore.serial.preset_json.apply_parameters`).

Modules may keep unknown parameters in ``module.extra_params``; those are
merged back into the written document so a chain round-trip loses nothing.
"""
from __future__ import annotations

import base64
import os
from typing import Any

from .. import registry
from . import typed_value as tv
from .preset_json import (
    apply_parameters,
    dict_state_payload,
    empty_report,
    merge_reports,
    plain_value,
    read_json,
    restore_state,
    with_identities,
    write_json,
)

CHAIN_FORMAT = "auralis.chain"
CHAIN_VERSION = 1

#: How many parameters :func:`describe_chain` reports per step.
MAX_KEY_PARAMS = 5

__all__ = [
    "CHAIN_FORMAT",
    "CHAIN_VERSION",
    "MAX_KEY_PARAMS",
    "save_chain",
    "load_chain",
    "chain_to_dict",
    "chain_from_dict",
    "reorder_chain",
    "describe_chain",
]


# ---------------------------------------------------------------------------
# documents
# ---------------------------------------------------------------------------
def chain_to_dict(engine: Any) -> dict:
    """Serialise ``engine``'s module chain into a chain document.

    The engine's own ``serialize()`` provides the skeleton; the id and the
    meaning of every known parameter are added beside the name, the
    parameters the modules kept in ``extra_params`` are merged back in and
    modules that keep their state in a dict get that state encoded as well.
    """
    data = engine.serialize()
    data.setdefault("format", CHAIN_FORMAT)
    data.setdefault("version", CHAIN_VERSION)
    steps = data.get("steps") or []
    live = list(getattr(engine, "chain", []) or [])

    for index, step in enumerate(steps):
        if index >= len(live):
            break
        module = live[index].module

        params = step.get("params")
        if isinstance(params, dict):
            step["params"] = with_identities(module, params)

        extras = getattr(module, "extra_params", None) or {}
        if extras:
            params = step.setdefault("params", {})
            for name, value in extras.items():
                params.setdefault(name, tv.wrap(plain_value(value)))

        state = step.get("state")
        empty_state = not isinstance(state, dict) or not state.get("Value")
        if empty_state:
            payload = dict_state_payload(module)
            if payload:
                step["state"] = {
                    "Type": "String",
                    "Value": base64.b64encode(payload).decode("ascii"),
                }
    return data


def chain_from_dict(engine: Any, data: dict) -> dict:
    """Restore a chain document into ``engine`` (the chain is replaced).

    The report of the load is returned: how many parameters were applied, and
    which ones were unknown, moved or changed their meaning.  A parameter the
    module does not know is reported and skipped; it is never handed to a
    neighbour.
    """
    from .migrate import migrate_chain  # local import keeps the import graph acyclic

    doc = migrate_chain(data) if isinstance(data, dict) else {}
    engine.clear_chain()
    report = empty_report()

    for step in doc.get("steps") or []:
        if not isinstance(step, dict):
            continue
        code = step.get("code")
        if not code or not registry.has(code):
            continue
        handle = engine.add_module(code)
        module = handle.module

        report = merge_reports(report, apply_parameters(module, step.get("params") or {}))

        raw = tv.unwrap(step.get("state"), None)
        if isinstance(raw, (bytes, bytearray)):
            payload = bytes(raw)
        elif isinstance(raw, str) and raw:
            try:
                payload = base64.b64decode(raw + "=" * (-len(raw) % 4))
            except Exception:
                payload = b""
        else:
            payload = b""
        restore_state(module, payload)

        engine.set_bypass(handle, not bool(step.get("enabled", True)))

    # The mode of the chain is part of the chain document: a session that was
    # rendered offline and is opened again in a real time host must not change
    # its meaning silently.  The mode is applied after the steps are built,
    # so every module of the chain is given it.
    engine.apply_document_mode(doc)

    return report


def save_chain(engine: Any, path: Any = None) -> dict:
    """Write a chain document to ``path`` and return the written document.

    Two call styles are supported:

    ``save_chain(engine, path)``
        serialise the engine's chain and write it.

    ``save_chain(path, document)``
        write an already built chain document (it is migrated first, so a
        legacy document is upgraded on the way out).
    """
    from .migrate import migrate_chain  # local import keeps the import graph acyclic

    if isinstance(engine, (str, os.PathLike)) and isinstance(path, dict):
        document = migrate_chain(path)
        write_json(engine, document)
        return document
    if path is None:
        raise TypeError("save_chain() needs a path")

    data = chain_to_dict(engine)
    write_json(path, data)
    return data


def load_chain(path: str | os.PathLike) -> dict:
    """Read a chain document from ``path`` (migrated to the current version)."""
    from .migrate import migrate_chain  # local import keeps the import graph acyclic

    data = read_json(path)
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a chain document")
    return migrate_chain(data)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def reorder_chain(chain_data: dict, src: int, dst: int) -> dict:
    """Return a copy of ``chain_data`` with the step at ``src`` moved to ``dst``.

    The input document is left untouched.  ``dst`` is clamped to the last
    step, matching the engine's own reorder behaviour.
    """
    if not isinstance(chain_data, dict):
        raise TypeError("reorder_chain expects a chain document")
    steps = chain_data.get("steps")
    if steps is None:
        steps = chain_data.get("elements") or []
    steps = list(steps)
    if not (0 <= int(src) < len(steps)):
        raise IndexError(src)
    target = max(0, min(int(dst), len(steps) - 1))
    moved = steps.pop(int(src))
    steps.insert(target, moved)

    out = dict(chain_data)
    out["steps"] = steps
    return out


def _module_class(code: str):
    try:
        return registry.get(code)
    except KeyError:
        return None


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "On" if value else "Off"
    if isinstance(value, float):
        return f"{value:g}"
    if value is None:
        return "-"
    return str(value)


def _key_params(code: str, params: dict) -> list[tuple[str, Any]]:
    """Pick the parameters worth showing in the chain strip.

    Parameters that differ from the module default come first; the declared
    order of the module is used to keep the result stable.
    """
    if not params:
        return []
    defaults = _param_order(code)
    names = list(params)
    if defaults:
        rank = {name: index for index, name in enumerate(defaults)}
        names.sort(key=lambda n: rank.get(n, len(rank)))
    changed = [n for n in names if n in defaults and params[n] != defaults[n]]
    rest = [n for n in names if n not in changed]
    picked = (changed + rest)[:MAX_KEY_PARAMS]
    return [(name, params[name]) for name in picked]


_PARAM_DEFAULTS: dict[str, dict[str, Any]] = {}


def _param_order(code: str) -> dict[str, Any]:
    """Default values of a module's parameters (empty when unknown)."""
    if code in _PARAM_DEFAULTS:
        return _PARAM_DEFAULTS[code]
    defaults: dict[str, Any] = {}
    cls = _module_class(code)
    if cls is None:
        # an unknown code is not cached: the module may be registered later
        return defaults
    module = None
    try:
        module = cls(48000, 2)
    except Exception:
        try:
            module = cls()
        except Exception:
            module = None
    if module is not None:
        try:
            for desc in module.describe():
                defaults[desc.name] = desc.default
        except Exception:
            defaults = {}
    _PARAM_DEFAULTS[code] = defaults
    return defaults


def describe_chain(chain_data: dict) -> list[dict]:
    """Summarise a chain document for the user interface.

    One record per step: its position, the module code, the display name, the
    enabled flag and a summary of the key parameters.
    """
    if not isinstance(chain_data, dict):
        return []
    steps = chain_data.get("steps")
    if steps is None:
        steps = chain_data.get("elements") or []

    described: list[dict] = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        code = str(step.get("code", "") or "")
        cls = _module_class(code)
        params = {name: tv.unwrap(node) for name, node in (step.get("params") or {}).items()}
        key = _key_params(code, params)
        described.append(
            {
                "index": index,
                "code": code,
                "display_name": getattr(cls, "display_name", code) if cls else code,
                "element": getattr(cls, "element", "") if cls else "",
                "category": getattr(cls, "category", "") if cls else "",
                "enabled": bool(step.get("enabled", True)),
                "params": dict(key),
                "summary": " · ".join(f"{name}: {_format_value(value)}" for name, value in key),
            }
        )
    return described
