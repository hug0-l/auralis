"""
aurserver.presets — the on-disk preset library.

Storage layout (the root defaults to ``<repo>/var/presets``)::

    var/presets/
        index.json                       manifest, regenerated from disk
        <Category>/<Name>.json            one preset per file
        builtin/<Category>/<Name>.json    the shipped starter library

Every preset file uses the ``auralis.preset`` envelope from CONTRACT §6.2 and
every scalar inside it is wrapped in the typed-value envelope from
:mod:`aurcore.serial.typed_value`.

The module is transport-only: it moves JSON in and JSON out.  It never runs
DSP.  It does read the module registry, because a preset only means something
next to the module it belongs to, so the library answers four questions about
every file it finds:

* does the module the preset names still exist in this build?
* do the parameters it carries belong to that module?
* are the values inside the domain the module accepts?
* is the file itself sound, or was it truncated or hand edited?

A file that cannot be read never takes the library down.  It is reported as a
problem, the rest of the library is still listed, and the report says what is
wrong with it.

The shipped library
-------------------

The presets the product ships with are defined in
:mod:`aurserver.preset_library` and are written into
``<root>/builtin/<Category>/<Name>.json`` by
:func:`seed_builtin_presets`.  They are the programs the plug-in family
publishes: the plug-in reads the same directory, so the drop-down a host
shows is the library this module writes.

Every module of the catalogue has at least one preset, and every preset
names a *situation* rather than a knob position.  A preset names its
parameters by the names the canonical table gives them, so the plug-in
matches it: the plug-in drops a preset that carries an old name, while the
server resolves an old name onto the parameter it belongs to.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from aurcore import registry
from aurcore.serial import migrate_preset, typed_value as tv
from aurcore.serial.preset_json import (
    PARAM_ID_KEY,
    PARAM_SIG_KEY,
    PRESET_FORMAT,
    PRESET_VERSION,
    parameter_signature,
    plain_value,
)

from . import preset_library

log = logging.getLogger(__name__)

INDEX_FORMAT = "auralis.preset-index"
FORMAT_VERSION = PRESET_VERSION
INDEX_NAME = "index.json"

#: The moment a shipped preset carries.  The presets of the product were
#: not written by a user, they came with the product, so they carry no
#: moment of their own: a build that wrote the moment of the build into
#: them would make two builds of the same tree differ.
BUILTIN_MODIFIED = 0.0

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = REPO_ROOT / "var" / "presets"

#: Characters that may never appear in a preset name or category.  The first
#: three are the path-traversal vectors and are rejected explicitly.
_TRAVERSAL = ("/", "\\", "..")
_ILLEGAL = (":", "*", "?", '"', "<", ">", "|", "\0")

#: Control characters.  The file systems refuse them, so a name that carries
#: one could never become a file; it is rejected before the write is tried
#: instead of failing the write with an operating system error.
_CONTROL = tuple(chr(code) for code in range(0x20)) + ("\x7f",)

_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

MAX_NAME_LENGTH = 96

#: The typed-value types the interchange format knows.
TYPED_TYPES = ("Float", "Int", "UInt", "Bool", "String", "Base64")

#: The status a library entry can carry.
STATUS_OK = "ok"          # the file is sound and its module is in this build
STATUS_ORPHAN = "orphan"  # the module the preset names is not in this build
STATUS_DAMAGED = "damaged"  # the file reads, but the document is not sound

router = APIRouter(tags=["presets"])


# ---------------------------------------------------------------------------
# names and paths
# ---------------------------------------------------------------------------
def sanitise_component(value: Any, field: str = "name") -> str:
    """Validate one path component (a preset name or a category).

    Rejects empty values, path separators, ``..`` traversal, characters that
    are illegal in a file name, control characters, reserved device names and
    hidden names.  What comes back is a single path component: it can be
    used as a file name and it can never become a path.

    Raises ``HTTPException`` (400) when the value cannot be used.
    """
    if value is None:
        raise HTTPException(400, f"preset {field} must not be empty")
    if not isinstance(value, str):
        raise HTTPException(400, f"preset {field} must be a string")
    text = value.strip()
    if not text:
        raise HTTPException(400, f"preset {field} must not be empty")
    if any(bad in text for bad in _TRAVERSAL):
        raise HTTPException(400, f"preset {field} must not contain '/', '\\' or '..'")
    if any(bad in text for bad in _ILLEGAL):
        raise HTTPException(400, f"preset {field} contains characters that are not allowed")
    if any(bad in text for bad in _CONTROL):
        raise HTTPException(400, f"preset {field} must not contain control characters")
    if text in {".", ".."} or text.startswith("."):
        raise HTTPException(400, f"preset {field} must not start with a dot")
    if text.endswith((" ", ".")):
        raise HTTPException(400, f"preset {field} must not end with a space or a dot")
    if text.upper() in _RESERVED:
        raise HTTPException(400, f"preset {field} '{text}' is a reserved device name")
    if len(text) > MAX_NAME_LENGTH:
        raise HTTPException(400, f"preset {field} is longer than {MAX_NAME_LENGTH} characters")
    return text


def preset_path(root: Path, category: str, name: str) -> Path:
    """Return the file a preset lives in.

    The name and the category are single path components and nothing else:
    the result is checked against the root, so a name can never turn into a
    path of its own.
    """
    file_name = f"{name}.json"
    if len(Path(file_name).parts) != 1 or len(Path(category).parts) != 1:
        raise HTTPException(400, "the preset name and the category must be one path component")
    target = root / category / file_name
    if not target.is_relative_to(root):
        raise HTTPException(400, "path escapes the preset library")
    return target


def resolve_root(app: Any = None) -> Path:
    """Return the preset library root.

    Resolution order: ``app.state.preset_root`` -> ``AURALIS_PRESET_DIR``
    environment variable -> ``<repo>/var/presets``.
    """
    state = getattr(app, "state", None)
    root = getattr(state, "preset_root", None) if state is not None else None
    if not root:
        root = os.environ.get("AURALIS_PRESET_DIR")
    if not root:
        root = DEFAULT_ROOT
    return Path(root).expanduser().resolve()


def resolve_preset_path(root: Path, path: Any) -> Path:
    """Resolve a client supplied path inside the library root.

    Absolute paths are accepted but must still live inside the root, so a
    traversal attempt cannot reach arbitrary files.
    """
    text = str(path if path is not None else "").strip()
    if not text:
        raise HTTPException(400, "path is required")
    candidate = Path(text)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    root_resolved = root.resolve()
    if resolved == root_resolved:
        raise HTTPException(400, "path must point at a preset file")
    if not resolved.is_relative_to(root_resolved):
        raise HTTPException(400, "path escapes the preset library")
    if resolved.name == INDEX_NAME:
        raise HTTPException(400, "the preset index is not a preset")
    if resolved.is_dir():
        raise HTTPException(400, "path must point at a preset file")
    return resolved


# ---------------------------------------------------------------------------
# the module registry
# ---------------------------------------------------------------------------
def module_known(code: str) -> bool:
    """True when ``code`` names a module this build has."""
    return bool(code) and registry.has(code)


def module_display_name(code: str) -> str:
    """The display name of a module, or an empty string when it is gone."""
    if not code:
        return ""
    try:
        cls = registry.get(code)
    except KeyError:
        return ""
    return str(getattr(cls, "display_name", "") or "")


#: The parameter descriptors of a module, keyed by parameter name.  Building
#: them means instantiating the module, so they are remembered: a scan of the
#: library asks for the same handful of modules over and over.
_DESCRIPTORS: dict[str, dict] = {}


def parameter_descriptors(module_code: str) -> dict:
    """The parameter descriptors of a module, keyed by parameter name.

    An empty mapping is returned for a module this build does not have.
    """
    if module_code in _DESCRIPTORS:
        return _DESCRIPTORS[module_code]
    descs: dict = {}
    try:
        cls = registry.get(module_code)
    except KeyError:
        _DESCRIPTORS[module_code] = descs
        return descs
    instance = None
    for args in ((48000, 2), ()):
        try:
            instance = cls(*args)
            break
        except Exception:  # noqa: BLE001 - a module that cannot start has no domain
            continue
    if instance is not None:
        try:
            described = list(instance.describe())
            descs = {desc.name: desc for desc in described}
            # A parameter that was renamed keeps the name it had: a preset
            # written before the rename is not a preset with a broken
            # parameter, so the old name is a key of the map as well.
            for desc in described:
                for alias in getattr(desc, "aliases", ()) or ():
                    descs.setdefault(str(alias), desc)
        except Exception:  # noqa: BLE001 - defensive
            descs = {}
    _DESCRIPTORS[module_code] = descs
    return descs


def module_code_of(document: Any) -> str:
    """The module code a preset document names (empty when it names none)."""
    if not isinstance(document, dict):
        return ""
    module = document.get("module")
    if isinstance(module, dict):
        return str(module.get("code") or "").strip()
    if isinstance(module, str):
        return module.strip()
    return ""


# ---------------------------------------------------------------------------
# typed-value helpers
# ---------------------------------------------------------------------------
def _wrap_params(params: dict | None, module_code: str = "") -> dict:
    """Wrap parameter values, keeping values that are already wrapped.

    A value that arrives as a numpy scalar is turned into the plain Python
    value first, so a whole number stays a whole number and does not become
    the string ``"4"`` on the way to the disk.

    Every parameter the module knows also carries the id it has inside the
    module and the meaning it has, so a reader can tell that the parameter
    moved or that it changed its meaning.  A parameter the module does not
    know carries neither: the library does not guess.
    """
    descs = parameter_descriptors(module_code) if module_code else {}
    ids = {name: index for index, name in enumerate(descs)}

    out: dict[str, Any] = {}
    for key, value in (params or {}).items():
        name = str(key)
        if isinstance(value, dict) and "Type" in value and "Value" in value:
            node = {"Type": str(value["Type"]), "Value": value["Value"]}
        else:
            node = tv.wrap(plain_value(value))

        desc = descs.get(name)
        if desc is not None:
            node.setdefault(PARAM_ID_KEY, ids[name])
            signature = parameter_signature(desc)
            if signature:
                node.setdefault(PARAM_SIG_KEY, signature)

        out[name] = node
    return out


def _state_envelope(state: Any) -> dict:
    """Normalise a module state blob into a typed-value envelope."""
    if state is None:
        return tv.wrap(b"")
    if isinstance(state, dict) and "Type" in state and "Value" in state:
        return {"Type": str(state["Type"]), "Value": state["Value"]}
    if isinstance(state, (bytes, bytearray)):
        return tv.wrap(bytes(state))
    if isinstance(state, str):
        # a bare string is assumed to be base64 payload text already
        return {"Type": "Base64", "Value": state}
    return tv.wrap(state)


def _text(value: Any) -> str:
    """Read a string out of a raw value or a typed-value envelope."""
    if isinstance(value, dict) and "Type" in value and "Value" in value:
        return str(tv.unwrap(value, ""))
    if value is None:
        return ""
    return str(value)


def typed_value_problem(node: Any) -> str:
    """What is wrong with one typed value (an empty string when it is sound)."""
    if not isinstance(node, dict):
        return "is not a typed value"
    if "Type" not in node or "Value" not in node:
        return "is not a typed value"
    kind = str(node.get("Type"))
    if kind not in TYPED_TYPES:
        return f"has the unknown type {kind!r}"
    value = node.get("Value")
    if kind == "Float":
        try:
            float(value)
        except (TypeError, ValueError):
            return "holds a value that is not a number"
    elif kind in ("Int", "UInt"):
        if isinstance(value, bool) or not isinstance(value, int):
            try:
                int(value)
            except (TypeError, ValueError):
                return "holds a value that is not a whole number"
    elif kind == "Bool":
        if not isinstance(value, (bool, int)):
            return "holds a value that is not a switch"
    return ""


# ---------------------------------------------------------------------------
# what is wrong with a document
# ---------------------------------------------------------------------------
def document_problems(document: Any) -> list[str]:
    """What is wrong with the shape of a preset document."""
    problems: list[str] = []
    if not isinstance(document, dict):
        return ["the preset document is not a JSON object"]
    module = document.get("module")
    if not isinstance(module, dict):
        problems.append("the module block is not an object")
    params = document.get("params")
    if params is None:
        problems.append("the document carries no parameter block")
    elif not isinstance(params, dict):
        problems.append("the parameter block is not an object")
    else:
        for name, node in params.items():
            problem = typed_value_problem(node)
            if problem:
                problems.append(f"the parameter {name!r} {problem}")
    return problems


def value_problem(desc: Any, value: Any) -> str:
    """What is wrong with one value for one parameter (empty when it fits).

    A value outside the domain is not a problem: the module clamps it when
    the preset is applied, and the library reports that separately.
    """
    kind = getattr(desc, "type", "float")
    if kind == "enum":
        choices = tuple(getattr(desc, "choices", ()) or ())
        if not isinstance(value, str):
            return f"is an enum and needs one of {list(choices)}, not {value!r}"
        if value not in choices:
            return f"is an enum and {value!r} is not one of {list(choices)}"
        return ""
    if kind == "bool":
        if not isinstance(value, bool):
            return f"is a switch and needs true or false, not {value!r}"
        return ""
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"is a whole number and needs a number, not {value!r}"
        return ""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return f"is a number and needs a number, not {value!r}"
    return ""


def module_problems(module_code: str, params: Any) -> list[str]:
    """Compare a preset with the module it claims to belong to."""
    if not module_code:
        return ["the preset names no module"]
    if not module_known(module_code):
        return [f"the module {module_code!r} is not in this build"]
    if not isinstance(params, dict):
        return []
    descs = parameter_descriptors(module_code)
    if not descs:
        # the module is known but its parameters could not be read: the
        # library says nothing about the values rather than guessing
        return []
    problems: list[str] = []
    for name, node in (params or {}).items():
        value = tv.unwrap(node, None)
        desc = descs.get(name)
        if desc is None:
            problems.append(
                f"the parameter {name!r} does not belong to module {module_code!r}"
            )
            continue
        problem = value_problem(desc, value)
        if problem:
            problems.append(f"the parameter {name!r} {problem}")
    return problems


def unknown_parameters(module_code: str, params: Any) -> list[str]:
    """The parameters of a preset that the module does not know.

    A module whose parameters could not be read has no unknown parameters:
    the library does not guess.
    """
    if not module_code or not module_known(module_code):
        return []
    if not isinstance(params, dict):
        return []
    descs = parameter_descriptors(module_code)
    if not descs:
        return []
    return sorted(name for name in params if name not in descs)


def out_of_range(module_code: str, params: Any) -> list[dict]:
    """The parameters whose value lies outside the domain of the module.

    The library keeps the value as it was saved; the module clamps it when
    the preset is applied, and this is where that is said out loud.
    """
    if not module_code or not module_known(module_code):
        return []
    if not isinstance(params, dict):
        return []
    descs = parameter_descriptors(module_code)
    found: list[dict] = []
    for name, node in params.items():
        desc = descs.get(name)
        if desc is None or getattr(desc, "type", "") in ("enum", "bool"):
            continue
        value = tv.unwrap(node, None)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        try:
            low = float(desc.minimum)
            high = float(desc.maximum)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            continue
        if float(value) < low or float(value) > high:
            found.append(
                {
                    "name": str(name),
                    "value": value,
                    "minimum": low,
                    "maximum": high,
                    "unit": str(getattr(desc, "unit", "") or ""),
                }
            )
    return found


# ---------------------------------------------------------------------------
# file IO
# ---------------------------------------------------------------------------
@dataclass
class PresetRecord:
    """One preset file, read from the disk."""

    path: Path
    document: dict
    version: int = 0
    problems: list[str] = field(default_factory=list)
    module_code: str = ""

    @property
    def status(self) -> str:
        """The status of the file: ok, orphan or damaged."""
        if not self.module_code:
            return STATUS_DAMAGED
        if not module_known(self.module_code):
            return STATUS_ORPHAN
        return STATUS_OK if not self.problems else STATUS_DAMAGED


def _document_version(data: Any) -> int:
    try:
        return int(data.get("version", 0) or 0)
    except (AttributeError, TypeError, ValueError):
        return 0


def read_preset_record(path: Path) -> PresetRecord:
    """Read one preset file and describe what is wrong with it.

    An older document is migrated to the current format.  A file that cannot
    be read at all raises ``HTTPException``; a file that reads but does not
    make sense comes back with its problems listed.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise HTTPException(404, "preset not found") from None
    except OSError as exc:
        raise HTTPException(500, f"cannot read preset: {exc}") from None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"preset file is not valid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise HTTPException(400, "preset file must contain a JSON object")

    version = _document_version(data)
    document = dict(migrate_preset(data))

    problems: list[str] = []
    if version > FORMAT_VERSION:
        problems.append(
            f"the file was written by a newer build (format version {version}, "
            f"this build reads {FORMAT_VERSION})"
        )
    problems.extend(document_problems(document))

    code = module_code_of(document)
    if code:
        problems.extend(module_problems(code, document.get("params")))
    else:
        problems.append("the preset names no module")
    return PresetRecord(
        path=path,
        document=document,
        version=version,
        problems=problems,
        module_code=code,
    )


