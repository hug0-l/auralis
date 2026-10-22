"""
qa.survey.survey — the runner of the module survey.

The runner measures every module of the catalogue against its specification
and writes the report.

    python qa/survey/survey.py --all            # measure and write the report
    python qa/survey/survey.py --module AUR_DECLICK
    python qa/survey/survey.py --all --json var/survey.json

The report lands in ``qa/survey/REPORT.md`` and its machine readable twin in
``qa/survey/report.json``.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

import exceptions as exc  # noqa: E402
import harness  # noqa: E402
import probes  # noqa: E402
import specs  # noqa: E402
from harness import ModuleSurvey, Region  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

REPORT_PATH = _HERE / "REPORT.md"
JSON_PATH = _HERE / "report.json"

#: The block delta below which a module counts as block coherent.
BLOCK_COHERENT_DB = -40.0

#: How much louder than the input the output may be, in decibels.  A module
#: that turns a -20 dBFS signal into a +280 dBFS one is broken, whatever it
#: does with the programme.
RUNAWAY_LIMIT_DB = 24.0

#: How far the measured delay of a module may be from the delay it declares,
#: in samples.  The delay of a module is a whole number of samples and the
#: measurement resolves it exactly — the delay line of a module has a fixed
#: length — so the number that is allowed here is only there to absorb the
#: rounding of a declaration that is written in samples.  Two samples are
#: 41.7 us at 48 kHz, and they are a thousand times smaller than the
#: 2048 sample window of the analysis of the core: a module whose
#: declaration is off by a whole analysis window is still caught.
LATENCY_TOLERANCE_SAMPLES = 2

#: How long a module that declares **no** latency may delay the signal, in
#: samples.  The delay of a module that filters and does not delay is the
#: group delay of the filter, and the group delay of a crossover is a few
#: samples; one block of 64 samples (1.33 ms at 48 kHz) is the resolution
#: a host compensates a delay with, so a module that is later than that
#: without saying so is caught.
LATENCY_TOLERANCE_UNDECLARED_SAMPLES = 64

#: The module every finding file in ``var/patches/b12/`` is about.  The name
#: of the file is the name of the module with the underscores taken out, so
#: the mapping is written down here once.
FINDING_MODULES: dict[str, str] = {
    "ambience_match": "AUR_AMBIENCEMATCH",
    "deconstruct": "AUR_DECONSTRUCT",
    "dialogue_isolate": "AUR_DIALOGUEISOLATE",
    "eq_match": "AUR_EQMATCH",
    "guitar_de_noise": "AUR_GUITARDENOISE",
    "music_rebalance": "AUR_MUSICREBALANCE",
    "spectral_de_noise": "AUR_SPECTRALDENOISE",
}

__all__ = [
    "CheckResult",
    "align_output",
    "latency_tolerance",
    "measure_delay",
    "measure_module",
    "measure_all",
    "prepare_module",
    "render_report",
    "main",
]


# ---------------------------------------------------------------------------
# preparing a module
# ---------------------------------------------------------------------------
def prepare_module(code: str, spec: specs.ModuleSpec | None, probe: probes.Probe | None):
    """Create a module and put it into the state the specification asks for.

    Parameters
    ----------
    code:
        The module code.
    spec:
        The specification of the module; ``None`` for a module that has no
        specification yet.
    probe:
        The probe the module is measured with.

    Returns
    -------
    object
        The prepared module.
    """
    from aurcore import registry
    import aurcore.modules  # noqa: F401 - importing registers the modules

    module = registry.create(code, harness.SAMPLE_RATE, harness.CHANNELS)
    if spec is None:
        return module
    for name, value in spec.params:
        module.params.set(name, value)
    if spec.region is not None:
        setter = getattr(module, "set_region", None)
        if callable(setter):
            setter(*spec.region)
    if spec.learn and probe is not None and probe.learn is not None:
        try:
            module.learn(np.asarray(probe.learn, dtype=np.float64))
        except Exception:  # noqa: BLE001 - a module that cannot learn is reported
            pass
    return module


def align_output(out: np.ndarray, delay: int) -> np.ndarray:
    """Shift a delayed output back so that it lines up with the input.

    Parameters
    ----------
    out:
        The output of the module.
    delay:
        The delay in samples; a positive number means the output is late.

    Returns
    -------
    numpy.ndarray
        The aligned output, as long as the input.
    """
    arr = np.asarray(out, dtype=np.float64)
    if delay <= 0:
        return arr
    if delay >= arr.shape[0]:
        return np.zeros_like(arr)
    aligned = arr[delay:]
    if aligned.shape[0] < arr.shape[0]:
        pad = np.zeros((arr.shape[0] - aligned.shape[0], arr.shape[1]), dtype=np.float64)
        aligned = np.concatenate([aligned, pad], axis=0)
    return aligned


def measure_delay(signal: np.ndarray, out: np.ndarray, declared: int = 0) -> int:
    """Return the delay of the output against the input, in samples.

    The whole search range of the catalogue is searched, and the search is
    **not** placed around the number the module declares: the measurement
    must not believe the module, or a module that lies about its delay is
    never caught.  The probe the delay is measured with is a broadband
    noise burst, so the cross correlation has one peak and not one peak
    per period of a tone, and the largest peak is the delay.

    Parameters
    ----------
    signal:
        The input signal.
    out:
        The output of the module.
    declared:
        The latency the module reports, in samples.  It is kept for the
        callers that pass it and it is **not** used to place the search.

    Returns
    -------
    int
        The delay in samples.
    """
    a = np.asarray(signal, dtype=np.float64)
    b = np.asarray(out, dtype=np.float64)
    n = min(a.shape[0], b.shape[0])
    if n == 0:
        return 0
    # The cross correlation of a signal that is late by D samples peaks at
    # the lag -D, so the delay is the negative of the lag.  Without the
    # sign the survey reads the wrong half of the correlation and reports
    # a delay that is off by up to the width of the search window.
    return -int(harness.cross_correlation_delay(a[:n, 0], b[:n, 0]))


# ---------------------------------------------------------------------------
# the checks
# ---------------------------------------------------------------------------
@dataclass
class CheckResult:
    """The measurement of one check."""

    label: str
    role: str
    before_db: float
    after_db: float
    change_db: float
    residual_db: float | None = None
    allowed: tuple[float | None, float | None] = (None, None)
    rule: tuple[float | None, float | None] = (None, None)
    ok: bool = True
    rescued: bool = False
    message: str = ""
    exception: exc.Exception_ | None = None

    @property
    def value(self) -> float:
        """The number the check is about."""
        if self.role == "residual" and self.residual_db is not None:
            return self.residual_db
        return self.change_db

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation."""
        return {
            "label": self.label,
            "role": self.role,
            "before_db": round(self.before_db, 2),
            "after_db": round(self.after_db, 2),
            "change_db": round(self.change_db, 2),
            "residual_db": None if self.residual_db is None else round(self.residual_db, 2),
            "allowed": [self.allowed[0], self.allowed[1]],
            "rule": [self.rule[0], self.rule[1]],
            "ok": self.ok,
            "rescued": self.rescued,
            "message": self.message,
            "exception": None if self.exception is None else self.exception.to_dict(),
        }


