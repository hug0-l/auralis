"""
qa.quality.quality_rules — the quality rules of the project, in one place.

Every rule says three things about one module, measured on one ground truth
case (see :mod:`qa.quality.ground_truth`):

``remove_db``
    The module must take away at least this much of what it is for.  The
    number is the reduction of the energy of the band that carries nothing
    but the artefact.
``keep_db``
    The module must not damage the wanted content by more than this.  The
    number is the largest movement of a partial that is provably above the
    noise.
``gain_var_db2``
    The gain the module applies inside that band must not breathe.  The
    number is the temporal variance of the gain curve in dB^2; 1.0 dB^2 is a
    standard deviation of 1 dB.

A rule may add two more criteria, for the modules whose work is not a plain
removal: ``event_reduction_db`` (an impulsive artefact must lose this much),
``programme_shift_db`` (the material around the artefact must not move),
``reference_error_db`` (a restoration must come this much closer to the
truth) and ``block_coherence_db`` (the block size of the host must not
change the result by more than this).

The thresholds are *targets*, not measurements: they are written down so that
every module is judged by the same yard and so that the yard itself can be
argued about in one file.  The numbers that were measured when the rules
were written down are in ``qa/quality/REPORT.md`` (the study of the Spectral
De-Noise that grew into this instrument is ``qa/quality/STUDY.md``).

The rules are read by the acceptance test (``tests/test_quality_spectral.py``),
which asserts every rule that has one.  A module owner therefore only has to
add a rule here to get their module measured and gated.
"""
from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "Rule",
    "RULES",
    "RULES_BY_CODE",
    "MODULES_WITH_RULES",
    "rule_for",
]


