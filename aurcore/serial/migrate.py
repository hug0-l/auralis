"""
aurcore.serial.migrate — versioned migration of Auralis JSON artefacts.

Every artefact carries a ``version`` field.  A migration is a plain function
that upgrades a document from one version to the next one.  Migrations are
registered per artefact kind (preset / chain / document) and applied in order
by :func:`migrate_preset`, :func:`migrate_chain` and :func:`migrate_document`.

The names of the migrations that ran are recorded on the returned document, so
:func:`migrations_applied` can report them.  :func:`migrate` is the generic
entry point: it picks the artefact kind from the ``format`` field and runs the
matching chain of migrations.

Built-in migrations
-------------------

``preset_v0_to_v1``
    A v0 preset carried the module code as a bare string and raw (untyped)
    parameter values.  The module block becomes ``{"code": ..., "element": ...}``,
    the parameters are wrapped in the typed-value envelope, a legacy
    ``parameters`` alias is renamed to ``params`` and an empty state block is
    added.

``chain_v0_to_v1``
    A v0 chain carried its modules in an ``elements`` list with an ``on`` flag.
    The list becomes ``steps``, ``on`` becomes ``enabled``, the parameters are
    typed and every step gets a state block.

``document_v0_to_v1``
    A v0 document used the short audio keys (``sr``, ``ch``, ``length``), a
    bare list for the chain and ``spectro`` for the spectrogram toggle.  They
    are renamed to the current names, the chain is normalised into a chain
    document and the view block gets its defaults.
"""
from __future__ import annotations

import base64
import copy
import json
from collections import OrderedDict
from typing import Any, Callable

from . import typed_value as tv

CURRENT_VERSION = 1

PRESET = "preset"
CHAIN = "chain"
DOCUMENT = "document"

_KIND_BY_FORMAT = {
    "auralis.preset": PRESET,
    "auralis.chain": CHAIN,
    "auralis.document": DOCUMENT,
}

__all__ = [
    "CURRENT_VERSION",
    "PRESET",
    "CHAIN",
    "DOCUMENT",
    "MigrationRegistry",
    "register",
    "migrate",
    "migrate_preset",
    "migrate_chain",
    "migrate_document",
    "migrations_applied",
    "preset_v0_to_v1",
    "chain_v0_to_v1",
    "document_v0_to_v1",
]


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _typed_params(raw: Any) -> dict:
    """Wrap raw parameter values in the typed-value envelope."""
    out: dict[str, dict] = {}
    for name, node in (raw or {}).items():
        if isinstance(node, dict) and "Type" in node and "Value" in node:
            out[str(name)] = dict(node)
        else:
            out[str(name)] = tv.wrap(node)
    return out


def _b64(data: bytes) -> str:
    return base64.b64encode(bytes(data)).decode("ascii")


class MigratedDict(dict):
    """A dictionary that remembers which migrations produced it."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.migrations: list[str] = []


class MigrationRegistry:
    """The ordered migrations of one artefact kind."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._functions: dict[int, Callable[[dict], dict]] = {}
        self._log: "OrderedDict[int, tuple[Any, list[str]]]" = OrderedDict()

    # -- registration ---------------------------------------------------------
    def register(self, from_version: int, fn: Callable[[dict], dict], overwrite: bool = False):
        """Register ``fn`` as the migration that leaves ``from_version``."""
        key = _as_int(from_version, -1)
        if key < 0:
            raise ValueError(f"invalid source version {from_version!r}")
        if key >= CURRENT_VERSION:
            raise ValueError(
                f"{self.kind} migration from v{key} is not older than the current version"
            )
        if key in self._functions and not overwrite:
            raise ValueError(
                f"a {self.kind} migration from v{key} is already registered"
            )
        self._functions[key] = fn
        return fn

    def migrations(self) -> list[Callable[[dict], dict]]:
        """The registered migration functions, oldest first."""
        return [self._functions[key] for key in sorted(self._functions)]

    # -- running --------------------------------------------------------------
    def migrate(self, data: Any, target: int = CURRENT_VERSION) -> MigratedDict:
        """Upgrade ``data`` to ``target`` and return the migrated document."""
        if not isinstance(data, dict):
            raise TypeError(f"{self.kind} migration expects a dictionary")
        work = MigratedDict(copy.deepcopy(dict(data)))
        version = _as_int(work.get("version"), 0)
        applied: list[str] = []

        guard = 0
        while version < target and guard < 64:
            guard += 1
            fn = self._functions.get(version)
            if fn is None:
                version += 1
                continue
            result = fn(work)
            if isinstance(result, dict):
                work = MigratedDict(result)
            applied.append(getattr(fn, "__name__", repr(fn)))
            stepped = _as_int(work.get("version"), version + 1)
            version = stepped if stepped > version else version + 1

        work["version"] = int(target)
        work.migrations = applied
        self._remember(data, applied)
        return work

    def applied(self, data: Any) -> list[str]:
        """The migration names that ran for ``data``."""
        found = getattr(data, "migrations", None)
        if found is not None:
            return list(found)
        entry = self._log.get(id(data))
        if entry is not None and entry[0] is data:
            return list(entry[1])
        return []

    def _remember(self, data: Any, applied: list[str]) -> None:
        self._log[id(data)] = (data, list(applied))
        self._log.move_to_end(id(data))
        while len(self._log) > 64:
            self._log.popitem(last=False)


