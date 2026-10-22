"""
aurcore.modules.clip_classifier — content classification by instrument family.

A measurement module: it analyses the audio and reports the dominant source
family plus a confidence figure.  The audio itself passes through untouched.

The classifier is a prototype-nearest-neighbour model over a hand-designed
feature space, with a bank of synthetic reference exemplars standing in for
trained weights.  The seam is deliberate: :meth:`_extract_features` and
:attr:`PROTOTYPES` are the two places a learned model would replace, and
nothing else in the module would change.
"""
from __future__ import annotations

import numpy as np

from ..modules.base import Module
from ..params import ParamDesc
from ..registry import register

CLASSES: tuple[str, ...] = (
    "Piano",
    "Bass",
    "Drums",
    "Guitar",
    "Vocals",
    "Synth",
    "Strings",
    "Woodwind",
    "Brass",
    "Organ",
)

# Feature order used everywhere below.
FEATURES: tuple[str, ...] = (
    "centroid",        # spectral centroid, normalised 0..1 of Nyquist
    "flatness",        # geometric/arithmetic mean ratio (0 tonal .. 1 noisy)
    "low_ratio",       # energy below 250 Hz
    "high_ratio",      # energy above 6 kHz
    "harmonic_ratio",  # energy on harmonic peaks vs total
    "harmonic_count",  # number of salient harmonics (normalised)
    "attack",          # 10-90 % rise time, seconds (log scaled)
    "decay",           # energy slope after the peak (per second, log scaled)
    "crest",           # peak / rms
    "flux",            # mean positive spectral flux
    "zcr",             # zero-crossing rate, normalised
    "sustain",         # fraction of frames above -20 dB of the peak frame
)


def _synth_exemplars(sample_rate: int = 22050, seconds: float = 2.0) -> dict[str, np.ndarray]:
    """Build one synthetic reference per class.

    These are deliberately simple physical models — enough to separate the
    broad families (a bass guitar is not a drum kit), not enough to be a
    trained model.
    """
    rng = np.random.default_rng(20240917)
    n = int(sample_rate * seconds)
    t = np.arange(n, dtype=np.float64) / sample_rate
    out: dict[str, np.ndarray] = {}

    def noise() -> np.ndarray:
        return rng.standard_normal(n)

    def harmonic(f0: float, amps, decay: float | None = None, phase=None) -> np.ndarray:
        sig = np.zeros(n)
        for k, a in enumerate(amps, start=1):
            f = f0 * k
            if f > sample_rate * 0.45:
                break
            env = np.exp(-t * decay) if decay else np.ones(n)
            sig += a * env * np.sin(2 * np.pi * f * t + (phase or 0.0))
        return sig

    # Piano: inharmonic-ish rich partials, fast decay, strong attack
    p = np.zeros(n)
    for k, a in enumerate([1.0, 0.6, 0.42, 0.3, 0.22, 0.16, 0.11, 0.08, 0.05, 0.04], 1):
        f = 220 * k * (1.0 + 0.0004 * k * k)
        if f > sample_rate * 0.45:
            break
        p += a * np.exp(-t * (2.2 + 0.35 * k)) * np.sin(2 * np.pi * f * t)
    p[: int(0.004 * sample_rate)] *= np.linspace(0, 1, int(0.004 * sample_rate))
    out["Piano"] = p

    # Bass: low fundamental, few harmonics, sustained
    out["Bass"] = harmonic(55, [1.0, 0.35, 0.18, 0.08, 0.04], decay=0.4)

    # Drums: broadband transients, no harmonic structure
    d = np.zeros(n)
    for hit in (0.05, 0.55, 1.05, 1.55):
        i0 = int(hit * sample_rate)
        seg = rng.standard_normal(min(n - i0, int(0.12 * sample_rate)))
        seg *= np.exp(-np.arange(len(seg)) / (0.02 * sample_rate))
        d[i0 : i0 + len(seg)] += seg * 0.9
    d += 0.25 * np.sin(2 * np.pi * 60 * t) * np.exp(-t * 0.5)
    out["Drums"] = d

    # Guitar: plucked string, pick transient, moderate decay
    g = harmonic(196, [1.0, 0.5, 0.3, 0.2, 0.12], decay=1.4)
    g[: int(0.003 * sample_rate)] += 0.5 * rng.standard_normal(int(0.003 * sample_rate))
    out["Guitar"] = g

    # Vocals: mid-band harmonic with formant emphasis, vibrato, breath
    v = np.zeros(n)
    vib = 1.0 + 0.012 * np.sin(2 * np.pi * 5.2 * t)
    for k, a in enumerate([1.0, 0.72, 0.55, 0.3, 0.2, 0.12, 0.07], 1):
        v += a * np.sin(2 * np.pi * 210 * vib * k * t + k * 0.4)
    for fc, g_ in ((700, 1.6), (1200, 1.1), (2600, 0.7)):
        from numpy.fft import rfft, irfft

        spec = rfft(v)
        f = np.fft.rfftfreq(len(v), 1 / sample_rate)
        w = 1.0 + (g_ - 1.0) * np.exp(-0.5 * ((f - fc) / 320.0) ** 2)
        v = irfft(spec * w, n=len(v))
    v += 0.02 * noise()
    out["Vocals"] = v

    # Synth: sustained, stable, saw-like
    out["Synth"] = harmonic(110, [1.0, 0.5, 0.33, 0.25, 0.2, 0.16, 0.14, 0.12], decay=0.02)

    # Strings: slow attack, many harmonics
    s = harmonic(147, [1.0, 0.6, 0.45, 0.35, 0.28, 0.22, 0.18, 0.15, 0.12, 0.1, 0.08], decay=0.25)
    s *= np.clip(t / 0.35, 0, 1)
    out["Strings"] = s

    # Woodwind: few harmonics, breathy, mid-register
    w = harmonic(523, [1.0, 0.22, 0.08, 0.03])
    w += 0.12 * noise()
    w *= np.clip(t / 0.12, 0, 1)
    out["Woodwind"] = w

    # Brass: strong upper harmonics, brassy
    out["Brass"] = harmonic(233, [1.0, 0.75, 0.6, 0.5, 0.42, 0.35, 0.3, 0.25], decay=0.1)

    # Organ: several simultaneous fundamentals
    o = np.zeros(n)
    for f0, a in ((130.8, 0.7), (196.0, 0.5), (261.6, 0.4), (329.6, 0.28), (392.0, 0.2)):
        o += a * harmonic(f0, [1.0, 0.3, 0.12], decay=0.01)
    out["Organ"] = o

    return out


