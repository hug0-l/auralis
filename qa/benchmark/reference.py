"""
qa.benchmark.reference — the numbers the study recorded.

These are measurements of somebody else's behaviour.  A number is a number:
it carries no name, no code and no parameter of the product it was measured
on.  What travels with it is the case it belongs to, the unit, the way it was
measured and the reference series it came from, so that the number can be
read, argued about and replaced when a better measurement arrives.

A reference is a **target, not a contract**.  The harness reports the gap; it
does not fail on it.  The reference was measured on somebody else's build, on
somebody else's machine, with somebody else's settings, so a gate that failed
on it would be a gate held by a machine we do not control.

Two reference series are recorded.  They are described by their shape, never
by their names:

``stem_separation``
    the reference that splits a recording into three stems and gives each of
    them a gain and three switches;
``band_restoration``
    the reference that picks an algorithm and gives it four bands.

How the numbers were taken
--------------------------

Every number below was measured **with the ruler of this harness**, so that
the two sides of the table are measured with the same code:

* the corpus cases are measured with the bands of the corpus manifest and the
  quality instrument of the project (``qa/quality/measurement.py``);
* the voice cases are measured with the voice instrument of the project
  (``qa/qualityvoice/harness.py``);
* the reference was rendered through the study's renderer, which loads a
  plug-in the way a host does and writes a parameter only when it is asked
  to, and the render was then measured here.

The setting of the reference is part of the number.  The study's rule is the
one the project already writes down: a setting is taken when it removes the
most artefact *and* the wanted content stays inside the budget of our own
rules.  Where the study had already made that choice, the number is the one
the study recorded; where it had not, the same rule was applied to the
renders of the study's renderer.

The cost is the one exception: it cannot be counted for a plug-in we cannot
see inside, so the reference number is a **timed** one, while ours is
**counted**.  The two are in the same unit (1024 point transforms per 64
sample block) and they are close, but they are not the same instrument.

**Both sides of the cost comparison are on one yardstick.**  The yardstick is
a constant of the project: one 1024 point transform costs **8.300 us** on the
reference machine, so a 64 sample block of real time at 48 kHz (1333.3 us)
may cost **160.6** transforms.  Ours is counted in that unit from the start.
The reference's was timed on the study machine and is converted into that
unit here, and every cost method says what it was measured as, on which
machine, and how the conversion was made.  A cost that moves with the
machine that measured it is an echo, not a budget, so a number whose
provenance cannot be established is not kept: it is recorded as unknown.

The cost belongs to the product and not to the material, so it is recorded
once, under the name ``"*"``.

The real time numbers
---------------------

Four of the numbers are not properties of a clip at all: they are properties
of the **product in a host**.  They are how long the product takes to let a
moved parameter be heard, how big a step that move makes in the output, how
many blocks of a minute of audio it is late for, and how far its output lags
its input.  A minute of audio and a moved knob do not belong to a clip of the
corpus, so they are recorded once, under the name ``"*"``, like the cost.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "Metric",
    "METRICS",
    "METRIC_BY_NAME",
    "Reference",
    "REFERENCES",
    "SERIES",
    "metric_for",
    "reference_for",
    "references_for",
    "REFERENCE_TRANSFORM_US",
    "COST_BLOCK",
    "BUDGET_US_PER_BLOCK",
    "BUDGET_TRANSFORMS_PER_BLOCK",
]


# ---------------------------------------------------------------------------
# the yardstick of the cost
# ---------------------------------------------------------------------------
#: The cost of one 1024 point transform on the reference machine, in
#: microseconds.  It is the number every cost of the benchmark is written in,
#: on **both** sides of the table: ours is counted in it, the reference's is
#: timed on the study machine and converted into it.  The constant is the one
#: of the performance gate (``qa/performance/budgets.py``), so a module and a
#: reference are held to the same stick.
REFERENCE_TRANSFORM_US = 8.300

#: The block the cost of a module is expressed in (samples).
COST_BLOCK = 64

#: The realtime budget of one block of 64 samples at 48 kHz, in microseconds.
BUDGET_US_PER_BLOCK = 1333.3333

#: The same budget in transform equivalents: 1333.3333 / 8.300.
BUDGET_TRANSFORMS_PER_BLOCK = 160.6426


@dataclass(frozen=True)
class Metric:
    """One number of the table and what a good number of it looks like."""

    name: str
    unit: str
    higher_is_better: bool
    note: str = ""


#: Every metric of the benchmark.
METRICS: tuple[Metric, ...] = (
    Metric(
        name="removed_db",
        unit="dB",
        higher_is_better=True,
        note=(
            "How much of the artefact is taken away: the drop of the level of "
            "the band that carries nothing but the artefact."
        ),
    ),
    Metric(
        name="kept_db",
        unit="dB",
        higher_is_better=False,
        note=(
            "How far the wanted content moves: the change of the level of the "
            "partial that has to survive.  A number of removal bought with a "
            "large movement here is not a win: read the two rows together."
        ),
    ),
    Metric(
        name="gain_var_db2",
        unit="dB^2",
        higher_is_better=False,
        note=(
            "How much the gain curve breathes: the temporal variance of the "
            "applied gain inside the artefact band."
        ),
    ),
    Metric(
        name="chatter_db",
        unit="dB",
        higher_is_better=False,
        note=(
            "How much the gain chatters on a voice: the drift of the applied "
            "gain, with the wanted content taken out of the measurement."
        ),
    ),
    Metric(
        name="latency_samples",
        unit="samples",
        higher_is_better=False,
        note=(
            "How many samples the output lags the input.  The delay of the "
            "reference grows with the block size of the host (1214 samples "
            "plus the block), so the number of the table is the delay at the "
            "256 frame block, where it is the smallest it gets."
        ),
    ),
    Metric(
        name="cost_transform_eq",
        unit="transforms",
        higher_is_better=False,
        note=(
            "How much work a module does: the number of 1024 point transforms "
            "one 64 sample block costs, on the yardstick of the project "
            "(8.300 us per transform, so a block may cost 160.6 of them).  "
            "Ours is counted, so the number does not move with the load of "
            "the machine; the reference's is timed on the study machine and "
            "then converted into the same yardstick, because a plug-in we "
            "cannot see inside cannot be counted."
        ),
    ),
    Metric(
        name="realtime_underruns_per_min",
        unit="blocks",
        higher_is_better=False,
        note=(
            "How many blocks of a minute of real time audio the reference was "
            "not finished with before their deadline: the number of the "
            "glitches a host has to cover up."
        ),
    ),
    Metric(
        name="change_latency_ms",
        unit="ms",
        higher_is_better=False,
        note=(
            "How long after a parameter change the changed audio is heard: the "
            "time from the change to the first sample that carries it."
        ),
    ),
    Metric(
        name="change_step",
        unit="amplitude",
        higher_is_better=False,
        note=(
            "How big the step is that a parameter change makes in the output, "
            "as an amplitude where 1.0 is full scale.  A change that is not "
            "smoothed is heard as a click."
        ),
    ),
)

#: The metrics, keyed by name.
METRIC_BY_NAME: dict[str, Metric] = {metric.name: metric for metric in METRICS}

#: The reference series, keyed by their name.
SERIES: dict[str, str] = {
    "stem_separation": (
        "the reference that splits a recording into three stems and gives each "
        "of them a gain and three switches"
    ),
    "band_restoration": (
        "the reference that picks an algorithm and gives it four bands"
    ),
}


@dataclass(frozen=True)
class Reference:
    """One number the study recorded, with everything needed to read it."""

    series: str
    case: str
    metric: str
    value: float | None
    unit: str
    method: str

    def as_dict(self) -> dict:
        """The reference as a dictionary, for the JSON result."""
        return {
            "series": self.series,
            "case": self.case,
            "metric": self.metric,
            "value": self.value,
            "unit": self.unit,
            "method": self.method,
        }


#: How the numbers of the corpus cases were taken.
#:
#: The instrument is named, because the study has more than one host and only
#: one of them moves a parameter of its own and never puts it back (see
#: docs/BENCHMARK_NOTES.md, section 5.1).  This is the study's renderer: it
#: writes a parameter only when it is asked to, and it moves none of its own.
_CORPUS_METHOD = (
    "the reference rendered through the study's renderer, which moves no "
    "parameter of its own, and measured with this harness's ruler: the bands "
    "of the corpus manifest and the quality instrument of the project"
)

#: How the numbers of the voice cases were taken.
_VOICE_METHOD = (
    "the reference rendered through the study's renderer, which moves no "
    "parameter of its own, and measured with this harness's ruler: the voice "
    "instrument of the project "
    "(the voice band, the bed band and the chatter of the gain)"
)

#: The setting the study's rule picked.
_BEST_METHOD = (
    "the setting the study's rule picks: the most artefact removed while the "
    "wanted content stays inside the budget of our own rules"
)

#: The setting the reference comes with.
_DEFAULT_METHOD = (
    "the setting the reference comes with; the study's rule would look for a "
    "better one, so this number is the floor of what the reference does"
)

#: The delay, as the study's renderer measured it on the render.
_LATENCY_METHOD = (
    "the delay the study's renderer measured on the render of the reference, "
    "from the cross correlation of the envelopes; a cross correlation of a "
    "heavily modified signal lands near the delay the reference really has, "
    "not on "
    "it.  The impulse test puts the delay of the reference at 1470 samples at "
    "a 256 frame block, at 1726 at 512 and at 2238 at 1024, that is 1214 "
    "samples plus the block size of the host above 256 frames"
)

#: How the numbers of the real time run were taken.
_REALTIME_METHOD = (
    "a minute of audio driven through the study's real time host, which "
    "sets only the preset it was given, at "
    "48 kHz with 256 frame blocks, the clock running against the blocks: an "
    "underrun is a block that was not finished before its deadline, the "
    "delay is the distance between the impulse that goes in and the impulse "
    "that comes out, and the change latency is the first sample of the run "
    "with the change that differs from the run without it"
)

#: The cost of the stem separation reference, and the conversion of it.
_COST_STEM_METHOD = (
    "the cost of one 64 sample block of the reference, as the study's renderer "
    "timed it, in the yardstick of "
    "the project: 8.300 us per 1024 point transform, so the realtime budget "
    "of a 64 sample block is 1333.3 us = 160.6 transforms.  The study's "
    "renderer measured the reference on the study machine at 0.23 to 0.32 "
    "times the "
    "real time (20 s of audio rendered in about 5 s), and the two "
    "observations are converted: 306.7 us per 64 sample block = 36.9466 "
    "transforms of 8.300 us (0.23 times the real time) and 426.7 us per 64 "
    "sample block = 51.4056 transforms of 8.300 us (0.32 times the real "
    "time).  The block size, the channel count and the material of that "
    "measurement are not written down in this tree, so the number is a "
    "figure per block of the study's own rendering"
)

#: The cost of the band restoration reference, and the conversion of it.
_COST_BAND_METHOD = (
    "the cost of one 64 sample block of the reference, as the study's renderer "
    "timed it, in the yardstick of "
    "the project: 8.300 us per 1024 point transform, so the realtime budget "
    "of a 64 sample block is 1333.3 us = 160.6 transforms.  The study's "
    "renderer measured the reference on the study machine at 5.5 to 23.0 ms "
    "per 512 "
    "frame block, and the two observations are converted: 687.5 us per 64 "
    "sample block = 82.8313 transforms of 8.300 us (the cheapest mode) and "
    "2875.0 us per 64 sample block = 346.3855 transforms of 8.300 us (the "
    "most expensive mode).  The block size, the channel count and the "
    "material of that measurement are not written down in this tree, so the "
    "number is a figure per block of the study's own rendering"
)

#: The number the study wrote down in transforms, and why it is not here.
_COST_REMOVED_METHOD = (
    "the number the study's own cost model wrote down in 1024 point "
    "transforms was taken "
    "against the transform of the study machine, and the cost of that "
    "transform is not recorded in this tree, so it could not be converted "
    "into the yardstick of the project (8.300 us per 1024 point transform, "
    "so the realtime budget of a 64 sample block is 1333.3 us = 160.6 "
    "transforms).  A number whose provenance cannot be established is not "
    "kept: it was removed and the measurements it came from are recorded "
    "above in their own unit"
)

#: Every number the study recorded.
REFERENCES: tuple[Reference, ...] = (
    # -- the corpus cases, the stem separation reference ---------------------
    Reference(
        series="stem_separation",
        case="hiss",
        metric="removed_db",
        value=21.9129,
        unit="dB",
        method="%s; %s; the whole clip is artefact, so there is nothing to keep"
        % (_CORPUS_METHOD, _BEST_METHOD),
    ),
    Reference(
        series="stem_separation",
        case="hiss",
        metric="gain_var_db2",
        value=0.11847,
        unit="dB^2",
        method="%s; %s" % (_CORPUS_METHOD, _BEST_METHOD),
    ),
    Reference(
        series="stem_separation",
        case="pink_hiss",
        metric="removed_db",
        value=19.8065,
        unit="dB",
        method="%s; %s; the whole clip is artefact, so there is nothing to keep"
        % (_CORPUS_METHOD, _BEST_METHOD),
    ),
    Reference(
        series="stem_separation",
        case="pink_hiss",
        metric="gain_var_db2",
        value=2.71085,
        unit="dB^2",
        method="%s; %s" % (_CORPUS_METHOD, _BEST_METHOD),
    ),
    Reference(
        series="stem_separation",
        case="hum50",
        metric="removed_db",
        value=0.0399,
        unit="dB",
        method="%s; %s; the settings that take more of the hum away take the "
        "wanted tone with them, so they do not hold the rule"
        % (_CORPUS_METHOD, _DEFAULT_METHOD),
    ),
    Reference(
        series="stem_separation",
        case="hum50",
        metric="kept_db",
        value=0.00555,
        unit="dB",
        method="%s; %s" % (_CORPUS_METHOD, _DEFAULT_METHOD),
    ),
    Reference(
        series="stem_separation",
        case="hum50",
        metric="gain_var_db2",
        value=0.00042,
        unit="dB^2",
        method="%s; %s" % (_CORPUS_METHOD, _DEFAULT_METHOD),
    ),
    Reference(
        series="stem_separation",
        case="rumble",
        metric="removed_db",
        value=0.037,
        unit="dB",
        method="%s; %s; the settings that take more of the rumble away take "
        "the wanted programme with them, so they do not hold the rule"
        % (_CORPUS_METHOD, _DEFAULT_METHOD),
    ),
    Reference(
        series="stem_separation",
        case="rumble",
        metric="kept_db",
        value=0.00555,
        unit="dB",
        method="%s; %s" % (_CORPUS_METHOD, _DEFAULT_METHOD),
    ),
    Reference(
        series="stem_separation",
        case="rumble",
        metric="gain_var_db2",
        value=8.88447,
        unit="dB^2",
        method="%s; %s" % (_CORPUS_METHOD, _DEFAULT_METHOD),
    ),
    Reference(
        series="stem_separation",
        case="voice_hiss",
        metric="removed_db",
        value=296.4518,
        unit="dB",
        method="%s; %s; the bed of the clip lives in the difference between the "
        "two channels and the reference silences that difference, so the level "
        "reaches the floor of the instrument: the number says the bed is "
        "gone, it is not a figure of signal to noise"
        % (_CORPUS_METHOD, _DEFAULT_METHOD),
    ),
    Reference(
        series="stem_separation",
        case="voice_hiss",
        metric="kept_db",
        value=0.01281,
        unit="dB",
        method="%s; %s" % (_CORPUS_METHOD, _DEFAULT_METHOD),
    ),
    Reference(
        series="stem_separation",
        case="voice_hiss",
        metric="gain_var_db2",
        value=0.02504,
        unit="dB^2",
        method="%s; %s" % (_CORPUS_METHOD, _DEFAULT_METHOD),
    ),
    # -- the corpus cases, the band restoration reference --------------------
    Reference(
        series="band_restoration",
        case="hiss",
        metric="removed_db",
        value=49.1941,
        unit="dB",
        method="%s; %s; the whole clip is artefact, so there is nothing to keep"
        % (_CORPUS_METHOD, _DEFAULT_METHOD),
    ),
    Reference(
        series="band_restoration",
        case="hiss",
        metric="gain_var_db2",
        value=0.01855,
        unit="dB^2",
        method="%s; %s" % (_CORPUS_METHOD, _DEFAULT_METHOD),
    ),
    Reference(
        series="band_restoration",
        case="pink_hiss",
        metric="removed_db",
        value=42.6057,
        unit="dB",
        method="%s; %s; the whole clip is artefact, so there is nothing to keep"
        % (_CORPUS_METHOD, _DEFAULT_METHOD),
    ),
    Reference(
        series="band_restoration",
        case="pink_hiss",
        metric="gain_var_db2",
        value=0.22788,
        unit="dB^2",
        method="%s; %s" % (_CORPUS_METHOD, _DEFAULT_METHOD),
    ),
    Reference(
        series="band_restoration",
        case="hum50",
        metric="removed_db",
        value=67.0024,
        unit="dB",
        method="%s; %s; the number is bought with a large movement of the "
        "wanted tone, so read it together with the kept_db row"
        % (_CORPUS_METHOD, _DEFAULT_METHOD),
    ),
    Reference(
        series="band_restoration",
        case="hum50",
        metric="kept_db",
        value=26.85793,
        unit="dB",
        method="%s; %s; the wanted tone moves this far: the removal of the "
        "hum is bought with the tone"
        % (_CORPUS_METHOD, _DEFAULT_METHOD),
    ),
    Reference(
        series="band_restoration",
        case="hum50",
        metric="gain_var_db2",
        value=36.89637,
        unit="dB^2",
        method="%s; %s" % (_CORPUS_METHOD, _DEFAULT_METHOD),
    ),
    Reference(
        series="band_restoration",
        case="rumble",
        metric="removed_db",
        value=53.871,
        unit="dB",
        method="%s; %s; the number is bought with a large movement of the "
        "wanted programme, so read it together with the kept_db row"
        % (_CORPUS_METHOD, _DEFAULT_METHOD),
    ),
    Reference(
        series="band_restoration",
        case="rumble",
        metric="kept_db",
        value=44.73293,
        unit="dB",
        method="%s; %s; the wanted programme moves this far: the removal of "
        "the rumble is bought with the programme"
        % (_CORPUS_METHOD, _DEFAULT_METHOD),
    ),
    Reference(
        series="band_restoration",
        case="rumble",
        metric="gain_var_db2",
        value=17.62301,
        unit="dB^2",
        method="%s; %s" % (_CORPUS_METHOD, _DEFAULT_METHOD),
    ),
    Reference(
        series="band_restoration",
        case="voice_hiss",
        metric="removed_db",
        value=14.2212,
        unit="dB",
        method="%s; %s" % (_CORPUS_METHOD, _DEFAULT_METHOD),
    ),
    Reference(
        series="band_restoration",
        case="voice_hiss",
        metric="kept_db",
        value=5.26286,
        unit="dB",
        method="%s; %s" % (_CORPUS_METHOD, _DEFAULT_METHOD),
    ),
    Reference(
        series="band_restoration",
        case="voice_hiss",
        metric="gain_var_db2",
        value=0.04624,
        unit="dB^2",
        method="%s; %s" % (_CORPUS_METHOD, _DEFAULT_METHOD),
    ),
    # -- the voice cases, the stem separation reference ------------------------
    Reference(
        series="stem_separation",
        case="corpus_voice",
        metric="removed_db",
        value=0.00049,
        unit="dB",
        method="%s; %s; the setting the study recorded: the reference does "
        "not touch the bed of the clip with its own settings"
        % (_VOICE_METHOD, _DEFAULT_METHOD),
    ),
    Reference(
        series="stem_separation",
        case="corpus_voice",
        metric="kept_db",
        value=0.0063,
        unit="dB",
        method="%s; %s" % (_VOICE_METHOD, _DEFAULT_METHOD),
    ),
    Reference(
        series="stem_separation",
        case="corpus_voice",
        metric="chatter_db",
        value=0.00029,
        unit="dB",
        method="%s; %s" % (_VOICE_METHOD, _DEFAULT_METHOD),
    ),
    Reference(
        series="stem_separation",
        case="synthetic_voice",
        metric="removed_db",
        value=8.9585,
        unit="dB",
        method="%s; %s; the setting the study recorded: the most bed removed "
        "while the voice stays inside the budget of our own rules"
        % (_VOICE_METHOD, _BEST_METHOD),
    ),
    Reference(
        series="stem_separation",
        case="synthetic_voice",
        metric="kept_db",
        value=0.039,
        unit="dB",
        method="%s; %s" % (_VOICE_METHOD, _BEST_METHOD),
    ),
    Reference(
        series="stem_separation",
        case="synthetic_voice",
        metric="chatter_db",
        value=0.44885,
        unit="dB",
        method="%s; %s" % (_VOICE_METHOD, _BEST_METHOD),
    ),
    Reference(
        series="stem_separation",
        case="speech_shaped",
        metric="removed_db",
        value=0.00019,
        unit="dB",
        method="%s; %s; the setting the study recorded: the only one that "
        "keeps the wanted stack inside the budget of our own rules"
        % (_VOICE_METHOD, _DEFAULT_METHOD),
    ),
    Reference(
        series="stem_separation",
        case="speech_shaped",
        metric="kept_db",
        value=0.00011,
        unit="dB",
        method="%s; %s" % (_VOICE_METHOD, _DEFAULT_METHOD),
    ),
    Reference(
        series="stem_separation",
        case="speech_shaped",
        metric="chatter_db",
        value=0.00044,
        unit="dB",
        method="%s; %s" % (_VOICE_METHOD, _DEFAULT_METHOD),
    ),
    # -- the voice cases, the band restoration reference ----------------------
    Reference(
        series="band_restoration",
        case="corpus_voice",
        metric="removed_db",
        value=14.29502,
        unit="dB",
        method="%s; %s" % (_VOICE_METHOD, _DEFAULT_METHOD),
    ),
    Reference(
        series="band_restoration",
        case="corpus_voice",
        metric="kept_db",
        value=2.91659,
        unit="dB",
        method="%s; %s" % (_VOICE_METHOD, _DEFAULT_METHOD),
    ),
    Reference(
        series="band_restoration",
        case="corpus_voice",
        metric="chatter_db",
        value=0.20303,
        unit="dB",
        method="%s; %s" % (_VOICE_METHOD, _DEFAULT_METHOD),
    ),
    Reference(
        series="band_restoration",
        case="synthetic_voice",
        metric="removed_db",
        value=13.42147,
        unit="dB",
        method="%s; %s" % (_VOICE_METHOD, _DEFAULT_METHOD),
    ),
    Reference(
        series="band_restoration",
        case="synthetic_voice",
        metric="kept_db",
        value=0.37065,
        unit="dB",
        method="%s; %s" % (_VOICE_METHOD, _DEFAULT_METHOD),
    ),
    Reference(
        series="band_restoration",
        case="synthetic_voice",
        metric="chatter_db",
        value=0.23813,
        unit="dB",
        method="%s; %s" % (_VOICE_METHOD, _DEFAULT_METHOD),
    ),
    Reference(
        series="band_restoration",
        case="speech_shaped",
        metric="removed_db",
        value=14.13831,
        unit="dB",
        method="%s; %s" % (_VOICE_METHOD, _DEFAULT_METHOD),
    ),
    Reference(
        series="band_restoration",
        case="speech_shaped",
        metric="kept_db",
        value=5.6647,
        unit="dB",
        method="%s; %s; the wanted stack moves this far: the removal of the "
        "bed is bought with the stack"
        % (_VOICE_METHOD, _DEFAULT_METHOD),
    ),
    Reference(
        series="band_restoration",
        case="speech_shaped",
        metric="chatter_db",
        value=0.00517,
        unit="dB",
        method="%s; %s" % (_VOICE_METHOD, _DEFAULT_METHOD),
    ),
    # -- the delay ------------------------------------------------------------
    Reference(
        series="stem_separation",
        case="hiss",
        metric="latency_samples",
        value=2238,
        unit="samples",
        method=_LATENCY_METHOD,
    ),
    Reference(
        series="stem_separation",
        case="pink_hiss",
        metric="latency_samples",
        value=2238,
        unit="samples",
        method=_LATENCY_METHOD,
    ),
    Reference(
        series="stem_separation",
        case="hum50",
        metric="latency_samples",
        value=2238,
        unit="samples",
        method=_LATENCY_METHOD,
    ),
    Reference(
        series="stem_separation",
        case="rumble",
        metric="latency_samples",
        value=2238,
        unit="samples",
        method=_LATENCY_METHOD,
    ),
    Reference(
        series="stem_separation",
        case="voice_hiss",
        metric="latency_samples",
        value=2238,
        unit="samples",
        method=_LATENCY_METHOD,
    ),
    Reference(
        series="stem_separation",
        case="corpus_voice",
        metric="latency_samples",
        value=2238,
        unit="samples",
        method=_LATENCY_METHOD,
    ),
    Reference(
        series="stem_separation",
        case="synthetic_voice",
        metric="latency_samples",
        value=2238,
        unit="samples",
        method=_LATENCY_METHOD,
    ),
    Reference(
        series="stem_separation",
        case="speech_shaped",
        metric="latency_samples",
        value=2238,
        unit="samples",
        method=_LATENCY_METHOD,
    ),
    Reference(
        series="band_restoration",
        case="hiss",
        metric="latency_samples",
        value=3880,
        unit="samples",
        method=_LATENCY_METHOD,
    ),
    Reference(
        series="band_restoration",
        case="pink_hiss",
        metric="latency_samples",
        value=3880,
        unit="samples",
        method=_LATENCY_METHOD,
    ),
    Reference(
        series="band_restoration",
        case="hum50",
        metric="latency_samples",
        value=3880,
        unit="samples",
        method=_LATENCY_METHOD,
    ),
    Reference(
        series="band_restoration",
        case="rumble",
        metric="latency_samples",
        value=3880,
        unit="samples",
        method=_LATENCY_METHOD,
    ),
    Reference(
        series="band_restoration",
        case="voice_hiss",
        metric="latency_samples",
        value=3880,
        unit="samples",
        method=_LATENCY_METHOD,
    ),
    Reference(
        series="band_restoration",
        case="corpus_voice",
        metric="latency_samples",
        value=3993,
        unit="samples",
        method=_LATENCY_METHOD,
    ),
    Reference(
        series="band_restoration",
        case="synthetic_voice",
        metric="latency_samples",
        value=3993,
        unit="samples",
        method=_LATENCY_METHOD,
    ),
    Reference(
        series="band_restoration",
        case="speech_shaped",
        metric="latency_samples",
        value=3993,
        unit="samples",
        method=_LATENCY_METHOD,
    ),
    # -- the cost -------------------------------------------------------------
    # The cost of a product does not belong to a case: the same number is the
    # reference for every case, so it is recorded once, under the name that
    # means "every case".
    #
    # Both sides of the comparison are on the yardstick of the project: 8.300
    # us per 1024 point transform, so a 64 sample block of real time at 48 kHz
    # may cost 160.6 transforms.  Ours is counted in that unit by the harness;
    # the reference's is timed on the study machine and converted here.
    Reference(
        series="stem_separation",
        case="*",
        metric="cost_transform_eq",
        value=36.9466,
        unit="transforms",
        method="%s; the cheapest observation of the two" % _COST_STEM_METHOD,
    ),
    Reference(
        series="stem_separation",
        case="*",
        metric="cost_transform_eq",
        value=51.4056,
        unit="transforms",
        method="%s; the dearest observation of the two" % _COST_STEM_METHOD,
    ),
    Reference(
        series="stem_separation",
        case="*",
        metric="cost_transform_eq",
        value=None,
        unit="transforms",
        method=_COST_REMOVED_METHOD,
    ),
    Reference(
        series="band_restoration",
        case="*",
        metric="cost_transform_eq",
        value=82.8313,
        unit="transforms",
        method="%s; the cheapest mode" % _COST_BAND_METHOD,
    ),
    Reference(
        series="band_restoration",
        case="*",
        metric="cost_transform_eq",
        value=346.3855,
        unit="transforms",
        method="%s; the most expensive mode" % _COST_BAND_METHOD,
    ),
    Reference(
        series="band_restoration",
        case="*",
        metric="cost_transform_eq",
        value=None,
        unit="transforms",
        method=_COST_REMOVED_METHOD,
    ),
    # -- the real time behaviour ---------------------------------------------
    # A minute of audio and a moved knob are not properties of a clip of the
    # corpus: they are properties of the product in a host, so they are
    # recorded once, under the name that means "every case".
    Reference(
        series="stem_separation",
        case="*",
        metric="latency_samples",
        value=1470,
        unit="samples",
        method=(
            "%s; the delay of the reference measured with an impulse test: an "
            "impulse in, the same impulse out, the distance read on the "
            "waveform, and it is the same for every impulse of every clip.  "
            "The delay grows with the block size of the host (1214 samples "
            "plus the block), so it is 1470 samples at 128 and 256 frames, "
            "1726 at 512, 2238 at 1024 and 5310 at 4096; the number of this "
            "row is the delay at the 256 frame block, where the reference is "
            "at its smallest" % _REALTIME_METHOD
        ),
    ),
    Reference(
        series="stem_separation",
        case="*",
        metric="realtime_underruns_per_min",
        value=1486,
        unit="blocks",
        method=(
            "%s; the block size of 256 frames, the worst overrun 313 ms and "
            "30.5 s of overrun inside the minute; a second run of the same "
            "build gave 1600 and an earlier one 1139, so the number moves "
            "with the load of the machine" % _REALTIME_METHOD
        ),
    ),
    Reference(
        series="stem_separation",
        case="*",
        metric="change_latency_ms",
        value=0.0,
        unit="ms",
        method=(
            "%s; the gain of a stem moved by 60 dB: the change reaches the "
            "engine in the block it is handed over in, so the changed audio "
            "starts in that same block" % _REALTIME_METHOD
        ),
    ),
    Reference(
        series="stem_separation",
        case="*",
        metric="change_step",
        value=0.0805,
        unit="amplitude",
        method=(
            "%s; the gain of a stem moved by 60 dB: the output jumps by this "
            "much, where the run without the change moves 0.027, so the "
            "change is a step and not a ramp and the user hears a click of "
            "about -22 dBFS" % _REALTIME_METHOD
        ),
    ),
)


def metric_for(name: str) -> Metric:
    """Return the metric of a name."""
    try:
        return METRIC_BY_NAME[name]
    except KeyError:
        raise KeyError(f"unknown metric {name!r}") from None


def references_for(case: str, metric: str) -> tuple[Reference, ...]:
    """Every reference of a case and a metric, one per series.

    A number recorded under the name ``"*"`` belongs to every case: the cost
    of a product is a property of the product, not of the material.
    """
    return tuple(
        reference for reference in REFERENCES
        if reference.metric == metric and reference.case in (case, "*")
    )


def reference_for(case: str, metric: str) -> float | None:
    """The reference number a case and a metric are held to.

    When more than one series has a number, the **strongest** one is the
    reference: the benchmark is held to the best of the references, because
    that is the one worth closing the gap to.
    """
    found = [reference for reference in references_for(case, metric)
             if reference.value is not None]
    if not found:
        return None
    rule = metric_for(metric)
    if rule.higher_is_better:
        return max(float(reference.value) for reference in found)
    return min(float(reference.value) for reference in found)