@dataclass(frozen=True)
class Rule:
    """One quality rule for one module on one ground truth case."""

    code: str
    name: str
    case: str
    band: tuple[float, float] | None = None
    tones: tuple[float, ...] | None = None
    params: dict = field(default_factory=dict)
    remove_db: float | None = None
    keep_db: float | None = None
    gain_var_db2: float | None = None
    gain_metric: str = "effective"
    event_reduction_db: float | None = None
    programme_shift_db: float | None = None
    reference_error_db: float | None = None
    block_coherence_db: float | None = None
    reason: str = ""

    # -- the yard -----------------------------------------------------------
    def check(self, measurement) -> list[str]:
        """Return the list of criteria this measurement breaks.

        An empty list means the module holds the rule.  A measurement that
        carries a non-finite sample breaks every rule: a module that returns
        NaN does not hold a rule, however good its numbers look.
        """
        failures: list[str] = []
        if not measurement.output_finite:
            failures.append("the module returned a non-finite sample")
        if self.remove_db is not None and not measurement.removed_db >= self.remove_db:
            failures.append(
                "it removed %.2f dB of the artefact, the rule asks for %.2f dB"
                % (measurement.removed_db, self.remove_db)
            )
        if self.keep_db is not None and not measurement.kept_db <= self.keep_db:
            failures.append(
                "it moved a wanted partial by %.2f dB, the budget is %.2f dB"
                % (measurement.kept_db, self.keep_db)
            )
        if self.gain_var_db2 is not None:
            value = self.gain_value(measurement)
            if value is None:
                failures.append("the gain curve could not be measured")
            elif not value <= self.gain_var_db2:
                failures.append(
                    "its gain curve varies by %.2f dB^2, the rule allows %.2f dB^2"
                    % (value, self.gain_var_db2)
                )
        if self.event_reduction_db is not None:
            value = measurement.event_reduction_db
            if value is None:
                failures.append("the case carries no impulsive artefact")
            elif not value >= self.event_reduction_db:
                failures.append(
                    "it left %.2f dB of the impulsive artefact, the rule asks for "
                    "%.2f dB" % (value, self.event_reduction_db)
                )
        if self.programme_shift_db is not None:
            value = measurement.programme_shift_db
            if value is None:
                failures.append("the case carries no impulsive artefact")
            elif not abs(value) <= self.programme_shift_db:
                failures.append(
                    "it moved the programme around the artefact by %.2f dB, the "
                    "budget is %.2f dB" % (value, self.programme_shift_db)
                )
        if self.reference_error_db is not None:
            value = measurement.reference_error_db
            if value is None:
                failures.append("the case carries no reference")
            elif not value >= self.reference_error_db:
                failures.append(
                    "it came %.2f dB closer to the truth, the rule asks for %.2f dB"
                    % (value, self.reference_error_db)
                )
        if self.block_coherence_db is not None:
            value = measurement.block_coherence_db
            if value != value:  # NaN
                failures.append("the block coherence could not be measured")
            elif not value <= self.block_coherence_db:
                failures.append(
                    "the block size changes the result by %.2f dB, the rule allows "
                    "%.2f dB" % (value, self.block_coherence_db)
                )
        return failures

    def gain_value(self, measurement) -> float | None:
        """The gain-curve variance this rule is measured with."""
        if self.gain_metric == "internal":
            return measurement.internal_gain_var_db2
        if self.gain_metric == "effective_smooth":
            return measurement.gain_var_smooth_db2
        return measurement.gain_var_db2

    @property
    def identifier(self) -> str:
        """A stable name for the rule (used in the test ids and the report)."""
        return "%s_%s" % (self.code.lower(), self.case)


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------
RULES: tuple[Rule, ...] = (
    Rule(
        code="AUR_BANDRESTORATION",
        name="Band Restoration",
        case="tone_noise",
        remove_db=12.0,
        keep_db=1.0,
        gain_var_db2=1.0,
        block_coherence_db=1.0,
        reason=(
            "The module splits the recording into four bands and takes the "
            "learned floor out of every one of them, so the case of the "
            "reference is the case of the module: a 1 kHz tone over a "
            "stationary bed, where the 4-8 kHz band carries nothing but the "
            "bed.  Every decibel that leaves the band is a decibel of SNR, "
            "and 12 dB is the target: the module reaches 18.5 dB, because "
            "the band is closed by the whole depth of 20 dB.  The wanted "
            "partial may move by at most 1 dB — the crossover moves it by "
            "0.03 dB and the expander does not touch the band it lives in.  "
            "The gain may not breathe either: the expander is a soft knee "
            "above the floor, so a stationary bed is taken out by the whole "
            "depth and the gain curve of the band is flat (0.02 dB^2 "
            "measured).  The block size may not change the answer: the "
            "crossover, the level follower and the delay line all carry "
            "their state, and the render of a 4096 sample block and the "
            "render of 64 sample blocks are the same samples (-200 dB)."
        ),
    ),
    Rule(
        code="AUR_SPECTRALDENOISE",
        name="Spectral De-Noise",
        case="tone_noise",
        remove_db=12.0,
        keep_db=1.0,
        gain_var_db2=1.0,
        gain_metric="internal",
        reason=(
            "The reference case of the module: a 1 kHz tone over a stationary "
            "broadband bed.  The bed sits in a band that carries no wanted "
            "content, so every decibel that leaves the band is a decibel of "
            "SNR.  12 dB is the target the study set (qa/quality/STUDY.md "
            "section 1.3); the module's own 'Noise Reduction' cap is 15 dB, so "
            "the target is reachable with margin.  The wanted partial may move "
            "by at most 1 dB — a 1 dB error in a partial is at the edge of "
            "audibility and 3 dB is plainly audible.  The gain curve may not "
            "breathe: 1.0 dB^2 is a standard deviation of 1 dB, which is the "
            "threshold below which the attenuation of a stationary bed is "
            "heard as a steady removal rather than as musical noise."
        ),
    ),
    Rule(
        code="AUR_SPECTRALDENOISE",
        name="Spectral De-Noise",
        case="tone_hum",
        remove_db=12.0,
        keep_db=1.0,
        gain_var_db2=1.0,
        gain_metric="internal",
        reason=(
            "The same rule on a tone that sits over mains hum.  The hum keeps "
            "the masking limit of the module moving, so this case is the "
            "harshest of the two for the musical-noise criterion: a stationary "
            "bed must produce a steady attenuation whatever else is in the "
            "signal."
        ),
    ),
    Rule(
        code="AUR_SPECTRALDENOISE",
        name="Spectral De-Noise",
        case="voice_hiss",
        remove_db=12.0,
        keep_db=1.0,
        reason=(
            "The dense case: a voice-like stack whose partials sit 120 Hz "
            "apart, under hiss that is only 15 dB below the fourth "
            "harmonic.  The noise must still go (12 dB), and the wanted "
            "partials must not move (1 dB).  This is where the analysis "
            "resolution bites: at the default quality the window is 20 ms, "
            "so two partials share a bin and no decision rule can separate "
            "them (qa/quality/STUDY.md section 4.2)."
        ),
    ),
    Rule(
        code="AUR_SPECTRALDENOISE",
        name="Spectral De-Noise",
        case="music_mixture",
        remove_db=12.0,
        keep_db=1.0,
        reason=(
            "The same rule on a music-like mixture: a chord whose notes sit "
            "57-110 Hz apart plus a plucked note 31 dB above the bed.  A "
            "plucked note is a wanted partial that happens to arrive; it "
            "must survive the de-noiser."
        ),
    ),
    Rule(
        code="AUR_VOICEDENOISE",
        name="Voice De-Noise",
        case="voice_hiss",
        remove_db=10.0,
        keep_db=1.5,
        gain_var_db2=1.0,
        gain_metric="effective_smooth",
        reason=(
            "The rule of the voice report (qa/qualityvoice/REPORT.md): the "
            "hiss must lose 10 dB, the voice band may move by at most 1.5 dB "
            "and the applied gain may not chatter by more than 1 dB.  A voice "
            "de-noiser works in a band where the wanted content and the noise "
            "share the same bins, so its budget is a little wider than the "
            "1 dB of the spectral de-noiser; the chatter figure is measured "
            "on the drift of the applied gain (107 ms average), because the "
            "module does not publish its own gain curve."
        ),
    ),
    Rule(
        code="AUR_VOICEDENOISE",
        name="Voice De-Noise",
        case="bed_only",
        remove_db=24.0,
        gain_var_db2=1.0,
        reason=(
            "The bed rule of the voice module: on a frame that carries no "
            "wanted content the estimate of the unwanted content is the bed "
            "itself, and the gain goes to the floor of the bed instead of "
            "stopping at the noise reduction cap.  The case is a stationary "
            "white noise bed with nothing wanted in it, so every decibel "
            "that leaves the band is a decibel of removal.  24 dB is the "
            "number the reference product reaches on a bed of the corpus "
            "(22.69 dB on the pink bed, 24.68 dB on the white one), and the "
            "module reaches 40 dB of it.  The gain curve may not breathe: "
            "the rule is latched, so the gain of a bed is a constant and the "
            "curve of it is flat (0.04 dB^2 measured)."
        ),
    ),
    Rule(
        code="AUR_DECLICK",
        name="De-Click",
        case="transient",
        event_reduction_db=20.0,
        programme_shift_db=1.0,
        reason=(
            "A click is an artefact of a few samples.  A repair must take at "
            "least 20 dB of it away — the artefact is 10 dB above the "
            "programme, so anything less leaves it audible — and it must not "
            "move the programme around the click by more than 1 dB.  The "
            "second criterion is the defect the team brief lists for this "
            "module: at some sensitivities the detector fires on steady "
            "material and the programme loses 10 dB."
        ),
    ),
    Rule(
        code="AUR_DECLIP",
        name="De-Clip",
        case="clipped",
        reference_error_db=6.0,
        keep_db=2.5,
        reason=(
            "The rule of the case in one sentence: a module hands back a "
            "signal inside the digital ceiling, and the ground truth of a "
            "clipped case is therefore stated at the digital ceiling too — a "
            "hard clipper is memoryless, so the level of the programme it "
            "destroyed cannot be read back from the material, and the yard "
            "measures at the one level the format can hold.  The truth of "
            "the case peaks at 1.000000 (it was 1.242148 before this rule "
            "was settled: a level no file could hold and no module may "
            "return), and the material is untouched — the same samples are "
            "clipped, by the same amount, as before.  A de-clipper must bring "
            "the signal at least 6 dB closer to that truth and must not "
            "damage the wanted partials while it does so.  The wanted "
            "partial of this case is the truth's own content, so it is "
            "measured against the reference: the clipped signal's 1 kHz "
            "partial sits 5.22 dB above the truth's, so a budget of 1 dB "
            "could not be held by any module that restores the programme's "
            "level.  2.5 dB is the room a module needs to restore the "
            "programme's level (the convention it uses for the height the "
            "clipper took away) without touching the partial.  Measured on "
            "the default preset: 14.95 dB closer to the truth and 1.37 dB "
            "of damage at 1 kHz (1.25 dB at 1.5 kHz) — the module's own "
            "output peaks at 1.196291, the family's ceiling rule scales that "
            "1.56 dB down to 1.000000, and the partial is left 1.37 dB from "
            "the truth's."
        ),
    ),
    Rule(
        code="AUR_DEHUM",
        name="De-Hum",
        case="tone_hum",
        band=(40.0, 90.0),
        remove_db=20.0,
        keep_db=1.0,
        gain_var_db2=5.0,
        gain_metric="effective_smooth",
        reason=(
            "The hum sits in the 40-90 Hz band; the module must take 20 dB "
            "of it away — a hum that is only 10 dB down is still audible — "
            "and must leave the 1 kHz programme where it is (1 dB).  The "
            "gain criterion is looser than the 1 dB^2 of the de-noisers "
            "because the band that is left behind is the noise bed itself, "
            "whose own fluctuation would otherwise be counted as a drift of "
            "the gain: 5.0 dB^2 is a drift of 2.2 dB over 107 ms."
        ),
    ),
)

#: The rules of every module, keyed by the module code.
RULES_BY_CODE: dict[str, tuple[Rule, ...]] = {}
for _rule in RULES:
    RULES_BY_CODE.setdefault(_rule.code, ())
    RULES_BY_CODE[_rule.code] += (_rule,)

#: The modules that have at least one rule.
MODULES_WITH_RULES: tuple[str, ...] = tuple(sorted(RULES_BY_CODE))


def rule_for(code: str, case: str) -> Rule:
    """Return the rule of a module on a case."""
    for rule in RULES:
        if rule.code == code and rule.case == case:
            return rule
    raise KeyError(f"no rule for {code!r} on {case!r}")
