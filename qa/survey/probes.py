"""
qa.survey.probes — the signals the survey measures the modules with.

Two kinds of probe are used:

* **corpus clips** — the synthesised golden set of ``corpus/audio/test``.
  The README of the corpus states which module each clip was built for, and
  the manifest carries the ground truth, so a clip is the honest probe for
  the module it was made for.
* **synthetic signals** — built for the purpose, for the modules that no
  corpus clip covers and for the probes that need two components in
  *disjoint* frequency bands so that "what was removed" and "what was kept"
  can be told apart.

Every probe is deterministic: the noise sources are drawn from a fixed seed.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_CORPUS = REPO_ROOT / "corpus"
if str(_CORPUS) not in sys.path:
    sys.path.insert(0, str(_CORPUS))

SAMPLE_RATE = 48_000
CHANNELS = 2
SEED = 20_260_101

__all__ = ["Probe", "build_probe", "probe_names", "PROBES"]


@dataclass
class Probe:
    """One probe signal and the material a module has to learn from."""

    name: str
    signal: np.ndarray
    learn: np.ndarray | None = None
    clean: np.ndarray | None = None
    note: str = ""

    @property
    def duration_s(self) -> float:
        """The length of the probe in seconds."""
        return self.signal.shape[0] / float(SAMPLE_RATE)


# ---------------------------------------------------------------------------
# building blocks
# ---------------------------------------------------------------------------
def tone(freq: float, amp: float, n: int, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Return a sine of ``amp`` peak amplitude."""
    t = np.arange(n, dtype=np.float64) / float(sample_rate)
    return amp * np.sin(2.0 * math.pi * float(freq) * t)


def harmonic_stack(
    f0: float,
    n: int,
    sample_rate: int = SAMPLE_RATE,
    count: int = 12,
    top_hz: float = 4000.0,
    tilt: bool = True,
) -> np.ndarray:
    """Return a voice-like harmonic stack with a 1/f tilt."""
    out = np.zeros(n, dtype=np.float64)
    t = np.arange(n, dtype=np.float64) / float(sample_rate)
    for k in range(1, count + 1):
        freq = f0 * k
        if freq > top_hz:
            break
        amp = 1.0 / k if tilt else 1.0
        out += amp * np.sin(2.0 * math.pi * freq * t)
    peak = float(np.max(np.abs(out)))
    if peak > 0.0:
        out = out / peak
    return out


def white(n: int, seed: int = SEED) -> np.ndarray:
    """Return white noise with unit standard deviation."""
    rng = np.random.default_rng(seed)
    return rng.standard_normal(n)


def band_noise(
    lo_hz: float,
    hi_hz: float,
    n: int,
    sample_rate: int = SAMPLE_RATE,
    seed: int = SEED,
) -> np.ndarray:
    """Return noise whose energy lies between ``lo_hz`` and ``hi_hz``."""
    rng = np.random.default_rng(seed)
    data = rng.standard_normal(n)
    spectrum = np.fft.rfft(data)
    freqs = np.fft.rfftfreq(n, 1.0 / float(sample_rate))
    spectrum[(freqs < float(lo_hz)) | (freqs > float(hi_hz))] = 0.0
    out = np.fft.irfft(spectrum, n)
    peak = float(np.max(np.abs(out)))
    if peak > 0.0:
        out = out / peak
    return out


def clicks(n: int, positions: list[int], amplitudes: list[float]) -> np.ndarray:
    """Return single sample impulses on a silent bed."""
    out = np.zeros(n, dtype=np.float64)
    for pos, amp in zip(positions, amplitudes):
        if 0 <= pos < n:
            out[pos] += amp
    return out


def rms(x: np.ndarray) -> float:
    """Return the root mean square of a signal."""
    arr = np.asarray(x, dtype=np.float64)
    if arr.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(arr * arr)))


def scale_rms(x: np.ndarray, level_dbfs: float) -> np.ndarray:
    """Return the signal scaled to a given RMS level."""
    value = rms(x)
    if value <= 0.0:
        return x
    return x * (10.0 ** (float(level_dbfs) / 20.0) / value)


def stereo(x: np.ndarray) -> np.ndarray:
    """Return the mono signal as an identical stereo pair."""
    arr = np.asarray(x, dtype=np.float64)
    return np.repeat(arr[:, None], CHANNELS, axis=1)