def read_preset(path: Path) -> dict:
    """Read one preset file and upgrade it to the current format version."""
    return read_preset_record(path).document


def write_preset(path: Path, data: dict) -> Path:
    """Write one preset file atomically."""
    document = dict(migrate_preset(data))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise HTTPException(500, f"cannot write preset: {exc}") from None
    return path


def _entry(path: Path, root: Path, record: PresetRecord) -> dict:
    """Build one listing record for a preset file."""
    data = record.document
    module = data.get("module") if isinstance(data.get("module"), dict) else {}
    try:
        modified = path.stat().st_mtime
    except OSError:
        modified = 0.0
    return {
        "name": _text(data.get("name")) or path.stem,
        "category": _text(data.get("category")) or path.parent.name,
        "path": path.relative_to(root).as_posix(),
        "module_code": record.module_code,
        "modified": modified,
        "builtin": bool(data.get("builtin", False)),
        "display_name": str(module.get("display_name") or "")
        or module_display_name(record.module_code),
        "status": record.status,
        "problems": list(record.problems),
    }


def _problem_entry(path: Path, root: Path, message: str, status: str) -> dict:
    """Build one listing record for a file that could not be read."""
    try:
        modified = path.stat().st_mtime
    except OSError:
        modified = 0.0
    return {
        "name": path.stem,
        "category": path.parent.name,
        "path": path.relative_to(root).as_posix(),
        "module_code": "",
        "modified": modified,
        "builtin": False,
        "display_name": "",
        "status": status,
        "problems": [message],
    }


