"""
plugins.identity — the plug-in identity record layer.

The adapter layer ships **one template binary per plug-in format**.  Which
Auralis module a given binary implements is decided at *build* time by
stamping an identity record into a reserved slot inside the binary.  This
module owns:

* :class:`PluginIdentity` — the record itself, and its JSON representation;
* the deterministic derivation of every field from the module registry
  (:func:`identities_for`), so adding a module to the registry automatically
  produces an identity (and therefore a plug-in) for every format;
* the stamp / read-back machinery (:func:`stamp_identity`,
  :func:`verify_identity`) that embeds the record in a binary.

Design rules
------------
* **Names, not numbers.**  The core identifies a parameter by its canonical
  name (see ``aurcore/params.py``).  The *host* parameter id is an integer
  taken from the generated registry index so that it is stable across
  rebuilds *and* across changes to the registry; the mapping lives in
  ``param_id_map`` and is what the adapter uses to translate between host
  ids and core parameter names.
* **Stable identifiers.**  The VST3 class id is derived from the module's
  four-character product code, so the value in the record is the class id
  the shipped binary reports.  The class id is a **GUID string**, and the
  sixteen bytes a binary reports are that string's *platform encoding* —
  see :func:`class_id_bytes` and :func:`class_id_string`.  A shipping
  product must register those class ids with the plug-in format body; the
  ones produced here are locally generated and must never be reused for a
  different module.
* **One source of truth.**  Nothing in this file hard-codes a module list or
  a parameter list.  Everything is derived from ``aurcore.schema``.

See ``plugins/README.md`` for the architecture and ``plugins/identity.schema.json``
for the on-disk format of the record.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

__all__ = [
    "PluginIdentity",
    "IdentityError",
    "IdentityStampError",
    "ParamIndex",
    "ParamRecord",
    "IDENTITY_MAGIC",
    "IDENTITY_REGION_SIZE",
    "IDENTITY_SLOT_SIZE",
    "IDENTITY_FORMAT",
    "IDENTITY_VERSION",
    "PLUGIN_MANUFACTURER_ID",
    "VST3_CLASS_ID_PREFIX",
    "AAX_MANUFACTURER_ID",
    "AAX_VARIANT_PLUG_IDS",
    "derive_fuid",
    "fuid_bytes",
    "fuid_hex",
    "class_id_text",
    "class_id_bytes",
    "class_id_string",
    "host_is_com_compatible",
    "host_class_id_bytes",
    "param_index",
    "module_param_ids",
    "module_param_records",
    "identities_for",
    "identity_for",
    "write_identity",
    "read_identity",
    "stamp_identity",
    "verify_identity",
    "make_template",
    "identity_bytes",
    "load_schema",
    "validate_identity",
]

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------

#: The ASCII marker that precedes the identity region inside a binary.
#: It is written as individual bytes by the C++ side so that the marker
#: sequence occurs exactly ONCE in the built binary (a second occurrence
#: would make the stamping ambiguous).
IDENTITY_MAGIC = b"AURIDENTITYv1"

#: Size of the identity region that follows the marker, in bytes.  The region
#: holds a UTF-8 JSON document padded with NUL bytes.  4 KiB is comfortably
#: larger than the largest record produced today (the largest module carries
#: 19 parameters; its record is ~1.4 kB) while staying small enough to be
#: stamped into a code section.
IDENTITY_REGION_SIZE = 4096

#: Total size of the reserved slot (marker + region).
IDENTITY_SLOT_SIZE = len(IDENTITY_MAGIC) + IDENTITY_REGION_SIZE

#: ``format`` discriminator written into the record.
IDENTITY_FORMAT = "auralis.identity"

#: Schema version of the record.
IDENTITY_VERSION = 1

#: Manufacturer id (four characters) shared by every plug-in format we
#: ship.  Frozen forever: changing it would change every identifier we
#: have ever emitted.
PLUGIN_MANUFACTURER_ID = "AURA"

#: AAX manufacturer id (four characters).  The same four characters are
#: used as the VST3 manufacturer code, so one module carries one identity
#: across formats.
AAX_MANUFACTURER_ID = PLUGIN_MANUFACTURER_ID

#: The two fixed words that precede the manufacturer and product codes
#: inside a VST3 class id.  They are the constants the plug-in wrapper
#: bakes into every binary it produces.
VST3_CLASS_ID_PREFIX = (0x0101ABAB, 0xABCDEF01)

#: The number of bytes a class id occupies.
CLASS_ID_LENGTH = 16

#: AAX plug-in ids for the three channel variants of every module.  The
#: variant is part of the effect identifier, so a mono, a mono-to-stereo and
#: a stereo build of the same module are three distinct AAX effect ids.
AAX_VARIANT_PLUG_IDS = ("MONO", "M2ST", "STER")

_BASE36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "identity.schema.json")


class IdentityError(Exception):
    """Raised when an identity record is malformed or inconsistent."""


class IdentityStampError(IdentityError):
    """Raised when a binary cannot be stamped or read back."""


# --------------------------------------------------------------------------
# the record
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PluginIdentity:
    """The identity of one Auralis plug-in instance.

    Attributes
    ----------
    module_code:
        Canonical module code, e.g. ``"AUR_DECLICK"``.  This is the key into
        the registry and into the core.
    display_name:
        Human readable module name, e.g. ``"De-Click"``.
    product_name:
        Product line name shown to the host, e.g. ``"Auralis De-Click"``.
    file_description:
        Long description shown in the plug-in file browser.
    original_filename:
        The file name the plug-in is installed under, e.g.
        ``"Auralis De-Click.vst3"``.
    internal_name:
        Stable identifier used by the host's session/preset machinery.
    category:
        Registry category (``"Repair"``, ``"Utility"``, ...).
    vst3_fuid:
        The VST3 class id as a canonical dashed GUID string.  Sixteen bytes
        are encoded as ``xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx``.  The
        string is the identifier; the bytes a binary reports are that
        string's platform encoding (see :func:`class_id_bytes`).
    aax_effect_ids:
        ``(manufacturer_id, product_id, plug_id)`` triples — one per channel
        variant (mono, mono-to-stereo, stereo).  Each component is exactly
        four characters.
    param_id_map:
        Host parameter id -> canonical core parameter name.  The ids come
        from the generated registry index, never from a hand written list.
    """

    module_code: str
    display_name: str
    product_name: str
    file_description: str
    original_filename: str
    internal_name: str
    category: str
    vst3_fuid: str
    aax_effect_ids: tuple[tuple[str, str, str], ...] = ()
    param_id_map: dict[int, str] = field(default_factory=dict)

    # -- serialisation ----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-compatible dictionary form of the record."""
        return {
            "format": IDENTITY_FORMAT,
            "version": IDENTITY_VERSION,
            "module_code": self.module_code,
            "display_name": self.display_name,
            "product_name": self.product_name,
            "file_description": self.file_description,
            "original_filename": self.original_filename,
            "internal_name": self.internal_name,
            "category": self.category,
            "vst3_fuid": self.vst3_fuid,
            "aax_effect_ids": [list(triple) for triple in self.aax_effect_ids],
            "param_id_map": {str(int(k)): v for k, v in sorted(self.param_id_map.items())},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PluginIdentity":
        """Build a record from its dictionary form, validating the shape."""
        if not isinstance(data, dict):
            raise IdentityError("identity record must be a JSON object")

        def _req(key: str) -> Any:
            if key not in data:
                raise IdentityError(f"identity record is missing {key!r}")
            return data[key]

        fmt = data.get("format", IDENTITY_FORMAT)
        if fmt != IDENTITY_FORMAT:
            raise IdentityError(f"unexpected identity format {fmt!r}")
        version = int(data.get("version", IDENTITY_VERSION))
        if version != IDENTITY_VERSION:
            raise IdentityError(f"unsupported identity version {version}")

        raw_ids = data.get("aax_effect_ids", [])
        triples: list[tuple[str, str, str]] = []
        for item in raw_ids:
            if not isinstance(item, (list, tuple)) or len(item) != 3:
                raise IdentityError(f"aax effect id must be a 3-tuple, got {item!r}")
            triples.append((str(item[0]), str(item[1]), str(item[2])))

        raw_map = data.get("param_id_map", {})
        if not isinstance(raw_map, dict):
            raise IdentityError("param_id_map must be an object")
        param_map: dict[int, str] = {}
        for key, value in raw_map.items():
            try:
                pid = int(key)
            except (TypeError, ValueError):
                raise IdentityError(f"param id {key!r} is not an integer") from None
            param_map[pid] = str(value)

        return cls(
            module_code=str(_req("module_code")),
            display_name=str(_req("display_name")),
            product_name=str(_req("product_name")),
            file_description=str(_req("file_description")),
            original_filename=str(_req("original_filename")),
            internal_name=str(_req("internal_name")),
            category=str(_req("category")),
            vst3_fuid=str(_req("vst3_fuid")).lower(),
            aax_effect_ids=tuple(triples),
            param_id_map=param_map,
        )

    def to_json(self, *, indent: int | None = None) -> str:
        """Return the record as a JSON string."""
        return json.dumps(
            self.to_dict(),
            indent=indent,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":") if indent is None else None,
        )

    @classmethod
    def from_json(cls, text: str) -> "PluginIdentity":
        """Parse a record from a JSON string."""
        return cls.from_dict(json.loads(text))

    # -- convenience ------------------------------------------------------
    @property
    def fuid_bytes(self) -> bytes:
        """The sixteen raw bytes of the VST3 FUID."""
        return fuid_bytes(self.vst3_fuid)

    @property
    def fuid_hex(self) -> str:
        """The VST3 FUID as thirty-two hex characters without dashes."""
        return fuid_hex(self.vst3_fuid)

    @property
    def class_id_bytes(self) -> bytes:
        """The sixteen class id bytes a binary reports on this platform."""
        return host_class_id_bytes(self.vst3_fuid)

    def class_id_bytes_for(self, com_compatible: bool) -> bytes:
        """The sixteen class id bytes a binary reports on a platform."""
        return class_id_bytes(self.vst3_fuid, com_compatible=com_compatible)

    @property
    def param_ids(self) -> list[int]:
        """The host parameter ids of this module, in ascending order."""
        return sorted(self.param_id_map)

    def param_name(self, param_id: int) -> str | None:
        """Return the core parameter name for a host parameter id."""
        return self.param_id_map.get(int(param_id))


