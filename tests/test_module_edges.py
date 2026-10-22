"""
tests.test_module_edges -- the gate against the edges of every module.

The rule comes from the robustness study: at the edges of the material a
module has to hold five rules.

    finite   the output holds no NaN and no infinity, and the module does
             not raise through the call boundary.
    length   the output is as long as the input.
    tail     the output goes quiet within the tail the module declares, so
             that a host which waits for that tail does not cut the sound
             off and click.
    silence  a silent input comes back as silence, unless the module says
             that it makes a sound of its own.
    material the level of the output does not depend on how much material
             the module has seen.

The edges are the five signals of the study -- silence, a full scale sine, a
single impulse, a signal at the clipping ceiling and a signal that is
shorter than the analysis window of the module -- and the shortest signal
there is, a signal of one sample.

The gate is :mod:`check_module_edges`, the script of ``qa/``.  The test
runs the same measurement the script runs, so the two can never disagree,
and the last tests check that the gate is a gate: a module that breaks a
rule has to make it fail.

The numbers of the two rules that came last are in :mod:`qa.edge_rules`, and
the test reads them from the gate, so that the rule, the gate and the test
carry one number.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The gate lives outside the test package, so it is put on the path.
_QA_DIR = ROOT / "qa"
if str(_QA_DIR) not in sys.path:
    sys.path.insert(0, str(_QA_DIR))

import check_module_edges as gate  # noqa: E402

from aurcore import registry  # noqa: E402
from aurcore.modules.base import Module  # noqa: E402

#: The codes of every registered module.
CODES = sorted(info.code for info in registry.all())

#: The edges, by name.
EDGES = tuple(gate.EDGE_NAMES)

#: The edge signals, built once: they are the same for every module.
SIGNALS = gate.edge_signals()

#: Every (module, edge) pair the gate walks over.
CASES = [(code, edge) for code in CODES for edge in EDGES]
CASE_IDS = [f"{code}-{edge}" for code, edge in CASES]


# ---------------------------------------------------------------------------
# the edges
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("code,edge", CASES, ids=CASE_IDS)
def test_the_output_at_the_edge_is_finite_and_the_right_length(code: str, edge: str):
    """A module holds the finite rule and the length rule at every edge.

    The signal of the edge is handed to a fresh module, so the module sees
    the state it sees when a host opens a file with it.

    Parameters
    ----------
    code:
        The module code.
    edge:
        The name of the edge signal.
    """
    signal = SIGNALS[edge]
    module = registry.create(code, gate.SAMPLE_RATE, gate.CHANNELS)

    out = np.asarray(module.process(signal), dtype=np.float64)

    assert out.ndim == 2, f"{code}: the output is {out.ndim} dimensional"
    assert out.shape[1] == gate.CHANNELS, (
        f"{code}: the output has {out.shape[1]} channels"
    )

    broken = int(np.count_nonzero(~np.isfinite(out)))
    assert broken == 0, (
        f"{code}: {broken} of {out.size} samples are not finite for the edge "
        f"{edge!r}"
    )

    wanted = gate.expected_length(module, int(signal.shape[0]))
    assert out.shape[0] == wanted, (
        f"{code}: the output is {out.shape[0]} samples long for the edge "
        f"{edge!r}, the input was {signal.shape[0]} and the module owes {wanted}"
    )


# ---------------------------------------------------------------------------
# the tail
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("code", CODES)
def test_the_module_does_not_ring_past_its_declared_tail(code: str):
    """A module holds the tail rule.

    The ring-out is measured the way the independent host measures it: a
    tone goes in, silence follows, and the ring-out is the number of samples
    the output stays above -60 dBFS after the input stopped.  Three
    excitations are used -- the tone of the host, the full scale sine and
    the signal at the clipping ceiling -- and the longest of the three is
    the number the module has to live with.

    Parameters
    ----------
    code:
        The module code.
    """
    module = registry.create(code, gate.SAMPLE_RATE, gate.CHANNELS)
    latency = int(getattr(module, "latency_samples", 0))
    tail = int(getattr(module, "tail_samples", 0))

    if tail == gate.TAIL_INFINITE:
        # A module that never goes quiet says so, and there is no tail to
        # hold: the gate of the tail checks the other modules.
        return

    longest = 0
    for excitation in gate.excitations().values():
        fresh = registry.create(code, gate.SAMPLE_RATE, gate.CHANNELS)
        longest = max(longest, gate.measure_ring_out(fresh, excitation))

    limit = latency + tail
    assert longest <= limit + gate.TAIL_ALLOWANCE, (
        f"{code}: the module sounds for {longest} samples after its input "
        f"stopped, but it declares a latency of {latency} and a tail of "
        f"{tail}; it may ring for {limit + gate.TAIL_ALLOWANCE} samples"
    )


def test_the_modules_that_never_go_quiet_say_so():
    """A module that makes a sound of its own declares an infinite tail.

    The declaration is what the gate reads: it holds no list of module codes
    of its own, so a module that joins the family later is judged by what it
    says.  The two modules of ours that make a sound of their own are the
    generator, which makes a tone, and the ambience match, which lays a bed
    of its own under the recording.
    """
    infinite = [
        code for code in CODES
        if int(getattr(registry.create(code, gate.SAMPLE_RATE, gate.CHANNELS),
                       "tail_samples", 0)) == gate.TAIL_INFINITE
    ]
    assert "AUR_SIGNALGEN" in infinite, (
        "the generator makes a tone of its own and has to say so"
    )
    assert "AUR_AMBIENCEMATCH" in infinite, (
        "the ambience match lays a bed of its own and has to say so"
    )


# ---------------------------------------------------------------------------
# the silence
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def silence_peaks() -> dict[str, float]:
    """The peak of the output of every module when the input is silence."""
    return {code: gate.measure_silence(code) for code in CODES}


@pytest.mark.parametrize("code", CODES)
def test_the_module_is_silent_on_silence(code: str, silence_peaks):
    """A silent input comes back as silence.

    The rule is the one of the study: the peak of the output on silence may
    not be above 1e-6, which is -120 dBFS.  A module that makes a sound of
    its own says so with an infinite tail and is exempt, because the sound
    it makes is not the material.
    """
    module = registry.create(code, gate.SAMPLE_RATE, gate.CHANNELS)
    if gate.makes_own_sound(module):
        assert int(getattr(module, "tail_samples", 0)) == gate.TAIL_INFINITE, (
            f"{code}: the module is exempt from the silence rule, so it has "
            "to declare that it never goes quiet"
        )
        return

    peak = silence_peaks[code]
    assert peak <= gate.SILENCE_RULE.limit, (
        f"{code}: a silent input comes back at {peak:.6e} "
        f"({20.0 * np.log10(max(peak, 1e-300)):.1f} dBFS), the rule allows "
        f"{gate.SILENCE_RULE.limit:.1e}"
    )


# ---------------------------------------------------------------------------
# the length of the material
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def materials() -> dict[str, "gate.MaterialReport"]:
    """What the gate measured about the length of the material."""
    return {code: gate.check_material(code) for code in CODES}


@pytest.mark.parametrize("code", CODES)
def test_the_level_does_not_depend_on_the_length_of_the_material(
    code: str, materials
):
    """The level of the output may not depend on how much material it saw.

    A module that has not heard enough material yet is not judged by it: it
    says so through the latency it declares or through the settling time it
    declares.  A module that makes a sound of its own says so with an
    infinite tail and is exempt.
    """
    report = materials[code]
    assert not report.problems, (
        f"{code}: the level of the output depends on the length of the "
        f"material: {report.problems}"
    )


@pytest.mark.parametrize("code", CODES)
def test_a_module_that_is_still_settling_says_so(code: str):
    """A module that is still settling declares the time it needs.

    The declaration is read from the module, not from a list in the gate: a
    module that needs material before it can judge it has to say how much.
    """
    module = registry.create(code, gate.SAMPLE_RATE, gate.CHANNELS)
    settling = gate.settling_frames(module)
    assert settling >= 0
    if settling > gate.MATERIAL_REFERENCE_FRAMES:
        pytest.fail(
            f"{code}: the module declares a settling time of {settling} "
            f"frames and is not ready after "
            f"{gate.MATERIAL_REFERENCE_FRAMES} frames of material"
        )


def test_the_fade_says_that_it_shapes_the_material_over_its_length():
    """The fade is not judged by the material it is still shaping.

    The fade ramps the head of the material over its own length, so the
    level of its output depends on where in the material it is.  It says so
    with its settling time, and the gate reads it from the module.
    """
    module = registry.create("AUR_FADE", gate.SAMPLE_RATE, gate.CHANNELS)
    assert gate.settling_frames(module) > 0, (
        "the fade shapes the material over its length and has to say so"
    )


# ---------------------------------------------------------------------------
# the gate is a gate
# ---------------------------------------------------------------------------
class _Broken(Module):
    """A module that breaks the rules on purpose."""

    code = "AUR_BROKENPROBE"
    display_name = "Broken Probe"
    category = "Utility"

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self.mode = "nan"
        self._delay = 0

    @property
    def tail_samples(self) -> int:
        """The probe declares an infinite tail in the mode that needs it."""
        if self.mode == "own":
            return gate.TAIL_INFINITE
        return 0

    def process(self, x: np.ndarray) -> np.ndarray:
        """Return something that breaks a rule."""
        out = np.array(x, dtype=np.float64, copy=True)
        if self.mode == "nan":
            out[0, 0] = float("nan")
        elif self.mode == "short":
            return out[:-1]
        elif self.mode == "ring":
            # A delay of 1000 samples and no latency declared: the module
            # keeps sounding for 1000 samples after the input stopped.
            self._delay += out.shape[0]
            return np.concatenate([np.zeros((self._delay, out.shape[1])), out], axis=0)
        elif self.mode == "voice":
            # A small signal of its own out of nothing.
            return out + 1.0e-3
        elif self.mode == "material":
            # The gain follows the length of the material: the level of the
            # output depends on how much material the module has seen.
            span = min(1.0, out.shape[0] / 1024.0)
            return out * (1.0 - 0.5 * span)
        return out


def _report_for(broken: _Broken) -> "gate.ModuleReport":
    """Run the gate over the broken module and return its report."""
    real_create = registry.create

    def fake_create(code, sample_rate, channels):  # type: ignore[no-untyped-def]
        if code == broken.code:
            return broken
        return real_create(code, sample_rate, channels)

    registry.create = fake_create  # type: ignore[assignment]
    try:
        return gate.check_module(broken.code)
    finally:
        registry.create = real_create  # type: ignore[assignment]


@pytest.mark.parametrize("mode,rule", [
    ("nan", "finite"),
    ("short", "length"),
    ("ring", "tail"),
    ("voice", "silence"),
    ("material", "material"),
])
def test_the_gate_fails_for_a_module_that_breaks_a_rule(mode: str, rule: str):
    """The gate is a gate: a module that breaks a rule makes it fail.

    The rules are run against a module whose output is deliberately broken:
    a NaN, a changed length, a ring-out that the module does not declare, a
    signal out of silence and a level that follows the length of the
    material.

    Parameters
    ----------
    mode:
        What the broken module does.
    rule:
        The rule that has to be caught.
    """
    broken = _Broken(gate.SAMPLE_RATE, gate.CHANNELS)
    broken.mode = mode

    report = _report_for(broken)

    assert not report.ok, f"the gate does not catch a broken {rule}"
    assert any(rule in problem for problem in report.problems), (
        f"the gate does not name the {rule} rule: {report.problems}"
    )


def test_the_exemption_comes_from_the_module_and_not_from_the_gate():
    """A module that declares that it makes a sound of its own is exempt.

    The probe makes a signal out of nothing and declares an infinite tail:
    the gate has to let it pass, because the exemption is the declaration of
    the module and not a list of module codes inside the gate.
    """
    broken = _Broken(gate.SAMPLE_RATE, gate.CHANNELS)
    broken.mode = "voice"

    exempt = _report_for(broken)
    assert any("silence" in problem for problem in exempt.problems), (
        "the gate has to catch a module that makes a sound out of silence"
    )

    broken.mode = "own"
    declared = _report_for(broken)
    assert not any("silence" in problem for problem in declared.problems), (
        "a module that declares an infinite tail is exempt from the silence "
        f"rule: {declared.problems}"
    )
