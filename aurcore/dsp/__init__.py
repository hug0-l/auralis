"""
aurcore.dsp — the DSP foundation layer of Auralis.

This package holds the primitive building blocks every processing module is
built from:

* :mod:`aurcore.dsp.util` — level, size, envelope and array helpers.
* :mod:`aurcore.dsp.fft` — real FFT wrappers, magnitude/phase helpers.
* :mod:`aurcore.dsp.windows` — exact window functions and COLA constants.
* :mod:`aurcore.dsp.stft` — the short-time Fourier transform engine.
* :mod:`aurcore.dsp.resample` — band-limited sample rate conversion.
* :mod:`aurcore.dsp.filters` — biquad design and stateful filtering.

Conventions
-----------
* Audio samples are ``float64`` in ``[-1, 1]``; ``1.0`` is 0 dBFS.
* Levels are decibels, frequencies hertz, times seconds, sample rates Hz.
* Signals are 1-D ``(n_samples,)`` or 2-D ``(n_samples, n_channels)``.
* Only NumPy is required; SciPy is used when it is installed and every SciPy
  call has a NumPy fallback.
"""
from __future__ import annotations

from .fft import (
    fft_pow2,
    fft_size,
    ifft_pow2,
    irfft,
    magnitude,
    magnitude_db,
    phase,
    rfft,
    to_complex,
)
from .filters import (
    Biquad,
    Butterworth,
    Cascade,
    DCBlocker,
    LinkwitzRiley,
    OnePole,
    biquad_allpass,
    biquad_bandpass,
    biquad_highpass,
    biquad_highshelf,
    biquad_lowpass,
    biquad_lowshelf,
    biquad_notch,
    biquad_peaking,
    butterworth_highpass,
    butterworth_lowpass,
    linkwitz_riley_highpass,
    linkwitz_riley_lowpass,
)
from .resample import Resampler, design_prototype, resample, resample_stream
from .stft import STFT, Spectrogram, istft, magnitude_spectrogram, spectrogram_db, stft
from .util import (
    apply_gain_db,
    bin_frequencies,
    bin_to_freq,
    crossfade,
    db_to_lin,
    dc_block,
    envelope_follower,
    fade,
    fade_in,
    fade_out,
    freq_to_bin,
    is_pow2,
    lin_to_db,
    next_pow2,
    normalize_peak,
    ola,
    overlap_add,
    peak_dbfs,
    running_peak,
    running_rms,
    safe_divide,
    soft_limiter,
)
from .windows import (
    blackman,
    blackman_harris,
    colA,
    cola_norm,
    cola_product,
    cola_ripple,
    cola_sum,
    gaussian,
    hamming,
    hann,
    kaiser,
    nuttall,
    rectangular,
    sqrt_hann,
    window,
    window_names,
)
from .wola import WolaBank, cola_window

__all__ = [
    # util
    "db_to_lin",
    "lin_to_db",
    "next_pow2",
    "is_pow2",
    "freq_to_bin",
    "bin_to_freq",
    "bin_frequencies",
    "running_rms",
    "running_peak",
    "envelope_follower",
    "soft_limiter",
    "dc_block",
    "safe_divide",
    "fade_in",
    "fade_out",
    "fade",
    "crossfade",
    "overlap_add",
    "ola",
    "apply_gain_db",
    "normalize_peak",
    "peak_dbfs",
    # fft
    "fft_size",
    "rfft",
    "irfft",
    "magnitude",
    "magnitude_db",
    "phase",
    "to_complex",
    "fft_pow2",
    "ifft_pow2",
    # windows
    "hann",
    "hamming",
    "blackman",
    "blackman_harris",
    "kaiser",
    "gaussian",
    "nuttall",
    "rectangular",
    "sqrt_hann",
    "window",
    "window_names",
    "colA",
    "cola_sum",
    "cola_norm",
    "cola_ripple",
    "cola_product",
    # stft
    "STFT",
    "Spectrogram",
    "stft",
    "istft",
    "spectrogram_db",
    "magnitude_spectrogram",
    # resample
    "resample",
    "resample_stream",
    "Resampler",
    "design_prototype",
    # filters
    "biquad_lowpass",
    "biquad_highpass",
    "biquad_bandpass",
    "biquad_notch",
    "biquad_peaking",
    "biquad_lowshelf",
    "biquad_highshelf",
    "biquad_allpass",
    "Biquad",
    "Cascade",
    "Butterworth",
    "LinkwitzRiley",
    "OnePole",
    "DCBlocker",
    "butterworth_lowpass",
    "butterworth_highpass",
    "linkwitz_riley_lowpass",
    "linkwitz_riley_highpass",
    # wola
    "WolaBank",
    "cola_window",
]

