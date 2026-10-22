"""
qa.quality.measurement — the measurement instrument of the project.

The instrument measures **any** module of the registry with the same
questions:

* **what is removed** — how much energy the module takes out of the band that
  carries nothing but the artefact (``removed_db``);
* **what is kept** — how far the wanted partials move (``kept_db``).  The
  movement is measured against the input, except in a case that carries a
  reference: there the input carries the artefact itself (a hard clipper
  inflates the wanted partials), so the partial that has to survive is the one
  of the reference;
* **the gain curve's variance** — how much the applied gain breathes from
  frame to frame inside that band (``gain_var_db2``);
* **the latency** — how many samples the module delays the signal
  (``latency_samples``);
* **the block coherence** — how much the result depends on the block size
  the host happens to use (``block_coherence_db``).

Nothing in here knows what a particular module is *for*: the ground truth
comes from :mod:`qa.quality.ground_truth` and the thresholds come from
:mod:`qa.quality.quality_rules`.  A module owner therefore only has to add a rule to
get the instrument to measure their module.

The instrument is deterministic.  Every case is built from a seeded generator,
the analysis geometry is fixed, and no wall clock, no thread and no unseeded
random number takes part in a measurement.  Running the same measurement twice
gives the same numbers — that is checked by the acceptance test.

Run it over every module that has a rule::

    python qa/quality/measure_all.py
"""
from __future__ import annotations

import hashlib
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

import numpy as np

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# The sibling modules of this directory are imported by their plain name, the
# way the study scripts in this directory already do it.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from aurcore import registry  # noqa: E402

try:  # pragma: no cover - importing the package registers every module
    import aurcore.modules  # noqa: F401, E402
except Exception:  # noqa: BLE001 - a broken module file must not break the tool
    pass

from ground_truth import SR, Case, make_case  # noqa: E402

__all__ = [
    "SR",
    "TONE_SNR_FLOOR_DB",
    "Measurement",
    "INTERNAL_GAIN_HOOKS",
    "band_power",
    "band_power_frames",
    "tone_level",
    "partial_snr_db",
    "wanted_frames",
    "effective_gain_curve",
    "estimate_latency",
    "block_coherence_db",
    "measure",
]

#: A partial counts as wanted content when it sits at least this far above
#: the noise in its own analysis band.  Below that level a repair module is
#: expected to remove it.
TONE_SNR_FLOOR_DB: float = 10.0

#: Analysis geometry of the measurements (Welch / short-time estimates).
N_FFT: int = 4096
HOP: int = 1024

#: How many frames the smoothed gain curve averages over.  Five frames of
#: 1024 samples are 107 ms at 48 kHz: long enough to average the fluctuation
#: of the material that is left behind, short enough to keep the slow drift
#: of a gain that breathes.
SMOOTH_FRAMES: int = 5

#: The modules that publish their own gain curve, and the method that returns
#: it.  A module that is not listed here is measured with the *effective* gain
#: curve (the gain the input/output pair shows), which every module can be
#: measured with.
INTERNAL_GAIN_HOOKS: dict[str, str] = {
    "AUR_SPECTRALDENOISE": "_apply_masking",
}

_EPS: float = 1e-12


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _as_mono(x: np.ndarray) -> np.ndarray:
    """Return ``x`` as a flat float64 array."""
    arr = np.asarray(x, dtype=np.float64)
    if arr.ndim > 1:
        arr = arr[:, 0] if arr.shape[1] else arr.ravel()
    return arr.ravel()


