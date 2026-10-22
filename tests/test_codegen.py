"""
tests/test_codegen.py — the properties of the generated registry.

The canonical parameter table (``aurcore/schema/params.tsv``) is the single
source of truth and ``tools/codegen_registry.py`` turns it into five
artefacts.  These tests pin the properties that keep a change in the table
from reaching a surface half applied:

* the committed artefacts are exactly what a fresh run of the generator
  produces, and running the generator twice changes nothing;
* the drift gate fails when an artefact is stale, and it says which artefact
  and which line;
* the parameter id is the **same number in every artefact**;
* the parameter id is **stable**: adding a module or a parameter to the
  table does not move the id of an existing parameter;
* every artefact carries the "do not edit" marker the hygiene gate wants.

The tests run against the real table, so they cannot rot: the numbers are
derived from the table, never written down here.
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import codegen_registry as cg  # noqa: E402

TSV_REL = cg.TSV_REL
ARTEFACTS: tuple[str, ...] = tuple(rel for rel, _ in cg.OUTPUTS)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _load_tool() -> object:
    """Import the generator by path, so the test does not depend on sys.path."""
    spec = importlib.util.spec_from_file_location(
        "codegen_registry_under_test", TOOLS / "codegen_registry.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _registry():
    """The registry of the committed table."""
    specs, widened = cg.load_canonical(ROOT / TSV_REL)
    assert widened == 0, f"{widened} row(s) of the table have 12 columns"
    return cg.build_registry(specs)


def _scratch_root(tmp_path: Path) -> Path:
    """A scratch copy of the parts of the tree the generator reads and writes."""
    root = tmp_path / "tree"
    (root / "aurcore" / "schema").mkdir(parents=True)
    (root / "docs").mkdir(parents=True)
    shutil.copy(ROOT / TSV_REL, root / TSV_REL)
    for rel in ARTEFACTS:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, target)
    return root


def _header_ids(text: str, reg) -> dict[tuple[str, str], int]:
    """Read the parameter ids out of the C header.

    The macro is named after the module and the id, so the parameter name is
    recovered from the registry.
    """
    out: dict[tuple[str, str], int] = {}
    pattern = re.compile(r"^#define AUR_PARAM_(AUR_[A-Z0-9_]+)_(\d+)\s+(\d+)\s*$")
    for line in text.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        code, index = match.group(1), int(match.group(2))
        name = reg.code_by_id[code][index]
        out[(code, name)] = int(match.group(3))
    return out


def _markdown_ids(text: str) -> dict[tuple[str, str], int]:
    """Read the parameter ids out of the module document.

    The document has one table per module; the first column of a parameter
    row is the id and the second is the parameter name.
    """
    out: dict[tuple[str, str], int] = {}
    code: str | None = None
    in_table = False
    for line in text.splitlines():
        heading = re.match(r"^### .*\(`(AUR_[A-Z0-9_]+)`\)\s*$", line)
        if heading:
            code = heading.group(1)
            in_table = False
            continue
        if line.startswith("## "):
            code = None
            in_table = False
            continue
        if code is None:
            continue
        if line.startswith("| Id |"):
            in_table = True
            continue
        if not in_table:
            continue
        if not line.startswith("|"):
            in_table = False
            continue
        cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", line)[1:-1]]
        if len(cells) < 2 or not cells[0].isdigit():
            continue
        name = cells[1].replace("\\|", "|").replace("\\\\", "\\")
        out[(code, name)] = int(cells[0])
    return out


def _append_parameter(text: str, code: str, row: str) -> str:
    """Return the table text with ``row`` appended at the end of ``code``'s block."""
    lines = text.split("\n")
    last = max(index for index, line in enumerate(lines) if line.startswith(f"{code}\t"))
    lines.insert(last + 1, row)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# the artefacts match a fresh run
# ---------------------------------------------------------------------------
def test_the_committed_artefacts_match_a_fresh_run() -> None:
    """Every committed artefact is byte identical to a fresh render."""
    reg = _registry()
    rendered = cg.render_all(reg)
    assert set(rendered) == set(ARTEFACTS)
    for rel, text in rendered.items():
        committed = (ROOT / rel).read_text(encoding="utf-8")
        assert committed == text, f"{rel} is stale - run: python tools/codegen_registry.py"


def test_running_the_generator_twice_changes_nothing(tmp_path: Path) -> None:
    """The generator is idempotent."""
    reg = _registry()
    first = cg.render_all(reg)
    second = cg.render_all(cg.build_registry(cg.load_canonical(ROOT / TSV_REL)[0]))
    assert first == second