@dataclass
class LibraryScan:
    """The result of walking the preset library."""

    presets: list[dict] = field(default_factory=list)
    problems: list[dict] = field(default_factory=list)

    @property
    def total(self) -> int:
        """The number of preset files the library holds."""
        return len(self.presets) + len(self.problems)


def scan_library(root: Path) -> LibraryScan:
    """Walk the library and report every file it finds.

    A file that cannot be read is reported as a problem; it never stops the
    walk and it never stops the rest of the library from being listed.
    """
    scan = LibraryScan()
    if not root.is_dir():
        return scan
    for path in sorted(root.rglob("*.json")):
        if path.name == INDEX_NAME:
            continue
        rel = path.relative_to(root)
        if any(part.startswith(".") for part in rel.parts[:-1]):
            continue
        try:
            record = read_preset_record(path)
        except HTTPException as exc:
            if exc.status_code == 404:
                message = "the file was removed while the library was read"
            else:
                message = str(exc.detail)
            scan.problems.append(_problem_entry(path, root, message, STATUS_DAMAGED))
            continue
        except Exception as exc:  # noqa: BLE001 - no file may take the library down
            scan.problems.append(
                _problem_entry(path, root, f"the file cannot be read: {exc}", STATUS_DAMAGED)
            )
            continue
        entry = _entry(path, root, record)
        scan.presets.append(entry)
        if entry["problems"]:
            scan.problems.append(entry)
    scan.presets.sort(key=sort_key)
    scan.problems.sort(key=sort_key)
    return scan


