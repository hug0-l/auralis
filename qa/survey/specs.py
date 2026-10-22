"""
qa.survey.specs — what every module of the catalogue is measured against.

The catalogue holds 48 modules, and they do not all do the same thing.  A
de-noiser removes a defect and must leave the programme alone; a gain stage
multiplies the programme by a number; a classifier measures the programme and
does not touch it.  One rule for all of them would be meaningless, so every
module carries a **specification**:

``policy``
    ``restoration`` — the module removes a defect.  The unwanted part has to
    go, the wanted part has to stay.
    ``transform`` — the module is supposed to change the signal.  The wanted
    part has to change by the documented amount and nothing else.
    ``passthrough`` — the module measures the signal and returns it.

``checks``
    Every check names a region of the signal, the role the region plays
    (``keep``, ``target`` or ``residual``) and the range the measurement has
    to land in.  The gate applies every check on its own, so one bad band is
    a failure even when the average looks good.

The budgets in this file are the *documented* budgets: the number of decibels
of the wanted part a module may take away before it is a defect.  They come
from the design of the module, not from the measurement — the measurement
goes into the report, the budget goes into the test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

try:  # pragma: no cover - the package and the script both have to work
    from .harness import Region
except ImportError:  # pragma: no cover
    from harness import Region  # type: ignore[no-redef]

__all__ = ["Check", "ModuleSpec", "SPECS", "spec_for", "spec_codes"]


@dataclass(frozen=True)
class Check:
    """One measured region of one module.

    Attributes
    ----------
    region:
        The region of the signal that is measured.
    role:
        ``keep`` (the programme that must survive), ``target`` (the defect
        that must go) or ``residual`` (the reconstruction error against a
        clean reference signal).
    expect:
        The range the measured change has to land in, in decibels.  ``None``
        on either side leaves that side open.
    budget_db:
        For a ``keep`` check without an explicit expectation: the most
        decibels of the wanted part the module may take away.
    min_removed_db:
        For a ``target`` check without an explicit expectation: the most
        decibels of the unwanted part the module has to take away.
    label:
        A human readable name for the report.
    """

    region: Region
    role: str = "keep"
    expect: tuple[float | None, float | None] | None = None
    budget_db: float = 1.0
    min_removed_db: float = 0.0
    label: str = ""

    def allowed(self) -> tuple[float | None, float | None]:
        """Return the range the measured change has to land in."""
        if self.expect is not None:
            return self.expect
        if self.role == "keep":
            return (-float(self.budget_db), None)
        if self.role == "target":
            return (None, -float(self.min_removed_db))
        return (None, None)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation."""
        return {
            "label": self.label or self.region.label,
            "role": self.role,
            "region": self.region.to_dict(),
            "expect": list(self.allowed()),
            "budget_db": self.budget_db,
            "min_removed_db": self.min_removed_db,
        }