# --------------------------------------------------------------------------
# FUIDs
# --------------------------------------------------------------------------


def derive_fuid(product_code: str) -> uuid.UUID:
    """Derive the VST3 class id (FUID) of a module's product code.

    The plug-in wrapper builds the VST3 class id from four compile-time
    values::

        INLINE_UID (0x0101ABAB, 0xABCDEF01,
                   JucePlugin_ManufacturerCode, JucePlugin_PluginCode)

    The manufacturer and plugin codes are the four ASCII characters of
    our own identifiers.  What the wrapper produces is a **class id
    string** — thirty-two hexadecimal digits — and the sixteen bytes a
    binary reports are that string's *platform encoding*:

    * everywhere else the four words are laid out big endian;
    * on Windows the layout is COM compatible: the first word little
      endian, the second one with its bytes swapped in pairs, the last two
      big endian.

    Both layouts decode to the **same string** on their own platform,
    which is the whole point of the convention: the class id a host
    reads is the same identifier everywhere, while the bytes on the wire
    follow the platform.

    The record therefore carries the string, and every binary has to
    report the bytes of *its* platform — see :func:`class_id_bytes` and
    :func:`class_id_string`.  Reading the record's hex digits as the
    class id's bytes is right on every platform except Windows, where it
    produces a class id the host reads back as a different identifier.

    A shipping product must register these class ids with the plug-in
    format body; the values produced here are locally generated and must
    never be re-used for a different module.
    """
    code = re.sub(r"[^A-Za-z0-9]", "", str(product_code)).upper()
    code = (code + "0000")[:4]

    raw = bytearray()
    for word in VST3_CLASS_ID_PREFIX:
        raw += int(word).to_bytes(4, "big")
    raw += PLUGIN_MANUFACTURER_ID.encode("ascii")
    raw += code.encode("ascii")

    return uuid.UUID(bytes=bytes(raw))