_REGISTRIES: dict[str, MigrationRegistry] = {
    PRESET: MigrationRegistry(PRESET),
    CHAIN: MigrationRegistry(CHAIN),
    DOCUMENT: MigrationRegistry(DOCUMENT),
}


def register(
    from_version: int,
    fn: Callable[[dict], dict],
    kind: str = PRESET,
    overwrite: bool = False,
) -> Callable[[dict], dict]:
    """Register a migration function for one artefact kind.

    ``fn`` receives the document and returns the upgraded document.  It is
    called for every document of ``kind`` that is older than the current
    version, in version order.
    """
    try:
        registry = _REGISTRIES[kind]
    except KeyError:
        raise ValueError(f"unknown artefact kind {kind!r}") from None
    return registry.register(from_version, fn, overwrite=overwrite)


# ---------------------------------------------------------------------------
# built-in migrations
# ---------------------------------------------------------------------------
def preset_v0_to_v1(data: dict) -> dict:
    """Upgrade a v0 preset to v1."""
    out = dict(data)
    out["format"] = "auralis.preset"
    out["version"] = 1

    module = out.get("module")
    if isinstance(module, str):
        out["module"] = {"code": module, "element": ""}
    elif isinstance(module, dict):
        block = dict(module)
        if "code" not in block and "name" in block:
            block["code"] = block.pop("name")
        block.setdefault("code", "")
        block.setdefault("element", "")
        out["module"] = block
    else:
        out["module"] = {"code": "", "element": ""}

    params = out.pop("parameters", None)
    if params is None:
        params = out.get("params")
    out["params"] = _typed_params(params)

    state = out.get("state")
    if isinstance(state, dict) and "Type" in state:
        out["state"] = dict(state)
    elif isinstance(state, (bytes, bytearray)):
        out["state"] = {"Type": "Base64", "Value": _b64(state)}
    elif isinstance(state, str):
        out["state"] = {"Type": "String", "Value": state}
    elif state is None:
        out["state"] = {"Type": "Base64", "Value": ""}
    else:
        out["state"] = {"Type": "String", "Value": json.dumps(state, sort_keys=True, default=str)}
    return out


def chain_v0_to_v1(data: dict) -> dict:
    """Upgrade a v0 chain to v1."""
    out = dict(data)
    out["format"] = "auralis.chain"
    out["version"] = 1

    steps = out.pop("steps", None)
    if steps is None:
        steps = out.pop("elements", None)
    out.pop("elements", None)

    upgraded: list[dict] = []
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        entry = dict(step)
        code = entry.get("code")
        if not code:
            code = entry.get("module")
        entry["code"] = str(code or "")

        enabled = entry.get("enabled")
        if enabled is None:
            if "on" in entry:
                enabled = bool(entry["on"])
            elif "active" in entry:
                enabled = bool(entry["active"])
            elif "bypass" in entry:
                enabled = not bool(entry["bypass"])
            else:
                enabled = True
        entry["enabled"] = bool(enabled)

        params = entry.get("params")
        if params is None:
            params = entry.get("settings")
        entry["params"] = _typed_params(params)

        state = entry.get("state")
        if isinstance(state, dict) and "Type" in state:
            entry["state"] = dict(state)
        elif isinstance(state, (bytes, bytearray)):
            entry["state"] = {"Type": "String", "Value": _b64(state)}
        elif isinstance(state, str):
            entry["state"] = {"Type": "String", "Value": state}
        elif isinstance(state, dict):
            entry["state"] = {
                "Type": "String",
                "Value": _b64(json.dumps(state, sort_keys=True, default=str).encode("utf-8")),
            }
        else:
            entry["state"] = {"Type": "String", "Value": ""}
        upgraded.append(entry)

    out["steps"] = upgraded
    return out


