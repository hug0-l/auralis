"""
qa.qualityvoice.rules — the rules the voice shaped modules have to meet.

The harness measures; this file says what the numbers have to be.  Every
rule carries its number, so the gate and the report cannot drift apart.

The rules
---------

============================  =======  =============================================
rule                          number   what it means
============================  =======  =============================================

**R1 the wanted content**     1.5 dB   the band that carries the voice may not move
                                      by more than 1.5 dB.  A repair module that
                                      takes the voice with it has failed, no
                                      matter what it did to the noise.

**R2 the job**                see      the band the module is there for has to get
                             below    quieter by at least the number in the table
                                      below.  The number is per module.

**R3 the chatter**            1.0 dB   the gain curve may not move by more than 1 dB
                                      from frame to frame (the roughness of the
                                      gain curve, see
                                      :func:`qa.qualityvoice.harness.measure_chatter`).
                                      A jittering gain is heard as musical noise.

**R4 the latency**            1 sample the delay the module reports has to be the
                                      delay it really adds.

**R5 the block coherence**    -60 dB   the output may not depend on the block size
                                      of the host: the RMS difference between the
                                      one block render and the 4096 sample blocks
                                      has to stay 60 dB below the signal.

**R6 the transparency**       1e-9     a module that only mixes its own stems has
                                      to give the input back unchanged when every
                                      gain is at 0 dB.

**R7 the level**              12 dB    the output may not be more than 12 dB
                                      quieter than the input.  A module that
                                      silences the signal has not done its job,
                                      it has destroyed the material.

**R8 the clean signal**       0.5 dB   a module that removes noise has to leave
                                      clean material alone: the voice band of a
                                      voice without any background may not move
                                      by more than 0.5 dB.  The rule does not
                                      apply to the De-Ess (it is there to tame
                                      the sibilance of a voice) and to the
                                      Dialogue Isolate (it separates).

**R9 the level of the input**  0.5 dB   the gain the module applies may not follow
                                      the level of the input: the same material at
                                      another level has to get the same gain.
                                      The number is the spread of the applied
                                      gain over the 18 dB that the level of the
                                      input moves by.  A decision that carries
                                      the level of the input pumps, and the pump
                                      is heard.

**R10 the bed**              24 dB    a bed that carries no wanted content has to
                                      be taken away: the band has to lose at
                                      least 24 dB of it (the number the
                                      reference product reaches on a bed of the
                                      corpus), and it may not lose more than
                                      45 dB of it.  R7 stays in force while
                                      the wanted content is there: on a bed
                                      there is nothing to swallow.

The numbers per module
----------------------

``AUR_VOICEDENOISE``
    the hiss band (5-14 kHz) has to lose **10 dB**, the voice band
    (120 Hz - 3 kHz) may move by **1.5 dB**.
``AUR_BREATH``
    the breath has to lose **6 dB** inside the breath (the module applies
    -9 dB by default, so that the rule is met with a margin), the voice band
    may move by **1.5 dB**.
``AUR_MOUTHDECLICK``
    a mouth click has to lose **10 dB**, the voice band may move by
    **1.5 dB**.
``AUR_DEESS``
    a sibilant has to lose **6 dB**, the band below the cutoff
    (300 Hz - 3.5 kHz) may move by **1.5 dB**.
``AUR_DIALOGUEISOLATE``
    with the noise gain at -12 dB the hiss band has to lose **6 dB** and
    the voice band may move by **1.5 dB**; with every gain at 0 dB the
    output has to be the input (rule R6).

Every rule is asserted for every module that has one.  The rules that the
modules do not meet yet carry a strict ``xfail`` marker in
``tests/test_quality_voice.py``: the marker turns into a failure as soon as
the module reaches the number, so a fix cannot be forgotten.
"""
from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "Rule",
    "Probe",
    "PROBES",
    "RULE_TABLE",
    "VOICE_BUDGET_DB",
    "CHATTER_BUDGET_DB",
    "BLOCK_COHERENCE_DB",
    "LATENCY_TOLERANCE_SAMPLES",
    "LEVEL_BUDGET_DB",
    "CLEAN_BUDGET_DB",
    "LEVEL_SPREAD_BUDGET_DB",
    "BED_CEILING_DB",
    "TRANSPARENCY_TOLERANCE",
]

#: R1 — the band that carries the wanted content may move by at most this
#: much (dB).
VOICE_BUDGET_DB = 1.5