# ---------------------------------------------------------------------------
# Sibling DSP modules
# ---------------------------------------------------------------------------
# The modules below are owned by other contributors to the DSP layer.  They are
# re-exported here (this file is the single public surface of ``aurcore.dsp``)
# with a guarded import, so a problem inside one of them can never break
# ``import aurcore.dsp`` for everybody else.
try:  # pragma: no cover - depends on the neighbouring modules
    # NOTE: the module exposes a function called ``dither``; re-exporting it
    # under that name would shadow the module itself in this namespace, so the
    # function is left inside the module (``aurcore.dsp.dither.dither``).
    from . import dither as _dither_module  # noqa: F401
    from .dither import (  # noqa: F401
        bit_depth_step,
        dither_amount_scale,
        noise_shaping_presets,
        quantize,
    )

    __all__ += [
        "quantize",
        "bit_depth_step",
        "noise_shaping_presets",
        "dither_amount_scale",
    ]
except Exception:  # pragma: no cover - optional sibling module
    pass

try:  # pragma: no cover - depends on the neighbouring modules
    from .loudness import (  # noqa: F401
        LoudnessMeasurements,
        channel_weights,
        integrated_loudness,
        k_weight,
        k_weighting_coefficients,
        loudness_measurements,
        loudness_range,
        momentary_loudness,
        short_term_loudness,
        true_peak,
        true_peak_linear,
    )

    __all__ += [
        "k_weighting_coefficients",
        "k_weight",
        "integrated_loudness",
        "momentary_loudness",
        "short_term_loudness",
        "loudness_range",
        "true_peak",
        "true_peak_linear",
        "loudness_measurements",
        "LoudnessMeasurements",
        "channel_weights",
    ]
except Exception:  # pragma: no cover - optional sibling module
    pass

try:  # pragma: no cover - depends on the neighbouring modules
    from .phase_vocoder import (  # noqa: F401
        PVState,
        PhaseVocoder,
        pitch_shift,
        resample_to_length,
        time_stretch,
        wsola_time_stretch,
    )

    # There are two time/pitch families in this package and they are not
    # interchangeable: the phase-vocoder one takes an explicit FFT size and
    # sample rate, while the quality-based one takes a quality preset and an
    # adaptive window.  Both are exported, but the phase-vocoder variants also
    # get explicit names so a caller can never pick the wrong one by accident.
    from .phase_vocoder import pitch_shift as phase_vocoder_pitch_shift
    from .phase_vocoder import time_stretch as phase_vocoder_time_stretch

    __all__ += [
        "PhaseVocoder",
        "PVState",
        "wsola_time_stretch",
        "resample_to_length",
        "time_stretch",
        "pitch_shift",
        "phase_vocoder_time_stretch",
        "phase_vocoder_pitch_shift",
    ]
except Exception:  # pragma: no cover - optional sibling module
    pass

try:  # pragma: no cover - depends on the neighbouring modules
    from .spectral import (  # noqa: F401
        apply_gain,
        apply_gain_ola,
        decision_directed_snr,
        estimate_noise_profile,
        interpolate_band,
        masking_model,
        om_lsa_gain,
        smooth_gain_curve,
        spectral_mask,
        spectral_subtract,
        whiten,
        wiener_gain,
    )

    __all__ += [
        "smooth_gain_curve",
        "apply_gain",
        "apply_gain_ola",
        "estimate_noise_profile",
        "spectral_subtract",
        "om_lsa_gain",
        "decision_directed_snr",
        "wiener_gain",
        "spectral_mask",
        "whiten",
        "masking_model",
        "interpolate_band",
    ]
except Exception:  # pragma: no cover - optional sibling module
    pass

try:  # pragma: no cover - depends on the neighbouring modules
    from .timepitch import (  # noqa: F401
        QUALITY_PRESETS,
        WindowSpec,
        adaptive_window,
    )

    __all__ += [
        "WindowSpec",
        "adaptive_window",
        "QUALITY_PRESETS",
    ]
except Exception:  # pragma: no cover - optional sibling module
    pass