def sort_key(entry: dict) -> tuple:
    """The order of the library: category, then name, then path.

    The key is total, so the order does not depend on the order the file
    system happens to hand the files back in.
    """
    name = str(entry.get("name", ""))
    category = str(entry.get("category", ""))
    return (category.casefold(), name.casefold(), name, str(entry.get("path", "")))


def search_text(entry: dict) -> str:
    """The text one entry is searched by.

    The same four fields the web interface searches, so the two agree.
    """
    return " ".join(
        [
            str(entry.get("name", "")),
            str(entry.get("category", "")),
            str(entry.get("module_code", "")),
            str(entry.get("display_name", "")),
        ]
    )


def search_presets(entries: list[dict], needle: Any) -> list[dict]:
    """The entries whose name, category or module carries ``needle``.

    The match is a case insensitive substring; an empty needle keeps
    everything.
    """
    text = str(needle or "").strip().casefold()
    if not text:
        return list(entries)
    return [entry for entry in entries if text in search_text(entry).casefold()]


def filter_presets(
    entries: list[dict],
    needle: Any = None,
    category: Any = None,
) -> list[dict]:
    """Filter the library by a search text and by a category."""
    found = search_presets(entries, needle)
    wanted = str(category or "").strip().casefold()
    if wanted:
        found = [e for e in found if str(e.get("category", "")).casefold() == wanted]
    return found