#: R3 — the gain curve may not be rougher than this (dB).
CHATTER_BUDGET_DB = 1.0

#: R5 — the output may not depend on the block size of the host (dB).
BLOCK_COHERENCE_DB = -60.0

#: R4 — the reported latency has to be the real one, to this many samples.
LATENCY_TOLERANCE_SAMPLES = 1

#: R7 — the output may not be more than this much quieter than the input (dB).
LEVEL_BUDGET_DB = 12.0

#: R8 — a clean signal may not move by more than this much (dB).
CLEAN_BUDGET_DB = 0.5

#: R9 — the applied gain may not move by more than this much when the level
#: of the input moves over 18 dB (dB).
LEVEL_SPREAD_BUDGET_DB = 0.5

#: R6 — a transparent mix has to give the input back to this tolerance.
TRANSPARENCY_TOLERANCE = 1e-9

#: R10 — the bed: how far the module may take a bed that carries no wanted
#: content away (dB).
#:
#: R7 allows the output to be 12 dB quieter than the input, and that is the
#: right budget while the wanted content is there: the module may not
#: swallow it.  On a bed that carries nothing wanted there is nothing to
#: swallow, and the rule the study of the reference hands over is that the
#: estimator goes all the way down to the floor of the bed instead of
#: stopping at the noise reduction cap.  The ceiling is the floor of the
#: estimator (the Wiener term of the module reaches 60 dB, and the module
#: stops 40 dB below it) with the settling of the decision on top.
BED_CEILING_DB = 45.0


@dataclass(frozen=True)
class Rule:
    """The numbers one measurement has to meet."""

    #: The band the module has to make quieter, as a name of the case.
    target_band: str = ""
    #: The stretches of the signal the target is measured in (empty: all).
    target_regions: tuple[str, ...] = ()
    #: R2 — the target band has to lose at least this much (dB).
    target_reduction_db: float = 0.0
    #: The band that carries the wanted content, as a name of the case.
    protected_band: str = "voice"
    #: R1 — the protected band may move by at most this much (dB).
    protected_change_db: float = VOICE_BUDGET_DB
    #: R3 — the chatter budget (dB).
    chatter_db: float = CHATTER_BUDGET_DB
    #: R5 — the block coherence budget (dB); ``None`` switches the rule off.
    block_coherence_db: float | None = BLOCK_COHERENCE_DB
    #: R4 — how far the reported latency may be from the real one (samples).
    latency_tolerance: int = LATENCY_TOLERANCE_SAMPLES
    #: R7 — how much quieter the output may be than the input (dB).
    level_db: float = LEVEL_BUDGET_DB
    #: R8 — how much the protected band may move on a clean signal (dB);
    #: ``None`` switches the rule off.
    clean_budget_db: float | None = None
    #: R9 — how much the applied gain may move when the level of the input
    #: moves over 18 dB (dB); ``None`` switches the rule off.
    level_spread_db: float | None = None
    #: R6 — the output has to equal the input within this tolerance.
    exact_output: bool = False


@dataclass(frozen=True)
class Probe:
    """One measurement of one module on one case."""

    #: The module code.
    module: str
    #: A unique name of the measurement.
    name: str
    #: The case the module is measured on.
    case: str
    #: The parameter overrides.
    config: dict[str, object] = field(default_factory=dict)
    #: The band the chatter is measured in (Hz).
    chatter_band: tuple[float, float] = (5_000.0, 14_000.0)
    #: The numbers the measurement has to meet.
    rule: Rule = field(default_factory=Rule)
    #: The rules of this probe are not met yet.  The gate carries a marker
    #: for such a probe, and the marker turns into a failure as soon as the
    #: module reaches the numbers.
    pending: bool = False