def fuid_bytes(fuid: str) -> bytes:
    """Return the sixteen raw bytes of a canonical FUID string."""
    return uuid.UUID(fuid).bytes


def fuid_hex(fuid: str) -> str:
    """Return a FUID as thirty-two hex characters without dashes."""
    return uuid.UUID(fuid).hex


def class_id_text(fuid: str) -> str:
    """Return the canonical text of a class id.

    Thirty-two hexadecimal digits, lower case, without dashes: the form
    two class id strings can be compared in.
    """
    return re.sub(r"[^0-9a-fA-F]", "", str(fuid)).lower()


# --------------------------------------------------------------------------
# the class id bytes
# --------------------------------------------------------------------------
#
# A class id is a GUID string; the sixteen bytes a binary reports are that
# string's platform encoding.  The two functions below are the two halves
# of the conversion the plug-in format's SDK performs, and they are
# deliberately written the way the SDK writes them, so that the two can be
# compared with each other:
#
# * :func:`class_id_bytes` is the SDK's ``INLINE_UID`` layout;
# * :func:`class_id_string` is the SDK's ``FUID::toString``.
#
# They are inverses of each other on the same platform, which is the
# property the family depends on: what a binary reports has to read back
# as the string the record carries.


def host_is_com_compatible() -> bool:
    """True when the host on this platform reads a class id COM compatible.

    The plug-in format's SDK keeps the COM compatible layout on Windows
    and the plain big endian layout everywhere else.  The layout is a
    property of the *platform*, not of the plug-in: the same class id
    string is the same identifier on both.
    """
    return os.name == "nt"


def _class_id_words(fuid: str) -> tuple[int, int, int, int]:
    """Return the four 32 bit words of a class id string."""
    text = re.sub(r"[^0-9a-fA-F]", "", str(fuid))
    if len(text) != 32:
        raise IdentityError(
            f"a class id is thirty-two hexadecimal digits, got {len(text)!r}"
        )
    return tuple(int(text[index * 8 : (index + 1) * 8], 16) for index in range(4))


