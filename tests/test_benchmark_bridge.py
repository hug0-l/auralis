"""
tests/test_benchmark_bridge.py — the gate of the numbers of the measurement bridge.

The numbers of the last measurement set of the study live in
``qa/benchmark/reference_bridge.py``.  A number that cannot be read is a
number that cannot be argued about, so this gate asserts the **properties of
the constants** and not the behaviour of any product:

* every constant carries the **case** it belongs to, the **unit** it is in and
  the **way it was measured**;
* every case is a case the product's **own harness** knows;
* the unit of a constant is the unit of its metric;
* every value is finite;
* the file names no product of anybody;
* the numbers of ours are filed under the series ``ours`` and the numbers of
  the reference under the series of its subject;
* the defect list and the numbers agree: every defect the numbers show is a
  rule of the product that the numbers break, and the list names it.

A measurement of a reference is a **target, not a contract**.  It was taken on
somebody else's build, on somebody else's machine, with somebody else's
settings, so nothing here fails on the number itself: a gate that failed on it
would be a gate held by a machine we do not control.

Nothing in this file asserts what Auralis does: the gates of the product
(``tests/test_quality_spectral.py`` and ``tests/test_quality_voice.py``)
measure the modules live.  The numbers that are measurements of our own
modules carry the series ``ours``, so that they are never mistaken for a
measurement of a reference, and the gate checks that they are filed that way.
"""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_BENCH = _ROOT / "qa" / "benchmark"
_CONSTANTS = _BENCH / "reference_bridge.py"
_DEFECTS = _ROOT / "docs" / "OPEN_DEFECTS.md"

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools import hygiene_check  # noqa: E402


