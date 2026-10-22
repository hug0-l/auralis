"""
qa.benchmark.reference_restoration — the numbers of the restoration study.

Every number in this file is a **measurement of a reference plug-in**, taken
with the same instruments the project measures ours with, so that the two sides
of the comparison are measured with the same code on the same machine.  Three
of the numbers are measurements of **our** modules instead; they say so in
their series, because a number of ours and a number of a reference must never
be mistaken for one another.

The shape is the shape of ``qa/benchmark/reference.py``: a number is a number,
it carries no name, no code and no parameter of the product it was measured
on, and what travels with it is the **case** it belongs to, the **unit** it is
in and the **way it was measured**.  A number without those three cannot be
read, cannot be argued about and cannot be replaced when a better measurement
arrives.

Two fields are added to that shape.  ``name`` is the name of the constant, so
that a reader can go from a number to the code that carries it.  ``setting``
says which setting of the reference the number belongs to: the same reference
at two settings gives two numbers on the same case, and without the setting the
two would be indistinguishable.

A reference is a **target, not a contract**.  It was measured on somebody
else's build, on somebody else's machine, with somebody else's settings, so
the benchmark reports the gap and does not fail on it.

The two references are described by their shape and never by their names:

``band_restoration``
    the one that picks a trained model and gives it four bands (22 parameters,
    7 modes in 16 slots);
``stem_separation``
    the one that splits a recording into three stems and gives each of them a
    gain and three switches (17 parameters, 6 programs).

``ours`` is not a reference.  It is the series the measurements of our own
modules are filed under, so that a reader can tell at a glance which side of
the comparison a number is on.

The design note that reads these numbers, says which of them we adopt and
records what could not be measured is ``docs/RESTORATION_REFERENCE.md``.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "CASES",
    "EVERY_CASE",
    "Metric",
    "METRICS",
    "METRIC_BY_NAME",
    "Restoration",
    "RESTORATIONS",
    "RESTORATION_BY_NAME",
    "SERIES",
    "restorations_for",
    "restoration_for",
]


@dataclass(frozen=True)
class Metric:
    """One number of the table and what a good number of it looks like."""

    name: str
    unit: str
    higher_is_better: bool
    note: str = ""


#: Every metric the restoration study measured.
METRICS: tuple[Metric, ...] = (
    Metric(
        name="removed_db",
        unit="dB",
        higher_is_better=True,
        note="How much of the artefact is taken away.",
    ),
    Metric(
        name="kept_db",
        unit="dB",
        higher_is_better=False,
        note="How far the wanted content moves.",
    ),
    Metric(
        name="untouched_level_db",
        unit="dB",
        higher_is_better=False,
        note=(
            "How far the level of the material the reference does not touch "
            "moves."
        ),
    ),
    Metric(
        name="chatter_db2",
        unit="dB^2",
        higher_is_better=False,
        note="How much the gain curve breathes while a voice speaks.",
    ),
    Metric(
        name="decision_ms",
        unit="ms",
        higher_is_better=False,
        note=(
            "How long the reference takes to let a wanted voice through, "
            "measured as the time to 63 % of the step."
        ),
    ),
    Metric(
        name="decision_lag_ms",
        unit="ms",
        higher_is_better=False,
        note="How long the decision of the reference lags behind its latency.",
    ),
    Metric(
        name="cost_ms",
        unit="ms",
        higher_is_better=False,
        note=(
            "How long the reference takes for one block of 512 frames, as the "
            "study measured it.  The method of the number carries the "
            "conversion into the yardstick of the project: the cost of one 64 "
            "sample block in 1024 point transforms of 8.300 us, which is the "
            "unit the cost row of the benchmark compares on."
        ),
    ),
    Metric(
        name="latency_samples",
        unit="samples",
        higher_is_better=False,
        note="How many samples the output lags the input.",
    ),
    Metric(
        name="tail_samples",
        unit="samples",
        higher_is_better=False,
        note="How many samples the output keeps ringing after the input stopped.",
    ),
    Metric(
        name="state_bytes",
        unit="bytes",
        higher_is_better=False,
        note="How large the state the reference carries is.",
    ),
    Metric(
        name="surface_parameters",
        unit="parameters",
        higher_is_better=False,
        note="How many parameters the reference publishes.",
    ),
    Metric(
        name="surface_modes",
        unit="modes",
        higher_is_better=False,
        note="How many modes the reference really has.",
    ),
    Metric(
        name="surface_mode_slots",
        unit="slots",
        higher_is_better=False,
        note="How many slots the mode selector of the reference offers.",
    ),
    Metric(
        name="surface_program_list",
        unit="entries",
        higher_is_better=False,
        note="How many entries the host preset list of the reference carries.",
    ),
    Metric(
        name="models_shipped",
        unit="models",
        higher_is_better=False,
        note="How many trained models the reference ships as data.",
    ),
    Metric(
        name="model_size_bytes",
        unit="bytes",
        higher_is_better=False,
        note="How large one trained model of the reference is on disk.",
    ),
    Metric(
        name="split_roundtrip_db",
        unit="dB",
        higher_is_better=False,
        note=(
            "What the band split of the reference costs the signal in rms, "
            "measured by sending a tone through the split and back."
        ),
    ),
    Metric(
        name="split_delay_samples",
        unit="samples",
        higher_is_better=False,
        note="How many samples the band split of the reference delays.",
    ),
    Metric(
        name="phase_correlation",
        unit="correlation",
        higher_is_better=True,
        note=(
            "The correlation of the input with the output, for two tones a "
            "quarter of a period apart.  One means the phase survives."
        ),
    ),
    Metric(
        name="curve_exponent",
        unit="exponent",
        higher_is_better=False,
        note="The exponent of the law the travel of a control of the reference follows.",
    ),
    Metric(
        name="range_low_db",
        unit="dB",
        higher_is_better=False,
        note="The lower end of the range of a gain control of the reference.",
    ),
    Metric(
        name="range_high_db",
        unit="dB",
        higher_is_better=False,
        note="The upper end of the range of a gain control of the reference.",
    ),
    Metric(
        name="range_low_hz",
        unit="Hz",
        higher_is_better=False,
        note="The lower end of the range of a frequency control of the reference.",
    ),
    Metric(
        name="range_high_hz",
        unit="Hz",
        higher_is_better=False,
        note="The upper end of the range of a frequency control of the reference.",
    ),
    Metric(
        name="db_per_percent_at_unity",
        unit="dB per 1 %",
        higher_is_better=False,
        note="How many decibels one percent of the travel is worth at unity.",
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
        name="output_level_change_db",
        unit="dB",
        higher_is_better=False,
        note=(
            "How far the level of the whole output moves against the level of "
            "the input.  A removal figure has to be read with this one: an "
            "output far below its input may be silence."
        ),
    ),
    Metric(
        name="output_silent_share",
        unit="fraction",
        higher_is_better=False,
        note=(
            "The share of the samples of the output that are digital silence.  "
            "A removal figure bought with silence is a replacement of the "
            "signal, not a removal of the artefact."
        ),
    ),
    Metric(
        name="reproducible",
        unit="flag",
        higher_is_better=True,
        note=(
            "Whether the same setting on the same input gives the same "
            "waveform.  One means bit identical, zero means it does not."
        ),
    ),
    Metric(
        name="reproducibility_spread_db",
        unit="dB",
        higher_is_better=False,
        note=(
            "How much the figures move between two runs of the same setting."
        ),
    ),
    Metric(
        name="default_crossover_hz",
        unit="Hz",
        higher_is_better=False,
        note=(
            "The frequency of the first band edge of the reference, as it "
            "comes.  A wanted partial that sits on a crossover is split "
            "between two bands, and the level of the partial after the split "
            "is not the level of the partial before it."
        ),
    ),
    Metric(
        name="bypass_bit_exact",
        unit="flag",
        higher_is_better=True,
        note=(
            "Whether the bypass hands the samples back untouched.  One means "
            "the largest difference of the samples is exactly zero."
        ),
    ),
    Metric(
        name="bypass_max_diff",
        unit="sample difference",
        higher_is_better=False,
        note=(
            "The largest difference between the samples of the bypass and the "
            "samples of the input.  Zero means the bypass is sample exact."
        ),
    ),
    Metric(
        name="bypass_delay_samples",
        unit="samples",
        higher_is_better=False,
        note=(
            "How many samples the bypassed path delays the signal.  A bypass "
            "that delays the signal is exact in the samples and wrong in the "
            "time."
        ),
    ),
    Metric(
        name="bypass_delay_ms",
        unit="ms",
        higher_is_better=False,
        note=(
            "The delay of the bypassed path in milliseconds, at 48 kHz."
        ),
    ),
    Metric(
        name="default_bit_exact",
        unit="flag",
        higher_is_better=True,
        note=(
            "Whether the default setting leaves a clean signal exactly "
            "alone.  One means the largest difference of the samples is "
            "exactly zero."
        ),
    ),
    Metric(
        name="default_max_diff",
        unit="sample difference",
        higher_is_better=False,
        note=(
            "The largest difference between the samples of the default "
            "setting and the samples of the clean input, aligned with the "
            "latency the product declares."
        ),
    ),
    Metric(
        name="default_max_diff_db",
        unit="dB",
        higher_is_better=False,
        note=(
            "The same difference, in decibels below the peak of the input."
        ),
    ),
    Metric(
        name="default_max_diff_steps",
        unit="steps of 24 bit",
        higher_is_better=False,
        note=(
            "The same difference, counted in steps of a 24 bit word."
        ),
    ),
    Metric(
        name="default_gain_db",
        unit="dB",
        higher_is_better=False,
        note=(
            "The static gain the default path applies to a steady tone.  The "
            "ideal is 0 dB, and the figure is read as the distance from it."
        ),
    ),
    Metric(
        name="default_gain_spread_db",
        unit="dB",
        higher_is_better=False,
        note=(
            "How far the applied gain moves between the levels of the input, "
            "peak to peak.  A gain that follows the level of the input "
            "gates a quiet recording away."
        ),
    ),
    Metric(
        name="default_gain_sd_db",
        unit="dB",
        higher_is_better=False,
        note=(
            "The standard deviation of the applied gain over the material."
        ),
    ),
    Metric(
        name="noise_floor_dbfs",
        unit="dBFS",
        higher_is_better=False,
        note=(
            "The level of the floor the default path puts in front of the "
            "programme, before the signal arrives."
        ),
    ),
)

#: The metrics, keyed by name.
METRIC_BY_NAME: dict[str, Metric] = {metric.name: metric for metric in METRICS}

#: The series the numbers are filed under, keyed by their name.
SERIES: dict[str, str] = {
    "band_restoration": (
        "the reference that picks a trained model and gives it four bands "
        "(22 parameters, 7 modes in 16 slots)"
    ),
    "stem_separation": (
        "the reference that splits a recording into three stems and gives each "
        "of them a gain and three switches (17 parameters, 6 programs)"
    ),
    "ours": (
        "not a reference: the measurements of our own modules, filed under "
        "their own series so that they are never mistaken for one"
    ),
}


@dataclass(frozen=True)
class Restoration:
    """One number of the restoration study, with everything needed to read it."""

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


#: The case that means "every case": a number that is a property of the
#: product and not of the material it is measured on.
EVERY_CASE = "*"

#: The cases the numbers belong to, and what each of them is.  A case of the
#: study that the benchmark of the project does not measure is named here as
#: well, so that the vocabulary of this file is written down in one place.
CASES: dict[str, str] = {
    "hiss": "a steady white bed with nothing wanted in it",
    "pink_hiss": "the same bed with a pink spectrum",
    "hum50": "mains hum with its first harmonic under a 1 kHz programme tone",
    "rumble": "low frequency rumble under a 1 kHz programme tone",
    "voice_hiss": (
        "a voice under hiss: the corpus case of the project, the one the "
        "study measured the references on"
    ),
    "corpus_voice": "the voice clip of the corpus",
    "synthetic_voice": "the synthetic voice under a white bed 45 dB down",
    "speech_shaped": "the speech shaped signal of the quality report",
    "plosive": (
        "a vowel with a burst of air in front of it: the case of the corpus "
        "the study measured the reference on besides the beds"
    ),
    "reverb": "the reverberant case of the study",
    "clean_music": (
        "the clean musical mixture of the corpus: there is nothing to repair "
        "in it, so it is the material the bypass and the defaults are "
        "measured on"
    ),
    "sine_probe": (
        "a steady 1 kHz tone at -6, -20, -40 and -60 dBFS: the probes the "
        "gain of the default path is measured at"
    ),
    EVERY_CASE: (
        "every case: the number is a property of the product and not of the "
        "material it is measured on"
    ),
}

#: How the numbers of the corpus cases were taken.
#:
#: The instrument is named, because the study has more than one host and only
#: one of them moves a parameter of its own and never puts it back (see
#: docs/BENCHMARK_NOTES.md, section 5.1).  This is the study's driver: it sets
#: only the parameters it was asked for.
_CORPUS_METHOD = (
    "the reference rendered through the study's driver, which sets only the "
    "parameters it was asked for, and "
    "measured with the ruler of this project: the bands of the corpus "
    "manifest and the quality instrument (qa/quality/measurement.py)"
)

#: How the numbers of the voice cases were taken.
_VOICE_METHOD = (
    "the reference rendered through the study's driver, which sets only the "
    "parameters it was asked for, and "
    "measured with the ruler of this project: the voice instrument "
    "(qa/qualityvoice/harness.py)"
)

#: How the surface of a reference was read.
_SURFACE_METHOD = (
    "the surface read out of the controller of the reference with the study's "
    "surface host, which reads the controller and changes nothing: every "
    "parameter with its name, its unit, its steps, its "
    "flags, its default, and the value it shows at 41 points across its travel"
)

#: How the cost of a reference was taken, and how it is converted into the
#: yardstick of the project.
_COST_METHOD = (
    "the cost of one 512 frame block of the reference, as the study's renderer "
    "timed it, next to a 1024 "
    "point transform of the study machine (the rule of our own performance "
    "harness).  The study machine is not the reference machine, so the "
    "number is not in the yardstick of the project as it stands, and the "
    "conversion is written down with it: a 512 frame block is eight 64 "
    "sample blocks, and one 1024 point transform is 8.300 us on the "
    "reference machine, so a block of real time at 48 kHz may cost 160.6 "
    "transforms.  Converted: %s ms per 512 frame block is %s us per 64 "
    "sample block = %s transforms of 8.300 us.  The channel count and the "
    "material of that measurement are not written down in this tree, so the "
    "number is a figure per block of the study's own rendering"
)

#: How the latency of a reference was taken.
_LATENCY_METHOD = (
    "the latency the reference declares to the host, read out of its "
    "controller, and the same figure measured on the render of the study's "
    "renderer"
)

#: How the numbers of our own modules were taken.
_OURS_METHOD = (
    "measured by the study on our own modules, with the study's renderer, on "
    "the tree of the study's day; the number is a measurement of ours and not "
    "of the reference.  The instrument of the study pairs the input and the "
    "output 2 L samples apart, so the decision time it measured on our voice "
    "module is 85.3 ms longer than the decision the module really takes"
)

#: How the numbers of the corpus head-to-head were taken.
_SIGNAL_METHOD = (
    "the corpus of the project measured with the ruler of the project: the "
    "bands of the corpus manifest and the quality instrument "
    "(qa/quality/measurement.py); the reference was rendered by the study's "
    "driver, which sets only the parameters it was asked for, "
    "at 48 kHz with "
    "512 frame blocks, and every figure is aligned with the latency the "
    "reference declares for that setting"
)

#: The setting the reference comes with.
_DEFAULT_SETTING_METHOD = (
    "the setting the reference comes with: the one a user hears before "
    "touching a knob"
)

#: The best setting the reference offers.
_BEST_SETTING_METHOD = (
    "the best setting the reference offers: every algorithm it has and both "
    "band edges of the case were tried, and the setting that removes the "
    "most artefact inside the budget of our own rules was kept"
)

#: How the state of the output was measured.
_OUTPUT_METHOD = (
    "the level of the whole output against the level of the input, and the "
    "share of the samples of the output that are digital silence"
)

#: How much the gain curve of the reference breathes.
_GAIN_METHOD = (
    "the temporal variance of the applied gain inside the artefact band"
)

#: What a floor of the measurement is.
_FLOOR_METHOD = (
    "the band of the output is exactly zero, so the decibels are the floor of "
    "the measurement and not a figure of removal: the figure to read it with "
    "is the level of the whole output and the share of it that is silence"
)

#: How the bypass of a reference was measured.
_BYPASS_METHOD = (
    "the reference rendered through the study's renderer, which moves no "
    "parameter of its own, with "
    "the bypass switch on, and the render compared with the input: the "
    "largest difference of the samples, and the delay the render carries, "
    "measured on the clean music clip at 48 kHz"
)

#: How the default setting of a reference was measured.
_DEFAULTS_METHOD = (
    "the reference rendered through the study's renderer, which moves no "
    "parameter of its own, with "
    "the setting it comes with, and the render compared with the input "
    "delayed by the latency the reference declares: the largest difference "
    "of the samples, the gain the render applies to a steady tone at four "
    "levels, and the level the path puts in front of the programme"
)


# ---------------------------------------------------------------------------
# the numbers
# ---------------------------------------------------------------------------
# What the reference removes, keeps and leaves alone.
REFERENCE_VOICE_HISS_REMOVED_DB = 96.3
REFERENCE_VOICE_HISS_KEPT_DB = 0.0
REFERENCE_UNTOUCHED_LEVEL_DB = 0.04

# Its best mode and its factory default, measured on the corpus of ours.
REFERENCE_MODE_BEST_REMOVED_DB = 24.64
REFERENCE_MODE_BEST_KEPT_DB = 0.22
REFERENCE_MODE_DEFAULT_REMOVED_DB = 27.32
REFERENCE_MODE_DEFAULT_KEPT_DB = 3.03
REFERENCE_MODE_REVERB_REMOVED_DB = 53.90
REFERENCE_MODE_REVERB_KEPT_DB = 1.19

# What a block of 512 frames costs the reference, as the study measured it.
REFERENCE_MODE_COST_MS_PER_512_FRAMES = 23.0
REFERENCE_MODE_COST_MS_PER_512_FRAMES_MIN = 5.5

#: The yardstick of the project: the cost of one 1024 point transform on the
#: reference machine, in microseconds.  It is the unit every cost of the
#: benchmark is written in, on both sides of the table, so that a number
#: measured on one machine is not read against a number measured on another.
REFERENCE_TRANSFORM_US = 8.300

#: The block the cost is expressed in (samples).
COST_BLOCK = 64

#: The frames of the block the study measured.
COST_FRAMES = 512

#: The same cost in the yardstick of the project: the cost of one 64 sample
#: block, in 1024 point transforms of 8.300 us.  The study measured 23.0 ms
#: per 512 frames, that is 2875.0 us per 64 sample block, that is 346.3855
#: transforms; and 5.5 ms per 512 frames, that is 687.5 us, that is 82.8313
#: transforms.  The two are derived, so they cannot drift from the numbers
#: they came from.
REFERENCE_MODE_COST_TRANSFORMS_PER_BLOCK = (
    REFERENCE_MODE_COST_MS_PER_512_FRAMES * 1000.0 * COST_BLOCK / COST_FRAMES
    / REFERENCE_TRANSFORM_US
)
REFERENCE_MODE_COST_TRANSFORMS_PER_BLOCK_MIN = (
    REFERENCE_MODE_COST_MS_PER_512_FRAMES_MIN * 1000.0 * COST_BLOCK / COST_FRAMES
    / REFERENCE_TRANSFORM_US
)

# How fast it decides, and how much its gain breathes.
REFERENCE_DECISION_MS = 28.7
#: The decision of our own voice module, with the input and the output paired the
#: right way (the output of the module at t carries the input at t - L).  The
#: instrument of the study pairs the two 2 L samples apart and measured 41.0 ms.
REFERENCE_DECISION_MS_OURS = 1.3
REFERENCE_CHATTER_DB2 = 2.518
#: The chatter of the gain of our own voice module while a voice speaks, measured
#: with the instrument of the study on the tree of the study's day (0.059 dB^2).
REFERENCE_CHATTER_DB2_OURS = 0.021

# The cost of splitting a signal into bands and mixing it back.
REFERENCE_SPLIT_ROUNDTRIP_DB = 0.73
REFERENCE_SPLIT_DELAY_SAMPLES = 3

# The latency it declares, and the tail it does not.
REFERENCE_LATENCY_SAMPLES_48K = 1470
REFERENCE_LATENCY_BLOCK_BASE_SAMPLES = 1214
REFERENCE_LATENCY_MODEL_SAMPLES_48K = 3993
REFERENCE_DECISION_LAG_MS = 12.5
REFERENCE_TAIL_SAMPLES = 0
REFERENCE_TAIL_EXCESS_SAMPLES = 325

# The state it carries.
REFERENCE_STATE_COMPONENT_BYTES = 1042
REFERENCE_STATE_CONTROLLER_BYTES = 0

# Its surface.
REFERENCE_SURFACE_PARAMETERS = 22
REFERENCE_SURFACE_MODES = 7
REFERENCE_SURFACE_MODE_SLOTS = 16
REFERENCE_SURFACE_PROGRAM_LIST = 1

# What it ships as data.
REFERENCE_MODELS_SHIPPED = 7
REFERENCE_MODEL_SIZE_BYTES_MIN = 16175616
REFERENCE_MODEL_SIZE_BYTES_MAX = 45376512

# The laws of its controls.
REFERENCE_FREQUENCY_CURVE_P = 3.334
REFERENCE_CUTOFF_RANGE_LO_HZ = 100.0
REFERENCE_CUTOFF_RANGE_HI_HZ = 10000.0
REFERENCE_GAIN_CURVE_P = 0.264
REFERENCE_GAIN_RANGE_LO_DB = -60.0
REFERENCE_GAIN_RANGE_HI_DB = 12.0
REFERENCE_GAIN_DB_PER_PERCENT_AT_UNITY = 0.316

# Its synthesis.
REFERENCE_PHASE_CORRELATION = 0.0124
REFERENCE_PHASE_CORRELATION_OURS = 0.9991

# ---------------------------------------------------------------------------
# the bypass and the defaults
# ---------------------------------------------------------------------------
# The bypass of the reference is sample exact: every sample of a clean signal
# comes back unchanged, measured as exactly zero with the float32 instrument
# of the study and as at most one step of a 24 bit word with the 24 bit
# harness, which is the rounding of the harness itself.  The bypass is not
# time exact: it delays the signal by the latency the reference declares, and
# the delay moves with the size of the block the host hands over.
REFERENCE_BYPASS_BIT_EXACT = True
REFERENCE_BYPASS_MAX_DIFF = 0.0
REFERENCE_BYPASS_DELAY_SAMPLES_256 = 1470
REFERENCE_BYPASS_DELAY_SAMPLES_512 = 1726
REFERENCE_BYPASS_DELAY_SAMPLES_1024 = 2238
REFERENCE_BYPASS_DELAY_MS_AT_48K = 35.96

# The default setting of the reference is almost transparent and not
# transparent: every sample of a clean signal comes out changed, by at most
# 0.000321984, which is 2701 steps of a 24 bit word and 63.84 dB below the
# peak of the input.  The gain it applies is a static -0.0055 dB that does
# not depend on the level of the input, and the default path puts a floor of
# its own in front of the programme at 103.10 dBFS.
REFERENCE_DEFAULT_MAX_DIFF = 0.000321984
REFERENCE_DEFAULT_MAX_DIFF_DB = -63.84
REFERENCE_DEFAULT_MAX_DIFF_24BIT_STEPS = 2701
REFERENCE_DEFAULT_GAIN_DB = -0.0055
REFERENCE_DEFAULT_GAIN_SPREAD_DB = 0.0073
REFERENCE_DEFAULT_GAIN_SD_DB = 0.0012
REFERENCE_DEFAULT_PRE_SIGNAL_NOISE_DBFS = -103.10

# Ours, measured with the same instrument on the same material: the bypass
# hands the samples back bit for bit and adds no delay, and the default
# setting leaves a clean signal exactly alone.
OWN_BYPASS_BIT_EXACT = True
OWN_BYPASS_DELAY_SAMPLES = 0
OWN_DEFAULT_BIT_EXACT_ON_CLEAN = True

# ---------------------------------------------------------------------------
# the corpus head-to-head: what the reference does to the corpus
# ---------------------------------------------------------------------------
#
# The setting the reference comes with.  It removes 14.08 to 14.13 dB of the
# noise beds and moves the wanted content of those beds by 0.00 dB, which is a
# tie with ours.  On the hum case and on the rumble case it moves the wanted
# partial by 13.31 dB and 16.18 dB, where we move it by 0.04 dB and 0.03 dB:
# its first band edge defaults to 1000 Hz and the wanted partial of those two
# cases sits at 1000 Hz, so the partial is split between two bands.
#
REFERENCE_HISS_REMOVED_DB_DEFAULT = 14.11
REFERENCE_PINK_HISS_REMOVED_DB_DEFAULT = 14.09
REFERENCE_HUM50_REMOVED_DB_DEFAULT = 14.08
REFERENCE_RUMBLE_REMOVED_DB_DEFAULT = 14.13
REFERENCE_VOICE_HISS_REMOVED_DB_DEFAULT = 14.12
REFERENCE_PLOSIVE_REMOVED_DB_DEFAULT = 13.71

REFERENCE_HISS_KEPT_DB_DEFAULT = 0.00
REFERENCE_PINK_HISS_KEPT_DB_DEFAULT = 0.00
REFERENCE_HUM50_KEPT_DB_DEFAULT = 13.31
REFERENCE_RUMBLE_KEPT_DB_DEFAULT = 16.18
REFERENCE_VOICE_HISS_KEPT_DB_DEFAULT = 5.79
REFERENCE_PLOSIVE_KEPT_DB_DEFAULT = 14.69

# The best setting the reference offers.
#
# CAUTION: three of the six removal figures below are marked FLOOR, and two
# of the six "kept" figures are marked FLOOR as well.  A figure that is a
# floor is not a figure of removal and not a figure of movement: the band of
# the output is exactly zero, so the instrument has nothing left to measure
# and prints the floor of its own range.  The figures to read them with are
# the level of the whole output and the share of it that is digital silence,
# and they are given below: on the noise beds the reference is 38 to 44 dB
# below its input and 81 to 86 per cent of its output is silence.  It has not
# removed the bed, it has stopped emitting anything where there was nothing
# to emit.
#
REFERENCE_HISS_REMOVED_DB_BEST_FLOOR = 269.29  # FLOOR: the band is exactly zero
REFERENCE_PINK_HISS_REMOVED_DB_BEST = 46.30
REFERENCE_HUM50_REMOVED_DB_BEST = 80.15
REFERENCE_RUMBLE_REMOVED_DB_BEST_FLOOR = 302.38  # FLOOR: the band is exactly zero
REFERENCE_VOICE_HISS_REMOVED_DB_BEST_FLOOR = 296.47  # FLOOR: the band is zero
REFERENCE_PLOSIVE_REMOVED_DB_BEST = 41.29

REFERENCE_HISS_KEPT_DB_BEST = 0.00
REFERENCE_PINK_HISS_KEPT_DB_BEST = 0.00
REFERENCE_HUM50_KEPT_DB_BEST_FLOOR = 232.37  # FLOOR: the partial is gone
REFERENCE_RUMBLE_KEPT_DB_BEST_FLOOR = 232.45  # FLOOR: the programme is gone
REFERENCE_VOICE_HISS_KEPT_DB_BEST = 1.28
REFERENCE_PLOSIVE_KEPT_DB_BEST = 0.17

# What the output of the reference is: the level of the whole output against
# the level of the input, and the share of the samples of the output that are
# digital silence.  These two are the figures every removal figure above has
# to be read with.
#
REFERENCE_OUTPUT_LEVEL_CHANGE_DB_HISS_DEFAULT = -14.29
REFERENCE_OUTPUT_LEVEL_CHANGE_DB_PINK_HISS_DEFAULT = -14.29
REFERENCE_OUTPUT_LEVEL_CHANGE_DB_HUM50_DEFAULT = -11.91
REFERENCE_OUTPUT_LEVEL_CHANGE_DB_RUMBLE_DEFAULT = -11.80
REFERENCE_OUTPUT_LEVEL_CHANGE_DB_VOICE_HISS_DEFAULT = -3.07
REFERENCE_OUTPUT_LEVEL_CHANGE_DB_PLOSIVE_DEFAULT = 1.36

REFERENCE_OUTPUT_LEVEL_CHANGE_DB_HISS_BEST = -38.36
REFERENCE_OUTPUT_LEVEL_CHANGE_DB_PINK_HISS_BEST = -43.74
REFERENCE_OUTPUT_LEVEL_CHANGE_DB_HUM50_BEST = -62.00
REFERENCE_OUTPUT_LEVEL_CHANGE_DB_RUMBLE_BEST = -28.12
REFERENCE_OUTPUT_LEVEL_CHANGE_DB_VOICE_HISS_BEST = -0.41
REFERENCE_OUTPUT_LEVEL_CHANGE_DB_PLOSIVE_BEST = -1.09

REFERENCE_OUTPUT_SILENT_SHARE_HISS_BEST = 0.812
REFERENCE_OUTPUT_SILENT_SHARE_PINK_HISS_BEST = 0.862
REFERENCE_OUTPUT_SILENT_SHARE_HUM50_BEST = 0.856
REFERENCE_OUTPUT_SILENT_SHARE_RUMBLE_BEST = 0.0
REFERENCE_OUTPUT_SILENT_SHARE_VOICE_HISS_BEST = 0.0
REFERENCE_OUTPUT_SILENT_SHARE_PLOSIVE_BEST = 0.0

# At the setting the reference comes with, every sample of every case of the
# table is not silent: the silence belongs to the best setting alone.
REFERENCE_OUTPUT_SILENT_SHARE_DEFAULT = 0.0

# How much the gain curve of the reference breathes.
REFERENCE_GAIN_VAR_DB2_HISS_DEFAULT = 0.042
REFERENCE_GAIN_VAR_DB2_PINK_HISS_DEFAULT = 0.458
REFERENCE_GAIN_VAR_DB2_HUM50_DEFAULT = 0.003
REFERENCE_GAIN_VAR_DB2_RUMBLE_DEFAULT = 7.476
REFERENCE_GAIN_VAR_DB2_VOICE_HISS_DEFAULT = 0.041
REFERENCE_GAIN_VAR_DB2_PLOSIVE_DEFAULT = 60.187

REFERENCE_GAIN_VAR_DB2_HISS_BEST = 0.019
REFERENCE_GAIN_VAR_DB2_PINK_HISS_BEST = 0.227
REFERENCE_GAIN_VAR_DB2_VOICE_HISS_BEST = 0.025
REFERENCE_GAIN_VAR_DB2_PLOSIVE_BEST = 8.152

# The reference is not bit-deterministic: the same setting on the same input
# gives another waveform, and the figures move by up to this much between two
# runs.  Ours is bit identical between two runs.
REFERENCE_REPRODUCIBLE = False
REFERENCE_REPRODUCIBILITY_SPREAD_DB = 0.8
REFERENCE_REPRODUCIBLE_OURS = True

# The first band edge of the reference, as it comes.  It is the reason the
# hum case and the rumble case carry a "kept" figure of 13 to 16 dB: the
# wanted partial of those two cases sits at 1000 Hz, exactly on the edge.
REFERENCE_FIRST_CROSSOVER_DEFAULT_HZ = 1000.0

# The latency the reference declares belongs to the model that is loaded, not
# to the plug-in: it is 3880 samples at some settings, 3993 at others and
# 4641 at one of them.
REFERENCE_LATENCY_SAMPLES_MIN = 3880
REFERENCE_LATENCY_SAMPLES_MAX = 4641

# Our own side of the same table, measured with the same instrument on the
# tree of the study's day.
REFERENCE_HISS_REMOVED_DB_OURS = 13.54
REFERENCE_PINK_HISS_REMOVED_DB_OURS = 17.30
REFERENCE_HUM50_REMOVED_DB_OURS = 38.03
REFERENCE_RUMBLE_REMOVED_DB_OURS = 11.44
REFERENCE_VOICE_HISS_REMOVED_DB_OURS = 17.70

REFERENCE_HISS_KEPT_DB_OURS = 0.00
REFERENCE_PINK_HISS_KEPT_DB_OURS = 0.00
REFERENCE_HUM50_KEPT_DB_OURS = 0.04
REFERENCE_RUMBLE_KEPT_DB_OURS = 0.03
REFERENCE_VOICE_HISS_KEPT_DB_OURS = 0.02


#: Every number of the restoration study, with the case, the unit and the way
#: it was measured.
RESTORATIONS: tuple[Restoration, ...] = (
    # -- what it removes, keeps and leaves alone ----------------------------
    Restoration(
        name="REFERENCE_VOICE_HISS_REMOVED_DB",
        series="stem_separation",
        case="voice_hiss",
        metric="removed_db",
        value=REFERENCE_VOICE_HISS_REMOVED_DB,
        unit="dB",
        method="%s; the level of the partial the reference does not want, "
        "before and after" % _CORPUS_METHOD,
    ),
    Restoration(
        name="REFERENCE_VOICE_HISS_KEPT_DB",
        series="stem_separation",
        case="voice_hiss",
        metric="kept_db",
        value=REFERENCE_VOICE_HISS_KEPT_DB,
        unit="dB",
        method="%s; the level of the partial the reference does want, before "
        "and after" % _CORPUS_METHOD,
    ),
    Restoration(
        name="REFERENCE_UNTOUCHED_LEVEL_DB",
        series="band_restoration",
        case="voice_hiss",
        metric="untouched_level_db",
        value=REFERENCE_UNTOUCHED_LEVEL_DB,
        unit="dB",
        method="%s; the level of the part of the spectrum the reference shapes "
        "nothing in" % _CORPUS_METHOD,
    ),
    # -- its best mode and its factory default ------------------------------
    Restoration(
        name="REFERENCE_MODE_BEST_REMOVED_DB",
        series="band_restoration",
        case="voice_hiss",
        metric="removed_db",
        value=REFERENCE_MODE_BEST_REMOVED_DB,
        unit="dB",
        method="%s; %s" % (_CORPUS_METHOD, _VOICE_METHOD),
        setting="the setting the study's rule picks",
    ),
    Restoration(
        name="REFERENCE_MODE_BEST_KEPT_DB",
        series="band_restoration",
        case="voice_hiss",
        metric="kept_db",
        value=REFERENCE_MODE_BEST_KEPT_DB,
        unit="dB",
        method="%s; %s" % (_CORPUS_METHOD, _VOICE_METHOD),
        setting="the setting the study's rule picks",
    ),
    Restoration(
        name="REFERENCE_MODE_DEFAULT_REMOVED_DB",
        series="band_restoration",
        case="voice_hiss",
        metric="removed_db",
        value=REFERENCE_MODE_DEFAULT_REMOVED_DB,
        unit="dB",
        method="%s; %s" % (_CORPUS_METHOD, _VOICE_METHOD),
        setting="the setting the reference comes with",
    ),
    Restoration(
        name="REFERENCE_MODE_DEFAULT_KEPT_DB",
        series="band_restoration",
        case="voice_hiss",
        metric="kept_db",
        value=REFERENCE_MODE_DEFAULT_KEPT_DB,
        unit="dB",
        method="%s; %s" % (_CORPUS_METHOD, _VOICE_METHOD),
        setting="the setting the reference comes with",
    ),
    Restoration(
        name="REFERENCE_MODE_REVERB_REMOVED_DB",
        series="band_restoration",
        case="reverb",
        metric="removed_db",
        value=REFERENCE_MODE_REVERB_REMOVED_DB,
        unit="dB",
        method="%s; the reverberant case of the study" % _CORPUS_METHOD,
    ),
    Restoration(
        name="REFERENCE_MODE_REVERB_KEPT_DB",
        series="band_restoration",
        case="reverb",
        metric="kept_db",
        value=REFERENCE_MODE_REVERB_KEPT_DB,
        unit="dB",
        method="%s; the reverberant case of the study" % _CORPUS_METHOD,
    ),
    # -- what a block costs it ----------------------------------------------
    # The raw measurement of the study, in the unit the study measured it:
    # milliseconds per 512 frame block, on the study machine.  The method
    # carries the conversion into the yardstick of the project, which is the
    # unit the cost row of the benchmark compares on.
    Restoration(
        name="REFERENCE_MODE_COST_MS_PER_512_FRAMES",
        series="band_restoration",
        case=EVERY_CASE,
        metric="cost_ms",
        value=REFERENCE_MODE_COST_MS_PER_512_FRAMES,
        unit="ms",
        method=_COST_METHOD % (
            "23.0",
            "2875.0",
            "%.4f" % REFERENCE_MODE_COST_TRANSFORMS_PER_BLOCK,
        ),
        setting="the most expensive mode",
    ),
    Restoration(
        name="REFERENCE_MODE_COST_MS_PER_512_FRAMES_MIN",
        series="band_restoration",
        case=EVERY_CASE,
        metric="cost_ms",
        value=REFERENCE_MODE_COST_MS_PER_512_FRAMES_MIN,
        unit="ms",
        method=_COST_METHOD % (
            "5.5",
            "687.5",
            "%.4f" % REFERENCE_MODE_COST_TRANSFORMS_PER_BLOCK_MIN,
        ),
        setting="the cheapest mode",
    ),
    # -- how fast it decides, and how much its gain breathes -----------------
    Restoration(
        name="REFERENCE_DECISION_MS",
        series="stem_separation",
        case=EVERY_CASE,
        metric="decision_ms",
        value=REFERENCE_DECISION_MS,
        unit="ms",
        method="%s; the time to 63 per cent of the step, in the 100 to 250 Hz "
        "band" % _VOICE_METHOD,
    ),
    Restoration(
        name="REFERENCE_DECISION_MS_OURS",
        series="ours",
        case=EVERY_CASE,
        metric="decision_ms",
        value=REFERENCE_DECISION_MS_OURS,
        unit="ms",
        method=_OURS_METHOD,
        setting="our voice module",
    ),
    Restoration(
        name="REFERENCE_CHATTER_DB2",
        series="stem_separation",
        case=EVERY_CASE,
        metric="chatter_db2",
        value=REFERENCE_CHATTER_DB2,
        unit="dB^2",
        method="%s; the variance of the applied gain while a voice speaks"
        % _VOICE_METHOD,
    ),
    Restoration(
        name="REFERENCE_CHATTER_DB2_OURS",
        series="ours",
        case=EVERY_CASE,
        metric="chatter_db2",
        value=REFERENCE_CHATTER_DB2_OURS,
        unit="dB^2",
        method=_OURS_METHOD,
        setting="our voice module",
    ),
    # -- the cost of splitting a signal into bands and mixing it back -------
    Restoration(
        name="REFERENCE_SPLIT_ROUNDTRIP_DB",
        series="band_restoration",
        case=EVERY_CASE,
        metric="split_roundtrip_db",
        value=REFERENCE_SPLIT_ROUNDTRIP_DB,
        unit="dB",
        method="the band split of the reference, measured on the live plug-in "
        "through the study's renderer: "
        "the rms of the signal against the rms of the signal that came back",
    ),
    Restoration(
        name="REFERENCE_SPLIT_DELAY_SAMPLES",
        series="band_restoration",
        case=EVERY_CASE,
        metric="split_delay_samples",
        value=REFERENCE_SPLIT_DELAY_SAMPLES,
        unit="samples",
        method="the band split of the reference, measured on the live plug-in "
        "through the study's renderer: "
        "the delay of the signal that came back",
    ),
    # -- the latency it declares, and the tail it does not -------------------
    Restoration(
        name="REFERENCE_LATENCY_SAMPLES_48K",
        series="stem_separation",
        case=EVERY_CASE,
        metric="latency_samples",
        value=REFERENCE_LATENCY_SAMPLES_48K,
        unit="samples",
        method=_LATENCY_METHOD,
        setting="48 kHz, a block of 256 samples",
    ),
    Restoration(
        name="REFERENCE_LATENCY_BLOCK_BASE_SAMPLES",
        series="stem_separation",
        case=EVERY_CASE,
        metric="latency_samples",
        value=REFERENCE_LATENCY_BLOCK_BASE_SAMPLES,
        unit="samples",
        method=_LATENCY_METHOD,
        setting="the base of the rule: the declared latency is this many "
        "samples plus the block size",
    ),
    Restoration(
        name="REFERENCE_LATENCY_MODEL_SAMPLES_48K",
        series="band_restoration",
        case=EVERY_CASE,
        metric="latency_samples",
        value=REFERENCE_LATENCY_MODEL_SAMPLES_48K,
        unit="samples",
        method=_LATENCY_METHOD,
        setting="48 kHz, with its model loaded",
    ),
    Restoration(
        name="REFERENCE_DECISION_LAG_MS",
        series="band_restoration",
        case=EVERY_CASE,
        metric="decision_lag_ms",
        value=REFERENCE_DECISION_LAG_MS,
        unit="ms",
        method="%s; the part of the opening that is not the latency of the "
        "plug-in" % _VOICE_METHOD,
    ),
    Restoration(
        name="REFERENCE_TAIL_SAMPLES",
        series="stem_separation",
        case=EVERY_CASE,
        metric="tail_samples",
        value=REFERENCE_TAIL_SAMPLES,
        unit="samples",
        method="the tail the reference declares to the host, in every probe, "
        "read out of the study's surface host",
    ),
    Restoration(
        name="REFERENCE_TAIL_EXCESS_SAMPLES",
        series="stem_separation",
        case=EVERY_CASE,
        metric="tail_samples",
        value=REFERENCE_TAIL_EXCESS_SAMPLES,
        unit="samples",
        method="the tail the reference really has, measured on the render of "
        "the study's renderer: how "
        "far its own sound runs past the latency it declares, in the worst of "
        "the fifteen probes",
    ),
    # -- the state it carries ------------------------------------------------
    Restoration(
        name="REFERENCE_STATE_COMPONENT_BYTES",
        series="band_restoration",
        case=EVERY_CASE,
        metric="state_bytes",
        value=REFERENCE_STATE_COMPONENT_BYTES,
        unit="bytes",
        method="the size of the state the component of the reference hands to "
        "the host, read out of the study's surface host",
    ),
    Restoration(
        name="REFERENCE_STATE_CONTROLLER_BYTES",
        series="band_restoration",
        case=EVERY_CASE,
        metric="state_bytes",
        value=REFERENCE_STATE_CONTROLLER_BYTES,
        unit="bytes",
        method="the size of the state the controller of the reference hands to "
        "the host, read out of the study's surface host",
    ),
    # -- its surface ---------------------------------------------------------
    Restoration(
        name="REFERENCE_SURFACE_PARAMETERS",
        series="band_restoration",
        case=EVERY_CASE,
        metric="surface_parameters",
        value=REFERENCE_SURFACE_PARAMETERS,
        unit="parameters",
        method=_SURFACE_METHOD,
    ),
    Restoration(
        name="REFERENCE_SURFACE_MODES",
        series="band_restoration",
        case=EVERY_CASE,
        metric="surface_modes",
        value=REFERENCE_SURFACE_MODES,
        unit="modes",
        method=_SURFACE_METHOD,
    ),
    Restoration(
        name="REFERENCE_SURFACE_MODE_SLOTS",
        series="band_restoration",
        case=EVERY_CASE,
        metric="surface_mode_slots",
        value=REFERENCE_SURFACE_MODE_SLOTS,
        unit="slots",
        method=_SURFACE_METHOD,
    ),
    Restoration(
        name="REFERENCE_SURFACE_PROGRAM_LIST",
        series="band_restoration",
        case=EVERY_CASE,
        metric="surface_program_list",
        value=REFERENCE_SURFACE_PROGRAM_LIST,
        unit="entries",
        method=_SURFACE_METHOD,
    ),
    # -- what it ships as data -----------------------------------------------
    Restoration(
        name="REFERENCE_MODELS_SHIPPED",
        series="band_restoration",
        case=EVERY_CASE,
        metric="models_shipped",
        value=REFERENCE_MODELS_SHIPPED,
        unit="models",
        method="the model files that sit beside the plug-in, counted by our own "
        "reader in the "
        "bundle of the study",
    ),
    Restoration(
        name="REFERENCE_MODEL_SIZE_BYTES_MIN",
        series="band_restoration",
        case=EVERY_CASE,
        metric="model_size_bytes",
        value=REFERENCE_MODEL_SIZE_BYTES_MIN,
        unit="bytes",
        method="the size of the smallest model file of the reference, counted by "
        "our own reader on disk",
    ),
    Restoration(
        name="REFERENCE_MODEL_SIZE_BYTES_MAX",
        series="band_restoration",
        case=EVERY_CASE,
        metric="model_size_bytes",
        value=REFERENCE_MODEL_SIZE_BYTES_MAX,
        unit="bytes",
        method="the size of the largest model file of the reference, counted by "
        "our own reader on disk",
    ),
    # -- the laws of its controls --------------------------------------------
    Restoration(
        name="REFERENCE_FREQUENCY_CURVE_P",
        series="band_restoration",
        case=EVERY_CASE,
        metric="curve_exponent",
        value=REFERENCE_FREQUENCY_CURVE_P,
        unit="exponent",
        method="%s; the fit of the law of a frequency control, scored by the "
        "largest error it makes over the travel" % _SURFACE_METHOD,
    ),
    Restoration(
        name="REFERENCE_CUTOFF_RANGE_LO_HZ",
        series="band_restoration",
        case=EVERY_CASE,
        metric="range_low_hz",
        value=REFERENCE_CUTOFF_RANGE_LO_HZ,
        unit="Hz",
        method=_SURFACE_METHOD,
    ),
    Restoration(
        name="REFERENCE_CUTOFF_RANGE_HI_HZ",
        series="band_restoration",
        case=EVERY_CASE,
        metric="range_high_hz",
        value=REFERENCE_CUTOFF_RANGE_HI_HZ,
        unit="Hz",
        method=_SURFACE_METHOD,
    ),
    Restoration(
        name="REFERENCE_GAIN_CURVE_P",
        series="band_restoration",
        case=EVERY_CASE,
        metric="curve_exponent",
        value=REFERENCE_GAIN_CURVE_P,
        unit="exponent",
        method="%s; the fit of the law of a gain control, scored by the "
        "largest error it makes over the travel" % _SURFACE_METHOD,
    ),
    Restoration(
        name="REFERENCE_GAIN_RANGE_LO_DB",
        series="band_restoration",
        case=EVERY_CASE,
        metric="range_low_db",
        value=REFERENCE_GAIN_RANGE_LO_DB,
        unit="dB",
        method=_SURFACE_METHOD,
    ),
    Restoration(
        name="REFERENCE_GAIN_RANGE_HI_DB",
        series="band_restoration",
        case=EVERY_CASE,
        metric="range_high_db",
        value=REFERENCE_GAIN_RANGE_HI_DB,
        unit="dB",
        method=_SURFACE_METHOD,
    ),
    Restoration(
        name="REFERENCE_GAIN_DB_PER_PERCENT_AT_UNITY",
        series="band_restoration",
        case=EVERY_CASE,
        metric="db_per_percent_at_unity",
        value=REFERENCE_GAIN_DB_PER_PERCENT_AT_UNITY,
        unit="dB per 1 %",
        method="%s; the slope of the law of the gain control at unity" % _SURFACE_METHOD,
    ),
    # -- its synthesis --------------------------------------------------------
    Restoration(
        name="REFERENCE_PHASE_CORRELATION",
        series="band_restoration",
        case=EVERY_CASE,
        metric="phase_correlation",
        value=REFERENCE_PHASE_CORRELATION,
        unit="correlation",
        method="the correlation of the input with the output of the reference, "
        "on the render of the study's renderer, "
        "for two tones a quarter of a period apart",
    ),
    Restoration(
        name="REFERENCE_PHASE_CORRELATION_OURS",
        series="ours",
        case=EVERY_CASE,
        metric="phase_correlation",
        value=REFERENCE_PHASE_CORRELATION_OURS,
        unit="correlation",
        method=_OURS_METHOD,
        setting="our resynthesis path",
    ),
    # -- the corpus head-to-head, the setting the reference comes with ------
    Restoration(
        name="REFERENCE_HISS_REMOVED_DB_DEFAULT",
        series="band_restoration",
        case="hiss",
        metric="removed_db",
        value=REFERENCE_HISS_REMOVED_DB_DEFAULT,
        unit="dB",
        method="%s; %s" % (_SIGNAL_METHOD, _DEFAULT_SETTING_METHOD),
        setting="the setting the reference comes with",
    ),
    Restoration(
        name="REFERENCE_PINK_HISS_REMOVED_DB_DEFAULT",
        series="band_restoration",
        case="pink_hiss",
        metric="removed_db",
        value=REFERENCE_PINK_HISS_REMOVED_DB_DEFAULT,
        unit="dB",
        method="%s; %s" % (_SIGNAL_METHOD, _DEFAULT_SETTING_METHOD),
        setting="the setting the reference comes with",
    ),
    Restoration(
        name="REFERENCE_HUM50_REMOVED_DB_DEFAULT",
        series="band_restoration",
        case="hum50",
        metric="removed_db",
        value=REFERENCE_HUM50_REMOVED_DB_DEFAULT,
        unit="dB",
        method="%s; %s" % (_SIGNAL_METHOD, _DEFAULT_SETTING_METHOD),
        setting="the setting the reference comes with",
    ),
    Restoration(
        name="REFERENCE_RUMBLE_REMOVED_DB_DEFAULT",
        series="band_restoration",
        case="rumble",
        metric="removed_db",
        value=REFERENCE_RUMBLE_REMOVED_DB_DEFAULT,
        unit="dB",
        method="%s; %s" % (_SIGNAL_METHOD, _DEFAULT_SETTING_METHOD),
        setting="the setting the reference comes with",
    ),
    Restoration(
        name="REFERENCE_VOICE_HISS_REMOVED_DB_DEFAULT",
        series="band_restoration",
        case="voice_hiss",
        metric="removed_db",
        value=REFERENCE_VOICE_HISS_REMOVED_DB_DEFAULT,
        unit="dB",
        method="%s; %s" % (_SIGNAL_METHOD, _DEFAULT_SETTING_METHOD),
        setting="the setting the reference comes with",
    ),
    Restoration(
        name="REFERENCE_PLOSIVE_REMOVED_DB_DEFAULT",
        series="band_restoration",
        case="plosive",
        metric="removed_db",
        value=REFERENCE_PLOSIVE_REMOVED_DB_DEFAULT,
        unit="dB",
        method="%s; %s" % (_SIGNAL_METHOD, _DEFAULT_SETTING_METHOD),
        setting="the setting the reference comes with",
    ),
    Restoration(
        name="REFERENCE_HISS_KEPT_DB_DEFAULT",
        series="band_restoration",
        case="hiss",
        metric="kept_db",
        value=REFERENCE_HISS_KEPT_DB_DEFAULT,
        unit="dB",
        method="%s; %s; the case carries no wanted content" % (
            _SIGNAL_METHOD, _DEFAULT_SETTING_METHOD
        ),
        setting="the setting the reference comes with",
    ),
    Restoration(
        name="REFERENCE_PINK_HISS_KEPT_DB_DEFAULT",
        series="band_restoration",
        case="pink_hiss",
        metric="kept_db",
        value=REFERENCE_PINK_HISS_KEPT_DB_DEFAULT,
        unit="dB",
        method="%s; %s; the case carries no wanted content" % (
            _SIGNAL_METHOD, _DEFAULT_SETTING_METHOD
        ),
        setting="the setting the reference comes with",
    ),
    Restoration(
        name="REFERENCE_HUM50_KEPT_DB_DEFAULT",
        series="band_restoration",
        case="hum50",
        metric="kept_db",
        value=REFERENCE_HUM50_KEPT_DB_DEFAULT,
        unit="dB",
        method="%s; %s; the wanted partial of the case sits at 1000 Hz, which "
        "is where the first band edge of the reference comes" % (
            _SIGNAL_METHOD, _DEFAULT_SETTING_METHOD
        ),
        setting="the setting the reference comes with",
    ),
    Restoration(
        name="REFERENCE_RUMBLE_KEPT_DB_DEFAULT",
        series="band_restoration",
        case="rumble",
        metric="kept_db",
        value=REFERENCE_RUMBLE_KEPT_DB_DEFAULT,
        unit="dB",
        method="%s; %s; the wanted partial of the case sits at 1000 Hz, which "
        "is where the first band edge of the reference comes" % (
            _SIGNAL_METHOD, _DEFAULT_SETTING_METHOD
        ),
        setting="the setting the reference comes with",
    ),
    Restoration(
        name="REFERENCE_VOICE_HISS_KEPT_DB_DEFAULT",
        series="band_restoration",
        case="voice_hiss",
        metric="kept_db",
        value=REFERENCE_VOICE_HISS_KEPT_DB_DEFAULT,
        unit="dB",
        method="%s; %s" % (_SIGNAL_METHOD, _DEFAULT_SETTING_METHOD),
        setting="the setting the reference comes with",
    ),
    Restoration(
        name="REFERENCE_PLOSIVE_KEPT_DB_DEFAULT",
        series="band_restoration",
        case="plosive",
        metric="kept_db",
        value=REFERENCE_PLOSIVE_KEPT_DB_DEFAULT,
        unit="dB",
        method="%s; %s" % (_SIGNAL_METHOD, _DEFAULT_SETTING_METHOD),
        setting="the setting the reference comes with",
    ),
    # -- the corpus head-to-head, the best setting the reference offers -----
    # The three removal figures marked FLOOR are floors of the measurement:
    # the band of the output is exactly zero there, so the decibels are what
    # the instrument prints when it has nothing left to measure.
    Restoration(
        name="REFERENCE_HISS_REMOVED_DB_BEST_FLOOR",
        series="band_restoration",
        case="hiss",
        metric="removed_db",
        value=REFERENCE_HISS_REMOVED_DB_BEST_FLOOR,
        unit="dB",
        method="%s; %s; %s" % (
            _SIGNAL_METHOD, _BEST_SETTING_METHOD, _FLOOR_METHOD
        ),
        setting="the best setting the reference offers",
    ),
    Restoration(
        name="REFERENCE_PINK_HISS_REMOVED_DB_BEST",
        series="band_restoration",
        case="pink_hiss",
        metric="removed_db",
        value=REFERENCE_PINK_HISS_REMOVED_DB_BEST,
        unit="dB",
        method="%s; %s" % (_SIGNAL_METHOD, _BEST_SETTING_METHOD),
        setting="the best setting the reference offers",
    ),
    Restoration(
        name="REFERENCE_HUM50_REMOVED_DB_BEST",
        series="band_restoration",
        case="hum50",
        metric="removed_db",
        value=REFERENCE_HUM50_REMOVED_DB_BEST,
        unit="dB",
        method="%s; %s" % (_SIGNAL_METHOD, _BEST_SETTING_METHOD),
        setting="the best setting the reference offers",
    ),
    Restoration(
        name="REFERENCE_RUMBLE_REMOVED_DB_BEST_FLOOR",
        series="band_restoration",
        case="rumble",
        metric="removed_db",
        value=REFERENCE_RUMBLE_REMOVED_DB_BEST_FLOOR,
        unit="dB",
        method="%s; %s; %s" % (
            _SIGNAL_METHOD, _BEST_SETTING_METHOD, _FLOOR_METHOD
        ),
        setting="the best setting the reference offers",
    ),
    Restoration(
        name="REFERENCE_VOICE_HISS_REMOVED_DB_BEST_FLOOR",
        series="band_restoration",
        case="voice_hiss",
        metric="removed_db",
        value=REFERENCE_VOICE_HISS_REMOVED_DB_BEST_FLOOR,
        unit="dB",
        method="%s; %s; %s" % (
            _SIGNAL_METHOD, _BEST_SETTING_METHOD, _FLOOR_METHOD
        ),
        setting="the best setting the reference offers",
    ),
    Restoration(
        name="REFERENCE_PLOSIVE_REMOVED_DB_BEST",
        series="band_restoration",
        case="plosive",
        metric="removed_db",
        value=REFERENCE_PLOSIVE_REMOVED_DB_BEST,
        unit="dB",
        method="%s; %s" % (_SIGNAL_METHOD, _BEST_SETTING_METHOD),
        setting="the best setting the reference offers",
    ),
    Restoration(
        name="REFERENCE_HISS_KEPT_DB_BEST",
        series="band_restoration",
        case="hiss",
        metric="kept_db",
        value=REFERENCE_HISS_KEPT_DB_BEST,
        unit="dB",
        method="%s; %s; the case carries no wanted content" % (
            _SIGNAL_METHOD, _BEST_SETTING_METHOD
        ),
        setting="the best setting the reference offers",
    ),
    Restoration(
        name="REFERENCE_PINK_HISS_KEPT_DB_BEST",
        series="band_restoration",
        case="pink_hiss",
        metric="kept_db",
        value=REFERENCE_PINK_HISS_KEPT_DB_BEST,
        unit="dB",
        method="%s; %s; the case carries no wanted content" % (
            _SIGNAL_METHOD, _BEST_SETTING_METHOD
        ),
        setting="the best setting the reference offers",
    ),
    Restoration(
        name="REFERENCE_HUM50_KEPT_DB_BEST_FLOOR",
        series="band_restoration",
        case="hum50",
        metric="kept_db",
        value=REFERENCE_HUM50_KEPT_DB_BEST_FLOOR,
        unit="dB",
        method="%s; %s; %s; the wanted partial of the case is not moved, it "
        "is gone" % (
            _SIGNAL_METHOD, _BEST_SETTING_METHOD, _FLOOR_METHOD
        ),
        setting="the best setting the reference offers",
    ),
    Restoration(
        name="REFERENCE_RUMBLE_KEPT_DB_BEST_FLOOR",
        series="band_restoration",
        case="rumble",
        metric="kept_db",
        value=REFERENCE_RUMBLE_KEPT_DB_BEST_FLOOR,
        unit="dB",
        method="%s; %s; %s; the wanted programme of the case is not moved, it "
        "is gone" % (
            _SIGNAL_METHOD, _BEST_SETTING_METHOD, _FLOOR_METHOD
        ),
        setting="the best setting the reference offers",
    ),
    Restoration(
        name="REFERENCE_VOICE_HISS_KEPT_DB_BEST",
        series="band_restoration",
        case="voice_hiss",
        metric="kept_db",
        value=REFERENCE_VOICE_HISS_KEPT_DB_BEST,
        unit="dB",
        method="%s; %s" % (_SIGNAL_METHOD, _BEST_SETTING_METHOD),
        setting="the best setting the reference offers",
    ),
    Restoration(
        name="REFERENCE_PLOSIVE_KEPT_DB_BEST",
        series="band_restoration",
        case="plosive",
        metric="kept_db",
        value=REFERENCE_PLOSIVE_KEPT_DB_BEST,
        unit="dB",
        method="%s; %s" % (_SIGNAL_METHOD, _BEST_SETTING_METHOD),
        setting="the best setting the reference offers",
    ),
    # -- what the output of the reference is --------------------------------
    Restoration(
        name="REFERENCE_OUTPUT_LEVEL_CHANGE_DB_HISS_DEFAULT",
        series="band_restoration",
        case="hiss",
        metric="output_level_change_db",
        value=REFERENCE_OUTPUT_LEVEL_CHANGE_DB_HISS_DEFAULT,
        unit="dB",
        method="%s; %s" % (_SIGNAL_METHOD, _OUTPUT_METHOD),
        setting="the setting the reference comes with",
    ),
    Restoration(
        name="REFERENCE_OUTPUT_LEVEL_CHANGE_DB_PINK_HISS_DEFAULT",
        series="band_restoration",
        case="pink_hiss",
        metric="output_level_change_db",
        value=REFERENCE_OUTPUT_LEVEL_CHANGE_DB_PINK_HISS_DEFAULT,
        unit="dB",
        method="%s; %s" % (_SIGNAL_METHOD, _OUTPUT_METHOD),
        setting="the setting the reference comes with",
    ),
    Restoration(
        name="REFERENCE_OUTPUT_LEVEL_CHANGE_DB_HUM50_DEFAULT",
        series="band_restoration",
        case="hum50",
        metric="output_level_change_db",
        value=REFERENCE_OUTPUT_LEVEL_CHANGE_DB_HUM50_DEFAULT,
        unit="dB",
        method="%s; %s" % (_SIGNAL_METHOD, _OUTPUT_METHOD),
        setting="the setting the reference comes with",
    ),
    Restoration(
        name="REFERENCE_OUTPUT_LEVEL_CHANGE_DB_RUMBLE_DEFAULT",
        series="band_restoration",
        case="rumble",
        metric="output_level_change_db",
        value=REFERENCE_OUTPUT_LEVEL_CHANGE_DB_RUMBLE_DEFAULT,
        unit="dB",
        method="%s; %s" % (_SIGNAL_METHOD, _OUTPUT_METHOD),
        setting="the setting the reference comes with",
    ),
    Restoration(
        name="REFERENCE_OUTPUT_LEVEL_CHANGE_DB_VOICE_HISS_DEFAULT",
        series="band_restoration",
        case="voice_hiss",
        metric="output_level_change_db",
        value=REFERENCE_OUTPUT_LEVEL_CHANGE_DB_VOICE_HISS_DEFAULT,
        unit="dB",
        method="%s; %s" % (_SIGNAL_METHOD, _OUTPUT_METHOD),
        setting="the setting the reference comes with",
    ),
    Restoration(
        name="REFERENCE_OUTPUT_LEVEL_CHANGE_DB_PLOSIVE_DEFAULT",
        series="band_restoration",
        case="plosive",
        metric="output_level_change_db",
        value=REFERENCE_OUTPUT_LEVEL_CHANGE_DB_PLOSIVE_DEFAULT,
        unit="dB",
        method="%s; %s" % (_SIGNAL_METHOD, _OUTPUT_METHOD),
        setting="the setting the reference comes with",
    ),
    Restoration(
        name="REFERENCE_OUTPUT_LEVEL_CHANGE_DB_HISS_BEST",
        series="band_restoration",
        case="hiss",
        metric="output_level_change_db",
        value=REFERENCE_OUTPUT_LEVEL_CHANGE_DB_HISS_BEST,
        unit="dB",
        method="%s; %s" % (_SIGNAL_METHOD, _OUTPUT_METHOD),
        setting="the best setting the reference offers",
    ),
    Restoration(
        name="REFERENCE_OUTPUT_LEVEL_CHANGE_DB_PINK_HISS_BEST",
        series="band_restoration",
        case="pink_hiss",
        metric="output_level_change_db",
        value=REFERENCE_OUTPUT_LEVEL_CHANGE_DB_PINK_HISS_BEST,
        unit="dB",
        method="%s; %s" % (_SIGNAL_METHOD, _OUTPUT_METHOD),
        setting="the best setting the reference offers",
    ),
    Restoration(
        name="REFERENCE_OUTPUT_LEVEL_CHANGE_DB_HUM50_BEST",
        series="band_restoration",
        case="hum50",
        metric="output_level_change_db",
        value=REFERENCE_OUTPUT_LEVEL_CHANGE_DB_HUM50_BEST,
        unit="dB",
        method="%s; %s" % (_SIGNAL_METHOD, _OUTPUT_METHOD),
        setting="the best setting the reference offers",
    ),
    Restoration(
        name="REFERENCE_OUTPUT_LEVEL_CHANGE_DB_RUMBLE_BEST",
        series="band_restoration",
        case="rumble",
        metric="output_level_change_db",
        value=REFERENCE_OUTPUT_LEVEL_CHANGE_DB_RUMBLE_BEST,
        unit="dB",
        method="%s; %s" % (_SIGNAL_METHOD, _OUTPUT_METHOD),
        setting="the best setting the reference offers",
    ),
    Restoration(
        name="REFERENCE_OUTPUT_LEVEL_CHANGE_DB_VOICE_HISS_BEST",
        series="band_restoration",
        case="voice_hiss",
        metric="output_level_change_db",
        value=REFERENCE_OUTPUT_LEVEL_CHANGE_DB_VOICE_HISS_BEST,
        unit="dB",
        method="%s; %s" % (_SIGNAL_METHOD, _OUTPUT_METHOD),
        setting="the best setting the reference offers",
    ),
    Restoration(
        name="REFERENCE_OUTPUT_LEVEL_CHANGE_DB_PLOSIVE_BEST",
        series="band_restoration",
        case="plosive",
        metric="output_level_change_db",
        value=REFERENCE_OUTPUT_LEVEL_CHANGE_DB_PLOSIVE_BEST,
        unit="dB",
        method="%s; %s" % (_SIGNAL_METHOD, _OUTPUT_METHOD),
        setting="the best setting the reference offers",
    ),
    Restoration(
        name="REFERENCE_OUTPUT_SILENT_SHARE_HISS_BEST",
        series="band_restoration",
        case="hiss",
        metric="output_silent_share",
        value=REFERENCE_OUTPUT_SILENT_SHARE_HISS_BEST,
        unit="fraction",
        method="%s; %s" % (_SIGNAL_METHOD, _OUTPUT_METHOD),
        setting="the best setting the reference offers",
    ),
    Restoration(
        name="REFERENCE_OUTPUT_SILENT_SHARE_PINK_HISS_BEST",
        series="band_restoration",
        case="pink_hiss",
        metric="output_silent_share",
        value=REFERENCE_OUTPUT_SILENT_SHARE_PINK_HISS_BEST,
        unit="fraction",
        method="%s; %s" % (_SIGNAL_METHOD, _OUTPUT_METHOD),
        setting="the best setting the reference offers",
    ),
    Restoration(
        name="REFERENCE_OUTPUT_SILENT_SHARE_HUM50_BEST",
        series="band_restoration",
        case="hum50",
        metric="output_silent_share",
        value=REFERENCE_OUTPUT_SILENT_SHARE_HUM50_BEST,
        unit="fraction",
        method="%s; %s" % (_SIGNAL_METHOD, _OUTPUT_METHOD),
        setting="the best setting the reference offers",
    ),
    Restoration(
        name="REFERENCE_OUTPUT_SILENT_SHARE_RUMBLE_BEST",
        series="band_restoration",
        case="rumble",
        metric="output_silent_share",
        value=REFERENCE_OUTPUT_SILENT_SHARE_RUMBLE_BEST,
        unit="fraction",
        method="%s; %s" % (_SIGNAL_METHOD, _OUTPUT_METHOD),
        setting="the best setting the reference offers",
    ),
    Restoration(
        name="REFERENCE_OUTPUT_SILENT_SHARE_VOICE_HISS_BEST",
        series="band_restoration",
        case="voice_hiss",
        metric="output_silent_share",
        value=REFERENCE_OUTPUT_SILENT_SHARE_VOICE_HISS_BEST,
        unit="fraction",
        method="%s; %s" % (_SIGNAL_METHOD, _OUTPUT_METHOD),
        setting="the best setting the reference offers",
    ),
    Restoration(
        name="REFERENCE_OUTPUT_SILENT_SHARE_PLOSIVE_BEST",
        series="band_restoration",
        case="plosive",
        metric="output_silent_share",
        value=REFERENCE_OUTPUT_SILENT_SHARE_PLOSIVE_BEST,
        unit="fraction",
        method="%s; %s" % (_SIGNAL_METHOD, _OUTPUT_METHOD),
        setting="the best setting the reference offers",
    ),
    Restoration(
        name="REFERENCE_OUTPUT_SILENT_SHARE_DEFAULT",
        series="band_restoration",
        case=EVERY_CASE,
        metric="output_silent_share",
        value=REFERENCE_OUTPUT_SILENT_SHARE_DEFAULT,
        unit="fraction",
        method="%s; %s; at this setting every sample of every case of the "
        "table is not silent: the silence belongs to the best setting alone" % (
            _SIGNAL_METHOD, _OUTPUT_METHOD
        ),
        setting="the setting the reference comes with",
    ),
    # -- how much the gain curve of the reference breathes --------------------
    Restoration(
        name="REFERENCE_GAIN_VAR_DB2_HISS_DEFAULT",
        series="band_restoration",
        case="hiss",
        metric="gain_var_db2",
        value=REFERENCE_GAIN_VAR_DB2_HISS_DEFAULT,
        unit="dB^2",
        method="%s; %s" % (_SIGNAL_METHOD, _GAIN_METHOD),
        setting="the setting the reference comes with",
    ),
    Restoration(
        name="REFERENCE_GAIN_VAR_DB2_PINK_HISS_DEFAULT",
        series="band_restoration",
        case="pink_hiss",
        metric="gain_var_db2",
        value=REFERENCE_GAIN_VAR_DB2_PINK_HISS_DEFAULT,
        unit="dB^2",
        method="%s; %s" % (_SIGNAL_METHOD, _GAIN_METHOD),
        setting="the setting the reference comes with",
    ),
    Restoration(
        name="REFERENCE_GAIN_VAR_DB2_HUM50_DEFAULT",
        series="band_restoration",
        case="hum50",
        metric="gain_var_db2",
        value=REFERENCE_GAIN_VAR_DB2_HUM50_DEFAULT,
        unit="dB^2",
        method="%s; %s" % (_SIGNAL_METHOD, _GAIN_METHOD),
        setting="the setting the reference comes with",
    ),
    Restoration(
        name="REFERENCE_GAIN_VAR_DB2_RUMBLE_DEFAULT",
        series="band_restoration",
        case="rumble",
        metric="gain_var_db2",
        value=REFERENCE_GAIN_VAR_DB2_RUMBLE_DEFAULT,
        unit="dB^2",
        method="%s; %s" % (_SIGNAL_METHOD, _GAIN_METHOD),
        setting="the setting the reference comes with",
    ),
    Restoration(
        name="REFERENCE_GAIN_VAR_DB2_VOICE_HISS_DEFAULT",
        series="band_restoration",
        case="voice_hiss",
        metric="gain_var_db2",
        value=REFERENCE_GAIN_VAR_DB2_VOICE_HISS_DEFAULT,
        unit="dB^2",
        method="%s; %s" % (_SIGNAL_METHOD, _GAIN_METHOD),
        setting="the setting the reference comes with",
    ),
    Restoration(
        name="REFERENCE_GAIN_VAR_DB2_PLOSIVE_DEFAULT",
        series="band_restoration",
        case="plosive",
        metric="gain_var_db2",
        value=REFERENCE_GAIN_VAR_DB2_PLOSIVE_DEFAULT,
        unit="dB^2",
        method="%s; %s" % (_SIGNAL_METHOD, _GAIN_METHOD),
        setting="the setting the reference comes with",
    ),
    Restoration(
        name="REFERENCE_GAIN_VAR_DB2_HISS_BEST",
        series="band_restoration",
        case="hiss",
        metric="gain_var_db2",
        value=REFERENCE_GAIN_VAR_DB2_HISS_BEST,
        unit="dB^2",
        method="%s; %s" % (_SIGNAL_METHOD, _GAIN_METHOD),
        setting="the best setting the reference offers",
    ),
    Restoration(
        name="REFERENCE_GAIN_VAR_DB2_PINK_HISS_BEST",
        series="band_restoration",
        case="pink_hiss",
        metric="gain_var_db2",
        value=REFERENCE_GAIN_VAR_DB2_PINK_HISS_BEST,
        unit="dB^2",
        method="%s; %s" % (_SIGNAL_METHOD, _GAIN_METHOD),
        setting="the best setting the reference offers",
    ),
    Restoration(
        name="REFERENCE_GAIN_VAR_DB2_VOICE_HISS_BEST",
        series="band_restoration",
        case="voice_hiss",
        metric="gain_var_db2",
        value=REFERENCE_GAIN_VAR_DB2_VOICE_HISS_BEST,
        unit="dB^2",
        method="%s; %s" % (_SIGNAL_METHOD, _GAIN_METHOD),
        setting="the best setting the reference offers",
    ),
    Restoration(
        name="REFERENCE_GAIN_VAR_DB2_PLOSIVE_BEST",
        series="band_restoration",
        case="plosive",
        metric="gain_var_db2",
        value=REFERENCE_GAIN_VAR_DB2_PLOSIVE_BEST,
        unit="dB^2",
        method="%s; %s" % (_SIGNAL_METHOD, _GAIN_METHOD),
        setting="the best setting the reference offers",
    ),
    # -- how repeatable the reference is --------------------------------------
    Restoration(
        name="REFERENCE_REPRODUCIBLE",
        series="band_restoration",
        case=EVERY_CASE,
        metric="reproducible",
        value=float(REFERENCE_REPRODUCIBLE),
        unit="flag",
        method="the same setting on the same input, three runs through the "
        "study's renderer: the waveform "
        "is not the same, and the figures move by up to the spread below",
        setting="the setting the reference comes with",
    ),
    Restoration(
        name="REFERENCE_REPRODUCIBILITY_SPREAD_DB",
        series="band_restoration",
        case=EVERY_CASE,
        metric="reproducibility_spread_db",
        value=REFERENCE_REPRODUCIBILITY_SPREAD_DB,
        unit="dB",
        method="the spread of the figures over three runs of the same setting "
        "through the study's renderer: "
        "the removal figure moves by 0.22 dB on the white bed and the wanted "
        "figure by 0.80 dB on the hum case",
    ),
    Restoration(
        name="REFERENCE_REPRODUCIBLE_OURS",
        series="ours",
        case=EVERY_CASE,
        metric="reproducible",
        value=float(REFERENCE_REPRODUCIBLE_OURS),
        unit="flag",
        method=_OURS_METHOD,
        setting="our own modules, two runs: the waveform is bit identical",
    ),
    # -- the first band edge, and the latency that belongs to the model ------
    Restoration(
        name="REFERENCE_FIRST_CROSSOVER_DEFAULT_HZ",
        series="band_restoration",
        case=EVERY_CASE,
        metric="default_crossover_hz",
        value=REFERENCE_FIRST_CROSSOVER_DEFAULT_HZ,
        unit="Hz",
        method="the frequency of the first band edge of the reference, read "
        "out of the controller of the reference as it comes, with the study's "
        "surface host",
    ),
    Restoration(
        name="REFERENCE_LATENCY_SAMPLES_MIN",
        series="band_restoration",
        case=EVERY_CASE,
        metric="latency_samples",
        value=REFERENCE_LATENCY_SAMPLES_MIN,
        unit="samples",
        method="the smallest latency the reference declares over the settings "
        "of the table, read out of the study's surface host; the delay belongs "
        "to "
        "the model that is loaded, not to the plug-in",
    ),
    Restoration(
        name="REFERENCE_LATENCY_SAMPLES_MAX",
        series="band_restoration",
        case=EVERY_CASE,
        metric="latency_samples",
        value=REFERENCE_LATENCY_SAMPLES_MAX,
        unit="samples",
        method="the largest latency the reference declares over the settings "
        "of the table, read out of the study's surface host; the delay belongs "
        "to the model that is loaded, not to "
        "the plug-in",
    ),
    # -- our own side of the same table --------------------------------------
    Restoration(
        name="REFERENCE_HISS_REMOVED_DB_OURS",
        series="ours",
        case="hiss",
        metric="removed_db",
        value=REFERENCE_HISS_REMOVED_DB_OURS,
        unit="dB",
        method=_OURS_METHOD,
        setting="our module, at its own default settings",
    ),
    Restoration(
        name="REFERENCE_PINK_HISS_REMOVED_DB_OURS",
        series="ours",
        case="pink_hiss",
        metric="removed_db",
        value=REFERENCE_PINK_HISS_REMOVED_DB_OURS,
        unit="dB",
        method=_OURS_METHOD,
        setting="our module, at its own default settings",
    ),
    Restoration(
        name="REFERENCE_HUM50_REMOVED_DB_OURS",
        series="ours",
        case="hum50",
        metric="removed_db",
        value=REFERENCE_HUM50_REMOVED_DB_OURS,
        unit="dB",
        method=_OURS_METHOD,
        setting="our module, at its own default settings",
    ),
    Restoration(
        name="REFERENCE_RUMBLE_REMOVED_DB_OURS",
        series="ours",
        case="rumble",
        metric="removed_db",
        value=REFERENCE_RUMBLE_REMOVED_DB_OURS,
        unit="dB",
        method=_OURS_METHOD,
        setting="our module, at its own default settings",
    ),
    Restoration(
        name="REFERENCE_VOICE_HISS_REMOVED_DB_OURS",
        series="ours",
        case="voice_hiss",
        metric="removed_db",
        value=REFERENCE_VOICE_HISS_REMOVED_DB_OURS,
        unit="dB",
        method=_OURS_METHOD,
        setting="our module, at its own default settings",
    ),
    Restoration(
        name="REFERENCE_HISS_KEPT_DB_OURS",
        series="ours",
        case="hiss",
        metric="kept_db",
        value=REFERENCE_HISS_KEPT_DB_OURS,
        unit="dB",
        method=_OURS_METHOD,
        setting="our module, at its own default settings",
    ),
    Restoration(
        name="REFERENCE_PINK_HISS_KEPT_DB_OURS",
        series="ours",
        case="pink_hiss",
        metric="kept_db",
        value=REFERENCE_PINK_HISS_KEPT_DB_OURS,
        unit="dB",
        method=_OURS_METHOD,
        setting="our module, at its own default settings",
    ),
    Restoration(
        name="REFERENCE_HUM50_KEPT_DB_OURS",
        series="ours",
        case="hum50",
        metric="kept_db",
        value=REFERENCE_HUM50_KEPT_DB_OURS,
        unit="dB",
        method=_OURS_METHOD,
        setting="our module, at its own default settings",
    ),
    Restoration(
        name="REFERENCE_RUMBLE_KEPT_DB_OURS",
        series="ours",
        case="rumble",
        metric="kept_db",
        value=REFERENCE_RUMBLE_KEPT_DB_OURS,
        unit="dB",
        method=_OURS_METHOD,
        setting="our module, at its own default settings",
    ),
    Restoration(
        name="REFERENCE_VOICE_HISS_KEPT_DB_OURS",
        series="ours",
        case="voice_hiss",
        metric="kept_db",
        value=REFERENCE_VOICE_HISS_KEPT_DB_OURS,
        unit="dB",
        method=_OURS_METHOD,
        setting="our module, at its own default settings",
    ),
    # -- the bypass -----------------------------------------------------------
    Restoration(
        name="REFERENCE_BYPASS_BIT_EXACT",
        series="stem_separation",
        case="clean_music",
        metric="bypass_bit_exact",
        value=float(REFERENCE_BYPASS_BIT_EXACT),
        unit="flag",
        method=_BYPASS_METHOD,
        setting="the bypass switch on",
    ),
    Restoration(
        name="REFERENCE_BYPASS_MAX_DIFF",
        series="stem_separation",
        case="clean_music",
        metric="bypass_max_diff",
        value=REFERENCE_BYPASS_MAX_DIFF,
        unit="sample difference",
        method=_BYPASS_METHOD,
        setting="the bypass switch on",
    ),
    Restoration(
        name="REFERENCE_BYPASS_DELAY_SAMPLES_256",
        series="stem_separation",
        case=EVERY_CASE,
        metric="bypass_delay_samples",
        value=REFERENCE_BYPASS_DELAY_SAMPLES_256,
        unit="samples",
        method=_BYPASS_METHOD,
        setting="48 kHz, a block of 256 samples",
    ),
    Restoration(
        name="REFERENCE_BYPASS_DELAY_SAMPLES_512",
        series="stem_separation",
        case=EVERY_CASE,
        metric="bypass_delay_samples",
        value=REFERENCE_BYPASS_DELAY_SAMPLES_512,
        unit="samples",
        method=_BYPASS_METHOD,
        setting="48 kHz, a block of 512 samples",
    ),
    Restoration(
        name="REFERENCE_BYPASS_DELAY_SAMPLES_1024",
        series="stem_separation",
        case=EVERY_CASE,
        metric="bypass_delay_samples",
        value=REFERENCE_BYPASS_DELAY_SAMPLES_1024,
        unit="samples",
        method=_BYPASS_METHOD,
        setting="48 kHz, a block of 1024 samples",
    ),
    Restoration(
        name="REFERENCE_BYPASS_DELAY_MS_AT_48K",
        series="stem_separation",
        case=EVERY_CASE,
        metric="bypass_delay_ms",
        value=REFERENCE_BYPASS_DELAY_MS_AT_48K,
        unit="ms",
        method=_BYPASS_METHOD,
        setting="48 kHz, a block of 512 samples",
    ),
    # -- the defaults ---------------------------------------------------------
    Restoration(
        name="REFERENCE_DEFAULT_MAX_DIFF",
        series="stem_separation",
        case="clean_music",
        metric="default_max_diff",
        value=REFERENCE_DEFAULT_MAX_DIFF,
        unit="sample difference",
        method=_DEFAULTS_METHOD,
        setting="the setting the reference comes with",
    ),
    Restoration(
        name="REFERENCE_DEFAULT_MAX_DIFF_DB",
        series="stem_separation",
        case="clean_music",
        metric="default_max_diff_db",
        value=REFERENCE_DEFAULT_MAX_DIFF_DB,
        unit="dB",
        method=_DEFAULTS_METHOD,
        setting="the setting the reference comes with",
    ),
    Restoration(
        name="REFERENCE_DEFAULT_MAX_DIFF_24BIT_STEPS",
        series="stem_separation",
        case="clean_music",
        metric="default_max_diff_steps",
        value=REFERENCE_DEFAULT_MAX_DIFF_24BIT_STEPS,
        unit="steps of 24 bit",
        method=_DEFAULTS_METHOD,
        setting="the setting the reference comes with",
    ),
    Restoration(
        name="REFERENCE_DEFAULT_GAIN_DB",
        series="stem_separation",
        case="sine_probe",
        metric="default_gain_db",
        value=REFERENCE_DEFAULT_GAIN_DB,
        unit="dB",
        method=_DEFAULTS_METHOD,
        setting="the setting the reference comes with",
    ),
    Restoration(
        name="REFERENCE_DEFAULT_GAIN_SPREAD_DB",
        series="stem_separation",
        case="sine_probe",
        metric="default_gain_spread_db",
        value=REFERENCE_DEFAULT_GAIN_SPREAD_DB,
        unit="dB",
        method=_DEFAULTS_METHOD,
        setting="the setting the reference comes with",
    ),
    Restoration(
        name="REFERENCE_DEFAULT_GAIN_SD_DB",
        series="stem_separation",
        case="clean_music",
        metric="default_gain_sd_db",
        value=REFERENCE_DEFAULT_GAIN_SD_DB,
        unit="dB",
        method=_DEFAULTS_METHOD,
        setting="the setting the reference comes with",
    ),
    Restoration(
        name="REFERENCE_DEFAULT_PRE_SIGNAL_NOISE_DBFS",
        series="stem_separation",
        case="clean_music",
        metric="noise_floor_dbfs",
        value=REFERENCE_DEFAULT_PRE_SIGNAL_NOISE_DBFS,
        unit="dBFS",
        method=_DEFAULTS_METHOD,
        setting="the setting the reference comes with",
    ),
    # -- ours, measured with the same instrument --------------------------------
    Restoration(
        name="OWN_BYPASS_BIT_EXACT",
        series="ours",
        case="clean_music",
        metric="bypass_bit_exact",
        value=float(OWN_BYPASS_BIT_EXACT),
        unit="flag",
        method=_OURS_METHOD,
        setting="our module, with the bypass on",
    ),
    Restoration(
        name="OWN_BYPASS_DELAY_SAMPLES",
        series="ours",
        case=EVERY_CASE,
        metric="bypass_delay_samples",
        value=OWN_BYPASS_DELAY_SAMPLES,
        unit="samples",
        method=_OURS_METHOD,
        setting="our module, with the bypass on",
    ),
    Restoration(
        name="OWN_DEFAULT_BIT_EXACT_ON_CLEAN",
        series="ours",
        case="clean_music",
        metric="default_bit_exact",
        value=float(OWN_DEFAULT_BIT_EXACT_ON_CLEAN),
        unit="flag",
        method=_OURS_METHOD,
        setting="our module, at its own default settings",
    ),
)

#: The numbers of the restoration study, keyed by the name of their constant.
RESTORATION_BY_NAME: dict[str, Restoration] = {
    restoration.name: restoration for restoration in RESTORATIONS
}


def restorations_for(case: str, metric: str) -> tuple[Restoration, ...]:
    """Every number of a case and a metric, one per series.

    A number recorded under the name ``"*"`` belongs to every case: the cost,
    the latency, the surface and the laws of a product are properties of the
    product, not of the material.
    """
    return tuple(
        restoration
        for restoration in RESTORATIONS
        if restoration.metric == metric and restoration.case in (case, EVERY_CASE)
    )


def restoration_for(case: str, metric: str) -> float | None:
    """The number a case and a metric are held to.

    When more than one series has a number, the **strongest** one is the
    number: the gap that is worth closing is the one against the best of
    them.
    """
    found = [
        restoration
        for restoration in restorations_for(case, metric)
        if restoration.value is not None
    ]
    if not found:
        return None
    rule = METRIC_BY_NAME[metric]
    if rule.higher_is_better:
        return max(float(restoration.value) for restoration in found)
    return min(float(restoration.value) for restoration in found)
