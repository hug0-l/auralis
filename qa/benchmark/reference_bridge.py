"""
qa.benchmark.reference_bridge — the numbers of the measurement bridge.

This file holds the last measurement set of the study: our own modules and
the reference, measured on the same cases, with the same rules and with the
same code, so that the two sides of the comparison cannot drift apart.

The shape is the shape of the other constants files of this directory
(``qa/benchmark/reference.py``, ``qa/benchmark/reference_restoration.py``,
``qa/benchmark/reference_voice.py``): a number is a number, it carries no
name, no code and no parameter of the product it was measured on, and what
travels with it is the **case** it belongs to, the **unit** it is in and the
**way it was measured**.  A number without those three cannot be read, cannot
be argued about and cannot be replaced when a better measurement arrives.

The instrument
--------------

The bridge of the study drives a plug-in, renders a case through it, writes
the result and measures what came out with the product's **own** readers and
judges it with the product's **own** rules:

* the ground truth cases are measured with ``qa/quality/measurement.py`` and
  judged by ``qa/quality/quality_rules.py``;
* the voice cases are measured with ``qa/qualityvoice/harness.py`` and judged
  by ``qa/qualityvoice/rules.py``;
* the clips of the corpus are measured with ``corpus/measure.py`` against the
  ground truth of ``corpus/manifest.json``.

Both sides go through the same code: our own module is rendered by the
product's own engine and the reference is rendered through the study's own
host, and the self check of the bridge proves that the measurement of the
bridge and the measurement of the product agree to the last bit on every rule
of the product.

The two sides of the comparison are described by their shape and never by
their names:

``stem_separation``
    the reference that splits a recording into three stems and gives each of
    them a gain and three switches;
``ours``
    not a reference: the measurements of our own modules, filed under their
    own series so that they are never mistaken for one.

**A reference is a target, not a contract.**  It was measured on somebody
else's build, on somebody else's machine, with somebody else's settings, so
the benchmark reports the gap and does not fail on it.

The cases and the families
--------------------------

Three families of case, and three harnesses that own them:

``quality``
    the ground truth cases of ``qa/quality/ground_truth.py``;
``voice``
    the voice cases of ``qa/qualityvoice/harness.py``;
``corpus``
    the clips of ``corpus/manifest.json``.

Two of the families carry a case of the same name (the voice under hiss is a
ground truth case and a voice case as well), so a number carries the **family**
it belongs to next to its case.  The cases of a family are written down in
``CASES``, one family at a time.

The tree the numbers describe
-----------------------------

The numbers of ours were measured on the tree of the study's day, and they
were measured again on the tree of today with the same instrument: every
figure below came out the same, to the last digit.  The gates of the product
(``tests/test_quality_spectral.py`` and ``tests/test_quality_voice.py``)
measure the modules live and are the ones that say whether the numbers still
hold; the gate of this file (``tests/test_benchmark_bridge.py``) holds the
constants to their properties.

The numbers that are already in the tree
----------------------------------------

The corpus head to head of the same subjects was measured by an earlier wave
of the study, and its numbers live in the sibling files
(``qa/benchmark/reference_restoration.py`` and
``qa/benchmark/reference_masking.py``).  Those constants are not changed by
this file.  The head to head below is the bridge's own measurement of the
same quantities, taken with the ruler of the bridge on the tree of the
study's day; where the two differ, the reader has both.

The list of the defects these numbers show is ``docs/OPEN_DEFECTS.md``.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "CASES",
    "EVERY_CASE",
    "FAMILIES",
    "Metric",
    "METRICS",
    "METRIC_BY_NAME",
    "Bridge",
    "BRIDGES",
    "BRIDGE_BY_NAME",
    "SERIES",
    "bridges_for",
    "bridge_for",
]


@dataclass(frozen=True)
class Metric:
    """One number of the table and what a good number of it looks like."""

    name: str
    unit: str
    higher_is_better: bool
    note: str = ""


@dataclass(frozen=True)
class Bridge:
    """One number of the measurement bridge, with everything needed to read it."""

    name: str
    series: str
    family: str
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
            "family": self.family,
            "case": self.case,
            "metric": self.metric,
            "value": self.value,
            "unit": self.unit,
            "method": self.method,
            "setting": self.setting,
        }


#: Every metric the bridge measured.
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
        name="gain_var_db2",
        unit="dB^2",
        higher_is_better=False,
        note="How much the gain curve breathes inside the artefact band.",
    ),
    Metric(
        name="chatter_db",
        unit="dB",
        higher_is_better=False,
        note="How much the gain chatters while a voice speaks.",
    ),
    Metric(
        name="event_reduction_db",
        unit="dB",
        higher_is_better=True,
        note="How much of an impulsive artefact is taken away.",
    ),
    Metric(
        name="programme_shift_db",
        unit="dB",
        higher_is_better=False,
        note="How far the programme around an impulsive artefact moves.",
    ),
    Metric(
        name="reference_error_db",
        unit="dB",
        higher_is_better=True,
        note="How much closer to the truth a restoration comes.",
    ),
    Metric(
        name="latency_samples",
        unit="samples",
        higher_is_better=False,
        note="How many samples the output lags the input.",
    ),
    Metric(
        name="block_coherence_db",
        unit="dB",
        higher_is_better=False,
        note=(
            "How much the output changes when the host chops the stream into "
            "blocks; the budget of the product is -60 dB."
        ),
    ),
    Metric(
        name="band_removed_db",
        unit="dB",
        higher_is_better=True,
        note="How much of a band the manifest names is taken away.",
    ),
    Metric(
        name="band_moved_db",
        unit="dB",
        higher_is_better=False,
        note="How far a band the manifest names moves.",
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
        note="How much a figure moves between two runs of the same setting.",
    ),
    Metric(
        name="output_drift",
        unit="relative",
        higher_is_better=False,
        note=(
            "How far the samples of the output move between two runs of the "
            "same setting, relative to the peak of the output."
        ),
    ),
)

#: The metrics, keyed by name.
METRIC_BY_NAME: dict[str, Metric] = {metric.name: metric for metric in METRICS}

#: The series the numbers are filed under, keyed by their name.
SERIES: dict[str, str] = {
    "stem_separation": (
        "the reference that splits a recording into three stems and gives each "
        "of them a gain and three switches"
    ),
    "ours": (
        "not a reference: the measurements of our own modules, filed under "
        "their own series so that they are never mistaken for one"
    ),
}

#: The case that means "every case": a number that is a property of the
#: product and not of the material it is measured on.
EVERY_CASE = "*"

#: The families of case, and the harness that owns each of them.
FAMILIES: dict[str, str] = {
    "quality": "qa/quality/ground_truth.py",
    "voice": "qa/qualityvoice/harness.py",
    "corpus": "corpus/manifest.json",
    "table": "the whole table of the bridge, every family of case at once",
}

#: The cases of every family, and what each of them is.
CASES: dict[str, dict[str, str]] = {
    "quality": {
        "tone_noise": (
            "a 1 kHz tone over a stationary broadband bed: the wanted part and "
            "the unwanted part are separate, so what was removed and what was "
            "kept are both measurable"
        ),
        "tone_hum": (
            "a 1 kHz tone over mains hum: the hum has to go and the tone has "
            "to stay"
        ),
        "voice_hiss": (
            "a harmonic voice like stack (its partials sit 120 Hz apart) under "
            "broadband hiss: two of its partials share a bin"
        ),
        "music_mixture": (
            "a chord plus a plucked note over a noisy bed: the plucked note is "
            "a wanted partial that arrives by chance"
        ),
        "transient": (
            "a steady programme with four impulsive artefacts: the sample "
            "range of every burst is known"
        ),
        "clipped": (
            "a programme driven past full scale: the truth is the programme "
            "before the clipping"
        ),
    },
    "voice": {
        "voice_hiss": "a voice under a white bed, 45 dB down",
        "voice_pink_hiss": "the same voice under a pink bed",
        "voice_clean": "the voice alone, with no background at all",
        "voice_breath": "a voice with a breath in it",
        "voice_click": "a voice with a mouth click in it",
        "voice_sibilance": "a voice with a sibilant in it",
    },
    "corpus": {
        "hiss": "a steady white bed with nothing wanted in it",
        "pink_hiss": "the same bed with a pink spectrum",
        "voice_hiss": (
            "a voice like tone under hiss: the hiss of the clip lives in the "
            "difference between the two channels"
        ),
        "hum50": (
            "mains hum with its first harmonic under a 1 kHz programme tone"
        ),
        "clicks": "a programme with six clicks in it",
        "clipped": "a programme that was driven past full scale",
    },
    "table": {
        EVERY_CASE: (
            "every case: the number is a property of the product and not of "
            "the material it is measured on"
        ),
    },
}

#: How the numbers of the ground truth cases were taken.
#:
#: The instrument is named, because the study has more than one host and only
#: one of them moves a parameter of its own and never puts it back (see
#: docs/BENCHMARK_NOTES.md, section 5.1).  The bridge of the study drives the
#: study's renderer, which writes a parameter only when it is asked to.
_QUALITY_METHOD = (
    "measured with the quality instrument of the project "
    "(qa/quality/measurement.py) on the ground truth case, and "
    "judged by the rule of the module "
    "(qa/quality/quality_rules.py); our own module is rendered by "
    "the product's own engine and the reference is rendered through "
    "the study's renderer, and both are measured with the same code"
)

#: How the numbers of the voice cases were taken.
_VOICE_METHOD = (
    "measured with the voice instrument of the project "
    "(qa/qualityvoice/harness.py) on the voice case, and judged by "
    "the rule of the module (qa/qualityvoice/rules.py); the "
    "reference is rendered through the study's renderer and "
    "measured with the same code"
)

#: How the numbers of the clips of the corpus were taken.
_CORPUS_METHOD = (
    "measured with the ruler of the corpus (corpus/measure.py) "
    "against the ground truth of the corpus manifest: the level of "
    "the band the manifest names, before and after; our own module is "
    "rendered by the product's own engine and the reference by the "
    "study's renderer"
)

#: How our own side was measured.
_OURS_METHOD = (
    "%s; the number is a measurement of our own module and not of "
    "the reference"
)

#: How our own side was measured on the ground truth cases.
_OURS_QUALITY_METHOD = (
    "measured with the quality instrument of the project "
    "(qa/quality/measurement.py) on the ground truth case, and "
    "judged by the rule of the module "
    "(qa/quality/quality_rules.py); our own module is rendered by "
    "the product's own engine and the reference is rendered through "
    "the study's renderer, and both are measured with the same "
    "code; the number is a measurement of our own module and not of "
    "the reference"
)

#: How our own side was measured on the voice cases.
_OURS_VOICE_METHOD = (
    "measured with the voice instrument of the project "
    "(qa/qualityvoice/harness.py) on the voice case, and judged by "
    "the rule of the module (qa/qualityvoice/rules.py); the "
    "reference is rendered through the study's renderer and "
    "measured with the same code; the number is a measurement of "
    "our own module and not of the reference"
)

#: How the determinism of the two sides was measured.
_DETERMINISM_METHOD = (
    "the whole table of the study's bridge, which drives the study's "
    "renderer, was measured twice and the two "
    "runs were compared row by row"
)

#: The setting our own modules were measured at.
_OURS_SETTING = "our own module at its own default settings"

#: The setting the reference comes with.
_REFERENCE_SETTING = "the setting the reference comes with"

#: How the determinism of OUR_REPRODUCIBLE was measured.
_DETERMINISM_OURS_METHOD = (
    "our own modules: the whole table of the study's bridge, which drives "
    "the study's renderer, was measured "
    "twice and the two runs were compared row by row; every row of "
    "ours whose module did not change between the two runs carries "
    "the same numbers to the last bit"
)

#: How the determinism of REFERENCE_CHATTER_SPREAD_DB was measured.
_DETERMINISM_CHATTER_METHOD = (
    "the reference: the whole table of the study's bridge, which drives the "
    "study's renderer, was measured "
    "twice and the two runs were compared row by row; the largest "
    "move of the chatter of the reference between the two runs"
)

#: How the determinism of REFERENCE_OUTPUT_DRIFT was measured.
_DETERMINISM_DRIFT_METHOD = (
    "the reference: the whole table of the study's bridge, which drives the "
    "study's renderer, was measured "
    "twice and the two runs were compared row by row; the output of "
    "the reference moves by about this much, relative to its own "
    "peak, between two runs of the same setting"
)

#: How the determinism of REFERENCE_REPRODUCIBILITY_SPREAD_DB was measured.
_DETERMINISM_SPREAD_METHOD = (
    "the reference: the whole table of the study's bridge, which drives the "
    "study's renderer, was measured "
    "twice and the two runs were compared row by row; the largest "
    "move of any figure of the reference between the two runs: the "
    "block coherence of a row of about -126 dB moved by 0.11 dB"
)

#: How the determinism of REFERENCE_REPRODUCIBLE was measured.
_DETERMINISM_REFERENCE_METHOD = (
    "the reference: the whole table of the study's bridge, which drives the "
    "study's renderer, was measured "
    "twice and the two runs were compared row by row; the waveform "
    "of the reference is not the same in the two runs"
)



def _ours(name, family, case, metric, value, method, setting=_OURS_SETTING):
    """One number of our own modules."""
    return Bridge(
        name=name,
        series="ours",
        family=family,
        case=case,
        metric=metric,
        value=value,
        unit=METRIC_BY_NAME[metric].unit,
        method=method,
        setting=setting,
    )


def _reference(name, family, case, metric, value, method, setting=_REFERENCE_SETTING):
    """One number of the reference."""
    return Bridge(
        name=name,
        series="stem_separation",
        family=family,
        case=case,
        metric=metric,
        value=value,
        unit=METRIC_BY_NAME[metric].unit,
        method=method,
        setting=setting,
    )


# ---------------------------------------------------------------------------
# the numbers
# ---------------------------------------------------------------------------
OUR_QUALITY_BANDRESTORATION_TONE_NOISE_REMOVED_DB = 18.5151
OUR_QUALITY_BANDRESTORATION_TONE_NOISE_KEPT_DB = 0.0304
OUR_QUALITY_BANDRESTORATION_TONE_NOISE_GAIN_VAR_DB2 = 0.0193
OUR_QUALITY_BANDRESTORATION_TONE_NOISE_LATENCY_SAMPLES = 0.0
OUR_QUALITY_BANDRESTORATION_TONE_NOISE_BLOCK_COHERENCE_DB = -200.0
OUR_QUALITY_SPECTRALDENOISE_TONE_NOISE_REMOVED_DB = 14.3466
OUR_QUALITY_SPECTRALDENOISE_TONE_NOISE_KEPT_DB = 0.0161
OUR_QUALITY_SPECTRALDENOISE_TONE_NOISE_GAIN_VAR_DB2 = 0.2072
OUR_QUALITY_SPECTRALDENOISE_TONE_NOISE_LATENCY_SAMPLES = 0.0
OUR_QUALITY_SPECTRALDENOISE_TONE_NOISE_BLOCK_COHERENCE_DB = -71.525
OUR_QUALITY_SPECTRALDENOISE_TONE_HUM_REMOVED_DB = 12.2972
OUR_QUALITY_SPECTRALDENOISE_TONE_HUM_KEPT_DB = 0.011
OUR_QUALITY_SPECTRALDENOISE_TONE_HUM_GAIN_VAR_DB2 = 0.1893
OUR_QUALITY_SPECTRALDENOISE_TONE_HUM_LATENCY_SAMPLES = 0.0
OUR_QUALITY_SPECTRALDENOISE_TONE_HUM_BLOCK_COHERENCE_DB = -68.3432
OUR_QUALITY_SPECTRALDENOISE_VOICE_HISS_REMOVED_DB = 42.5481
OUR_QUALITY_SPECTRALDENOISE_VOICE_HISS_KEPT_DB = 9.1645
OUR_QUALITY_SPECTRALDENOISE_VOICE_HISS_GAIN_VAR_DB2 = 0.0529
OUR_QUALITY_SPECTRALDENOISE_VOICE_HISS_LATENCY_SAMPLES = 960.0
OUR_QUALITY_SPECTRALDENOISE_VOICE_HISS_BLOCK_COHERENCE_DB = -65.1982
OUR_QUALITY_SPECTRALDENOISE_MUSIC_MIXTURE_REMOVED_DB = 12.8315
OUR_QUALITY_SPECTRALDENOISE_MUSIC_MIXTURE_KEPT_DB = 1.683
OUR_QUALITY_SPECTRALDENOISE_MUSIC_MIXTURE_GAIN_VAR_DB2 = 0.1449
OUR_QUALITY_SPECTRALDENOISE_MUSIC_MIXTURE_LATENCY_SAMPLES = 320.0
OUR_QUALITY_SPECTRALDENOISE_MUSIC_MIXTURE_BLOCK_COHERENCE_DB = -73.1456
OUR_QUALITY_VOICEDENOISE_VOICE_HISS_REMOVED_DB = 17.9677
OUR_QUALITY_VOICEDENOISE_VOICE_HISS_KEPT_DB = 0.3592
OUR_QUALITY_VOICEDENOISE_VOICE_HISS_GAIN_VAR_DB2 = 0.1077
OUR_QUALITY_VOICEDENOISE_VOICE_HISS_LATENCY_SAMPLES = 1152.0
OUR_QUALITY_VOICEDENOISE_VOICE_HISS_BLOCK_COHERENCE_DB = -200.0
OUR_QUALITY_DECLICK_TRANSIENT_REMOVED_DB = 1.5862
OUR_QUALITY_DECLICK_TRANSIENT_KEPT_DB = 0.0048
OUR_QUALITY_DECLICK_TRANSIENT_GAIN_VAR_DB2 = 0.1255
OUR_QUALITY_DECLICK_TRANSIENT_LATENCY_SAMPLES = 0.0
OUR_QUALITY_DECLICK_TRANSIENT_BLOCK_COHERENCE_DB = -200.0
OUR_QUALITY_DECLICK_TRANSIENT_EVENT_REDUCTION_DB = -2.2018
OUR_QUALITY_DECLICK_TRANSIENT_PROGRAMME_SHIFT_DB = 0.6051
OUR_QUALITY_DECLIP_CLIPPED_REMOVED_DB = 19.0217
OUR_QUALITY_DECLIP_CLIPPED_KEPT_DB = 3.2502
OUR_QUALITY_DECLIP_CLIPPED_GAIN_VAR_DB2 = 1.1949
OUR_QUALITY_DECLIP_CLIPPED_LATENCY_SAMPLES = 0.0
OUR_QUALITY_DECLIP_CLIPPED_BLOCK_COHERENCE_DB = -13.2312
OUR_QUALITY_DECLIP_CLIPPED_REFERENCE_ERROR_DB = 5.3164
OUR_QUALITY_DEHUM_TONE_HUM_REMOVED_DB = 47.8004
OUR_QUALITY_DEHUM_TONE_HUM_KEPT_DB = 0.0332
OUR_QUALITY_DEHUM_TONE_HUM_GAIN_VAR_DB2 = 12.3013
OUR_QUALITY_DEHUM_TONE_HUM_LATENCY_SAMPLES = 0.0
OUR_QUALITY_DEHUM_TONE_HUM_BLOCK_COHERENCE_DB = -200.0
OUR_VOICE_VOICEDENOISE_VOICE_HISS_REMOVED_DB = 13.4098
OUR_VOICE_VOICEDENOISE_VOICE_HISS_KEPT_DB = 0.0013
OUR_VOICE_VOICEDENOISE_VOICE_HISS_CHATTER_DB = 0.3143
OUR_VOICE_VOICEDENOISE_VOICE_PINK_HISS_REMOVED_DB = 13.5892
OUR_VOICE_VOICEDENOISE_VOICE_PINK_HISS_KEPT_DB = 0.0018
OUR_VOICE_VOICEDENOISE_VOICE_PINK_HISS_CHATTER_DB = 0.3568
OUR_VOICE_BREATH_VOICE_BREATH_REMOVED_DB = 8.9936
OUR_VOICE_BREATH_VOICE_BREATH_KEPT_DB = 1.079
OUR_VOICE_BREATH_VOICE_BREATH_CHATTER_DB = 0.0142
OUR_VOICE_BREATH_VOICE_CLEAN_KEPT_DB = 0.0
OUR_VOICE_BREATH_VOICE_CLEAN_CHATTER_DB = 0.0
OUR_VOICE_VOICEDENOISE_VOICE_CLEAN_KEPT_DB = 0.0005
OUR_VOICE_VOICEDENOISE_VOICE_CLEAN_CHATTER_DB = 0.0
OUR_VOICE_MOUTHDECLICK_VOICE_CLICK_REMOVED_DB = 11.6784
OUR_VOICE_MOUTHDECLICK_VOICE_CLICK_KEPT_DB = 0.0733
OUR_VOICE_MOUTHDECLICK_VOICE_CLICK_CHATTER_DB = 0.0
OUR_VOICE_MOUTHDECLICK_VOICE_CLEAN_KEPT_DB = 0.0025
OUR_VOICE_MOUTHDECLICK_VOICE_CLEAN_CHATTER_DB = 0.0
OUR_VOICE_DEESS_VOICE_SIBILANCE_REMOVED_DB = 22.3839
OUR_VOICE_DEESS_VOICE_SIBILANCE_KEPT_DB = 0.0
OUR_VOICE_DEESS_VOICE_SIBILANCE_CHATTER_DB = 0.6497
OUR_VOICE_DIALOGUEISOLATE_VOICE_HISS_REMOVED_DB = 0.0
OUR_VOICE_DIALOGUEISOLATE_VOICE_HISS_KEPT_DB = 0.0
OUR_VOICE_DIALOGUEISOLATE_VOICE_HISS_CHATTER_DB = 0.0
REFERENCE_QUALITY_BANDRESTORATION_TONE_NOISE_REMOVED_DB = 0.0008
REFERENCE_QUALITY_BANDRESTORATION_TONE_NOISE_KEPT_DB = 0.0056
REFERENCE_QUALITY_BANDRESTORATION_TONE_NOISE_GAIN_VAR_DB2 = 0.0
REFERENCE_QUALITY_BANDRESTORATION_TONE_NOISE_BLOCK_COHERENCE_DB = -130.0935
REFERENCE_QUALITY_SPECTRALDENOISE_TONE_NOISE_REMOVED_DB = 0.0008
REFERENCE_QUALITY_SPECTRALDENOISE_TONE_NOISE_KEPT_DB = 0.0056
REFERENCE_QUALITY_SPECTRALDENOISE_TONE_NOISE_GAIN_VAR_DB2 = 0.0
REFERENCE_QUALITY_SPECTRALDENOISE_TONE_NOISE_BLOCK_COHERENCE_DB = -130.0935
REFERENCE_QUALITY_SPECTRALDENOISE_TONE_HUM_REMOVED_DB = 0.0009
REFERENCE_QUALITY_SPECTRALDENOISE_TONE_HUM_KEPT_DB = 0.0056
REFERENCE_QUALITY_SPECTRALDENOISE_TONE_HUM_GAIN_VAR_DB2 = 0.0
REFERENCE_QUALITY_SPECTRALDENOISE_TONE_HUM_BLOCK_COHERENCE_DB = -130.2713
REFERENCE_QUALITY_SPECTRALDENOISE_VOICE_HISS_REMOVED_DB = 0.0009
REFERENCE_QUALITY_SPECTRALDENOISE_VOICE_HISS_KEPT_DB = 0.0091
REFERENCE_QUALITY_SPECTRALDENOISE_VOICE_HISS_GAIN_VAR_DB2 = 0.0
REFERENCE_QUALITY_SPECTRALDENOISE_VOICE_HISS_BLOCK_COHERENCE_DB = -126.4352
REFERENCE_QUALITY_SPECTRALDENOISE_MUSIC_MIXTURE_REMOVED_DB = 0.0008
REFERENCE_QUALITY_SPECTRALDENOISE_MUSIC_MIXTURE_KEPT_DB = 0.0131
REFERENCE_QUALITY_SPECTRALDENOISE_MUSIC_MIXTURE_GAIN_VAR_DB2 = 0.0
REFERENCE_QUALITY_SPECTRALDENOISE_MUSIC_MIXTURE_BLOCK_COHERENCE_DB = -132.3461
REFERENCE_QUALITY_VOICEDENOISE_VOICE_HISS_REMOVED_DB = 0.0009
REFERENCE_QUALITY_VOICEDENOISE_VOICE_HISS_KEPT_DB = 0.0091
REFERENCE_QUALITY_VOICEDENOISE_VOICE_HISS_GAIN_VAR_DB2 = 0.0
REFERENCE_QUALITY_VOICEDENOISE_VOICE_HISS_BLOCK_COHERENCE_DB = -126.4352
REFERENCE_QUALITY_DECLICK_TRANSIENT_REMOVED_DB = 0.0008
REFERENCE_QUALITY_DECLICK_TRANSIENT_KEPT_DB = 0.0056
REFERENCE_QUALITY_DECLICK_TRANSIENT_GAIN_VAR_DB2 = 0.0
REFERENCE_QUALITY_DECLICK_TRANSIENT_BLOCK_COHERENCE_DB = -131.5173
REFERENCE_QUALITY_DECLICK_TRANSIENT_EVENT_REDUCTION_DB = 0.0004
REFERENCE_QUALITY_DECLICK_TRANSIENT_PROGRAMME_SHIFT_DB = -0.0008
REFERENCE_QUALITY_DECLIP_CLIPPED_REMOVED_DB = 0.0198
REFERENCE_QUALITY_DECLIP_CLIPPED_KEPT_DB = 3.3346
REFERENCE_QUALITY_DECLIP_CLIPPED_GAIN_VAR_DB2 = 0.1582
REFERENCE_QUALITY_DECLIP_CLIPPED_BLOCK_COHERENCE_DB = -136.3338
REFERENCE_QUALITY_DECLIP_CLIPPED_REFERENCE_ERROR_DB = -1.3197
REFERENCE_QUALITY_DEHUM_TONE_HUM_REMOVED_DB = 0.0009
REFERENCE_QUALITY_DEHUM_TONE_HUM_KEPT_DB = 0.0056
REFERENCE_QUALITY_DEHUM_TONE_HUM_GAIN_VAR_DB2 = 0.0
REFERENCE_QUALITY_DEHUM_TONE_HUM_BLOCK_COHERENCE_DB = -130.2713
REFERENCE_VOICE_VOICEDENOISE_VOICE_HISS_REMOVED_DB = 0.0003
REFERENCE_VOICE_VOICEDENOISE_VOICE_HISS_KEPT_DB = 0.0081
REFERENCE_VOICE_VOICEDENOISE_VOICE_HISS_CHATTER_DB = 0.0004
REFERENCE_VOICE_VOICEDENOISE_VOICE_PINK_HISS_REMOVED_DB = 0.0
REFERENCE_VOICE_VOICEDENOISE_VOICE_PINK_HISS_KEPT_DB = 0.0081
REFERENCE_VOICE_VOICEDENOISE_VOICE_PINK_HISS_CHATTER_DB = 0.0004
REFERENCE_VOICE_BREATH_VOICE_BREATH_REMOVED_DB = 0.0001
REFERENCE_VOICE_BREATH_VOICE_BREATH_KEPT_DB = 0.0076
REFERENCE_VOICE_BREATH_VOICE_BREATH_CHATTER_DB = 0.0005
REFERENCE_VOICE_BREATH_VOICE_CLEAN_KEPT_DB = 0.0081
REFERENCE_VOICE_BREATH_VOICE_CLEAN_CHATTER_DB = 0.004
REFERENCE_VOICE_VOICEDENOISE_VOICE_CLEAN_KEPT_DB = 0.0081
REFERENCE_VOICE_VOICEDENOISE_VOICE_CLEAN_CHATTER_DB = 0.004
REFERENCE_VOICE_MOUTHDECLICK_VOICE_CLICK_REMOVED_DB = -0.0037
REFERENCE_VOICE_MOUTHDECLICK_VOICE_CLICK_KEPT_DB = 0.0081
REFERENCE_VOICE_MOUTHDECLICK_VOICE_CLICK_CHATTER_DB = 0.0004
REFERENCE_VOICE_MOUTHDECLICK_VOICE_CLEAN_KEPT_DB = 0.0081
REFERENCE_VOICE_MOUTHDECLICK_VOICE_CLEAN_CHATTER_DB = 0.004
REFERENCE_VOICE_DEESS_VOICE_SIBILANCE_REMOVED_DB = 0.0008
REFERENCE_VOICE_DEESS_VOICE_SIBILANCE_KEPT_DB = 0.0083
REFERENCE_VOICE_DEESS_VOICE_SIBILANCE_CHATTER_DB = 0.0004
REFERENCE_VOICE_DIALOGUEISOLATE_VOICE_HISS_REMOVED_DB = 0.0003
REFERENCE_VOICE_DIALOGUEISOLATE_VOICE_HISS_KEPT_DB = 0.0081
REFERENCE_VOICE_DIALOGUEISOLATE_VOICE_HISS_CHATTER_DB = 0.0004
OUR_CORPUS_VOICEDENOISE_VOICE_HISS_HISS_BAND_REMOVED_DB = 17.8843
OUR_CORPUS_VOICEDENOISE_VOICE_HISS_VOICE_BAND_MOVED_DB = 0.1913
OUR_CORPUS_VOICEDENOISE_VOICE_HISS_BROADBAND_BAND_REMOVED_DB = 0.2512
REFERENCE_CORPUS_VOICE_HISS_HISS_BAND_REMOVED_DB = 83.3554
REFERENCE_CORPUS_VOICE_HISS_VOICE_BAND_MOVED_DB = 0.0895
REFERENCE_CORPUS_VOICE_HISS_BROADBAND_BAND_REMOVED_DB = 0.0894
OUR_CORPUS_VOICEDENOISE_HISS_BROADBAND_BAND_REMOVED_DB = 12.1864
OUR_CORPUS_VOICEDENOISE_HISS_HIGH_BAND_REMOVED_DB = 17.9039
REFERENCE_CORPUS_HISS_BROADBAND_BAND_REMOVED_DB = 0.0791
REFERENCE_CORPUS_HISS_HIGH_BAND_REMOVED_DB = 0.0782
OUR_VOICE_DIALOGUEISOLATE_VOICE_HISS_REMOVED_DB_AT_RULE_SETTING = 8.2213
OUR_VOICE_DIALOGUEISOLATE_VOICE_HISS_KEPT_DB_AT_RULE_SETTING = 0.012
OUR_VOICE_DIALOGUEISOLATE_VOICE_HISS_CHATTER_DB_AT_RULE_SETTING = 0.3801
OUR_REPRODUCIBLE = 1.0
REFERENCE_REPRODUCIBLE = 0.0
REFERENCE_REPRODUCIBILITY_SPREAD_DB = 0.11
REFERENCE_CHATTER_SPREAD_DB = 0.0003
REFERENCE_OUTPUT_DRIFT = 1e-06

#: Every number of the measurement bridge.
BRIDGES: tuple[Bridge, ...] = (
    Bridge(
        name="OUR_QUALITY_BANDRESTORATION_TONE_NOISE_REMOVED_DB",
        series="ours",
        family="quality",
        case="tone_noise",
        metric="removed_db",
        value=18.5151,
        unit="dB",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_BANDRESTORATION_TONE_NOISE_KEPT_DB",
        series="ours",
        family="quality",
        case="tone_noise",
        metric="kept_db",
        value=0.0304,
        unit="dB",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_BANDRESTORATION_TONE_NOISE_GAIN_VAR_DB2",
        series="ours",
        family="quality",
        case="tone_noise",
        metric="gain_var_db2",
        value=0.0193,
        unit="dB^2",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_BANDRESTORATION_TONE_NOISE_LATENCY_SAMPLES",
        series="ours",
        family="quality",
        case="tone_noise",
        metric="latency_samples",
        value=0.0,
        unit="samples",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_BANDRESTORATION_TONE_NOISE_BLOCK_COHERENCE_DB",
        series="ours",
        family="quality",
        case="tone_noise",
        metric="block_coherence_db",
        value=-200.0,
        unit="dB",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_SPECTRALDENOISE_TONE_NOISE_REMOVED_DB",
        series="ours",
        family="quality",
        case="tone_noise",
        metric="removed_db",
        value=14.3466,
        unit="dB",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_SPECTRALDENOISE_TONE_NOISE_KEPT_DB",
        series="ours",
        family="quality",
        case="tone_noise",
        metric="kept_db",
        value=0.0161,
        unit="dB",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_SPECTRALDENOISE_TONE_NOISE_GAIN_VAR_DB2",
        series="ours",
        family="quality",
        case="tone_noise",
        metric="gain_var_db2",
        value=0.2072,
        unit="dB^2",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_SPECTRALDENOISE_TONE_NOISE_LATENCY_SAMPLES",
        series="ours",
        family="quality",
        case="tone_noise",
        metric="latency_samples",
        value=0.0,
        unit="samples",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_SPECTRALDENOISE_TONE_NOISE_BLOCK_COHERENCE_DB",
        series="ours",
        family="quality",
        case="tone_noise",
        metric="block_coherence_db",
        value=-71.525,
        unit="dB",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_SPECTRALDENOISE_TONE_HUM_REMOVED_DB",
        series="ours",
        family="quality",
        case="tone_hum",
        metric="removed_db",
        value=12.2972,
        unit="dB",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_SPECTRALDENOISE_TONE_HUM_KEPT_DB",
        series="ours",
        family="quality",
        case="tone_hum",
        metric="kept_db",
        value=0.011,
        unit="dB",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_SPECTRALDENOISE_TONE_HUM_GAIN_VAR_DB2",
        series="ours",
        family="quality",
        case="tone_hum",
        metric="gain_var_db2",
        value=0.1893,
        unit="dB^2",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_SPECTRALDENOISE_TONE_HUM_LATENCY_SAMPLES",
        series="ours",
        family="quality",
        case="tone_hum",
        metric="latency_samples",
        value=0.0,
        unit="samples",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_SPECTRALDENOISE_TONE_HUM_BLOCK_COHERENCE_DB",
        series="ours",
        family="quality",
        case="tone_hum",
        metric="block_coherence_db",
        value=-68.3432,
        unit="dB",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_SPECTRALDENOISE_VOICE_HISS_REMOVED_DB",
        series="ours",
        family="quality",
        case="voice_hiss",
        metric="removed_db",
        value=42.5481,
        unit="dB",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_SPECTRALDENOISE_VOICE_HISS_KEPT_DB",
        series="ours",
        family="quality",
        case="voice_hiss",
        metric="kept_db",
        value=9.1645,
        unit="dB",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_SPECTRALDENOISE_VOICE_HISS_GAIN_VAR_DB2",
        series="ours",
        family="quality",
        case="voice_hiss",
        metric="gain_var_db2",
        value=0.0529,
        unit="dB^2",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_SPECTRALDENOISE_VOICE_HISS_LATENCY_SAMPLES",
        series="ours",
        family="quality",
        case="voice_hiss",
        metric="latency_samples",
        value=960.0,
        unit="samples",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_SPECTRALDENOISE_VOICE_HISS_BLOCK_COHERENCE_DB",
        series="ours",
        family="quality",
        case="voice_hiss",
        metric="block_coherence_db",
        value=-65.1982,
        unit="dB",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_SPECTRALDENOISE_MUSIC_MIXTURE_REMOVED_DB",
        series="ours",
        family="quality",
        case="music_mixture",
        metric="removed_db",
        value=12.8315,
        unit="dB",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_SPECTRALDENOISE_MUSIC_MIXTURE_KEPT_DB",
        series="ours",
        family="quality",
        case="music_mixture",
        metric="kept_db",
        value=1.683,
        unit="dB",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_SPECTRALDENOISE_MUSIC_MIXTURE_GAIN_VAR_DB2",
        series="ours",
        family="quality",
        case="music_mixture",
        metric="gain_var_db2",
        value=0.1449,
        unit="dB^2",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_SPECTRALDENOISE_MUSIC_MIXTURE_LATENCY_SAMPLES",
        series="ours",
        family="quality",
        case="music_mixture",
        metric="latency_samples",
        value=320.0,
        unit="samples",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_SPECTRALDENOISE_MUSIC_MIXTURE_BLOCK_COHERENCE_DB",
        series="ours",
        family="quality",
        case="music_mixture",
        metric="block_coherence_db",
        value=-73.1456,
        unit="dB",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_VOICEDENOISE_VOICE_HISS_REMOVED_DB",
        series="ours",
        family="quality",
        case="voice_hiss",
        metric="removed_db",
        value=17.9677,
        unit="dB",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_VOICEDENOISE_VOICE_HISS_KEPT_DB",
        series="ours",
        family="quality",
        case="voice_hiss",
        metric="kept_db",
        value=0.3592,
        unit="dB",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_VOICEDENOISE_VOICE_HISS_GAIN_VAR_DB2",
        series="ours",
        family="quality",
        case="voice_hiss",
        metric="gain_var_db2",
        value=0.1077,
        unit="dB^2",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_VOICEDENOISE_VOICE_HISS_LATENCY_SAMPLES",
        series="ours",
        family="quality",
        case="voice_hiss",
        metric="latency_samples",
        value=1152.0,
        unit="samples",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_VOICEDENOISE_VOICE_HISS_BLOCK_COHERENCE_DB",
        series="ours",
        family="quality",
        case="voice_hiss",
        metric="block_coherence_db",
        value=-200.0,
        unit="dB",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_DECLICK_TRANSIENT_REMOVED_DB",
        series="ours",
        family="quality",
        case="transient",
        metric="removed_db",
        value=1.5862,
        unit="dB",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_DECLICK_TRANSIENT_KEPT_DB",
        series="ours",
        family="quality",
        case="transient",
        metric="kept_db",
        value=0.0048,
        unit="dB",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_DECLICK_TRANSIENT_GAIN_VAR_DB2",
        series="ours",
        family="quality",
        case="transient",
        metric="gain_var_db2",
        value=0.1255,
        unit="dB^2",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_DECLICK_TRANSIENT_LATENCY_SAMPLES",
        series="ours",
        family="quality",
        case="transient",
        metric="latency_samples",
        value=0.0,
        unit="samples",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_DECLICK_TRANSIENT_BLOCK_COHERENCE_DB",
        series="ours",
        family="quality",
        case="transient",
        metric="block_coherence_db",
        value=-200.0,
        unit="dB",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_DECLICK_TRANSIENT_EVENT_REDUCTION_DB",
        series="ours",
        family="quality",
        case="transient",
        metric="event_reduction_db",
        value=-2.2018,
        unit="dB",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_DECLICK_TRANSIENT_PROGRAMME_SHIFT_DB",
        series="ours",
        family="quality",
        case="transient",
        metric="programme_shift_db",
        value=0.6051,
        unit="dB",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_DECLIP_CLIPPED_REMOVED_DB",
        series="ours",
        family="quality",
        case="clipped",
        metric="removed_db",
        value=19.0217,
        unit="dB",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_DECLIP_CLIPPED_KEPT_DB",
        series="ours",
        family="quality",
        case="clipped",
        metric="kept_db",
        value=3.2502,
        unit="dB",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_DECLIP_CLIPPED_GAIN_VAR_DB2",
        series="ours",
        family="quality",
        case="clipped",
        metric="gain_var_db2",
        value=1.1949,
        unit="dB^2",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_DECLIP_CLIPPED_LATENCY_SAMPLES",
        series="ours",
        family="quality",
        case="clipped",
        metric="latency_samples",
        value=0.0,
        unit="samples",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_DECLIP_CLIPPED_BLOCK_COHERENCE_DB",
        series="ours",
        family="quality",
        case="clipped",
        metric="block_coherence_db",
        value=-13.2312,
        unit="dB",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_DECLIP_CLIPPED_REFERENCE_ERROR_DB",
        series="ours",
        family="quality",
        case="clipped",
        metric="reference_error_db",
        value=5.3164,
        unit="dB",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_DEHUM_TONE_HUM_REMOVED_DB",
        series="ours",
        family="quality",
        case="tone_hum",
        metric="removed_db",
        value=47.8004,
        unit="dB",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_DEHUM_TONE_HUM_KEPT_DB",
        series="ours",
        family="quality",
        case="tone_hum",
        metric="kept_db",
        value=0.0332,
        unit="dB",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_DEHUM_TONE_HUM_GAIN_VAR_DB2",
        series="ours",
        family="quality",
        case="tone_hum",
        metric="gain_var_db2",
        value=12.3013,
        unit="dB^2",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_DEHUM_TONE_HUM_LATENCY_SAMPLES",
        series="ours",
        family="quality",
        case="tone_hum",
        metric="latency_samples",
        value=0.0,
        unit="samples",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_QUALITY_DEHUM_TONE_HUM_BLOCK_COHERENCE_DB",
        series="ours",
        family="quality",
        case="tone_hum",
        metric="block_coherence_db",
        value=-200.0,
        unit="dB",
        method=_OURS_QUALITY_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_VOICE_VOICEDENOISE_VOICE_HISS_REMOVED_DB",
        series="ours",
        family="voice",
        case="voice_hiss",
        metric="removed_db",
        value=13.4098,
        unit="dB",
        method=_OURS_VOICE_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_VOICE_VOICEDENOISE_VOICE_HISS_KEPT_DB",
        series="ours",
        family="voice",
        case="voice_hiss",
        metric="kept_db",
        value=0.0013,
        unit="dB",
        method=_OURS_VOICE_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_VOICE_VOICEDENOISE_VOICE_HISS_CHATTER_DB",
        series="ours",
        family="voice",
        case="voice_hiss",
        metric="chatter_db",
        value=0.3143,
        unit="dB",
        method=_OURS_VOICE_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_VOICE_VOICEDENOISE_VOICE_PINK_HISS_REMOVED_DB",
        series="ours",
        family="voice",
        case="voice_pink_hiss",
        metric="removed_db",
        value=13.5892,
        unit="dB",
        method=_OURS_VOICE_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_VOICE_VOICEDENOISE_VOICE_PINK_HISS_KEPT_DB",
        series="ours",
        family="voice",
        case="voice_pink_hiss",
        metric="kept_db",
        value=0.0018,
        unit="dB",
        method=_OURS_VOICE_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_VOICE_VOICEDENOISE_VOICE_PINK_HISS_CHATTER_DB",
        series="ours",
        family="voice",
        case="voice_pink_hiss",
        metric="chatter_db",
        value=0.3568,
        unit="dB",
        method=_OURS_VOICE_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_VOICE_BREATH_VOICE_BREATH_REMOVED_DB",
        series="ours",
        family="voice",
        case="voice_breath",
        metric="removed_db",
        value=8.9936,
        unit="dB",
        method=_OURS_VOICE_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_VOICE_BREATH_VOICE_BREATH_KEPT_DB",
        series="ours",
        family="voice",
        case="voice_breath",
        metric="kept_db",
        value=1.079,
        unit="dB",
        method=_OURS_VOICE_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_VOICE_BREATH_VOICE_BREATH_CHATTER_DB",
        series="ours",
        family="voice",
        case="voice_breath",
        metric="chatter_db",
        value=0.0142,
        unit="dB",
        method=_OURS_VOICE_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_VOICE_BREATH_VOICE_CLEAN_KEPT_DB",
        series="ours",
        family="voice",
        case="voice_clean",
        metric="kept_db",
        value=0.0,
        unit="dB",
        method=_OURS_VOICE_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_VOICE_BREATH_VOICE_CLEAN_CHATTER_DB",
        series="ours",
        family="voice",
        case="voice_clean",
        metric="chatter_db",
        value=0.0,
        unit="dB",
        method=_OURS_VOICE_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_VOICE_VOICEDENOISE_VOICE_CLEAN_KEPT_DB",
        series="ours",
        family="voice",
        case="voice_clean",
        metric="kept_db",
        value=0.0005,
        unit="dB",
        method=_OURS_VOICE_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_VOICE_VOICEDENOISE_VOICE_CLEAN_CHATTER_DB",
        series="ours",
        family="voice",
        case="voice_clean",
        metric="chatter_db",
        value=0.0,
        unit="dB",
        method=_OURS_VOICE_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_VOICE_MOUTHDECLICK_VOICE_CLICK_REMOVED_DB",
        series="ours",
        family="voice",
        case="voice_click",
        metric="removed_db",
        value=11.6784,
        unit="dB",
        method=_OURS_VOICE_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_VOICE_MOUTHDECLICK_VOICE_CLICK_KEPT_DB",
        series="ours",
        family="voice",
        case="voice_click",
        metric="kept_db",
        value=0.0733,
        unit="dB",
        method=_OURS_VOICE_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_VOICE_MOUTHDECLICK_VOICE_CLICK_CHATTER_DB",
        series="ours",
        family="voice",
        case="voice_click",
        metric="chatter_db",
        value=0.0,
        unit="dB",
        method=_OURS_VOICE_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_VOICE_MOUTHDECLICK_VOICE_CLEAN_KEPT_DB",
        series="ours",
        family="voice",
        case="voice_clean",
        metric="kept_db",
        value=0.0025,
        unit="dB",
        method=_OURS_VOICE_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_VOICE_MOUTHDECLICK_VOICE_CLEAN_CHATTER_DB",
        series="ours",
        family="voice",
        case="voice_clean",
        metric="chatter_db",
        value=0.0,
        unit="dB",
        method=_OURS_VOICE_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_VOICE_DEESS_VOICE_SIBILANCE_REMOVED_DB",
        series="ours",
        family="voice",
        case="voice_sibilance",
        metric="removed_db",
        value=22.3839,
        unit="dB",
        method=_OURS_VOICE_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_VOICE_DEESS_VOICE_SIBILANCE_KEPT_DB",
        series="ours",
        family="voice",
        case="voice_sibilance",
        metric="kept_db",
        value=0.0,
        unit="dB",
        method=_OURS_VOICE_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_VOICE_DEESS_VOICE_SIBILANCE_CHATTER_DB",
        series="ours",
        family="voice",
        case="voice_sibilance",
        metric="chatter_db",
        value=0.6497,
        unit="dB",
        method=_OURS_VOICE_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_VOICE_DIALOGUEISOLATE_VOICE_HISS_REMOVED_DB",
        series="ours",
        family="voice",
        case="voice_hiss",
        metric="removed_db",
        value=0.0,
        unit="dB",
        method=_OURS_VOICE_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_VOICE_DIALOGUEISOLATE_VOICE_HISS_KEPT_DB",
        series="ours",
        family="voice",
        case="voice_hiss",
        metric="kept_db",
        value=0.0,
        unit="dB",
        method=_OURS_VOICE_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_VOICE_DIALOGUEISOLATE_VOICE_HISS_CHATTER_DB",
        series="ours",
        family="voice",
        case="voice_hiss",
        metric="chatter_db",
        value=0.0,
        unit="dB",
        method=_OURS_VOICE_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_BANDRESTORATION_TONE_NOISE_REMOVED_DB",
        series="stem_separation",
        family="quality",
        case="tone_noise",
        metric="removed_db",
        value=0.0008,
        unit="dB",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_BANDRESTORATION_TONE_NOISE_KEPT_DB",
        series="stem_separation",
        family="quality",
        case="tone_noise",
        metric="kept_db",
        value=0.0056,
        unit="dB",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_BANDRESTORATION_TONE_NOISE_GAIN_VAR_DB2",
        series="stem_separation",
        family="quality",
        case="tone_noise",
        metric="gain_var_db2",
        value=0.0,
        unit="dB^2",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_BANDRESTORATION_TONE_NOISE_BLOCK_COHERENCE_DB",
        series="stem_separation",
        family="quality",
        case="tone_noise",
        metric="block_coherence_db",
        value=-130.0935,
        unit="dB",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_SPECTRALDENOISE_TONE_NOISE_REMOVED_DB",
        series="stem_separation",
        family="quality",
        case="tone_noise",
        metric="removed_db",
        value=0.0008,
        unit="dB",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_SPECTRALDENOISE_TONE_NOISE_KEPT_DB",
        series="stem_separation",
        family="quality",
        case="tone_noise",
        metric="kept_db",
        value=0.0056,
        unit="dB",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_SPECTRALDENOISE_TONE_NOISE_GAIN_VAR_DB2",
        series="stem_separation",
        family="quality",
        case="tone_noise",
        metric="gain_var_db2",
        value=0.0,
        unit="dB^2",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_SPECTRALDENOISE_TONE_NOISE_BLOCK_COHERENCE_DB",
        series="stem_separation",
        family="quality",
        case="tone_noise",
        metric="block_coherence_db",
        value=-130.0935,
        unit="dB",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_SPECTRALDENOISE_TONE_HUM_REMOVED_DB",
        series="stem_separation",
        family="quality",
        case="tone_hum",
        metric="removed_db",
        value=0.0009,
        unit="dB",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_SPECTRALDENOISE_TONE_HUM_KEPT_DB",
        series="stem_separation",
        family="quality",
        case="tone_hum",
        metric="kept_db",
        value=0.0056,
        unit="dB",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_SPECTRALDENOISE_TONE_HUM_GAIN_VAR_DB2",
        series="stem_separation",
        family="quality",
        case="tone_hum",
        metric="gain_var_db2",
        value=0.0,
        unit="dB^2",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_SPECTRALDENOISE_TONE_HUM_BLOCK_COHERENCE_DB",
        series="stem_separation",
        family="quality",
        case="tone_hum",
        metric="block_coherence_db",
        value=-130.2713,
        unit="dB",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_SPECTRALDENOISE_VOICE_HISS_REMOVED_DB",
        series="stem_separation",
        family="quality",
        case="voice_hiss",
        metric="removed_db",
        value=0.0009,
        unit="dB",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_SPECTRALDENOISE_VOICE_HISS_KEPT_DB",
        series="stem_separation",
        family="quality",
        case="voice_hiss",
        metric="kept_db",
        value=0.0091,
        unit="dB",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_SPECTRALDENOISE_VOICE_HISS_GAIN_VAR_DB2",
        series="stem_separation",
        family="quality",
        case="voice_hiss",
        metric="gain_var_db2",
        value=0.0,
        unit="dB^2",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_SPECTRALDENOISE_VOICE_HISS_BLOCK_COHERENCE_DB",
        series="stem_separation",
        family="quality",
        case="voice_hiss",
        metric="block_coherence_db",
        value=-126.4352,
        unit="dB",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_SPECTRALDENOISE_MUSIC_MIXTURE_REMOVED_DB",
        series="stem_separation",
        family="quality",
        case="music_mixture",
        metric="removed_db",
        value=0.0008,
        unit="dB",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_SPECTRALDENOISE_MUSIC_MIXTURE_KEPT_DB",
        series="stem_separation",
        family="quality",
        case="music_mixture",
        metric="kept_db",
        value=0.0131,
        unit="dB",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_SPECTRALDENOISE_MUSIC_MIXTURE_GAIN_VAR_DB2",
        series="stem_separation",
        family="quality",
        case="music_mixture",
        metric="gain_var_db2",
        value=0.0,
        unit="dB^2",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_SPECTRALDENOISE_MUSIC_MIXTURE_BLOCK_COHERENCE_DB",
        series="stem_separation",
        family="quality",
        case="music_mixture",
        metric="block_coherence_db",
        value=-132.3461,
        unit="dB",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_VOICEDENOISE_VOICE_HISS_REMOVED_DB",
        series="stem_separation",
        family="quality",
        case="voice_hiss",
        metric="removed_db",
        value=0.0009,
        unit="dB",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_VOICEDENOISE_VOICE_HISS_KEPT_DB",
        series="stem_separation",
        family="quality",
        case="voice_hiss",
        metric="kept_db",
        value=0.0091,
        unit="dB",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_VOICEDENOISE_VOICE_HISS_GAIN_VAR_DB2",
        series="stem_separation",
        family="quality",
        case="voice_hiss",
        metric="gain_var_db2",
        value=0.0,
        unit="dB^2",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_VOICEDENOISE_VOICE_HISS_BLOCK_COHERENCE_DB",
        series="stem_separation",
        family="quality",
        case="voice_hiss",
        metric="block_coherence_db",
        value=-126.4352,
        unit="dB",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_DECLICK_TRANSIENT_REMOVED_DB",
        series="stem_separation",
        family="quality",
        case="transient",
        metric="removed_db",
        value=0.0008,
        unit="dB",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_DECLICK_TRANSIENT_KEPT_DB",
        series="stem_separation",
        family="quality",
        case="transient",
        metric="kept_db",
        value=0.0056,
        unit="dB",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_DECLICK_TRANSIENT_GAIN_VAR_DB2",
        series="stem_separation",
        family="quality",
        case="transient",
        metric="gain_var_db2",
        value=0.0,
        unit="dB^2",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_DECLICK_TRANSIENT_BLOCK_COHERENCE_DB",
        series="stem_separation",
        family="quality",
        case="transient",
        metric="block_coherence_db",
        value=-131.5173,
        unit="dB",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_DECLICK_TRANSIENT_EVENT_REDUCTION_DB",
        series="stem_separation",
        family="quality",
        case="transient",
        metric="event_reduction_db",
        value=0.0004,
        unit="dB",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_DECLICK_TRANSIENT_PROGRAMME_SHIFT_DB",
        series="stem_separation",
        family="quality",
        case="transient",
        metric="programme_shift_db",
        value=-0.0008,
        unit="dB",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_DECLIP_CLIPPED_REMOVED_DB",
        series="stem_separation",
        family="quality",
        case="clipped",
        metric="removed_db",
        value=0.0198,
        unit="dB",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_DECLIP_CLIPPED_KEPT_DB",
        series="stem_separation",
        family="quality",
        case="clipped",
        metric="kept_db",
        value=3.3346,
        unit="dB",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_DECLIP_CLIPPED_GAIN_VAR_DB2",
        series="stem_separation",
        family="quality",
        case="clipped",
        metric="gain_var_db2",
        value=0.1582,
        unit="dB^2",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_DECLIP_CLIPPED_BLOCK_COHERENCE_DB",
        series="stem_separation",
        family="quality",
        case="clipped",
        metric="block_coherence_db",
        value=-136.3338,
        unit="dB",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_DECLIP_CLIPPED_REFERENCE_ERROR_DB",
        series="stem_separation",
        family="quality",
        case="clipped",
        metric="reference_error_db",
        value=-1.3197,
        unit="dB",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_DEHUM_TONE_HUM_REMOVED_DB",
        series="stem_separation",
        family="quality",
        case="tone_hum",
        metric="removed_db",
        value=0.0009,
        unit="dB",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_DEHUM_TONE_HUM_KEPT_DB",
        series="stem_separation",
        family="quality",
        case="tone_hum",
        metric="kept_db",
        value=0.0056,
        unit="dB",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_DEHUM_TONE_HUM_GAIN_VAR_DB2",
        series="stem_separation",
        family="quality",
        case="tone_hum",
        metric="gain_var_db2",
        value=0.0,
        unit="dB^2",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_QUALITY_DEHUM_TONE_HUM_BLOCK_COHERENCE_DB",
        series="stem_separation",
        family="quality",
        case="tone_hum",
        metric="block_coherence_db",
        value=-130.2713,
        unit="dB",
        method=_QUALITY_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_VOICE_VOICEDENOISE_VOICE_HISS_REMOVED_DB",
        series="stem_separation",
        family="voice",
        case="voice_hiss",
        metric="removed_db",
        value=0.0003,
        unit="dB",
        method=_VOICE_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_VOICE_VOICEDENOISE_VOICE_HISS_KEPT_DB",
        series="stem_separation",
        family="voice",
        case="voice_hiss",
        metric="kept_db",
        value=0.0081,
        unit="dB",
        method=_VOICE_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_VOICE_VOICEDENOISE_VOICE_HISS_CHATTER_DB",
        series="stem_separation",
        family="voice",
        case="voice_hiss",
        metric="chatter_db",
        value=0.0004,
        unit="dB",
        method=_VOICE_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_VOICE_VOICEDENOISE_VOICE_PINK_HISS_REMOVED_DB",
        series="stem_separation",
        family="voice",
        case="voice_pink_hiss",
        metric="removed_db",
        value=0.0,
        unit="dB",
        method=_VOICE_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_VOICE_VOICEDENOISE_VOICE_PINK_HISS_KEPT_DB",
        series="stem_separation",
        family="voice",
        case="voice_pink_hiss",
        metric="kept_db",
        value=0.0081,
        unit="dB",
        method=_VOICE_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_VOICE_VOICEDENOISE_VOICE_PINK_HISS_CHATTER_DB",
        series="stem_separation",
        family="voice",
        case="voice_pink_hiss",
        metric="chatter_db",
        value=0.0004,
        unit="dB",
        method=_VOICE_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_VOICE_BREATH_VOICE_BREATH_REMOVED_DB",
        series="stem_separation",
        family="voice",
        case="voice_breath",
        metric="removed_db",
        value=0.0001,
        unit="dB",
        method=_VOICE_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_VOICE_BREATH_VOICE_BREATH_KEPT_DB",
        series="stem_separation",
        family="voice",
        case="voice_breath",
        metric="kept_db",
        value=0.0076,
        unit="dB",
        method=_VOICE_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_VOICE_BREATH_VOICE_BREATH_CHATTER_DB",
        series="stem_separation",
        family="voice",
        case="voice_breath",
        metric="chatter_db",
        value=0.0005,
        unit="dB",
        method=_VOICE_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_VOICE_BREATH_VOICE_CLEAN_KEPT_DB",
        series="stem_separation",
        family="voice",
        case="voice_clean",
        metric="kept_db",
        value=0.0081,
        unit="dB",
        method=_VOICE_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_VOICE_BREATH_VOICE_CLEAN_CHATTER_DB",
        series="stem_separation",
        family="voice",
        case="voice_clean",
        metric="chatter_db",
        value=0.004,
        unit="dB",
        method=_VOICE_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_VOICE_VOICEDENOISE_VOICE_CLEAN_KEPT_DB",
        series="stem_separation",
        family="voice",
        case="voice_clean",
        metric="kept_db",
        value=0.0081,
        unit="dB",
        method=_VOICE_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_VOICE_VOICEDENOISE_VOICE_CLEAN_CHATTER_DB",
        series="stem_separation",
        family="voice",
        case="voice_clean",
        metric="chatter_db",
        value=0.004,
        unit="dB",
        method=_VOICE_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_VOICE_MOUTHDECLICK_VOICE_CLICK_REMOVED_DB",
        series="stem_separation",
        family="voice",
        case="voice_click",
        metric="removed_db",
        value=-0.0037,
        unit="dB",
        method=_VOICE_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_VOICE_MOUTHDECLICK_VOICE_CLICK_KEPT_DB",
        series="stem_separation",
        family="voice",
        case="voice_click",
        metric="kept_db",
        value=0.0081,
        unit="dB",
        method=_VOICE_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_VOICE_MOUTHDECLICK_VOICE_CLICK_CHATTER_DB",
        series="stem_separation",
        family="voice",
        case="voice_click",
        metric="chatter_db",
        value=0.0004,
        unit="dB",
        method=_VOICE_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_VOICE_MOUTHDECLICK_VOICE_CLEAN_KEPT_DB",
        series="stem_separation",
        family="voice",
        case="voice_clean",
        metric="kept_db",
        value=0.0081,
        unit="dB",
        method=_VOICE_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_VOICE_MOUTHDECLICK_VOICE_CLEAN_CHATTER_DB",
        series="stem_separation",
        family="voice",
        case="voice_clean",
        metric="chatter_db",
        value=0.004,
        unit="dB",
        method=_VOICE_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_VOICE_DEESS_VOICE_SIBILANCE_REMOVED_DB",
        series="stem_separation",
        family="voice",
        case="voice_sibilance",
        metric="removed_db",
        value=0.0008,
        unit="dB",
        method=_VOICE_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_VOICE_DEESS_VOICE_SIBILANCE_KEPT_DB",
        series="stem_separation",
        family="voice",
        case="voice_sibilance",
        metric="kept_db",
        value=0.0083,
        unit="dB",
        method=_VOICE_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_VOICE_DEESS_VOICE_SIBILANCE_CHATTER_DB",
        series="stem_separation",
        family="voice",
        case="voice_sibilance",
        metric="chatter_db",
        value=0.0004,
        unit="dB",
        method=_VOICE_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_VOICE_DIALOGUEISOLATE_VOICE_HISS_REMOVED_DB",
        series="stem_separation",
        family="voice",
        case="voice_hiss",
        metric="removed_db",
        value=0.0003,
        unit="dB",
        method=_VOICE_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_VOICE_DIALOGUEISOLATE_VOICE_HISS_KEPT_DB",
        series="stem_separation",
        family="voice",
        case="voice_hiss",
        metric="kept_db",
        value=0.0081,
        unit="dB",
        method=_VOICE_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_VOICE_DIALOGUEISOLATE_VOICE_HISS_CHATTER_DB",
        series="stem_separation",
        family="voice",
        case="voice_hiss",
        metric="chatter_db",
        value=0.0004,
        unit="dB",
        method=_VOICE_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="OUR_CORPUS_VOICEDENOISE_VOICE_HISS_HISS_BAND_REMOVED_DB",
        series="ours",
        family="corpus",
        case="voice_hiss",
        metric="band_removed_db",
        value=17.8843,
        unit="dB",
        method=_CORPUS_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_CORPUS_VOICEDENOISE_VOICE_HISS_VOICE_BAND_MOVED_DB",
        series="ours",
        family="corpus",
        case="voice_hiss",
        metric="band_moved_db",
        value=0.1913,
        unit="dB",
        method=_CORPUS_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_CORPUS_VOICEDENOISE_VOICE_HISS_BROADBAND_BAND_REMOVED_DB",
        series="ours",
        family="corpus",
        case="voice_hiss",
        metric="band_removed_db",
        value=0.2512,
        unit="dB",
        method=_CORPUS_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="REFERENCE_CORPUS_VOICE_HISS_HISS_BAND_REMOVED_DB",
        series="stem_separation",
        family="corpus",
        case="voice_hiss",
        metric="band_removed_db",
        value=83.3554,
        unit="dB",
        method=_CORPUS_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_CORPUS_VOICE_HISS_VOICE_BAND_MOVED_DB",
        series="stem_separation",
        family="corpus",
        case="voice_hiss",
        metric="band_moved_db",
        value=0.0895,
        unit="dB",
        method=_CORPUS_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_CORPUS_VOICE_HISS_BROADBAND_BAND_REMOVED_DB",
        series="stem_separation",
        family="corpus",
        case="voice_hiss",
        metric="band_removed_db",
        value=0.0894,
        unit="dB",
        method=_CORPUS_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="OUR_CORPUS_VOICEDENOISE_HISS_BROADBAND_BAND_REMOVED_DB",
        series="ours",
        family="corpus",
        case="hiss",
        metric="band_removed_db",
        value=12.1864,
        unit="dB",
        method=_CORPUS_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="OUR_CORPUS_VOICEDENOISE_HISS_HIGH_BAND_REMOVED_DB",
        series="ours",
        family="corpus",
        case="hiss",
        metric="band_removed_db",
        value=17.9039,
        unit="dB",
        method=_CORPUS_METHOD,
        setting=_OURS_SETTING,
    ),
    Bridge(
        name="REFERENCE_CORPUS_HISS_BROADBAND_BAND_REMOVED_DB",
        series="stem_separation",
        family="corpus",
        case="hiss",
        metric="band_removed_db",
        value=0.0791,
        unit="dB",
        method=_CORPUS_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="REFERENCE_CORPUS_HISS_HIGH_BAND_REMOVED_DB",
        series="stem_separation",
        family="corpus",
        case="hiss",
        metric="band_removed_db",
        value=0.0782,
        unit="dB",
        method=_CORPUS_METHOD,
        setting=_REFERENCE_SETTING,
    ),
    Bridge(
        name="OUR_VOICE_DIALOGUEISOLATE_VOICE_HISS_REMOVED_DB_AT_RULE_SETTING",
        series="ours",
        family="voice",
        case="voice_hiss",
        metric="removed_db",
        value=8.2213,
        unit="dB",
        method=_OURS_VOICE_METHOD,
        setting='our own module with the noise gain of its own rule at -12 dB',
    ),
    Bridge(
        name="OUR_VOICE_DIALOGUEISOLATE_VOICE_HISS_KEPT_DB_AT_RULE_SETTING",
        series="ours",
        family="voice",
        case="voice_hiss",
        metric="kept_db",
        value=0.012,
        unit="dB",
        method=_OURS_VOICE_METHOD,
        setting='our own module with the noise gain of its own rule at -12 dB',
    ),
    Bridge(
        name="OUR_VOICE_DIALOGUEISOLATE_VOICE_HISS_CHATTER_DB_AT_RULE_SETTING",
        series="ours",
        family="voice",
        case="voice_hiss",
        metric="chatter_db",
        value=0.3801,
        unit="dB",
        method=_OURS_VOICE_METHOD,
        setting='our own module with the noise gain of its own rule at -12 dB',
    ),
    Bridge(
        name="OUR_REPRODUCIBLE",
        series="ours",
        family="table",
        case="*",
        metric="reproducible",
        value=1.0,
        unit="flag",
        method=_DETERMINISM_OURS_METHOD,
        setting='every case of the table',
    ),
    Bridge(
        name="REFERENCE_REPRODUCIBLE",
        series="stem_separation",
        family="table",
        case="*",
        metric="reproducible",
        value=0.0,
        unit="flag",
        method=_DETERMINISM_REFERENCE_METHOD,
        setting='every case of the table',
    ),
    Bridge(
        name="REFERENCE_REPRODUCIBILITY_SPREAD_DB",
        series="stem_separation",
        family="table",
        case="*",
        metric="reproducibility_spread_db",
        value=0.11,
        unit="dB",
        method=_DETERMINISM_SPREAD_METHOD,
        setting='every case of the table',
    ),
    Bridge(
        name="REFERENCE_CHATTER_SPREAD_DB",
        series="stem_separation",
        family="table",
        case="*",
        metric="reproducibility_spread_db",
        value=0.0003,
        unit="dB",
        method=_DETERMINISM_CHATTER_METHOD,
        setting='every case of the table',
    ),
    Bridge(
        name="REFERENCE_OUTPUT_DRIFT",
        series="stem_separation",
        family="table",
        case="*",
        metric="output_drift",
        value=1e-06,
        unit="relative",
        method=_DETERMINISM_DRIFT_METHOD,
        setting='every case of the table',
    ),
)

#: The numbers of the measurement bridge, keyed by the name of their constant.
BRIDGE_BY_NAME: dict[str, Bridge] = {bridge.name: bridge for bridge in BRIDGES}


def bridges_for(
    case: str,
    metric: str,
    family: str | None = None,
) -> tuple[Bridge, ...]:
    """Every number of a case and a metric, one per series.

    A number recorded under the name ``"*"`` belongs to every case: the
    determinism of a product is a property of the product, not of the
    material.
    """
    return tuple(
        bridge
        for bridge in BRIDGES
        if bridge.metric == metric
        and bridge.case in (case, EVERY_CASE)
        and (family is None or bridge.family == family)
    )


def bridge_for(
    case: str,
    metric: str,
    series: str | None = None,
    family: str | None = None,
) -> float | None:
    """The number a case and a metric are held to.

    Without a series the **strongest** number is the reference: the gap that
    is worth closing is the one against the best of them.  With a series the
    number of that series is the reference.
    """
    found = [
        bridge
        for bridge in bridges_for(case, metric, family)
        if series is None or bridge.series == series
    ]
    if not found:
        return None
    rule = METRIC_BY_NAME[metric]
    if rule.higher_is_better:
        return max(float(bridge.value) for bridge in found)
    return min(float(bridge.value) for bridge in found)