def _residual_db(out: np.ndarray, clean: np.ndarray, region: Region, sample_rate: int) -> float:
    """Return the reconstruction error of a region, relative to the clean signal."""
    arr = np.asarray(out, dtype=np.float64)
    ref = np.asarray(clean, dtype=np.float64)
    n = min(arr.shape[0], ref.shape[0])
    if n == 0:
        return harness.FLOOR_DB
    i0 = max(0, int(round(region.start_s * sample_rate)))
    i1 = n if region.end_s is None else min(n, int(round(region.end_s * sample_rate)))
    if i1 <= i0:
        return harness.FLOOR_DB
    a = arr[i0:i1]
    b = ref[i0:i1]
    error = float(np.sum((a - b) ** 2))
    power = float(np.sum(b ** 2))
    if power <= 0.0:
        return harness.FLOOR_DB
    return 10.0 * math.log10(max(error / power, 1e-30))


def evaluate_checks(
    spec: specs.ModuleSpec,
    probe: probes.Probe,
    signal: np.ndarray,
    aligned: np.ndarray,
) -> list[CheckResult]:
    """Measure every check of a module and judge it.

    A check that is covered by a registered exception is judged against the
    exception instead of the specification: the module is allowed to be as
    bad as the exception says, and no worse.

    Parameters
    ----------
    spec:
        The specification of the module.
    probe:
        The probe the module was driven with.
    signal:
        The input signal.
    aligned:
        The output, aligned with the input.

    Returns
    -------
    list of CheckResult
        One entry per check.
    """
    results: list[CheckResult] = []
    for check in spec.checks:
        label = check.label or check.region.label or check.role
        before = harness.region_level_db(signal, harness.SAMPLE_RATE, check.region)
        after = harness.region_level_db(aligned, harness.SAMPLE_RATE, check.region)
        change = harness.level_change_db(before, after)
        entry = CheckResult(
            label=label,
            role=check.role,
            before_db=before,
            after_db=after,
            change_db=change,
            allowed=check.allowed(),
        )
        if check.role == "residual" and probe.clean is not None:
            entry.residual_db = _residual_db(
                aligned, probe.clean, check.region, harness.SAMPLE_RATE
            )
        entry.exception = exc.exception_for(spec.code, check.role, label)
        low, high = entry.allowed
        entry.rule = (low, high)
        value = entry.value
        default_ok = True
        if low is not None and value < low:
            default_ok = False
        if high is not None and value > high:
            default_ok = False
        if entry.exception is not None:
            known = entry.exception
            if check.role == "keep":
                low = min(low if low is not None else 0.0,
                          known.limit - known.tolerance)
            elif check.role == "target":
                high = max(high if high is not None else 0.0,
                           known.limit + known.tolerance)
            elif check.role == "residual":
                high = max(high if high is not None else 0.0,
                           known.limit + known.tolerance)
            entry.allowed = (low, high)
        if low is not None and value < low:
            entry.ok = False
            entry.message = f"{value:.2f} dB is below the allowed {low:.2f} dB"
        if high is not None and value > high:
            entry.ok = False
            entry.message = f"{value:.2f} dB is above the allowed {high:.2f} dB"
        # A check that the rule would have failed but that a listed defect
        # covers is *rescued*: the module is listed, not clean.
        entry.rescued = default_ok is False and entry.ok
        results.append(entry)
    return results