#: The measurements the gate runs.  One entry per rule that a module has.
#: The probes of R9, the level of the input.  Every module that has a
#: voice rule is measured on it: the gain of a module that works on a voice
#: may not follow the level of the input.
_LEVEL_PROBES: tuple[Probe, ...] = (
    Probe(
        module="AUR_VOICEDENOISE",
        name="voice_denoise_level",
        case="voice_hiss",
        config={},
        chatter_band=(5_000.0, 14_000.0),
        rule=Rule(
            target_band="",
            protected_band="voice",
            level_spread_db=LEVEL_SPREAD_BUDGET_DB,
        ),
    ),
    Probe(
        module="AUR_BREATH",
        name="breath_control_level",
        case="voice_hiss",
        config={},
        chatter_band=(4_000.0, 8_000.0),
        rule=Rule(
            target_band="",
            protected_band="voice",
            level_spread_db=LEVEL_SPREAD_BUDGET_DB,
        ),
    ),
    Probe(
        module="AUR_MOUTHDECLICK",
        name="mouth_de_click_level",
        case="voice_hiss",
        config={},
        chatter_band=(4_000.0, 14_000.0),
        rule=Rule(
            target_band="",
            protected_band="voice",
            level_spread_db=LEVEL_SPREAD_BUDGET_DB,
        ),
    ),
    Probe(
        module="AUR_DEESS",
        name="de_ess_level",
        case="voice_hiss",
        config={},
        chatter_band=(6_000.0, 14_000.0),
        rule=Rule(
            target_band="",
            protected_band="voice",
            level_spread_db=LEVEL_SPREAD_BUDGET_DB,
        ),
    ),
    Probe(
        module="AUR_DIALOGUEISOLATE",
        name="dialogue_isolate_level",
        case="voice_hiss",
        config={"Noise Gain": -12.0},
        chatter_band=(5_000.0, 14_000.0),
        rule=Rule(
            target_band="",
            protected_band="voice",
            level_spread_db=LEVEL_SPREAD_BUDGET_DB,
        ),
    ),
)


PROBES: tuple[Probe, ...] = (
    Probe(
        module="AUR_VOICEDENOISE",
        name="voice_denoise_bed",
        case="voice_bed",
        config={},
        chatter_band=(5_000.0, 14_000.0),
        rule=Rule(
            target_band="hiss",
            target_reduction_db=24.0,
            protected_band="hiss",
            protected_change_db=BED_CEILING_DB,
            chatter_db=CHATTER_BUDGET_DB,
            block_coherence_db=BLOCK_COHERENCE_DB,
            level_db=BED_CEILING_DB,
        ),
    ),
    Probe(
        module="AUR_VOICEDENOISE",
        name="voice_denoise_hiss",
        case="voice_hiss",
        config={},
        chatter_band=(5_000.0, 14_000.0),
        rule=Rule(
            target_band="hiss",
            target_reduction_db=10.0,
            protected_band="voice",
            protected_change_db=VOICE_BUDGET_DB,
            chatter_db=CHATTER_BUDGET_DB,
            block_coherence_db=BLOCK_COHERENCE_DB,
        ),
    ),
    Probe(
        module="AUR_VOICEDENOISE",
        name="voice_denoise_pink_hiss",
        case="voice_pink_hiss",
        config={},
        chatter_band=(5_000.0, 14_000.0),
        rule=Rule(
            target_band="hiss",
            target_reduction_db=10.0,
            protected_band="voice",
            protected_change_db=VOICE_BUDGET_DB,
            chatter_db=CHATTER_BUDGET_DB,
            block_coherence_db=BLOCK_COHERENCE_DB,
        ),
    ),
    Probe(
        module="AUR_BREATH",
        name="breath_control_breath",
        case="voice_breath",
        config={},
        chatter_band=(4_000.0, 8_000.0),
        rule=Rule(
            target_band="breath",
            target_regions=("breath",),
            target_reduction_db=6.0,
            protected_band="voice",
            protected_change_db=VOICE_BUDGET_DB,
            chatter_db=CHATTER_BUDGET_DB,
            block_coherence_db=BLOCK_COHERENCE_DB,
        ),
    ),
    Probe(
        module="AUR_BREATH",
        name="breath_control_clean",
        case="voice_clean",
        config={},
        chatter_band=(4_000.0, 8_000.0),
        rule=Rule(
            target_band="",
            protected_band="voice",
            protected_change_db=VOICE_BUDGET_DB,
            chatter_db=CHATTER_BUDGET_DB,
            block_coherence_db=BLOCK_COHERENCE_DB,
            clean_budget_db=CLEAN_BUDGET_DB,
        ),
    ),
    Probe(
        module="AUR_VOICEDENOISE",
        name="voice_denoise_clean",
        case="voice_clean",
        config={},
        chatter_band=(5_000.0, 14_000.0),
        rule=Rule(
            target_band="",
            protected_band="voice",
            protected_change_db=VOICE_BUDGET_DB,
            chatter_db=CHATTER_BUDGET_DB,
            block_coherence_db=BLOCK_COHERENCE_DB,
            clean_budget_db=CLEAN_BUDGET_DB,
        ),
    ),
    Probe(
        module="AUR_MOUTHDECLICK",
        name="mouth_de_click_click",
        case="voice_click",
        config={},
        chatter_band=(4_000.0, 14_000.0),
        rule=Rule(
            target_band="click",
            target_regions=("click_a", "click_b"),
            target_reduction_db=10.0,
            protected_band="voice",
            protected_change_db=VOICE_BUDGET_DB,
            chatter_db=CHATTER_BUDGET_DB,
            block_coherence_db=BLOCK_COHERENCE_DB,
        ),
    ),
    Probe(
        module="AUR_MOUTHDECLICK",
        name="mouth_de_click_clean",
        case="voice_clean",
        config={},
        chatter_band=(4_000.0, 14_000.0),
        rule=Rule(
            target_band="",
            protected_band="voice",
            protected_change_db=VOICE_BUDGET_DB,
            chatter_db=CHATTER_BUDGET_DB,
            block_coherence_db=BLOCK_COHERENCE_DB,
            clean_budget_db=CLEAN_BUDGET_DB,
        ),
    ),
    Probe(
        module="AUR_DEESS",
        name="de_ess_sibilance",
        case="voice_sibilance",
        config={},
        chatter_band=(6_000.0, 14_000.0),
        rule=Rule(
            target_band="sib",
            target_regions=("sib",),
            target_reduction_db=6.0,
            protected_band="low",
            protected_change_db=VOICE_BUDGET_DB,
            chatter_db=CHATTER_BUDGET_DB,
            block_coherence_db=BLOCK_COHERENCE_DB,
        ),
    ),
    Probe(
        module="AUR_DIALOGUEISOLATE",
        name="dialogue_isolate_noise_gain",
        case="voice_hiss",
        config={"Noise Gain": -12.0},
        chatter_band=(5_000.0, 14_000.0),
        rule=Rule(
            target_band="hiss",
            target_reduction_db=6.0,
            protected_band="voice",
            protected_change_db=VOICE_BUDGET_DB,
            chatter_db=CHATTER_BUDGET_DB,
            block_coherence_db=BLOCK_COHERENCE_DB,
        ),
    ),
    Probe(
        module="AUR_DIALOGUEISOLATE",
        name="dialogue_isolate_transparent",
        case="voice_hiss",
        config={"Voice Gain": 0.0, "Reverb Gain": 0.0, "Noise Gain": 0.0},
        chatter_band=(5_000.0, 14_000.0),
        rule=Rule(
            target_band="",
            target_reduction_db=0.0,
            protected_band="voice",
            protected_change_db=VOICE_BUDGET_DB,
            chatter_db=CHATTER_BUDGET_DB,
            block_coherence_db=BLOCK_COHERENCE_DB,
            exact_output=True,
        ),
    ),
) + _LEVEL_PROBES