def _frame_features(spec_db: np.ndarray, freqs: np.ndarray, sr: int) -> np.ndarray:
    """Turn one magnitude frame into the feature vector."""
    mag = np.maximum(10.0 ** (spec_db / 20.0), 1e-12)
    total = float(mag.sum()) + 1e-12
    p = mag / total

    centroid = float((p * freqs).sum() / (sr * 0.5))
    geo = float(np.exp(np.mean(np.log(mag + 1e-12))))
    arith = float(np.mean(mag))
    flatness = geo / (arith + 1e-12)

    low = float(mag[freqs < 250].sum() / total)
    high = float(mag[freqs > 6000].sum() / total)

    # harmonic ratio: energy on integer multiples of the estimated f0
    f0 = _estimate_f0(mag, freqs, sr)
    if f0 > 0:
        harm_mask = np.zeros_like(mag, dtype=bool)
        k = 1
        while k * f0 < sr * 0.5:
            idx = int(round(k * f0 / (sr * 0.5) * len(mag)))
            if 0 <= idx < len(mag):
                lo = max(0, idx - 1)
                harm_mask[lo : idx + 2] = True
            k += 1
        harmonic_ratio = float(mag[harm_mask].sum() / total)
        harmonic_count = float(min(k - 1, 20) / 20.0)
    else:
        harmonic_ratio = 0.0
        harmonic_count = 0.0

    return np.array(
        [
            centroid,
            flatness,
            low,
            high,
            harmonic_ratio,
            harmonic_count,
            0.0,  # attack, filled per-clip
            0.0,  # decay, filled per-clip
            0.0,  # crest, filled per-clip
            0.0,  # flux, filled per-clip
            0.0,  # zcr, filled per-clip
            0.0,  # sustain, filled per-clip
        ]
    )


def _estimate_f0(mag: np.ndarray, freqs: np.ndarray, sr: int) -> float:
    """Coarse fundamental estimate from the harmonic comb of the spectrum."""
    band = (freqs > 60) & (freqs < 1200)
    if not band.any():
        return 0.0
    sub = mag[band]
    f = freqs[band]
    best, best_score = 0.0, -1.0
    for f0 in np.arange(65.0, 600.0, 2.0):
        score = 0.0
        k = 1
        while k * f0 < 1200:
            idx = int(np.argmin(np.abs(f - k * f0)))
            score += sub[idx]
            k += 1
        score /= k
        if score > best_score:
            best_score, best = score, float(f0)
    return best


