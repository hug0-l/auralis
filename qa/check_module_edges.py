"""
qa.check_module_edges -- the gate against the edges of every module.

The rule comes from the robustness study of the two foreign products: at the
edges of the material a module has to hold five rules.

    finite   the output holds no NaN and no infinity, and the module does
             not raise through the call boundary.
    length   the output is as long as the input.  The one exception is the
             module that changes the rate on purpose: it says so, and the
             length follows the rate it declares.
    tail     the output goes quiet within the tail the module declares, so
             that a host which waits for that tail does not cut the sound
             off and click.
    silence  a silent input comes back as silence.  The one exception is the
             module that makes a sound of its own -- a generator, a bed of
             ambience -- and it says so by declaring an infinite tail.
    material the level of the output does not depend on how much material the
             module has seen.  A module that has not heard enough material
             yet is not judged by it: it says so through the latency it
             declares or through the settling time it declares.

The two rules that came last -- the silence and the length of the material --
carry their number, their unit and the way they are measured in
:mod:`qa.edge_rules`, and the gate reads them from there, so that the rule
and the gate cannot drift apart.

The edges are the five signals of the study: silence, a full scale sine, a
single impulse, a signal at the clipping ceiling and a signal that is
shorter than the analysis window of the module.  A sixth edge is added: a
signal of one sample, the shortest signal there is.

The tail is measured the way the independent host of ``qa/vst3host`` measures
it: a tone goes in, silence follows, and the ring-out is the number of
samples the output stays above -60 dBFS after the input stopped.  Three
excitations are used -- the tone of the host, the full scale sine and the
signal at the clipping ceiling -- and the longest ring-out of the three is
the number the module has to live with, because a module has to hold its
contract for every input and not only for the one the host happens to
feed.  The number the host measures is in the table as well, so that the
declaration of a module and the finding of the host can be read side by
side.

How much longer than the declaration the ring-out may be is stated in
:data:`TAIL_ALLOWANCE`: 64 samples, which is 1.33 ms at 48 kHz.  That is the
same allowance the product gives a latency (the host uses it as
``Tolerance::kLatency``, the tail gate of ``tests/test_tail.py`` as
``DELAY_TOLERANCE``); below it the misalignment is inaudible.

Run it::

    python qa/check_module_edges.py               # every module
    python qa/check_module_edges.py AUR_DECLICK   # one module
    python qa/check_module_edges.py --json        # machine readable

Exit code 0 when every module holds every rule, 1 otherwise.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurcore import registry  # noqa: E402
from aurcore.ceiling import peak_of  # noqa: E402
from aurcore.params import TAIL_INFINITE  # noqa: E402

try:  # the gate is run as a script from the qa directory
    from edge_rules import (  # noqa: E402
        MATERIAL_LENGTHS,
        MATERIAL_REFERENCE_FRAMES,
        MATERIAL_RULE,
        SILENCE_RULE,
    )
except ImportError:  # pragma: no cover - imported as a package module
    from qa.edge_rules import (  # noqa: E402
        MATERIAL_LENGTHS,
        MATERIAL_REFERENCE_FRAMES,
        MATERIAL_RULE,
        SILENCE_RULE,
    )


def _load_performance_harness():
    """Return the harness of the performance directory, loaded by path.

    Several directories of the project hold a module called ``harness``, and
    two of them in the same session would take each other out of the module
    cache.  The meter of the load therefore lives in its own module, and it
    is the one the performance measurements use, so that the number of the
    load means the same thing everywhere.
    """
    path = ROOT / "qa" / "performance" / "harness.py"
    spec = importlib.util.spec_from_file_location("auralis_performance_harness", path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


#: The load meter of the performance harness.
LoadMonitor = _load_performance_harness().LoadMonitor

# ---------------------------------------------------------------------------
# the shape of the measurement
# ---------------------------------------------------------------------------
#: The sample rate every module is driven at.
SAMPLE_RATE = 48_000

#: The channel count every module is driven at.
CHANNELS = 2

#: The length of the edge signals, in samples: 0.25 s at 48 kHz.  It is
#: three times the longest buffer of the core (4096 samples), so a module
#: that fills its buffer and then answers is seen at work.
EDGE_FRAMES = 12_000

#: The length of the "very short" signal, in samples.  It is shorter than
#: every analysis window of the core (2048 samples), so a module that waits
#: for a full window never sees it.
SHORT_FRAMES = 64

#: The block size the measurement is driven with, in samples.  It is the
#: block size of the independent host.
BLOCK = 256

#: How many samples of silence are fed before the excitation, in samples.
#: The core buffers up to 4096 samples, so the measurement means nothing
#: before the buffer is full.  The host uses the same warm up.
WARMUP_FRAMES = 4096

#: How long the excitation lasts, in samples: the 32 blocks of the host.
EXCITATION_FRAMES = 8192

#: How long the ring-out is watched, in samples.  The longest ring-out of
#: the family is 3286 samples, so this window holds it with room to spare.
WATCH_FRAMES = 8192

#: The level below which a sample counts as silence when the ring-out is
#: measured: -60 dBFS, which is below what any system shows.  The host uses
#: the same one.
SILENCE = 1.0e-3

#: How much longer than the declaration the ring-out may be, in samples.
#: 64 samples are 1.33 ms at 48 kHz: below that the misalignment is
#: inaudible.  The host allows the same for a latency.
TAIL_ALLOWANCE = 64

#: The tone the host feeds in, in hertz, and its amplitude (-6 dBFS).
TONE_HZ = 1000.0
TONE_AMPLITUDE = 0.5

#: The frequency of the edge signals.  It is not a multiple of the bin width
#: of the analysis, so the signal has no bin that sits exactly on it.
EDGE_HZ = 997.0


# ---------------------------------------------------------------------------
# the edges
# ---------------------------------------------------------------------------
def _stereo(signal: np.ndarray) -> np.ndarray:
    """Return the signal as an identical stereo pair."""
    data = np.asarray(signal, dtype=np.float64)
    return np.repeat(data[:, None], CHANNELS, axis=1)


def _tone(frames: int, hz: float, amplitude: float) -> np.ndarray:
    """Return a sine of the given peak amplitude."""
    t = np.arange(frames, dtype=np.float64) / float(SAMPLE_RATE)
    return amplitude * np.sin(2.0 * np.pi * float(hz) * t)


def edge_signals() -> dict[str, np.ndarray]:
    """Return the edge signals of the gate, by name.

    The five edges of the study and the shortest signal there is:

    ``silence``
        nothing at all.  A module that adds its own noise, or that divides
        by the level of the signal, shows it here.
    ``full_scale``
        a sine at 0 dBFS.  The largest signal the format can hold: a
        module that adds gain, or that normalises upwards, shows it here.
    ``impulse``
        one sample of full scale and silence around it.  A module with a
        filter that rings, or a detector that divides by the sample it
        found, shows it here.
    ``ceiling``
        a sine driven into the rails, so the samples sit at the clipping
        ceiling.  The hardest input there is.
    ``short``
        64 samples of a sine: shorter than every analysis window of the
        core, so a module that waits for a full window never sees it.
    ``one_sample``
        one sample of full scale: the shortest signal there is.

    Returns
    -------
    dict[str, numpy.ndarray]
        The signals, shape ``(n_samples, 2)``.
    """
    impulse = np.zeros(EDGE_FRAMES, dtype=np.float64)
    impulse[0] = 1.0

    one = np.zeros(1, dtype=np.float64)
    one[0] = 1.0

    ceiling = np.clip(3.0 * _tone(EDGE_FRAMES, EDGE_HZ, 1.0), -1.0, 1.0)

    return {
        "silence": _stereo(np.zeros(EDGE_FRAMES, dtype=np.float64)),
        "full_scale": _stereo(_tone(EDGE_FRAMES, EDGE_HZ, 1.0)),
        "impulse": _stereo(impulse),
        "ceiling": _stereo(ceiling),
        "short": _stereo(_tone(SHORT_FRAMES, EDGE_HZ, 1.0)),
        "one_sample": _stereo(one),
    }


#: The names of the edges, in the order they are reported.
EDGE_NAMES = ("silence", "full_scale", "impulse", "ceiling", "short", "one_sample")


def excitations() -> dict[str, np.ndarray]:
    """Return the signals the ring-out is measured with.

    Three excitations, and the longest ring-out of the three is the number
    the module has to live with:

    ``tone``
        the tone of the independent host, at -6 dBFS.  The declaration of a
        module is a contract with the host, and the host measures the tail
        with this signal, so it is the number the host compares the
        declaration against.
    ``full``
        the full scale sine of the study, at 0 dBFS: the loudest signal
        that fits in the format.
    ``ceiling``
        the signal at the clipping ceiling: the samples sit at the rails.
        It has the most energy the edges offer and it drives a module
        harder than the tone does.

    Returns
    -------
    dict[str, numpy.ndarray]
        The excitations, shape ``(EXCITATION_FRAMES, 2)``.
    """
    return {
        "tone": _stereo(_tone(EXCITATION_FRAMES, TONE_HZ, TONE_AMPLITUDE)),
        "full": _stereo(_tone(EXCITATION_FRAMES, TONE_HZ, 1.0)),
        "ceiling": _stereo(
            np.clip(3.0 * _tone(EXCITATION_FRAMES, TONE_HZ, 1.0), -1.0, 1.0)
        ),
    }


# ---------------------------------------------------------------------------
# driving a module
# ---------------------------------------------------------------------------
def expected_length(module, frames: int) -> int:
    """Return the number of samples the module has to hand back.

    A module that keeps the rate of the signal hands back the number of
    samples it was given.  The module that changes the rate on purpose says
    so, and the number of samples follows the rate it declares.

    Parameters
    ----------
    module:
        The module.
    frames:
        The number of samples that were handed in.

    Returns
    -------
    int
        The number of samples the module owes.
    """
    rate = getattr(module, "output_sample_rate", None)
    if rate:
        try:
            return int(round(float(frames) * float(rate) / float(SAMPLE_RATE)))
        except (TypeError, ValueError):
            return int(frames)
    return int(frames)


def feed(module, signal: np.ndarray, block: int = BLOCK) -> np.ndarray:
    """Drive a module with a signal, one block at a time.

    The signal is fed the way a host feeds it: in blocks of ``block``
    samples, and the last block is padded with silence.

    Parameters
    ----------
    module:
        The module.
    signal:
        The signal, shape ``(n_samples, n_channels)``.
    block:
        The block size.

    Returns
    -------
    numpy.ndarray
        What the module handed back.
    """
    out: list[np.ndarray] = []
    total = int(signal.shape[0])
    for start in range(0, total, block):
        piece = signal[start : start + block]
        if piece.shape[0] < block:
            piece = np.pad(piece, ((0, block - piece.shape[0]), (0, 0)))
        out.append(np.asarray(module.process(piece), dtype=np.float64))
    if not out:
        return np.zeros((0, CHANNELS), dtype=np.float64)
    return np.concatenate(out, axis=0)


def measure_ring_out(module, excitation: np.ndarray) -> int:
    """Return how long the output keeps sounding after the input stopped.

    The measurement is the one of the independent host: a warm up of
    silence, then the excitation, then silence, and the ring-out is the
    number of samples the output stays above -60 dBFS after the excitation
    ended.

    Parameters
    ----------
    module:
        The module.
    excitation:
        The excitation, shape ``(n_samples, n_channels)``.

    Returns
    -------
    int
        The ring-out in samples; zero when the module goes quiet at once.
    """
    stream = np.concatenate(
        [
            _stereo(np.zeros(WARMUP_FRAMES, dtype=np.float64)),
            np.asarray(excitation, dtype=np.float64),
            _stereo(np.zeros(WATCH_FRAMES, dtype=np.float64)),
        ]
    )
    answer = feed(module, stream)
    offset = WARMUP_FRAMES + int(excitation.shape[0])

    last = -1
    for index in range(offset, answer.shape[0]):
        if float(np.max(np.abs(answer[index]))) >= SILENCE:
            last = index
    if last < 0:
        return 0
    return last - offset + 1


# ---------------------------------------------------------------------------
# the silence and the length of the material
# ---------------------------------------------------------------------------
def makes_own_sound(module) -> bool:
    """Return True when the module declares that it makes a sound of its own.

    The declaration is the one the tree already has: **a module that never
    goes quiet says so with an infinite tail**
    (:data:`aurcore.params.TAIL_INFINITE`).  The generator makes a tone and
    the ambience match lays a bed of its own under the recording, and both
    declare it.

    The gate holds no list of module codes of its own: the declaration is read
    from the module, so a module that joins the family later is judged by
    what it says and not by what the gate happened to know when it was
    written.  A list in the gate drifts.

    Parameters
    ----------
    module:
        The module.

    Returns
    -------
    bool
        True when the module is exempt from the rules of the material.
    """
    return int(getattr(module, "tail_samples", 0)) == TAIL_INFINITE


def settling_frames(module) -> int:
    """Return how many frames of material the module needs before it is ready.

    A module that estimates over time has to hear the material before it can
    judge it, and a module that says so is not judged by the material it has
    not heard yet.  The number is the module's own declaration
    (:attr:`aurcore.modules.base.Module.settling_frames`); zero means the
    module is ready at once.

    Parameters
    ----------
    module:
        The module.

    Returns
    -------
    int
        The number of frames, never below zero.
    """
    try:
        return max(0, int(getattr(module, "settling_frames", 0)))
    except (TypeError, ValueError):
        return 0


def measure_silence(code: str) -> float:
    """Return the peak of the output of the module when the input is silence.

    Parameters
    ----------
    code:
        The module code.

    Returns
    -------
    float
        The peak of the output, in signal units.
    """
    signal = edge_signals()["silence"]
    module = registry.create(code, SAMPLE_RATE, CHANNELS)
    out = np.asarray(module.process(signal), dtype=np.float64)
    return peak_of(out)


def measure_material_level(code: str, frames: int) -> float:
    """Return the level of the output after a material of that many frames.

    The material is a full scale tone, and the level is the peak of the
    output in dBFS.  The module is driven with the whole material in one
    call, the way the study drove it.

    Parameters
    ----------
    code:
        The module code.
    frames:
        The length of the material, in frames.

    Returns
    -------
    float
        The level in dBFS; ``-inf`` when the module answered with silence.
    """
    signal = _stereo(_tone(frames, EDGE_HZ, 1.0))
    module = registry.create(code, SAMPLE_RATE, CHANNELS)
    out = np.asarray(module.process(signal), dtype=np.float64)
    peak = peak_of(out)
    if peak <= 0.0:
        return float("-inf")
    return 20.0 * math.log10(peak)


def level_difference_db(first: float, second: float) -> float:
    """Return the distance between two levels, in dB.

    Two levels that are both silence are the same level; a level of silence
    and a level that is not are as far apart as the rule can measure.

    Parameters
    ----------
    first:
        The first level, in dBFS.
    second:
        The second level, in dBFS.

    Returns
    -------
    float
        The distance in dB.
    """
    if first == second:
        return 0.0
    if first == float("-inf") or second == float("-inf"):
        return float("inf")
    return abs(first - second)


def format_level(level: float) -> str:
    """Return a level as it is written in the report.

    Parameters
    ----------
    level:
        The level, in dBFS.

    Returns
    -------
    str
        The level, two decimals, or ``-inf`` for silence.
    """
    if level == float("-inf"):
        return "-inf"
    return f"{level:.2f}"


# ---------------------------------------------------------------------------
# the rules
# ---------------------------------------------------------------------------
@dataclass
class ModuleReport:
    """What the gate measured on one module.

    Attributes
    ----------
    code:
        The module code.
    latency:
        The latency the module declares, in samples.
    tail:
        The tail the module declares, in samples.
    rings:
        The ring-out of every excitation, in samples.
    problems:
        The rules the module broke, one line each.
    notes:
        What the gate saw that is not a broken rule, one line each.
    """

    code: str
    latency: int = 0
    tail: int = 0
    rings: dict[str, int] = field(default_factory=dict)
    silence_peak: float = 0.0
    material: "MaterialReport" = field(default_factory=lambda: MaterialReport())
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def limit(self) -> int:
        """The number of samples the module is allowed to sound for."""
        return int(self.latency) + int(self.tail)

    @property
    def ring_out(self) -> int:
        """The longest ring-out the gate measured, in samples."""
        return max(self.rings.values()) if self.rings else 0

    @property
    def excess(self) -> int:
        """How much longer than the declaration the module sounds."""
        return int(self.ring_out) - self.limit

    @property
    def ok(self) -> bool:
        """True when the module holds every rule."""
        return not self.problems

    def to_dict(self) -> dict:
        """Return the JSON safe form of the report."""
        return {
            "code": self.code,
            "latency": int(self.latency),
            "tail": int(self.tail),
            "limit": self.limit,
            "ring_out": int(self.ring_out),
            "excess": self.excess,
            "rings": {name: int(value) for name, value in self.rings.items()},
            "silence_peak": float(self.silence_peak),
            "material": self.material.to_dict(),
            "ok": self.ok,
            "problems": list(self.problems),
            "notes": list(self.notes),
        }


@dataclass
class MaterialReport:
    """What the gate measured about the length of the material.

    Attributes
    ----------
    levels:
        The level of the output after a material of that many frames, in dBFS.
    reference:
        The level of the output after the long material, in dBFS.
    judged:
        The lengths the rule was measured at.
    exempt:
        True when the module declares that it makes a sound of its own.
    problems:
        The rules the module broke, one line each.
    notes:
        What the gate saw that is not a broken rule, one line each.
    """

    levels: dict[int, float] = field(default_factory=dict)
    reference: float = float("-inf")
    judged: list[int] = field(default_factory=list)
    exempt: bool = False
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def spread_db(self) -> float:
        """The largest distance between a judged level and the long one."""
        worst = 0.0
        for frames in self.judged:
            distance = level_difference_db(
                self.levels.get(frames, self.reference), self.reference
            )
            if distance > worst:
                worst = distance
        return worst

    def to_dict(self) -> dict:
        """Return the JSON safe form of the report."""
        return {
            "levels": {str(frames): float(level)
                       for frames, level in sorted(self.levels.items())},
            "reference": float(self.reference),
            "judged": [int(frames) for frames in self.judged],
            "exempt": bool(self.exempt),
            "spread_db": float(self.spread_db),
            "problems": list(self.problems),
            "notes": list(self.notes),
        }


def check_silence(code: str) -> tuple[float, bool, list[str]]:
    """Return what the module does with silence.

    Parameters
    ----------
    code:
        The module code.

    Returns
    -------
    tuple[float, bool, list[str]]
        The peak of the output, whether the module is exempt (it declares
        that it makes a sound of its own) and the rules it breaks.
    """
    module = registry.create(code, SAMPLE_RATE, CHANNELS)
    if makes_own_sound(module):
        return 0.0, True, []
    try:
        peak = measure_silence(code)
    except Exception as exc:  # noqa: BLE001 - the finding is the report
        return 0.0, False, [
            f"silence: the module raised {type(exc).__name__}: {exc}"
        ]
    problems: list[str] = []
    if peak != peak:
        problems.append(
            "silence: the output of the module on silence is not a number"
        )
    elif peak > SILENCE_RULE.limit:
        problems.append(
            f"silence: a silent input comes back at {peak:.6e} "
            f"({20.0 * math.log10(max(peak, 1e-300)):.1f} dBFS), the rule "
            f"allows {SILENCE_RULE.limit:.1e}"
        )
    return peak, False, problems


def check_material(code: str) -> MaterialReport:
    """Return what the module does with the length of the material.

    A module that has not heard enough material yet is not judged by it: the
    material is shorter than the latency the module declares, or shorter than
    the settling time it declares.  A module that declares an infinite tail
    makes a sound of its own, and the level of its output is not the level of
    the material.

    Parameters
    ----------
    code:
        The module code.

    Returns
    -------
    MaterialReport
        What was measured.
    """
    report = MaterialReport()
    module = registry.create(code, SAMPLE_RATE, CHANNELS)
    if makes_own_sound(module):
        report.exempt = True
        report.notes.append(
            "the module declares an infinite tail: it makes a sound of its "
            "own, so the level of its output is not the level of the material"
        )
        return report

    latency = int(getattr(module, "latency_samples", 0))
    settling = settling_frames(module)

    if settling > MATERIAL_REFERENCE_FRAMES:
        report.problems.append(
            f"material: the module declares a settling time of {settling} "
            f"frames and is not ready after {MATERIAL_REFERENCE_FRAMES} "
            "frames of material, so the rule cannot be measured"
        )
        return report

    try:
        report.reference = measure_material_level(code, MATERIAL_REFERENCE_FRAMES)
    except Exception as exc:  # noqa: BLE001 - the finding is the report
        report.problems.append(
            f"material: the level of the long material could not be measured: "
            f"{type(exc).__name__}: {exc}"
        )
        return report

    for frames in MATERIAL_LENGTHS:
        try:
            level = measure_material_level(code, frames)
        except Exception as exc:  # noqa: BLE001 - the finding is the report
            report.problems.append(
                f"material: {frames} frames raised {type(exc).__name__}: {exc}"
            )
            continue
        report.levels[frames] = level
        if level != level:
            report.problems.append(
                f"material: the level of the output after {frames} frames is "
                "not a number"
            )
            continue
        if frames <= latency:
            report.notes.append(
                f"{frames} frames: the module declares a latency of {latency} "
                "and has not heard enough material yet"
            )
            continue
        if frames < settling:
            report.notes.append(
                f"{frames} frames: the module declares a settling time of "
                f"{settling} frames and is still settling"
            )
            continue
        report.judged.append(frames)
        distance = level_difference_db(level, report.reference)
        if distance > MATERIAL_RULE.limit:
            report.problems.append(
                f"material: the output is {distance:.2f} dB away from the "
                f"level it holds after {MATERIAL_REFERENCE_FRAMES} frames "
                f"({format_level(level)} dBFS against "
                f"{format_level(report.reference)} dBFS) when it is given "
                f"{frames} frames of material, the rule allows "
                f"{MATERIAL_RULE.limit:.2f} dB"
            )
    return report


def check_edges(code: str) -> list[str]:
    """Return the rules the module breaks at the edges.

    The finite rule and the length rule are checked here, on the six edge
    signals of :func:`edge_signals`.

    Parameters
    ----------
    code:
        The module code.

    Returns
    -------
    list[str]
        One line per broken rule; empty when the module holds them all.
    """
    problems: list[str] = []
    signals = edge_signals()
    for name in EDGE_NAMES:
        signal = signals[name]
        module = registry.create(code, SAMPLE_RATE, CHANNELS)
        try:
            out = np.asarray(module.process(signal), dtype=np.float64)
        except Exception as exc:  # noqa: BLE001 - the finding is the report
            problems.append(f"finite: {name} raised {type(exc).__name__}: {exc}")
            continue

        if out.ndim != 2:
            problems.append(f"finite: {name} the output is {out.ndim} dimensional")
            continue
        if out.shape[1] != CHANNELS:
            problems.append(
                f"length: {name} the output has {out.shape[1]} channels"
            )
            continue

        broken = int(np.count_nonzero(~np.isfinite(out)))
        if broken:
            problems.append(
                f"finite: {name} {broken} of {out.size} samples are not finite"
            )
            continue

        wanted = expected_length(module, int(signal.shape[0]))
        if int(out.shape[0]) != wanted:
            problems.append(
                f"length: {name} the output is {out.shape[0]} samples long, "
                f"the input was {signal.shape[0]}"
            )
    return problems


def check_tail(code: str) -> tuple[dict[str, int], int, int]:
    """Return the ring-out of the module and what it declares.

    Parameters
    ----------
    code:
        The module code.

    Returns
    -------
    tuple[dict[str, int], int, int]
        The ring-out of every excitation in samples, the declared latency
        and the declared tail.
    """
    module = registry.create(code, SAMPLE_RATE, CHANNELS)
    latency = int(getattr(module, "latency_samples", 0))
    tail = int(getattr(module, "tail_samples", 0))

    rings: dict[str, int] = {}
    for name, excitation in excitations().items():
        fresh = registry.create(code, SAMPLE_RATE, CHANNELS)
        rings[name] = measure_ring_out(fresh, excitation)
    return rings, latency, tail


def check_module(code: str) -> ModuleReport:
    """Drive one module over the edges and report what it broke.

    Parameters
    ----------
    code:
        The module code.

    Returns
    -------
    ModuleReport
        The report.
    """
    report = ModuleReport(code=code)

    try:
        report.problems.extend(check_edges(code))
    except Exception as exc:  # noqa: BLE001 - the finding is the report
        report.problems.append(f"finite: raised {type(exc).__name__}: {exc}")

    # -- the silence --------------------------------------------------------
    try:
        peak, exempt, problems = check_silence(code)
    except Exception as exc:  # noqa: BLE001 - the finding is the report
        peak, exempt, problems = 0.0, False, [
            f"silence: the measurement failed: {type(exc).__name__}: {exc}"
        ]
    report.silence_peak = peak
    report.problems.extend(problems)
    if exempt:
        report.notes.append(
            "the module declares an infinite tail: it makes a sound of its "
            "own, so the silence rule does not hold it"
        )

    # -- the length of the material ----------------------------------------
    try:
        material = check_material(code)
    except Exception as exc:  # noqa: BLE001 - the finding is the report
        material = MaterialReport()
        material.problems.append(
            f"material: the measurement failed: {type(exc).__name__}: {exc}"
        )
    report.material = material
    report.problems.extend(material.problems)
    report.notes.extend(material.notes)

    try:
        rings, latency, tail = check_tail(code)
    except Exception as exc:  # noqa: BLE001 - the finding is the report
        report.problems.append(f"tail: the ring-out could not be measured: {exc}")
        return report

    report.rings = {name: int(value) for name, value in rings.items()}
    report.latency = latency
    report.tail = tail

    if tail == TAIL_INFINITE:
        # A module that never goes quiet says so; there is no tail to hold.
        return report

    if report.excess > TAIL_ALLOWANCE:
        report.problems.append(
            f"tail: the module sounds for {report.ring_out} samples after its "
            f"input stopped, but it declares a latency of {latency} and a "
            f"tail of {tail} ({report.excess} samples too many)"
        )
    elif report.excess > 0:
        report.notes.append(
            f"the module sounds for {report.excess} samples more than it "
            f"declares, which is inside the allowance of {TAIL_ALLOWANCE}"
        )
    return report


def module_codes() -> list[str]:
    """Return the code of every registered module."""
    return sorted(info.code for info in registry.all())


# ---------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------
def run(codes: list[str]) -> list[ModuleReport]:
    """Run the gate over the given modules.

    Parameters
    ----------
    codes:
        The module codes.

    Returns
    -------
    list[ModuleReport]
        One report per module.
    """
    return [check_module(code) for code in codes]


def render(reports: list[ModuleReport]) -> str:
    """Render the reports as a table.

    Parameters
    ----------
    reports:
        The reports.

    Returns
    -------
    str
        The table.
    """
    lines = [
        f"{'code':<22}{'lat':>7}{'tail':>8}{'limit':>9}{'ring':>8}{'over':>8}"
        f"{'silence':>11}{'material':>9}"
        + "".join(f"{name:>10}" for name in excitations())
        + "  rule",
    ]
    for report in reports:
        infinite = report.tail == TAIL_INFINITE
        tail = "inf" if infinite else str(report.tail)
        limit = "inf" if infinite else str(report.limit)
        over = "-" if infinite else str(report.excess)
        state = "ok" if report.ok else "BROKEN"
        if report.material.exempt:
            material = "exempt"
        elif not report.material.judged:
            material = "none"
        else:
            material = f"{report.material.spread_db:.2f}"
        lines.append(
            f"{report.code:<22}{report.latency:>7}{tail:>8}{limit:>9}"
            f"{report.ring_out:>8}{over:>8}"
            f"{report.silence_peak:>11.2e}{material:>9}"
            + "".join(f"{report.rings.get(name, 0):>10}" for name in excitations())
            + f"  {state}"
        )
        for problem in report.problems:
            lines.append(f"{'':<22}  {problem}")
        for note in report.notes:
            lines.append(f"{'':<22}  note: {note}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Run the gate and report the result."""
    parser = argparse.ArgumentParser(description="The gate against the edges of a module")
    parser.add_argument("codes", nargs="*", help="the modules to check (default: all)")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="write the report as JSON")
    args = parser.parse_args(argv)

    codes = args.codes or module_codes()

    started = time.perf_counter()
    with LoadMonitor(interval=0.25) as monitor:
        reports = run(codes)
    elapsed = time.perf_counter() - started

    broken = [report for report in reports if not report.ok]
    load = monitor.mean_percent

    if args.as_json:
        print(json.dumps({
            "passed": not broken,
            "modules": len(reports),
            "broken": len(broken),
            "seconds": round(elapsed, 2),
            "load_percent": None if load != load else round(load, 1),
            "reports": [report.to_dict() for report in reports],
        }, indent=1))
        return 1 if broken else 0

    print("=" * 78)
    print(" AURALIS MODULE EDGES")
    print(f" {len(reports)} module(s), {len(EDGE_NAMES)} edge(s), 5 rule(s)")
    print("=" * 78)
    print()
    print(render(reports))
    print()
    print(f"  time: {elapsed:.1f} s, machine load: "
          f"{'unknown' if load != load else f'{load:.0f} %'}")
    print()
    if broken:
        print(f"RESULT: FAIL ({len(broken)} module(s) broke a rule)")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
