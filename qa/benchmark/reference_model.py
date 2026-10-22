"""
qa.benchmark.reference_model — the numbers of a model in the offline path.

A **model** in this product is one file with weights and a manifest, and it
belongs to the offline path: the real time path never loads one.  This file
holds the numbers that say what such a model costs and what it buys, so the
product has a yardstick: a model that is slower than this, or that removes
less, is behind.

The file holds **two subjects**, and they do not mix:

``realtime_hand_written``, ``offline_hand_written``, ``offline_with_model``
    the **model path of ours**: the same module of ours in three
    configurations, with the quality it reaches, the latency it publishes
    and the cost of the model it runs.  These numbers are measurements of
    ours, taken with the instruments of the tree.
``reference_model``
    the **study of the reference's model library**: the anatomy of a model
    artefact that was read from the outside, and the arithmetic of what a
    model of that size costs at the audio rate and at the frame rate.  These
    numbers are not measurements of ours: nothing was loaded and nothing was
    executed to get them.

The shape is the shape of ``qa/benchmark/reference.py`` and of
``qa/benchmark/reference_restoration.py``: a number is a number, it carries
no name, no code and no parameter of the product it was measured on, and what
travels with it is the **case** it belongs to, the **unit** it is in, the
**way it was measured** and the **series** it was measured in.

The series
----------
``offline_hand_written``
    the module without a model: the hand written path, in the offline
    configuration.  It is the **floor** of the offline path, not a target.
``offline_with_model``
    the same module with a model of the library running on it.
``realtime_hand_written``
    the module without a model, in the real time configuration.  It is the
    path that may not change, and the numbers here say what it does today.
``reference_model``
    the study of the library of the reference: the size of its models, the
    depth of its stacks, the cost of one pass of it and what that pass costs
    per block at the two granularities.

A reference is a **target, not a contract**: the numbers were measured on
one machine, with one model, on one day.  The runner reports the gap; it
does not fail on it.

How the numbers were taken
--------------------------
* the quality is measured with the **voice instrument** of the project
  (``qa/qualityvoice/harness.py``) on the three voice cases of the
  benchmark (``qa/benchmark/cases.py``): the clip of the corpus, the
  synthetic voice under a white bed, and the speech shaped signal of the
  quality report;
* the latency is the figure the **module publishes** and the delay the
  output really has, found with a cross correlation of the envelopes;
* the cost is **timed** on the machine of the measurement, because a model
  cannot be counted the way a transform can: the load of the model file, the
  cost of one frame of inference, and the share of the render the model
  takes.  A number that moves with the load of the machine is an echo, not a
  budget, so every cost of this file says which machine took it and what it
  is in the yardstick of the project: one 1024 point transform is 8.300 us
  there, so the realtime budget of a 64 sample block is 1333.3 us = 160.6
  transforms.  A number that is not a per block cost says so;
* the **study of the reference's model library** was read from the outside:
  the files of the library were parsed and the strings were read, nothing
  was loaded and nothing was executed.  The cost of one pass of a model of
  that size is then arithmetic: the number of the weights, the rate the
  study computed at, and the granularity the model runs at.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "Metric",
    "METRICS",
    "METRIC_BY_NAME",
    "ModelReference",
    "MODEL_REFERENCES",
    "MODEL_REFERENCE_BY_NAME",
    "SERIES",
    "CASES",
    "COST_METRICS",
    "PER_BLOCK_COST",
    "EVERY_CASE",
    "REFERENCE_TRANSFORM_US",
    "BUDGET_US_PER_BLOCK",
    "BUDGET_TRANSFORMS_PER_BLOCK",
    "MODEL_LOAD_TRANSFORMS",
    "MODEL_FRAME_TRANSFORMS",
    "REFERENCE_MODEL_MAC_PER_SECOND",
    "REFERENCE_MODEL_AUDIO_RATE_STEPS_PER_BLOCK",
    "REFERENCE_MODEL_FRAME_RATE_STEPS_PER_BLOCK",
    "model_references_for",
    "model_reference_for",
]

#: The case that means "every case": a number that is a property of the
#: product and not of the material it is measured on.
EVERY_CASE = "*"

#: The yardstick of the project: the cost of one 1024 point transform on the
#: reference machine, in microseconds.  It is the unit the cost row of the
#: benchmark compares on, and a number of this file is converted into it.
REFERENCE_TRANSFORM_US = 8.300

#: The realtime budget of one block of 64 samples at 48 kHz, in microseconds,
#: and the same budget in transform equivalents.
BUDGET_US_PER_BLOCK = 1333.3333
BUDGET_TRANSFORMS_PER_BLOCK = 160.6426

#: The rate the cost of one pass of the model of the reference was computed
#: at: one core with a fused multiply-add.
REFERENCE_MODEL_MAC_PER_SECOND = 8.0e9

#: How many passes a 64 sample block costs at the audio rate: one per
#: sample, so a block costs 64 passes.
REFERENCE_MODEL_AUDIO_RATE_STEPS_PER_BLOCK = 64.0

#: How many passes a 64 sample block costs at the frame rate: one per 1024
#: samples, so a 64 sample block carries a sixteenth of a pass.
REFERENCE_MODEL_FRAME_RATE_STEPS_PER_BLOCK = 0.0625

#: The cost of the load of the model file and the cost of one frame of
#: inference, in the yardstick of the project, are **derived from the records
#: of the table** and are written down below it, where the records are: they
#: cannot drift from the milliseconds they came from.


@dataclass(frozen=True)
class Metric:
    """One number of the table and what a good number of it looks like."""

    name: str
    unit: str
    higher_is_better: bool
    note: str = ""


#: Every metric a model in the offline path is measured with.
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
            "band that carries the voice.  A number of removal bought with a "
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
            "How much the gain chatters on a voice: the roughness of the "
            "applied gain, with the wanted content taken out of the measurement."
        ),
    ),
    Metric(
        name="latency_samples",
        unit="samples",
        higher_is_better=False,
        note=(
            "How many samples the output lags the input: the look ahead of "
            "the module plus the context of the model that runs."
        ),
    ),
    Metric(
        name="model_load_ms",
        unit="ms",
        higher_is_better=False,
        note="How long the load of the model file takes.",
    ),
    Metric(
        name="model_frame_ms",
        unit="ms",
        higher_is_better=False,
        note="How long one frame of inference takes, 64 frames at a time.",
    ),
    Metric(
        name="model_file_share",
        unit="fraction",
        higher_is_better=False,
        note=(
            "How much of the render the model takes: the share of the wall "
            "clock time of the file render that the model is responsible for."
        ),
    ),
    # -- the anatomy of the model library of the reference --------------------
    Metric(
        name="parameter_count",
        unit="parameters",
        higher_is_better=False,
        note=(
            "How many weights a model of the reference holds: the words of "
            "the payload, counted without loading anything."
        ),
    ),
    Metric(
        name="weight_bytes",
        unit="bytes",
        higher_is_better=False,
        note=(
            "How big the decoded payload of the weights of a model of the "
            "reference is."
        ),
    ),
    Metric(
        name="mac_per_network_sample",
        unit="MAC",
        higher_is_better=False,
        note=(
            "What one pass of a model of the reference costs: one "
            "multiply-accumulate per weight per output element."
        ),
    ),
    Metric(
        name="us_per_block",
        unit="us",
        higher_is_better=False,
        note=(
            "What one 64 sample block costs, at the audio rate or at the "
            "frame rate: the two granularities are the two ends of the cost "
            "arithmetic of the study."
        ),
    ),
    Metric(
        name="budget_share_percent",
        unit="per cent",
        higher_is_better=False,
        note=(
            "How much of the block budget the model takes at the frame rate."
        ),
    ),
    Metric(
        name="model_count",
        unit="models",
        higher_is_better=False,
        note=(
            "How many trained models the library of the reference ships: they "
            "are artefacts of their own, not configurations of one file."
        ),
    ),
    Metric(
        name="generation_count",
        unit="generations",
        higher_is_better=False,
        note=(
            "How many dated architecture generations the models of the "
            "library come in."
        ),
    ),
    Metric(
        name="shared_float_words",
        unit="words",
        higher_is_better=False,
        note=(
            "How many float words the two heaviest models of the library have "
            "in common: the weights are trained per model."
        ),
    ),
    Metric(
        name="layer_depth",
        unit="layers",
        higher_is_better=False,
        note=(
            "How many layer objects one pass of a model of the reference "
            "crosses: the depth of the stack, counted from the code."
        ),
    ),
)

#: The metrics, keyed by name.
METRIC_BY_NAME: dict[str, Metric] = {metric.name: metric for metric in METRICS}

#: The series the numbers are filed under, keyed by their name.
SERIES: dict[str, str] = {
    "realtime_hand_written": (
        "the module without a model, in the real time configuration: the path "
        "that may not change"
    ),
    "offline_hand_written": (
        "the module without a model, in the offline configuration: the floor "
        "of the offline path"
    ),
    "offline_with_model": (
        "the module with a model of the library running on it, in the offline "
        "configuration"
    ),
    "reference_model": (
        "the study of the model library of the reference: the anatomy of a "
        "model artefact read from the outside, and the arithmetic of what it "
        "costs at the two granularities"
    ),
}

#: The cases the numbers of the file belong to.  ``"*"`` is every case: the
#: size of a model, the cost of an instance and the latency of a product are
#: properties of the product, not of the material it runs on.  The two
#: granularities are the two ends of the cost arithmetic of the study, and
#: the three voice cases are the voice cases of the benchmark
#: (``qa/benchmark/cases.py``).
CASES: tuple[str, ...] = (
    EVERY_CASE,
    "audio_rate",
    "frame_rate",
    "corpus_voice",
    "synthetic_voice",
    "speech_shaped",
)

#: The metrics of the file that carry a cost.
COST_METRICS: frozenset[str] = frozenset(
    {"model_load_ms", "model_frame_ms", "model_file_share"}
)

#: The costs of the file, and what each of them is a cost of.  ``False``
#: means the cost is **not** the cost of one 64 sample block, so it must not
#: be compared with the block budget: a load happens once per instance, a
#: frame of inference is not a block, and a share of a render is not a cost
#: in time at all.  A cost that does not say which of the two it is is a
#: trap, so every cost of the file is in this table.
PER_BLOCK_COST: dict[str, bool] = {
    "model_load_ms": False,
    "model_frame_ms": False,
    "model_file_share": False,
}


@dataclass(frozen=True)
class ModelReference:
    """One number of the model path, with everything needed to read it."""

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


#: How the quality numbers were taken.
_QUALITY_METHOD = (
    "measured with the voice instrument of the project "
    "(qa/qualityvoice/harness.py) on the case, with the module of ours that "
    "owns it, in the configuration of the series"
)

#: How the latency was taken.
_LATENCY_METHOD = (
    "the figure the module of ours publishes, checked against the delay of the "
    "output "
    "found with a cross correlation of the envelopes"
)

#: The measured cost of the load of the model file, in milliseconds.  It is
#: timed, so it moves with the load of the machine: it is a measurement of
#: one load, not a cost of a block.
_LOAD_MS = 12.57

#: The measured cost of one frame of inference, in milliseconds.  A frame is
#: 64 inferences at a time, and the size of a frame in samples is not written
#: down in this tree.
_FRAME_MS = 18.17

#: The measured share of the wall clock time of a render the model takes.
_FILE_SHARE = 0.3939

#: How the cost of a load was taken, and where it stands.
#:
#: The numbers of the method are **derived from the measurement**, so the
#: prose of a cost cannot drift from the number it describes: a cost that
#: was measured again is written down once, here, and everything that
#: follows it follows.
_COST_LOAD_METHOD = (
    "timed with our own clock on the machine of the measurement; a model cannot "
    "be counted the "
    "way a transform can, so the numbers move with the load of the machine.  "
    "The yardstick of the project is fixed: 8.300 us per 1024 point "
    "transform, so the realtime budget of a 64 sample block is 1333.3 us = "
    "160.6 transforms.  A load happens once per instance and not once per "
    "block, so this is not a per-block cost: "
    f"{_LOAD_MS} ms is {_LOAD_MS * 1000.0:.1f} us, that is "
    f"{_LOAD_MS * 1000.0 / REFERENCE_TRANSFORM_US:.2f} transforms of "
    f"{REFERENCE_TRANSFORM_US:.3f} us, that is "
    f"{_LOAD_MS * 1000.0 / BUDGET_US_PER_BLOCK:.1f} times the budget of a 64 "
    "sample block"
)

#: How the cost of one frame of inference was taken, and where it stands.
_COST_FRAME_METHOD = (
    "timed with our own clock on the machine of the measurement; a model cannot "
    "be counted the "
    "way a transform can, so the numbers move with the load of the machine.  "
    "The yardstick of the project is fixed: 8.300 us per 1024 point "
    "transform, so the realtime budget of a 64 sample block is 1333.3 us = "
    "160.6 transforms.  The number is the cost of one frame of inference of "
    "the model of the reference; the size of that frame in samples is not "
    "written down in this tree, so the cost of a 64 sample block cannot be "
    f"derived from it: {_FRAME_MS} ms is {_FRAME_MS * 1000.0:.1f} us, that is "
    f"{_FRAME_MS * 1000.0 / REFERENCE_TRANSFORM_US:.2f} transforms of "
    f"{REFERENCE_TRANSFORM_US:.3f} us per frame of inference"
)

#: How the share of the render the model takes was taken.
_COST_SHARE_METHOD = (
    "timed with our own clock on the machine of the measurement; a model cannot "
    "be counted the "
    "way a transform can, so the numbers move with the load of the machine.  "
    "The number is a share of the wall clock time of a render and not a cost "
    "in time, so it is not on the yardstick of the project (8.300 us per "
    "1024 point transform, so the realtime budget of a 64 sample block is "
    "1333.3 us = 160.6 transforms); it says how much of a render the model "
    "is responsible for"
)


def _quality(name: str, series: str, case: str, metric: str, value: float) -> ModelReference:
    """One quality number of the model path."""
    return ModelReference(
        name=name,
        series=series,
        case=case,
        metric=metric,
        value=float(value),
        unit=METRIC_BY_NAME[metric].unit,
        method=_QUALITY_METHOD,
        setting=SERIES[series],
    )


#: The tag of a series in the name of its numbers.
_SERIES_TAG: dict[str, str] = {
    "realtime_hand_written": "REALTIME",
    "offline_hand_written": "OFFLINE",
    "offline_with_model": "MODEL",
}

#: The quality of the three voice cases, in the three configurations.
#:
#: The rows are: the case, the series, the removal, the movement of the
#: wanted content, the variance of the gain curve and the chatter of the
#: gain.  The numbers are the ones of the measurement of wave 35 (see
#: ``var/w35/measurements.json``).
_QUALITY_TABLE: tuple[tuple[str, str, float, float, float, float], ...] = (
    ("corpus_voice", "realtime_hand_written", 17.8450, 0.0520, 0.01050, 0.0130),
    ("corpus_voice", "offline_hand_written", 17.9850, 0.0660, 0.00080, 0.0010),
    ("corpus_voice", "offline_with_model", 21.7620, 0.0660, 0.05050, 0.1620),
    ("synthetic_voice", "realtime_hand_written", 17.8470, 0.1350, 0.01890, 0.0250),
    ("synthetic_voice", "offline_hand_written", 17.9230, 0.0550, 0.02060, 0.0050),
    ("synthetic_voice", "offline_with_model", 21.0840, 0.0550, 0.17230, 0.1530),
    ("speech_shaped", "realtime_hand_written", 17.8640, 0.1040, 0.02110, 0.0180),
    ("speech_shaped", "offline_hand_written", 17.9760, 0.2250, 0.00380, 0.0010),
    ("speech_shaped", "offline_with_model", 21.2560, 0.2250, 0.13770, 0.2670),
)

#: The metrics of a row of the quality table, in the order they stand in.
_QUALITY_METRICS: tuple[str, ...] = ("removed_db", "kept_db", "gain_var_db2", "chatter_db")


def _quality_numbers() -> tuple[ModelReference, ...]:
    """The quality numbers of the table, one reference per metric."""
    out: list[ModelReference] = []
    for case, series, *values in _QUALITY_TABLE:
        stem = f"{case.upper()}_{_SERIES_TAG[series]}"
        for metric, value in zip(_QUALITY_METRICS, values):
            out.append(
                _quality(
                    f"{stem}_{metric.upper()}", series, case, metric, float(value)
                )
            )
    return tuple(out)


# ---------------------------------------------------------------------------
# the study of the model library of the reference
#
# The library was read **from the outside**: the files were parsed, the
# strings were read and the code was disassembled.  Nothing was loaded and
# nothing was executed, so none of these numbers is a claim about what a
# model of that library sounds like.  They are the anatomy of a model
# artefact, and the arithmetic of what such a model costs.
# ---------------------------------------------------------------------------

#: How the anatomy of the library was measured.
_ANATOMY_METHOD = (
    "read from the outside with our own reader, from the payload of the "
    "library "
    "of the reference: "
    "the files were parsed and the strings were read, nothing was loaded and "
    "nothing was executed"
)

#: How the size of the weights was measured.
_WEIGHT_METHOD = (
    "the decoded payload of the weights of the class, in bytes: the text of "
    "the payload was decoded by our own reader and the length of the result "
    "was taken"
)

#: How the cost of one pass of a model was computed.
_PASS_METHOD = (
    "one multiply-accumulate per weight per output element of a convolution, "
    "at the rate the study's own cost model computed at: one core with a "
    "fused multiply-add"
)

#: How the cost of a block was computed at the audio rate.
_AUDIO_RATE_METHOD = (
    "the cost of one 64 sample block of a model that runs once per audio "
    "sample: 64 passes of the largest model of the library, at the rate the "
    "study's own cost model computed at"
)

#: How the cost of a block was computed at the frame rate.
_FRAME_RATE_METHOD = (
    "the cost of one 64 sample block of a model that runs once per spectral "
    "frame: with a hop of 1024 samples a 64 sample block carries a sixteenth "
    "of a pass, at the rate the study's own cost model computed at"
)

#: How the budget of the project was written down.
_BUDGET_METHOD = (
    "the realtime budget of ours, computed by our own clock: 64 samples at "
    "48 kHz, so the time a "
    "block of the real time path has to be played in"
)

#: How the share of the budget was computed.
_SHARE_METHOD = (
    "the share of the block budget of ours the frame rate cost takes at the "
    "top of the band, in per cent, computed by our own clock"
)

#: How the size of the library was counted.
_LIBRARY_METHOD = (
    "counted by our own reader from the library of the reference: the digests "
    "of the artefacts "
    "and the dates of the generations they come in"
)

#: How the depth of a stack was counted.
_DEPTH_METHOD = (
    "counted by our own reader from the code of the library of the reference "
    "and not from the "
    "names in the data: the number of the layer objects one pass crosses"
)

#: The numbers of the study.  The series is its own: none of them is a
#: measurement of ours, and none of them belongs to a material.
_STUDY_REFERENCES: tuple[ModelReference, ...] = (
    # -- the size of the models ----------------------------------------------
    ModelReference(
        name="REFERENCE_MODEL_PARAMETER_COUNT_LARGE",
        series="reference_model",
        case=EVERY_CASE,
        metric="parameter_count",
        value=7530937.0,
        unit="parameters",
        method=_ANATOMY_METHOD,
        setting="the largest model of the library of the reference",
    ),
    ModelReference(
        name="REFERENCE_MODEL_PARAMETER_COUNT_MEDIUM",
        series="reference_model",
        case=EVERY_CASE,
        metric="parameter_count",
        value=4205625.0,
        unit="parameters",
        method=_ANATOMY_METHOD,
        setting="the middle model of the library of the reference",
    ),
    ModelReference(
        name="REFERENCE_MODEL_PARAMETER_COUNT_SMALL",
        series="reference_model",
        case=EVERY_CASE,
        metric="parameter_count",
        value=2381156.0,
        unit="parameters",
        method=_ANATOMY_METHOD,
        setting="the smallest model of the library of the reference",
    ),
    ModelReference(
        name="REFERENCE_MODEL_WEIGHT_BYTES_LARGE",
        series="reference_model",
        case=EVERY_CASE,
        metric="weight_bytes",
        value=30123750.0,
        unit="bytes",
        method=_WEIGHT_METHOD,
        setting="the weights of the largest model of the library",
    ),
    ModelReference(
        name="REFERENCE_MODEL_WEIGHT_BYTES_MEDIUM",
        series="reference_model",
        case=EVERY_CASE,
        metric="weight_bytes",
        value=16822500.0,
        unit="bytes",
        method=_WEIGHT_METHOD,
        setting="the weights of the middle model of the library",
    ),
    ModelReference(
        name="REFERENCE_MODEL_WEIGHT_BYTES_SMALL",
        series="reference_model",
        case=EVERY_CASE,
        metric="weight_bytes",
        value=9524625.0,
        unit="bytes",
        method=_WEIGHT_METHOD,
        setting="the weights of the smallest model of the library",
    ),
    # -- the cost of one pass ------------------------------------------------
    ModelReference(
        name="REFERENCE_MODEL_MAC_PER_NETWORK_SAMPLE",
        series="reference_model",
        case=EVERY_CASE,
        metric="mac_per_network_sample",
        value=7530937.0,
        unit="MAC",
        method=_PASS_METHOD,
        setting="one pass of the largest model of the library of the reference",
    ),
    # -- the cost at the two granularities ------------------------------------
    ModelReference(
        name="REFERENCE_MODEL_AUDIO_RATE_US_PER_BLOCK",
        series="reference_model",
        case="audio_rate",
        metric="us_per_block",
        value=60000.0,
        unit="us",
        method=_AUDIO_RATE_METHOD,
        setting=(
            "the largest model of the library, running once per audio sample: "
            "the figure is rounded to a whole millisecond"
        ),
    ),
    ModelReference(
        name="REFERENCE_MODEL_FRAME_RATE_US_PER_BLOCK_MAX",
        series="reference_model",
        case="frame_rate",
        metric="us_per_block",
        value=59.0,
        unit="us",
        method=_FRAME_RATE_METHOD,
        setting=(
            "the largest model of the library, running once per spectral "
            "frame: the top of the band"
        ),
    ),
    ModelReference(
        name="REFERENCE_MODEL_FRAME_RATE_US_PER_BLOCK_MIN",
        series="reference_model",
        case="frame_rate",
        metric="us_per_block",
        value=19.0,
        unit="us",
        method=_FRAME_RATE_METHOD,
        setting=(
            "the smallest model of the library, running once per spectral "
            "frame: the bottom of the band"
        ),
    ),
    # -- the budget and the share of it --------------------------------------
    ModelReference(
        name="REFERENCE_MODEL_BLOCK_BUDGET_US",
        series="reference_model",
        case=EVERY_CASE,
        metric="us_per_block",
        value=1333.0,
        unit="us",
        method=_BUDGET_METHOD,
        setting="the budget every cost of the tree is measured against",
    ),
    ModelReference(
        name="REFERENCE_MODEL_BUDGET_SHARE_PERCENT",
        series="reference_model",
        case="frame_rate",
        metric="budget_share_percent",
        value=4.0,
        unit="per cent",
        method=_SHARE_METHOD,
        setting=(
            "the share of the budget the frame rate cost takes at the top of "
            "the band, rounded to a whole per cent"
        ),
    ),
    # -- the library ---------------------------------------------------------
    ModelReference(
        name="REFERENCE_MODEL_COUNT",
        series="reference_model",
        case=EVERY_CASE,
        metric="model_count",
        value=7.0,
        unit="models",
        method=_LIBRARY_METHOD,
        setting="the models the library of the reference ships",
    ),
    ModelReference(
        name="REFERENCE_MODEL_GENERATION_COUNT",
        series="reference_model",
        case=EVERY_CASE,
        metric="generation_count",
        value=3.0,
        unit="generations",
        method=_LIBRARY_METHOD,
        setting="the dated architecture generations the models come in",
    ),
    ModelReference(
        name="REFERENCE_MODEL_MAX_SHARED_FLOAT_WORDS",
        series="reference_model",
        case=EVERY_CASE,
        metric="shared_float_words",
        value=1233.0,
        unit="words",
        method=_LIBRARY_METHOD,
        setting=(
            "the float words the two heaviest models of the library have in "
            "common: the weights are trained per model"
        ),
    ),
    # -- the depth of the stacks ---------------------------------------------
    ModelReference(
        name="REFERENCE_MODEL_DEPTH_SMALL",
        series="reference_model",
        case=EVERY_CASE,
        metric="layer_depth",
        value=12.0,
        unit="layers",
        method=_DEPTH_METHOD,
        setting="one pass of the smallest model of the library",
    ),
    ModelReference(
        name="REFERENCE_MODEL_DEPTH_MEDIUM",
        series="reference_model",
        case=EVERY_CASE,
        metric="layer_depth",
        value=23.0,
        unit="layers",
        method=_DEPTH_METHOD,
        setting="one pass of the middle model of the library",
    ),
    ModelReference(
        name="REFERENCE_MODEL_DEPTH_LARGE",
        series="reference_model",
        case=EVERY_CASE,
        metric="layer_depth",
        value=47.0,
        unit="layers",
        method=_DEPTH_METHOD,
        setting="one pass of the largest model of the library",
    ),
)


# ---------------------------------------------------------------------------
# the numbers
# ---------------------------------------------------------------------------
MODEL_REFERENCES: tuple[ModelReference, ...] = _quality_numbers() + (
    # -- the latency ---------------------------------------------------------
    ModelReference(
        name="REALTIME_LATENCY_SAMPLES",
        series="realtime_hand_written",
        case=EVERY_CASE,
        metric="latency_samples",
        value=2047.0,
        unit="samples",
        method=_LATENCY_METHOD,
        setting="the real time configuration, without a model",
    ),
    ModelReference(
        name="OFFLINE_LATENCY_SAMPLES",
        series="offline_hand_written",
        case=EVERY_CASE,
        metric="latency_samples",
        value=4095.0,
        unit="samples",
        method=_LATENCY_METHOD,
        setting="the offline configuration, without a model",
    ),
    ModelReference(
        name="OFFLINE_MODEL_LATENCY_SAMPLES",
        series="offline_with_model",
        case=EVERY_CASE,
        metric="latency_samples",
        value=4095.0,
        unit="samples",
        method=_LATENCY_METHOD,
        setting=(
            "the offline configuration with the voice model of the library: it "
            "reads no history, so its context is zero and the figure is the "
            "one of the offline configuration"
        ),
    ),
    ModelReference(
        name="OFFLINE_MODEL_CONTEXT_SAMPLES",
        series="offline_with_model",
        case=EVERY_CASE,
        metric="latency_samples",
        value=4607.0,
        unit="samples",
        method=_LATENCY_METHOD,
        setting=(
            "the offline configuration with a model that reads two frames of "
            "history: the figure is the look ahead of the configuration plus "
            "the 512 samples of context the model publishes"
        ),
    ),
    # -- the cost ------------------------------------------------------------
    ModelReference(
        name="MODEL_LOAD_MS",
        series="offline_with_model",
        case=EVERY_CASE,
        metric="model_load_ms",
        value=_LOAD_MS,
        unit="ms",
        method=_COST_LOAD_METHOD,
        setting="the median of five loads of the model file of the library",
    ),
    ModelReference(
        name="MODEL_FRAME_MS",
        series="offline_with_model",
        case=EVERY_CASE,
        metric="model_frame_ms",
        value=_FRAME_MS,
        unit="ms",
        method=_COST_FRAME_METHOD,
        setting="64 frames of inference, the fastest of twenty observations",
    ),
    ModelReference(
        name="MODEL_FILE_SHARE",
        series="offline_with_model",
        case=EVERY_CASE,
        metric="model_file_share",
        value=_FILE_SHARE,
        unit="fraction",
        method=_COST_SHARE_METHOD,
        setting=(
            "the share of the wall clock time of the render of the synthetic "
            "voice case that the model takes, the fastest of three renders"
        ),
    ),
) + _STUDY_REFERENCES

#: The numbers of the model path, keyed by the name of their constant.
MODEL_REFERENCE_BY_NAME: dict[str, ModelReference] = {
    reference.name: reference for reference in MODEL_REFERENCES
}

#: The cost of the load of the model file and the cost of one frame of
#: inference, in the yardstick of the project.  They are **derived from the
#: records of the table**, so they cannot drift from the milliseconds they
#: came from.
MODEL_LOAD_TRANSFORMS = _LOAD_MS * 1000.0 / REFERENCE_TRANSFORM_US
MODEL_FRAME_TRANSFORMS = _FRAME_MS * 1000.0 / REFERENCE_TRANSFORM_US


def model_references_for(case: str, metric: str) -> tuple[ModelReference, ...]:
    """Every number of a case and a metric, one per series.

    A number recorded under the name ``"*"`` belongs to every case: the
    latency and the cost of a product are properties of the product, not of
    the material.
    """
    return tuple(
        reference
        for reference in MODEL_REFERENCES
        if reference.metric == metric and reference.case in (case, EVERY_CASE)
    )


def model_reference_for(case: str, metric: str, series: str | None = None) -> float | None:
    """The number a case and a metric are held to.

    Without a series the **strongest** number is the reference: the gap that
    is worth closing is the one against the best of them.  With a series the
    number of that series is the reference.
    """
    found = [
        reference
        for reference in model_references_for(case, metric)
        if series is None or reference.series == series
    ]
    if not found:
        return None
    rule = METRIC_BY_NAME[metric]
    if rule.higher_is_better:
        return max(float(reference.value) for reference in found)
    return min(float(reference.value) for reference in found)