class _Analyzer:
    """Reusable feature extractor shared by the prototypes and the module."""

    def __init__(self, sample_rate: int = 22050):
        self.sr = sample_rate
        self.n_fft = 2048
        self.hop = 512

    def spectrogram(self, x: np.ndarray):
        from numpy.fft import rfft

        win = np.hanning(self.n_fft)
        n_frames = max(1, 1 + (len(x) - self.n_fft) // self.hop)
        freqs = np.fft.rfftfreq(self.n_fft, 1 / self.sr)
        frames = np.empty((n_frames, len(freqs)))
        for i in range(n_frames):
            seg = x[i * self.hop : i * self.hop + self.n_fft]
            if len(seg) < self.n_fft:
                seg = np.pad(seg, (0, self.n_fft - len(seg)))
            frames[i] = np.abs(rfft(seg * win))
        return frames, freqs

    def features(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64).ravel()
        if len(x) < self.n_fft * 2:
            x = np.pad(x, (0, max(0, self.n_fft * 2 - len(x))))
        frames, freqs = self.spectrogram(x)
        rms = np.sqrt(np.mean(frames**2, axis=1) + 1e-18)
        peak_i = int(np.argmax(rms))
        rms_n = rms / (rms[peak_i] + 1e-18)

        per_frame = np.array([_frame_features(20 * np.log10(frames[i] + 1e-12), freqs, self.sr)
                              for i in range(len(frames))])

        # attack: frames from 10 % to 90 % of the peak, in seconds
        hi = np.where(rms_n >= 0.9)[0]
        lo = np.where(rms_n >= 0.1)[0]
        if len(hi) and len(lo):
            a0, a1 = int(lo[0]), int(hi[0])
            attack = max(0, (a1 - a0) * self.hop / self.sr)
        else:
            attack = 0.05
        attack = float(np.log1p(attack * 20.0) / np.log1p(4.0))

        # decay: mean log-slope of the envelope after the peak
        tail = np.log10(rms_n[peak_i:] + 1e-6)
        decay = float(np.polyfit(np.arange(len(tail)), tail, 1)[0]) if len(tail) > 3 else 0.0
        decay = float(np.clip(-decay / 3.0, 0.0, 1.0))

        crest = float(peak_i and (rms[peak_i] / (np.mean(rms) + 1e-18)) / 10.0)
        crest = float(np.clip(crest, 0.0, 1.0))

        flux = np.mean(np.maximum(np.diff(frames, axis=0), 0.0), axis=1)
        flux_f = float(np.clip(np.log1p(flux.mean() * 1e4) / 12.0, 0.0, 1.0))

        zcr = float(np.mean(np.abs(np.diff(np.sign(x))) > 0) * 0.5)

        sustain = float(np.mean(rms_n > 0.1))

        f = per_frame.mean(axis=0)
        f[6] = attack
        f[7] = decay
        f[8] = crest
        f[9] = flux_f
        f[10] = zcr
        f[11] = sustain
        return f


def _normalise(f: np.ndarray) -> np.ndarray:
    """Scale each feature into a comparable 0..1 range."""
    f = np.asarray(f, dtype=np.float64).copy()
    f[0] = float(np.clip(f[0] / 0.5, 0, 1))          # centroid
    f[1] = float(np.clip(f[1], 0, 1))                # flatness
    f[2] = float(np.clip(f[2] * 2.0, 0, 1))          # low ratio
    f[3] = float(np.clip(f[3] * 3.0, 0, 1))          # high ratio
    f[4] = float(np.clip(f[4], 0, 1))                # harmonic ratio
    f[5] = float(np.clip(f[5], 0, 1))                # harmonic count
    f[6] = float(np.clip(f[6], 0, 1))                # attack
    f[7] = float(np.clip(f[7], 0, 1))                # decay
    f[8] = float(np.clip(f[8], 0, 1))                # crest
    f[9] = float(np.clip(f[9], 0, 1))                # flux
    f[10] = float(np.clip(f[10] * 8.0, 0, 1))        # zcr
    f[11] = float(np.clip(f[11], 0, 1))              # sustain
    return f


@register
class ClipClassifier(Module):
    """Classifies the dominant source family in a clip and reports a confidence."""

    code = "AUR_CLIPCLASSIFIER"
    display_name = "Clip Classifier"
    category = "Measurement"
    edition = "standard"
    element = "ClipClassifier"
    analysis = True

    PROTOTYPES: dict[str, np.ndarray] = {}
    _built = False

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._analyzer = _Analyzer(22050)
        self._resample_ratio = self._analyzer.sr / float(sample_rate)
        self.detected_class = CLASSES[0]
        self.confidence = 0.0
        self.match_strength = 0.0
        self._ensure_prototypes()

    @classmethod
    def _ensure_prototypes(cls) -> None:
        if cls._built:
            return
        an = _Analyzer(22050)
        for name, sig in _synth_exemplars(an.sr).items():
            cls.PROTOTYPES[name] = _normalise(an.features(sig))
        cls._built = True

    # -- contract -------------------------------------------------------------
    def describe(self) -> list[ParamDesc]:
        return [
            ParamDesc(name="Detected Class", type="enum", default=CLASSES[0],
                      choices=CLASSES, group="Result", automatable=False,
                      help="The source family the classifier believes dominates the clip."),
            ParamDesc(name="Confidence", type="float", default=0.0, minimum=0.0, maximum=1.0,
                      group="Result", automatable=False,
                      help="How strongly the clip matches the detected class."),
            ParamDesc(name="Similarity", type="float", default=0.0, minimum=0.0, maximum=1.0,
                      group="Result", automatable=False,
                      help="Normalised distance to the best-matching reference.",
                      aliases=("Match Strength",)),
            ParamDesc(name="Highlight Match", type="bool", default=False, group="Display",
                      help="Flag the detected class for the interface to highlight."),
            ParamDesc(name="Analysis Length", type="float", default=4.0, minimum=0.5, maximum=30.0,
                      unit="s", group="Analysis",
                      help="How much audio from the start of the clip is analysed."),
        ]

    # -- analysis -------------------------------------------------------------
    def _analyse(self, x: np.ndarray) -> tuple[str, float, float]:
        mono = np.asarray(x, dtype=np.float64)
        if mono.ndim > 1:
            mono = mono.mean(axis=1)
        want = int(self.params.get("Analysis Length") * self._analyzer.sr)
        seg = mono[:want] if len(mono) > want else mono
        if len(seg) < self._analyzer.n_fft * 2:
            return CLASSES[0], 0.0, 0.0
        if abs(self._resample_ratio - 1.0) > 1e-6:
            n = int(len(seg) * self._resample_ratio)
            seg = np.interp(
                np.linspace(0, len(seg) - 1, n),
                np.arange(len(seg)),
                seg,
            )
        feat = _normalise(self._analyzer.features(seg))

        dists = {}
        for name, proto in self.PROTOTYPES.items():
            dists[name] = float(np.linalg.norm(feat - proto))
        order = sorted(dists, key=lambda k: dists[k])
        best, second = order[0], order[1]
        d_best, d_second = dists[best], dists[second]

        # softmax over negative distance -> confidence
        scale = 0.18
        logits = np.array([-dists[k] / scale for k in CLASSES])
        logits -= logits.max()
        probs = np.exp(logits)
        probs /= probs.sum()
        confidence = float(probs[CLASSES.index(best)])

        margin = float(np.clip((d_second - d_best) / (d_second + 1e-6), 0, 1))
        strength = float(np.clip(1.0 - d_best / 2.0, 0.0, 1.0))
        return best, confidence * (0.5 + 0.5 * margin), strength

    def process(self, x: np.ndarray) -> np.ndarray:
        self.detected_class, self.confidence, self.match_strength = self._analyse(x)
        self.params.set("Detected Class", self.detected_class)
        self.params.set("Confidence", self.confidence)
        self.params.set("Similarity", self.match_strength)
        return x

    def learn(self, x: np.ndarray) -> None:
        self.detected_class, self.confidence, self.match_strength = self._analyse(x)
        self.params.set("Detected Class", self.detected_class)
        self.params.set("Confidence", self.confidence)
        self.params.set("Similarity", self.match_strength)

    def report(self) -> dict:
        return {
            "class": self.detected_class,
            "confidence": self.confidence,
            "match_strength": self.match_strength,
            "classes": list(CLASSES),
        }