def list_presets(
    root: Path,
    query: Any = None,
    category: Any = None,
) -> list[dict]:
    """List the presets in the library, sorted by category then name.

    ``query`` keeps the entries whose name, category or module carries the
    text; ``category`` keeps the entries of one category.
    """
    entries = scan_library(root).presets
    found = filter_presets(entries, query, category)
    found.sort(key=sort_key)
    return found


def write_index(root: Path, scan: LibraryScan | None = None) -> Path:
    """Regenerate the manifest from the files on disk."""
    root.mkdir(parents=True, exist_ok=True)
    index = root / INDEX_NAME
    if scan is None:
        scan = scan_library(root)
    data = {
        "format": INDEX_FORMAT,
        "version": FORMAT_VERSION,
        "updated": time.time(),
        "presets": scan.presets,
        "problems": scan.problems,
    }
    tmp = index.with_name(index.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, index)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        log.warning("cannot write preset index: %s", exc)
    return index


# ---------------------------------------------------------------------------
# the shipped library
# ---------------------------------------------------------------------------
def module_element(module_code: str) -> str:
    """The element a module publishes (the name of its runtime element).

    The canonical table knows the element of every module, so the library
    does not have to be told: a preset that names a module carries the
    element of that module.
    """
    if not module_code:
        return ""
    try:
        cls = registry.get(module_code)
    except KeyError:
        return ""
    return str(getattr(cls, "element", "") or "")