def test_the_drift_gate_passes_on_the_committed_tree() -> None:
    """``--check`` is green on the tree that is committed."""
    tool = _load_tool()
    assert tool.main(["--check", "--root", str(ROOT), "--quiet"]) == 0


# ---------------------------------------------------------------------------
# the drift gate catches a stale artefact
# ---------------------------------------------------------------------------
def test_the_drift_gate_fails_and_names_the_artefact_and_the_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A stale artefact is reported with its name and the line that differs."""
    tool = _load_tool()
    root = _scratch_root(tmp_path)

    assert tool.main(["--check", "--root", str(root), "--quiet"]) == 0

    # Age one line of one artefact.
    path = root / "aurcore" / "schema" / "modules.json"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace('"id": 0,', '"id": 99,', 1), encoding="utf-8")

    capsys.readouterr()
    assert tool.main(["--check", "--root", str(root), "--quiet"]) == 1
    report = capsys.readouterr().out
    assert "aurcore/schema/modules.json" in report
    assert "line " in report
    # The line number of the difference is reported.
    assert re.search(r"line \d+:", report)


def test_the_drift_gate_reports_a_missing_artefact(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An artefact that is not there is reported as missing."""
    tool = _load_tool()
    root = _scratch_root(tmp_path)
    (root / "docs" / "MODULES.md").unlink()

    capsys.readouterr()
    assert tool.main(["--check", "--root", str(root), "--quiet"]) == 1
    report = capsys.readouterr().out
    assert "docs/MODULES.md" in report
    assert "missing" in report