def class_id_bytes(fuid: str, *, com_compatible: bool | None = None) -> bytes:
    """Return the sixteen bytes a binary reports for a class id.

    Parameters
    ----------
    fuid:
        The class id string, with or without dashes.
    com_compatible:
        The layout of the platform the binary runs on.  ``None`` (the
        default) means the platform this process runs on.

    Returns
    -------
    bytes
        The sixteen bytes, in the order the platform's host reads them.
    """
    if com_compatible is None:
        com_compatible = host_is_com_compatible()

    word1, word2, word3, word4 = _class_id_words(fuid)

    if not com_compatible:
        return b"".join(
            word.to_bytes(4, "big") for word in (word1, word2, word3, word4)
        )

    # COM compatible: the first word little endian, the second one with
    # its bytes swapped in pairs, the last two big endian.
    out = bytearray()
    out += word1.to_bytes(4, "little")
    out += bytes(
        [
            (word2 >> 16) & 0xFF,
            (word2 >> 24) & 0xFF,
            word2 & 0xFF,
            (word2 >> 8) & 0xFF,
        ]
    )
    out += word3.to_bytes(4, "big")
    out += word4.to_bytes(4, "big")
    return bytes(out)


def class_id_string(raw: bytes, *, com_compatible: bool | None = None) -> str:
    """Return the class id a host reads from sixteen bytes.

    This is the inverse of :func:`class_id_bytes` on the same platform:
    the bytes a binary reports have to read back as the string the record
    carries, on every platform.

    Parameters
    ----------
    raw:
        The sixteen bytes the binary reports.
    com_compatible:
        The layout of the platform the host runs on.  ``None`` (the
        default) means the platform this process runs on.
    """
    data = bytes(raw)
    if len(data) != CLASS_ID_LENGTH:
        raise IdentityError(
            f"a class id is {CLASS_ID_LENGTH} bytes, got {len(data)}"
        )

    if not com_compatible:
        return data.hex().upper()

    # COM compatible: the string is the GUID the byte order describes.
    word1 = int.from_bytes(data[0:4], "little")
    word2 = int.from_bytes(data[4:6], "little")
    word3 = int.from_bytes(data[6:8], "little")
    return f"{word1:08X}{word2:04X}{word3:04X}{data[8:16].hex().upper()}"


def host_class_id_bytes(fuid: str) -> bytes:
    """Return the class id bytes a binary reports on this platform."""
    return class_id_bytes(fuid, com_compatible=host_is_com_compatible())


# --------------------------------------------------------------------------
# the generated registry index
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ParamIndex:
    """The global parameter index used to number host parameters.

    The index is a flat, ordered list of entries; every entry carries the
    *host parameter id*, the module code that owns the parameter and the
    canonical parameter name.  The id of a parameter is its position inside
    its own module, so the ids of a module never depend on any other module
    and adding or removing a module leaves every existing id where it was.
    The ids come from the generated registry (``aurcore/schema/generated.py``
    and the matching ``aurcore/schema/aur_modules.h``), so the C++ adapter,
    the identity record and the AAX page table all agree on the numbers.
    """

    entries: tuple[tuple[int, str, str], ...]
    source: str

    def __len__(self) -> int:
        return len(self.entries)

    @property
    def names(self) -> tuple[str, ...]:
        """The parameter names, in the order of the index.

        The index is sorted by host parameter id and then by module code;
        the ids of a module are the positions of its parameters inside it.
        """
        return tuple(name for _, _, name in self.entries)

    @property
    def codes(self) -> tuple[str, ...]:
        """The module codes, in the order of the index."""
        return tuple(code for _, code, _ in self.entries)

    def ids_for(self, module_code: str) -> list[int]:
        """Return the host parameter ids belonging to ``module_code``."""
        return [pid for pid, code, _ in self.entries if code == module_code]

    def names_for(self, module_code: str) -> dict[int, str]:
        """Return the ``{host id: parameter name}`` map for ``module_code``."""
        return {pid: name for pid, code, name in self.entries if code == module_code}

    def name_for(self, module_code: str, param_id: int) -> str | None:
        """Return the parameter name of ``param_id`` inside ``module_code``."""
        for pid, code, name in self.entries:
            if pid == param_id and code == module_code:
                return name
        return None


@dataclass(frozen=True)
class ParamRecord:
    """One module parameter together with its host id and its metadata."""

    id: int
    name: str
    module_code: str
    display_name: str
    type: str
    default: Any
    minimum: float
    maximum: float
    unit: str
    group: str
    choices: tuple[str, ...] = ()
    automatable: bool = True
    stepped: bool = False
    curve: str = "linear"
    help: str = ""
    #: The exponent of the taper law.  It only means something when the curve
    #: is a taper.
    curve_exponent: float = 1.0
    #: The names the parameter had before it was renamed.  A host that stored
    #: one of them still finds the parameter.
    aliases: tuple[str, ...] = ()


_INDEX_CACHE: dict[str, ParamIndex] = {}


def _entries_from_mapping(mapping: Any) -> list[tuple[int, str, str]] | None:
    """Normalise a ``{module_code: {name: id}}`` style mapping.

    The generator owns ``aurcore/schema/generated.py``; this helper accepts
    the shapes that mapping can reasonably take so the adapter layer keeps
    working if the generator is reworked.
    """
    if not isinstance(mapping, dict) or not mapping:
        return None
    entries: list[tuple[int, str, str]] = []
    for code, inner in mapping.items():
        if not isinstance(inner, dict):
            return None
        for name, pid in inner.items():
            try:
                entries.append((int(pid), str(code), str(name)))
            except (TypeError, ValueError):
                return None
    if not entries:
        return None
    entries.sort(key=lambda e: (e[0], e[1], e[2]))
    return entries


