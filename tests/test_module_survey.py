"""
tests/test_module_survey.py — the gate of the module survey.

The survey in ``qa/survey/`` measures what every module of the catalogue does
to a signal.  This file is the gate: it holds every module to the rules that
the survey found out about.

The rules
---------

1. the output is finite — no NaN, no infinity;
2. the output is as long as the input;
3. the output does not depend on what the allocator handed out;
4. two runs of the same module produce the same numbers;
5. the wanted part of the signal is not damaged by more than the budget that
   ``qa/survey/specs.py`` documents for that module;
6. the unwanted part of the signal is removed by at least the amount the
   specification asks for;
7. the module does not make the signal more than 24 dB louder;
8. the module fits into the realtime budget of a 64 sample block
   (1333.3 us at 48 kHz), or it is listed in ``qa/survey/exceptions.py`` with
   a reason;
9. the delay the module declares is the delay it puts into the signal: the
   two may not differ by more than the tolerance of the module, which is two
   samples for a module that declares a delay and one block for a module
   that declares none.

A module that breaks a rule is listed with the number that was measured, so
the gate holds it to that number instead of to the rule.  When the module is
repaired the entry is deleted and the gate holds it to the rule again.

The gate is a gate, so it has to fail when a module breaks a rule.  That is
checked by ``test_the_gate_fails_for_a_broken_module``, which runs the rules
against a module whose output is deliberately broken.

The measurement is the one of ``qa/survey/``; the report is
``qa/survey/REPORT.md`` and the machine readable result is
``qa/survey/report.json``.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

# The survey lives outside the test package, so it is put on the path.
_SURVEY_DIR = Path(__file__).resolve().parents[1] / "qa" / "survey"
if str(_SURVEY_DIR) not in sys.path:
    sys.path.insert(0, str(_SURVEY_DIR))

import exceptions as survey_exceptions  # noqa: E402
import harness  # noqa: E402
import probes  # noqa: E402
import specs  # noqa: E402
import survey  # noqa: E402

from aurcore import registry  # noqa: E402
from aurcore.modules.base import Module  # noqa: E402

try:  # pragma: no cover - importing registers the modules
    import aurcore.modules  # noqa: F401
except Exception:  # pragma: no cover - a broken module file must not kill the run
    pass


#: The realtime budget of one 64 sample block at 48 kHz, in microseconds.
BUDGET_US = harness.BUDGET_US

#: How much louder than the input the output may be, in decibels.
RUNAWAY_LIMIT_DB = survey.RUNAWAY_LIMIT_DB

#: The modules of the catalogue.
CODES = sorted(info.code for info in registry.all())

Measurements = dict


# ---------------------------------------------------------------------------
# The rules of the gate
# ---------------------------------------------------------------------------
def problems_finite(measurements: Measurements) -> list[str]:
    """Return the modules whose output holds a NaN or an infinity."""
    return [f"{code}: the output holds a non finite sample" for code, m in measurements.items()
            if not m.finite]


def problems_length(measurements: Measurements) -> list[str]:
    """Return the modules whose output is not as long as the input."""
    return [f"{code}: the output is not as long as the input" for code, m in measurements.items()
            if not m.same_length]


def problems_allocator(measurements: Measurements) -> list[str]:
    """Return the modules whose output depends on uninitialised memory."""
    return [f"{code}: the output depends on the uninitialised memory" for code, m in
            measurements.items() if not m.allocator_independent]


def problems_determinism(measurements: Measurements) -> list[str]:
    """Return the modules that are not deterministic."""
    return [f"{code}: two runs of the module differ" for code, m in measurements.items()
            if not m.deterministic]


def problems_wanted(measurements: Measurements) -> list[str]:
    """Return the modules that damage the wanted part beyond their budget."""
    broken: list[str] = []
    for code, result in measurements.items():
        for check in result.checks:
            if check["role"] != "keep" or check["ok"] or check.get("exception"):
                continue
            broken.append(
                f"{code} / {check['label']}: {check['change_db']:.2f} dB "
                f"(allowed {check['allowed'][0]})"
            )
    return broken


def problems_unwanted(measurements: Measurements) -> list[str]:
    """Return the modules that do not remove the unwanted part."""
    broken: list[str] = []
    for code, result in measurements.items():
        for check in result.checks:
            if check["role"] != "target" or check["ok"] or check.get("exception"):
                continue
            broken.append(
                f"{code} / {check['label']}: {check['change_db']:.2f} dB "
                f"(allowed {check['allowed'][1]})"
            )
    return broken


def problems_runaway(measurements: Measurements) -> list[str]:
    """Return the modules that make the signal louder than they may."""
    broken = []
    for code, result in measurements.items():
        limit = RUNAWAY_LIMIT_DB
        entry = survey_exceptions.exception_for(code, "runaway")
        if entry is not None:
            limit = max(limit, entry.limit + entry.tolerance)
        if result.runaway_db <= limit:
            continue
        broken.append(f"{code}: the output is {result.runaway_db:.0f} dB louder")
    return broken


def problems_cost(measurements: Measurements) -> list[str]:
    """Return the modules that do not fit into the realtime budget."""
    broken: list[str] = []
    for code, result in measurements.items():
        entry = survey_exceptions.exception_for(code, "cost")
        if entry is None:
            if result.cost_us > BUDGET_US:
                broken.append(
                    f"{code}: {result.cost_us:.0f} us (the budget is {BUDGET_US:.0f} us)"
                )
        elif result.cost_us > entry.limit:
            broken.append(
                f"{code}: {result.cost_us:.0f} us (the listed ceiling is "
                f"{entry.limit:.0f} us)"
            )
    return broken


def problems_latency(measurements: Measurements) -> list[str]:
    """Return the modules whose delay is not the delay they declare."""
    broken: list[str] = []
    for code, result in measurements.items():
        error = abs(int(result.latency_measured) - int(result.latency_declared))
        if error <= int(result.latency_tolerance):
            continue
        broken.append(
            f"{code}: the module delays the signal by {result.latency_measured} "
            f"samples and declares {result.latency_declared}"
        )
    return broken


# ---------------------------------------------------------------------------
# The measurement
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def measurements() -> Measurements:
    """Measure every module once and share the result between the tests."""
    return {code: survey.measure_module(code) for code in CODES}


# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------
def test_every_module_has_a_specification():
    """Every module of the catalogue is measured by the survey."""
    missing = [code for code in CODES if code not in specs.SPECS]
    assert not missing, f"no survey specification for {missing}"
    assert len(CODES) == 51, f"the catalogue holds {len(CODES)} modules, not 51"


def test_the_report_has_a_row_for_every_module():
    """The report carries one row per module of the catalogue."""
    report = Path(__file__).resolve().parents[1] / "qa" / "survey" / "REPORT.md"
    assert report.is_file(), "qa/survey/REPORT.md is missing"
    text = report.read_text(encoding="utf-8")
    missing = [code for code in CODES if f"`{code}`" not in text]
    assert not missing, f"the report has no row for {missing}"


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------
def test_the_output_is_finite(measurements):
    """No module returns a NaN or an infinity."""
    broken = problems_finite(measurements)
    assert not broken, "the output holds a non finite sample:\n  " + "\n  ".join(broken)


def test_the_output_is_as_long_as_the_input(measurements):
    """No module changes the length of the signal."""
    broken = problems_length(measurements)
    assert not broken, "the output is not as long as the input:\n  " + "\n  ".join(broken)


def test_the_output_does_not_depend_on_the_allocator(measurements):
    """No module returns memory the allocator handed out."""
    broken = problems_allocator(measurements)
    assert not broken, "the output depends on uninitialised memory:\n  " + "\n  ".join(broken)


def test_two_runs_of_a_module_are_the_same(measurements):
    """No module is random."""
    broken = problems_determinism(measurements)
    assert not broken, "two runs of a module differ:\n  " + "\n  ".join(broken)


def test_the_wanted_part_is_not_damaged(measurements):
    """The programme survives the module, within the documented budget."""
    broken = problems_wanted(measurements)
    assert not broken, "the wanted part is damaged:\n  " + "\n  ".join(broken)


def test_the_unwanted_part_is_removed(measurements):
    """The defect goes, at least as far as the specification asks for."""
    broken = problems_unwanted(measurements)
    assert not broken, "the unwanted part is not removed:\n  " + "\n  ".join(broken)


def test_the_module_does_not_make_the_signal_louder(measurements):
    """No module turns a quiet signal into a loud one."""
    broken = problems_runaway(measurements)
    assert not broken, "the output runs away:\n  " + "\n  ".join(broken)


def test_the_module_fits_into_the_realtime_budget(measurements):
    """Every module fits into the budget of a 64 sample block, or is listed."""
    broken = problems_cost(measurements)
    assert not broken, "the module is too slow:\n  " + "\n  ".join(broken)


def test_the_declared_latency_is_the_measured_latency(measurements):
    """The delay a module declares is the delay it puts into the signal."""
    broken = problems_latency(measurements)
    assert not broken, (
        "the delay of a module is not the delay it declares:\n  " + "\n  ".join(broken)
    )


def test_every_listed_defect_has_a_reason():
    """Every entry of the exception list explains itself."""
    entries = survey_exceptions.all_exceptions()
    assert entries, "the exception list is empty"
    for entry in entries:
        assert entry.reason.strip(), f"{entry.code} / {entry.kind} has no reason"
        assert entry.code in CODES, f"{entry.code} is not a module of the catalogue"


# ---------------------------------------------------------------------------
# The gate itself
# ---------------------------------------------------------------------------
class _Broken(Module):
    """A module that breaks the rules on purpose."""

    code = "AUR_BROKENPROBE"
    display_name = "Broken Probe"
    category = "Utility"

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self.mode = "nan"

    def process(self, x: np.ndarray) -> np.ndarray:
        """Return something that breaks a rule."""
        out = np.array(x, dtype=np.float64, copy=True)
        if self.mode == "nan":
            out[0, 0] = float("nan")
        elif self.mode == "short":
            return out[:-1]
        elif self.mode == "loud":
            out = out * 1.0e6
        return out


def test_the_gate_fails_for_a_broken_module():
    """The gate is a gate: a module that breaks a rule makes it fail.

    The rules are run against a module whose output is deliberately broken:
    a NaN, a changed length and a runaway all have to be caught, and a
    module that damages the wanted part has to be caught as well.
    """
    probe = probes.build_probe("tone_1k", 0.1)
    signal = np.asarray(probe.signal, dtype=np.float64)
    module = _Broken(48000, 2)

    result = survey.ModuleSurvey(code=module.code)

    module.mode = "nan"
    out = np.asarray(module.process(signal.copy()), dtype=np.float64)
    result.finite = bool(np.all(np.isfinite(out)))
    assert problems_finite({module.code: result}), "the gate does not catch a NaN"

    module.mode = "short"
    out = np.asarray(module.process(signal.copy()), dtype=np.float64)
    result.same_length = out.shape[0] == signal.shape[0]
    assert problems_length({module.code: result}), "the gate does not catch a short output"

    module.mode = "loud"
    # the ceiling rule of the family (aurcore/ceiling.py) scales a block that
    # goes over the digital ceiling back down to the ceiling, so the runaway
    # of a loud input is capped at the ceiling and the rule cannot see it.
    # The runaway of a quiet input is what the rule catches, so the broken
    # module is driven with a quiet block.
    quiet = signal * 1.0e-4
    out = np.asarray(module.process(quiet.copy()), dtype=np.float64)
    result.runaway_db = harness._peak_delta_db(quiet, out)
    assert problems_runaway({module.code: result}), "the gate does not catch a runaway"

    # a module that damages the wanted part of the signal
    result.checks = [
        {
            "role": "keep",
            "label": "programme",
            "change_db": -12.0,
            "allowed": [-1.0, None],
            "ok": False,
            "exception": None,
        }
    ]
    assert problems_wanted({module.code: result}), "the gate does not catch a damaged programme"

    # a module that is too slow
    result.cost_us = BUDGET_US * 2.0
    assert problems_cost({module.code: result}), "the gate does not catch a slow module"

    # a module that lies about its delay
    result.latency_declared = 1024
    result.latency_measured = 0
    result.latency_tolerance = 2
    assert problems_latency({module.code: result}), (
        "the gate does not catch a module that lies about its delay"
    )