def document_v0_to_v1(data: dict) -> dict:
    """Upgrade a v0 session document to v1."""
    out = dict(data)
    out["format"] = "auralis.document"
    out["version"] = 1

    audio = out.get("audio")
    if isinstance(audio, dict):
        block = dict(audio)
        block["path"] = str(block.get("path", "") or "")
        block["sample_rate"] = _as_int(
            block.get("sample_rate", block.get("sr", block.get("sampleRate", 0))), 0
        )
        block["channels"] = _as_int(block.get("channels", block.get("ch", 0)), 0)
        block["frames"] = _as_int(
            block.get("frames", block.get("length", block.get("nframes", 0))), 0
        )
        for alias in ("sr", "sampleRate", "ch", "num_channels", "length", "nframes", "samples"):
            block.pop(alias, None)
        out["audio"] = block
    else:
        out["audio"] = {"path": "", "sample_rate": 0, "channels": 0, "frames": 0}

    chain = out.get("chain")
    if isinstance(chain, list):
        chain = {"format": "auralis.chain", "version": 0, "elements": chain}
    if isinstance(chain, dict):
        chain = chain_v0_to_v1(chain) if _as_int(chain.get("version"), 1) < 1 else dict(chain)
        chain.setdefault("format", "auralis.chain")
        chain.setdefault("version", 1)
        chain.setdefault("steps", [])
    else:
        chain = {"format": "auralis.chain", "version": 1, "steps": []}
    out["chain"] = chain

    view = out.get("view")
    if isinstance(view, dict):
        block = dict(view)
        if "spectrogram" not in block:
            for alias in ("spectro", "show_spectrogram", "spectrogram_enabled"):
                if alias in block:
                    block["spectrogram"] = block[alias]
                    break
        for alias in ("spectro", "show_spectrogram", "spectrogram_enabled"):
            block.pop(alias, None)
        block.setdefault("zoom", 1.0)
        block.setdefault("scroll", 0.0)
        block.setdefault("spectrogram", True)
        block.setdefault("selection", None)
        out["view"] = block
    else:
        out["view"] = {"zoom": 1.0, "scroll": 0.0, "spectrogram": True, "selection": None}
    return out


# ---------------------------------------------------------------------------
# public entry points
# ---------------------------------------------------------------------------
def migrate_preset(data: dict) -> dict:
    """Upgrade a preset document to the current version."""
    return _REGISTRIES[PRESET].migrate(data)


def migrate_chain(data: dict) -> dict:
    """Upgrade a chain document to the current version."""
    return _REGISTRIES[CHAIN].migrate(data)


def migrate_document(data: dict) -> dict:
    """Upgrade a session document to the current version."""
    return _REGISTRIES[DOCUMENT].migrate(data)


def migrate(data: Any) -> dict:
    """Upgrade any Auralis document to the current version.

    The artefact kind is taken from the ``format`` field; when that field is
    missing the shape of the document decides.
    """
    if not isinstance(data, dict):
        raise TypeError("migration expects a dictionary")
    kind = _KIND_BY_FORMAT.get(str(data.get("format", "") or ""))
    if kind is None:
        if "steps" in data or "elements" in data:
            kind = CHAIN
        elif "chain" in data or "audio" in data or "view" in data:
            kind = DOCUMENT
        else:
            kind = PRESET
    return _REGISTRIES[kind].migrate(data)


def migrations_applied(data: Any) -> list[str]:
    """The names of the migrations that ran for ``data`` (empty list if none)."""
    if not isinstance(data, dict):
        return []
    found = getattr(data, "migrations", None)
    if found is not None:
        return list(found)
    kind = _KIND_BY_FORMAT.get(str(data.get("format", "") or ""))
    registries = [_REGISTRIES[kind]] if kind else list(_REGISTRIES.values())
    for registry in registries:
        names = registry.applied(data)
        if names:
            return names
    return []


# The built-in migrations are registered last so that they can be replaced by
# an application that knows a different history.
register(0, preset_v0_to_v1, PRESET, overwrite=True)
register(0, chain_v0_to_v1, CHAIN, overwrite=True)
register(0, document_v0_to_v1, DOCUMENT, overwrite=True)