def _entries_from_generated(generated: Any) -> tuple[list[tuple[int, str, str]], str] | None:
    """Build the flat index from the generated registry module.

    ``PARAM_ID`` is the authoritative table: it maps ``module code`` to
    ``{parameter name: id}``, where the id is the parameter's position
    inside its own module.  ``PARAM_INDEX`` is the same table under the name
    it had before the ids became module local.  Both spellings are accepted.
    """
    param_id = getattr(generated, "PARAM_ID", None)
    if isinstance(param_id, dict):
        entries = _entries_from_mapping(param_id)
        if entries is not None:
            return entries, "aurcore/schema/generated.py (PARAM_ID)"

    param_index_obj = getattr(generated, "PARAM_INDEX", None)
    if isinstance(param_index_obj, dict):
        entries = _entries_from_mapping(param_index_obj)
        if entries is not None:
            return entries, "aurcore/schema/generated.py (PARAM_INDEX)"

    return None


def _index_from_tsv() -> tuple[list[tuple[int, str, str]], str]:
    """Derive the index from the canonical parameter table.

    This is the bootstrap path used before the registry generator has run.
    The ids follow the same rule the generator uses: the id of a parameter
    is its position inside its own module, so the ids of a module never
    depend on any other module.
    """
    from aurcore import schema as _schema

    entries: list[tuple[int, str, str]] = []
    for spec in _schema.load().values():
        for index, param in enumerate(spec.params):
            entries.append((index, spec.code, param.name))
    return entries, "aurcore/schema/params.tsv"


def param_index(*, refresh: bool = False) -> ParamIndex:
    """Return the global parameter index.

    The generated registry index (``aurcore.schema.generated``) is
    authoritative.  If it is not present yet the index is derived from the
    canonical parameter table so that the adapter layer stays testable
    before code generation has run.
    """
    if not refresh and "index" in _INDEX_CACHE:
        return _INDEX_CACHE["index"]

    entries: list[tuple[int, str, str]] = []
    source = "aurcore/schema/params.tsv"

    try:
        from aurcore.schema import generated  # type: ignore
    except Exception:
        generated = None  # type: ignore[assignment]

    if generated is not None:
        from_generated = _entries_from_generated(generated)
        if from_generated is not None:
            entries, source = from_generated

    if not entries:
        entries, source = _index_from_tsv()

    index = ParamIndex(entries=tuple(entries), source=source)
    _INDEX_CACHE["index"] = index
    return index


def module_param_ids(module_code: str) -> dict[int, str]:
    """Return the ``{host id: parameter name}`` map for one module."""
    return param_index().names_for(module_code)


def module_param_records(module_code: str) -> list[ParamRecord]:
    """Return the full parameter records of one module, in id order.

    The ids come from the generated registry index; the metadata comes from
    the canonical parameter table.
    """
    from aurcore import schema as _schema

    specs = _schema.load()
    try:
        spec = specs[module_code]
    except KeyError:
        raise KeyError(f"unknown module code {module_code!r}") from None

    index = param_index()
    by_name = {name: pid for pid, code, name in index.entries if code == module_code}

    records: list[ParamRecord] = []
    for param in spec.params:
        pid = by_name.get(param.name)
        if pid is None:
            # The parameter is not part of the generated index (should not
            # happen); give it a deterministic id after the last known one.
            pid = len(index) + len(records)
        records.append(
            ParamRecord(
                id=pid,
                name=param.name,
                module_code=spec.code,
                display_name=spec.display_name,
                type=param.type,
                default=param.default,
                minimum=float(param.minimum),
                maximum=float(param.maximum),
                unit=param.unit,
                group=param.group,
                choices=tuple(param.choices),
                automatable=bool(param.automatable),
                stepped=bool(param.stepped),
                curve=param.curve,
                help=param.help,
                curve_exponent=float(param.curve_exponent),
                aliases=tuple(param.aliases),
            )
        )
    records.sort(key=lambda r: r.id)
    return records


# --------------------------------------------------------------------------
# AAX identifiers
# --------------------------------------------------------------------------


def _base_product_code(module_code: str) -> str:
    """Return the readable four character AAX product code of a module."""
    suffix = module_code
    if suffix.startswith("AUR_"):
        suffix = suffix[4:]
    cleaned = re.sub(r"[^A-Za-z0-9]", "", suffix).upper()
    cleaned = (cleaned + "0000")[:4]
    return cleaned