#: The rules per module, for the report.  The first probe of a module
#: carries its numbers; every probe is listed.
RULE_TABLE: dict[str, dict[str, object]] = {}
for _probe in PROBES:
    _entry = RULE_TABLE.setdefault(
        _probe.module,
        {
            "case": _probe.case,
            "target_band": _probe.rule.target_band,
            "target_reduction_db": _probe.rule.target_reduction_db,
            "protected_band": _probe.rule.protected_band,
            "protected_change_db": _probe.rule.protected_change_db,
            "chatter_db": _probe.rule.chatter_db,
            "block_coherence_db": _probe.rule.block_coherence_db,
            "clean_budget_db": _probe.rule.clean_budget_db,
            "level_spread_db": _probe.rule.level_spread_db,
            "probes": [],
        },
    )
    if _probe.rule.target_band and not _entry["target_band"]:
        _entry["target_band"] = _probe.rule.target_band
        _entry["target_reduction_db"] = _probe.rule.target_reduction_db
        _entry["protected_band"] = _probe.rule.protected_band
        _entry["protected_change_db"] = _probe.rule.protected_change_db
    if _probe.rule.clean_budget_db is not None and _entry["clean_budget_db"] is None:
        _entry["clean_budget_db"] = _probe.rule.clean_budget_db
    if _probe.rule.level_spread_db is not None and _entry["level_spread_db"] is None:
        _entry["level_spread_db"] = _probe.rule.level_spread_db
    _entry["probes"].append(_probe.name)
del _entry, _probe
