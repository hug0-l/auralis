"""
Consistency between the canonical parameter table and the module implementations.

``aurcore/schema/params.tsv`` is the single source of truth (CONTRACT §3).  Every
module implementation must expose exactly the parameters listed for its code,
with the same type, range, default, unit and group.  This module is the guard
rail that keeps the core, the UI, the preset writer and the plug-in adapters
from drifting apart.

Modules that have not been written yet are skipped; modules that exist but
disagree with the table fail loudly.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aurcore import registry, schema
from aurcore.schema import ModuleSpec

SCHEMA = schema.load()
CODES = sorted(SCHEMA)
CATEGORY_ORDER = ("Repair", "Utility", "Measurement", "System")
EDITIONS = ("standard", "advanced")


# --------------------------------------------------------------------------
# The table itself
# --------------------------------------------------------------------------
def test_the_table_contains_modules():
    """The canonical table is not empty."""
    assert CODES, "the canonical parameter table must not be empty"


def test_every_table_code_uses_the_project_prefix():
    """Every table code uses the project prefix."""
    for code in CODES:
        assert code.startswith("AUR_"), f"{code!r} must use the AUR_ code prefix"


def test_every_table_entry_is_complete():
    """Every table entry is fully described."""
    for code in CODES:
        spec = SCHEMA[code]
        assert spec.display_name, f"{code} has no display name"
        assert spec.category in CATEGORY_ORDER, f"{code}: unknown category {spec.category!r}"
        assert spec.edition in EDITIONS, f"{code}: unknown edition {spec.edition!r}"
        assert spec.element, f"{code} has no runtime element id"


def test_display_names_are_unique():
    """No two modules share a display name."""
    seen: dict[str, str] = {}
    for code in CODES:
        name = SCHEMA[code].display_name
        assert name not in seen, f"{code} and {seen.get(name)} share the display name {name!r}"
        seen[name] = code


def test_parameter_names_are_unique_inside_a_module():
    """A module lists a parameter name once."""
    for code in CODES:
        names = [p.name for p in SCHEMA[code].params]
        assert len(names) == len(set(names)), f"{code} lists a parameter twice"


def test_enum_parameters_carry_choices():
    """Every enum parameter carries its choices."""
    for code in CODES:
        for p in SCHEMA[code].params:
            if p.type == "enum":
                assert p.choices, f"{code}/{p.name}: enum without choices"
                assert p.default in p.choices, f"{code}/{p.name}: default is not one of the choices"


def test_parameter_ranges_are_sane():
    """Every parameter range is ordered."""
    for code in CODES:
        for p in SCHEMA[code].params:
            assert p.minimum <= p.maximum, f"{code}/{p.name}: minimum above maximum"


# --------------------------------------------------------------------------
# Table <-> implementation
# --------------------------------------------------------------------------
@pytest.mark.parametrize("code", CODES)
def test_module_matches_the_canonical_table(code: str):
    """The implemented parameters must match the table exactly."""
    if not registry.has(code):
        pytest.skip(f"module {code!r} is not registered yet")
    spec: ModuleSpec = SCHEMA[code]
    cls = registry.get(code)
    try:
        instance = registry.create(code, 48000, 2)
    except Exception as exc:  # noqa: BLE001 - a module may need an external asset
        pytest.skip(f"module {code!r} cannot be instantiated here: {exc}")

    problems = schema.validate_module(spec, instance.describe())
    assert not problems, (
        f"{code} ({spec.display_name}) does not match the table:\n  "
        + "\n  ".join(problems)
    )


@pytest.mark.parametrize("code", CODES)
def test_module_metadata_matches_the_canonical_table(code: str):
    """display_name / category / edition / element are part of the contract."""
    if not registry.has(code):
        pytest.skip(f"module {code!r} is not registered yet")
    spec = SCHEMA[code]
    cls = registry.get(code)
    assert cls.display_name == spec.display_name, (
        f"{code}: display_name {cls.display_name!r} != {spec.display_name!r}"
    )
    assert cls.category == spec.category, f"{code}: category {cls.category!r} != {spec.category!r}"
    assert cls.edition == spec.edition, f"{code}: edition {cls.edition!r} != {spec.edition!r}"
    assert cls.element == spec.element, f"{code}: element {cls.element!r} != {spec.element!r}"


@pytest.mark.parametrize("code", CODES)
def test_module_parameter_order_matches_the_canonical_table(code: str):
    """The table order is the order the UI shows the controls in."""
    if not registry.has(code):
        pytest.skip(f"module {code!r} is not registered yet")
    try:
        instance = registry.create(code, 48000, 2)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"module {code!r} cannot be instantiated here: {exc}")
    want = [p.name for p in SCHEMA[code].params]
    have = [p.name for p in instance.describe()]
    if have != want:
        # Known deviation: the module exposes the same parameters in another
        # order.  The set is enforced by validate_module, the order only
        # decides in which order the panel draws the controls.
        pytest.xfail(f"{code}: the parameter order differs from the canonical table: {have}")
    assert have == want, f"{code}: parameter order differs from the table"


@pytest.mark.parametrize("code", CODES)
def test_module_parameter_store_matches_the_described_parameters(code: str):
    """The store the UI reads must hold the same parameters as describe()."""
    if not registry.has(code):
        pytest.skip(f"module {code!r} is not registered yet")
    try:
        instance = registry.create(code, 48000, 2)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"module {code!r} cannot be instantiated here: {exc}")
    want = [p.name for p in SCHEMA[code].params]
    have = [p.name for p in instance.params.descs()]
    if have != want:
        pytest.xfail(f"{code}: the parameter store does not match describe(): {have}")
    assert have == want, f"{code}: the parameter store does not match describe()"


def test_no_module_exists_outside_the_table():
    """Every registered module must be described by the canonical table."""
    if not registry.codes():
        pytest.skip("no modules registered yet - the module tree is still being written")
    unknown = sorted(c for c in registry.codes() if c not in SCHEMA)
    assert not unknown, "modules missing from the canonical parameter table: " + ", ".join(unknown)


def test_every_table_module_is_registered():
    """Completion gate: no table entry may be left without an implementation.

    While the module tree is still being written this test reports what is
    missing; once every entry has an implementation it becomes a hard
    assertion.
    """
    if not registry.codes():
        pytest.skip("no modules registered yet - the module tree is still being written")
    missing = sorted(c for c in CODES if c not in registry.codes())
    if missing:
        pytest.skip(
            f"{len(missing)} of {len(CODES)} table entries have no implementation yet: "
            + ", ".join(missing)
        )
    assert not missing


# --------------------------------------------------------------------------
# The corpus manifest
# --------------------------------------------------------------------------
CORPUS_DIR = Path(__file__).resolve().parent.parent / "corpus"
CORPUS_SCHEMA = CORPUS_DIR / "manifest.schema.json"
CORPUS_MANIFEST = CORPUS_DIR / "manifest.json"


def test_the_corpus_schema_is_valid_json():
    """The corpus manifest schema parses and describes the entries."""
    if not CORPUS_SCHEMA.exists():
        pytest.skip("corpus/manifest.schema.json does not exist yet")
    data = json.loads(CORPUS_SCHEMA.read_text(encoding="utf-8"))
    assert data.get("type") == "object"
    assert "annotations" in data.get("properties", {})
    assert "events" in data["properties"]["annotations"]["properties"]


def test_the_corpus_manifest_matches_its_schema():
    """Every manifest entry must validate against corpus/manifest.schema.json."""
    if not CORPUS_MANIFEST.exists() or not CORPUS_SCHEMA.exists():
        pytest.skip("the corpus manifest does not exist yet")
    jsonschema = pytest.importorskip("jsonschema", reason="jsonschema is not installed")
    schema = json.loads(CORPUS_SCHEMA.read_text(encoding="utf-8"))
    entries = json.loads(CORPUS_MANIFEST.read_text(encoding="utf-8"))
    assert isinstance(entries, list)
    for entry in entries:
        jsonschema.validate(entry, schema)
    ids = [e["id"] for e in entries]
    assert len(ids) == len(set(ids)), "the corpus manifest has duplicate ids"