def _product_codes(module_codes: Sequence[str]) -> dict[str, str]:
    """Assign a unique four character product code to every module.

    The assignment is computed over the *whole* registry, so a module keeps
    the same product code no matter which subset is asked for.

    Two passes:

    1. every module takes its natural code (the first four characters of
       its code suffix);
    2. when several modules want the same natural code, the first one in
       canonical registry order keeps it and the others are re-assigned a
       code built from their own hash, chosen so that it neither collides
       with another re-assigned module nor steals the *natural* code of
       some other module.

    The product code is shared by every format: it is the VST3 plugin code
    (and therefore part of the VST3 class id) and the AAX product id.
    """
    ordered = list(module_codes)
    natural = {code: _base_product_code(code) for code in ordered}
    reserved = {value for value in natural.values()}

    groups: dict[str, list[str]] = {}
    for code in ordered:
        groups.setdefault(natural[code], []).append(code)

    assigned: dict[str, str] = {}
    for base, members in groups.items():
        if len(members) == 1:
            assigned[members[0]] = base
            continue
        taken: set[str] = {base}
        # The first module in registry order keeps the natural code.
        keeper = members[0]
        assigned[keeper] = base
        for code in members[1:]:
            digest = hashlib.sha256(code.encode("utf-8")).digest()
            candidate = ""
            for byte in digest:
                candidate = base[:3] + _BASE36[byte % 36]
                if candidate not in taken and candidate not in reserved:
                    break
            else:
                # No free single-character suffix: widen the search.
                for byte in digest:
                    for second in digest:
                        candidate = base[:2] + _BASE36[byte % 36] + _BASE36[second % 36]
                        if candidate not in taken and candidate not in reserved:
                            break
                    else:
                        continue
                    break
            assigned[code] = candidate
            taken.add(candidate)

    return assigned


def _aax_effect_ids(module_code: str, product_code: str) -> tuple[tuple[str, str, str], ...]:
    """Return the three AAX effect ids of a module (mono / M2S / stereo)."""
    return tuple(
        (AAX_MANUFACTURER_ID, product_code, plug_id) for plug_id in AAX_VARIANT_PLUG_IDS
    )


# --------------------------------------------------------------------------
# identity derivation
# --------------------------------------------------------------------------


def _internal_name(display_name: str) -> str:
    """Return a stable, host-safe internal name for a module."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", display_name).strip("_")
    return f"Auralis_{cleaned}"


def identities_for(codes: Iterable[str] | None = None) -> list[PluginIdentity]:
    """Derive the identity of every module in the registry.

    Parameters
    ----------
    codes:
        The module codes to derive identities for.  ``None`` (the default)
        means *every* module in ``aurcore.schema.load()``.  Unknown codes
        raise :class:`KeyError`.

    Returns
    -------
    list[PluginIdentity]
        One record per module, in registry order.
    """
    from aurcore import schema as _schema

    specs = _schema.load()
    registry_order = list(specs.keys())

    if codes is None:
        wanted = registry_order
    else:
        wanted = [str(c) for c in codes]
        unknown = [c for c in wanted if c not in specs]
        if unknown:
            raise KeyError(f"unknown module code(s): {', '.join(unknown)}")
        # Keep the registry order so the result does not depend on the order
        # in which the caller happened to list the modules.
        wanted_set = set(wanted)
        wanted = [c for c in registry_order if c in wanted_set]

    product_codes = _product_codes(registry_order)

    identities: list[PluginIdentity] = []
    for code in wanted:
        spec = specs[code]
        product_code = product_codes[code]
        fuid = derive_fuid(product_code)
        identities.append(
            PluginIdentity(
                module_code=spec.code,
                display_name=spec.display_name,
                product_name=f"Auralis {spec.display_name}",
                file_description=f"Auralis {spec.display_name} - Audio Restoration",
                original_filename=f"Auralis {spec.display_name}.vst3",
                internal_name=_internal_name(spec.display_name),
                category=spec.category,
                vst3_fuid=str(fuid),
                aax_effect_ids=_aax_effect_ids(code, product_code),
                param_id_map=module_param_ids(code),
            )
        )
    return identities


def identity_for(code: str) -> PluginIdentity:
    """Derive the identity of a single module."""
    return identities_for([code])[0]


# --------------------------------------------------------------------------
# on-disk records
# --------------------------------------------------------------------------


def identity_bytes(identity: PluginIdentity) -> bytes:
    """Return the compact JSON encoding of a record (no padding)."""
    return identity.to_json().encode("utf-8")


def write_identity(identity: PluginIdentity, path: str | os.PathLike) -> Path:
    """Write an identity record to a JSON file.

    The file is the human readable twin of the record embedded in a binary:
    the adapter reads the embedded copy, the build system writes both.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(identity.to_json(indent=2) + "\n", encoding="utf-8")
    return target


def read_identity(path: str | os.PathLike) -> PluginIdentity:
    """Read an identity record from disk.

    Accepts either a JSON identity file or a stamped binary (in which case
    the embedded record is extracted).
    """
    source = Path(path)
    raw = source.read_bytes()
    if raw.lstrip()[:1] == b"{":
        return PluginIdentity.from_dict(json.loads(raw.decode("utf-8")))
    return _identity_from_region(raw, str(source))


