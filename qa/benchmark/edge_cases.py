"""
qa.benchmark.edge_cases — the edge contract, as plain constants.

The **edge contract** is the set of rules a module has to hold at the limits
of the signal: silence, a full scale tone, an impulse at the first frame and
in the middle, a hard clipped signal, a full scale offset, a denormal, a
square at the ceiling, and inputs of one, two, seven, sixty four and five
hundred and eleven frames.  The rules, the measurements behind them and the
state of the family are in ``docs/EDGE_CONTRACT.md``.

The shape is the shape of the other constants files of this directory
(``qa/benchmark/reference.py``, ``qa/benchmark/reference_voice.py`` and
``qa/benchmark/reference_model.py``): a number is a number, it carries no
name, no code and no parameter of the product it was measured on, and what
travels with it is the **case** it belongs to, the **unit** it is in, the
**way it was measured** and the **series** it was measured in.

The series
----------
``edge_plug_in``
    the reference that is a plug-in module, measured at the extremes of the
    signal: it holds whole blocks of its own, so its latency moves with the
    block size of the host.
``edge_installed``
    the reference that is installed from an installer: its latency is fixed
    and does not move with the block size of the host.
``ours``
    not a reference: the measurements of our own family, filed under their
    own series so that they are never mistaken for one.

**A reference is a target, not a contract.**  It was measured on somebody
else's build, on somebody else's machine, so the benchmark reports the gap
and does not fail on it.  The gate that holds *our* side to the rules is
``qa/check_module_edges.py`` (finite, length and tail) and the rule of the
ceiling lives in ``aurcore/ceiling.py``.

How the numbers were taken
--------------------------
* the numbers of the two references were measured by the study, through the
  study's own host, at 48 kHz in stereo, and the render was measured with
  the same code, so the two sides of the table are measured with one
  instrument;
* the numbers of ours were measured on the tree of 2026-10-21 (23:30) with
  ``qa/measure_edge_contract.py``: every module of the registry was driven
  over the edge signals of the study in one call to ``process()``, and
  every number of ours is the **worst of the family**, so that it is the
  number a rule has to be held to;
* the ring-out is counted at two floors, because they are not the same
  measurement: the -180 dBFS floor the study used, and the -60 dBFS floor
  the gate of the tree uses (``qa/check_module_edges.py``).  The floor of a
  record is part of its method, and the two numbers of a family are two
  numbers, not one number and a mistake.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "CASES",
    "EVERY_CASE",
    "Edge",
    "EDGES",
    "EDGE_BY_NAME",
    "METRICS",
    "METRIC_BY_NAME",
    "SERIES",
    "edge_for",
    "edges_for",
]


@dataclass(frozen=True)
class Metric:
    """One number of the table and what a good number of it looks like."""

    name: str
    unit: str
    higher_is_better: bool
    note: str = ""


#: Every metric the edge contract is measured with.
METRICS: tuple[Metric, ...] = (
    Metric(
        name="edge_output_frames",
        unit="frames",
        higher_is_better=False,
        note=(
            "How many frames the output has when the input is one frame "
            "long: the answer must be one frame.  The length of the output "
            "is the first thing a host checks and the first thing a caller "
            "trusts."
        ),
    ),
    Metric(
        name="edge_non_finite",
        unit="samples",
        higher_is_better=False,
        note=(
            "How many output samples are not a number at the edges: a full "
            "scale tone, a hard clipped signal, a full scale offset, a "
            "denormal and a one frame input.  The answer must be zero."
        ),
    ),
    Metric(
        name="edge_silence_out_peak",
        unit="1",
        higher_is_better=False,
        note=(
            "The peak of the output when the input is silence.  The answer "
            "must be zero: a module that makes a sound of its own out of "
            "nothing has a job to declare, and the two of ours that have "
            "one say so with an infinite tail."
        ),
    ),
    Metric(
        name="edge_ring_out_frames",
        unit="frames",
        higher_is_better=False,
        note=(
            "How many frames the module keeps making a sound after the "
            "input stopped, measured on a tone that ends and is followed by "
            "silence.  The answer must not be larger than the latency and "
            "the tail the module declares, or a host that cuts the stream "
            "cuts sound off."
        ),
    ),
    Metric(
        name="edge_ceiling_square_peak",
        unit="1",
        higher_is_better=False,
        note=(
            "The peak of the output when the input is a square at the "
            "ceiling, alternating between +1.0 and -1.0.  The answer must "
            "not be much above 1.0: the reference lets the ceiling through "
            "at 1.000729, and a module that makes it louder is not holding "
            "the contract."
        ),
    ),
    Metric(
        name="edge_ceiling_square_samples_over",
        unit="samples",
        higher_is_better=False,
        note=(
            "How many output samples are outside [-1, 1] when the input is a "
            "square at the ceiling.  The answer must be zero."
        ),
    ),
    Metric(
        name="edge_full_scale_tone_peak",
        unit="1",
        higher_is_better=False,
        note=(
            "The peak of the output when the input is a full scale sine.  "
            "It is the case that says whether the module adds level."
        ),
    ),
    Metric(
        name="edge_latency_samples",
        unit="samples",
        higher_is_better=False,
        note=(
            "How many samples the output lags the input.  A module that "
            "holds whole blocks of its own moves this number with the block "
            "size of the host, and a host has to be told which one it gets."
        ),
    ),
    Metric(
        name="edge_declared_tail_frames",
        unit="frames",
        higher_is_better=False,
        note=(
            "How many frames the module declares it keeps making a sound "
            "after the input stopped.  The number is a promise, and the "
            "ring-out is the finding."
        ),
    ),
    Metric(
        name="edge_declared_limit_frames",
        unit="frames",
        higher_is_better=False,
        note=(
            "The limit the module declares: its latency plus its tail.  The "
            "ring-out may not be larger than this number."
        ),
    ),
    Metric(
        name="edge_gain_db",
        unit="dB",
        higher_is_better=False,
        note=(
            "The gain the module applies to the signal it leaves alone.  A "
            "gain above zero is why a signal at the ceiling can come out "
            "above it."
        ),
    ),
)

#: The metrics, keyed by name.
METRIC_BY_NAME: dict[str, Metric] = {metric.name: metric for metric in METRICS}

#: The series the numbers are filed under, keyed by their name.
SERIES: dict[str, str] = {
    "edge_plug_in": (
        "the reference that is a plug-in module, measured at the extremes of "
        "the signal: it holds whole blocks of its own, so its latency moves "
        "with the block size of the host"
    ),
    "edge_installed": (
        "the reference that is installed from an installer: its latency is "
        "fixed and does not move with the block size of the host"
    ),
    "ours": (
        "not a reference: the measurements of our own family, filed under "
        "their own series so that they are never mistaken for one"
    ),
}

#: The case that means "every case": a number that is a property of the
#: product and not of the signal it is measured on.
EVERY_CASE = "*"

#: The cases the numbers belong to, and what each of them is.
CASES: dict[str, str] = {
    "one_frame": (
        "the input of one frame: the shortest signal there is, and the case "
        "that says whether a module keeps the length of what it is given"
    ),
    "silence": "the input of nothing at all",
    "full_scale_tone": "a sine at the ceiling, 1 kHz",
    "ceiling_square": (
        "a square at the ceiling: it alternates between +1.0 and -1.0, so "
        "every sample of it is at the rails"
    ),
    "ring_out": "a tone that ends and is followed by silence",
    "passband": (
        "the band the module leaves alone: the gain it applies to a tone "
        "inside it"
    ),
    EVERY_CASE: (
        "every case: the number is a property of the product and not of the "
        "signal it is measured on"
    ),
}


@dataclass(frozen=True)
class Edge:
    """One number of the edge contract, with everything needed to read it."""

    name: str
    series: str
    case: str
    metric: str
    value: float
    unit: str
    method: str
    setting: str = ""

    def as_dict(self) -> dict:
        """The number as a dictionary, for the JSON result."""
        return {
            "name": self.name,
            "series": self.series,
            "case": self.case,
            "metric": self.metric,
            "value": self.value,
            "unit": self.unit,
            "method": self.method,
            "setting": self.setting,
        }


# ---------------------------------------------------------------------------
# how the numbers were taken
# ---------------------------------------------------------------------------
#: How the reference that is a plug-in module was measured.
#:
#: The instrument is named, because the study has more than one host and only
#: one of them moves a parameter of its own and never puts it back (see
#: docs/BENCHMARK_NOTES.md, section 5.1).  This is the study's edge host: it
#: sets only the parameters it was asked for.
_PLUG_IN_METHOD = (
    "measured by the study on the reference that is a plug-in module, through "
    "the study's edge host, which sets only the parameters it was asked for, "
    "at 48 kHz in stereo, and the render was measured "
    "with the same code, so the two sides of the table are measured with one "
    "instrument"
)

#: How the reference that is installed from an installer was measured.
_INSTALLED_METHOD = (
    "measured by the study on the reference that is installed from an "
    "installer, through the study's edge host, which sets only the parameters "
    "it was asked for, at 48 kHz in stereo, and the "
    "render was measured with the same code"
)

#: How our own side was measured.
_OURS_METHOD = (
    "measured on our own modules with the edge contract instrument of the "
    "project (qa/measure_edge_contract.py) on the tree of 2026-10-21 at "
    "23:30: every module of the registry was driven over the edge signals of "
    "the study in one call to process(), at 48 kHz in stereo, and the number "
    "is the worst of the family.  The gate of the tree that holds the same "
    "rules is qa/check_module_edges.py (finite, length and tail) and the "
    "rule of the ceiling lives in aurcore/ceiling.py"
)

#: How the ring-out of the references was taken.
_REFERENCE_RING_METHOD = (
    "measured by the study on the reference, through the study's edge host, "
    "which sets only the parameters it was asked for: a tone ends and silence "
    "follows "
    "it, and the ring-out is the number of frames of the silence that stay "
    "above the floor the record says"
)

#: How the ring-out of ours was taken, at the floor of the study.
_OURS_RING_METHOD = (
    "measured on our own modules with the edge contract instrument of the "
    "project (qa/measure_edge_contract.py) on the tree of 2026-10-21: a tone "
    "of 4096 frames goes in, 8192 frames of silence follow it, and the "
    "ring-out is the number of frames of that silence that stay above the "
    "-180 dBFS floor the study used"
)

#: How the ring-out of ours was taken, with the gate of the tree.
_OURS_RING_TREE_METHOD = (
    "measured on our own modules with the gate of the tree "
    "(qa/check_module_edges.py): a warm up of silence, then a tone, then "
    "silence, and the ring-out is the number of samples the output stays "
    "above -60 dBFS after the input stopped.  Three excitations are used "
    "(the tone of the host, the full scale sine and the signal at the "
    "clipping ceiling) and the longest of the three is the number, because a "
    "module has to hold its contract for every input"
)


# ---------------------------------------------------------------------------
# the numbers
# ---------------------------------------------------------------------------
EDGES: tuple[Edge, ...] = (
    # -- the reference that is a plug-in module --------------------------
    Edge(
        name="REFERENCE_EDGE_OUTPUT_FRAMES",
        series="edge_plug_in",
        case="one_frame",
        metric="edge_output_frames",
        value=0.0,
        unit="frames",
        method=_PLUG_IN_METHOD,
        setting=(
            "the plug-in holds whole blocks of its own: it is fed one frame "
            "and it hands back nothing, and the answer comes 1726 samples "
            "later at the block of 512 frames the study used.  Over the "
            "stream the length is the same; the frames are only later"
        ),
    ),
    Edge(
        name="REFERENCE_EDGE_NON_FINITE",
        series="edge_plug_in",
        case=EVERY_CASE,
        metric="edge_non_finite",
        value=0.0,
        unit="samples",
        method=_PLUG_IN_METHOD,
        setting=(
            "not one sample of the output was a NaN or an infinity, over the "
            "whole edge set of the study"
        ),
    ),
    Edge(
        name="REFERENCE_EDGE_SILENCE_OUT_PEAK",
        series="edge_plug_in",
        case="silence",
        metric="edge_silence_out_peak",
        value=0.0,
        unit="1",
        method=_PLUG_IN_METHOD,
        setting="a silent input comes back as zeros",
    ),
    Edge(
        name="REFERENCE_EDGE_FULL_SCALE_TONE_PEAK",
        series="edge_plug_in",
        case="full_scale_tone",
        metric="edge_full_scale_tone_peak",
        value=0.999642,
        unit="1",
        method=_PLUG_IN_METHOD,
        setting="a sine at the ceiling comes back just under the ceiling",
    ),
    Edge(
        name="REFERENCE_EDGE_RING_OUT_FRAMES",
        series="edge_plug_in",
        case="ring_out",
        metric="edge_ring_out_frames",
        value=1518.0,
        unit="frames",
        method=_REFERENCE_RING_METHOD,
        setting=(
            "the frames above the -180 dBFS floor.  The reference declares a "
            "tail of zero, so it under-declares the 1518 frames it rings "
            "for; the rule of the tree counts the latency in, and the "
            "reference holds it"
        ),
    ),
    Edge(
        name="REFERENCE_EDGE_CEILING_SQUARE_PEAK",
        series="edge_plug_in",
        case="ceiling_square",
        metric="edge_ceiling_square_peak",
        value=1.000729,
        unit="1",
        method=_PLUG_IN_METHOD,
        setting=(
            "a signal at the ceiling comes out of the reference **above** the "
            "ceiling: its passband gain is +0.00627 dB below 1 kHz, so a "
            "square that alternates between +1.0 and -1.0 leaves at "
            "1.000729.  It is a design choice and not a numerical accident, "
            "and it is the bar: a module of ours that lets the ceiling "
            "through untouched is at the reference, and one that lets it "
            "through louder is not"
        ),
    ),
    Edge(
        name="REFERENCE_EDGE_CEILING_SQUARE_SAMPLES_OVER",
        series="edge_plug_in",
        case="ceiling_square",
        metric="edge_ceiling_square_samples_over",
        value=47996.0,
        unit="samples",
        method=_PLUG_IN_METHOD,
        setting=(
            "the samples outside [-1, 1] on the same square: the reference "
            "is 47996 samples over the ceiling out of the 48000 of the "
            "render"
        ),
    ),
    Edge(
        name="REFERENCE_EDGE_LATENCY_SAMPLES",
        series="edge_plug_in",
        case="one_frame",
        metric="edge_latency_samples",
        value=1726.0,
        unit="samples",
        method=_PLUG_IN_METHOD,
        setting=(
            "the latency at the block of 512 frames the study used.  The "
            "latency of the reference is exactly the delay it has at every "
            "block size, and it moves with the block size because the "
            "plug-in holds whole blocks of its own: 1582 samples at 16 "
            "frames, 1470 at 128 and 256, 1726 at 512, 3262 at 2048 and 5310 "
            "at 4096"
        ),
    ),
    Edge(
        name="REFERENCE_EDGE_DECLARED_TAIL_FRAMES",
        series="edge_plug_in",
        case=EVERY_CASE,
        metric="edge_declared_tail_frames",
        value=0.0,
        unit="frames",
        method=_PLUG_IN_METHOD,
        setting="the reference declares a tail of zero",
    ),
    Edge(
        name="REFERENCE_EDGE_DECLARED_LIMIT_FRAMES",
        series="edge_plug_in",
        case="ring_out",
        metric="edge_declared_limit_frames",
        value=1726.0,
        unit="frames",
        method=_PLUG_IN_METHOD,
        setting=(
            "the latency plus the tail the reference declares: its latency "
            "is 1726 samples at the block of 512 frames and its tail is "
            "zero, so the 1518 frames it rings for are inside the limit"
        ),
    ),
    Edge(
        name="REFERENCE_EDGE_GAIN_DB",
        series="edge_plug_in",
        case="passband",
        metric="edge_gain_db",
        value=0.00627,
        unit="dB",
        method=_PLUG_IN_METHOD,
        setting=(
            "the gain of the reference below 1 kHz; the gain is linear and "
            "the same at every level from 0.01 to 1.0, so it is a property "
            "of the reference and not of the material"
        ),
    ),
    Edge(
        name="REFERENCE_EDGE_GAIN_DB_AT_1KHZ",
        series="edge_plug_in",
        case="passband",
        metric="edge_gain_db",
        value=-0.00555,
        unit="dB",
        method=_PLUG_IN_METHOD,
        setting="the gain of the reference at 1 kHz",
    ),
    # -- the reference that is installed from an installer ----------------
    Edge(
        name="INSTALLED_EDGE_OUTPUT_FRAMES",
        series="edge_installed",
        case="one_frame",
        metric="edge_output_frames",
        value=1.0,
        unit="frames",
        method=_INSTALLED_METHOD,
        setting="the output is as long as the input: one frame in, one frame out",
    ),
    Edge(
        name="INSTALLED_EDGE_NON_FINITE",
        series="edge_installed",
        case=EVERY_CASE,
        metric="edge_non_finite",
        value=0.0,
        unit="samples",
        method=_INSTALLED_METHOD,
        setting=(
            "not one sample of the output was a NaN or an infinity, over the "
            "whole edge set of the study"
        ),
    ),
    Edge(
        name="INSTALLED_EDGE_SILENCE_OUT_PEAK",
        series="edge_installed",
        case="silence",
        metric="edge_silence_out_peak",
        value=0.0,
        unit="1",
        method=_INSTALLED_METHOD,
        setting="a silent input comes back as exact zeros",
    ),
    Edge(
        name="INSTALLED_EDGE_FULL_SCALE_TONE_PEAK",
        series="edge_installed",
        case="full_scale_tone",
        metric="edge_full_scale_tone_peak",
        value=1.0,
        unit="1",
        method=_INSTALLED_METHOD,
        setting=(
            "the peak of the output is the peak of the input: the reference "
            "adds no level"
        ),
    ),
    Edge(
        name="INSTALLED_EDGE_CEILING_SQUARE_PEAK",
        series="edge_installed",
        case="ceiling_square",
        metric="edge_ceiling_square_peak",
        value=1.0,
        unit="1",
        method=_INSTALLED_METHOD,
        setting=(
            "the peak of the output is the peak of the input: the reference "
            "lets the ceiling through untouched"
        ),
    ),
    Edge(
        name="INSTALLED_EDGE_CEILING_SQUARE_SAMPLES_OVER",
        series="edge_installed",
        case="ceiling_square",
        metric="edge_ceiling_square_samples_over",
        value=0.0,
        unit="samples",
        method=_INSTALLED_METHOD,
        setting="not one sample of the output is outside [-1, 1]",
    ),
    Edge(
        name="INSTALLED_EDGE_RING_OUT_FRAMES",
        series="edge_installed",
        case="ring_out",
        metric="edge_ring_out_frames",
        value=0.0,
        unit="frames",
        method=_REFERENCE_RING_METHOD,
        setting=(
            "the frames above the -80 dBFS floor: the reference goes quiet "
            "at once.  The same measurement below -30 dB gives 243 frames"
        ),
    ),
    Edge(
        name="INSTALLED_EDGE_LATENCY_SAMPLES",
        series="edge_installed",
        case="one_frame",
        metric="edge_latency_samples",
        value=3993.0,
        unit="samples",
        method=_INSTALLED_METHOD,
        setting=(
            "the latency at 48 kHz, and it does not move with the block size "
            "of the host at all"
        ),
    ),
    Edge(
        name="INSTALLED_EDGE_DECLARED_TAIL_FRAMES",
        series="edge_installed",
        case=EVERY_CASE,
        metric="edge_declared_tail_frames",
        value=0.0,
        unit="frames",
        method=_INSTALLED_METHOD,
        setting="the reference declares a tail of zero",
    ),
    Edge(
        name="INSTALLED_EDGE_DECLARED_LIMIT_FRAMES",
        series="edge_installed",
        case="ring_out",
        metric="edge_declared_limit_frames",
        value=3993.0,
        unit="frames",
        method=_INSTALLED_METHOD,
        setting=(
            "the latency plus the tail the reference declares: it goes "
            "quiet at once, so the limit is never reached"
        ),
    ),
    Edge(
        name="INSTALLED_EDGE_GAIN_DB",
        series="edge_installed",
        case="passband",
        metric="edge_gain_db",
        value=0.0,
        unit="dB",
        method=_INSTALLED_METHOD,
        setting=(
            "the gain of the reference: the peak of the output is the peak "
            "of the input at every level the study measured"
        ),
    ),
    # -- our own family ---------------------------------------------------
    Edge(
        name="OURS_EDGE_OUTPUT_FRAMES",
        series="ours",
        case="one_frame",
        metric="edge_output_frames",
        value=1.0,
        unit="frames",
        method=_OURS_METHOD,
        setting=(
            "every one of the 51 modules of the registry hands back one "
            "frame for a frame, and hands back the length of every other "
            "edge signal as well"
        ),
    ),
    Edge(
        name="OURS_EDGE_NON_FINITE",
        series="ours",
        case=EVERY_CASE,
        metric="edge_non_finite",
        value=0.0,
        unit="samples",
        method=_OURS_METHOD,
        setting=(
            "not one sample of the output of any of the 51 modules was a NaN "
            "or an infinity, over the 13 edge signals of the study"
        ),
    ),
    Edge(
        name="OURS_EDGE_SILENCE_OUT_PEAK",
        series="ours",
        case="silence",
        metric="edge_silence_out_peak",
        value=2.363031e-14,
        unit="1",
        method=_OURS_METHOD,
        setting=(
            "the worst of the 49 modules that declare a finite tail: the "
            "spectral de-noise, at -268 dBFS.  The 48 others come back as "
            "exact zeros"
        ),
    ),
    Edge(
        name="OURS_EDGE_SILENCE_OUT_PEAK_GENERATOR",
        series="ours",
        case="silence",
        metric="edge_silence_out_peak",
        value=0.128235,
        unit="1",
        method=_OURS_METHOD,
        setting=(
            "the generator: it makes a tone of its own and says so, its tail "
            "is infinite.  The ambience match declares an infinite tail for "
            "the same reason (it lays a bed of its own under the recording) "
            "and is at zero here, because it has no learned profile"
        ),
    ),
    Edge(
        name="OURS_EDGE_RING_OUT_FRAMES",
        series="ours",
        case="ring_out",
        metric="edge_ring_out_frames",
        value=4848.0,
        unit="frames",
        method=_OURS_RING_TREE_METHOD,
        setting=(
            "the longest ring-out of the family: the leveler and the "
            "loudness control, which ring for exactly the 4848 samples of "
            "latency they declare and no more"
        ),
    ),
    Edge(
        name="OURS_EDGE_RING_OUT_FRAMES_DEEP",
        series="ours",
        case="ring_out",
        metric="edge_ring_out_frames",
        value=8192.0,
        unit="frames",
        method=_OURS_RING_METHOD,
        setting=(
            "the longest ring-out of the family at the floor of the study: "
            "the guitar de-noise, which fills the whole watch window of "
            "8192 frames, so the number is a lower bound and not a finding"
        ),
    ),
    Edge(
        name="OURS_EDGE_CEILING_SQUARE_PEAK",
        series="ours",
        case="ceiling_square",
        metric="edge_ceiling_square_peak",
        value=1.0,
        unit="1",
        method=_OURS_METHOD,
        setting=(
            "the worst of the 49 modules that declare a finite tail: 43 of "
            "them let the ceiling through untouched, at exactly 1.0, and the "
            "other 6 take level off it"
        ),
    ),
    Edge(
        name="OURS_EDGE_CEILING_SQUARE_SAMPLES_OVER",
        series="ours",
        case="ceiling_square",
        metric="edge_ceiling_square_samples_over",
        value=0.0,
        unit="samples",
        method=_OURS_METHOD,
        setting=(
            "not one sample of the output of any of the 49 modules is "
            "outside [-1, 1] on a square at the ceiling"
        ),
    ),
    Edge(
        name="OURS_EDGE_CEILING_SQUARE_PEAK_GENERATOR",
        series="ours",
        case="ceiling_square",
        metric="edge_ceiling_square_peak",
        value=1.128866,
        unit="1",
        method=_OURS_METHOD,
        setting=(
            "the generator, which makes a tone of its own on top of the "
            "signal: it is the only module of ours that comes out above the "
            "ceiling"
        ),
    ),
    Edge(
        name="OURS_EDGE_CEILING_SQUARE_SAMPLES_OVER_GENERATOR",
        series="ours",
        case="ceiling_square",
        metric="edge_ceiling_square_samples_over",
        value=47980.0,
        unit="samples",
        method=_OURS_METHOD,
        setting=(
            "the samples outside [-1, 1] of the generator, out of the 96000 "
            "of the two channels of the render"
        ),
    ),
    Edge(
        name="OURS_EDGE_LATENCY_SAMPLES",
        series="ours",
        case="one_frame",
        metric="edge_latency_samples",
        value=4848.0,
        unit="samples",
        method=_OURS_METHOD,
        setting=(
            "the longest latency the family declares: the leveler and the "
            "loudness control.  The number does not move with the block size "
            "of the host"
        ),
    ),
    Edge(
        name="OURS_EDGE_DECLARED_TAIL_FRAMES",
        series="ours",
        case=EVERY_CASE,
        metric="edge_declared_tail_frames",
        value=2240.0,
        unit="frames",
        method=_OURS_METHOD,
        setting=(
            "the longest tail the family declares: the de-hum.  Two modules "
            "declare an infinite tail, the generator and the ambience match, "
            "because both make a sound of their own"
        ),
    ),
    Edge(
        name="OURS_EDGE_DECLARED_LIMIT_FRAMES",
        series="ours",
        case="ring_out",
        metric="edge_declared_limit_frames",
        value=3583.0,
        unit="frames",
        method=_OURS_METHOD,
        setting=(
            "the limit of the voice de-noise, the module that rings the "
            "longest of the family with a finite tail: a latency of 2047 "
            "samples and a tail of 1536.  Its ring-out is 3375 samples, so "
            "it holds the rule"
        ),
    ),
    Edge(
        name="OURS_EDGE_GAIN_DB",
        series="ours",
        case="passband",
        metric="edge_gain_db",
        value=0.0,
        unit="dB",
        method=_OURS_METHOD,
        setting=(
            "the gain of the family on a signal it leaves alone: 43 modules "
            "hand the ceiling back at exactly 1.0, so the gain is zero"
        ),
    ),
)

#: The numbers of the edge contract, keyed by the name of their constant.
EDGE_BY_NAME: dict[str, Edge] = {edge.name: edge for edge in EDGES}


def edges_for(case: str, metric: str) -> tuple[Edge, ...]:
    """Every number of a case and a metric.

    A number recorded under the name ``"*"`` belongs to every case: the
    finiteness of a product and the tail it declares are properties of the
    product, not of the signal.

    Parameters
    ----------
    case:
        The name of the case.
    metric:
        The name of the metric.

    Returns
    -------
    tuple[Edge, ...]
        The numbers that answer the question.
    """
    return tuple(
        edge
        for edge in EDGES
        if edge.metric == metric and edge.case in (case, EVERY_CASE)
    )


def edge_for(case: str, metric: str, series: str | None = None) -> float | None:
    """The number a case and a metric are held to.

    Without a series the **strongest** number is the reference: the gap that
    is worth closing is the one against the best of them.  With a series the
    number of that series is the reference.

    Parameters
    ----------
    case:
        The name of the case.
    metric:
        The name of the metric.
    series:
        The name of the series; every series when it is empty.

    Returns
    -------
    float | None
        The number, or None when nothing answers the question.
    """
    found = [
        edge
        for edge in edges_for(case, metric)
        if series is None or edge.series == series
    ]
    if not found:
        return None
    rule = METRIC_BY_NAME[metric]
    if rule.higher_is_better:
        return max(float(edge.value) for edge in found)
    return min(float(edge.value) for edge in found)