def _load(name: str, path: Path):
    """Load a module by path, under a name of its own."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


constants = _load("aur_qa_benchmark_reference_bridge", _CONSTANTS)


# ---------------------------------------------------------------------------
# the defects the numbers show
#
# Every entry is one defect of our own modules: the module, the case, the
# criterion of the rule of the product that the numbers break, and the gate
# that holds the rule.  The gate marks a rule that is not met yet with a
# strict ``xfail`` marker, so a rule that is met turns the marker into a
# failure: the marker is a promise to look at the defect, not a pass.
# ---------------------------------------------------------------------------
DEFECTS: tuple[dict, ...] = (
    {
        "module": "AUR_SPECTRALDENOISE",
        "case": "voice_hiss",
        "family": "quality",
        "name": "OUR_QUALITY_SPECTRALDENOISE_VOICE_HISS_KEPT_DB",
        "value": 9.1645,
        "budget": 1.0,
        "rule": "keep_db",
        "direction": "too high",
        "gate": "tests/test_quality_spectral.py",
        "marker": "aur_spectraldenoise_voice_hiss",
    },
    {
        "module": "AUR_SPECTRALDENOISE",
        "case": "music_mixture",
        "family": "quality",
        "name": "OUR_QUALITY_SPECTRALDENOISE_MUSIC_MIXTURE_KEPT_DB",
        "value": 1.683,
        "budget": 1.0,
        "rule": "keep_db",
        "direction": "too high",
        "gate": "tests/test_quality_spectral.py",
        "marker": "aur_spectraldenoise_music_mixture",
    },
    {
        "module": "AUR_DECLICK",
        "case": "transient",
        "family": "quality",
        "name": "OUR_QUALITY_DECLICK_TRANSIENT_EVENT_REDUCTION_DB",
        "value": -2.2018,
        "budget": 20.0,
        "rule": "event_reduction_db",
        "direction": "too low",
        "gate": "tests/test_quality_spectral.py",
        "marker": "aur_declick_transient",
    },
    {
        "module": "AUR_DECLIP",
        "case": "clipped",
        "family": "quality",
        "name": "OUR_QUALITY_DECLIP_CLIPPED_KEPT_DB",
        "value": 3.2502,
        "budget": 2.5,
        "rule": "keep_db",
        "direction": "too high",
        "gate": "tests/test_quality_spectral.py",
        "marker": "",
    },
    {
        "module": "AUR_DECLIP",
        "case": "clipped",
        "family": "quality",
        "name": "OUR_QUALITY_DECLIP_CLIPPED_REFERENCE_ERROR_DB",
        "value": 5.3164,
        "budget": 6.0,
        "rule": "reference_error_db",
        "direction": "too low",
        "gate": "tests/test_quality_spectral.py",
        "marker": "",
    },
)


# ---------------------------------------------------------------------------
# the file
# ---------------------------------------------------------------------------
def test_the_constants_are_importable():
    """The file has to be a module a reader can import."""
    assert constants.BRIDGES, "there is no constant at all"
    assert constants.METRICS, "there is no metric at all"


def test_the_file_names_no_product():
    """No trademark of anybody may appear in the file.

    The gate of the repository is used to check the gate of the constants, so
    the two can never disagree about what a banned name is.
    """
    report = hygiene_check.Report(root=str(_ROOT))
    hygiene_check.check_text(
        "qa/benchmark/reference_bridge.py",
        _CONSTANTS.read_text(encoding="utf-8"),
        report,
    )
    assert not report.findings, "\n".join(f.format() for f in report.findings)


def test_the_defect_list_names_no_product():
    """The same rule holds for the list of the defects."""
    report = hygiene_check.Report(root=str(_ROOT))
    hygiene_check.check_text(
        "docs/OPEN_DEFECTS.md",
        _DEFECTS.read_text(encoding="utf-8"),
        report,
    )
    assert not report.findings, "\n".join(f.format() for f in report.findings)


# ---------------------------------------------------------------------------
# the constants
# ---------------------------------------------------------------------------
def test_every_number_carries_its_case_its_unit_and_its_method():
    """A number without those three cannot be read, and cannot be replaced."""
    for bridge in constants.BRIDGES:
        assert bridge.name, bridge
        assert bridge.case, bridge.name
        assert bridge.unit, bridge.name
        assert bridge.method, bridge.name
        assert bridge.series in constants.SERIES, bridge.name
        assert bridge.family in constants.FAMILIES, bridge.name


def test_the_unit_of_a_number_is_the_unit_of_its_metric():
    """The unit is carried twice on purpose, and the two have to agree."""
    for bridge in constants.BRIDGES:
        assert bridge.metric in constants.METRIC_BY_NAME, bridge.name
        rule = constants.METRIC_BY_NAME[bridge.metric]
        assert bridge.unit == rule.unit, bridge.name


def test_every_value_is_finite():
    """A number that is not finite cannot be compared with anything."""
    for bridge in constants.BRIDGES:
        assert math.isfinite(float(bridge.value)), bridge.name


def test_the_case_of_a_number_is_one_the_harness_knows():
    """A case the instrument does not know is a case nobody measured.

    The cases of the three families belong to three harnesses of the product:
    the ground truth library, the voice instrument and the corpus manifest.
    """
    known = _known_cases()
    for bridge in constants.BRIDGES:
        if bridge.family == "table":
            assert bridge.case == constants.EVERY_CASE, bridge.name
            continue
        assert bridge.case in constants.CASES[bridge.family], bridge.name
        assert bridge.case in known[bridge.family], (
            "%s names the case %r, which the harness of the family does not "
            "know" % (bridge.name, bridge.case)
        )


def _known_cases() -> dict[str, set[str]]:
    """The cases the three harnesses of the product know."""
    quality = _load("aur_qa_quality_ground_truth", _ROOT / "qa" / "quality" / "ground_truth.py")
    voice = _load(
        "aur_qa_qualityvoice_harness", _ROOT / "qa" / "qualityvoice" / "harness.py"
    )
    manifest = json.loads((_ROOT / "corpus" / "manifest.json").read_text(encoding="utf-8"))
    return {
        "quality": set(quality.CASE_NAMES),
        "voice": set(voice.build_cases()),
        "corpus": {str(entry["id"]) for entry in manifest},
    }


def test_a_number_of_ours_is_filed_under_its_own_series():
    """A number of ours must never be mistaken for a number of a reference."""
    for bridge in constants.BRIDGES:
        if bridge.name.startswith("OUR_"):
            assert bridge.series == "ours", bridge.name
        else:
            assert bridge.series == "stem_separation", bridge.name
    assert "ours" in constants.SERIES


def test_the_table_of_the_constants_and_the_records_agree():
    """The value in the table is the value in the record of the number."""
    for bridge in constants.BRIDGES:
        assert getattr(constants, bridge.name) == float(bridge.value), bridge.name


def test_the_series_of_the_file_are_the_series_of_the_benchmark():
    """The constants file and the benchmark have to speak of the same series."""
    benchmark = _load("aur_qa_benchmark_reference", _BENCH / "reference.py")
    assert "stem_separation" in benchmark.SERIES
    assert "stem_separation" in constants.SERIES


# ---------------------------------------------------------------------------
# the defect list
# ---------------------------------------------------------------------------
def _rule_for(module: str, case: str):
    """The rule of the product of a module on a case."""
    rules = _load("aur_qa_quality_rules", _ROOT / "qa" / "quality" / "quality_rules.py")
    return rules.rule_for(module, case)


@pytest.mark.parametrize(
    "defect", DEFECTS, ids=lambda item: "%s/%s" % (item["module"], item["case"])
)
def test_the_numbers_of_a_defect_break_the_rule_of_the_product(defect: dict):
    """The number the list quotes is the number that breaks the rule."""
    bridge = constants.BRIDGE_BY_NAME[defect["name"]]
    assert float(bridge.value) == defect["value"], defect["name"]
    rule = _rule_for(defect["module"], defect["case"])
    budget = getattr(rule, defect["rule"])
    assert budget == defect["budget"], defect["rule"]
    if defect["direction"] == "too high":
        assert defect["value"] > budget, defect["name"]
    else:
        assert defect["value"] < budget, defect["name"]


def test_the_defect_list_names_every_defect_the_numbers_show():
    """The list and the constants have to agree, defect by defect."""
    text = _DEFECTS.read_text(encoding="utf-8")
    for defect in DEFECTS:
        assert defect["module"] in text, defect["module"]
        assert defect["case"] in text, defect["case"]
        assert "%.2f" % defect["value"] in text, defect["name"]
        assert defect["gate"] in text, defect["gate"]
        if defect["marker"]:
            assert defect["marker"] in text, defect["marker"]