def load_schema() -> dict[str, Any]:
    """Load the JSON Schema that describes an identity record."""
    with open(_SCHEMA_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------
# stamping
# --------------------------------------------------------------------------


def _find_slot(data: bytes, *, allow_multiple: bool = False) -> int:
    """Return the offset of the identity slot inside ``data``."""
    first = data.find(IDENTITY_MAGIC)
    if first < 0:
        raise IdentityStampError(
            f"no identity slot found: the binary does not contain the marker {IDENTITY_MAGIC!r}"
        )
    if not allow_multiple:
        second = data.find(IDENTITY_MAGIC, first + 1)
        if second >= 0:
            raise IdentityStampError(
                "the identity marker occurs more than once in the binary; "
                "the stamping target is ambiguous"
            )
    room = len(data) - (first + len(IDENTITY_MAGIC))
    if room < IDENTITY_REGION_SIZE:
        raise IdentityStampError(
            f"identity region is truncated: {room} bytes available, "
            f"{IDENTITY_REGION_SIZE} required"
        )
    return first


def make_template(path: str | os.PathLike, *, filler: int = 4096) -> Path:
    """Write a stub template binary that carries an empty identity slot.

    The real template is the compiled adapter binary; the slot is reserved
    by the C++ side (see ``plugins/vst3/AuralisPlugin.cpp``).  This helper
    produces a byte-identical *shape* so that the stamping path can be
    exercised and tested without a compiler.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = bytes((i * 37 + 11) % 256 for i in range(filler))
    target.write_bytes(IDENTITY_MAGIC + bytes(IDENTITY_REGION_SIZE) + body)
    return target


def _replace_atomically(target: Path, data: bytes) -> None:
    """Write the data to a temporary file and move it over the target."""
    handle = tempfile.NamedTemporaryFile(
        dir=str(target.parent),
        prefix=target.name + ".",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(data)
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _write_bytes(target: Path, data: bytes, *, attempts: int = 20, delay: float = 0.1) -> None:
    """Write a file, replacing it atomically.

    The file is written next to the target and then moved into place, so a
    reader never sees a half written binary.  Windows holds a lock on a
    file for a moment after it was written — a scanner, an indexer or the
    host that has the plug-in loaded — so a locked file is retried instead
    of failing the run.  When the lock does not let the file be replaced,
    the file is written in place, which asks for less.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    problem: Exception | None = None

    for _ in range(attempts):
        try:
            _replace_atomically(target, data)
            return
        except PermissionError as exc:
            problem = exc
            time.sleep(delay)

    for _ in range(attempts):
        try:
            with open(target, "wb") as handle:
                handle.write(data)
            return
        except PermissionError as exc:
            problem = exc
            time.sleep(delay)

    raise IdentityStampError(
        f"{target}: the file could not be written ({problem}); it is locked by "
        f"another process"
    )


def stamp_identity(
    template_path: str | os.PathLike,
    output_path: str | os.PathLike,
    identity: PluginIdentity,
) -> Path:
    """Stamp an identity record into a copy of a template binary.

    The slot is the ASCII marker ``AURIDENTITYv1`` followed by a fixed
    region.  The marker is left untouched; the region is overwritten with
    the UTF-8 JSON record padded with NUL bytes.

    The stamping is **idempotent**: stamping a binary that already carries
    the record writes exactly the same bytes, so the file does not change.

    Returns
    -------
    pathlib.Path
        The path of the written output binary.
    """
    problems = validate_identity(identity)
    if problems:
        raise IdentityError("identity record is invalid:\n  " + "\n  ".join(problems))

    template = Path(template_path)
    data = bytearray(template.read_bytes())
    offset = _find_slot(bytes(data))

    payload = identity_bytes(identity)
    if len(payload) > IDENTITY_REGION_SIZE:
        raise IdentityStampError(
            f"identity record is {len(payload)} bytes, the region holds {IDENTITY_REGION_SIZE}"
        )

    region = bytearray(IDENTITY_REGION_SIZE)
    region[: len(payload)] = payload
    start = offset + len(IDENTITY_MAGIC)
    data[start : start + IDENTITY_REGION_SIZE] = region

    target = Path(output_path)
    _write_bytes(target, bytes(data))
    return target


def _identity_from_region(data: bytes, source: str) -> PluginIdentity:
    """Extract and parse the identity record embedded in ``data``."""
    offset = _find_slot(data)
    start = offset + len(IDENTITY_MAGIC)
    region = data[start : start + IDENTITY_REGION_SIZE]
    end = region.find(b"\x00")
    payload = region if end < 0 else region[:end]
    if not payload.strip():
        raise IdentityStampError(f"{source}: the identity slot is empty (template not stamped?)")
    try:
        record = json.loads(payload.decode("utf-8"))
    except Exception as exc:
        raise IdentityStampError(f"{source}: embedded identity is not valid JSON: {exc}") from exc
    return PluginIdentity.from_dict(record)


def verify_identity(
    binary_path: str | os.PathLike,
    *,
    expect_module_code: str | None = None,
) -> PluginIdentity:
    """Read the identity record back out of a stamped binary.

    The record is validated against ``plugins/identity.schema.json`` before
    it is returned, so a successful return means "this binary really does
    carry the identity it claims".

    Parameters
    ----------
    binary_path:
        The stamped binary.
    expect_module_code:
        The module code the *file* claims to implement.  When it is given,
        a record that names another module is rejected: a binary called
        ``Auralis De-Hum.vst3`` that carries De-Click's record is wrong,
        and a reader has to be able to say so.
    """
    path = Path(binary_path)
    identity = _identity_from_region(path.read_bytes(), str(path))
    problems = validate_identity(identity)
    if expect_module_code is not None and identity.module_code != expect_module_code:
        problems.append(
            f"the record names module {identity.module_code!r}, but the file "
            f"claims module {expect_module_code!r}"
        )
    if problems:
        raise IdentityStampError(
            f"{path}: embedded identity does not match the schema:\n  " + "\n  ".join(problems)
        )
    return identity


# --------------------------------------------------------------------------
# schema validation
# --------------------------------------------------------------------------


def validate_identity(
    identity: PluginIdentity | dict[str, Any],
    schema: dict[str, Any] | None = None,
) -> list[str]:
    """Validate a record against the identity JSON Schema.

    Uses ``jsonschema`` when it happens to be installed and falls back to a
    small built-in checker otherwise, so the tooling never grows a hard
    dependency.  The returned list is empty when the record is valid.
    """
    instance = identity.to_dict() if isinstance(identity, PluginIdentity) else dict(identity)
    schema = schema if schema is not None else load_schema()

    try:
        import jsonschema  # type: ignore
    except Exception:
        return _minimal_validate(instance, schema)

    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.path))
    return [f"{_json_path(e.path) or '<root>'}: {e.message}" for e in errors]


