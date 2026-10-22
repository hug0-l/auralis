"""
qa.quality.harness — measurement harness for the Spectral De-Noise module.

The harness is a **scratch** artefact: it never touches the module or the
DSP layer, it only *reads* them.  It provides three things.

1. **Synthetic cases** — deterministic test signals (a tone over broadband
   noise, a tone over mains hum, a harmonic voice-like stack over hiss and a
   music-like mixture).  Every case carries the ground truth (the clean part
   and the additive noise part), so an honest SNR figure can be computed.

2. **Metrics** — noise-band energy before/after, tone level before/after,
   SNR improvement and a musical-noise figure (the temporal variance of the
   gain curve inside the noise band).

3. **A mirror of the module's gain law** (``MirrorDenoiser``) built directly
   on :mod:`aurcore.dsp.spectral` and :mod:`aurcore.dsp.stft`.  The mirror
   exposes the levers the experiment is about (over-subtraction factor,
   decision-directed alpha, gain smoothing width, noise-profile percentile and
   the noise floor) and it hands back the gain curve so the musical-noise
   metric can be measured on the *gain* rather than on a proxy.

The mirror is validated against the real module (``qa/quality/run_experiments.py``)
with the module's default settings; the two must agree to within 1e-9 before
any lever result is trusted.

Run it directly for the baseline table::

    python qa/quality/harness.py
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

import numpy as np

# ---------------------------------------------------------------------------
# bootstrap: make the repository importable when the file is run directly
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# The ground truth library and the measurement instrument live next to this
# file and are imported by their plain name.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from aurcore.dsp import spectral as _spectral            # noqa: E402
from aurcore.dsp.stft import STFT                        # noqa: E402
from aurcore.dsp.util import db_to_lin, lin_to_db        # noqa: E402
from aurcore.modules.spectral_de_noise import (           # noqa: E402
    SpectralDenoise,
    broadband_floor,
    median_filter_axis,
    soft_weight,
)

from ground_truth import (                             # noqa: E402
    CASES,
    SR,
    Case,
    case_bed_only,
    case_music_mixture,
    case_tone_hum,
    case_tone_noise,
    case_transient,
    case_voice_hiss,
    make_case,
)

__all__ = [
    "SR",
    "Case",
    "Metrics",
    "Levers",
    "MirrorDenoiser",
    "GainProbe",
    "CASES",
    "make_case",
    "measure_module",
    "measure_mirror",
    "band_power",
    "tone_amplitude",
    "tone_level",
    "partial_snr_db",
    "projection_snr",
    "gain_curve_stats",
]

SR: int = 48_000
_EPS: float = 1e-12

#: A partial counts as "wanted content" when it sits at least this far above
#: the noise in its own analysis band.  Below that level a de-noiser is
#: expected to remove it — the 1 dB "the tone must not move" rule applies
#: to the partials that are clearly above the noise.
TONE_SNR_FLOOR_DB: float = 10.0

#: The recommended configuration, as lever overrides on top of the module's
#: own defaults.  Every entry maps to one of the changes proposed in
#: ``qa/quality/REPORT.md``; none of them is applied to the module.
RECOMMENDED: dict = {
    "masking_guard": True,        # R1: the mask may not undo the reduction
    "synthesis_fix": True,        # R2: the synthesised floor uses a power
    "temporal_smooth": True,      # R3: the temporal smoothing engages at once
    "tonal_mask_ref": "learned",  # R4: the tonal mask uses the learned profile
    "tonal_ref": "learned",       # R4: the tonal SNR uses the learned profile
    "broad_gap_hz": 500.0,        # R5: the broadband window scales in Hz
    "broad_span_hz": 1500.0,
}


def _db(x: np.ndarray) -> np.ndarray:
    """Magnitude in dB (amplitude convention)."""
    return 20.0 * np.log10(np.maximum(np.asarray(x, dtype=np.float64), _EPS))


# ---------------------------------------------------------------------------
# 1. Synthetic cases
#
# The cases live in ``qa/quality/ground_truth.py``: they are the ground truth of the
# whole project, not of this study alone, so that the study, the rules and
# the acceptance test all measure against the same signals.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 2. Metrics
# ---------------------------------------------------------------------------
def band_power(x: np.ndarray, f_lo: float, f_hi: float, sr: int = SR,
               n_fft: int = 4096, hop: int = 1024) -> float:
    """Mean-square power inside a band (Welch average of the power spectrum).

    Parameters
    ----------
    x:
        Mono signal.
    f_lo, f_hi:
        Band limits in Hz.
    sr:
        Sample rate in Hz.
    n_fft, hop:
        Welch estimator geometry.

    Returns
    -------
    float
        The mean-square power of the signal inside the band.
    """
    arr = np.asarray(x, dtype=np.float64).ravel()
    if arr.size < n_fft:
        return float(np.mean(arr * arr))
    win = np.hanning(n_fft)
    views = np.lib.stride_tricks.sliding_window_view(arr, n_fft)
    frames = views[::hop][: 1 + (views.shape[0] - 1) // hop]
    spec = np.fft.rfft(frames * win, axis=-1)
    freqs = np.fft.rfftfreq(n_fft, 1.0 / float(sr))
    sel = (freqs >= float(f_lo)) & (freqs <= float(f_hi))
    if not np.any(sel):
        return 0.0
    norm = float(np.sum(win * win))
    return float(np.mean(np.abs(spec[:, sel]) ** 2, axis=0).sum() / norm)


def tone_amplitude(x: np.ndarray, freq: float, sr: int = SR) -> float:
    """Amplitude of the sinusoid at ``freq`` (single-bin DFT projection)."""
    arr = np.asarray(x, dtype=np.float64).ravel()
    n = arr.size
    if n == 0:
        return 0.0
    t = np.arange(n, dtype=np.float64) / float(sr)
    spec = np.sum(arr * np.exp(-2j * math.pi * float(freq) * t))
    return float(2.0 * abs(spec) / n)


def _frame_amplitudes(x: np.ndarray, freq: float, sr: int = SR, n_fft: int = 4096,
                      hop: int = 1024) -> np.ndarray:
    """Short-time amplitude of the partial at ``freq``, one value per frame."""
    arr = np.asarray(x, dtype=np.float64).ravel()
    if arr.size < n_fft:
        return np.zeros(0, dtype=np.float64)
    win = np.hanning(n_fft)
    frames = np.lib.stride_tricks.sliding_window_view(arr, n_fft)[::hop]
    basis = np.exp(-2j * math.pi * float(freq) * np.arange(n_fft) / float(sr))
    return np.abs((frames * win) @ basis) * (2.0 / float(np.sum(win)))


def wanted_frames(clean: np.ndarray, noise: np.ndarray, freq: float, sr: int = SR,
                  n_fft: int = 4096, hop: int = 1024,
                  snr_db: float = TONE_SNR_FLOOR_DB) -> np.ndarray | None:
    """Frames in which the partial is provably above the noise.

    A frame qualifies when the clean part's short-time amplitude at
    ``freq`` is at least ``snr_db`` above the noise part's amplitude in the
    same frame.  Only those frames carry wanted content, so only those
    frames may be used to judge whether the de-noiser moved the partial.

    Returns
    -------
    numpy.ndarray | None
        A boolean mask over the frames, or ``None`` when no frame qualifies.
    """
    c = _frame_amplitudes(clean, freq, sr, n_fft, hop)
    n = _frame_amplitudes(noise, freq, sr, n_fft, hop)
    if c.size == 0:
        return None
    mask = c >= n * (10.0 ** (float(snr_db) / 20.0))
    return mask if np.any(mask) else None


def tone_level(x: np.ndarray, freq: float, sr: int = SR, n_fft: int = 4096,
               hop: int = 1024, frames: np.ndarray | None = None) -> float:
    """Short-time level of a partial, in dB.

    The level is the median of the per-frame amplitudes.  When ``frames`` is
    given, only those frames are used — that restricts the measurement to
    the passages where the partial is actually above the noise.

    Returns
    -------
    float
        The level in dB (relative to full scale).
    """
    amps = _frame_amplitudes(x, freq, sr, n_fft, hop)
    if amps.size == 0:
        return 20.0 * math.log10(max(tone_amplitude(x, freq, sr), 1e-12))
    if frames is not None:
        sel = amps[frames]
        if sel.size:
            amps = sel
    return 20.0 * math.log10(max(float(np.median(amps)), 1e-12))


def partial_snr_db(clean: np.ndarray, noise: np.ndarray, freq: float, sr: int = SR,
                   half_width_hz: float = 30.0) -> float:
    """In-band SNR of a partial: its power against the noise's power.

    Both powers are measured in a narrow band around ``freq``, so the number
    answers the question "is this partial well above the noise it sits in?"
    rather than "is the noise perfectly incoherent with this partial?".

    Returns
    -------
    float
        The SNR in dB.
    """
    lo = float(freq) - float(half_width_hz)
    hi = float(freq) + float(half_width_hz)
    p_clean = band_power(clean, lo, hi, sr)
    p_noise = band_power(noise, lo, hi, sr)
    if p_noise <= 0.0:
        return 120.0
    return 10.0 * math.log10(max(p_clean, 1e-30) / p_noise)


def projection_snr(out: np.ndarray, clean: np.ndarray) -> float:
    """SNR of ``out`` against the known clean signal (in dB).

    The clean part is the orthogonal projection of ``out`` onto ``clean``;
    everything that is left over counts as error.
    """
    o = np.asarray(out, dtype=np.float64).ravel()
    c = np.asarray(clean, dtype=np.float64).ravel()
    denom = float(np.dot(c, c))
    if denom <= 0.0:
        return 0.0
    scale = float(np.dot(o, c)) / denom
    sig = scale * c
    err = o - sig
    return float(10.0 * math.log10(max(float(np.dot(sig, sig)),
                                       1e-30) / max(float(np.dot(err, err)), 1e-30)))


def gain_curve_stats(gain_db: np.ndarray, band: tuple[float, float], sr: int = SR,
                      n_fft: int | None = None, skip: float = 0.25) -> dict[str, float]:
    """Musical-noise statistics of a gain curve inside a band.

    The gain curve is ``(frames, bins)`` in dB.  The band is selected by
    frequency, the first ``skip`` fraction of the frames is discarded (the
    settling transient) and the temporal variance of every bin inside the
    band is computed.  A stable gain curve has a small variance; musical
    noise shows up as a large one.

    Returns
    -------
    dict
        ``var_db2`` (mean per-bin temporal variance), ``std_db`` (its square
        root), ``band_mean_var_db2`` (variance of the band-mean curve) and
        ``range_db`` (peak-to-peak of the band-mean curve).
    """
    g = np.asarray(gain_db, dtype=np.float64)
    if g.ndim != 2 or g.size == 0:
        return {"var_db2": 0.0, "std_db": 0.0, "band_mean_var_db2": 0.0, "range_db": 0.0}
    bins = g.shape[1]
    if n_fft is None:
        n_fft = 2 * (bins - 1)
    freqs = np.fft.rfftfreq(int(n_fft), 1.0 / float(sr))[:bins]
    sel = (freqs >= float(band[0])) & (freqs <= float(band[1]))
    if not np.any(sel):
        return {"var_db2": 0.0, "std_db": 0.0, "band_mean_var_db2": 0.0, "range_db": 0.0}
    start = int(g.shape[0] * float(skip))
    g = g[start:, sel]
    if g.shape[0] < 3:
        return {"var_db2": 0.0, "std_db": 0.0, "band_mean_var_db2": 0.0, "range_db": 0.0}
    per_bin_var = g.var(axis=0)
    band_mean = g.mean(axis=1)
    return {
        "var_db2": float(np.mean(per_bin_var)),
        "std_db": float(np.sqrt(np.mean(per_bin_var))),
        "band_mean_var_db2": float(np.var(band_mean)),
        "range_db": float(np.max(band_mean) - np.min(band_mean)),
    }


@dataclass
class Metrics:
    """The numbers measured for one case / one configuration."""

    name: str
    band_before_db: float = 0.0
    band_after_db: float = 0.0
    band_reduction_db: float = 0.0
    tone_before_db: dict[str, float] = field(default_factory=dict)
    tone_after_db: dict[str, float] = field(default_factory=dict)
    tone_shift_db: dict[str, float] = field(default_factory=dict)
    tone_snr_db: dict[str, float] = field(default_factory=dict)
    tone_shift_max_db: float = 0.0
    tone_shift_max_wanted_db: float = 0.0
    snr_in_db: float = 0.0
    snr_out_db: float = 0.0
    snr_improvement_db: float = 0.0
    gain_var_db2: float = 0.0
    gain_std_db: float = 0.0
    gain_band_var_db2: float = 0.0
    gain_range_db: float = 0.0
    gain_mean_db: float = 0.0
    notes: str = ""

    def as_dict(self) -> dict:
        """Flat dictionary for the JSON results."""
        return {
            "name": self.name,
            "band_before_db": round(self.band_before_db, 4),
            "band_after_db": round(self.band_after_db, 4),
            "band_reduction_db": round(self.band_reduction_db, 4),
            "tone_before_db": {k: round(v, 4) for k, v in self.tone_before_db.items()},
            "tone_after_db": {k: round(v, 4) for k, v in self.tone_after_db.items()},
            "tone_shift_db": {k: round(v, 4) for k, v in self.tone_shift_db.items()},
            "tone_snr_db": {k: round(v, 4) for k, v in self.tone_snr_db.items()},
            "tone_shift_max_db": round(self.tone_shift_max_db, 4),
            "tone_shift_max_wanted_db": round(self.tone_shift_max_wanted_db, 4),
            "snr_in_db": round(self.snr_in_db, 4),
            "snr_out_db": round(self.snr_out_db, 4),
            "snr_improvement_db": round(self.snr_improvement_db, 4),
            "gain_var_db2": round(self.gain_var_db2, 4),
            "gain_std_db": round(self.gain_std_db, 4),
            "gain_band_var_db2": round(self.gain_band_var_db2, 4),
            "gain_range_db": round(self.gain_range_db, 4),
            "gain_mean_db": round(self.gain_mean_db, 4),
            "notes": self.notes,
        }


def _measure_common(case: Case, out: np.ndarray, gain_db: np.ndarray | None,
                    sr: int = SR) -> Metrics:
    """Compute the metric set for one processed signal."""
    m = Metrics(name=case.name)
    before = band_power(case.mixed, case.band[0], case.band[1], sr)
    after = band_power(out, case.band[0], case.band[1], sr)
    m.band_before_db = 10.0 * math.log10(max(before, 1e-30))
    m.band_after_db = 10.0 * math.log10(max(after, 1e-30))
    m.band_reduction_db = m.band_before_db - m.band_after_db

    for freq in case.tones:
        key = f"{freq:.0f}Hz"
        frames = wanted_frames(case.clean, case.noise, freq, sr)
        level_in = tone_level(case.mixed, freq, sr, frames=frames)
        level_out = tone_level(out, freq, sr, frames=frames)
        snr = partial_snr_db(case.clean, case.noise, freq, sr)
        m.tone_before_db[key] = level_in
        m.tone_after_db[key] = level_out
        shift = level_out - level_in
        m.tone_shift_db[key] = shift
        m.tone_snr_db[key] = snr
        m.tone_shift_max_db = max(m.tone_shift_max_db, abs(shift))
        if snr >= TONE_SNR_FLOOR_DB:
            m.tone_shift_max_wanted_db = max(m.tone_shift_max_wanted_db, abs(shift))

    clean_p = float(np.dot(case.clean, case.clean))
    noise_p = float(np.dot(case.noise, case.noise))
    m.snr_in_db = 10.0 * math.log10(max(clean_p, 1e-30) / max(noise_p, 1e-30))
    m.snr_out_db = projection_snr(out, case.clean)
    m.snr_improvement_db = m.snr_out_db - m.snr_in_db

    if gain_db is not None:
        stats = gain_curve_stats(gain_db, case.band, sr)
        m.gain_var_db2 = stats["var_db2"]
        m.gain_std_db = stats["std_db"]
        m.gain_band_var_db2 = stats["band_mean_var_db2"]
        m.gain_range_db = stats["range_db"]
        g = np.asarray(gain_db, dtype=np.float64)
        bins = g.shape[1]
        freqs = np.fft.rfftfreq(2 * (bins - 1), 1.0 / float(sr))[:bins]
        sel = (freqs >= case.band[0]) & (freqs <= case.band[1])
        if np.any(sel):
            m.gain_mean_db = float(np.mean(g[:, sel]))
    return m


# ---------------------------------------------------------------------------
# 3. The module, with the gain curve captured
# ---------------------------------------------------------------------------
class GainProbe(SpectralDenoise):
    """``SpectralDenoise`` that records the gain curve it applies.

    The behaviour is unchanged — ``_apply_masking`` is the last step that
    touches the gain curve, so its return value is exactly the gain that
    multiplies the spectrum.  Nothing in the module is modified.
    """

    def __init__(self, *args, **kwargs):
        """Create the probe (same signature as the module)."""
        super().__init__(*args, **kwargs)
        self.captures: list[np.ndarray] = []

    def clear(self) -> None:
        """Drop the recorded gain curves."""
        self.captures.clear()

    def _apply_masking(self, gain_db, mag, cfg, *args, **kwargs):
        """Record the final gain curve and delegate to the module."""
        out = super()._apply_masking(gain_db, mag, cfg, *args, **kwargs)
        self.captures.append(np.asarray(out, dtype=np.float64).copy())
        return out

    @property
    def gain_curve(self) -> np.ndarray | None:
        """The captured gain curve (``(frames, bins)``) or ``None``."""
        if not self.captures:
            return None
        return max(self.captures, key=lambda g: g.size)


def measure_module(case: Case, learn: bool = True, params: dict | None = None,
                    sr: int = SR) -> tuple[Metrics, np.ndarray]:
    """Run the real module over a case and measure it.

    Parameters
    ----------
    case:
        The case to process.
    learn:
        Feed the case's own noise part to ``learn()`` first (the documented
        workflow) and run one warm-up block so the trackers settle.
    params:
        Optional parameter overrides for the module.
    sr:
        Sample rate.

    Returns
    -------
    (Metrics, numpy.ndarray)
        The measurements and the processed signal.
    """
    module = GainProbe(sr, 1)
    if params:
        for key, value in params.items():
            module.params.set(key, value)
    if learn:
        module.learn(case.noise[:, None])
    module.clear()
    # warm-up: lets the minimum-statistics tracker and the average spectrum
    # settle before the measured block.
    module.process(case.noise[:, None])
    module.clear()
    out = np.asarray(module.process(case.mixed[:, None]), dtype=np.float64)[:, 0]
    gain = module.gain_curve
    metrics = _measure_common(case, out, gain, sr)
    return metrics, out


# ---------------------------------------------------------------------------
# 4. The mirror: the module's gain law with the levers exposed
# ---------------------------------------------------------------------------
@dataclass
class Levers:
    """The knobs the experiment is about.

    The defaults are the values the current module uses, so the mirror
    reproduces the module exactly until a lever is changed.
    """

    # -- analysis
    fft: int = 720
    hop: int = 180
    window: str = "hann"
    multi_resolution: bool = True
    omlsa: bool = False

    # -- the levers under test
    over_subtraction: float = 1.0      # multiplier on the noise estimate
    dd_alpha: float = 0.98             # decision-directed smoothing factor
    freq_smooth_bins: int = 4          # gain-curve smoothing width (bins)
    noise_percentile: float = 50.0     # percentile of the learned profile
    floor_bias_db: float = 12.0        # offset of the minimum-statistics floor
    floor_from_learned: bool = True    # use the learned profile in the floor
    learned_adaptive: bool = True      # adaptive: max(floor, learned)
    broad_gap: int = 4                 # half width of the excluded gap (bins)
    broad_span: int = 16               # half width of the broadband window (bins)
    broad_gap_hz: float = 0.0          # excluded gap in Hz (overrides broad_gap)
    broad_span_hz: float = 0.0         # broadband window in Hz (overrides broad_span)
    tonal_ref: str = "broad"           # "broad" | "learned" tonal reference
    tonal_mask_ref: str = "broad"      # "broad" | "learned" reference for the tonal mask
    temporal_smooth: bool = False      # engage the temporal smoothing at once
    law: str = "soft"                  # "soft" (module) | "subtract"
    gain_floor_db: float = -40.0       # lower bound of the gain curve (dB)
    noise_power_units: str = "magnitude"  # "magnitude" (module) | "power"

    # -- reduction law
    noise_threshold: float = 0.0
    tonal_threshold: float = 0.0
    knee_db: float = 9.0
    noise_reduction: float = 12.0
    tonal_reduction: float = 12.0

    # -- smoothing / dynamics
    artifact: float = 0.5
    release_s: float = 0.1
    attack_s: float = 0.004
    release_mapping: str = "module"     # "module" | "swapped"
    smooth_tau: float = 0.025
    min_rise_db: float = 0.15

    # -- noise floor features
    masking: float = 0.5
    masking_guard: bool = False        # do not let the mask undo noise reduction
    synthesis: float = 0.5
    synthesis_fix: bool = False        # use a consistent power for the target
    enhancement: float = 0.5
    whitening: float = 0.5

    # -- profile
    learned: "np.ndarray | None" = None
    learned_tonal: "np.ndarray | None" = None

    def label(self) -> str:
        """Short human readable description of the non-default levers."""
        parts = []
        if self.over_subtraction != 1.0:
            parts.append(f"over={self.over_subtraction:g}")
        if self.dd_alpha != 0.98:
            parts.append(f"alpha={self.dd_alpha:g}")
        if self.freq_smooth_bins != 4:
            parts.append(f"smooth={self.freq_smooth_bins}")
        if self.noise_percentile != 50.0:
            parts.append(f"pct={self.noise_percentile:g}")
        if self.floor_bias_db != 12.0:
            parts.append(f"bias={self.floor_bias_db:g}")
        if (self.broad_gap, self.broad_span) != (4, 16):
            parts.append(f"broad={self.broad_gap}/{self.broad_span}")
        if self.tonal_ref != "broad":
            parts.append(f"tonal-ref={self.tonal_ref}")
        if self.tonal_mask_ref != "broad":
            parts.append(f"mask-ref={self.tonal_mask_ref}")
        if self.temporal_smooth:
            parts.append("temporal")
        if self.release_mapping != "module":
            parts.append(f"release={self.release_mapping}")
        if self.law != "soft":
            parts.append(f"law={self.law}")
        if self.gain_floor_db != -40.0:
            parts.append(f"gfloor={self.gain_floor_db:g}")
        if self.noise_power_units != "magnitude":
            parts.append(f"npower={self.noise_power_units}")
        if self.masking_guard:
            parts.append("mask-guard")
        if self.synthesis_fix:
            parts.append("synth-fix")
        if self.learned_adaptive is False:
            parts.append("no-adaptive")
        if self.floor_from_learned is False:
            parts.append("no-learned-floor")
        return " ".join(parts) if parts else "module defaults"


def _moving_average(x: np.ndarray, width: int, axis: int = -1) -> np.ndarray:
    """Centred moving average with edge replication (same as the module)."""
    arr = np.asarray(x, dtype=np.float64)
    width = int(width)
    if width <= 1 or arr.size == 0:
        return arr.copy()
    if width % 2 == 0:
        width += 1
    pad = width // 2
    moved = np.moveaxis(arr, axis, -1)
    padded = np.pad(moved, [(0, 0)] * (moved.ndim - 1) + [(pad, pad)], mode="edge")
    kernel = np.ones(width, dtype=np.float64) / float(width)
    out = np.apply_along_axis(lambda v: np.convolve(v, kernel, mode="valid"), -1, padded)
    return np.moveaxis(out, -1, axis)


class MirrorDenoiser:
    """A faithful re-implementation of the module's gain law.

    It is built directly on :mod:`aurcore.dsp.spectral` and
    :mod:`aurcore.dsp.stft` so that the levers can be driven without
    touching the module.  ``Levers`` carries the settings; the class keeps
    the per-channel tracking state.
    """

    def __init__(self, levers: Levers, sr: int = SR):
        """Create the mirror with the given lever settings."""
        self.lv = levers
        self.sr = int(sr)
        self.sm_db: np.ndarray | None = None
        self.min_db: np.ndarray | None = None
        self.prev_gain_db: np.ndarray | None = None
        self.prev_mag: np.ndarray | None = None
        self.dev_db: np.ndarray | None = None
        self.avg_db: np.ndarray | None = None
        self.tonal_acc: np.ndarray | None = None
        self.learned_floor_db: np.ndarray | None = None
        self.learned_tonal: np.ndarray | None = None
        self._rng = np.random.default_rng(0xA0A15)

    # -- learning ------------------------------------------------------------
    def learn(self, noise: np.ndarray) -> None:
        """Capture a noise profile from a noise-only block."""
        arr = np.asarray(noise, dtype=np.float64).ravel()
        engine = STFT(self.lv.fft, self.lv.hop, window=self.lv.window)
        mag, _ = engine.analyze_block(arr, center=True)
        mag_db = np.asarray(mag[0] if mag.ndim == 3 else mag, dtype=np.float64).T
        profile = np.asarray(
            _spectral.estimate_noise_profile(mag_db, mag_db.shape[0],
                                             percentile=self.lv.noise_percentile),
            dtype=np.float64,
        )
        if profile.shape[-1] != mag_db.shape[-1]:
            profile = np.resize(profile, mag_db.shape[-1])
        self.learned_floor_db = profile
        broad = median_filter_axis(profile, 0, 9)
        self.learned_tonal = (profile - broad) > 8.0

    # -- processing ----------------------------------------------------------
    def process(self, signal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Denoise a mono signal; returns ``(out, gain_db)``."""
        arr = np.asarray(signal, dtype=np.float64).ravel()
        lv = self.lv
        engine = STFT(lv.fft, lv.hop, window=lv.window)
        mag, phase = engine.analyze_block(arr, center=True)
        mag = np.asarray(mag[0] if mag.ndim == 3 else mag, dtype=np.float64).T
        phase = np.asarray(phase[0] if phase.ndim == 3 else phase, dtype=np.float64).T
        freqs = np.fft.rfftfreq(lv.fft, 1.0 / float(self.sr))

        sm_db = self._smooth_magnitude(mag)
        broad_db = broadband_floor(sm_db, gap=self._broad_geometry()[0],
                                   span=self._broad_geometry()[1])
        tonal_mask = np.clip((sm_db - broad_db) / 6.0, 0.0, 1.0)
        learned_tonal = self._tonal_mask(mag.shape[-1])
        blend = tonal_mask * (1.0 - learned_tonal)
        floor_db = self._noise_floor(sm_db, broad_db, tonal_mask, learned_tonal)
        ref_db = self._tonal_reference(floor_db, broad_db)
        if self.lv.tonal_mask_ref == "learned" and self.learned_floor_db is not None:
            # Judge the tonal mask against the learned noise profile: it is
            # not polluted by the wanted content that sits next to it.
            ref_db = self._tonal_reference(floor_db, broad_db)
            tonal_mask = np.clip((sm_db - ref_db) / 6.0, 0.0, 1.0)
            blend = tonal_mask * (1.0 - learned_tonal)
            floor_db = self._noise_floor(sm_db, broad_db, tonal_mask, learned_tonal)

        gain_db = self._gain_curve(mag, sm_db, floor_db, ref_db, blend)

        extra = []
        if lv.multi_resolution:
            for factor in (2, 0.5):
                cand = int(round(lv.fft * factor))
                cand = max(128, min(8192, cand))
                cand += cand % 2
                if cand != lv.fft:
                    extra.append(cand)
        if extra:
            gain_db = self._multi_resolution(arr, gain_db, floor_db, freqs, blend, extra)

        gain_db = self._apply_masking(gain_db, mag, sm_db, floor_db)
        gain = db_to_lin(gain_db)

        spec = mag * np.exp(1j * phase)
        out_spec = spec * gain
        out_spec = self._apply_enhancement(out_spec, sm_db, floor_db)
        out_spec = self._apply_synthesis(out_spec, floor_db, gain)
        out_spec = self._apply_whitening(out_spec, sm_db, tonal_mask)

        out_mag = np.abs(out_spec)
        out_phase = np.angle(out_spec)
        rendered = engine.synthesize_block(out_mag.T, out_phase.T, norm="exact",
                                           length=int(arr.size), center=True)
        rendered = np.asarray(rendered, dtype=np.float64).reshape(-1)
        return rendered, gain_db

    # -- the parts -----------------------------------------------------------
    def _smooth_magnitude(self, mag: np.ndarray) -> np.ndarray:
        """Time smoothed magnitude in dB plus the minimum-statistics tracker."""
        lv = self.lv
        mag_db = _db(mag)
        beta = 1.0 - math.exp(-lv.hop / max(1e-6, self.sr * lv.smooth_tau))
        if self.sm_db is None or self.sm_db.shape[-1] != mag_db.shape[-1]:
            self.sm_db = mag_db[0].copy()
            self.dev_db = np.zeros(mag_db.shape[-1], dtype=np.float64)
            self.min_db = mag_db[0].copy()
        smoothed = np.empty_like(mag_db)
        prev = self.sm_db
        tracker = self.min_db
        for i in range(mag_db.shape[0]):
            cur = mag_db[i]
            prev = prev + beta * (cur - prev)
            smoothed[i] = prev
            tracker = np.minimum(tracker + lv.min_rise_db, prev)
        self.sm_db = prev
        self.min_db = tracker
        deviation = np.abs(mag_db - smoothed)
        self.dev_db = self.dev_db + beta * (deviation.mean(axis=0) - self.dev_db)
        return smoothed

    def _tonal_mask(self, bins: int) -> np.ndarray:
        """The learned tonal peaks as a 0/1 array over the bins."""
        mask = np.zeros(bins, dtype=np.float64)
        tonal = self.learned_tonal
        if tonal is None:
            return mask
        if tonal.shape[-1] != bins:
            tonal = np.interp(np.linspace(0.0, 1.0, bins),
                              np.linspace(0.0, 1.0, tonal.shape[-1]),
                              tonal.astype(np.float64)) > 0.5
        return np.maximum(mask, tonal.astype(np.float64))

    def _noise_floor(self, sm_db, broad_db, tonal_mask, learned_tonal) -> np.ndarray:
        """Per-bin broadband noise floor in dB."""
        lv = self.lv
        floor = np.maximum(broad_db, self.min_db[None, :] + lv.floor_bias_db)
        wanted_tonal = (tonal_mask > 0.5) & (learned_tonal < 0.5)
        if np.any(wanted_tonal):
            floor = np.where(wanted_tonal, broad_db, floor)
        learned = self.learned_floor_db
        if learned is not None and lv.floor_from_learned:
            if learned.shape[-1] != floor.shape[-1]:
                learned = np.interp(np.linspace(0.0, 1.0, floor.shape[-1]),
                                    np.linspace(0.0, 1.0, learned.shape[-1]), learned)
                self.learned_floor_db = learned
            if lv.learned_adaptive:
                floor = np.maximum(floor, learned[None, :])
            else:
                floor = learned[None, :] + (floor - learned[None, :]) * 0.25
        return floor

    def _effective_floor(self, floor_db: np.ndarray) -> np.ndarray:
        """The floor with the over-subtraction factor applied."""
        over = float(self.lv.over_subtraction)
        if over == 1.0:
            return floor_db
        return floor_db + 20.0 * math.log10(max(over, 1e-6))

    def _tonal_reference(self, floor_db: np.ndarray, broad_db: np.ndarray) -> np.ndarray:
        """The reference the tonal decision is made against.

        ``"broad"`` is the module's behaviour: the instantaneous broadband
        estimate.  ``"learned"`` uses the learned per-bin noise profile, which
        is not contaminated by the wanted content that sits next to it.
        """
        if self.lv.tonal_ref != "learned":
            return broad_db
        learned = self.learned_floor_db
        if learned is None:
            return broad_db
        ref = np.asarray(learned, dtype=np.float64)
        if ref.shape[-1] != broad_db.shape[-1]:
            ref = np.interp(np.linspace(0.0, 1.0, broad_db.shape[-1]),
                            np.linspace(0.0, 1.0, ref.shape[-1]), ref)
        return ref[None, :]

    def _broad_geometry(self) -> tuple[int, int]:
        """The gap / span of the broadband floor, in bins.

        A fixed bin count means a different width in Hz for every FFT size, so
        the geometry can also be given in Hz (``broad_gap_hz`` /
        ``broad_span_hz``); it is then converted with the actual bin width.
        """
        lv = self.lv
        gap, span = lv.broad_gap, lv.broad_span
        if lv.broad_gap_hz > 0.0 or lv.broad_span_hz > 0.0:
            bin_hz = float(self.sr) / float(lv.fft)
            if lv.broad_gap_hz > 0.0:
                gap = max(2, int(round(lv.broad_gap_hz / bin_hz)))
            if lv.broad_span_hz > 0.0:
                span = max(gap + 4, int(round(lv.broad_span_hz / bin_hz)))
        return int(gap), int(span)

    def _noise_power(self, floor_db: np.ndarray) -> np.ndarray:
        """The noise estimate used by the OM-LSA branch.

        The module feeds the *magnitude* of the floor into the estimator
        although the estimator expects a power; ``noise_power_units="power"``
        fixes that.
        """
        level = np.maximum(db_to_lin(floor_db), 1e-18)
        if self.lv.noise_power_units == "power":
            return level * level
        return level

    def _gain_curve(self, mag, sm_db, floor_db, ref_db, blend) -> np.ndarray:
        """The broadband + tonal gain curve in dB (module logic + levers)."""
        lv = self.lv
        eff_floor = self._effective_floor(floor_db)
        snr = sm_db - eff_floor
        noise_power = self._noise_power(eff_floor)

        if lv.law == "subtract":
            # Classic spectral subtraction (aurcore.dsp.spectral): the noise
            # estimate is subtracted from the observed magnitude, with an
            # over-subtraction factor and a floor on the gain.
            gain_db = _spectral.spectral_subtract(
                mag, eff_floor, over_subtraction=lv.over_subtraction,
                floor_db=lv.gain_floor_db, mag_is_db=False, profile_is_db=True,
            )
            depth = np.minimum(-gain_db, lv.noise_reduction)
            gain_db = -depth
        else:
            weight = soft_weight(snr, lv.noise_threshold, lv.knee_db)
            depth = weight * lv.noise_reduction
            if lv.omlsa:
                gamma = (mag * mag) / noise_power
                prior = mag if self.prev_mag is None else self.prev_mag
                xi = _spectral.decision_directed_snr(prior, mag, lv.dd_alpha, noise_power)
                gain = _spectral.om_lsa_gain(gamma, xi, 0.0, 1.0)
                om_depth = np.maximum(0.0, -lin_to_db(np.maximum(gain, 1e-6)))
                depth = np.maximum(depth, om_depth)
            depth = np.minimum(depth, lv.noise_reduction)
            gain_db = -depth

        tonal_snr = sm_db - ref_db
        tonal_weight = soft_weight(tonal_snr, lv.tonal_threshold, lv.knee_db)
        tonal_depth = np.minimum(tonal_weight * lv.tonal_reduction, lv.tonal_reduction)
        tonal_gain_db = -tonal_depth
        gain_db = gain_db * (1.0 - blend) + tonal_gain_db * blend

        protect_snr = np.where(blend > 0.5, tonal_snr, snr)
        protect = protect_snr > 6.0
        width = int(lv.freq_smooth_bins)
        if width > 1:
            smoothed = _spectral.smooth_gain_curve(gain_db, width, "gaussian")
            gain_db = np.where(protect, gain_db, smoothed)

        gain_db = self._temporal_smooth(gain_db)
        self.prev_mag = mag * db_to_lin(gain_db)
        return gain_db

    def _temporal_smooth(self, gain_db: np.ndarray) -> np.ndarray:
        """Smooth the gain curve across frames."""
        lv = self.lv
        release = lv.release_s * (0.5 + lv.artifact)
        alpha_rel = 1.0 - math.exp(-lv.hop / max(1e-6, self.sr * release))
        alpha_att = 1.0 - math.exp(-lv.hop / max(1e-6, self.sr * lv.attack_s))
        if lv.release_mapping == "swapped":
            # the gain recovers quickly when the wanted content returns and
            # falls back to the attenuation slowly
            pass
        if self.prev_gain_db is None or self.prev_gain_db.shape != (gain_db.shape[1],):
            self.prev_gain_db = gain_db[-1].copy()
            if not lv.temporal_smooth:
                return gain_db
            # Engage the smoothing right away: frame 0 keeps its raw value,
            # frame 1 onwards is smoothed against the running state.
            self.prev_gain_db = gain_db[0].copy()
            out = gain_db.copy()
            current = gain_db[0].astype(np.float64, copy=True)
            for i in range(1, gain_db.shape[0]):
                target = gain_db[i]
                a = self._alpha(target, current, alpha_rel, alpha_att)
                current = current + a * (target - current)
                out[i] = current
            self.prev_gain_db = out[-1].copy()
            return out
        prev = self.prev_gain_db
        out = np.empty_like(gain_db)
        current = prev.astype(np.float64, copy=True)
        for i in range(gain_db.shape[0]):
            target = gain_db[i]
            a = self._alpha(target, current, alpha_rel, alpha_att)
            current = current + a * (target - current)
            out[i] = current
        self.prev_gain_db = out[-1].copy()
        return out

    def _alpha(self, target, current, alpha_rel: float, alpha_att: float):
        """The per-bin smoothing coefficient for one frame."""
        if self.lv.release_mapping == "swapped":
            return np.where(target > current, alpha_att, alpha_rel)
        return np.where(target > current, alpha_rel, alpha_att)

    def _multi_resolution(self, segment, gain_db, floor_db, freqs, blend, extra_ffts):
        """Combine the gain curves of several window sizes."""
        lv = self.lv
        base_frames = gain_db.shape[0]
        base_times = np.arange(base_frames, dtype=np.float64) * lv.hop / float(self.sr)
        for extra_fft in extra_ffts:
            hop_extra = max(16, int(round(extra_fft * lv.hop / float(lv.fft))))
            engine = STFT(extra_fft, hop_extra, window=lv.window)
            mag, _ = engine.analyze_block(segment, center=True)
            mag = np.asarray(mag[0] if mag.ndim == 3 else mag, dtype=np.float64).T
            if mag.size == 0:
                continue
            extra_freqs = np.fft.rfftfreq(extra_fft, 1.0 / float(self.sr))
            extra_times = np.arange(mag.shape[0], dtype=np.float64) * hop_extra / float(self.sr)
            sm_db = _moving_average(_db(mag), 3, axis=0)
            extra_floor = np.interp(extra_freqs, freqs, floor_db[-1])[None, :]
            broad = broadband_floor(sm_db, gap=self._broad_geometry()[0],
                                    span=self._broad_geometry()[1])
            tonal = np.clip((sm_db - broad) / 6.0, 0.0, 1.0)
            learned = self._tonal_mask(extra_freqs.size)
            extra_blend = tonal * (1.0 - learned[None, :])
            extra_ref = broad
            if lv.tonal_ref == "learned" and self.learned_floor_db is not None:
                extra_ref = np.interp(extra_freqs, freqs, self.learned_floor_db)[None, :]
            extra_gain = self._gain_curve_static(mag, sm_db, extra_floor, extra_ref, extra_blend)
            extra_gain = _resample_gain_2d(extra_gain, extra_times, extra_freqs, base_times, freqs)
            extra_blend_rs = _resample_gain_2d(extra_blend, extra_times, extra_freqs,
                                               base_times, freqs)
            protect = (blend > 0.5) | (extra_blend_rs > 0.5)
            gain_db = np.where(protect, gain_db, np.minimum(gain_db, extra_gain))
        return gain_db

    def _gain_curve_static(self, mag, sm_db, floor_db, ref_db, blend) -> np.ndarray:
        """Gain curve without the temporal state (extra resolutions)."""
        lv = self.lv
        eff_floor = self._effective_floor(floor_db)
        snr = sm_db - eff_floor
        if lv.law == "subtract":
            gain_db = _spectral.spectral_subtract(
                mag, eff_floor, over_subtraction=lv.over_subtraction,
                floor_db=lv.gain_floor_db, mag_is_db=False, profile_is_db=True,
            )
            gain_db = -np.minimum(-gain_db, lv.noise_reduction)
        else:
            weight = soft_weight(snr, lv.noise_threshold, lv.knee_db)
            depth = np.minimum(weight * lv.noise_reduction, lv.noise_reduction)
            gain_db = -depth
        tonal_snr = sm_db - ref_db
        tonal_weight = soft_weight(tonal_snr, lv.tonal_threshold, lv.knee_db)
        tonal_gain_db = -np.minimum(tonal_weight * lv.tonal_reduction, lv.tonal_reduction)
        gain_db = gain_db * (1.0 - blend) + tonal_gain_db * blend
        width = int(lv.freq_smooth_bins)
        if width > 1:
            protect = np.where(blend > 0.5, tonal_snr, snr) > 6.0
            smoothed = _spectral.smooth_gain_curve(gain_db, width, "gaussian")
            gain_db = np.where(protect, gain_db, smoothed)
        return gain_db

    def _apply_masking(self, gain_db, mag, sm_db, floor_db) -> np.ndarray:
        """Limit the attenuation with a psychoacoustic masking model."""
        amount = float(self.lv.masking)
        if amount <= 0.0:
            return gain_db
        mag_db = _db(mag)
        offset = -(14.0 + 26.0 * (1.0 - amount))
        try:
            mask_db = _spectral.masking_model(mag_db, offset, int(self.sr), int(self.lv.fft))
        except Exception:
            return gain_db
        limit = np.minimum(mask_db - mag_db, 0.0)
        if self.lv.masking_guard:
            # The mask may only relax the attenuation of bins that carry
            # wanted content; a bin that is pure noise may be attenuated as
            # far as the reduction law asks for.
            snr = sm_db - floor_db
            limit = np.where(snr > self.lv.noise_threshold, limit, -np.inf)
        return gain_db + amount * np.maximum(0.0, limit - gain_db)

    def _apply_enhancement(self, out_spec, sm_db, floor_db) -> np.ndarray:
        """Re-add low level but temporally consistent content."""
        amount = float(self.lv.enhancement)
        if amount <= 0.0:
            return out_spec
        snr = sm_db - floor_db
        window = np.clip(snr / 12.0, 0.0, 1.0) * np.clip((12.0 - snr) / 12.0, 0.0, 1.0)
        if self.dev_db is None:
            return out_spec
        consistency = np.exp(-self.dev_db[None, :] / 6.0)
        boost = 1.0 + amount * 0.5 * window * consistency
        return out_spec * boost

    def _apply_synthesis(self, out_spec, floor_db, gain) -> np.ndarray:
        """Regenerate the noise floor that was removed."""
        amount = float(self.lv.synthesis)
        if amount <= 0.0:
            return out_spec
        noise_level = np.maximum(db_to_lin(floor_db), 1e-18)
        neighbour_gain = median_filter_axis(gain, 1, 9)
        if self.lv.synthesis_fix:
            # consistent units: the target is the expected *power* of the
            # removed noise after the gain has been applied.
            target = (noise_level * neighbour_gain) ** 2
        else:
            target = noise_level * neighbour_gain * neighbour_gain
        out_power = np.abs(out_spec) ** 2
        fill = np.maximum(0.0, 0.35 * target - out_power)
        synth = np.sqrt(fill) * amount
        if not np.any(synth > 0.0):
            return out_spec
        random_phase = self._rng.uniform(0.0, 2.0 * math.pi, size=synth.shape)
        return out_spec + synth * np.exp(1j * random_phase)

    def _apply_whitening(self, out_spec, sm_db, tonal_mask) -> np.ndarray:
        """Flatten the long term spectrum towards a target curve."""
        amount = float(self.lv.whitening)
        if amount <= 0.0:
            return out_spec
        bins = sm_db.shape[-1]
        if self.avg_db is None or self.avg_db.shape[-1] != bins:
            self.avg_db = sm_db[-1].copy()
            self.tonal_acc = np.zeros(bins, dtype=np.float64)
        beta = 0.05
        self.avg_db = self.avg_db + beta * (sm_db[-1] - self.avg_db)
        self.tonal_acc = self.tonal_acc + beta * (tonal_mask[-1] - self.tonal_acc)
        avg = self.avg_db.copy()
        tonal_bins = (self.tonal_acc > 0.5) | (tonal_mask[-1] > 0.5)
        if np.any(tonal_bins):
            broad = broadband_floor(sm_db[-1:], gap=2, span=8)[0]
            avg[tonal_bins] = broad[tonal_bins]
        target = np.full(bins, float(np.mean(avg)), dtype=np.float64)
        try:
            gain_db = _spectral.whiten(avg, target, mag_is_db=True, target_is_db=True,
                                       preserve_level=True)
        except Exception:
            return out_spec
        gain_db = np.where(tonal_bins, 0.0, gain_db)
        return out_spec * db_to_lin(gain_db * amount)


