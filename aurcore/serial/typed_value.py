"""
aurcore.serial.typed_value — the Auralis interchange value format.

Every scalar in every Auralis JSON artefact is wrapped:

    { "Type": "Float" | "Int" | "UInt" | "Bool" | "String" | "Base64", "Value": ... }

This makes artefacts self-describing and lets unknown fields survive a
round-trip, which is what keeps old presets loadable after the schema changes.
"""
from __future__ import annotations

import base64
from typing import Any


def wrap(value: Any) -> dict:
    """Wrap a Python value in the typed-value envelope."""
    if isinstance(value, bool):
        return {"Type": "Bool", "Value": value}
    if isinstance(value, int):
        return {"Type": "Int", "Value": value}
    if isinstance(value, float):
        return {"Type": "Float", "Value": value}
    if isinstance(value, (bytes, bytearray)):
        return {"Type": "Base64", "Value": base64.b64encode(bytes(value)).decode("ascii")}
    return {"Type": "String", "Value": "" if value is None else str(value)}


def wrap_all(values: dict) -> dict:
    return {k: wrap(v) for k, v in (values or {}).items()}


def unwrap(node: Any, default: Any = None) -> Any:
    """Unwrap a typed-value envelope; pass through raw values unchanged."""
    if isinstance(node, dict) and "Type" in node and "Value" in node:
        t = node["Type"]
        v = node["Value"]
        if t == "Base64":
            try:
                return base64.b64decode(v + "=" * (-len(v) % 4))
            except Exception:
                return default
        if t == "Bool":
            return bool(v)
        if t in ("Int", "UInt"):
            try:
                return int(v)
            except (TypeError, ValueError):
                return default
        if t == "Float":
            try:
                return float(v)
            except (TypeError, ValueError):
                return default
        return v
    if node is None:
        return default
    return node


def unwrap_all(nodes: dict) -> dict:
    return {k: unwrap(v) for k, v in (nodes or {}).items()}