# ---------------------------------------------------------------------------
# the measurement of one module
# ---------------------------------------------------------------------------
def measure_module(code: str, with_cost: bool = True) -> ModuleSurvey:
    """Measure one module against its specification.

    Parameters
    ----------
    code:
        The module code, e.g. ``AUR_DECLICK``.
    with_cost:
        Measure the cost of the module as well; the cost pass is the slow
        one for the heavy modules.

    Returns
    -------
    ModuleSurvey
        Everything that was measured.
    """
    from aurcore import registry
    import aurcore.modules  # noqa: F401 - importing registers the modules

    spec = specs.SPECS.get(code)
    cls = registry.get(code)
    result = ModuleSurvey(code=code)
    result.display_name = str(getattr(cls, "display_name", code))
    result.category = str(getattr(cls, "category", ""))
    result.policy = spec.policy if spec is not None else "unknown"
    result.probe = spec.probe if spec is not None else "latency_probe"
    if spec is not None and spec.note:
        result.notes.append(spec.note)
    if spec is None:
        result.notes.append(
            "the module has no survey specification: it is measured, but it is "
            "held to no rule about the signal"
        )

    probe = probes.build_probe(spec.probe, spec.duration_s) if spec is not None else None
    signal = (
        np.asarray(probe.signal, dtype=np.float64)
        if probe is not None
        else harness.latency_probe_signal()
    )

    try:
        # -- the delay, on a broadband burst and with a module of its own
        delay_module = prepare_module(code, spec, probe)
        result.latency_declared = int(getattr(delay_module, "latency_samples", 0) or 0)
        latency_signal = harness.latency_probe_signal()
        latency_out = np.asarray(
            delay_module.process(latency_signal.copy()), dtype=np.float64
        )
        if latency_out.ndim == 1:
            latency_out = latency_out[:, None]
        result.latency_measured = int(measure_delay(latency_signal, latency_out))
        result.latency_tolerance = latency_tolerance(spec, result)

        # -- the signal rules, read at the position the module really has
        module = prepare_module(code, spec, probe)
        out = module.process(signal.copy())
        out = np.asarray(out, dtype=np.float64)
        if out.ndim == 1:
            out = out[:, None]
        result.same_length = out.shape[0] == signal.shape[0]
        result.finite = bool(np.all(np.isfinite(out)))
        aligned = align_output(out, result.latency_measured)

        if spec is not None:
            check_results = evaluate_checks(spec, probe, signal, aligned)
            result.checks = [c.to_dict() for c in check_results]
            keep_changes = [c.change_db for c in check_results if c.role == "keep"]
            target_changes = [c.change_db for c in check_results if c.role == "target"]
            if keep_changes:
                result.wanted_removed_db = min(keep_changes)
            if target_changes:
                result.unwanted_removed_db = sum(target_changes) / len(target_changes)

        result.allocator_independent = harness.measure_allocator_independence(
            code,
            signal,
            prepare=lambda module: _prepare_for_cost(module, spec, probe),
        )
        result.deterministic = _measure_determinism(code, spec, probe, signal)
    except Exception as error:  # noqa: BLE001 - a broken module is reported
        result.error = f"{type(error).__name__}: {error}"
        return result

    # -- the cost and the block coherence
    if with_cost:
        result.cost_us = harness.measure_cost_us(
            code,
            signal=signal,
            prepare=lambda module: _prepare_for_cost(module, spec, probe),
        )
    delta, lag, runaway = harness.measure_block_coherence(
        code, signal, prepare=lambda module: _prepare_for_cost(module, spec, probe)
    )
    result.block_delta_db = delta
    result.block_lag = lag
    result.block_coherent = delta <= BLOCK_COHERENT_DB
    result.runaway_db = runaway
    return result


def _prepare_for_cost(module, spec: specs.ModuleSpec | None, probe: probes.Probe | None) -> None:
    """Put a module into the state the cost measurement expects."""
    if spec is None:
        return
    for name, value in spec.params:
        module.params.set(name, value)
    if spec.region is not None:
        setter = getattr(module, "set_region", None)
        if callable(setter):
            setter(*spec.region)
    if spec.learn and probe is not None and probe.learn is not None:
        try:
            module.learn(np.asarray(probe.learn, dtype=np.float64))
        except Exception:  # noqa: BLE001
            pass


def _measure_determinism(
    code: str,
    spec: specs.ModuleSpec | None,
    probe: probes.Probe | None,
    signal: np.ndarray,
) -> bool:
    """Return True when two fresh instances produce the same output."""
    try:
        first = prepare_module(code, spec, probe)
        second = prepare_module(code, spec, probe)
        a = np.asarray(first.process(signal.copy()), dtype=np.float64)
        b = np.asarray(second.process(signal.copy()), dtype=np.float64)
    except Exception:  # noqa: BLE001
        return False
    if a.shape != b.shape:
        return False
    return bool(np.array_equal(a, b))


def measure_all(codes: Sequence[str] | None = None, with_cost: bool = True) -> list[ModuleSurvey]:
    """Measure every module of the catalogue."""
    if codes is None:
        from aurcore import registry
        import aurcore.modules  # noqa: F401

        codes = [info.code for info in registry.all()]
    # The bound of the delay search is taken from the catalogue, so the
    # catalogue is asked again before the first module is measured.
    harness.reset_latency_search_bound()
    return [measure_module(code, with_cost=with_cost) for code in codes]