#: The shipped library.  Every module of the catalogue has at least one
#: preset, and every preset names a situation rather than a knob position.
#: The definitions live in :mod:urserver.preset_library; they are
#: imported here so that the module that reads and writes the library also
#: carries what it ships.
BUILTIN_PRESETS: tuple[dict, ...] = preset_library.BUILTIN_PRESETS

#: The parameters a preset does not set, per module: the ones the product
#: owns (the mode of the chain and the answer of the classifier).
OWNED_PARAMETERS: dict[str, tuple[str, ...]] = preset_library.OWNED_PARAMETERS

#: The modules whose first program has to be the configuration the module
#: comes with.
FIRST_PROGRAM_IS_THE_DEFAULT: tuple[str, ...] = (
    preset_library.FIRST_PROGRAM_IS_THE_DEFAULT
)


def build_preset_document(preset: dict, *, builtin: bool = False) -> dict:
    """Build the JSON document for one preset definition.

    The element of the module is taken from the registry when the preset
    does not carry one: the canonical table names the element of every
    module, and the table is the one place that knows.

    The shipped library carries a fixed moment, and the presets the user
    made carry the moment they were written.  The shipped presets were
    not written by anybody: they came with the product, and a build that
    wrote the moment of the build into them would make two builds of the
    same tree differ by a byte.
    """
    code = str(preset["module_code"])
    element = str(preset.get("element", "")) or module_element(code)
    return {
        "format": PRESET_FORMAT,
        "version": FORMAT_VERSION,
        "name": tv.wrap(preset["name"]),
        "category": tv.wrap(preset["category"]),
        "description": tv.wrap(preset.get("description", "")),
        "module": {
            "code": code,
            "element": element,
            "display_name": str(preset.get("display_name", "")),
        },
        "params": _wrap_params(preset.get("params"), code),
        "state": _state_envelope(preset.get("state")),
        "builtin": bool(builtin),
        "modified": BUILTIN_MODIFIED if builtin else time.time(),
    }