# ---------------------------------------------------------------------------
# a synthetic voice
# ---------------------------------------------------------------------------
#: The formants of the synthetic voice: centre frequency, bandwidth, strength.
_VOICE_FORMANTS: tuple[tuple[float, float, float], ...] = (
    (520.0, 70.0, 1.00),
    (1480.0, 95.0, 0.72),
    (2480.0, 130.0, 0.42),
    (3450.0, 220.0, 0.22),
)


def _formant_response(freq: float) -> float:
    """Return the amplitude of the formant envelope at a frequency."""
    total = 0.0
    for centre, bandwidth, gain in _VOICE_FORMANTS:
        total += gain / (1.0 + ((freq - centre) / bandwidth) ** 2)
    return total


def synthetic_voice(
    n: int,
    f0: float = 110.0,
    level_dbfs: float = -20.0,
    seed: int = SEED + 61,
    tilt_db_per_octave: float = -6.0,
) -> np.ndarray:
    """Return a voice-like signal with a moving fundamental and pauses.

    A harmonic stack on its own is not a voice: the partials of a real voice
    move, the spectrum has formants, the level follows the syllables and
    there are pauses between them.  The de-noisers and the dialogue
    separator all depend on those properties, so the probes use a voice that
    has them.

    Parameters
    ----------
    n:
        Number of samples.
    f0:
        The mean fundamental in Hz.
    level_dbfs:
        The RMS level of the voiced parts.
    seed:
        The seed of the random parts.
    tilt_db_per_octave:
        The slope of the radiated spectrum.

    Returns
    -------
    numpy.ndarray
        The voice, shape ``(n,)``.
    """
    rng = np.random.default_rng(seed)
    t = np.arange(n, dtype=np.float64) / float(SAMPLE_RATE)
    vibrato = 0.01 * np.sin(2.0 * math.pi * 4.8 * t + 0.7)
    drift = 0.02 * np.sin(2.0 * math.pi * 0.7 * t + 1.1)
    step = max(2, int(0.030 * SAMPLE_RATE))
    coarse = rng.standard_normal(n // step + 3) * 0.0035
    jitter = np.interp(np.arange(n), np.arange(coarse.size) * step, coarse)
    contour = f0 * (1.0 + vibrato + drift + jitter)
    phase = 2.0 * math.pi * np.cumsum(contour) / float(SAMPLE_RATE)

    # the syllable structure: five syllables with a pause between them
    envelope = np.zeros(n, dtype=np.float64)
    syllable = int(0.17 * SAMPLE_RATE)
    pause = int(0.07 * SAMPLE_RATE)
    cursor = int(0.05 * SAMPLE_RATE)
    voiced: list[tuple[int, int]] = []
    for _ in range(5):
        start = cursor
        end = min(n, start + syllable)
        if end > start:
            envelope[start:end] = 1.0
            voiced.append((start, end))
        cursor = end + pause
        if cursor >= n:
            break
    # the attack and the release of every syllable
    ramp = max(1, int(0.012 * SAMPLE_RATE))
    for start, end in voiced:
        head = min(ramp, (end - start) // 2)
        tail = min(ramp, (end - start) // 2)
        envelope[start : start + head] *= 0.5 - 0.5 * np.cos(
            np.pi * np.arange(head) / float(max(head, 1))
        )
        envelope[end - tail : end] *= 0.5 + 0.5 * np.cos(
            np.pi * np.arange(tail) / float(max(tail, 1))
        )

    exponent = float(tilt_db_per_octave) / (20.0 * math.log10(2.0))
    out = np.zeros(n, dtype=np.float64)
    count = min(60, int(0.45 * SAMPLE_RATE / max(f0 * 0.96, 1.0)))
    for k in range(1, count + 1):
        freq = k * f0
        amp = envelope * math.pow(freq / f0, exponent) * _formant_response(freq)
        if float(np.max(amp)) < 1e-7:
            continue
        out += amp * np.sin(k * phase + rng.uniform(0.0, 2.0 * math.pi))
    if voiced:
        mask = np.zeros(n, dtype=bool)
        for start, end in voiced:
            mask[start:end] = True
        level = rms(out[mask])
        if level > 0.0:
            out *= (10.0 ** (float(level_dbfs) / 20.0)) / level
    return out


def _corpus_clip(clip_id: str, seconds: float) -> np.ndarray:
    """Return the first ``seconds`` of a corpus clip."""
    import measure  # the corpus measurement helper, numpy only

    samples, sample_rate = measure.load_clip(clip_id)
    if sample_rate != SAMPLE_RATE:
        raise ValueError(f"corpus clip {clip_id} is not at {SAMPLE_RATE} Hz")
    count = int(seconds * SAMPLE_RATE)
    return np.asarray(samples[:count], dtype=np.float64)


# ---------------------------------------------------------------------------
# the probes
# ---------------------------------------------------------------------------
def probe_tone_1k(duration_s: float = 0.5) -> Probe:
    """A 1 kHz tone at -6 dBFS peak, identical in both channels."""
    n = int(duration_s * SAMPLE_RATE)
    return Probe("tone_1k", stereo(tone(1000.0, 0.501187, n)))


def probe_hum_tone(duration_s: float = 0.5) -> Probe:
    """The corpus hum clip: a 1 kHz tone over 50 Hz mains hum."""
    return Probe("hum_tone", _corpus_clip("hum50", duration_s),
                 note="corpus clip 'hum50' — 1 kHz tone, 50 Hz hum, 100 Hz harmonic")


def probe_sibilance_tone(duration_s: float = 1.0) -> Probe:
    """The corpus sibilance clip: a 1 kHz tone with a 6 kHz burst."""
    return Probe("sibilance_tone", _corpus_clip("sibilance", duration_s),
                 note="corpus clip 'sibilance' — 1 kHz tone, 6 kHz burst at 0.6 s")


def probe_click_tone(duration_s: float = 0.8) -> Probe:
    """The corpus click clip: a 480 Hz bed with single sample impulses."""
    return Probe("click_tone", _corpus_clip("clicks", duration_s),
                 note="corpus clip 'clicks' — 480 Hz bed, six impulses")


def probe_crackle_tone(duration_s: float = 0.5) -> Probe:
    """A 480 Hz bed with dense crackle above it."""
    n = int(duration_s * SAMPLE_RATE)
    bed = tone(480.0, 0.05, n)
    rng = np.random.default_rng(SEED + 7)
    positions = rng.integers(0, n - 1, size=20).tolist()
    crackle = clicks(n, positions, [0.3] * len(positions))
    return Probe("crackle_tone", stereo(bed + crackle),
                 note="synthetic — 480 Hz bed (-26 dBFS) with 20 crackle impulses (-10 dBFS)")


def probe_plosive_vowel(duration_s: float = 0.8) -> Probe:
    """The corpus plosive clip: a vowel with a low frequency burst."""
    return Probe("plosive_vowel", _corpus_clip("plosive", duration_s),
                 note="corpus clip 'plosive' — vowel tone with an 80 Hz burst at 0.5 s")


def probe_reverb_tail(duration_s: float = 1.0) -> Probe:
    """The corpus reverberation clip: a 5 ms starter and a 1.2 s tail."""
    return Probe("reverb_tail", _corpus_clip("reverb", duration_s),
                 note="corpus clip 'reverb' — 440 Hz starter at 0.2 s, 1.2 s tail")


def probe_clipped_tone(duration_s: float = 0.5) -> Probe:
    """The corpus clipping clip: a 1 kHz sine driven into the rails."""
    return Probe("clipped_tone", _corpus_clip("clipped", duration_s),
                 note="corpus clip 'clipped' — 1 kHz sine, 70.8 % of the samples at full scale")


def probe_voice_hiss(duration_s: float = 1.0) -> Probe:
    """A synthetic voice under a white hiss bed."""
    n = int(duration_s * SAMPLE_RATE)
    voice = synthetic_voice(n)
    hiss = scale_rms(white(n, SEED + 11), -45.0)
    return Probe("voice_hiss", stereo(voice + hiss), learn=stereo(hiss),
                 note="synthetic — voice (110 Hz, moving) under white hiss at -45 dBFS")


def probe_spectral_voice(duration_s: float = 1.0) -> Probe:
    """A close voiced stack under a low passed hiss.

    The partials sit 120 Hz apart, which is the case the denoiser is known
    to get wrong: the partials are not separable at the analysis resolution
    of the default quality preset.
    """
    n = int(duration_s * SAMPLE_RATE)
    t = np.arange(n, dtype=np.float64) / float(SAMPLE_RATE)
    voice = np.zeros(n, dtype=np.float64)
    for k in range(1, 13):
        voice += (1.0 / k) * np.sin(2.0 * math.pi * 120.0 * k * t)
    voice = scale_rms(voice, -18.0)
    envelope = 0.5 + 0.5 * np.sin(2.0 * math.pi * 2.0 * t)
    voice = voice * envelope
    hiss = scale_rms(band_noise(200.0, 12000.0, n, seed=SEED + 67), -40.0)
    return Probe("spectral_voice", stereo(voice + hiss), learn=stereo(hiss),
                 note="synthetic — close voiced stack (120 Hz, 12 partials) under a hiss bed")


def probe_tone_hiss(duration_s: float = 1.0) -> Probe:
    """A 1 kHz tone over a stationary white bed."""
    n = int(duration_s * SAMPLE_RATE)
    wanted = tone(1000.0, 0.501187, n)
    bed = scale_rms(white(n, SEED + 13), -40.0)
    return Probe("tone_hiss", stereo(wanted + bed), learn=stereo(bed),
                 note="synthetic — 1 kHz tone over a stationary white bed")


def probe_rustle_bed(duration_s: float = 0.5) -> Probe:
    """A 1 kHz tone over a quiet broadband friction bed."""
    n = int(duration_s * SAMPLE_RATE)
    wanted = tone(1000.0, 0.501187, n)
    bed = scale_rms(white(n, SEED + 17), -50.0)
    return Probe("rustle_bed", stereo(wanted + bed),
                 note="synthetic — 1 kHz tone over a -50 dBFS friction bed")


def probe_wind_rumble(duration_s: float = 1.5) -> Probe:
    """A wanted low note under a gusting low frequency rumble."""
    n = int(duration_s * SAMPLE_RATE)
    wanted = tone(200.0, 0.251188, n) + tone(1000.0, 0.251188, n)
    rumble = scale_rms(band_noise(30.0, 500.0, n, seed=SEED + 19), -22.0)
    t = np.arange(n, dtype=np.float64) / float(SAMPLE_RATE)
    gust = 0.05 + 0.95 * (0.5 - 0.5 * np.cos(2.0 * math.pi * 0.7 * t))
    return Probe("wind_rumble", stereo(wanted + rumble * gust),
                 note="synthetic — a 200 Hz note and a 1 kHz tone under a 30-500 Hz "
                      "rumble that gusts at 0.7 Hz")


def probe_breath_burst(duration_s: float = 0.5) -> Probe:
    """A 1 kHz tone with a broadband breath between 0.2 s and 0.45 s."""
    n = int(duration_s * SAMPLE_RATE)
    wanted = tone(1000.0, 0.501187, n)
    burst = scale_rms(band_noise(2000.0, 8000.0, n, seed=SEED + 23), -12.0)
    envelope = np.zeros(n, dtype=np.float64)
    i0, i1 = int(0.2 * SAMPLE_RATE), int(0.45 * SAMPLE_RATE)
    envelope[i0:i1] = 1.0
    return Probe("breath_burst", stereo(wanted + burst * envelope),
                 note="synthetic — 1 kHz tone with a 2-8 kHz breath burst (-12 dBFS)")


def probe_bleed_pair(duration_s: float = 0.5) -> Probe:
    """A target with a bleed source and the reference that carries it."""
    n = int(duration_s * SAMPLE_RATE)
    bleed = tone(500.0, 0.158489, n)
    wanted = tone(1000.0, 0.501187, n)
    target = wanted + bleed
    out = np.zeros((n, 2), dtype=np.float64)
    out[:, 0] = target
    out[:, 1] = bleed
    return Probe("bleed_pair", out,
                 learn=out.copy(),
                 note="synthetic — ch0 carries a 1 kHz programme and a 500 Hz bleed, ch1 the bleed")


def probe_spatial_pair(duration_s: float = 1.0) -> Probe:
    """A voice in the middle and a hiss on the sides.

    The voice is the **same** in both channels, so it lives in the middle;
    the hiss is **independent** in the two channels, so it lives on the sides.
    A suppressor that removes what the channels do not share takes the hiss and
    leaves the voice.
    """
    n = int(duration_s * SAMPLE_RATE)
    voice = harmonic_stack(130.0, n, count=12, top_hz=3000.0)
    voice = scale_rms(voice, -18.0)
    rng = np.random.default_rng(20261022)
    hiss = 0.02 * rng.standard_normal((n, 2))
    out = voice[:, None] + hiss
    return Probe("spatial_pair", out,
                 note="synthetic — a 130 Hz voice in the middle, independent hiss on the sides")


def probe_guitar_squeak(duration_s: float = 0.5) -> Probe:
    """A guitar-like harmonic stack with a narrow string squeak."""
    n = int(duration_s * SAMPLE_RATE)
    guitar = harmonic_stack(196.0, n, count=8, top_hz=3000.0)
    guitar = scale_rms(guitar, -20.0)
    squeak = tone(3000.0, 0.031623, n)
    envelope = np.zeros(n, dtype=np.float64)
    i0, i1 = int(0.2 * SAMPLE_RATE), int(0.32 * SAMPLE_RATE)
    envelope[i0:i1] = 1.0
    return Probe("guitar_squeak", stereo(guitar + squeak * envelope),
                 note="synthetic — guitar stack (196 Hz) with a 3 kHz squeak at 0.2 s")


def probe_ambience_pair(duration_s: float = 0.5) -> Probe:
    """A target recording with a noise floor, and the reference ambience.

    The target carries a programme that comes and goes, a loud noise floor of
    its own and the reference carries a much quieter room tone.  The module
    has to gate the target's own floor out and put the reference's room tone
    underneath the programme.
    """
    n = int(duration_s * SAMPLE_RATE)
    t = np.arange(n, dtype=np.float64) / float(SAMPLE_RATE)
    programme = tone(1000.0, 0.501187, n)
    gate = np.zeros(n, dtype=np.float64)
    for start in (0.0, 0.25):
        i0 = int(start * SAMPLE_RATE)
        i1 = min(n, i0 + int(0.12 * SAMPLE_RATE))
        gate[i0:i1] = 1.0
    programme = programme * gate
    floor = scale_rms(white(n, SEED + 29), -30.0)
    reference = scale_rms(white(n, SEED + 71), -50.0)
    target = programme + floor
    out = np.zeros((n, 2), dtype=np.float64)
    out[:, 0] = target
    out[:, 1] = reference
    return Probe("ambience_pair", out, learn=out[:, 1:2].copy(),
                 note="synthetic — ch0 carries a 1 kHz programme over a -30 dBFS floor, "
                      "ch1 a -50 dBFS reference room tone")


def probe_dialogue_mix(duration_s: float = 1.0) -> Probe:
    """A voice in the first half, a reverberant tail in the second."""
    n = int(duration_s * SAMPLE_RATE)
    voice = synthetic_voice(n)
    noise = scale_rms(white(n, SEED + 41), -45.0)
    tail = np.zeros(n, dtype=np.float64)
    start = int(0.5 * SAMPLE_RATE)
    burst = scale_rms(white(n - start, SEED + 37), -30.0)
    kernel = np.exp(-np.arange(6000) / 1500.0)
    tail[start:] = np.convolve(burst, kernel, mode="full")[: n - start]
    return Probe("dialogue_mix", stereo(voice + tail * 0.5 + noise),
                 note="synthetic — voice, a reverberant tail from 0.5 s and a noise bed")


def probe_deconstruct_mix(duration_s: float = 0.5) -> Probe:
    """A tonal signal, a noise bed and a transient."""
    n = int(duration_s * SAMPLE_RATE)
    tonal = scale_rms(harmonic_stack(220.0, n, count=8, top_hz=2000.0), -20.0)
    noisy = scale_rms(white(n, SEED + 31), -35.0)
    transient = clicks(n, [int(0.25 * SAMPLE_RATE)], [0.5])
    return Probe("deconstruct_mix", stereo(tonal + noisy + transient),
                 note="synthetic — tonal stack, noise bed and one transient")


def probe_dialogue_mix(duration_s: float = 1.0) -> Probe:
    """A voice with a reverberant tail and a noise bed."""
    n = int(duration_s * SAMPLE_RATE)
    voice = scale_rms(harmonic_stack(120.0, n, count=16, top_hz=3000.0), -20.0)
    tail = scale_rms(white(n, SEED + 37), -30.0)
    tail = np.convolve(tail, np.exp(-np.arange(4000) / 900.0), mode="full")[:n]
    noise = scale_rms(white(n, SEED + 41), -45.0)
    return Probe("dialogue_mix", stereo(voice + tail * 0.3 + noise),
                 note="synthetic — voice stack, reverberant tail and a noise bed")


def probe_music_mix(duration_s: float = 0.5) -> Probe:
    """A vocal, a bass line and a drum-like transient."""
    n = int(duration_s * SAMPLE_RATE)
    vocal = scale_rms(harmonic_stack(440.0, n, count=8, top_hz=4000.0), -22.0)
    bass = scale_rms(tone(110.0, 0.1, n), -18.0)
    rng = np.random.default_rng(SEED + 43)
    drum = clicks(n, [int(0.15 * SAMPLE_RATE), int(0.35 * SAMPLE_RATE)], [0.4, 0.35])
    return Probe("music_mix", stereo(vocal + bass + drum),
                 note="synthetic — vocal stack, bass line and two drum hits")


def probe_wow_tone(duration_s: float = 4.0) -> Probe:
    """A 440 Hz tone with a 6 % pitch modulation at 1 Hz, over a noise floor.

    The probe is four seconds long, so that the sidebands of the modulation
    (1 Hz away from the carrier) are resolved by the measurement.
    """
    n = int(duration_s * SAMPLE_RATE)
    t = np.arange(n, dtype=np.float64) / float(SAMPLE_RATE)
    phase = 2.0 * math.pi * 440.0 * t + 0.06 * np.sin(2.0 * math.pi * 1.0 * t)
    carrier = 0.5 * np.sin(phase)
    floor = scale_rms(white(n, SEED + 73), -60.0)
    return Probe("wow_tone", stereo(carrier + floor),
                 note="synthetic — 440 Hz tone with 6 % wow at 1 Hz over a -60 dBFS floor")


def probe_dropout_tone(duration_s: float = 0.5) -> Probe:
    """A 1 kHz tone with two isolated dropouts."""
    n = int(duration_s * SAMPLE_RATE)
    wanted = tone(1000.0, 0.501187, n)
    damaged = wanted.copy()
    for start, length in ((int(0.15 * SAMPLE_RATE), 64), (int(0.35 * SAMPLE_RATE), 96)):
        damaged[start : start + length] = 0.0
    return Probe("dropout_tone", stereo(damaged),
                 note="synthetic — 1 kHz tone with two dropouts (64 and 96 samples)")


def probe_stereo_tone(duration_s: float = 0.5) -> Probe:
    """A stereo pair: a 440 Hz tone centred and a 220 Hz tone hard panned left."""
    n = int(duration_s * SAMPLE_RATE)
    centred = tone(440.0, 0.501187, n)
    panned = tone(220.0, 0.501187, n)
    out = np.zeros((n, 2), dtype=np.float64)
    out[:, 0] = centred + panned
    out[:, 1] = centred
    return Probe("stereo_tone", out,
                 note="synthetic — a centred 440 Hz tone and a 220 Hz tone "
                      "in the left channel only")


def probe_stereo_corpus(duration_s: float = 0.5) -> Probe:
    """The corpus channel clips: a centred tone and a side-only tone."""
    mid = _corpus_clip("stereo_mid", duration_s)
    side = _corpus_clip("stereo_side", duration_s)
    out = np.zeros((mid.shape[0], 2), dtype=np.float64)
    out[:, 0] = mid[:, 0] + side[:, 0]
    out[:, 1] = mid[:, 1] + side[:, 1]
    return Probe("stereo_corpus", out,
                 note="corpus clips 'stereo_mid' and 'stereo_side' summed")


def probe_silence(duration_s: float = 0.5) -> Probe:
    """The corpus silence clip."""
    return Probe("silence", _corpus_clip("silence", duration_s),
                 note="corpus clip 'silence'")


def probe_music_like(duration_s: float = 1.0) -> Probe:
    """A music-like signal for the classifier."""
    n = int(duration_s * SAMPLE_RATE)
    voice = scale_rms(harmonic_stack(220.0, n, count=10, top_hz=3500.0), -20.0)
    noise = scale_rms(white(n, SEED + 47), -40.0)
    return Probe("music_like", stereo(voice + noise),
                 note="synthetic — music-like stack for the content classifier")


def probe_repeated_phrase(duration_s: float = 1.0) -> Probe:
    """A phrase that repeats, for the similarity search."""
    n = int(duration_s * SAMPLE_RATE)
    phrase = harmonic_stack(330.0, n // 2, count=8, top_hz=3000.0)
    phrase = scale_rms(phrase, -20.0)
    out = np.concatenate([phrase, phrase])
    return Probe("repeated_phrase", stereo(out),
                 note="synthetic — the same phrase twice")


def probe_repair_mix(duration_s: float = 1.0) -> Probe:
    """A programme with all three defects the repair assistant looks for."""
    n = int(duration_s * SAMPLE_RATE)
    programme = synthetic_voice(n)
    hum = scale_rms(tone(50.0, 0.1, n) + tone(100.0, 0.05, n), -25.0)
    bed = scale_rms(white(n, SEED + 53), -25.0)
    click = clicks(n, [int(0.2 * SAMPLE_RATE)], [0.4])
    out = stereo(programme + hum + bed + click)
    return Probe("repair_mix", out, learn=out.copy(),
                 note="synthetic — a voice with 50 Hz hum, a -25 dBFS noise bed and a click")


def probe_band_limited(duration_s: float = 0.5) -> Probe:
    """A recording whose top end was lost: two tones, low passed at 2.5 kHz."""
    n = int(duration_s * SAMPLE_RATE)
    source = tone(1000.0, 0.501187, n) + tone(2000.0, 0.158489, n)
    spectrum = np.fft.rfft(source)
    freqs = np.fft.rfftfreq(n, 1.0 / float(SAMPLE_RATE))
    spectrum[freqs > 2500.0] = 0.0
    out = np.fft.irfft(spectrum, n)
    return Probe("band_limited", stereo(out), learn=stereo(out),
                 note="synthetic — 1 kHz and 2 kHz tones, low passed at 2.5 kHz")


def probe_eq_tones(duration_s: float = 0.5) -> Probe:
    """A 1 kHz tone and a 5 kHz tone, for the equaliser."""
    n = int(duration_s * SAMPLE_RATE)
    out = tone(1000.0, 0.501187, n) + tone(5000.0, 0.316228, n)
    return Probe("eq_tones", stereo(out),
                 note="synthetic — 1 kHz and 5 kHz tones")


def probe_eq_target(duration_s: float = 0.5) -> Probe:
    """A dull target, with a brighter reference of the same material."""
    n = int(duration_s * SAMPLE_RATE)
    target = tone(1000.0, 0.501187, n) + tone(3000.0, 0.125893, n)
    reference = tone(1000.0, 0.501187, n) + tone(3000.0, 0.501187, n)
    return Probe("eq_target", stereo(target), learn=stereo(reference),
                 note="synthetic — a 1 kHz tone with a -12 dB 3 kHz partial, and a "
                      "reference that carries the same material 12 dB brighter")


def probe_band_noise(duration_s: float = 0.5) -> Probe:
    """A programme tone in the middle band over a noise bed in the low band.

    The two live in bands of their own, so the band restoration can be told
    apart from the programme: the noise bed is in the first band of the
    crossover (below 200 Hz), the programme is in the second one (200 Hz to
    2 kHz).  The noise is also the material the module learns, so the floor
    of the band is the level the bed really has.
    """
    n = int(duration_s * SAMPLE_RATE)
    programme = scale_rms(tone(1000.0, 0.501187, n), -12.0)
    bed = scale_rms(band_noise(40.0, 180.0, n, seed=SEED + 83), -30.0)
    return Probe("band_noise", stereo(programme + bed), learn=stereo(bed),
                 note="synthetic: a 1 kHz programme (-12 dBFS) over a 40-180 Hz noise "
                      "bed (-30 dBFS), the bed is also the learn material")


def probe_voice_room(duration_s: float = 1.2) -> Probe:
    """A voice in a room: the wanted voice and the field the room adds.

    The room is the one of the golden corpus clip ``reverb``: the direct
    sound plus exponentially decaying noise, so the reverberation time of
    the room is exactly the number the recipe asks for.  The voice is the
    synthetic voice of this file, which has the pauses between the
    syllables that the module needs to hear the room in.  The dry voice is
    carried along as the clean reference.
    """
    n = int(duration_s * SAMPLE_RATE)
    voice = synthetic_voice(n)
    rt60 = 0.9
    rng = np.random.default_rng(SEED + 89)
    tau = rt60 / math.log(1000.0)
    times = np.arange(n, dtype=np.float64) / float(SAMPLE_RATE)
    room = rng.standard_normal(n) * np.exp(-times / tau) * 0.01
    room[0] = 1.0
    wet = np.convolve(voice, room)[:n]
    if wet.size < n:
        wet = np.concatenate([wet, np.zeros(n - wet.size, dtype=np.float64)])
    return Probe("voice_room", stereo(wet), clean=stereo(voice),
                 note="synthetic: the synthetic voice in a room of RT60 0.9 s, the "
                      "dry voice is the clean reference")


#: The probes whose material comes from the golden corpus, by name.  The
#: other probes are synthetic signals built for the purpose.
CORPUS_PROBES: tuple[str, ...] = (
    "hum_tone",
    "sibilance_tone",
    "click_tone",
    "plosive_vowel",
    "reverb_tail",
    "clipped_tone",
    "stereo_corpus",
    "silence",
)

#: Every probe of the survey, by name.
PROBES: dict[str, Callable[..., Probe]] = {
    "tone_1k": probe_tone_1k,
    "hum_tone": probe_hum_tone,
    "sibilance_tone": probe_sibilance_tone,
    "click_tone": probe_click_tone,
    "crackle_tone": probe_crackle_tone,
    "plosive_vowel": probe_plosive_vowel,
    "reverb_tail": probe_reverb_tail,
    "clipped_tone": probe_clipped_tone,
    "voice_hiss": probe_voice_hiss,
    "spectral_voice": probe_spectral_voice,
    "tone_hiss": probe_tone_hiss,
    "rustle_bed": probe_rustle_bed,
    "wind_rumble": probe_wind_rumble,
    "breath_burst": probe_breath_burst,
    "bleed_pair": probe_bleed_pair,
    "spatial_pair": probe_spatial_pair,
    "guitar_squeak": probe_guitar_squeak,
    "ambience_pair": probe_ambience_pair,
    "deconstruct_mix": probe_deconstruct_mix,
    "dialogue_mix": probe_dialogue_mix,
    "music_mix": probe_music_mix,
    "wow_tone": probe_wow_tone,
    "dropout_tone": probe_dropout_tone,
    "stereo_tone": probe_stereo_tone,
    "stereo_corpus": probe_stereo_corpus,
    "silence": probe_silence,
    "music_like": probe_music_like,
    "repeated_phrase": probe_repeated_phrase,
    "repair_mix": probe_repair_mix,
    "band_limited": probe_band_limited,
    "eq_tones": probe_eq_tones,
    "eq_target": probe_eq_target,
    "band_noise": probe_band_noise,
    "voice_room": probe_voice_room,
}


def probe_names() -> list[str]:
    """Return the names of every probe, sorted."""
    return sorted(PROBES)


def corpus_probe_names() -> list[str]:
    """Return the names of the probes whose material comes from the corpus."""
    return [name for name in CORPUS_PROBES if name in PROBES]


def build_probe(name: str, duration_s: float | None = None) -> Probe:
    """Build a probe by name.

    Parameters
    ----------
    name:
        The name of the probe.
    duration_s:
        The length of the probe; the probe's own default is used when it is
        omitted.

    Returns
    -------
    Probe
        The probe.
    """
    builder = PROBES.get(name)
    if builder is None:
        raise KeyError(f"unknown probe {name!r}")
    if duration_s is None:
        return builder()
    return builder(duration_s)