def test_the_drift_gate_reports_the_ids_that_moved(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A renumbered parameter is reported by name, with both numbers."""
    tool = _load_tool()
    root = _scratch_root(tmp_path)

    # Age one id in the committed registry.
    path = root / "aurcore" / "schema" / "modules.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    module = document["AUR_DECLICK"]
    module["params"][0]["id"] = 41
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    capsys.readouterr()
    assert tool.main(["--check", "--root", str(root), "--quiet"]) == 1
    report = capsys.readouterr().out
    assert "parameter ids moved" in report
    assert "AUR_DECLICK" in report
    assert "41" in report


# ---------------------------------------------------------------------------
# the id is the same number in every artefact
# ---------------------------------------------------------------------------
def test_every_artefact_carries_the_same_parameter_ids() -> None:
    """The JSON, the Python module, the C header and the document agree."""
    reg = _registry()
    expected = cg.id_table(reg)

    document = json.loads((ROOT / "aurcore/schema/modules.json").read_text(encoding="utf-8"))
    from_json = {
        (code, param["name"]): param["id"]
        for code, module in document.items()
        if code.startswith("AUR_")
        for param in module["params"]
    }
    assert from_json == expected

    from aurcore.schema import generated  # noqa: PLC0415 - the artefact under test

    from_python = {
        (code, name): pid
        for code, inner in generated.PARAM_ID.items()
        for name, pid in inner.items()
    }
    assert from_python == expected

    from_header = _header_ids(
        (ROOT / "aurcore/schema/aur_modules.h").read_text(encoding="utf-8"), reg
    )
    assert from_header == expected

    from_markdown = _markdown_ids((ROOT / "docs/MODULES.md").read_text(encoding="utf-8"))
    assert from_markdown == expected


def test_the_ids_are_module_local() -> None:
    """The ids of a module are 0 .. n-1 and belong to that module alone."""
    reg = _registry()
    for code in reg.codes:
        ids = reg.param_id[code]
        assert sorted(ids.values()) == list(range(len(ids))), (
            f"{code}: the ids are not 0 .. n-1"
        )


def test_the_schema_declares_the_parameter_id() -> None:
    """The JSON Schema describes the id, so the JSON cannot drift from it."""
    schema = json.loads(
        (ROOT / "aurcore/schema/modules.schema.json").read_text(encoding="utf-8")
    )
    param = schema["$defs"]["param"]
    assert "id" in param["required"]
    assert param["properties"]["id"]["type"] == "integer"


def test_the_registry_json_validates_against_the_schema() -> None:
    """The committed registry satisfies the committed schema."""
    jsonschema = pytest.importorskip("jsonschema", reason="jsonschema is not installed")
    instance = json.loads((ROOT / "aurcore/schema/modules.json").read_text(encoding="utf-8"))
    schema = json.loads(
        (ROOT / "aurcore/schema/modules.schema.json").read_text(encoding="utf-8")
    )
    jsonschema.validate(instance, schema)


# ---------------------------------------------------------------------------
# the ids are stable
# ---------------------------------------------------------------------------
def test_the_id_invariants_hold() -> None:
    """The structural invariants of the id model."""
    problems = cg.check_id_invariants(_registry())
    assert not problems, "\n".join(problems)


def test_the_ids_do_not_move_when_the_registry_grows() -> None:
    """Adding a parameter or a module leaves every existing id where it was."""
    problems = cg.check_id_stability(_registry())
    assert not problems, "\n".join(problems)


def test_adding_a_parameter_to_the_table_does_not_move_an_id(tmp_path: Path) -> None:
    """The proof on a scratch copy of the table: add a parameter, compare the ids."""
    table = (ROOT / TSV_REL).read_text(encoding="utf-8")
    specs, _ = cg.load_canonical(ROOT / TSV_REL)
    before = cg.id_table(cg.build_registry(specs))

    # The first module of the table: under the old scheme, adding a parameter
    # here moved the ids of every parameter of every later module.
    code = next(iter(specs))
    row = (
        f"{code}\t{specs[code].display_name}\t{specs[code].category}\t"
        f"{specs[code].edition}\t{specs[code].element}\tCodegen Probe\tfloat\t"
        f"1.0\t0\t2\t\t\tCodegen Probe"
    )
    scratch = tmp_path / "params.tsv"
    scratch.write_text(_append_parameter(table, code, row), encoding="utf-8")

    after_specs, _ = cg.load_canonical(scratch)
    after = cg.id_table(cg.build_registry(after_specs))

    moved = {key: (before[key], after.get(key)) for key in before if before[key] != after.get(key)}
    assert not moved, f"{len(moved)} id(s) moved: {sorted(moved.items())[:5]}"
    # The new parameter got an id of its own.
    assert after[(code, "Codegen Probe")] == len(specs[code].params)


def test_adding_a_module_to_the_table_does_not_move_an_id(tmp_path: Path) -> None:
    """The proof on a scratch copy of the table: add a module, compare the ids."""
    table = (ROOT / TSV_REL).read_text(encoding="utf-8")
    specs, _ = cg.load_canonical(ROOT / TSV_REL)
    before = cg.id_table(cg.build_registry(specs))

    scratch = tmp_path / "params.tsv"
    scratch.write_text(
        table.rstrip("\n")
        + "\nAUR_CODEGENPROBE\tCodegen Probe\tSystem\tstandard\tCodegenProbe\t"
        "Probe\tfloat\t1.0\t0\t2\t\t\tCodegen\n",
        encoding="utf-8",
    )

    after_specs, _ = cg.load_canonical(scratch)
    after = cg.id_table(cg.build_registry(after_specs))

    moved = {key: (before[key], after.get(key)) for key in before if before[key] != after.get(key)}
    assert not moved, f"{len(moved)} id(s) moved: {sorted(moved.items())[:5]}"
    assert after[("AUR_CODEGENPROBE", "Probe")] == 0


def test_the_property_checks_pass() -> None:
    """``--properties`` is green on the committed table."""
    tool = _load_tool()
    assert tool.main(["--properties", "--root", str(ROOT), "--quiet"]) == 0


# ---------------------------------------------------------------------------
# the "do not edit" marker
# ---------------------------------------------------------------------------
def test_every_artefact_carries_the_do_not_edit_marker() -> None:
    """Each artefact says that it must not be edited by hand."""
    for rel in ARTEFACTS:
        head = "\n".join((ROOT / rel).read_text(encoding="utf-8").splitlines()[:25])
        assert re.search(r"(?i)do\s*not\s*(?:hand[- ]?)?edit", head), (
            f"{rel} carries no 'do not edit' marker"
        )


def test_the_json_artefacts_carry_the_marker_as_a_key() -> None:
    """JSON has no comment syntax, so the marker is a key."""
    for rel in ("aurcore/schema/modules.json", "aurcore/schema/modules.schema.json"):
        document = json.loads((ROOT / rel).read_text(encoding="utf-8"))
        if rel.endswith("modules.json"):
            assert "_generated" in document
            assert "do not edit" in document["_generated"].lower()
        else:
            # The schema describes the marker of the registry it validates.
            assert "do not edit" in document["description"].lower()
            marker = document["properties"]["_generated"]
            assert "do not edit" in marker["description"].lower()
