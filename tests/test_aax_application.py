"""
tests/test_aax_application.py — the tests of the AAX application package.

The application package is the part of the AAX plug-in that describes the
plug-in to the host: the effect identifiers, the parameter identifiers, the
page layout, the labels, the ranges, the defaults and the category.  It is
generated from the canonical parameter table (``aurcore/schema/params.tsv``)
by ``scripts/aax_tables.py`` and it needs no vendor SDK.

The tests below check the package against the canonical table and against
the plug-in layer:

    * every module of the registry has an effect id, and the ids are unique;
    * every parameter of every module appears on exactly one page;
    * the parameter ids are the ones the VST3 adapter uses — the numbers in
      the identity record's ``param_id_map``;
    * no third party name appears anywhere in the package or in the
      generator that writes it.

They also check that the generator is **idempotent** — two runs produce
byte-identical files — and that the committed package is what a fresh run
produces (the drift check).

Every banned token is assembled from fragments at run time, so this file
never contains a third party trademark itself (CONTRACT §0).
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts" / "aax_tables.py"
MARKDOWN = ROOT / "docs" / "AAX_PAGE_TABLES.md"
JSON_COPY = ROOT / "docs" / "aax_page_tables.json"
APPLICATION_DOC = ROOT / "docs" / "AAX_APPLICATION.md"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import aax_tables  # noqa: E402
from tools import hygiene_check  # noqa: E402

#: The identifier rule of the format: exactly four characters, uppercase
#: ASCII letters and digits only.
IDENTIFIER_RE = re.compile(r"\A[A-Z0-9]{4}\Z")


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def package():
    """The generated application package."""
    return aax_tables.build_package(ROOT)


@pytest.fixture(scope="module")
def canonical():
    """The canonical parameter table, with the identifiers of the registry."""
    modules = aax_tables.assign_parameter_ids(
        aax_tables.load_canonical(ROOT / aax_tables.TSV_REL)
    )
    return {module.code: module for module in modules}


@pytest.fixture(scope="module")
def rendered(package):
    """The two artefacts of the package, keyed by their relative path."""
    return aax_tables.render_all(package)


@pytest.fixture(scope="module")
def machine_copy():
    """The machine readable half of the package, parsed."""
    return json.loads(JSON_COPY.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# the package covers the registry
# ---------------------------------------------------------------------------
def test_every_module_of_the_registry_is_in_the_package(package, canonical):
    """The package holds one page table per module of the canonical table."""
    assert [m.code for m in package.modules] == list(canonical.keys())


def test_the_package_holds_no_module_that_the_registry_does_not_have(package, canonical):
    """No module is invented by the generator."""
    assert {m.code for m in package.modules} == set(canonical.keys())


def test_every_module_has_a_page_table(package):
    """Every module has at least one page."""
    for module in package.modules:
        assert module.page_count >= 1, f"{module.code} has no page"


def test_the_totals_are_the_ones_the_package_holds(package, machine_copy):
    """The published totals are the totals of the package."""
    totals = machine_copy["totals"]
    assert totals["modules"] == len(package.modules)
    assert totals["pages"] == package.page_count
    assert totals["parameters"] == package.param_count
    assert totals["switches"] == package.discrete_count
    assert totals["knobs"] == package.param_count - package.discrete_count
    assert totals["effect_ids"] == package.effect_id_count


# ---------------------------------------------------------------------------
# the effect identifiers
# ---------------------------------------------------------------------------
def test_every_module_has_three_effect_ids(package):
    """Every module carries the three channel configurations."""
    for module in package.modules:
        assert len(module.effect_ids) == 3, f"{module.code} has no three effect ids"
        plugs = [triple[2] for triple in module.effect_ids]
        assert plugs == list(aax_tables.PLUG_IDS)


def test_every_effect_id_obeys_the_identifier_rule(package):
    """Every component of every effect id is four characters of [A-Z0-9]."""
    for module in package.modules:
        for triple in module.effect_ids:
            for component in triple:
                assert IDENTIFIER_RE.match(component), (
                    f"{module.code}: {component!r} is not a valid identifier"
                )


def test_the_effect_ids_are_unique(package):
    """No two modules share a product id, and no two triples are equal."""
    products: dict[str, str] = {}
    triples: dict[tuple[str, str, str], str] = {}
    for module in package.modules:
        product = module.product_id
        assert product not in products, (
            f"{module.code} and {products.get(product)} share the product id {product!r}"
        )
        products[product] = module.code
        for triple in module.effect_ids:
            assert triple not in triples, (
                f"{module.code} and {triples.get(triple)} share the effect id {triple}"
            )
            triples[triple] = module.code


def test_the_manufacturer_id_is_the_one_the_plug_in_layer_registers(package):
    """The manufacturer id is the one the identity layer derives."""
    assert aax_tables.MANUFACTURER_ID == "AURA"
    for module in package.modules:
        for triple in module.effect_ids:
            assert triple[0] == "AURA"


def test_the_product_ids_are_the_ones_the_plug_in_layer_derives(package):
    """The product ids agree with ``plugins/identity.py``."""
    from plugins.identity import _product_codes  # noqa: PLC0415 - the owner

    codes = [module.code for module in package.modules]
    owned = _product_codes(codes)
    for module in package.modules:
        assert module.product_id == owned[module.code]


def test_the_effect_ids_are_the_ones_the_identity_record_carries(package):
    """The effect ids agree with the identity record of every module."""
    from plugins.identity import identities_for  # noqa: PLC0415 - the owner

    owned = {record.module_code: record for record in identities_for()}
    for module in package.modules:
        record = owned[module.code]
        assert tuple(record.aax_effect_ids) == module.effect_ids
        assert record.category == module.category
        assert record.display_name == module.display_name


# ---------------------------------------------------------------------------
# the parameters
# ---------------------------------------------------------------------------
def test_every_parameter_appears_on_exactly_one_page(package, canonical):
    """No parameter is missing, none is on two pages, none is invented."""
    for module in package.modules:
        spec = canonical[module.code]
        wanted = {param.identifier for param in spec.params}
        seen: list[int] = []
        for page in module.pages:
            for control in page.controls:
                seen.append(control.parameter)
        assert len(seen) == len(set(seen)), (
            f"{module.code}: a parameter appears on more than one page"
        )
        assert set(seen) == wanted, (
            f"{module.code}: the pages do not hold exactly the module's parameters"
        )


def test_the_parameter_names_on_the_pages_are_the_canonical_names(package, canonical):
    """A control carries the name the canonical table gives the parameter."""
    for module in package.modules:
        spec = canonical[module.code]
        by_id = {param.identifier: param for param in spec.params}
        for page in module.pages:
            for control in page.controls:
                assert control.name == by_id[control.parameter].name


def test_the_parameter_ids_are_the_ones_the_vst3_adapter_uses(package):
    """The ids are the ones the identity record gives the VST3 adapter.

    The VST3 adapter maps a host parameter id to a canonical name through
    the identity record's ``param_id_map``; those are the numbers the
    package has to publish, so that a session means the same thing in both
    formats.
    """
    from plugins.identity import identities_for  # noqa: PLC0415 - the owner

    owned = {record.module_code: record for record in identities_for()}
    for module in package.modules:
        record = owned[module.code]
        published = {
            control.parameter: control.name
            for page in module.pages
            for control in page.controls
        }
        assert published == dict(record.param_id_map), (
            f"{module.code}: the published parameter ids differ from the identity record"
        )


def test_the_types_ranges_and_defaults_come_from_the_canonical_table(package, canonical):
    """A control carries the type, the range, the default and the unit."""
    for module in package.modules:
        spec = canonical[module.code]
        by_id = {param.identifier: param for param in spec.params}
        for page in module.pages:
            for control in page.controls:
                param = by_id[control.parameter]
                assert control.type == param.type
                if param.type in ("float", "int"):
                    assert control.minimum == pytest.approx(param.minimum)
                    assert control.maximum == pytest.approx(param.maximum)
                    assert control.default == pytest.approx(float(param.default_text))
                if param.type == "enum":
                    assert tuple(control.choices) == param.choices
                    assert control.default == param.default_text
                if param.type == "bool":
                    assert isinstance(control.default, bool)
                assert control.unit == param.unit


def test_the_category_is_published_for_every_module(package, canonical):
    """Every module states the category the host files it under."""
    for module in package.modules:
        assert module.category
        assert module.category == canonical[module.code].category


# ---------------------------------------------------------------------------
# the pages
# ---------------------------------------------------------------------------
def test_a_page_holds_at_most_one_switch_and_three_knobs(package):
    """The page geometry is the one the application document states."""
    for module in package.modules:
        for page in module.pages:
            slots = [control.slot for control in page.controls]
            assert len(slots) == len(set(slots)), f"{module.code}: a slot is used twice"
            assert slots.count("switch") <= 1
            assert slots.count("knob1") <= 1
            assert slots.count("knob2") <= 1
            assert slots.count("knob3") <= 1
            assert len(slots) <= 4


def test_the_pages_are_numbered_from_one(package):
    """The page index starts at one and increases by one."""
    for module in package.modules:
        for number, page in enumerate(module.pages, start=1):
            assert page.index == number


def test_the_controls_of_a_page_are_written_in_slot_order(package):
    """The switch comes first, then the knobs, in slot order."""
    order = list(aax_tables.SLOT_ORDER)
    for module in package.modules:
        for page in module.pages:
            positions = [order.index(control.slot) for control in page.controls]
            assert positions == sorted(positions)


def test_a_discrete_parameter_is_a_switch_and_a_continuous_one_is_a_knob(package, canonical):
    """The control kind is a pure function of the canonical type."""
    for module in package.modules:
        spec = canonical[module.code]
        by_id = {param.identifier: param for param in spec.params}
        for page in module.pages:
            for control in page.controls:
                param = by_id[control.parameter]
                if param.type in ("enum", "bool"):
                    assert control.slot == "switch"
                else:
                    assert control.slot.startswith("knob")


# ---------------------------------------------------------------------------
# the labels
# ---------------------------------------------------------------------------
def test_every_label_fits_the_budget(package):
    """No label is longer than the budget."""
    for module in package.modules:
        for page in module.pages:
            for control in page.controls:
                assert len(control.label) <= aax_tables.LABEL_BUDGET, (
                    f"{module.code}.{control.name}: the label {control.label!r} is too long"
                )


def test_every_abbreviation_fits_the_budget():
    """The hand-maintained abbreviation table cannot overflow the budget."""
    for name, abbreviation in aax_tables.ABBREVIATIONS.items():
        assert len(abbreviation) <= aax_tables.LABEL_BUDGET, (
            f"the abbreviation of {name!r} does not fit the budget"
        )


def test_the_ladder_is_a_straight_prefix_cut(package):
    """Every ladder entry is a prefix of the label, in ascending size order."""
    for module in package.modules:
        for page in module.pages:
            for control in page.controls:
                sizes = [size for size, _text in control.ladder]
                assert sizes == sorted(sizes)
                assert len(set(sizes)) == len(sizes)
                for size, text in control.ladder:
                    assert text == control.label[:size]
                    assert 0 < size < len(control.label)


def test_the_base_label_follows_the_documented_ladder():
    """The ladder of section 4.2 of the application document."""
    assert aax_tables.base_label("Algorithm") == "Algorithm"
    assert aax_tables.base_label("Sensitivity") == "Sensitivity"
    assert aax_tables.base_label("Multi Resolution") == "Multi"
    assert aax_tables.base_label("Noise Threshold") == "Noise"
    assert aax_tables.base_label("Noise Floor Synthesis") == "Noise Floor"
    assert aax_tables.base_label("Output Noise Only") == "Output Noise"
    assert aax_tables.base_label("Short Term Loudness") == "Short Term"
    # the abbreviation step
    assert aax_tables.base_label("Output Clicks Only") == "Clicks Only"
    assert aax_tables.base_label("Center Global Pitch") == "Center Pitch"
    # the truncation step
    truncated = aax_tables.base_label("A Very Long Parameter Name Indeed")
    assert len(truncated) == aax_tables.LABEL_BUDGET
    assert truncated.endswith(aax_tables.ELLIPSIS)


def test_the_label_of_a_parameter_is_the_same_in_every_module(package, canonical):
    """A shared parameter name reads the same way everywhere."""
    by_name: dict[str, str] = {}
    for module in package.modules:
        for page in module.pages:
            for control in page.controls:
                if control.name in by_name:
                    assert by_name[control.name] == control.label, (
                        f"{control.name!r} has two labels: "
                        f"{by_name[control.name]!r} and {control.label!r}"
                    )
                else:
                    by_name[control.name] = control.label


# ---------------------------------------------------------------------------
# the generator
# ---------------------------------------------------------------------------
def _run_generator(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run the generator in a subprocess."""
    return subprocess.run(
        [sys.executable, str(GENERATOR), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd or ROOT),
        timeout=300,
    )


