"""
qa.benchmark.reference_voice — the numbers of the voice head to head.

This file holds the numbers of the comparison between **our** voice de-noise
and a commercial voice cleaner, measured on the three voice cases of the
project: the voice clip of the corpus, the synthetic voice and the speech
shaped signal.

The shape is the shape of the other constants files of this directory
(``qa/benchmark/reference.py`` and ``qa/benchmark/reference_restoration.py``):
a number is a number, it carries no name, no code and no parameter of the
product it was measured on, and what travels with it is the **case** it
belongs to, the **unit** it is in and the **way it was measured**.  A number
without those three cannot be read, cannot be argued about and cannot be
replaced when a better measurement arrives.

The two sides of the comparison are described by their shape and never by
their names:

``stem_separation``
    the reference that splits a recording into three stems (a voice stem, an
    ambience stem and a voice reverb stem) and mixes them with faders: the
    hiss is removed by muting a stem, not by a gate;
``ours``
    not a reference: the measurements of our own module, filed under their own
    series so that they are never mistaken for one.

**The tree the numbers describe.**  The tree was being worked on while the
study ran, so the numbers of ours are tied to the tree they were measured on:
the sha256 of the module that carries them is recorded below, read before and
after the measurement.  The module was rewritten five times after the study
measured it, so the numbers of ours were taken again on each of the six trees
with the instrument of the project, and they came out the same every time.
Then the module was rewritten again -- its decision and its noise reference
moved into the **ratio domain**, so the gain it applies does not follow the
level of the input any more -- and the numbers moved with it: the rewrite is
worth **+8.9 dB of hiss removed** on the voice clip of the corpus.  The
numbers below were therefore taken again, with the instrument of the project,
on the tree the hash below names.  The tree keeps moving, so the hash is the
tree of the **last** measurement; the gate measures the module live, and it
is the gate that says whether the numbers still hold.

**The constants are a record of the module as it is now.**  They are not a
promise and not a target: they say what the module does on the tree named
below, measured with the instrument named below.  When the module changes,
they are measured again -- the numbers of ours and only the numbers of ours;
the numbers of the reference are the study's measurements of a foreign
product and they do not depend on our tree.

**A reference is a target, not a contract.**  It was measured on somebody
else's build, on somebody else's machine, with somebody else's settings, so
the benchmark reports the gap and does not fail on it.  The gate that holds
*our* side to the numbers is ``tests/test_benchmark_voice.py``.

The design note that reads these numbers, says what they mean and records
what could not be measured is ``docs/VOICE_HEAD_TO_HEAD.md``.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "CASES",
    "EVERY_CASE",
    "Metric",
    "METRICS",
    "METRIC_BY_NAME",
    "Voice",
    "VOICES",
    "VOICE_BY_NAME",
    "SERIES",
    "voices_for",
    "voice_for",
]


@dataclass(frozen=True)
class Metric:
    """One number of the table and what a good number of it looks like."""

    name: str
    unit: str
    higher_is_better: bool
    note: str = ""


#: Every metric the voice comparison measured.
METRICS: tuple[Metric, ...] = (
    Metric(
        name="removed_db",
        unit="dB",
        higher_is_better=True,
        note="How much of the hiss is taken away.",
    ),
    Metric(
        name="kept_db",
        unit="dB",
        higher_is_better=False,
        note="How far the wanted voice moves.",
    ),
    Metric(
        name="chatter_db",
        unit="dB",
        higher_is_better=False,
        note=(
            "How much the gain curve breathes while the voice speaks (our own "
            "budget is 1.0 dB)."
        ),
    ),
    Metric(
        name="block_coherence_db",
        unit="dB",
        higher_is_better=False,
        note=(
            "How much the output changes when the host chops the stream into "
            "blocks (our own budget is -60 dB)."
        ),
    ),
    Metric(
        name="delay_samples",
        unit="samples",
        higher_is_better=False,
        note="How many samples the output lags the input.",
    ),
    Metric(
        name="side_channel_rms",
        unit="rms",
        higher_is_better=False,
        note=(
            "How much of the side channel of a stereo signal survives: the "
            "difference of the two channels."
        ),
    ),
)

#: The metrics, keyed by name.
METRIC_BY_NAME: dict[str, Metric] = {metric.name: metric for metric in METRICS}

#: The series the numbers are filed under, keyed by their name.
SERIES: dict[str, str] = {
    "stem_separation": (
        "the reference that splits a recording into three stems (voice, "
        "ambience, voice reverb) and mixes them with faders; the hiss is "
        "removed by muting a stem, not by a gate"
    ),
    "ours": (
        "not a reference: the measurements of our own voice module, filed "
        "under their own series so that they are never mistaken for one"
    ),
}

#: The case that means "every case": a number that is a property of the
#: product and not of the material it is measured on.
EVERY_CASE = "*"

#: The cases the numbers belong to, and what each of them is.
CASES: dict[str, str] = {
    "corpus_voice": (
        "the voice clip of the corpus, read in the mid channel: the manifest "
        "puts the voice in the mid and the hiss in the side"
    ),
    "synthetic_voice": (
        "the synthetic voice of the voice instrument under a white bed 45 dB "
        "down"
    ),
    "speech_shaped": (
        "the speech shaped signal of the quality report: a harmonic voice "
        "like stack under hiss"
    ),
    EVERY_CASE: (
        "every case: the number is a property of the product and not of the "
        "material it is measured on"
    ),
}


@dataclass(frozen=True)
class Voice:
    """One number of the voice comparison, with everything needed to read it."""

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
# the tree the numbers of ours describe
# ---------------------------------------------------------------------------
#: The sha256 of the module that carries our side, read before and after the
#: measurement of the numbers below: the measurement belongs to that file.
#: The tree is being worked on while this note is written, so this is the tree
#: of the **last** measurement; the gate (``tests/test_benchmark_voice.py``)
#: measures the module live, so it is the gate and not this hash that says
#: whether the numbers still hold.
TREE_SHA256_VOICE_DE_NOISE = (
    "9d1e841ad6c1b2e1a56ecfd22e41ae67092ce27c639e52dc775515745d87dd91"
)

#: The sha256 of the parameter descriptors of the core
#: (``aurcore/params.py``) of the same tree.
TREE_SHA256_PARAMS = (
    "c411dc2b4b0772b2c9e6fb73440fee4a8ee6858f7a4dc08c6230279147dc02ec"
)

#: The sha256 of the canonical parameter table of the same tree
#: (``aurcore/schema/params.tsv``), read at the moment of the measurement.
#: The table of the product is being worked on as well, so this hash is the
#: one of the moment of the measurement and not a promise about the tree.
TREE_SHA256_PARAMS_TABLE = (
    "088833e9a2cd0673584478488601e9ad7363a40017d11f71782f0331c6ac6bb1"
)

#: When the numbers of ours were measured on that tree.
TREE_MEASURED_AT = "2026-10-21 23:34:25"

#: The sha256 of the module when the study measured it.  The module was
#: rewritten after that, which is why the numbers were taken again.
STUDY_TREE_SHA256_VOICE_DE_NOISE = (
    "d0bfa2149810fed30ec0a8339f975151e30f241c081544d8b240960c1dbe38f6"
)

#: The sha256 of the parameter table when the study measured it.
STUDY_TREE_SHA256_PARAMS = (
    "ad13235b787946093da092794f063bdbfa23f7ebef993bb452d4522bb8f589c2"
)

#: The sha256 of every revision of the module the numbers of ours were
#: measured on, oldest first: the tree of the study, the five that followed it
#: while the first note was written, and the two that the rewrite of the
#: module (the ratio domain) and its tail declaration came with.  The numbers
#: came out the same on every one of the first six; the rewrite moved them,
#: and the numbers of this file are the ones of the last revision.
TREE_SHA256_VOICE_DE_NOISE_REVISIONS = (
    "d0bfa2149810fed30ec0a8339f975151e30f241c081544d8b240960c1dbe38f6",
    "f2ca310374423e60da3e856a25e97f0163f32816a59023566be2d68b03a7ea29",
    "b2346e8cbfc2271082cb8c7eeadad1f2b310d77c01870e3cab411ec3a8356895",
    "2d48dbee1c95bc86fd7a213c7afbbb7070b5c27fada95dea9e53ae086d2bff95",
    "aca38b17c385a309c6ea1aa8a7eccc6743158566b08ea789fe73bed1d1b4ec9c",
    "4b188c46dab6056f14a52013cd4686be26f2c4a0b0617bfecc6cd7a8065b5e3c",
    "42bae3b792a5d7142867b6688a5d03fcfe8bdf9c8dbed5235ec72ca7e848edf6",
    "9d1e841ad6c1b2e1a56ecfd22e41ae67092ce27c639e52dc775515745d87dd91",
)

#: How many revisions of the module the numbers of ours were measured on:
#: the tree of the study, the five that followed it, the one the rewrite of
#: the module came with and the one its tail declaration came with.
TREE_REVISIONS_MEASURED = 8

#: True when the tree moved between the measurement of the study and the
#: measurement on the tree of this file.
TREE_MOVED_SINCE_THE_STUDY = True

#: True when the numbers of ours were measured on the tree this file names.
#: They were: the numbers of this file are a record of the module as it is on
#: that tree, and the gate measures the module live to say whether they still
#: hold.
NUMBERS_HOLD_ON_THE_CURRENT_TREE = True

#: True when the numbers of ours came out the same as the numbers of the
#: study.  They did not: the module was rewritten (the ratio domain) and the
#: numbers moved with it.  Only the numbers of ours moved; the numbers of the
#: reference are the study's and do not depend on our tree.
NUMBERS_SAME_AS_THE_STUDY = False

#: True when the numbers of ours moved because the module was rewritten.
THE_NUMBERS_MOVED_WITH_THE_MODULE = True

# ---------------------------------------------------------------------------
# how the numbers were taken
# ---------------------------------------------------------------------------
#: The instrument: the voice instrument of the project, on both sides.
_VOICE_METHOD = (
    "measured with the voice instrument of the project "
    "(qa/qualityvoice/harness.py): the level of the hiss band before and "
    "after, the level of the voice band before and after, the roughness of the "
    "applied gain and the delay of the output"
)

#: How the reference was measured.
#:
#: The instrument is named, because the study has more than one host and only
#: one of them moves a parameter of its own and never puts it back (see
#: docs/BENCHMARK_NOTES.md, section 5.1).  This is the study's render host:
#: it puts every parameter back to its default before it renders, so a render
#: starts from the state the plug-in ships with.
_REFERENCE_METHOD = (
    "measured by the study on the reference with the voice instrument of the "
    "project: the reference was rendered through the study's render host, "
    "which puts every parameter back to its default before it renders, and the "
    "render was measured with the same code, so the two sides of the table "
    "are measured with one instrument"
)

#: How our own side was measured.  It names the tree and the moment, so that
#: a reader knows which module the number belongs to and when it was read.
_OURS_METHOD = (
    "measured on our own module with the voice instrument of the project, on "
    "the tree this file names: the sha256 of the module is %s, the "
    "measurement was taken at %s, and the numbers of ours are a record of the "
    "module as it is on that tree; the number is a measurement of ours and "
    "not of the reference"
    % (TREE_SHA256_VOICE_DE_NOISE, TREE_MEASURED_AT)
)

#: The setting the product's own rules pick.
_BEST_METHOD = (
    "the setting the product's own rules pick: the most hiss removed among "
    "the settings that hold the voice budget (1.5 dB) and the chatter budget "
    "(1.0 dB)"
)

#: The setting of ours the numbers were taken at.
_OURS_SETTING = (
    "our voice module with the reduction of its noise at its maximum (30 dB)"
)

#: The setting of the reference that removes the most and keeps the least.
_AGGRESSIVE_SETTING = (
    "the most aggressive setting of the reference: the gain of its ambience "
    "stem at its minimum (-60 dB)"
)

#: The setting of the reference that plays its voice stem alone.
_VOICE_STEM_SETTING = (
    "the voice stem of the reference alone, with the other two stems muted"
)

# ---------------------------------------------------------------------------
# the shape of the two sides
# ---------------------------------------------------------------------------
#: True when the reference collapses the channels: its output carries the same
#: samples in both channels, so the side channel of a stereo file is discarded
#: with the image instead of being de-noised.
REFERENCE_OUTPUT_IS_DUAL_MONO = True

#: True when the delay of the reference follows the block size of the host.
REFERENCE_DELAY_DEPENDS_ON_BLOCK_SIZE = True

#: True when the delay of our module follows the block size of the host.  It
#: does not: the same 2047 samples at every block size.
OUR_DELAY_DEPENDS_ON_BLOCK_SIZE = False

#: True when the second reference can be compared on the voice at all.  It
#: cannot: its plug-in class publishes one of its modules only, and the noise
#: module of that product is not reachable through the plug-in interface, so
#: there is no de-noising setting of it to measure.  The comparison of this
#: file is therefore against the one reference whose voice handling is
#: reachable.
BAND_RESTORATION_VOICE_REACHABLE = False

# ---------------------------------------------------------------------------
# the numbers of ours
# ---------------------------------------------------------------------------
# The numbers below are the numbers of the module as it is on the tree named
# above, measured with the instrument of the project at the setting named
# above.  They are a record, not a promise: the module was rewritten (the
# ratio domain) and the numbers were taken again on the new tree.  The gate
# (``tests/test_benchmark_voice.py``) measures the module live and says
# whether they still hold.

#: The hiss our voice module removes on the voice clip of the corpus.
OUR_CORPUS_VOICE_REMOVED_DB = 29.65
#: How far it moves the wanted voice on that clip.
OUR_CORPUS_VOICE_KEPT_DB = 0.06
#: How much its gain curve breathes on that clip.
OUR_CORPUS_VOICE_CHATTER_DB = 0.03
#: How much its output changes when the host chops the stream into blocks.
OUR_CORPUS_VOICE_BLOCK_COHERENCE_DB = -221.9
#: The delay it adds on that clip.
OUR_CORPUS_VOICE_DELAY_SAMPLES = 2047

#: The hiss our voice module removes on the synthetic voice.
OUR_SYNTHETIC_VOICE_REMOVED_DB = 29.69
#: How far it moves the wanted voice there.
OUR_SYNTHETIC_VOICE_KEPT_DB = 0.18
#: How much its gain curve breathes there.
OUR_SYNTHETIC_VOICE_CHATTER_DB = 0.05
#: How much its output changes when the host chops the stream into blocks.
OUR_SYNTHETIC_VOICE_BLOCK_COHERENCE_DB = -218.2
#: The delay it adds there.
OUR_SYNTHETIC_VOICE_DELAY_SAMPLES = 2047

#: The hiss our voice module removes on the speech shaped signal.
OUR_SPEECH_SHAPED_REMOVED_DB = 29.73
#: How far it moves the wanted content there.
OUR_SPEECH_SHAPED_KEPT_DB = 0.12
#: How much its gain curve breathes there.
OUR_SPEECH_SHAPED_CHATTER_DB = 0.03
#: How much its output changes when the host chops the stream into blocks.
OUR_SPEECH_SHAPED_BLOCK_COHERENCE_DB = -213.9
#: The delay it adds there.
OUR_SPEECH_SHAPED_DELAY_SAMPLES = 2047

# ---------------------------------------------------------------------------
# what moved, and why
# ---------------------------------------------------------------------------
#: How much more hiss the rewrite of the module removes on the voice clip of
#: the corpus (dB): the old module took 20.72 dB away, the new one takes
#: 29.65 dB.
REMOVAL_GAINED_BY_THE_REWRITE_DB = 8.93

#: How much the chatter of the gain curve fell on that clip (dB): 0.85 dB
#: before the rewrite, 0.03 dB after it.
CHATTER_LOST_BY_THE_REWRITE_DB = 0.82

#: How far the applied gain moved when the level of the input moved by 18 dB,
#: before the rewrite (dB).  The decision and the noise reference of the
#: module were a **level**, and a level carries the level of the input, so
#: the gain followed the level of the material.
LEVEL_SPREAD_BEFORE_THE_REWRITE_DB = 24.16

#: The same measurement after the rewrite (dB).  The decision and the noise
#: reference are a **ratio** -- the balance inside a band -- and a ratio does
#: not carry the level of the input, so the gain does not follow it any more.
#: The number is read with the voice instrument of the project on the ground
#: truth voice and hiss case of the quality report, in the band that case
#: names.
LEVEL_SPREAD_AFTER_THE_REWRITE_DB = 0.01

# ---------------------------------------------------------------------------
# the numbers of the reference
# ---------------------------------------------------------------------------
# The setting that holds the voice budget, on the voice clip of the corpus.
REFERENCE_CORPUS_VOICE_REMOVED_DB = 0.09
REFERENCE_CORPUS_VOICE_KEPT_DB = 0.15
REFERENCE_CORPUS_VOICE_CHATTER_DB = 0.00
REFERENCE_CORPUS_VOICE_BLOCK_COHERENCE_DB = -42.4
REFERENCE_CORPUS_VOICE_DELAY_SAMPLES = 2238

# The most aggressive setting, on the voice clip of the corpus: the hiss goes
# down, and the wanted voice goes with it.
REFERENCE_CORPUS_VOICE_AGGRESSIVE_REMOVED_DB = 23.87
REFERENCE_CORPUS_VOICE_AGGRESSIVE_KEPT_DB = 15.83
REFERENCE_CORPUS_VOICE_AGGRESSIVE_CHATTER_DB = 0.89

# The defaults of the reference, on the voice clip of the corpus.
REFERENCE_CORPUS_VOICE_DEFAULT_REMOVED_DB = 0.00
REFERENCE_CORPUS_VOICE_DEFAULT_KEPT_DB = 0.01

# A mild setting: the gain of the ambience stem at -12 dB.
REFERENCE_CORPUS_VOICE_MILD_REMOVED_DB = 3.35
REFERENCE_CORPUS_VOICE_MILD_KEPT_DB = 3.26

# A medium setting: the gain of the ambience stem at -24 dB.
REFERENCE_CORPUS_VOICE_MEDIUM_REMOVED_DB = 9.69
REFERENCE_CORPUS_VOICE_MEDIUM_KEPT_DB = 9.03

# The voice stem alone, on the voice clip of the corpus.
REFERENCE_CORPUS_VOICE_STEM_ALONE_REMOVED_DB = 23.45
REFERENCE_CORPUS_VOICE_STEM_ALONE_KEPT_DB = 16.28

# The setting that holds the voice budget, on the synthetic voice.
REFERENCE_SYNTHETIC_VOICE_REMOVED_DB = 8.94
REFERENCE_SYNTHETIC_VOICE_KEPT_DB = 0.04
REFERENCE_SYNTHETIC_VOICE_CHATTER_DB = 0.45
REFERENCE_SYNTHETIC_VOICE_BLOCK_COHERENCE_DB = -60.8
REFERENCE_SYNTHETIC_VOICE_DELAY_SAMPLES = 2238

# The most aggressive setting, on the synthetic voice.
REFERENCE_SYNTHETIC_VOICE_AGGRESSIVE_REMOVED_DB = 8.92
REFERENCE_SYNTHETIC_VOICE_AGGRESSIVE_KEPT_DB = 0.04
REFERENCE_SYNTHETIC_VOICE_AGGRESSIVE_CHATTER_DB = 0.45

# The setting that holds the voice budget, on the speech shaped signal.
REFERENCE_SPEECH_SHAPED_REMOVED_DB = 0.05
REFERENCE_SPEECH_SHAPED_KEPT_DB = 0.20
REFERENCE_SPEECH_SHAPED_CHATTER_DB = 0.00
REFERENCE_SPEECH_SHAPED_BLOCK_COHERENCE_DB = -136.2
REFERENCE_SPEECH_SHAPED_DELAY_SAMPLES = 2238

# The most aggressive setting, on the speech shaped signal.
REFERENCE_SPEECH_SHAPED_AGGRESSIVE_REMOVED_DB = 35.73
REFERENCE_SPEECH_SHAPED_AGGRESSIVE_KEPT_DB = 16.59
REFERENCE_SPEECH_SHAPED_AGGRESSIVE_CHATTER_DB = 0.89

# ---------------------------------------------------------------------------
# the delay against the block size of the host
# ---------------------------------------------------------------------------
REFERENCE_DELAY_SAMPLES_BLOCK_64 = 1534
REFERENCE_DELAY_SAMPLES_BLOCK_256 = 1470
REFERENCE_DELAY_SAMPLES_BLOCK_1024 = 2238
REFERENCE_DELAY_SAMPLES_BLOCK_4096 = 5310

OUR_DELAY_SAMPLES_BLOCK_64 = 2047
OUR_DELAY_SAMPLES_BLOCK_256 = 2047
OUR_DELAY_SAMPLES_BLOCK_1024 = 2047
OUR_DELAY_SAMPLES_BLOCK_4096 = 2047

# ---------------------------------------------------------------------------
# the stereo image
# ---------------------------------------------------------------------------
#: The rms of the side channel of the voice clip of the corpus, before the
#: reference is put in front of it.
REFERENCE_SIDE_CHANNEL_RMS_IN = 0.0186
#: The rms of the side channel of the same clip, after it: the two channels
#: of the output carry the same samples, so the side is gone with the image.
REFERENCE_SIDE_CHANNEL_RMS_OUT = 0.0


# ---------------------------------------------------------------------------
# the numbers, with the case, the unit and the way they were measured
# ---------------------------------------------------------------------------
#: Every number of the voice comparison.
VOICES: tuple[Voice, ...] = (
    # -- our side, the voice clip of the corpus ------------------------------
    Voice(
        name="OUR_CORPUS_VOICE_REMOVED_DB",
        series="ours",
        case="corpus_voice",
        metric="removed_db",
        value=OUR_CORPUS_VOICE_REMOVED_DB,
        unit="dB",
        method="%s; %s" % (_OURS_METHOD, _VOICE_METHOD),
        setting=_OURS_SETTING,
    ),
    Voice(
        name="OUR_CORPUS_VOICE_KEPT_DB",
        series="ours",
        case="corpus_voice",
        metric="kept_db",
        value=OUR_CORPUS_VOICE_KEPT_DB,
        unit="dB",
        method="%s; %s" % (_OURS_METHOD, _VOICE_METHOD),
        setting=_OURS_SETTING,
    ),
    Voice(
        name="OUR_CORPUS_VOICE_CHATTER_DB",
        series="ours",
        case="corpus_voice",
        metric="chatter_db",
        value=OUR_CORPUS_VOICE_CHATTER_DB,
        unit="dB",
        method="%s; %s" % (_OURS_METHOD, _VOICE_METHOD),
        setting=_OURS_SETTING,
    ),
    Voice(
        name="OUR_CORPUS_VOICE_BLOCK_COHERENCE_DB",
        series="ours",
        case="corpus_voice",
        metric="block_coherence_db",
        value=OUR_CORPUS_VOICE_BLOCK_COHERENCE_DB,
        unit="dB",
        method="%s; %s" % (_OURS_METHOD, _VOICE_METHOD),
        setting=_OURS_SETTING,
    ),
    Voice(
        name="OUR_CORPUS_VOICE_DELAY_SAMPLES",
        series="ours",
        case="corpus_voice",
        metric="delay_samples",
        value=OUR_CORPUS_VOICE_DELAY_SAMPLES,
        unit="samples",
        method="%s; %s" % (_OURS_METHOD, _VOICE_METHOD),
        setting=_OURS_SETTING,
    ),
    # -- our side, the synthetic voice ---------------------------------------
    Voice(
        name="OUR_SYNTHETIC_VOICE_REMOVED_DB",
        series="ours",
        case="synthetic_voice",
        metric="removed_db",
        value=OUR_SYNTHETIC_VOICE_REMOVED_DB,
        unit="dB",
        method="%s; %s" % (_OURS_METHOD, _VOICE_METHOD),
        setting=_OURS_SETTING,
    ),
    Voice(
        name="OUR_SYNTHETIC_VOICE_KEPT_DB",
        series="ours",
        case="synthetic_voice",
        metric="kept_db",
        value=OUR_SYNTHETIC_VOICE_KEPT_DB,
        unit="dB",
        method="%s; %s" % (_OURS_METHOD, _VOICE_METHOD),
        setting=_OURS_SETTING,
    ),
    Voice(
        name="OUR_SYNTHETIC_VOICE_CHATTER_DB",
        series="ours",
        case="synthetic_voice",
        metric="chatter_db",
        value=OUR_SYNTHETIC_VOICE_CHATTER_DB,
        unit="dB",
        method="%s; %s" % (_OURS_METHOD, _VOICE_METHOD),
        setting=_OURS_SETTING,
    ),
    Voice(
        name="OUR_SYNTHETIC_VOICE_BLOCK_COHERENCE_DB",
        series="ours",
        case="synthetic_voice",
        metric="block_coherence_db",
        value=OUR_SYNTHETIC_VOICE_BLOCK_COHERENCE_DB,
        unit="dB",
        method="%s; %s" % (_OURS_METHOD, _VOICE_METHOD),
        setting=_OURS_SETTING,
    ),
    Voice(
        name="OUR_SYNTHETIC_VOICE_DELAY_SAMPLES",
        series="ours",
        case="synthetic_voice",
        metric="delay_samples",
        value=OUR_SYNTHETIC_VOICE_DELAY_SAMPLES,
        unit="samples",
        method="%s; %s" % (_OURS_METHOD, _VOICE_METHOD),
        setting=_OURS_SETTING,
    ),
    # -- our side, the speech shaped signal ----------------------------------
    Voice(
        name="OUR_SPEECH_SHAPED_REMOVED_DB",
        series="ours",
        case="speech_shaped",
        metric="removed_db",
        value=OUR_SPEECH_SHAPED_REMOVED_DB,
        unit="dB",
        method="%s; %s" % (_OURS_METHOD, _VOICE_METHOD),
        setting=_OURS_SETTING,
    ),
    Voice(
        name="OUR_SPEECH_SHAPED_KEPT_DB",
        series="ours",
        case="speech_shaped",
        metric="kept_db",
        value=OUR_SPEECH_SHAPED_KEPT_DB,
        unit="dB",
        method="%s; %s" % (_OURS_METHOD, _VOICE_METHOD),
        setting=_OURS_SETTING,
    ),
    Voice(
        name="OUR_SPEECH_SHAPED_CHATTER_DB",
        series="ours",
        case="speech_shaped",
        metric="chatter_db",
        value=OUR_SPEECH_SHAPED_CHATTER_DB,
        unit="dB",
        method="%s; %s" % (_OURS_METHOD, _VOICE_METHOD),
        setting=_OURS_SETTING,
    ),
    Voice(
        name="OUR_SPEECH_SHAPED_BLOCK_COHERENCE_DB",
        series="ours",
        case="speech_shaped",
        metric="block_coherence_db",
        value=OUR_SPEECH_SHAPED_BLOCK_COHERENCE_DB,
        unit="dB",
        method="%s; %s" % (_OURS_METHOD, _VOICE_METHOD),
        setting=_OURS_SETTING,
    ),
    Voice(
        name="OUR_SPEECH_SHAPED_DELAY_SAMPLES",
        series="ours",
        case="speech_shaped",
        metric="delay_samples",
        value=OUR_SPEECH_SHAPED_DELAY_SAMPLES,
        unit="samples",
        method="%s; %s" % (_OURS_METHOD, _VOICE_METHOD),
        setting=_OURS_SETTING,
    ),
    # -- the reference, the setting that holds the voice budget --------------
    Voice(
        name="REFERENCE_CORPUS_VOICE_REMOVED_DB",
        series="stem_separation",
        case="corpus_voice",
        metric="removed_db",
        value=REFERENCE_CORPUS_VOICE_REMOVED_DB,
        unit="dB",
        method="%s; %s" % (_REFERENCE_METHOD, _BEST_METHOD),
        setting=_BEST_METHOD,
    ),
    Voice(
        name="REFERENCE_CORPUS_VOICE_KEPT_DB",
        series="stem_separation",
        case="corpus_voice",
        metric="kept_db",
        value=REFERENCE_CORPUS_VOICE_KEPT_DB,
        unit="dB",
        method="%s; %s" % (_REFERENCE_METHOD, _BEST_METHOD),
        setting=_BEST_METHOD,
    ),
    Voice(
        name="REFERENCE_CORPUS_VOICE_CHATTER_DB",
        series="stem_separation",
        case="corpus_voice",
        metric="chatter_db",
        value=REFERENCE_CORPUS_VOICE_CHATTER_DB,
        unit="dB",
        method="%s; %s" % (_REFERENCE_METHOD, _BEST_METHOD),
        setting=_BEST_METHOD,
    ),
    Voice(
        name="REFERENCE_CORPUS_VOICE_BLOCK_COHERENCE_DB",
        series="stem_separation",
        case="corpus_voice",
        metric="block_coherence_db",
        value=REFERENCE_CORPUS_VOICE_BLOCK_COHERENCE_DB,
        unit="dB",
        method="%s; %s" % (_REFERENCE_METHOD, _BEST_METHOD),
        setting=_BEST_METHOD,
    ),
    Voice(
        name="REFERENCE_CORPUS_VOICE_DELAY_SAMPLES",
        series="stem_separation",
        case="corpus_voice",
        metric="delay_samples",
        value=REFERENCE_CORPUS_VOICE_DELAY_SAMPLES,
        unit="samples",
        method="%s; %s" % (_REFERENCE_METHOD, _BEST_METHOD),
        setting=_BEST_METHOD,
    ),
    Voice(
        name="REFERENCE_SYNTHETIC_VOICE_REMOVED_DB",
        series="stem_separation",
        case="synthetic_voice",
        metric="removed_db",
        value=REFERENCE_SYNTHETIC_VOICE_REMOVED_DB,
        unit="dB",
        method="%s; %s" % (_REFERENCE_METHOD, _BEST_METHOD),
        setting=_BEST_METHOD,
    ),
    Voice(
        name="REFERENCE_SYNTHETIC_VOICE_KEPT_DB",
        series="stem_separation",
        case="synthetic_voice",
        metric="kept_db",
        value=REFERENCE_SYNTHETIC_VOICE_KEPT_DB,
        unit="dB",
        method="%s; %s" % (_REFERENCE_METHOD, _BEST_METHOD),
        setting=_BEST_METHOD,
    ),
    Voice(
        name="REFERENCE_SYNTHETIC_VOICE_CHATTER_DB",
        series="stem_separation",
        case="synthetic_voice",
        metric="chatter_db",
        value=REFERENCE_SYNTHETIC_VOICE_CHATTER_DB,
        unit="dB",
        method="%s; %s" % (_REFERENCE_METHOD, _BEST_METHOD),
        setting=_BEST_METHOD,
    ),
    Voice(
        name="REFERENCE_SYNTHETIC_VOICE_BLOCK_COHERENCE_DB",
        series="stem_separation",
        case="synthetic_voice",
        metric="block_coherence_db",
        value=REFERENCE_SYNTHETIC_VOICE_BLOCK_COHERENCE_DB,
        unit="dB",
        method="%s; %s" % (_REFERENCE_METHOD, _BEST_METHOD),
        setting=_BEST_METHOD,
    ),
    Voice(
        name="REFERENCE_SYNTHETIC_VOICE_DELAY_SAMPLES",
        series="stem_separation",
        case="synthetic_voice",
        metric="delay_samples",
        value=REFERENCE_SYNTHETIC_VOICE_DELAY_SAMPLES,
        unit="samples",
        method="%s; %s" % (_REFERENCE_METHOD, _BEST_METHOD),
        setting=_BEST_METHOD,
    ),
    Voice(
        name="REFERENCE_SPEECH_SHAPED_REMOVED_DB",
        series="stem_separation",
        case="speech_shaped",
        metric="removed_db",
        value=REFERENCE_SPEECH_SHAPED_REMOVED_DB,
        unit="dB",
        method="%s; %s" % (_REFERENCE_METHOD, _BEST_METHOD),
        setting=_BEST_METHOD,
    ),
    Voice(
        name="REFERENCE_SPEECH_SHAPED_KEPT_DB",
        series="stem_separation",
        case="speech_shaped",
        metric="kept_db",
        value=REFERENCE_SPEECH_SHAPED_KEPT_DB,
        unit="dB",
        method="%s; %s" % (_REFERENCE_METHOD, _BEST_METHOD),
        setting=_BEST_METHOD,
    ),
    Voice(
        name="REFERENCE_SPEECH_SHAPED_CHATTER_DB",
        series="stem_separation",
        case="speech_shaped",
        metric="chatter_db",
        value=REFERENCE_SPEECH_SHAPED_CHATTER_DB,
        unit="dB",
        method="%s; %s" % (_REFERENCE_METHOD, _BEST_METHOD),
        setting=_BEST_METHOD,
    ),
    Voice(
        name="REFERENCE_SPEECH_SHAPED_BLOCK_COHERENCE_DB",
        series="stem_separation",
        case="speech_shaped",
        metric="block_coherence_db",
        value=REFERENCE_SPEECH_SHAPED_BLOCK_COHERENCE_DB,
        unit="dB",
        method="%s; %s" % (_REFERENCE_METHOD, _BEST_METHOD),
        setting=_BEST_METHOD,
    ),
    Voice(
        name="REFERENCE_SPEECH_SHAPED_DELAY_SAMPLES",
        series="stem_separation",
        case="speech_shaped",
        metric="delay_samples",
        value=REFERENCE_SPEECH_SHAPED_DELAY_SAMPLES,
        unit="samples",
        method="%s; %s" % (_REFERENCE_METHOD, _BEST_METHOD),
        setting=_BEST_METHOD,
    ),
    # -- the reference, the most aggressive setting --------------------------
    Voice(
        name="REFERENCE_CORPUS_VOICE_AGGRESSIVE_REMOVED_DB",
        series="stem_separation",
        case="corpus_voice",
        metric="removed_db",
        value=REFERENCE_CORPUS_VOICE_AGGRESSIVE_REMOVED_DB,
        unit="dB",
        method="%s; the removal is bought with the wanted voice, so read it "
        "together with the kept_db row" % _REFERENCE_METHOD,
        setting=_AGGRESSIVE_SETTING,
    ),
    Voice(
        name="REFERENCE_CORPUS_VOICE_AGGRESSIVE_KEPT_DB",
        series="stem_separation",
        case="corpus_voice",
        metric="kept_db",
        value=REFERENCE_CORPUS_VOICE_AGGRESSIVE_KEPT_DB,
        unit="dB",
        method="%s; the wanted voice moves this far" % _REFERENCE_METHOD,
        setting=_AGGRESSIVE_SETTING,
    ),
    Voice(
        name="REFERENCE_CORPUS_VOICE_AGGRESSIVE_CHATTER_DB",
        series="stem_separation",
        case="corpus_voice",
        metric="chatter_db",
        value=REFERENCE_CORPUS_VOICE_AGGRESSIVE_CHATTER_DB,
        unit="dB",
        method="%s" % _REFERENCE_METHOD,
        setting=_AGGRESSIVE_SETTING,
    ),
    Voice(
        name="REFERENCE_SYNTHETIC_VOICE_AGGRESSIVE_REMOVED_DB",
        series="stem_separation",
        case="synthetic_voice",
        metric="removed_db",
        value=REFERENCE_SYNTHETIC_VOICE_AGGRESSIVE_REMOVED_DB,
        unit="dB",
        method="%s" % _REFERENCE_METHOD,
        setting=_AGGRESSIVE_SETTING,
    ),
    Voice(
        name="REFERENCE_SYNTHETIC_VOICE_AGGRESSIVE_KEPT_DB",
        series="stem_separation",
        case="synthetic_voice",
        metric="kept_db",
        value=REFERENCE_SYNTHETIC_VOICE_AGGRESSIVE_KEPT_DB,
        unit="dB",
        method="%s" % _REFERENCE_METHOD,
        setting=_AGGRESSIVE_SETTING,
    ),
    Voice(
        name="REFERENCE_SYNTHETIC_VOICE_AGGRESSIVE_CHATTER_DB",
        series="stem_separation",
        case="synthetic_voice",
        metric="chatter_db",
        value=REFERENCE_SYNTHETIC_VOICE_AGGRESSIVE_CHATTER_DB,
        unit="dB",
        method="%s" % _REFERENCE_METHOD,
        setting=_AGGRESSIVE_SETTING,
    ),
    Voice(
        name="REFERENCE_SPEECH_SHAPED_AGGRESSIVE_REMOVED_DB",
        series="stem_separation",
        case="speech_shaped",
        metric="removed_db",
        value=REFERENCE_SPEECH_SHAPED_AGGRESSIVE_REMOVED_DB,
        unit="dB",
        method="%s; the removal is bought with the wanted content, so read it "
        "together with the kept_db row" % _REFERENCE_METHOD,
        setting=_AGGRESSIVE_SETTING,
    ),
    Voice(
        name="REFERENCE_SPEECH_SHAPED_AGGRESSIVE_KEPT_DB",
        series="stem_separation",
        case="speech_shaped",
        metric="kept_db",
        value=REFERENCE_SPEECH_SHAPED_AGGRESSIVE_KEPT_DB,
        unit="dB",
        method="%s; the wanted content moves this far" % _REFERENCE_METHOD,
        setting=_AGGRESSIVE_SETTING,
    ),
    Voice(
        name="REFERENCE_SPEECH_SHAPED_AGGRESSIVE_CHATTER_DB",
        series="stem_separation",
        case="speech_shaped",
        metric="chatter_db",
        value=REFERENCE_SPEECH_SHAPED_AGGRESSIVE_CHATTER_DB,
        unit="dB",
        method="%s" % _REFERENCE_METHOD,
        setting=_AGGRESSIVE_SETTING,
    ),
    # -- the reference, the sweep of the voice clip of the corpus -------------
    Voice(
        name="REFERENCE_CORPUS_VOICE_DEFAULT_REMOVED_DB",
        series="stem_separation",
        case="corpus_voice",
        metric="removed_db",
        value=REFERENCE_CORPUS_VOICE_DEFAULT_REMOVED_DB,
        unit="dB",
        method="%s; the setting the reference comes with" % _REFERENCE_METHOD,
        setting="the setting the reference comes with",
    ),
    Voice(
        name="REFERENCE_CORPUS_VOICE_DEFAULT_KEPT_DB",
        series="stem_separation",
        case="corpus_voice",
        metric="kept_db",
        value=REFERENCE_CORPUS_VOICE_DEFAULT_KEPT_DB,
        unit="dB",
        method="%s; the setting the reference comes with" % _REFERENCE_METHOD,
        setting="the setting the reference comes with",
    ),
    Voice(
        name="REFERENCE_CORPUS_VOICE_MILD_REMOVED_DB",
        series="stem_separation",
        case="corpus_voice",
        metric="removed_db",
        value=REFERENCE_CORPUS_VOICE_MILD_REMOVED_DB,
        unit="dB",
        method="%s; the removal is bought with the wanted voice, so read it "
        "together with the kept_db row" % _REFERENCE_METHOD,
        setting="the gain of the ambience stem at -12 dB",
    ),
    Voice(
        name="REFERENCE_CORPUS_VOICE_MILD_KEPT_DB",
        series="stem_separation",
        case="corpus_voice",
        metric="kept_db",
        value=REFERENCE_CORPUS_VOICE_MILD_KEPT_DB,
        unit="dB",
        method="%s; the wanted voice moves this far" % _REFERENCE_METHOD,
        setting="the gain of the ambience stem at -12 dB",
    ),
    Voice(
        name="REFERENCE_CORPUS_VOICE_MEDIUM_REMOVED_DB",
        series="stem_separation",
        case="corpus_voice",
        metric="removed_db",
        value=REFERENCE_CORPUS_VOICE_MEDIUM_REMOVED_DB,
        unit="dB",
        method="%s; the removal is bought with the wanted voice, so read it "
        "together with the kept_db row" % _REFERENCE_METHOD,
        setting="the gain of the ambience stem at -24 dB",
    ),
    Voice(
        name="REFERENCE_CORPUS_VOICE_MEDIUM_KEPT_DB",
        series="stem_separation",
        case="corpus_voice",
        metric="kept_db",
        value=REFERENCE_CORPUS_VOICE_MEDIUM_KEPT_DB,
        unit="dB",
        method="%s; the wanted voice moves this far" % _REFERENCE_METHOD,
        setting="the gain of the ambience stem at -24 dB",
    ),
    Voice(
        name="REFERENCE_CORPUS_VOICE_STEM_ALONE_REMOVED_DB",
        series="stem_separation",
        case="corpus_voice",
        metric="removed_db",
        value=REFERENCE_CORPUS_VOICE_STEM_ALONE_REMOVED_DB,
        unit="dB",
        method="%s; the voice stem of the reference is empty on this clip, so "
        "the removal is bought with the wanted voice" % _REFERENCE_METHOD,
        setting=_VOICE_STEM_SETTING,
    ),
    Voice(
        name="REFERENCE_CORPUS_VOICE_STEM_ALONE_KEPT_DB",
        series="stem_separation",
        case="corpus_voice",
        metric="kept_db",
        value=REFERENCE_CORPUS_VOICE_STEM_ALONE_KEPT_DB,
        unit="dB",
        method="%s; the wanted voice moves this far" % _REFERENCE_METHOD,
        setting=_VOICE_STEM_SETTING,
    ),
    # -- the delay against the block size of the host ------------------------
    Voice(
        name="REFERENCE_DELAY_SAMPLES_BLOCK_64",
        series="stem_separation",
        case=EVERY_CASE,
        metric="delay_samples",
        value=REFERENCE_DELAY_SAMPLES_BLOCK_64,
        unit="samples",
        method="the delay the reference adds, measured on the render of the "
        "study's render host at this block size; the reference declares it "
        "honestly, "
        "the declared delay and the measured one agree to the sample",
        setting="a host block of 64 samples",
    ),
    Voice(
        name="REFERENCE_DELAY_SAMPLES_BLOCK_256",
        series="stem_separation",
        case=EVERY_CASE,
        metric="delay_samples",
        value=REFERENCE_DELAY_SAMPLES_BLOCK_256,
        unit="samples",
        method="the delay the reference adds, measured on the render of the "
        "study's render host at this block size",
        setting="a host block of 256 samples",
    ),
    Voice(
        name="REFERENCE_DELAY_SAMPLES_BLOCK_1024",
        series="stem_separation",
        case=EVERY_CASE,
        metric="delay_samples",
        value=REFERENCE_DELAY_SAMPLES_BLOCK_1024,
        unit="samples",
        method="the delay the reference adds, measured on the render of the "
        "study's render host at this block size",
        setting="a host block of 1024 samples",
    ),
    Voice(
        name="REFERENCE_DELAY_SAMPLES_BLOCK_4096",
        series="stem_separation",
        case=EVERY_CASE,
        metric="delay_samples",
        value=REFERENCE_DELAY_SAMPLES_BLOCK_4096,
        unit="samples",
        method="the delay the reference adds, measured on the render of the "
        "study's render host at this block size",
        setting="a host block of 4096 samples",
    ),
    Voice(
        name="OUR_DELAY_SAMPLES_BLOCK_64",
        series="ours",
        case=EVERY_CASE,
        metric="delay_samples",
        value=OUR_DELAY_SAMPLES_BLOCK_64,
        unit="samples",
        method="%s; the delay measured on the corpus clip, the signal fed to "
        "the module in blocks of this size" % _OURS_METHOD,
        setting="a host block of 64 samples",
    ),
    Voice(
        name="OUR_DELAY_SAMPLES_BLOCK_256",
        series="ours",
        case=EVERY_CASE,
        metric="delay_samples",
        value=OUR_DELAY_SAMPLES_BLOCK_256,
        unit="samples",
        method="%s; the delay measured on the corpus clip, the signal fed to "
        "the module in blocks of this size" % _OURS_METHOD,
        setting="a host block of 256 samples",
    ),
    Voice(
        name="OUR_DELAY_SAMPLES_BLOCK_1024",
        series="ours",
        case=EVERY_CASE,
        metric="delay_samples",
        value=OUR_DELAY_SAMPLES_BLOCK_1024,
        unit="samples",
        method="%s; the delay measured on the corpus clip, the signal fed to "
        "the module in blocks of this size" % _OURS_METHOD,
        setting="a host block of 1024 samples",
    ),
    Voice(
        name="OUR_DELAY_SAMPLES_BLOCK_4096",
        series="ours",
        case=EVERY_CASE,
        metric="delay_samples",
        value=OUR_DELAY_SAMPLES_BLOCK_4096,
        unit="samples",
        method="%s; the delay measured on the corpus clip, the signal fed to "
        "the module in blocks of this size" % _OURS_METHOD,
        setting="a host block of 4096 samples",
    ),
    # -- the stereo image ----------------------------------------------------
    Voice(
        name="REFERENCE_SIDE_CHANNEL_RMS_IN",
        series="stem_separation",
        case="corpus_voice",
        metric="side_channel_rms",
        value=REFERENCE_SIDE_CHANNEL_RMS_IN,
        unit="rms",
        method="the rms of the side channel of the voice clip of the corpus, "
        "measured with the ruler of the corpus, "
        "(the difference of its two channels) before the reference is put in "
        "front of it; the manifest puts the hiss of the clip there",
    ),
    Voice(
        name="REFERENCE_SIDE_CHANNEL_RMS_OUT",
        series="stem_separation",
        case="corpus_voice",
        metric="side_channel_rms",
        value=REFERENCE_SIDE_CHANNEL_RMS_OUT,
        unit="rms",
        method="the rms of the side channel of the same clip after the "
        "reference, measured with the ruler of the corpus on the render of the "
        "study's render host: "
        "the two channels of its output carry the same samples, so "
        "the side is discarded with the image instead of being de-noised",
    ),
)

#: The numbers of the voice comparison, keyed by the name of their constant.
VOICE_BY_NAME: dict[str, Voice] = {voice.name: voice for voice in VOICES}


def voices_for(case: str, metric: str) -> tuple[Voice, ...]:
    """Every number of a case and a metric, one per series.

    A number recorded under the name ``"*"`` belongs to every case: the delay
    and the image of a product are properties of the product, not of the
    material.
    """
    return tuple(
        voice
        for voice in VOICES
        if voice.metric == metric and voice.case in (case, EVERY_CASE)
    )


def voice_for(case: str, metric: str) -> float | None:
    """The number a case and a metric are held to.

    When more than one series has a number, the **strongest** one is the
    number: the gap that is worth closing is the one against the best of them.
    """
    found = [voice for voice in voices_for(case, metric) if voice.value is not None]
    if not found:
        return None
    rule = METRIC_BY_NAME[metric]
    if rule.higher_is_better:
        return max(float(voice.value) for voice in found)
    return min(float(voice.value) for voice in found)
