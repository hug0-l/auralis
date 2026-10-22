"""
qa.quality.ground_truth — the ground truth library of the quality instrument.

Every case is a signal whose *truth* is known: the wanted part, the unwanted
part and the signal that is actually fed to the module are three separate
arrays, so a measurement can say what a module removed and what it kept
without guessing.

The library is shared by everybody: the spectral study (``qa/quality/harness.py``),
the module rules (``qa/quality/quality_rules.py``) and the acceptance test
(``tests/test_quality_spectral.py``) all read the cases from here, so a module
owner finds the same ground truth the rest of the project measures against.

Cases
-----
``tone_noise``      a 1 kHz tone over a stationary broadband bed
``tone_hum``        a 1 kHz tone over 50/100/150 Hz mains hum
``voice_hiss``      a harmonic voice-like stack under hiss
``music_mixture``   a music-like chord and plucked note over a bed
``bed_only``        a stationary bed, no wanted content at all
``clipped``         a programme at the digital ceiling, driven past it and hard clipped
``transient``       a steady programme with four impulsive artefacts

Every case is deterministic: the random parts come from a seeded generator, so
two runs of the same measurement give the same numbers.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

__all__ = [
    "SR",
    "Case",
    "CASES",
    "CASE_NAMES",
    "make_case",
    "case_tone_noise",
    "case_tone_hum",
    "case_voice_hiss",
    "case_music_mixture",
    "case_bed_only",
    "case_clipped",
    "case_transient",
]

#: The sample rate every case is built at.
SR: int = 48_000

#: The default length of a case in seconds.
DURATION: float = 2.0

#: The factor the clipping chain of the ``clipped`` case drove the programme
#: by before the hard clipper took it.  The drive is stated against the
#: *material*; the case's truth is the programme at the digital ceiling, so
#: the drive seen from the truth is ``DRIVE / scale`` (2.981155).
DRIVE: float = 2.4


@dataclass(frozen=True)
class Case:
    """One synthetic measurement case with a known ground truth.

    Attributes
    ----------
    name:
        Identifier used in the report, in the rules and in the JSON results.
    clean:
        The wanted part of the signal.
    noise:
        The additive unwanted part (noise bed, hum, hiss, clicks).
    mixed:
        What is fed to the module.  It is ``clean + noise`` for every additive
        case; for the clipped case it is the *clipped* programme.
    tones:
        Frequencies (Hz) that carry wanted content.
    band:
        ``(lo, hi)`` Hz band that carries no wanted content.  The energy
        inside it is what a module is supposed to take away.
    description:
        Human readable one-liner for the report.
    events:
        Ground truth sample ranges of impulsive artefacts.  Empty when the
        case has none.
    reference:
        The signal a perfect module would return, when the case has one.
        The clipped case has it: the programme before the clipping, stated
        at the digital ceiling (see :func:`case_clipped`).
    warmup:
        The signal used to settle the module's trackers before the measured
        render.  It never contains the artefact the case is about.
    """

    name: str
    clean: np.ndarray
    noise: np.ndarray
    mixed: np.ndarray
    tones: tuple[float, ...]
    band: tuple[float, float]
    description: str = ""
    events: tuple[tuple[int, int], ...] = ()
    reference: np.ndarray | None = None
    warmup: np.ndarray | None = None
    meta: dict = field(default_factory=dict)

    @property
    def sr(self) -> int:
        """Sample rate of the case (always 48 kHz)."""
        return SR

    @property
    def length(self) -> int:
        """Length of the case in samples."""
        return int(self.mixed.size)


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------
def _tone(freq: float, n: int, amp: float, sr: int = SR, phase: float = 0.0) -> np.ndarray:
    """A sine of ``freq`` Hz lasting ``n`` samples."""
    t = np.arange(n, dtype=np.float64) / float(sr)
    return amp * np.sin(2.0 * math.pi * freq * t + phase)


def _white(n: int, std: float, rng: np.random.Generator) -> np.ndarray:
    """White Gaussian noise with the given standard deviation."""
    return rng.standard_normal(n) * float(std)


def _lowpass(x: np.ndarray, cutoff_hz: float, sr: int = SR) -> np.ndarray:
    """Zero-phase low pass (FFT domain) — used for the hiss beds."""
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(x.size, 1.0 / float(sr))
    spec[freqs > float(cutoff_hz)] = 0.0
    return np.fft.irfft(spec, n=x.size)


def case_tone_noise(duration: float = DURATION, seed: int = 101) -> Case:
    """A 1 kHz tone over a stationary broadband noise bed.

    The bed sits 17 dB below the tone, so the SNR in the noise-only band
    (4-8 kHz) is meaningful while the tone stays clearly audible.
    """
    rng = np.random.default_rng(seed)
    n = int(duration * SR)
    tone = _tone(1000.0, n, 0.20)
    noise = _white(n, 0.020, rng)
    return Case(
        name="tone_noise",
        clean=tone,
        noise=noise,
        mixed=tone + noise,
        tones=(1000.0,),
        band=(4000.0, 8000.0),
        description="1 kHz tone over a stationary white noise bed (4-8 kHz is noise only)",
        warmup=noise,
    )


def case_tone_hum(duration: float = DURATION, seed: int = 202) -> Case:
    """A 1 kHz tone over mains hum plus a broadband bed."""
    rng = np.random.default_rng(seed)
    n = int(duration * SR)
    tone = _tone(1000.0, n, 0.20)
    hum = np.zeros(n, dtype=np.float64)
    for k, amp in ((1, 0.06), (2, 0.03), (3, 0.015)):
        hum += _tone(50.0 * k, n, amp)
    bed = _white(n, 0.010, rng)
    noise = hum + bed
    return Case(
        name="tone_hum",
        clean=tone,
        noise=noise,
        mixed=tone + noise,
        tones=(1000.0,),
        band=(4000.0, 8000.0),
        description="1 kHz tone over 50/100/150 Hz mains hum on a quiet broadband bed",
        warmup=noise,
    )


def case_voice_hiss(duration: float = DURATION, seed: int = 303) -> Case:
    """A harmonic voice-like stack (120 Hz fundamental) under hiss."""
    rng = np.random.default_rng(seed)
    n = int(duration * SR)
    t = np.arange(n, dtype=np.float64) / float(SR)
    # slow syllable envelope: the voice fades in over the case
    env = 0.5 - 0.5 * np.cos(2.0 * math.pi * 1.6 * t / max(duration, 1e-6))
    env = 0.25 + 0.75 * env
    voice = np.zeros(n, dtype=np.float64)
    f0 = 120.0
    for k in range(1, 13):
        voice += _tone(f0 * k, n, 0.10 / k) * math.sin(math.pi * k * 0.31)
    voice *= env
    hiss = _lowpass(_white(n, 0.030, rng), 12000.0)
    return Case(
        name="voice_hiss",
        clean=voice,
        noise=hiss,
        mixed=voice + hiss,
        tones=(120.0, 240.0, 360.0, 480.0),
        band=(6000.0, 10000.0),
        description="harmonic voice-like stack (f0 = 120 Hz) under broadband hiss",
        warmup=hiss,
    )


def case_music_mixture(duration: float = DURATION, seed: int = 404) -> Case:
    """A music-like mixture: a chord plus a plucked note over a noisy bed."""
    rng = np.random.default_rng(seed)
    n = int(duration * SR)
    t = np.arange(n, dtype=np.float64) / float(SR)
    music = np.zeros(n, dtype=np.float64)
    for freq, amp in ((220.0, 0.16), (277.2, 0.12), (329.6, 0.14), (440.0, 0.10)):
        music += _tone(freq, n, amp)
    # plucked note with an exponential decay, changing every 0.5 s
    for start in (0.0, 0.5, 1.0, 1.5):
        i0 = int(start * SR)
        length = min(int(0.45 * SR), n - i0)
        if length <= 0:
            continue
        decay = np.exp(-np.arange(length) / (0.09 * SR))
        music[i0 : i0 + length] += _tone(660.0, length, 0.13) * decay
    music *= 0.6 + 0.4 * np.sin(2.0 * math.pi * 1.1 * t) ** 2
    bed = _white(n, 0.012, rng)
    return Case(
        name="music_mixture",
        clean=music,
        noise=bed,
        mixed=music + bed,
        tones=(220.0, 277.2, 329.6, 440.0, 660.0),
        band=(8000.0, 12000.0),
        description="music-like chord plus plucked note over a noisy bed (8-12 kHz is noise only)",
        warmup=bed,
    )


def case_bed_only(duration: float = DURATION, seed: int = 505) -> Case:
    """Noise only — the purest stationary-bed probe (no wanted content)."""
    rng = np.random.default_rng(seed)
    n = int(duration * SR)
    noise = _white(n, 0.020, rng)
    return Case(
        name="bed_only",
        clean=np.zeros(n, dtype=np.float64),
        noise=noise,
        mixed=noise.copy(),
        tones=(),
        band=(1000.0, 16000.0),
        description="stationary white noise bed with no wanted content",
        warmup=noise,
    )


def case_clipped(duration: float = DURATION, seed: int = 606) -> Case:
    """A programme at the digital ceiling, driven past it and hard clipped.

    The clean programme is the truth: the clipped signal is what the module
    is given, and the reference is what a perfect module would return.  The
    2.5-8 kHz band carries nothing but clipping harmonics and intermodulation
    products, so everything in it has to go.

    The truth is the programme **at the digital ceiling**, and that is a
    decision, not an accident.  A hard clipper is memoryless: the samples it
    flattens carry no information at all about the height of the waveform
    that was there, so the level of the programme the clipper destroyed
    cannot be read back from the material.  The case therefore states the
    truth at the level the format can hold — the rail — and that is the
    level the product delivers: the rule of the family holds the **output**
    of the product at the ceiling (the chain's output and the plug-in's
    output are clipped there), so a truth above the rail would be a material
    no file could hold and a level the product may never hand out, and the
    yard would measure against something the product can never deliver.

    The rule is at the output of the product and not inside a module, so the
    module is measured as it is: the de-clipper hands the programme back
    with the height the clipping chain took away, and the chain's output is
    clipped at the rail where it goes over it.  The wanted partial of the
    case is the truth's own content, so it is measured against the
    reference.

    The material is not touched by this: the same samples are clipped, by the
    same amount, as before.  The programme is scaled by
    ``1 / peak(material)`` and the drive follows it, so the clipped signal is
    bit for bit the signal the case always had (the truth's peak is
    1.000000, the drive 2.981155).
    """
    rng = np.random.default_rng(seed)
    n = int(duration * SR)
    programme = _tone(1000.0, n, 0.80) + _tone(1500.0, n, 0.50)
    programme *= 0.7 + 0.3 * np.sin(2.0 * math.pi * 0.8 * np.arange(n) / float(SR)) ** 2
    # a quiet bed so the case is not a pure tone
    bed = _white(n, 0.004, rng)
    material = programme + bed
    # the material the module is given: the programme driven past full scale
    # and hard clipped.  This is the signal the case has always had.
    mixed = np.clip(material * DRIVE, -1.0, 1.0)
    # the truth, at the digital ceiling
    scale = 1.0 / float(np.max(np.abs(material)))
    clean = material * scale
    return Case(
        name="clipped",
        clean=clean,
        noise=np.zeros(n, dtype=np.float64),
        mixed=mixed,
        tones=(1000.0, 1500.0),
        band=(2500.0, 8000.0),
        description="1 kHz + 1.5 kHz programme at the digital ceiling, driven 2.98x "
                    "past it and hard clipped",
        reference=clean,
        warmup=clean,
        meta={
            "clip_ceiling": 1.0,
            "drive": DRIVE / scale,
            "programme_drive": DRIVE,
            "truth_scale": scale,
        },
    )


def case_transient(duration: float = DURATION, seed: int = 707) -> Case:
    """A steady programme with four impulsive artefacts.

    The programme is a tone plus a quiet bed; the artefact is four short
    broadband bursts.  The ground truth carries the sample range of every
    burst, so the instrument can measure what happened *inside* the artefacts
    and what happened to the programme *around* them.

    The bursts are impulse-like (a sharp exponential of about 0.5 ms), not
    noise bursts: a click is a discontinuity, and a detector that looks for
    discontinuities must see one.
    """
    rng = np.random.default_rng(seed)
    n = int(duration * SR)
    programme = _tone(1000.0, n, 0.25) + _tone(250.0, n, 0.12)
    bed = _white(n, 0.010, rng)
    clean = programme + bed
    artefact = np.zeros(n, dtype=np.float64)
    events: list[tuple[int, int]] = []
    burst = int(0.0005 * SR)
    decay = max(1.0, 0.00002 * SR)
    for start_s in (0.30, 0.75, 1.20, 1.65):
        i0 = int(start_s * SR)
        length = min(burst, n - i0)
        if length <= 0:
            continue
        shape = np.exp(-np.arange(length) / decay)
        artefact[i0 : i0 + length] += 0.85 * shape
        events.append((i0, i0 + length))
    mixed = clean + artefact
    return Case(
        name="transient",
        clean=clean,
        noise=artefact,
        mixed=mixed,
        tones=(250.0, 1000.0),
        band=(2000.0, 16000.0),
        description="steady 1 kHz programme with four broadband click bursts",
        events=tuple(events),
        warmup=clean,
    )


#: Every case the instrument knows.
CASES: list[Case] = [
    case_tone_noise(),
    case_tone_hum(),
    case_voice_hiss(),
    case_music_mixture(),
    case_bed_only(),
    case_clipped(),
    case_transient(),
]

#: The names of the cases, in the order they are measured.
CASE_NAMES: tuple[str, ...] = tuple(case.name for case in CASES)


def make_case(name: str) -> Case:
    """Return a case by name."""
    for case in CASES:
        if case.name == name:
            return case
    raise KeyError(f"unknown case {name!r}")