def _same_document(left: Any, right: Any) -> bool:
    """True when two documents say the same thing.

    The moment a document was written is not part of what it says: a file
    that only differs in that field is the same file.
    """
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    first = {k: v for k, v in left.items() if k != "modified"}
    second = {k: v for k, v in right.items() if k != "modified"}
    return first == second


def seed_builtin_presets(root: Path | None = None) -> list[str]:
    """Write the shipped library into ``<root>/builtin``.

    The ``builtin`` folder belongs to the product: it *is* the shipped
    library, so a preset that changed is written again and a file the
    product no longer ships is taken away.  That matters for the plug-in:
    the program list it publishes is built from this folder, and a file
    that names a parameter the module does not have is dropped from it.

    The presets the user has made live in the category folders beside
    ``builtin`` and are never touched.

    Returns the list of files that were written.
    """
    base = Path(root).resolve() if root is not None else DEFAULT_ROOT.resolve()
    folder = base / "builtin"
    written: list[str] = []

    expected: dict[Path, dict] = {}
    for preset in BUILTIN_PRESETS:
        target = folder / preset["category"] / f"{preset['name']}.json"
        expected[target] = build_preset_document(preset, builtin=True)

    for target, document in expected.items():
        if target.exists():
            try:
                current = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                current = None
            if _same_document(current, document):
                continue
        try:
            write_preset(target, document)
            written.append(str(target))
        except HTTPException as exc:
            log.warning("cannot seed preset %s: %s", preset["name"], exc.detail)

    # a file the product no longer ships must not stay in the library: the
    # plug-in would publish it and the server would list it.
    for path in sorted(folder.rglob("*.json")) if folder.is_dir() else []:
        if path in expected:
            continue
        try:
            path.unlink()
            written.append(str(path))
        except OSError as exc:  # pragma: no cover - defensive
            log.warning("cannot remove the stale preset %s: %s", path, exc)

    if written:
        write_index(base)
    return written


def ensure_seeded(root: Path) -> None:
    """Seed the shipped library if it is missing entirely.

    The library that is there is the product's own: it is put in step with
    the build at the start-up of the application, where
    :func:`seed_builtin_presets` is called, and not on every request.
    """
    try:
        if not (root / "builtin").is_dir():
            seed_builtin_presets(root)
    except OSError as exc:  # pragma: no cover - defensive
        log.warning("cannot seed the preset library: %s", exc)


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------
async def _json_body(request: Request) -> dict:
    """Read a JSON object from the request body."""
    try:
        raw = await request.body()
    except Exception:
        return {}
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(400, "request body must be valid JSON") from None
    if not isinstance(data, dict):
        raise HTTPException(400, "request body must be a JSON object")
    return data


@router.get("/api/presets")
async def api_list_presets(
    request: Request,
    q: str | None = None,
    category: str | None = None,
) -> dict:
    """Return the presets in the library.

    ``q`` keeps the presets whose name, category or module carries the text;
    ``category`` keeps the presets of one category.  The files that cannot be
    read are reported in ``problems``; they never stop the listing.
    """
    root = resolve_root(request.app)
    ensure_seeded(root)
    scan = scan_library(root)
    entries = filter_presets(scan.presets, q, category)
    entries.sort(key=sort_key)
    write_index(root, scan)
    return {
        "presets": entries,
        "problems": scan.problems,
        "count": len(entries),
        "total": len(scan.presets),
    }