def test_the_generator_is_idempotent(tmp_path):
    """Two runs produce byte-identical files."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    for target in (first, second):
        result = _run_generator("--out-dir", str(target))
        assert result.returncode == 0, result.stderr
    for rel in (aax_tables.MARKDOWN_REL, aax_tables.JSON_REL):
        one = (first / rel).read_bytes()
        other = (second / rel).read_bytes()
        assert hashlib.sha256(one).hexdigest() == hashlib.sha256(other).hexdigest(), (
            f"{rel} differs between two runs of the generator"
        )


def test_the_committed_package_is_what_a_fresh_run_produces(rendered):
    """The drift check: the committed artefacts are up to date."""
    problems = aax_tables.check(ROOT, rendered)
    assert not problems, "\n".join(problems)


def test_the_drift_check_reports_a_drift(tmp_path, rendered):
    """The drift check really does fail when the tree is stale."""
    for rel, text in rendered.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "\nstale\n", encoding="utf-8", newline="\n")
    problems = aax_tables.check(tmp_path, rendered)
    assert len(problems) == len(rendered)


def test_the_check_mode_passes_on_the_committed_tree():
    """``python scripts/aax_tables.py --check`` is green."""
    result = _run_generator("--check")
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_package_carries_the_generated_header(rendered):
    """The artefacts say that they are generated and must not be edited."""
    for rel, text in rendered.items():
        header = "\n".join(text.splitlines()[:25])
        assert re.search(r"(?i)generated file", header), f"{rel} has no generated header"
        assert re.search(r"(?i)do\s*not\s*edit", header), f"{rel} has no do-not-edit header"


def test_the_machine_readable_copy_matches_the_human_one(machine_copy, package):
    """The two halves of the package hold the same data."""
    assert len(machine_copy["modules"]) == len(package.modules)
    for entry, module in zip(machine_copy["modules"], package.modules):
        assert entry["code"] == module.code
        assert entry["category"] == module.category
        assert entry["product_id"] == module.product_id
        assert entry["page_count"] == module.page_count
        assert len(entry["pages"]) == module.page_count
        for page, generated in zip(entry["pages"], module.pages):
            assert page["index"] == generated.index
            assert len(page["controls"]) == len(generated.controls)
            for control, published in zip(page["controls"], generated.controls):
                assert control["slot"] == published.slot
                assert control["parameter"] == published.parameter
                assert control["name"] == published.name
                assert control["label"] == published.label
                assert [list(step) for step in control["ladder"]] == [
                    list(step) for step in published.ladder
                ]


def _documented_page_tables() -> list[str]:
    """The XML blocks of the application document, as text."""
    text = APPLICATION_DOC.read_text(encoding="utf-8")
    return re.findall(r"```xml\n(.*?)```", text, re.S)


def test_the_worked_example_in_the_application_document_matches_the_generator(package):
    """The document's worked example is the generator's own output."""
    blocks = _documented_page_tables()
    assert blocks, "the application document holds no page table example"
    expected = aax_tables.render_xml(package, "AUR_SPECTRALDENOISE").strip()
    assert any(block.strip() == expected for block in blocks), (
        "the worked example in docs/AAX_APPLICATION.md differs from the generator"
    )


def test_the_documented_example_parses_as_a_page_table(package):
    """The worked example is a well formed page table."""
    blocks = [
        block
        for block in _documented_page_tables()
        if 'module="AUR_SPECTRALDENOISE"' in block
    ]
    assert blocks, "the application document holds no worked example"
    root = ET.fromstring(blocks[0])
    assert root.tag == "PageTable"
    assert root.get("module") == "AUR_SPECTRALDENOISE"
    assert root.get("manufacturer") == aax_tables.MANUFACTURER_ID
    assert root.get("product") == "SPEC"
    assert root.get("paramIdBase") == str(aax_tables.PARAM_ID_BASE)
    pages = root.findall("Page")
    expected_pages = next(
        module.page_count for module in package.modules
        if module.code == "AUR_SPECTRALDENOISE"
    )
    assert len(pages) == expected_pages
    for number, page in enumerate(pages, start=1):
        assert page.get("index") == str(number)
        for control in page.findall("Control"):
            assert control.get("slot") in aax_tables.SLOT_ORDER
            assert control.get("parameter") is not None


# ---------------------------------------------------------------------------
# the naming rule
# ---------------------------------------------------------------------------
def _forbidden_tokens() -> tuple[str, ...]:
    """The banned tokens, taken from the hygiene gate."""
    return tuple(hygiene_check.LONG_TOKENS) + (hygiene_check.SHORT_TOKEN,)


def _scan(text: str) -> list[str]:
    """Return every banned token that occurs in ``text``."""
    hits: list[str] = []
    for token in _forbidden_tokens():
        if re.search(r"(?<![A-Za-z0-9])" + re.escape(token) + r"(?![A-Za-z0-9])", text):
            hits.append(token)
    return hits


def test_the_documented_input_table_matches_the_canonical_table(package):
    """The input the document quotes is the canonical table."""
    text = APPLICATION_DOC.read_text(encoding="utf-8")
    match = re.search(
        r"The input \(the canonical table, abbreviated\):\n\n(\|.*?\|\n\|.*?\|\n.*?)\n\nThe output",
        text,
        re.S,
    )
    assert match, "the application document holds no input table"
    expected = aax_tables.render_input_table(
        package, aax_tables.WORKED_EXAMPLE_MODULE
    ).strip()
    assert match.group(1).strip() == expected, (
        "the input table in docs/AAX_APPLICATION.md differs from the canonical table"
    )


def test_the_sync_doc_mode_is_idempotent(tmp_path):
    """``--sync-doc`` does not change a document that is already in step."""
    root = tmp_path
    (root / "aurcore" / "schema").mkdir(parents=True)
    (root / "aurcore" / "schema" / "params.tsv").write_bytes(
        (ROOT / "aurcore" / "schema" / "params.tsv").read_bytes()
    )
    (root / "docs").mkdir(parents=True)
    doc = root / "docs" / "AAX_APPLICATION.md"
    doc.write_text(APPLICATION_DOC.read_text(encoding="utf-8"), encoding="utf-8")
    before = doc.read_text(encoding="utf-8")
    result = _run_generator("--root", str(root), "--sync-doc")
    assert result.returncode == 0, result.stdout + result.stderr
    assert doc.read_text(encoding="utf-8") == before


def test_no_third_party_name_in_the_package(rendered):
    """The generated package carries no third party product or vendor name."""
    for rel, text in rendered.items():
        assert not _scan(text), f"{rel} carries a third party name: {_scan(text)}"


def test_no_third_party_name_in_the_generator():
    """The generator carries no third party product or vendor name."""
    text = GENERATOR.read_text(encoding="utf-8")
    assert not _scan(text), f"the generator carries a third party name: {_scan(text)}"


def test_no_third_party_name_in_the_application_document():
    """The application document carries no third party product or vendor name."""
    text = APPLICATION_DOC.read_text(encoding="utf-8")
    assert not _scan(text), f"the document carries a third party name: {_scan(text)}"


def test_no_third_party_name_in_the_parameter_names():
    """No parameter name of the canonical table carries one either."""
    modules = aax_tables.load_canonical(ROOT / aax_tables.TSV_REL)
    for module in modules:
        for param in module.params:
            assert not _scan(param.name), (
                f"{module.code}.{param.name} carries a third party name"
            )