def _json_path(parts: Iterable[Any]) -> str:
    out = []
    for part in parts:
        out.append(str(part))
    return ".".join(out)


def _resolve_ref(root: dict[str, Any], ref: str) -> dict[str, Any] | None:
    """Resolve a local ``#/$defs/...`` reference."""
    if not ref.startswith("#/"):
        return None
    node: Any = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, dict) else None


def _type_ok(value: Any, expected: Any) -> bool:
    if isinstance(expected, list):
        return any(_type_ok(value, item) for item in expected)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def _minimal_validate(
    instance: Any,
    schema: dict[str, Any],
    root: dict[str, Any] | None = None,
    path: str = "$",
) -> list[str]:
    """A small JSON Schema checker covering the subset this schema uses.

    Supported keywords: ``$ref`` (local), ``type``, ``const``, ``enum``,
    ``properties``, ``required``, ``additionalProperties``,
    ``propertyNames``, ``items``, ``minItems``, ``maxItems``,
    ``minLength``, ``maxLength``, ``pattern``, ``minimum``, ``maximum``.
    """
    if root is None:
        root = schema

    if "$ref" in schema:
        target = _resolve_ref(root, schema["$ref"])
        if target is None:
            return [f"{path}: cannot resolve $ref {schema['$ref']!r}"]
        return _minimal_validate(instance, target, root, path)

    problems: list[str] = []

    expected = schema.get("type")
    if expected is not None and not _type_ok(instance, expected):
        return [f"{path}: expected type {expected!r}, got {type(instance).__name__}"]

    if "const" in schema and instance != schema["const"]:
        problems.append(f"{path}: expected {schema['const']!r}, got {instance!r}")

    if "enum" in schema and instance not in schema["enum"]:
        problems.append(f"{path}: {instance!r} is not one of {schema['enum']!r}")

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            problems.append(f"{path}: shorter than {schema['minLength']} characters")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            problems.append(f"{path}: longer than {schema['maxLength']} characters")
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            problems.append(f"{path}: {instance!r} does not match {schema['pattern']!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            problems.append(f"{path}: {instance} is below the minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            problems.append(f"{path}: {instance} is above the maximum {schema['maximum']}")

    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                problems.append(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        for key, value in instance.items():
            if key in properties:
                problems.extend(_minimal_validate(value, properties[key], root, f"{path}.{key}"))
            else:
                extra = schema.get("additionalProperties", True)
                if extra is False:
                    problems.append(f"{path}: unexpected property {key!r}")
                elif isinstance(extra, dict):
                    problems.extend(_minimal_validate(value, extra, root, f"{path}.{key}"))
        names_schema = schema.get("propertyNames")
        if isinstance(names_schema, dict):
            for key in instance:
                problems.extend(
                    _minimal_validate(key, names_schema, root, f"{path}.<property {key!r}>")
                )

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            problems.append(f"{path}: needs at least {schema['minItems']} items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            problems.append(f"{path}: allows at most {schema['maxItems']} items")
        items = schema.get("items")
        if isinstance(items, dict):
            for index, value in enumerate(instance):
                problems.extend(_minimal_validate(value, items, root, f"{path}[{index}]"))

    return problems