def _frames(x: np.ndarray, n_fft: int = N_FFT, hop: int = HOP) -> np.ndarray:
    """Split ``x`` into overlapping frames (zero padded when it is short)."""
    arr = _as_mono(x)
    if arr.size < n_fft:
        arr = np.concatenate([arr, np.zeros(n_fft - arr.size)])
    views = np.lib.stride_tricks.sliding_window_view(arr, n_fft)
    return views[::hop][: 1 + (views.shape[0] - 1) // hop]


def _band_mask(lo: float, hi: float, n_fft: int = N_FFT, sr: int = SR) -> np.ndarray:
    """Boolean mask of the bins inside a band."""
    freqs = np.fft.rfftfreq(n_fft, 1.0 / float(sr))
    return (freqs >= float(lo)) & (freqs <= float(hi))


def band_power(
    x: np.ndarray,
    f_lo: float,
    f_hi: float,
    sr: int = SR,
    n_fft: int = N_FFT,
    hop: int = HOP,
    mask: np.ndarray | None = None,
) -> float:
    """Mean-square power inside a band (Welch average of the power spectrum).

    The absolute scale is ``number of bins`` rather than the mean-square
    power, which is irrelevant here: only ratios of two measurements of the
    same kind are used.

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
    mask:
        Optional boolean mask over the samples; only the selected samples
        take part in the estimate.
    """
    arr = _as_mono(x)
    if mask is not None:
        sel = np.asarray(mask, dtype=bool)
        if sel.shape[0] != arr.shape[0]:
            raise ValueError("the mask does not match the signal")
        arr = arr[sel]
    if arr.size < n_fft:
        return float(np.mean(arr * arr)) if arr.size else 0.0
    win = np.hanning(n_fft)
    frames = _frames(arr, n_fft, hop)
    spec = np.fft.rfft(frames * win, axis=-1)
    sel = _band_mask(f_lo, f_hi, n_fft, sr)
    if not np.any(sel):
        return 0.0
    norm = float(np.sum(win * win))
    return float(np.mean(np.abs(spec[:, sel]) ** 2, axis=0).sum() / norm)


def band_power_frames(
    x: np.ndarray,
    f_lo: float,
    f_hi: float,
    sr: int = SR,
    n_fft: int = N_FFT,
    hop: int = HOP,
) -> np.ndarray:
    """Mean-square power inside a band, one value per frame."""
    arr = _as_mono(x)
    if arr.size < n_fft:
        arr = np.concatenate([arr, np.zeros(n_fft - arr.size)])
    win = np.hanning(n_fft)
    frames = _frames(arr, n_fft, hop)
    spec = np.abs(np.fft.rfft(frames * win, axis=-1)) ** 2
    sel = _band_mask(f_lo, f_hi, n_fft, sr)
    if not np.any(sel):
        return np.zeros(frames.shape[0], dtype=np.float64)
    return spec[:, sel].sum(axis=1) / float(np.sum(win * win))


def _frame_amplitudes(
    x: np.ndarray, freq: float, sr: int = SR, n_fft: int = N_FFT, hop: int = HOP
) -> np.ndarray:
    """Short-time amplitude of the partial at ``freq``, one value per frame."""
    arr = _as_mono(x)
    if arr.size < n_fft:
        return np.zeros(0, dtype=np.float64)
    win = np.hanning(n_fft)
    frames = _frames(arr, n_fft, hop)
    basis = np.exp(-2j * math.pi * float(freq) * np.arange(n_fft) / float(sr))
    return np.abs((frames * win) @ basis) * (2.0 / float(np.sum(win)))


def wanted_frames(
    clean: np.ndarray,
    noise: np.ndarray,
    freq: float,
    sr: int = SR,
    snr_db: float = TONE_SNR_FLOOR_DB,
) -> np.ndarray | None:
    """Frames in which the partial is provably above the noise.

    A frame qualifies when the clean part's short-time amplitude at ``freq``
    is at least ``snr_db`` above the noise part's amplitude in the same
    frame.  Only those frames carry wanted content, so only those frames may
    be used to judge whether the module moved the partial.
    """
    c = _frame_amplitudes(clean, freq, sr)
    n = _frame_amplitudes(noise, freq, sr)
    if c.size == 0:
        return None
    mask = c >= n * (10.0 ** (float(snr_db) / 20.0))
    return mask if np.any(mask) else None


def _wanted_mask(
    clean: np.ndarray,
    noise: np.ndarray,
    freq: float,
    n_frames: int,
    sr: int = SR,
    snr_db: float = TONE_SNR_FLOOR_DB,
) -> np.ndarray | None:
    """The wanted-frame mask, fitted to a frame grid of ``n_frames`` entries."""
    c = _frame_amplitudes(clean, freq, sr)
    n = _frame_amplitudes(noise, freq, sr)
    count = min(c.size, n.size)
    if count == 0:
        return None
    mask = c[:count] >= n[:count] * (10.0 ** (float(snr_db) / 20.0))
    fitted = np.zeros(max(n_frames, 0), dtype=bool)
    take = min(count, fitted.size)
    fitted[:take] = mask[:take]
    return fitted if np.any(fitted) else None


def _level_db(amps: np.ndarray, mask: np.ndarray | None = None) -> float:
    """Median level of a frame amplitude array, in dB."""
    if amps.size == 0:
        return -300.0
    if mask is not None:
        sel = amps[mask]
        if sel.size:
            amps = sel
    return 20.0 * math.log10(max(float(np.median(amps)), _EPS))


def tone_level(
    x: np.ndarray,
    freq: float,
    sr: int = SR,
    frames: np.ndarray | None = None,
) -> float:
    """Short-time level of a partial, in dB (median over the frames)."""
    return _level_db(_frame_amplitudes(x, freq, sr), frames)


def partial_snr_db(
    clean: np.ndarray,
    noise: np.ndarray,
    freq: float,
    sr: int = SR,
    half_width_hz: float = 30.0,
) -> float:
    """In-band SNR of a partial: its power against the noise's power."""
    lo = float(freq) - float(half_width_hz)
    hi = float(freq) + float(half_width_hz)
    p_clean = band_power(clean, lo, hi, sr)
    p_noise = band_power(noise, lo, hi, sr)
    if p_noise <= 0.0:
        return 120.0
    return 10.0 * math.log10(max(p_clean, 1e-30) / p_noise)


def _moving_average(x: np.ndarray, width: int) -> np.ndarray:
    """Centred moving average; the edges are held at the edge value.

    The convolution runs over the valid range only, so the ends of the curve
    are extended with the value they already have instead of with a padded
    step.  A padded step would show up as a jump in the gain curve that the
    module never produced.
    """
    arr = np.asarray(x, dtype=np.float64)
    width = int(width)
    if width <= 1 or arr.size == 0:
        return arr.copy()
    if arr.size < width:
        return np.full(arr.shape, float(np.mean(arr)))
    kernel = np.ones(width, dtype=np.float64) / float(width)
    smoothed = np.convolve(arr, kernel, mode="valid")
    head = np.full(width // 2, smoothed[0])
    tail = np.full(width - 1 - width // 2, smoothed[-1])
    return np.concatenate([head, smoothed, tail])


def effective_gain_curve(
    x_in: np.ndarray,
    x_out: np.ndarray,
    band: tuple[float, float],
    sr: int = SR,
    n_fft: int = N_FFT,
    hop: int = HOP,
    skip: float = 0.25,
    exclude: np.ndarray | None = None,
    smooth_frames: int = 0,
) -> np.ndarray:
    """The gain the module applied inside a band, one value per frame.

    The curve is built from the band energy of the input and of the output:
    ``10 log10(E_out(t) / E_in(t))``.  It is the *effective* gain, so it
    needs nothing from the module but its input and its output — that is what
    makes it usable for every module.

    The first ``skip`` fraction of the frames is discarded: it is the
    settling transient of the module.  ``exclude`` is an optional mask over
    the frames that must not take part (the frames of an impulsive artefact
    carry the very gain swing the module is there to produce).

    ``smooth_frames`` averages the two energy curves over that many frames
    before the ratio is taken.  The smoothed curve answers a narrower
    question — does the gain *drift* over time — while the raw curve also
    carries the fluctuation of the material that is left behind.
    """
    p_in = band_power_frames(x_in, band[0], band[1], sr, n_fft, hop)
    p_out = band_power_frames(x_out, band[0], band[1], sr, n_fft, hop)
    n = min(p_in.size, p_out.size)
    if n == 0:
        return np.zeros(0, dtype=np.float64)
    if smooth_frames > 1:
        p_in = _moving_average(p_in, smooth_frames)
        p_out = _moving_average(p_out, smooth_frames)
    ratio = np.maximum(p_out[:n], 1e-30) / np.maximum(p_in[:n], 1e-30)
    curve = 10.0 * np.log10(ratio)
    start = int(n * float(skip))
    curve = curve[start:]
    if exclude is not None:
        mask = np.asarray(exclude, dtype=bool)
        if mask.shape[0] == n:
            keep = ~mask[start:]
            if np.any(keep):
                curve = curve[keep]
    return curve


def estimate_latency(
    x_in: np.ndarray,
    x_out: np.ndarray,
    sr: int = SR,
    max_latency: int = 8192,
    envelope_hop: int = 64,
) -> int:
    """How many samples the output lags the input, in samples.

    The amplitude envelopes are cross-correlated; the peak of the correlation
    is the delay.  The envelope is used rather than the waveform because a
    module may change the phase of the material it processes.

    Only delays of zero or more are considered: a module cannot deliver a
    sample before the input that produced it.  A peak that is not convincing
    (a normalised correlation below 0.5) is reported as no delay at all.
    """
    a = _as_mono(x_in)
    b = _as_mono(x_out)
    if a.size < 64 or b.size < 64:
        return 0
    size = min(a.size, b.size)
    a = a[:size]
    b = b[:size]
    window = max(4, int(round(0.001 * sr)))
    kernel = np.ones(window) / float(window)
    env_a = np.sqrt(np.maximum(np.convolve(a * a, kernel, mode="same"), 0.0))
    env_b = np.sqrt(np.maximum(np.convolve(b * b, kernel, mode="same"), 0.0))
    env_a = env_a[::envelope_hop]
    env_b = env_b[::envelope_hop]
    if env_a.size < 8 or env_b.size < 8:
        return 0
    env_a = env_a - float(np.mean(env_a))
    env_b = env_b - float(np.mean(env_b))
    corr = np.correlate(env_a, env_b, mode="full")
    # the part of the correlation that belongs to a delay of zero or more
    positive = corr[env_b.size - 1 :]
    limit = max(1, int(max_latency) // int(envelope_hop))
    positive = positive[: limit + 1]
    if positive.size == 0:
        return 0
    best = int(np.argmax(positive))
    norm = float(np.sqrt(np.dot(env_a, env_a) * np.dot(env_b, env_b)))
    peak = float(positive[best]) / norm if norm > 0.0 else 0.0
    if peak < 0.5:
        # The correlation does not show a convincing delay.
        return 0
    return int(best * envelope_hop)


def block_coherence_db(reference: np.ndarray, test: np.ndarray) -> float:
    """The RMS difference between two renders of the same signal, in dB.

    The number is relative to the level of the reference, so it is negative:
    ``-20 dB`` means the two renders differ by 20 dB below the signal.
    """
    ref = _as_mono(reference)
    other = _as_mono(test)
    n = min(ref.size, other.size)
    if n == 0:
        return 0.0
    ref = ref[:n]
    other = other[:n]
    diff = other - ref
    rms_ref = float(np.sqrt(np.mean(ref * ref)))
    rms_diff = float(np.sqrt(np.mean(diff * diff)))
    if rms_ref <= 0.0:
        return 0.0
    return max(20.0 * math.log10(max(rms_diff, 1e-30) / rms_ref), -200.0)


def _shift(a: np.ndarray, b: np.ndarray, latency: int) -> tuple[np.ndarray, np.ndarray]:
    """Align two signals by ``latency`` samples (positive = ``b`` is late)."""
    a = _as_mono(a)
    b = _as_mono(b)
    if latency > 0:
        b = b[latency:]
        a = a[: b.size]
    elif latency < 0:
        a = a[-latency:]
        b = b[: a.size]
    n = min(a.size, b.size)
    return a[:n], b[:n]


# ---------------------------------------------------------------------------
# the internal gain curve
# ---------------------------------------------------------------------------
def make_gain_probe(base: type, hook: str) -> type:
    """Return a subclass of ``base`` that records the curve ``hook`` returns.

    The subclass only *observes* the module: the algorithm is untouched.
    """
    original = getattr(base, hook)

    class Probe(base):  # type: ignore[misc, valid-type]
        """The module with a recorder on its gain curve."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.captures: list[np.ndarray] = []

        def clear(self) -> None:
            """Drop the recorded curves."""
            self.captures.clear()

    def wrapped(self, *args, **kwargs):
        out = original(self, *args, **kwargs)
        self.captures.append(np.asarray(out, dtype=np.float64).copy())
        return out

    setattr(Probe, hook, wrapped)

    def gain_curve(self) -> np.ndarray | None:
        """The captured gain curve (``(frames, bins)``) or ``None``."""
        if not self.captures:
            return None
        return max(self.captures, key=lambda g: g.size)

    Probe.gain_curve = property(gain_curve)  # type: ignore[attr-defined]
    return Probe


def internal_gain_variance(
    gain_db: np.ndarray | None,
    band: tuple[float, float],
    sr: int = SR,
    skip: float = 0.25,
) -> float | None:
    """Temporal variance of a gain curve inside a band (dB^2).

    A stationary noise bed must produce a steady attenuation; a gain curve
    that breathes from frame to frame is the signature of musical noise.
    """
    if gain_db is None:
        return None
    g = np.asarray(gain_db, dtype=np.float64)
    if g.ndim != 2 or g.size == 0:
        return None
    bins = g.shape[1]
    freqs = np.fft.rfftfreq(2 * (bins - 1), 1.0 / float(sr))[:bins]
    sel = (freqs >= float(band[0])) & (freqs <= float(band[1]))
    if not np.any(sel):
        return None
    start = int(g.shape[0] * float(skip))
    window = g[start:, sel]
    if window.shape[0] < 3:
        return None
    return float(np.mean(window.var(axis=0)))


# ---------------------------------------------------------------------------
# the measurement
# ---------------------------------------------------------------------------
@dataclass
class Measurement:
    """The numbers the instrument produced for one module and one case."""

    module: str
    case: str
    band: tuple[float, float]
    removed_db: float = 0.0
    kept_db: float = 0.0
    gain_var_db2: float = 0.0
    gain_std_db: float = 0.0
    gain_var_smooth_db2: float = 0.0
    internal_gain_var_db2: float | None = None
    latency_samples: int = 0
    block_coherence_db: float = 0.0
    event_reduction_db: float | None = None
    programme_shift_db: float | None = None
    reference_error_db: float | None = None
    snr_improvement_db: float = 0.0
    tone_shift_db: dict[str, float] = field(default_factory=dict)
    tone_snr_db: dict[str, float] = field(default_factory=dict)
    output_length: int = 0
    output_finite: bool = True
    source_sha: str = ""
    source_changed: bool = False
    notes: str = ""

    def as_dict(self) -> dict:
        """Flat dictionary for the JSON results."""
        return {
            "module": self.module,
            "case": self.case,
            "band": [float(self.band[0]), float(self.band[1])],
            "removed_db": round(self.removed_db, 4),
            "kept_db": round(self.kept_db, 4),
            "gain_var_db2": round(self.gain_var_db2, 6),
            "gain_std_db": round(self.gain_std_db, 4),
            "gain_var_smooth_db2": round(self.gain_var_smooth_db2, 6),
            "internal_gain_var_db2": (
                None if self.internal_gain_var_db2 is None
                else round(self.internal_gain_var_db2, 6)
            ),
            "latency_samples": int(self.latency_samples),
            "block_coherence_db": round(self.block_coherence_db, 4),
            "event_reduction_db": (
                None if self.event_reduction_db is None
                else round(self.event_reduction_db, 4)
            ),
            "programme_shift_db": (
                None if self.programme_shift_db is None
                else round(self.programme_shift_db, 4)
            ),
            "reference_error_db": (
                None if self.reference_error_db is None
                else round(self.reference_error_db, 4)
            ),
            "snr_improvement_db": round(self.snr_improvement_db, 4),
            "tone_shift_db": {k: round(v, 4) for k, v in self.tone_shift_db.items()},
            "tone_snr_db": {k: round(v, 4) for k, v in self.tone_snr_db.items()},
            "output_length": int(self.output_length),
            "output_finite": bool(self.output_finite),
            "source_sha": self.source_sha,
            "source_changed": bool(self.source_changed),
            "notes": self.notes,
        }


def source_fingerprint(code: str) -> str:
    """The SHA-256 of the source file of a module.

    The fingerprint is what makes a measurement auditable: if the module's
    source changes between two runs, the two runs measured different
    programs and their numbers are not comparable.
    """
    try:
        cls = registry.get(code)
    except KeyError:
        return ""
    path = getattr(cls, "__module__", "")
    module = sys.modules.get(path)
    file = getattr(module, "__file__", "")
    if not file:
        return ""
    try:
        with open(file, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return ""


def _build(code: str, params: dict | None, sr: int) -> object:
    """Instantiate a module (with a gain probe when the module publishes one)."""
    cls = registry.get(code)
    hook = INTERNAL_GAIN_HOOKS.get(code)
    factory: Callable[..., object] = make_gain_probe(cls, hook) if hook else cls
    module = factory(sr, 1)
    if params:
        for key, value in params.items():
            if module.params.has(key):
                module.params.set(key, value)
    return module


def _event_masks(events: Sequence[tuple[int, int]], length: int) -> tuple[np.ndarray, np.ndarray]:
    """Boolean masks for the samples inside and outside the events."""
    inside = np.zeros(length, dtype=bool)
    for start, end in events:
        inside[max(0, int(start)) : min(length, int(end))] = True
    return inside, ~inside


def _frames_to_mask(sample_mask: np.ndarray, n_fft: int = N_FFT, hop: int = HOP) -> np.ndarray:
    """Lift a per-sample mask onto the analysis frame grid."""
    arr = np.asarray(sample_mask, dtype=bool)
    if arr.size < n_fft:
        arr = np.concatenate([arr, np.zeros(n_fft - arr.size, dtype=bool)])
    views = np.lib.stride_tricks.sliding_window_view(arr, n_fft)
    frames = views[::hop][: 1 + (views.shape[0] - 1) // hop]
    return frames.any(axis=1)


def measure(
    code: str,
    case: Case | str,
    params: dict | None = None,
    band: tuple[float, float] | None = None,
    tones: Iterable[float] | None = None,
    sr: int = SR,
    learn: bool = True,
    warmup: bool = True,
    block_size: int = 4096,
) -> Measurement:
    """Measure one module over one case.

    Parameters
    ----------
    code:
        The module code (as in the registry).
    case:
        The case, or its name.
    params:
        Optional parameter overrides for the module.
    band:
        Optional override of the case's noise band.
    tones:
        Optional override of the case's wanted partials.
    sr:
        Sample rate.
    learn:
        Feed the case's noise part to ``learn()`` when the module supports it.
    warmup:
        Process the case's warm-up signal once so the trackers settle before
        the measured render.
    block_size:
        The block size used for the block-coherence measurement.

    Returns
    -------
    Measurement
        The numbers the instrument produced.
    """
    if isinstance(case, str):
        case = make_case(case)
    use_band = tuple(band) if band is not None else tuple(case.band)
    use_tones = tuple(tones) if tones is not None else tuple(case.tones)

    module = _build(code, params, sr)
    if learn and bool(getattr(module, "supports_learn", False)):
        try:
            module.learn(case.noise[:, None])
        except Exception:  # noqa: BLE001 - a module may refuse to learn
            pass
    if warmup and case.warmup is not None:
        try:
            module.process(case.warmup[:, None])
        except Exception:  # noqa: BLE001
            pass
        # The warm-up is a settling step and not material.  The rule of the
        # family scales a signal by the loudest thing the module has
        # produced in the session it is in, and the warm-up is louder than
        # the material that is measured here: without this the instrument
        # would measure the rule and not the module.  The state of the rule
        # is cleared, the module's own state is not.
        reset_ceiling = getattr(module, "reset_ceiling", None)
        if callable(reset_ceiling):
            reset_ceiling()

    mixed = case.mixed
    note = ""
    one_block = True
    try:
        out = _as_mono(np.asarray(module.process(mixed[:, None]), dtype=np.float64))
    except Exception as exc:  # noqa: BLE001 - a module may refuse a big block
        one_block = False
        note = (
            "the module cannot process the whole block in one go (%s: %s); it "
            "was measured in blocks of %d samples"
            % (type(exc).__name__, exc, block_size)
        )
        pieces = []
        for start in range(0, mixed.size, block_size):
            chunk = mixed[start : start + block_size]
            rendered = np.asarray(module.process(chunk[:, None]), dtype=np.float64)
            pieces.append(_as_mono(rendered))
        out = np.concatenate(pieces) if pieces else np.zeros(0)
    result = Measurement(module=code, case=case.name, band=use_band)
    result.output_length = int(out.size)
    result.output_finite = bool(np.all(np.isfinite(out)))
    result.source_sha = source_fingerprint(code)
    result.notes = note
    sha_before = result.source_sha

    latency = estimate_latency(mixed, out, sr)
    result.latency_samples = int(latency)
    aligned_in, aligned_out = _shift(mixed, out, latency)

    # -- what is removed ----------------------------------------------------
    before = band_power(aligned_in, use_band[0], use_band[1], sr)
    after = band_power(aligned_out, use_band[0], use_band[1], sr)
    result.removed_db = 10.0 * math.log10(max(before, 1e-30) / max(after, 1e-30))

    # -- what is kept -------------------------------------------------------
    # In a restoration case the input carries the artefact: a hard clipper
    # inflates the wanted partials, so the level that has to survive is the
    # one of the reference and not the one of the input.  The movement of the
    # partial is measured against the reference when the case carries one.
    kept = 0.0
    reference = case.reference
    for freq in use_tones:
        amps_in = _frame_amplitudes(aligned_in, freq, sr)
        amps_out = _frame_amplitudes(aligned_out, freq, sr)
        count = min(amps_in.size, amps_out.size)
        if reference is not None:
            ref = _as_mono(reference)
            if latency > 0:
                ref = ref[latency:]
            amps_in = _frame_amplitudes(ref, freq, sr)
            count = min(count, amps_in.size)
        mask = _wanted_mask(case.clean, case.noise, freq, count, sr)
        level_in = _level_db(amps_in[:count], mask)
        level_out = _level_db(amps_out[:count], mask)
        shift = level_out - level_in
        key = f"{freq:.0f}Hz"
        result.tone_shift_db[key] = shift
        result.tone_snr_db[key] = partial_snr_db(case.clean, case.noise, freq, sr)
        if result.tone_snr_db[key] >= TONE_SNR_FLOOR_DB:
            kept = max(kept, abs(shift))
    result.kept_db = kept

    # -- the gain curve -----------------------------------------------------
    event_frames = None
    if case.events:
        inside, _outside = _event_masks(case.events, aligned_in.size)
        event_frames = _frames_to_mask(inside)
    curve = effective_gain_curve(aligned_in, aligned_out, use_band, sr, exclude=event_frames)
    if curve.size >= 3:
        result.gain_var_db2 = float(np.var(curve))
        result.gain_std_db = float(np.std(curve))
    smooth = effective_gain_curve(
        aligned_in, aligned_out, use_band, sr, exclude=event_frames, smooth_frames=SMOOTH_FRAMES
    )
    if smooth.size >= 3:
        result.gain_var_smooth_db2 = float(np.var(smooth))
    internal = getattr(module, "gain_curve", None)
    if internal is not None:
        result.internal_gain_var_db2 = internal_gain_variance(internal, use_band, sr)

    # -- the impulsive artefacts -------------------------------------------
    if case.events:
        inside, outside = _event_masks(case.events, aligned_in.size)
        truth = _as_mono(case.clean)
        truth = truth[: aligned_in.size] if truth.size >= aligned_in.size else np.concatenate(
            [truth, np.zeros(aligned_in.size - truth.size)]
        )
        # what is left of the artefact: the difference between the signal and
        # the truth it should have been
        err_in = aligned_in - truth
        err_out = aligned_out - truth
        e_before = float(np.sqrt(np.mean(err_in[inside] ** 2))) if np.any(inside) else 0.0
        e_after = float(np.sqrt(np.mean(err_out[inside] ** 2))) if np.any(inside) else 0.0
        result.event_reduction_db = 10.0 * math.log10(
            max(e_before, 1e-30) / max(e_after, 1e-30)
        )
        p_before = band_power(aligned_in, use_band[0], use_band[1], sr, mask=outside)
        p_after = band_power(aligned_out, use_band[0], use_band[1], sr, mask=outside)
        result.programme_shift_db = 10.0 * math.log10(
            max(p_after, 1e-30) / max(p_before, 1e-30)
        )

    # -- the restoration error against the reference ------------------------
    if case.reference is not None:
        ref = _as_mono(case.reference)
        size = min(ref.size, aligned_in.size, aligned_out.size)
        err_before = float(np.sqrt(np.mean((aligned_in[:size] - ref[:size]) ** 2)))
        err_after = float(np.sqrt(np.mean((aligned_out[:size] - ref[:size]) ** 2)))
        result.reference_error_db = 20.0 * math.log10(
            max(err_before, 1e-30) / max(err_after, 1e-30)
        )

    # -- the signal to noise ratio -----------------------------------------
    clean_p = float(np.dot(case.clean, case.clean))
    noise_p = float(np.dot(case.noise, case.noise))
    snr_in = 10.0 * math.log10(max(clean_p, 1e-30) / max(noise_p, 1e-30))
    result.snr_improvement_db = projection_snr(aligned_out, case.clean) - snr_in

    # -- the block coherence ------------------------------------------------
    if one_block and out.size == mixed.size:
        try:
            module2 = _build(code, params, sr)
            if learn and bool(getattr(module2, "supports_learn", False)):
                try:
                    module2.learn(case.noise[:, None])
                except Exception:  # noqa: BLE001
                    pass
            if warmup and case.warmup is not None:
                try:
                    module2.process(case.warmup[:, None])
                except Exception:  # noqa: BLE001
                    pass
            pieces = []
            for start in range(0, mixed.size, block_size):
                chunk = mixed[start : start + block_size]
                rendered = np.asarray(module2.process(chunk[:, None]), dtype=np.float64)
                pieces.append(_as_mono(rendered))
            blocked = np.concatenate(pieces) if pieces else np.zeros(0)
            if blocked.size == out.size:
                result.block_coherence_db = block_coherence_db(out, blocked)
        except Exception:  # noqa: BLE001 - a module may refuse small blocks
            result.block_coherence_db = float("nan")
    elif not one_block:
        result.block_coherence_db = float("nan")
        if result.notes:
            result.notes += "; the block coherence is not defined"
    else:
        result.block_coherence_db = float("nan")
        result.notes = "the module changes the length; the block coherence is not defined"

    # -- the audit trail ----------------------------------------------------
    sha_after = source_fingerprint(code)
    result.source_changed = bool(sha_before) and sha_after != sha_before
    if result.source_changed:
        result.notes = (result.notes + "; " if result.notes else "") + (
            "the source of the module changed while it was being measured"
        )
    return result


def projection_snr(out: np.ndarray, clean: np.ndarray) -> float:
    """SNR of ``out`` against the known clean signal (in dB).

    The clean part is the orthogonal projection of ``out`` onto ``clean``;
    everything that is left over counts as error.
    """
    o = _as_mono(out)
    c = _as_mono(clean)
    n = min(o.size, c.size)
    o = o[:n]
    c = c[:n]
    denom = float(np.dot(c, c))
    if denom <= 0.0:
        return 0.0
    scale = float(np.dot(o, c)) / denom
    sig = scale * c
    err = o - sig
    return float(
        10.0 * math.log10(max(float(np.dot(sig, sig)), 1e-30) / max(float(np.dot(err, err)), 1e-30))
    )
