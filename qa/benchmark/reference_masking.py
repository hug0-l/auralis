"""
qa.benchmark.reference_masking — the masking laws of the reference plug-ins, as
plain constants.

The masking law is the rule that decides, per band, what is **wanted** and what
is **noise**.  These are the numbers that were measured for the two reference
series of the study, and the yard our own modules are held to.

Nothing in this file names a third party.  The two references are described by
their shape:

``stem_separation``
    the reference that splits a recording into three stems and gives each of
    them a gain and three switches;
``band_restoration``
    the reference that picks an algorithm and gives it four bands.

How the numbers were taken
---------------------------

Every number was measured with the same ruler: a signal is rendered through the
reference and the output is compared with the input in the **long term average
power spectrum** (Welch, 4096 point window, 1024 sample hop, the frames before
0.5 s dropped).  The gain inside a band is

    10 * log10 ( sum_out P(f) / sum_in P(f) )

A long term average is used because it does not move with the latency the
reference adds: a delay moves the frames around, it does not change their
average power.  The instrument was checked against known scalings (a signal
scaled by 0.5 measured -6.021 dB, by 0.316 -10.006 dB, by 0.1 -20.000 dB).

The reference was rendered through the study's own VST3 renderer, which moves
no parameter of its own, so the default it measures is the plug-in's default.

A reference is a **target, not a contract**: it was measured on somebody else's
build, on somebody else's machine, with somebody else's settings.  The
benchmark reports the gap; it does not fail on it.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "MaskingLaw",
    "MASKING_LAWS",
    "LAW_BY_KEY",
    "law_for",
    "REFERENCE_A_SERIES",
    "REFERENCE_B_SERIES",
    # -- the stem separation reference -------------------------------------
    "REFERENCE_STEM_NOISE_REMOVAL_DB",
    "REFERENCE_STEM_NOISE_REMOVAL_MAX_DB",
    "REFERENCE_STEM_WANTED_MOVEMENT_DB",
    "REFERENCE_STEM_WANTED_MOVEMENT_WORST_DB",
    "REFERENCE_STEM_DECISION_LOW_SNR_DB",
    "REFERENCE_STEM_DECISION_HIGH_SNR_DB",
    "REFERENCE_STEM_DECISION_TRANSITION_SNR_DB",
    "REFERENCE_STEM_VOICE_STEM_EMPTY_DB",
    "REFERENCE_STEM_REVERB_STEM_EMPTY_DB",
    "REFERENCE_STEM_ROOM_TONE_GAIN_DB",
    "REFERENCE_STEM_FORMANT_PARTIAL_DB",
    "REFERENCE_STEM_PROGRAMME_MOVEMENT_DB",
    "REFERENCE_STEM_HUM_REMOVAL_DB",
    # -- the adaptation of the stem separation reference --------------------
    "REFERENCE_BED_LEAK_INTO_WANTED_DB",
    "REFERENCE_SEPARATION_WANTED_OVER_BED_DB",
    "REFERENCE_CHARACTER_SETTLING_MS",
    "REFERENCE_LEVEL_SETTLING_MS",
    "REFERENCE_CONVERGENCE_MS",
    # -- the band restoration reference ------------------------------------
    "REFERENCE_MODEL_NOISE_REMOVAL_DB",
    "REFERENCE_MODEL_WANTED_MOVEMENT_DB",
    "REFERENCE_MODEL_WANTED_MOVEMENT_WORST_DB",
    "REFERENCE_MODEL_PARTIAL_ATTENUATION_MIN_DB",
    "REFERENCE_MODEL_PARTIAL_ATTENUATION_MAX_DB",
    "REFERENCE_MODEL_ROOM_TONE_GAIN_DB",
    "REFERENCE_MODEL_BAND_COUNT",
    "REFERENCE_MODEL_CUTOFF_COUNT",
    "REFERENCE_MODEL_CUTOFF_MIN_HZ",
    "REFERENCE_MODEL_CUTOFF_MAX_HZ",
    "REFERENCE_MODEL_ALGORITHM_COUNT",
    "REFERENCE_MODEL_BAND_MIX_MIN",
    "REFERENCE_MODEL_BAND_MIX_MAX",
    "REFERENCE_MODEL_GLOBAL_MIX_MIN",
    "REFERENCE_MODEL_GLOBAL_MIX_MAX",
    "REFERENCE_MODEL_BEST_SETTING_MOVEMENT_DB",
    # -- ours ---------------------------------------------------------------
    "OUR_NOISE_THRESHOLD_DB",
    "OUR_TONAL_THRESHOLD_DB",
    "OUR_KNEE_DB",
    "OUR_NOISE_REDUCTION_DB",
    "OUR_TONAL_REDUCTION_DB",
    "OUR_VOICE_BAND_MOVEMENT_DB",
    "OUR_HISS_BAND_REMOVAL_DB",
    "OUR_PROGRAMME_MOVEMENT_DB",
    "OUR_HUM_REMOVAL_DB",
    "OUR_PINK_REMOVAL_DB",
    # -- the gap to the reference, measured on ours --------------------------
    "OUR_BED_LEAK_LEVEL_SENSITIVITY_DB",
    "REFERENCE_BED_LEAK_LEVEL_SENSITIVITY_DB",
]


#: The two reference series, by their shape.
REFERENCE_A_SERIES = "stem_separation"
REFERENCE_B_SERIES = "band_restoration"


# ---------------------------------------------------------------------------
# The stem separation reference
# ---------------------------------------------------------------------------
#
# The reference splits a recording into three stems — a wanted stem, an
# unwanted stem and a reverb stem — and gives each of them a gain and a solo
# and a mute.  The dry signal is **not** mixed in: with the bypass on the
# output is the input, otherwise the output is the sum of the three stems.
#
# At its default the reference removes **nothing**: a stationary bed comes out
# unchanged, band by band:
#
#   white noise:  -0.08  -0.04  +0.01  -0.00  -0.03  +0.00   dB
#   pink noise:   -0.10  -0.05  +0.01  -0.00  -0.03  +0.00   dB
#
# What it *does* do at the default is add a room tone: a hum bed, which has
# no energy above 1 kHz, comes out 20 dB and 45 dB higher in the 4-8 kHz and
# 8-20 kHz bands.
#
# The floor of the removal is the **gain of the unwanted stem**, not a
# threshold.  With that gain at its end stop the bed loses 59 ... 90 dB and
# nothing is left of it:
#
#   white noise: -73.24  -74.15  -76.99  -73.43  -67.36  -76.61   dB
#   pink noise:  -64.58  -64.07  -64.80  -60.82  -60.12  -61.84   dB
#
REFERENCE_STEM_NOISE_REMOVAL_DB = 0.0
REFERENCE_STEM_NOISE_REMOVAL_MAX_DB = 90.0

#: How far the wanted content moves **while the reference's decision keeps
#: it**: a formant shaped harmonic stack over a bed of the same level, the
#: eight partials between 130 Hz and 2600 Hz, measured at the partial after
#: the unwanted stem was taken away:
#
#:     -87.59  -85.26  -76.82  -61.52  -14.47  -28.31  -43.20  -18.82   dB
#:
#: The best of them is -14.47 dB and the worst -87.59 dB.
REFERENCE_STEM_WANTED_MOVEMENT_DB = 14.5
REFERENCE_STEM_WANTED_MOVEMENT_WORST_DB = 87.6

#: The decision of the reference is **not** a threshold on the wanted part's
#: own level.  The wanted part is protected while the bed beside it is at
#: least as loud as it is, and it is taken away once the bed is more than
#: about 3 dB below it.  Measured on the same eight partials, the strongest
#: of them (650 Hz, the first formant):
#:
#:   at  0 dB of signal to noise:  -14.47 dB   (kept)
#:   at  3 dB:                     -19.68 dB   (kept)
#:   at  6 dB:                     -22.51 dB   (half gone)
#:   at  9 dB:                     -59.33 dB   (gone)
#:   at 12 dB:                     -81.66 dB   (gone)
#:   at 24 dB:                     -89.95 dB   (gone)
#:
#: So the wanted part is lost **more** completely the cleaner it is.  The
#: transition is between the two numbers below, and it is the one behaviour
#: of the reference that must not be copied.
REFERENCE_STEM_DECISION_LOW_SNR_DB = 3.0
REFERENCE_STEM_DECISION_HIGH_SNR_DB = 9.0
REFERENCE_STEM_DECISION_TRANSITION_SNR_DB = 6.0

#: With a bed alone, the wanted stem is empty: 67 ... 79 dB below the input.
REFERENCE_STEM_VOICE_STEM_EMPTY_DB = -67.0

#: With a bed alone, the reverb stem is empty: 80 ... 88 dB below the input.
REFERENCE_STEM_REVERB_STEM_EMPTY_DB = -80.0

#: The unwanted stem carries a **synthesised room tone**: a hum bed, which
#: has no energy above 1 kHz, comes out 20 dB and 45 dB higher in the 4-8 kHz
#: and 8-20 kHz bands.
REFERENCE_STEM_ROOM_TONE_GAIN_DB = 20.0

#: Inside the wanted part the partials that survive best are the ones in the
#: formants of the wanted spectrum: at 0 dB of signal to noise the 650 Hz
#: partial was at -14.47 dB and the 2600 Hz partial at -18.82 dB, while the
#: partials between them were 25 to 72 dB further down.  The decision is
#: about the shape of the spectrum, not about its level.
REFERENCE_STEM_FORMANT_PARTIAL_DB = -14.5

#: The clearest single number of the comparison: on the corpus' hum case (a
#: 50 Hz hum with its 100 Hz harmonic under a 1 kHz programme), the reference
#: at the end stop of its unwanted stem takes 83.06 dB out of the hum **and
#: 70.10 dB out of the 1 kHz programme** next to it.  Ours takes 3.24 dB of
#: the hum and leaves the programme at 0.00 dB.
REFERENCE_STEM_PROGRAMME_MOVEMENT_DB = 70.1
REFERENCE_STEM_HUM_REMOVAL_DB = 83.1


# ---------------------------------------------------------------------------
# The adaptation of the stem separation reference
# ---------------------------------------------------------------------------
#
# The reference does not decide on an absolute level.  It learns the bed of the
# material it is given, it keeps the same **fraction** of that bed out of the
# wanted part whatever the level of the material is, and it looks again when
# the character of the material changes.
#
# The numbers below were measured by the study on the **voice case of the
# voice harness** (a voice under a white bed; the wanted part and the bed are
# read through the reference's own stem switches, in the band of the voice).
# Three files were used:
#
# * the level step: the same file at -30 dBFS and at -18 dBFS, the step of
#   12.00 dB in the middle of it, the levels read in the half second before
#   and in the half second after the step;
# * the character change: a white bed for the first 3.75 s and a pink bed
#   (the same voice) for the 1.25 s after it;
# * the controls: the same material, one file at a time, a **fresh instance
#   for every file**.  The controls and the step agree to 0.43 dB, so the
#   numbers are a function of the level in front of the reference and not of
#   its history.
#
#: The bed that reaches the wanted part of the reference, relative to the
#: wanted part, in dB.  It is -0.12 dB at the quiet level, -0.12 dB at the
#: loud level, -0.12 dB before the level step and -0.12 dB after it: the
#: reference keeps the same fraction of the bed whatever the level of the
#: material is, and that is the property ours does not have.  Measured by
#: the study on the voice case of the voice harness, the wanted part and the
#: bed read through the reference's own stem switches, in the band of the
#: voice, in the half second before and after the step.
REFERENCE_BED_LEAK_INTO_WANTED_DB = -0.12

#: The separation of the reference on the same case, in dB: the wanted part
#: comes back 25.4 dB above the bed when the bed is asked for on its own.
#: Measured by the study on the voice case of the voice harness, the wanted
#: part and the bed read through the reference's own stem switches, the
#: difference of their levels in the band of the voice.
REFERENCE_SEPARATION_WANTED_OVER_BED_DB = 25.4

#: The time the reference needs to look again when the **character** of the
#: material changes, in ms: 90 % of the excursion of the bed estimate is
#: covered in 100 ms, that is four blocks of 512 samples at 48 kHz.  The
#: wanted part does not move while it happens (0.43 dB, which is the edge of
#: the measuring window), so a re-estimate costs the wanted part nothing.
#: Measured by the study on the voice case of the voice harness, the bed
#: estimate of the reference read through its own stem switches, the gain
#: curve sampled every 25 ms, from the white bed to the pink one.
REFERENCE_CHARACTER_SETTLING_MS = 100.0

#: The time the reference needs to settle after the **level** of the material
#: steps, in ms: 90 % of the 3.5 dB excursion of the gain curve of the
#: wanted part is covered in 25 ms, that is two blocks of 512 samples at
#: 48 kHz.  Measured by the study on the voice case of the voice harness, a
#: step of 12.00 dB in the middle of the file, the gain curve of the wanted
#: part read through the reference's own stem switches and sampled every
#: 25 ms.
REFERENCE_LEVEL_SETTLING_MS = 25.0

#: The time the reference needs to be converged after the first audio, in ms:
#: the level of each of its stems, read in steps of 250 ms from the start of
#: the first file, does not move after that.  Measured by the study on the
#: voice case of the voice harness, the level of the output, of the wanted
#: part and of the bed estimate, read in steps of 250 ms from the first
#: audio.
REFERENCE_CONVERGENCE_MS = 250.0


# ---------------------------------------------------------------------------
# The band restoration reference
# ---------------------------------------------------------------------------
#
# The reference splits the signal into four bands at three cutoffs and hands
# each band to one of several trained models.  The decision is inside the
# model, and it is made **per band, not per bin**: the removal is a flat
# 14.1 dB in every band, for every bed, whatever the material is.
#
#   white noise:  -13.97  -14.09  -14.10  -14.09  -14.09  -14.11   dB
#   the corpus' hiss clip, 20-250 and 5000-20000 Hz:  -14.12  -14.11 dB
#   the corpus' pink hiss clip, 20-250 and 5000-20000 Hz: -14.15 -14.09 dB
#
# What the user can reach is the band edges and a per band crossfade:
#
#     global mix   0 ... 100 %    0 = dry, 50 = half, 100 = fully processed
#     band mix   -100 ... +100 %  0 = follow the global mix,
#                                 +100 = that band fully processed,
#                                 -100 = that band fully dry
#
REFERENCE_MODEL_NOISE_REMOVAL_DB = 14.1

#: How far the wanted content moves.  A wanted partial is attenuated by
#: 11 ... 17 dB at every signal to noise ratio between 0 dB and 30 dB, so
#: **there is no ratio at which the reference protects it**:
#:
#:   the 1 kHz partial over a white bed:
#:     at  0 dB of signal to noise: -17.34 dB
#:     at  6 dB:                     -14.86 dB
#:     at 12 dB:                     -14.80 dB
#:     at 18 dB:                     -13.81 dB
#:     at 24 dB:                     -12.11 dB
#:     at 30 dB:                     -11.28 dB
#:
#: The attenuation falls monotonically as the wanted part gets louder
#: relative to the bed — the law is monotone in the right direction — but
#: its floor is 11 dB of removal of the wanted content, not 0 dB.
REFERENCE_MODEL_WANTED_MOVEMENT_DB = 3.0
REFERENCE_MODEL_WANTED_MOVEMENT_WORST_DB = 13.0
REFERENCE_MODEL_PARTIAL_ATTENUATION_MIN_DB = 11.3
REFERENCE_MODEL_PARTIAL_ATTENUATION_MAX_DB = 17.3

#: The reference re-synthesises what it removes: a wanted signal with no
#: energy above 4 kHz comes out with 41 ... 116 dB more energy in the bands
#: that had none.
REFERENCE_MODEL_ROOM_TONE_GAIN_DB = 41.0

REFERENCE_MODEL_BAND_COUNT = 4
REFERENCE_MODEL_CUTOFF_COUNT = 3
REFERENCE_MODEL_CUTOFF_MIN_HZ = 100.0
REFERENCE_MODEL_CUTOFF_MAX_HZ = 10000.0

#: How many trained models the reference ships.
REFERENCE_MODEL_ALGORITHM_COUNT = 7

REFERENCE_MODEL_BAND_MIX_MIN = -100.0
REFERENCE_MODEL_BAND_MIX_MAX = 100.0
REFERENCE_MODEL_GLOBAL_MIX_MIN = 0.0
REFERENCE_MODEL_GLOBAL_MIX_MAX = 100.0

#: The price of asking for more: the setting that removes the most artefact
#: also throws the wanted content away.  The study's own sweep of the
#: sixteen algorithm slots found 269 dB of removal on a white bed and 80 dB
#: on the hum, with the wanted 1 kHz tone moved by 232 dB — gone.
REFERENCE_MODEL_BEST_SETTING_MOVEMENT_DB = 232.0


# ---------------------------------------------------------------------------
# Ours
# ---------------------------------------------------------------------------
#
# Our law is an explicit threshold with a knee, in
# ``aurcore/modules/spectral_de_noise.py``:
#
#     snr_db   = smoothed_magnitude_db - noise_floor_db
#     weight   = 0.5 * (1 + cos(pi * clip((snr_db - T) / K, 0, 1)))
#     depth_db = min(weight * R, R)
#     gain_db  = -depth_db
#
# A bin is noise when it is not more than T above the local floor; the
# transition is K wide; a bin that is noise is attenuated by up to R.
#
OUR_NOISE_THRESHOLD_DB = 0.0
OUR_TONAL_THRESHOLD_DB = 0.0
OUR_KNEE_DB = 0.5
OUR_NOISE_REDUCTION_DB = 15.0
OUR_TONAL_REDUCTION_DB = 12.0

#: What our own module does on the corpus' cases, measured with the ruler
#: above:
#:
#:   voice case  (voice band 100-4000 Hz)   -1.15 dB
#:   voice case  (hiss band 5000-20000 Hz) -13.60 dB
#:   hiss clip   (20-250 Hz)                -8.50 dB
#:   hiss clip   (5000-20000 Hz)           -14.08 dB
#:   pink clip   (20-250 Hz)               -19.99 dB
#:   hum clip    (45-55 Hz)                 -3.24 dB
#:   hum clip    (950-1050 Hz, wanted)      -0.00 dB
#
OUR_VOICE_BAND_MOVEMENT_DB = 1.15
OUR_HISS_BAND_REMOVAL_DB = 13.60
OUR_PROGRAMME_MOVEMENT_DB = 0.0
OUR_HUM_REMOVAL_DB = 3.24
OUR_PINK_REMOVAL_DB = 19.99

#: The gap to the reference, measured on our own spectral de-noise, in dB:
#: how much more of the bed our module keeps in the wanted part when the
#: material is 12 dB louder.  It removes 10.84 dB of the bed at the quiet
#: level and 10.05 dB of it at the loud one, and the difference is 0.79 dB.
#: The reference keeps the **same** fraction of the bed at every level (its
#: bed leak is -0.12 dB before the step and -0.12 dB after it), so this
#: number is the gap that has to be closed: our threshold is not level
#: invariant yet.  Measured by the study with the project's own engine on
#: the voice case of the voice harness, the same file at -30 dBFS and at
#: -18 dBFS and across a step of 12.00 dB, the levels read in the half
#: second before and in the half second after the step.
OUR_BED_LEAK_LEVEL_SENSITIVITY_DB = 0.79

#: The name the study prepared for that number.  It is a number of **ours**
#: and not of the reference: it measures our own module, so it is filed
#: with our own numbers and only keeps the name the study gave it.
REFERENCE_BED_LEAK_LEVEL_SENSITIVITY_DB = OUR_BED_LEAK_LEVEL_SENSITIVITY_DB


# ---------------------------------------------------------------------------
# The law, as a table
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class MaskingLaw:
    """One masking law: what decides wanted from noise, and by how much."""

    key: str
    series: str
    unit_of_decision: str
    kind: str
    threshold_db: float | None
    knee_db: float | None
    wanted_movement_db: float | None
    noise_removal_db: float | None
    note: str = ""

    def as_dict(self) -> dict:
        """The law as a dictionary, for the JSON result."""
        return {
            "key": self.key,
            "series": self.series,
            "unit_of_decision": self.unit_of_decision,
            "kind": self.kind,
            "threshold_db": self.threshold_db,
            "knee_db": self.knee_db,
            "wanted_movement_db": self.wanted_movement_db,
            "noise_removal_db": self.noise_removal_db,
            "note": self.note,
        }


#: The three laws of the table: the two references and ours.
MASKING_LAWS: tuple[MaskingLaw, ...] = (
    MaskingLaw(
        key="stem_separation",
        series=REFERENCE_A_SERIES,
        unit_of_decision="the time-frequency cell",
        kind="classification into three stems",
        threshold_db=None,
        knee_db=None,
        wanted_movement_db=REFERENCE_STEM_WANTED_MOVEMENT_DB,
        noise_removal_db=REFERENCE_STEM_NOISE_REMOVAL_DB,
        note=(
            "There is no threshold on the wanted part's own level.  The "
            "wanted part is protected while the bed beside it is at least as "
            "loud as it is, and it is taken away once the bed is more than "
            f"about {REFERENCE_STEM_DECISION_LOW_SNR_DB} dB below it: the "
            "transition sits between "
            f"{REFERENCE_STEM_DECISION_LOW_SNR_DB} dB and "
            f"{REFERENCE_STEM_DECISION_HIGH_SNR_DB} dB of signal to noise.  "
            "The partials that survive best are the ones in the formants of "
            "the wanted spectrum, so the decision is about the shape of the "
            "spectrum and not about its level.  This is the behaviour that "
            "must not be copied."
        ),
    ),
    MaskingLaw(
        key="band_restoration",
        series=REFERENCE_B_SERIES,
        unit_of_decision="the band (four bands, three cutoffs)",
        kind="a trained model inside the band",
        threshold_db=None,
        knee_db=None,
        wanted_movement_db=REFERENCE_MODEL_WANTED_MOVEMENT_DB,
        noise_removal_db=REFERENCE_MODEL_NOISE_REMOVAL_DB,
        note=(
            "The removal is a flat "
            f"{REFERENCE_MODEL_NOISE_REMOVAL_DB} dB in every band, for "
            "every bed, and the wanted content goes with it: a wanted "
            "partial is attenuated by 11 ... 17 dB at every signal to noise "
            "ratio between 0 dB and 30 dB, so there is no ratio at which it "
            "is protected.  The user reaches the decision only through the "
            "band edges and a per band crossfade, and the reference "
            "re-synthesises what it removes."
        ),
    ),
    MaskingLaw(
        key="ours",
        series="ours",
        unit_of_decision="the bin",
        kind="an explicit threshold with a knee",
        threshold_db=OUR_NOISE_THRESHOLD_DB,
        knee_db=OUR_KNEE_DB,
        wanted_movement_db=OUR_VOICE_BAND_MOVEMENT_DB,
        noise_removal_db=OUR_HISS_BAND_REMOVAL_DB,
        note=(
            "A bin is noise when it is not more than "
            f"{OUR_NOISE_THRESHOLD_DB} dB above the local floor; the "
            f"transition is {OUR_KNEE_DB} dB wide; a bin that is noise is "
            f"attenuated by up to {OUR_NOISE_REDUCTION_DB} dB.  The law is "
            "monotone in the right direction, which the stem reference's is "
            "not; what it needs is the defect fixed, not replaced.  One "
            "defect of the law is measured and written down: it is not "
            "level invariant yet.  Our module keeps "
            f"{OUR_BED_LEAK_LEVEL_SENSITIVITY_DB} dB more of the bed in the "
            "wanted part when the material is 12 dB louder, where the stem "
            f"reference keeps the same fraction at every level "
            f"({REFERENCE_BED_LEAK_INTO_WANTED_DB} dB, before the step and "
            "after it)."
        ),
    ),
)

#: The laws, keyed by their key.
LAW_BY_KEY: dict[str, MaskingLaw] = {law.key: law for law in MASKING_LAWS}


def law_for(key: str) -> MaskingLaw:
    """Return the masking law of one series (or of ``"ours"``)."""
    return LAW_BY_KEY[key]