# ---------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------
def _fmt(value: float | None, digits: int = 2, dash: str = "—") -> str:
    if value is None:
        return dash
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return dash
    return f"{value:.{digits}f}"


def latency_tolerance(
    spec: specs.ModuleSpec | None,
    result: ModuleSurvey,
) -> int:
    """Return how far the measured delay may be from the declared one.

    A module that declares a latency must delay the signal by exactly that
    latency: the delay line of a module has a fixed length, so the
    measurement resolves it to the sample.  A module that declares no
    latency is held to the resolution of a host instead: the correlation of
    a filtered signal peaks at the group delay of the filter, and one block
    is the resolution a host compensates a delay with.

    A module whose delay is not a constant — a module that warps the time
    axis — carries the tolerance it needs in its specification.

    Parameters
    ----------
    spec:
        The specification of the module; ``None`` for a module that has no
        specification yet.
    result:
        The measurement of the module.

    Returns
    -------
    int
        The tolerance, in samples.
    """
    if spec is not None and spec.latency_tolerance is not None:
        return int(spec.latency_tolerance)
    if int(result.latency_declared) > 0:
        return LATENCY_TOLERANCE_SAMPLES
    return LATENCY_TOLERANCE_UNDECLARED_SAMPLES


def module_verdict(result: ModuleSurvey) -> tuple[str, str]:
    """Return the verdict of a module and the reason behind it.

    The verdict is ``PASS`` when every rule holds, ``EXCEPTION`` when every
    broken rule is covered by a registered exception, and ``FAIL``
    otherwise.
    """
    problems: list[str] = []
    for check in result.checks:
        if check.get("ok"):
            continue
        known = check.get("exception")
        if known:
            continue
        problems.append(f"{check['label']}: {check['message']}")
    if result.error:
        problems.append(result.error)
    if not result.finite:
        problems.append("the output holds a non finite sample")
    if not result.same_length:
        problems.append("the output is not as long as the input")
    if not result.allocator_independent:
        problems.append("the output depends on what the allocator handed out")
    if not result.deterministic:
        problems.append("two runs of the same module differ")
    if abs(result.latency_error) > result.latency_tolerance:
        problems.append(
            f"the module delays the signal by {result.latency_measured} samples "
            f"but declares {result.latency_declared}"
        )
    cost_exception = exc.exception_for(result.code, "cost")
    if result.over_budget and cost_exception is None:
        problems.append(
            f"the module costs {result.cost_us:.0f} us for a 64 sample block, "
            f"the budget is {result.budget_us:.0f} us"
        )
    elif cost_exception is not None and result.cost_us > cost_exception.limit:
        problems.append(
            f"the module costs {result.cost_us:.0f} us for a 64 sample block, "
            f"the listed ceiling is {cost_exception.limit:.0f} us"
        )
    runaway_exception = exc.exception_for(result.code, "runaway")
    runaway_limit = RUNAWAY_LIMIT_DB
    if runaway_exception is not None:
        runaway_limit = max(
            runaway_limit, runaway_exception.limit + runaway_exception.tolerance
        )
    if result.runaway_db > runaway_limit:
        problems.append(
            f"the output is {result.runaway_db:.0f} dB louder than the input "
            f"when the host feeds 64 sample blocks"
        )
    if problems:
        return ("FAIL", "; ".join(problems))
    broken = [c for c in result.checks if not c.get("ok")]
    rescued = [c for c in result.checks if c.get("rescued")]
    runaway_listed = (
        runaway_exception is not None and result.runaway_db > RUNAWAY_LIMIT_DB
    )
    if (
        broken
        or rescued
        or runaway_listed
        or (cost_exception is not None and result.over_budget)
    ):
        return ("EXCEPTION", "; ".join(c.get("message", "") for c in broken + rescued))
    return ("PASS", "")


def module_status(result: ModuleSurvey) -> tuple[str, str]:
    """Return the verdict of a module and the kind of verdict it is.

    The verdict is ``PASS`` when every rule holds, ``EXCEPTION`` when every
    broken rule is covered by a registered exception, and ``FAIL``
    otherwise.  The kind tells a defect that was already on the list from
    one the survey found.
    """
    verdict, reason = module_verdict(result)
    if verdict != "EXCEPTION":
        return verdict, verdict.lower()
    entries = [
        entry
        for entry in exc.all_exceptions()
        if entry.code == result.code
    ]
    if any(not entry.known for entry in entries):
        return verdict, "new"
    return verdict, "known"


def module_deviations(result: ModuleSurvey) -> list[tuple[str, float, str]]:
    """Return how far the module misses every rule it breaks.

    Each entry is ``(rule, amount, text)`` where the amount is in decibels
    for the signal rules and in the budget share for the cost rule.
    """
    out: list[tuple[str, float, str]] = []
    for check in result.checks:
        low, high = check.get("rule", check["allowed"])
        change = float(check["change_db"])
        if low is not None and change < low:
            out.append(
                (
                    f"{check['label']}: the wanted part",
                    low - change,
                    f"{change:.2f} dB of the wanted part are lost, the budget is "
                    f"{low:.2f} dB",
                )
            )
        if high is not None and change > high:
            out.append(
                (
                    f"{check['label']}: the unwanted part",
                    change - high,
                    f"the unwanted part loses {change:.2f} dB, the rule asks for "
                    f"{high:.2f} dB",
                )
            )
    if result.over_budget:
        out.append(
            (
                "the realtime budget",
                20.0 * math.log10(max(result.budget_fraction, 1.0)),
                f"{result.cost_us:.0f} us for a 64 sample block, the budget is "
                f"{result.budget_us:.0f} us ({result.budget_fraction:.2f}x)",
            )
        )
    if result.runaway_db > RUNAWAY_LIMIT_DB:
        out.append(
            (
                "the output level",
                result.runaway_db - RUNAWAY_LIMIT_DB,
                f"the output is {result.runaway_db:.0f} dB louder than the input",
            )
        )
    return out