@router.post("/api/preset/save")
async def api_save_preset(request: Request) -> dict:
    """Save one preset into the library.

    Accepts the library shape ``{name, category, module_code, params, state}``
    and a ready made preset document ``{name, module: {code, element},
    params, state}``; both are stored in the canonical envelope.

    The module the preset belongs to must exist in this build, and the name
    and the category must each be a single path component.
    """
    payload = await _json_body(request)
    name = sanitise_component(payload.get("name"), "name")
    if name.lower().endswith(".json"):
        name = name[:-5]
        name = sanitise_component(name, "name")
    category = sanitise_component(payload.get("category") or "Presets", "category")

    module_block = payload.get("module")
    module_block = module_block if isinstance(module_block, dict) else {}
    module_code = str(payload.get("module_code") or module_block.get("code") or "").strip()
    if not module_code:
        raise HTTPException(400, "module_code is required")
    if not module_known(module_code):
        raise HTTPException(400, f"unknown module code {module_code!r}")
    element = str(payload.get("element") or module_block.get("element") or "")
    display_name = str(payload.get("display_name") or module_block.get("display_name") or "")

    root = resolve_root(request.app)
    target = preset_path(root, category, name)

    data = {
        "format": PRESET_FORMAT,
        "version": FORMAT_VERSION,
        "name": tv.wrap(name),
        "category": tv.wrap(category),
        "description": tv.wrap(str(payload.get("description") or "")),
        "module": {
            "code": module_code,
            "element": element,
            "display_name": display_name,
        },
        "params": _wrap_params(payload.get("params"), module_code),
        "state": _state_envelope(payload.get("state")),
        "builtin": False,
        "modified": time.time(),
    }
    write_preset(target, data)
    write_index(root)
    return {
        "path": target.relative_to(root).as_posix(),
        "preset": data,
        "problems": [],
    }


@router.post("/api/preset/load")
async def api_load_preset(request: Request) -> dict:
    """Load one preset from the library.

    When the caller names the module the preset is meant for, a preset that
    belongs to another module is refused.  A preset whose module is not in
    this build is reported, not silently handed over as if it fitted.
    """
    payload = await _json_body(request)
    root = resolve_root(request.app)
    target = resolve_preset_path(root, payload.get("path"))
    record = read_preset_record(target)

    wanted = str(payload.get("module_code") or "").strip()
    if wanted:
        if record.module_code and record.module_code != wanted:
            raise HTTPException(
                409,
                f"the preset {_text(record.document.get('name')) or record.path.stem!r} "
                f"belongs to module {record.module_code!r} and cannot be applied to "
                f"module {wanted!r}",
            )
        if not module_known(wanted):
            raise HTTPException(409, f"the module {wanted!r} is not in this build")

    params = record.document.get("params")
    params = params if isinstance(params, dict) else {}
    document = dict(record.document)
    document["problems"] = list(record.problems)
    document["report"] = {
        "module": record.module_code,
        "module_known": module_known(record.module_code),
        "module_missing": bool(record.module_code) and not module_known(record.module_code),
        "status": record.status,
        "out_of_range": out_of_range(record.module_code, params),
        "unknown_params": unknown_parameters(record.module_code, params),
    }
    return document


@router.delete("/api/preset")
async def api_delete_preset(request: Request) -> dict:
    """Delete one preset from the library."""
    payload = await _json_body(request)
    path = payload.get("path") or request.query_params.get("path")
    root = resolve_root(request.app)
    target = resolve_preset_path(root, path)
    if not target.exists():
        raise HTTPException(404, "preset not found")
    try:
        target.unlink()
    except OSError as exc:
        raise HTTPException(500, f"cannot delete preset: {exc}") from None
    write_index(root)
    return {"ok": True, "path": target.relative_to(root).as_posix()}


# ---------------------------------------------------------------------------
# wiring
# ---------------------------------------------------------------------------
def register(app) -> None:
    """Attach the preset library routes to an application.

    The starter library is seeded here, which is the application start-up
    moment for this module.  It is also re-seeded lazily if the ``builtin``
    directory disappears.
    """
    app.include_router(router)
    try:
        seed_builtin_presets(resolve_root(app))
    except OSError as exc:  # pragma: no cover - defensive
        log.warning("cannot seed the preset library: %s", exc)