def _resample_gain_2d(gain_db, src_times, src_freqs, dst_times, dst_freqs) -> np.ndarray:
    """Interpolate a gain curve onto another time/frequency grid."""
    in_time = np.empty((dst_times.size, gain_db.shape[1]), dtype=np.float64)
    for b in range(gain_db.shape[1]):
        in_time[:, b] = np.interp(dst_times, src_times, gain_db[:, b])
    out = np.empty((dst_times.size, dst_freqs.size), dtype=np.float64)
    for i in range(dst_times.size):
        out[i] = np.interp(dst_freqs, src_freqs, in_time[i])
    return out


def measure_mirror(case: Case, levers: Levers, learn: bool = True,
                   sr: int = SR) -> tuple[Metrics, np.ndarray]:
    """Run the mirror over a case and measure it."""
    mirror = MirrorDenoiser(levers, sr)
    if learn:
        mirror.learn(case.noise)
    # warm-up so the trackers settle exactly like the module's warm-up block
    mirror.process(case.noise)
    out, gain = mirror.process(case.mixed)
    metrics = _measure_common(case, out, gain, sr)
    metrics.notes = levers.label()
    return metrics, out


# ---------------------------------------------------------------------------
# 5. Self check
# ---------------------------------------------------------------------------
def _selftest() -> None:  # pragma: no cover - manual helper
    """Print the baseline table."""
    header = f"{'case':<14} {'band red':>9} {'tone shift':>10} {'snr gain':>9} {'gain var':>9}"
    print(header)
    print("-" * len(header))
    for case in CASES:
        m, _ = measure_module(case)
        tone = f"{m.tone_shift_max_db:+.2f} dB" if case.tones else "-"
        print(f"{case.name:<14} {m.band_reduction_db:>8.2f}dB {tone:>10} "
              f"{m.snr_improvement_db:>8.2f}dB {m.gain_var_db2:>8.2f}")


if __name__ == "__main__":  # pragma: no cover - manual helper
    _selftest()