def _finding_state(path: Path) -> str:
    """Return the state a finding file declares (``open`` or ``closed``)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:  # pragma: no cover - defensive
        return "unknown"
    for line in text.splitlines()[:20]:
        if line.strip().lower().startswith("**state:**"):
            return "closed" if "closed" in line.lower() else "open"
    return "unknown"


def source_fingerprints() -> dict[str, tuple[float, int]]:
    """Return the size and the time of the source file of every module.

    The survey takes the fingerprint before and after the measurement: a
    module whose source changed while the survey ran was being rewritten
    underneath the measurement, and its row is a snapshot of a file that
    was in motion.
    """
    import inspect

    from aurcore import registry
    import aurcore.modules  # noqa: F401 - importing registers the modules

    out: dict[str, tuple[float, int]] = {}
    for info in registry.all():
        try:
            cls = registry.get(info.code)
            path = Path(inspect.getfile(cls))
            stat = path.stat()
        except Exception:  # noqa: BLE001 - a module without a file is skipped
            continue
        out[info.code] = (stat.st_mtime, stat.st_size)
    return out


def render_report(
    results: Sequence[ModuleSurvey],
    environment: dict[str, Any] | None = None,
    findings: Sequence[str] = (),
    finding_states: dict[str, str] | None = None,
    rewritten: Sequence[str] = (),
) -> str:
    """Render the measurements as a Markdown report."""
    statuses = [module_status(result) for result in results]
    passed = sum(1 for verdict, _kind in statuses if verdict == "PASS")
    known = sum(1 for verdict, kind in statuses if verdict == "EXCEPTION" and kind == "known")
    new = sum(1 for verdict, kind in statuses if verdict == "EXCEPTION" and kind == "new")
    failed = sum(1 for verdict, _kind in statuses if verdict == "FAIL")
    over = [r for r in results if r.over_budget]

    lines: list[str] = []
    lines.append("# Auralis — the module survey")
    lines.append("")
    lines.append(
        f"**{len(results)} modules measured — {passed} pass every rule, "
        f"{known} {'is' if known == 1 else 'are'} listed with a defect that was "
        f"already known, {new} {'is' if new == 1 else 'are'} listed with a defect "
        f"the survey found, {failed} {'fails' if failed == 1 else 'fail'}.**  What "
        "every module of the catalogue does to a signal, and what it costs."
    )
    lines.append("")
    lines.append(
        f"{len(exc.all_exceptions())} "
        f"{'defect is' if len(exc.all_exceptions()) == 1 else 'defects are'} "
        "listed in `qa/survey/exceptions.py`, each of them with a finding file "
        "in `var/patches/b12/` (§5 and §6).  A module that is listed may be as "
        "bad as the listed number and no worse; a module that is not listed "
        "has to satisfy every rule."
    )
    lines.append("")
    lines.append(
        "The measurement code is in `qa/survey/`, the machine readable result is "
        "`qa/survey/report.json`, and the gate is `tests/test_module_survey.py`."
    )
    lines.append("")
    lines.append("## 0. How this report was produced")
    lines.append("")
    lines.append(
        "```\n"
        "python qa/survey/survey.py --all\n"
        "```\n"
        "\n"
        "The runner measures every module of the registry, writes this report and "
        "writes `qa/survey/report.json`.  It takes no other option: `--all` is the "
        "whole survey, and there is no subset of it that would answer the "
        "questions of this report.  A single module can be measured with "
        "`python qa/survey/survey.py --module AUR_DECLICK`, which does not write "
        "the report."
    )
    lines.append("")
    if environment:
        load = environment.get("load_percent")
        load_text = (
            f", the machine was {load:.1f} % busy while it ran"
            if isinstance(load, (int, float)) and load == load
            else ", the load of the machine was not measured"
        )
        lines.append(
            f"Measured on {environment.get('platform', '?')} with Python "
            f"{environment.get('python', '?')} and NumPy {environment.get('numpy', '?')} "
            f"on {environment.get('date', '?')}{load_text}."
        )
        lines.append("")
    lines.append(
        "> The cost column is the cost of the block **on the reference "
        "machine**: every block is timed next to a batch of 1024 point "
        "transforms of the machine that measures it, and the ratio of the "
        "two — which is the number of transform equivalents the block "
        "costs — does not move with the load of the machine.  The result "
        "is expressed in the microseconds of the reference machine (8.300 "
        "us per transform), the yardstick of the performance table "
        "(`qa/performance/budgets.py`), so the budget of 1333.3 us is the "
        "same requirement in both tables.  The number is the fastest of "
        "five passes of fifteen timed blocks after three warm up blocks, "
        "so it is the least disturbed observation of the run."
    )
    lines.append("")
    if rewritten:
        lines.append(
            "> **The source of "
            + ("one module was" if len(rewritten) == 1 else "these modules were")
            + " changed while the survey ran**: "
            + ", ".join(f"`{code}`" for code in rewritten)
            + ".  The module was being written underneath the measurement, so "
            "its row is a snapshot of a file that was in motion — it has to be "
            "measured again after the repair.  Every other row is of a file that "
            "did not move."
        )
        lines.append("")
    lines.append("## 1. How the modules are measured")
    lines.append("")
    corpus_probes = len(probes.CORPUS_PROBES)
    lines.append(
        "| item | value |\n|---|---|\n"
        "| sample rate | 48 000 Hz |\n"
        "| channels | 2 |\n"
        "| block size of the cost measurement | 64 samples |\n"
        "| realtime budget of a 64 sample block | 1333.3 us |\n"
        f"| probes | {len(probes.PROBES)} signals: {corpus_probes} of them are "
        "clips of `corpus/audio/test`, the other "
        f"{len(probes.PROBES) - corpus_probes} are synthetic signals built for the "
        "purpose |\n"
        "| the wanted part | the programme material of the probe, in a frequency "
        "band that carries nothing but the programme |\n"
        "| the unwanted part | the defect of the probe, in a band that carries "
        "nothing but the defect |\n"
        "| the cost | the fastest of five passes of 15 timed blocks after 3 warm "
        "up blocks, the median of the pass, every block timed next to a batch "
        "of 1024 point transforms and expressed in the microseconds of the "
        "reference machine (8.300 us per transform) |\n"
        "| the latency | the delay of the output against the input, from the peak "
        "of the cross correlation of a broadband noise burst, searched over the "
        "whole range the catalogue can declare |\n"
        "| the block delta | the RMS difference between the one block render and "
        "the 64 sample render, aligned by the delay; a block coherent module is "
        "at or below -40 dB |\n"
        "| the runaway | how much louder the 64 sample render is than the input; "
        "a module may not be more than 24 dB louder |\n"
        "| the allocator check | the module is run twice, once with a clean heap "
        "and once with a poisoned heap; the two outputs have to be identical bit "
        "for bit |\n"
        "| the determinism check | the module is run twice; the two outputs have "
        "to be identical bit for bit |"
    )
    lines.append("")
    lines.append(
        "The budget of the wanted part is a property of the module, not of the "
        "measurement: it is written down in `qa/survey/specs.py` and the gate "
        "holds every module to it.  A module that breaks a rule is listed in "
        "`qa/survey/exceptions.py` with the number that was measured and the "
        "reason; the gate then holds it to the listed number instead of to the "
        "rule, so a repair cannot make the defect worse unnoticed."
    )
    lines.append("")
    lines.append("## 2. The table")
    lines.append("")
    lines.append(
        "One row per module.  *keeps the wanted part* is the change of the "
        "programme: negative is damage.  *removes the unwanted part* is the "
        "change of the defect: negative is the job being done, positive means "
        "the module *added* something (Dither, Signal Generator, Spectral "
        "Recovery and EQ Match are supposed to do that)."
    )
    lines.append("")
    header = (
        "| # | module | name | policy | keeps the wanted part (dB) | removes the "
        "unwanted part (dB) | cost us | budget % | latency (samples) | block "
        "delta (dB) | runaway (dB) | finite | same length | alloc | verdict |"
    )
    lines.append(header)
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for index, result in enumerate(results, start=1):
        verdict, _reason = module_verdict(result)
        lines.append(
            f"| {index} | `{result.code}` | {result.display_name} | {result.policy} "
            f"| {_fmt(result.wanted_removed_db)} | {_fmt(result.unwanted_removed_db)} "
            f"| {result.cost_us:.0f} | {result.budget_fraction * 100:.0f}% "
            f"| {result.latency_measured} | {_fmt(result.block_delta_db, 1)} "
            f"| {_fmt(result.runaway_db, 0)} "
            f"| {'yes' if result.finite else 'NO'} "
            f"| {'yes' if result.same_length else 'NO'} "
            f"| {'yes' if result.allocator_independent else 'NO'} "
            f"| {verdict} |"
        )
    lines.append("")
    lines.append("## 3. The latency of every module")
    lines.append("")
    bound = harness.latency_search_bound()
    longest = max(results, key=lambda r: r.latency_declared)
    lines.append(
        f"The delay a module puts into the signal is measured on a broadband "
        f"noise burst: the cross correlation of noise has one peak, so the "
        f"peak is the delay, and the search does not have to believe the "
        f"module.  The range that is searched is not a constant: it is the "
        f"largest latency any module of the catalogue declares "
        f"({longest.latency_declared} samples for `{longest.code}`) plus a "
        f"margin of {harness.LATENCY_SEARCH_MARGIN} samples, so the search "
        f"covers **every delay the product can declare**: {bound} samples, "
        f"{bound / harness.SAMPLE_RATE * 1000:.1f} ms at 48 kHz.  The margin "
        f"is one window of the analysis of the core (2048 samples), so a "
        f"module whose declaration is off by a whole analysis window is "
        f"measured inside the search and not missed."
    )
    lines.append("")
    lines.append(
        "The rule of the gate: the delay a module declares and the delay that "
        "was measured may not differ by more than the tolerance of the "
        "module.  A module that declares a latency must delay the signal by "
        f"exactly that latency — the tolerance is {LATENCY_TOLERANCE_SAMPLES} "
        f"samples (41.7 us at 48 kHz), because the delay line of a module has "
        f"a fixed length and the measurement resolves it to the sample.  A "
        f"module that declares no latency may not delay the signal by more "
        f"than a block ({LATENCY_TOLERANCE_UNDECLARED_SAMPLES} samples, 1.33 ms "
        "at 48 kHz), because the cross correlation of a filtered signal peaks "
        "at the group delay of the filter and not at a latency.  A module "
        "whose delay is not a constant — a module that warps the time axis — "
        "carries the tolerance it needs in `qa/survey/specs.py`."
    )
    lines.append("")
    lines.append(
        "| module | the delay it declares | the delay that was measured | the "
        "difference | the tolerance | the verdict |"
    )
    lines.append("|---|---|---|---|---|---|")
    for result in results:
        verdict, _reason = module_verdict(result)
        lines.append(
            f"| `{result.code}` | {result.latency_declared} | "
            f"{result.latency_measured} | {result.latency_error:+d} | "
            f"{result.latency_tolerance} | {verdict} |"
        )
    lines.append("")
    lines.append("## 4. The summary")
    lines.append("")
    lines.append(
        "| | count |\n|---|---|\n"
        f"| modules measured | {len(results)} |\n"
        f"| pass every rule | {passed} |\n"
        f"| listed with a defect that was already known | {known} |\n"
        f"| listed with a defect the survey found | {new} |\n"
        f"| fail a rule | {failed} |\n"
        f"| over the 64 sample budget | {len(over)} |\n"
        f"| finite output | {sum(1 for r in results if r.finite)} |\n"
        f"| output as long as the input | {sum(1 for r in results if r.same_length)} |\n"
        f"| output independent of the allocator | "
        f"{sum(1 for r in results if r.allocator_independent)} |\n"
        f"| two runs of a module are the same | "
        f"{sum(1 for r in results if r.deterministic)} |\n"
        f"| the declared latency is the measured latency | "
        f"{sum(1 for r in results if abs(r.latency_error) <= r.latency_tolerance)} |\n"
        f"| block coherent | {sum(1 for r in results if r.block_coherent)} |"
    )
    lines.append("")
    failures = [
        (result.code, module_verdict(result)[1]) for result in results
        if module_verdict(result)[0] == "FAIL"
    ]
    if failures:
        lines.append("### The failures")
        lines.append("")
        lines.append(
            "A module that fails is not in the table of the rules above: it broke "
            "one of them, and the gate holds it to the rule."
        )
        lines.append("")
        lines.append("| module | what it did |")
        lines.append("|---|---|")
        for code, reason in failures:
            lines.append(f"| `{code}` | {reason} |")
        lines.append("")
    lines.append("### The modules over the 64 sample budget")
    lines.append("")
    lines.append(
        "A module that is over the budget is held at a **debt ceiling** — the "
        "measured cost plus 50 % — until its owner brings it down.  A module "
        "that is inside the budget is held to the budget itself."
    )
    lines.append("")
    lines.append("| module | cost us | budget % | the ceiling | the ceiling in us |")
    lines.append("|---|---|---|---|---|")
    for result in sorted(over, key=lambda r: -r.cost_us):
        entry = exc.exception_for(result.code, "cost")
        ceiling = f"{entry.limit:.0f} us" if entry else "—"
        share = "the budget" if entry is None else "a debt ceiling"
        lines.append(
            f"| `{result.code}` | {result.cost_us:.0f} | "
            f"{result.budget_fraction * 100:.0f}% | {share} | {ceiling} |"
        )
    if not over:
        lines.append(
            "| — | — | — | no module was measured over the budget | — |"
        )
    lines.append("")
    lines.append("### The biggest misses of a rule")
    lines.append("")
    lines.append(
        "The biggest misses of a rule, in the order of the size of the miss.  A "
        "module that is listed with a defect is measured against the rule, not "
        "against the listed number.  The cost is expressed in the decibels of the "
        "budget the module misses."
    )
    lines.append("")
    ranked: list[tuple[float, str, str, str]] = []
    for result in results:
        for rule, amount, text in module_deviations(result):
            ranked.append((amount, result.code, rule, text))
    ranked.sort(key=lambda row: -row[0])
    lines.append("| module | rule | how far it misses |")
    lines.append("|---|---|---|")
    for amount, code, rule, text in ranked[:5]:
        lines.append(f"| `{code}` | {rule} | {amount:.2f} dB — {text} |")
    if not ranked:
        lines.append("| — | — | no module misses a rule |")
    lines.append("")
    lines.append("## 5. The open defects")
    lines.append("")
    lines.append(
        "Every entry of `qa/survey/exceptions.py`: the module may be as bad as "
        "the listed number, and no worse.  When the module is repaired the entry "
        "is deleted and the gate holds it to the rule.  Every entry has a "
        "finding file that carries the numbers."
    )
    lines.append("")
    lines.append("| module | what | measured | allowed | the finding file | why |")
    lines.append("|---|---|---|---|---|---|")
    for entry in exc.all_exceptions():
        lines.append(
            f"| `{entry.code}` | {entry.kind} | {entry.measured:g} | "
            f"{entry.limit:g} | `{entry.owner or '—'}` | {entry.reason} |"
        )
    lines.append("")
    if findings:
        lines.append("## 6. The findings")
        lines.append("")
        lines.append(
            "One file per module that broke a rule, with the numbers, so that "
            "the owner of the module can pick it up.  The modules are not "
            "touched by the survey.  A finding whose module has been repaired "
            "since is marked *closed* and says what the survey measures now."
        )
        lines.append("")
        lines.append("| finding | the module | the state |")
        lines.append("|---|---|---|")
        states = finding_states or {}
        for path in findings:
            state = states.get(path, "unknown")
            stem = Path(path).stem
            code = FINDING_MODULES.get(stem, stem)
            lines.append(f"| `{path}` | `{code}` | {state} |")
        lines.append("")
    lines.append("## 7. The files")
    lines.append("")
    lines.append(
        "| file | what it is |\n|---|---|\n"
        "| `qa/survey/harness.py` | the measurement library: region levels, the "
        "cost, the latency, the block coherence, the allocator check |\n"
        "| `qa/survey/probes.py` | the 31 probe signals |\n"
        "| `qa/survey/specs.py` | the specification of every module: the probe, "
        "the regions, the budgets |\n"
        "| `qa/survey/exceptions.py` | the known open defects |\n"
        "| `qa/survey/survey.py` | the runner; it writes this report and "
        "`report.json` |\n"
        "| `qa/survey/report.json` | the machine readable result |\n"
        "| `tests/test_module_survey.py` | the gate |"
    )
    lines.append("")
    return "\n".join(lines)


def environment_info(load_percent: float | None = None) -> dict[str, Any]:
    """Return a description of the machine the measurements ran on."""
    import datetime
    import platform

    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "platform": platform.platform(),
        "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "command": "python qa/survey/survey.py --all",
        "load_percent": load_percent,
    }


def write_json(
    results: Sequence[ModuleSurvey],
    path: Path,
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write the machine readable result and return it."""
    payload = {
        "environment": environment if environment is not None else environment_info(),
        "budget_us": harness.BUDGET_US,
        "modules": [result.to_dict() for result in results],
        "exceptions": [entry.to_dict() for entry in exc.all_exceptions()],
        "specs": {code: spec.to_dict() for code, spec in sorted(specs.SPECS.items())},
        "verdicts": {result.code: module_verdict(result)[0] for result in results},
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    """Entry point of the runner."""
    parser = argparse.ArgumentParser(description="Run the module survey.")
    parser.add_argument("--module", action="append", default=[], help="measure one module")
    parser.add_argument("--all", action="store_true", help="measure every module")
    parser.add_argument("--json", type=Path, default=None, help="write the raw results as JSON")
    parser.add_argument("--no-cost", action="store_true", help="skip the cost measurement")
    parser.add_argument("--no-report", action="store_true", help="do not write the report")
    args = parser.parse_args(argv)
    if not args.module and not args.all:
        parser.print_help()
        return 2
    codes = args.module or None
    start = time.perf_counter()
    cpu_start = harness._cpu_times()
    before = source_fingerprints()
    results = measure_all(codes, with_cost=not args.no_cost)
    load = harness.machine_load(cpu_start, harness._cpu_times())
    after = source_fingerprints()
    rewritten = sorted(
        code for code, stamp in before.items() if after.get(code) != stamp
    )
    print(f"measured {len(results)} module(s) in {time.perf_counter() - start:.1f} s")
    print(f"the machine was {load:.1f} % busy while the survey ran")
    if rewritten:
        print(
            "the source of these modules changed while the survey ran: "
            + ", ".join(rewritten)
        )
    for result in results:
        verdict, reason = module_verdict(result)
        print(
            f"  {result.code:<22} {verdict:<9} wanted {_fmt(result.wanted_removed_db):>8} "
            f"unwanted {_fmt(result.unwanted_removed_db):>8} "
            f"cost {result.cost_us:>8.0f} us  block {_fmt(result.block_delta_db, 1):>7} dB"
            f"  latency {result.latency_declared:>5d}/{result.latency_measured:<5d}"
        )
        if reason and verdict != "PASS":
            print(f"      {reason}")
    if args.json:
        args.json.write_text(
            json.dumps([r.to_dict() for r in results], indent=2), encoding="utf-8"
        )
    if not args.no_report and not args.module:
        patches = _HERE.parent.parent / "var" / "patches" / "b12"
        findings = sorted(
            str(p.relative_to(_HERE.parent.parent)).replace("\\", "/")
            for p in patches.glob("*.md")
        )
        states = {
            path: _finding_state(_HERE.parent.parent / path) for path in findings
        }
        environment = environment_info(load)
        report = render_report(results, environment, findings, states, rewritten)
        REPORT_PATH.write_text(report, encoding="utf-8")
        write_json(results, JSON_PATH, environment)
        print(f"wrote {REPORT_PATH}")
        print(f"wrote {JSON_PATH}")
    # The survey is a gate: a module that breaks a rule makes it fail.
    broken = [result.code for result in results if module_verdict(result)[0] == "FAIL"]
    if broken:
        print(f"{len(broken)} module(s) broke a rule: " + ", ".join(broken))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