@dataclass(frozen=True)
class ModuleSpec:
    """The specification of one module of the catalogue."""

    code: str
    policy: str
    probe: str
    checks: tuple[Check, ...] = ()
    params: tuple[tuple[str, Any], ...] = ()
    duration_s: float | None = None
    learn: bool = False
    region: tuple[float, float, float, float] | None = None
    latency_tolerance: int | None = None
    note: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)

    def keep_checks(self) -> tuple[Check, ...]:
        """Return the checks that measure the wanted part."""
        return tuple(c for c in self.checks if c.role == "keep")

    def target_checks(self) -> tuple[Check, ...]:
        """Return the checks that measure the unwanted part."""
        return tuple(c for c in self.checks if c.role == "target")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation."""
        return {
            "code": self.code,
            "policy": self.policy,
            "probe": self.probe,
            "params": {name: value for name, value in self.params},
            "duration_s": self.duration_s,
            "learn": self.learn,
            "region": list(self.region) if self.region else None,
            "latency_tolerance": self.latency_tolerance,
            "checks": [c.to_dict() for c in self.checks],
            "note": self.note,
            "tags": list(self.tags),
        }


# ---------------------------------------------------------------------------
# shorthand
# ---------------------------------------------------------------------------
def band(
    lo_hz: float,
    hi_hz: float,
    label: str = "",
    start_s: float = 0.0,
    end_s: float | None = None,
    metric: str = "rms",
    channel: int | None = None,
) -> Region:
    """Return a frequency band region."""
    return Region(
        lo_hz=lo_hz,
        hi_hz=hi_hz,
        start_s=start_s,
        end_s=end_s,
        metric=metric,
        channel=channel,
        label=label,
    )


def keep(region: Region, budget_db: float = 1.0, expect=None, label: str = "") -> Check:
    """Return a check on the wanted part of the signal."""
    return Check(region=region, role="keep", budget_db=budget_db, expect=expect, label=label)


def target(region: Region, min_removed_db: float = 0.0, expect=None, label: str = "") -> Check:
    """Return a check on the unwanted part of the signal."""
    return Check(
        region=region,
        role="target",
        min_removed_db=min_removed_db,
        expect=expect,
        label=label,
    )


def residual(region: Region, expect, label: str = "") -> Check:
    """Return a check on the reconstruction error against a clean reference."""
    return Check(region=region, role="residual", expect=expect, label=label)


#: The whole programme, as a frequency band.
FULL_BAND = band(20.0, 20000.0, "full band")


# ---------------------------------------------------------------------------
# the 48 specifications
# ---------------------------------------------------------------------------
SPECS: dict[str, ModuleSpec] = {}


def _add(spec: ModuleSpec) -> ModuleSpec:
    SPECS[spec.code] = spec
    return spec


# -- Repair ------------------------------------------------------------------
_add(ModuleSpec(
    code="AUR_AMBIENCEMATCH",
    policy="restoration",
    probe="ambience_pair",
    duration_s=0.5,
    learn=True,
    checks=(
        keep(band(950.0, 1050.0, "programme 1 kHz", start_s=0.0, end_s=0.12),
             budget_db=1.5),
        target(band(6000.0, 12000.0, "source noise floor", start_s=0.15, end_s=0.25),
               min_removed_db=3.0),
    ),
    note="learn() captures the reference ambience; the source's own floor must be gated out",
))

_add(ModuleSpec(
    code="AUR_AZIMUTH",
    policy="transform",
    probe="stereo_tone",
    duration_s=0.5,
    checks=(
        keep(band(200.0, 240.0, "left channel 220 Hz", channel=0), expect=(-0.3, 0.3)),
        keep(band(410.0, 470.0, "right channel 440 Hz", channel=1), expect=(-0.3, 0.3)),
    ),
    note="the shipped defaults are neutral (Delay 0, Level 0), so the module must not change "
    "anything",
))

_add(ModuleSpec(
    code="AUR_BREATH",
    policy="restoration",
    probe="breath_burst",
    duration_s=0.5,
    checks=(
        keep(band(950.0, 1050.0, "programme 1 kHz", start_s=0.0, end_s=0.15),
             budget_db=1.0),
        target(band(2000.0, 8000.0, "breath burst", start_s=0.2, end_s=0.45),
               min_removed_db=3.0),
    ),
))

_add(ModuleSpec(
    code="AUR_CENTEREXTRACT",
    policy="restoration",
    probe="stereo_tone",
    duration_s=0.5,
    checks=(
        keep(band(410.0, 470.0, "centred 440 Hz"), budget_db=1.5),
        target(band(200.0, 240.0, "panned 220 Hz", channel=0), min_removed_db=3.0),
    ),
    note="a centred source must survive, a hard panned source must go",
))

_add(ModuleSpec(
    code="AUR_DEBLEED",
    policy="restoration",
    probe="bleed_pair",
    duration_s=0.5,
    learn=True,
    checks=(
        keep(band(950.0, 1050.0, "programme 1 kHz", channel=0), budget_db=1.5),
        target(band(400.0, 600.0, "bleeding 500 Hz source", channel=0), min_removed_db=3.0),
    ),
    note="channel 0 is the target, channel 1 carries the bleeding source",
))

_add(ModuleSpec(
    code="AUR_DECLICK",
    policy="restoration",
    probe="click_tone",
    duration_s=0.8,
    checks=(
        keep(band(450.0, 510.0, "480 Hz bed"), budget_db=1.0),
        target(band(0.0, 24000.0, "click at 0.25 s", start_s=0.249, end_s=0.251,
                    metric="peak"), min_removed_db=10.0),
        target(band(0.0, 24000.0, "click at 0.75 s", start_s=0.749, end_s=0.751,
                    metric="peak"), min_removed_db=10.0),
    ),
    note="the detector no longer fires on steady material: a 1 kHz programme keeps "
         "its level at every sensitivity from 0 to 10",
))

_add(ModuleSpec(
    code="AUR_DECLIP",
    policy="restoration",
    probe="clipped_tone",
    duration_s=0.5,
    checks=(
        keep(band(950.0, 1050.0, "1 kHz fundamental"), expect=(-3.5, 0.5)),
        target(band(2900.0, 3100.0, "3rd harmonic"), min_removed_db=3.0),
        target(band(4900.0, 5100.0, "5th harmonic"), min_removed_db=3.0),
    ),
    note="the clipping distortion of a hard clipped sine is odd harmonic; the "
         "rebuild of the flattened peaks takes the odd harmonics away, and the "
         "programme is given back the level the clipping chain took away, so "
         "the fundamental comes back at the level it had before the chain and "
         "not louder",
))

_add(ModuleSpec(
    code="AUR_DECRACKLE",
    policy="restoration",
    probe="crackle_tone",
    duration_s=0.5,
    checks=(
        keep(band(450.0, 510.0, "480 Hz bed"), budget_db=1.5),
        target(band(0.0, 24000.0, "crackle impulses", metric="peak"), min_removed_db=4.0),
    ),
))

_add(ModuleSpec(
    code="AUR_DEESS",
    policy="restoration",
    probe="sibilance_tone",
    duration_s=1.0,
    checks=(
        keep(band(950.0, 1050.0, "programme 1 kHz"), budget_db=1.5),
        target(band(5000.0, 7000.0, "sibilance burst", start_s=0.6, end_s=0.9),
               min_removed_db=3.0),
    ),
))

_add(ModuleSpec(
    code="AUR_DEHUM",
    policy="restoration",
    probe="hum_tone",
    duration_s=0.5,
    learn=True,
    checks=(
        keep(band(950.0, 1050.0, "programme 1 kHz"), budget_db=1.5),
        target(band(42.0, 108.0, "50 Hz hum and its 100 Hz harmonic"), min_removed_db=10.0),
    ),
))

_add(ModuleSpec(
    code="AUR_DEPLOSIVE",
    policy="restoration",
    probe="plosive_vowel",
    duration_s=0.8,
    checks=(
        keep(band(300.0, 3000.0, "vowel harmonics"), budget_db=1.5),
        target(band(40.0, 100.0, "low frequency burst", start_s=0.48, end_s=0.58),
               min_removed_db=3.0),
    ),
    note="the vowel's 120 Hz fundamental shares the band with the burst and is watched separately",
))

_add(ModuleSpec(
    code="AUR_SPATIALSUPPRESSOR",
    policy="restoration",
    probe="spatial_pair",
    duration_s=1.0,
    checks=(
        keep(band(120.0, 3000.0, "the voice in the middle"), budget_db=1.0),
        target(band(5000.0, 20000.0, "the hiss on the side"),
               min_removed_db=20.0),
    ),
))

_add(ModuleSpec(
    code="AUR_DEREVERB",
    policy="restoration",
    probe="reverb_tail",
    duration_s=1.0,
    checks=(
        keep(band(410.0, 470.0, "direct starter", start_s=0.19, end_s=0.22), budget_db=2.5),
        target(band(410.0, 470.0, "reverberant tail", start_s=0.4, end_s=1.0),
               min_removed_db=3.0),
    ),
))

_add(ModuleSpec(
    code="AUR_DERUSTLE",
    policy="restoration",
    probe="rustle_bed",
    duration_s=0.5,
    checks=(
        keep(band(950.0, 1050.0, "programme 1 kHz"), budget_db=1.5),
        target(band(6000.0, 12000.0, "friction bed"), min_removed_db=3.0),
    ),
))

_add(ModuleSpec(
    code="AUR_DEWIND",
    policy="restoration",
    probe="wind_rumble",
    duration_s=1.5,
    checks=(
        keep(band(950.0, 1050.0, "programme 1 kHz"), budget_db=1.5),
        keep(band(190.0, 210.0, "wanted low note"), budget_db=4.0),
        target(band(30.0, 120.0, "wind rumble, during the gust", start_s=0.60,
                    end_s=0.80), min_removed_db=3.0),
    ),
    note="the fundamental recovery puts half of the attenuation back on the "
         "tracked peak, so the low note keeps half of the reduction",
))

_add(ModuleSpec(
    code="AUR_DECONSTRUCT",
    policy="transform",
    probe="deconstruct_mix",
    duration_s=0.5,
    params=(("Noisy Gain", -12.0),),
    checks=(
        keep(band(200.0, 2000.0, "tonal part"), expect=(-4.0, -1.0)),
        target(band(6000.0, 12000.0, "noisy part"), expect=(-18.0, -6.0)),
    ),
    note="the tonal part must survive the separation, the noisy part must be attenuated",
))

_add(ModuleSpec(
    code="AUR_DIALOGUEISOLATE",
    policy="transform",
    probe="dialogue_mix",
    duration_s=1.0,
    params=(("Noise Gain", -12.0),),
    checks=(
        keep(band(120.0, 3000.0, "voice", start_s=0.05, end_s=0.45), budget_db=3.0),
        target(band(12000.0, 18000.0, "noise bed"), min_removed_db=6.0),
    ),
    note="the voice has to stay in the voice stem, otherwise the noise gain removes it",
))

_add(ModuleSpec(
    code="AUR_GUITARDENOISE",
    policy="restoration",
    probe="guitar_squeak",
    duration_s=0.5,
    checks=(
        keep(band(250.0, 3000.0, "guitar harmonics"), budget_db=3.0),
        keep(band(120.0, 250.0, "guitar fundamental"), budget_db=1.0),
        target(band(2800.0, 3200.0, "string squeak", start_s=0.2, end_s=0.32),
               min_removed_db=3.0),
    ),
    note="the squeak detector must not mistake the held fundamental for a squeak",
))

_add(ModuleSpec(
    code="AUR_BANDRESTORATION",
    policy="restoration",
    probe="band_noise",
    duration_s=0.5,
    learn=True,
    checks=(
        keep(band(950.0, 1050.0, "the programme of the second band"), budget_db=1.0),
        target(band(40.0, 180.0, "the noise of the first band"), min_removed_db=3.0),
    ),
    note="the module learns the noise of the recording and takes it out of the band "
         "it is in (below 200 Hz), the programme of the other bands has to stay",
))

_add(ModuleSpec(
    code="AUR_MOUTHDECLICK",
    policy="restoration",
    probe="click_tone",
    duration_s=0.8,
    checks=(
        keep(band(450.0, 510.0, "480 Hz bed"), budget_db=1.5),
        target(band(0.0, 24000.0, "click at 0.25 s", start_s=0.249, end_s=0.251,
                    metric="peak"), min_removed_db=6.0),
    ),
))

_add(ModuleSpec(
    code="AUR_MUSICREBALANCE",
    policy="transform",
    probe="music_mix",
    duration_s=0.5,
    params=(("Bass Gain", -12.0),),
    checks=(
        keep(band(400.0, 3000.0, "vocal"), expect=(-1.5, 1.5)),
        target(band(95.0, 130.0, "bass stem"), expect=(-18.0, -6.0)),
    ),
))

_add(ModuleSpec(
    code="AUR_REPAIRASSISTANT",
    policy="restoration",
    probe="repair_mix",
    duration_s=1.0,
    learn=True,
    params=(
        ("Noise Reduction", 0.5),
        ("Hum Reduction", 0.5),
        ("Click Reduction", 0.5),
    ),
    checks=(
        keep(band(120.0, 3000.0, "the voice"), budget_db=2.0),
        target(band(42.0, 108.0, "mains hum"), min_removed_db=2.0),
        target(band(6000.0, 12000.0, "noise bed"), min_removed_db=2.0),
    ),
    note="the shipped defaults are all zero, so the module does nothing until it is asked to; "
         "the depth of every repair is the parameter multiplied by the detection score",
))

_add(ModuleSpec(
    code="AUR_SPECTRALDENOISE",
    policy="restoration",
    probe="spectral_voice",
    duration_s=1.0,
    learn=True,
    checks=(
        keep(band(440.0, 520.0, "480 Hz partial"), budget_db=1.0),
        target(band(6000.0, 10000.0, "noise bed"), min_removed_db=6.0),
    ),
    note="known defect, in the team brief as defect 1: 6.46 dB of the wanted "
         "partial are removed; the fix of the study is history and the defect "
         "is still open",
))

_add(ModuleSpec(
    code="AUR_SPECTRALEDITOR",
    policy="transform",
    probe="tone_1k",
    duration_s=0.5,
    region=(0.1, 0.3, 4000.0, 8000.0),
    params=(("Mode", "Attenuate"),),
    checks=(
        keep(band(950.0, 1050.0, "outside the region"), expect=(-0.2, 0.2)),
    ),
    note="everything outside the rectangle must be passed through bit for bit",
))

_add(ModuleSpec(
    code="AUR_SPECTRALRECOVERY",
    policy="transform",
    probe="band_limited",
    duration_s=0.5,
    learn=True,
    checks=(
        keep(band(950.0, 1050.0, "surviving programme"), expect=(-1.5, 1.5)),
        target(band(4000.0, 8000.0, "missing top end"), expect=(0.0, None)),
    ),
    note="the module is non destructive: it adds the missing band, it never removes",
))

_add(ModuleSpec(
    code="AUR_SPECTRALREPAIR",
    policy="transform",
    probe="tone_1k",
    duration_s=0.5,
    region=(0.1, 0.3, 4000.0, 8000.0),
    params=(("Mode", "Attenuate"),),
    checks=(
        keep(band(950.0, 1050.0, "outside the region"), expect=(-0.2, 0.2)),
    ),
    note="everything outside the rectangle must be passed through bit for bit",
))

_add(ModuleSpec(
    code="AUR_VOICEDENOISE",
    policy="restoration",
    probe="voice_hiss",
    duration_s=1.0,
    learn=True,
    checks=(
        keep(band(120.0, 3000.0, "voice band"), budget_db=1.5),
        target(band(5000.0, 14000.0, "hiss band"), min_removed_db=6.0),
    ),
    note="the module was rewritten: the voice band moves by 0.02 dB and the "
         "module is block coherent to -314 dB",
))

_add(ModuleSpec(
    code="AUR_VOICEREVERB",
    policy="restoration",
    probe="voice_room",
    duration_s=1.2,
    checks=(
        keep(band(120.0, 3000.0, "the voice", start_s=0.29, end_s=1.18),
             budget_db=2.0),
        target(band(120.0, 3000.0, "the room in the first gap", start_s=0.22,
                    end_s=0.29), min_removed_db=4.0),
    ),
    note="the module measures the room in the gaps between the words and takes the "
         "reverberant field out of the voice; the voice itself has to stay, and what "
         "is left in the gap is the room",
))

_add(ModuleSpec(
    code="AUR_WOWFLUTTER",
    policy="restoration",
    probe="wow_tone",
    duration_s=4.0,
    checks=(
        keep(band(437.0, 443.0, "440 Hz carrier"), budget_db=1.5),
        target(band(434.0, 438.0, "lower sideband"), min_removed_db=2.0),
        target(band(442.0, 446.0, "upper sideband"), min_removed_db=2.0),
    ),
    latency_tolerance=64,
    note="the correction is not symmetric: the sidebands above and below the carrier "
         "do not lose the same amount.  The module warps the time axis, so the delay "
         "it applies is not a constant: the delay it declares (1024 samples) is the "
         "nominal one, and the cross correlation of the output against the input "
         "peaks within a block of it (974 samples measured with 0.64 s of probe, "
         "1024 with 0.25 s), so the delay is held to a block"
))

# -- Utility -----------------------------------------------------------------
_add(ModuleSpec(
    code="AUR_DITHER",
    policy="transform",
    probe="tone_1k",
    duration_s=0.5,
    checks=(
        keep(band(950.0, 1050.0, "programme 1 kHz"), expect=(-0.1, 0.1)),
        target(band(16000.0, 20000.0, "dither noise floor"), expect=(0.0, None)),
    ),
    note="16 bit TPDF dither: the tone must not move, the noise floor must rise",
))

_add(ModuleSpec(
    code="AUR_EQ",
    policy="transform",
    probe="eq_tones",
    duration_s=0.5,
    params=(("Band 2 Gain", 3.0),),
    checks=(
        keep(band(4800.0, 5200.0, "5 kHz tone"), expect=(-0.6, 0.6)),
        target(band(950.0, 1050.0, "1 kHz tone, +3 dB"), expect=(1.5, 4.5)),
    ),
    note="the gain of a band runs from -18 to +3.6 dB, so the boost is a "
         "third of the cut",
))

_add(ModuleSpec(
    code="AUR_EQMATCH",
    policy="transform",
    probe="eq_target",
    duration_s=0.5,
    learn=True,
    params=(("Amount", 1.0),),
    checks=(
        keep(band(950.0, 1050.0, "target programme"), expect=(-2.5, 2.5)),
        target(band(2500.0, 3500.0, "the brightness of the reference"),
               expect=(6.0, 18.0)),
    ),
    note="the match curve is the difference between the reference and the target "
         "spectrum, so a band the reference does not carry is unbounded",
))

_add(ModuleSpec(
    code="AUR_FADE",
    policy="transform",
    probe="tone_1k",
    duration_s=0.5,
    params=(("Fade Length", 0.1),),
    checks=(
        keep(band(950.0, 1050.0, "steady part", start_s=0.15, end_s=0.35),
             expect=(-0.3, 0.3)),
        target(band(950.0, 1050.0, "head of the fade", start_s=0.0, end_s=0.02),
               expect=(None, -3.0)),
    ),
))

_add(ModuleSpec(
    code="AUR_GAIN",
    policy="transform",
    probe="tone_1k",
    duration_s=0.5,
    params=(("Gain", -6.0),),
    checks=(
        keep(band(950.0, 1050.0, "programme 1 kHz"), expect=(-6.3, -5.7)),
    ),
))

_add(ModuleSpec(
    code="AUR_INTERPOLATE",
    policy="restoration",
    probe="dropout_tone",
    duration_s=0.5,
    checks=(
        keep(band(950.0, 1050.0, "intact part", start_s=0.0, end_s=0.14),
             expect=(-1.0, 1.0)),
        target(band(950.0, 1050.0, "first dropout", start_s=0.149, end_s=0.152),
               expect=(0.0, None)),
        target(band(950.0, 1050.0, "second dropout", start_s=0.349, end_s=0.352),
               expect=(0.0, None)),
    ),
))

_add(ModuleSpec(
    code="AUR_LEVELER",
    policy="transform",
    probe="tone_1k",
    duration_s=0.5,
    checks=(
        keep(band(950.0, 1050.0, "programme 1 kHz"), expect=(-21.0, -8.0)),
    ),
    note="the leveler rides a slow gain towards -23 LUFS; a -9 dBFS tone has to "
         "come down by about 14 dB",
))

_add(ModuleSpec(
    code="AUR_LOUDNESS",
    policy="transform",
    probe="tone_1k",
    duration_s=0.5,
    checks=(
        keep(band(950.0, 1050.0, "programme 1 kHz"), expect=(-21.0, -8.0)),
    ),
    note="the loudness control rides the programme towards -23 LUFS",
))

_add(ModuleSpec(
    code="AUR_LOUDNESSOPT",
    policy="transform",
    probe="tone_1k",
    duration_s=0.5,
    checks=(
        keep(band(950.0, 1050.0, "programme 1 kHz"), expect=(-12.0, 12.0)),
    ),
))

_add(ModuleSpec(
    code="AUR_MIXING",
    policy="transform",
    probe="stereo_tone",
    duration_s=0.5,
    checks=(
        keep(band(200.0, 240.0, "left channel", channel=0), expect=(-0.3, 0.3)),
        keep(band(410.0, 470.0, "right channel", channel=1), expect=(-0.3, 0.3)),
    ),
    note="the shipped defaults are 100 % on both faders",
))

_add(ModuleSpec(
    code="AUR_NORMALIZE",
    policy="transform",
    probe="tone_1k",
    duration_s=0.5,
    checks=(
        keep(band(0.0, 24000.0, "output peak", metric="peak"), expect=(4.4, 5.6)),
    ),
    note="the tone peaks at -6 dBFS and the target is -1 dBFS, so the level must rise by 5 dB",
))

_add(ModuleSpec(
    code="AUR_PHASE",
    policy="transform",
    probe="stereo_tone",
    duration_s=0.5,
    checks=(
        keep(band(200.0, 240.0, "left channel", channel=0), expect=(-0.3, 0.3)),
        keep(band(410.0, 470.0, "right channel", channel=1), expect=(-0.3, 0.3)),
    ),
    note="the shipped defaults are neutral (Rotation 0, Adaptive off)",
))

_add(ModuleSpec(
    code="AUR_RESAMPLE",
    policy="transform",
    probe="tone_1k",
    duration_s=0.5,
    checks=(
        keep(band(950.0, 1050.0, "programme 1 kHz"), expect=(-0.5, 0.5)),
    ),
    note="the default target rate is the rate of the recording, so nothing has to move",
))

_add(ModuleSpec(
    code="AUR_SIGNALGEN",
    policy="transform",
    probe="tone_1k",
    duration_s=0.5,
    params=(("Frequency", 440.0),),
    checks=(
        keep(band(950.0, 1050.0, "incoming programme"), expect=(-0.3, 0.3)),
        target(band(430.0, 450.0, "generated tone"), expect=(0.0, None)),
    ),
))

_add(ModuleSpec(
    code="AUR_STREAMPREVIEW",
    policy="transform",
    probe="tone_1k",
    duration_s=0.5,
    checks=(
        keep(band(950.0, 1050.0, "programme 1 kHz"), expect=(-12.0, 12.0)),
    ),
))

_add(ModuleSpec(
    code="AUR_TIMEPITCH",
    policy="transform",
    probe="tone_1k",
    duration_s=0.5,
    checks=(
        keep(band(950.0, 1050.0, "programme 1 kHz"), expect=(-1.0, 1.0)),
    ),
    note="the shipped defaults are neutral (ratio 1, shift 0 st)",
))

_add(ModuleSpec(
    code="AUR_VARIPITCH",
    policy="transform",
    probe="tone_1k",
    duration_s=0.5,
    checks=(
        keep(band(950.0, 1050.0, "programme 1 kHz"), expect=(-1.0, 1.0)),
    ),
    note="the shipped default contour is 0 st, so the pitch must not move",
))

_add(ModuleSpec(
    code="AUR_VARTIME",
    policy="transform",
    probe="tone_1k",
    duration_s=0.5,
    checks=(
        keep(band(950.0, 1050.0, "programme 1 kHz"), expect=(-1.0, 1.0)),
    ),
    note="the shipped default ratio is 1, so the speed must not change",
))

# -- Measurement -------------------------------------------------------------
_add(ModuleSpec(
    code="AUR_CLIPCLASSIFIER",
    policy="passthrough",
    probe="music_like",
    duration_s=1.0,
    checks=(
        keep(band(0.0, 24000.0, "the whole signal"), expect=(-0.2, 0.2)),
    ),
    note="a measurement module: the audio passes through untouched",
))

_add(ModuleSpec(
    code="AUR_FINDSIMILAR",
    policy="passthrough",
    probe="repeated_phrase",
    duration_s=1.0,
    learn=True,
    checks=(
        keep(band(0.0, 24000.0, "the whole signal"), expect=(-0.2, 0.2)),
    ),
    note="a measurement module: the audio passes through untouched",
))

# -- System ------------------------------------------------------------------
_add(ModuleSpec(
    code="AUR_CONNECT",
    policy="passthrough",
    probe="tone_1k",
    duration_s=0.5,
    checks=(
        keep(band(0.0, 24000.0, "the whole signal"), expect=(-0.2, 0.2)),
    ),
    note="a system module: the audio passes through untouched",
))

_add(ModuleSpec(
    code="AUR_MONITOR",
    policy="passthrough",
    probe="tone_1k",
    duration_s=0.5,
    checks=(
        keep(band(0.0, 24000.0, "the whole signal"), expect=(-0.2, 0.2)),
    ),
    note="a system module: the audio passes through untouched",
))


# ---------------------------------------------------------------------------
# access
# ---------------------------------------------------------------------------
def spec_for(code: str) -> ModuleSpec:
    """Return the specification of one module."""
    try:
        return SPECS[code]
    except KeyError:
        raise KeyError(f"no survey specification for {code!r}") from None


def spec_codes() -> list[str]:
    """Return the codes of every specification, sorted."""
    return sorted(SPECS)
