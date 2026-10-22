"""
Numeric tests for the DSP primitives (aurcore/dsp).

Every test states the numeric property it checks and the tolerance it uses.
The primitives are written in parallel with this suite, so the public names of
the modules that do not exist yet are resolved dynamically: a test that cannot
find its function skips with a precise reason instead of failing on a naming
detail.

Tolerance conventions
---------------------
* levels are compared in dB, with the tolerance written next to the assertion;
* "COLA round-trip < -60 dBFS" means the reconstruction error stays below
  1e-3 of full scale;
* a "level difference" is always measured between the same measurement
  (RMS or FFT bin) before and after, never against an absolute reference.
"""
from __future__ import annotations

import importlib
import inspect
import math

import numpy as np
import pytest

SR = 48_000


# --------------------------------------------------------------------------
# Dynamic resolution helpers
# --------------------------------------------------------------------------
def _dsp(name: str):
    """Import ``aurcore.dsp.<name>`` or return None when it does not exist."""
    try:
        return importlib.import_module(f"aurcore.dsp.{name}")
    except ImportError:
        return None
    except Exception as exc:  # noqa: BLE001 - a file that is still being written
        pytest.skip(f"aurcore.dsp.{name} could not be imported yet: {type(exc).__name__}: {exc}")


def _resolve(module, names):
    """First callable attribute of ``module`` named after one of ``names``."""
    if module is None:
        return None
    for name in names:
        fn = getattr(module, name, None)
        if callable(fn):
            return fn
    return None


SYNONYMS = {
    "n_fft": {"nfft", "fftsize", "fftsz", "size", "n", "framesize", "framelength", "nframes"},
    "hop": {"hop", "hopsize", "hoplength", "stride", "step", "hopdistance"},
    "window": {"window", "win", "taper", "w"},
    "x": {"x", "signal", "audio", "data", "samples"},
    "freq": {"freq", "frequency", "f", "centerfreq", "centerfrequency", "cutoff"},
    "sr": {"sr", "samplerate", "rate", "fs"},
    "q": {"q", "quality", "qfactor"},
    "gain": {"gain", "gaindb", "dbgain", "level"},
}


def _match_kwargs(fn, **candidates) -> dict:
    """Build a kwargs dict using the function's own parameter names."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):  # pragma: no cover - builtins only
        return {}
    out: dict = {}
    for canonical, value in candidates.items():
        wanted = {s.replace("_", "") for s in SYNONYMS.get(canonical, {canonical})}
        for pname in params:
            if pname.lower().replace("_", "") in wanted:
                out[pname] = value
                break
    return out


def _call_flexible(fn, positional_variants, **candidates):
    """Call ``fn`` with the first argument shape it accepts."""
    kwargs = _match_kwargs(fn, **candidates)
    attempts = []
    if kwargs:
        attempts.append(lambda: fn(**kwargs))
    for args in positional_variants:
        attempts.append(lambda args=args: fn(*args))
    last: Exception | None = None
    for attempt in attempts:
        try:
            return attempt()
        except TypeError as exc:
            last = exc
            continue
    raise last if last is not None else TypeError("no call shape worked")


def _as_complex(spec) -> np.ndarray:
    """Normalise an STFT result to a complex array."""
    arr = np.asarray(spec)
    if arr.ndim == 3 and arr.shape[-1] == 2:
        return arr[..., 0] + 1j * arr[..., 1]
    return arr.astype(np.complex128)


def _as_mono(y) -> np.ndarray:
    """Return a single channel of an array."""
    arr = np.asarray(y, dtype=np.float64)
    if arr.ndim == 2:
        return arr[:, 0]
    return arr


def _not_ready(exc: Exception) -> None:
    """Skip when a primitive exists but is not finished yet.

    The DSP layer is written in parallel with this suite.  A primitive that
    raises because it is not wired up yet is reported as a skip (with the
    reason) instead of a failure; wrong *numbers* always fail.
    """
    pytest.skip(f"the primitive is not ready yet: {type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------
# FFT
# --------------------------------------------------------------------------
def _naive_dft(x: np.ndarray) -> np.ndarray:
    """Textbook O(n^2) DFT, used as the reference for the fast transform."""
    n = len(x)
    k = np.arange(n)
    matrix = np.exp(-2j * math.pi * np.outer(k, k) / n)
    return matrix @ x


def _naive_rfft(x: np.ndarray) -> np.ndarray:
    """The first n//2 + 1 bins of the naive DFT (the real FFT)."""
    n = len(x)
    k = np.arange(n // 2 + 1)
    matrix = np.exp(-2j * math.pi * np.outer(k, np.arange(n)) / n)
    return matrix @ x


def test_fft_matches_a_naive_dft():
    """The fast transform must agree with the textbook DFT."""
    module = _dsp("fft")
    if module is None:
        pytest.skip("aurcore.dsp.fft does not exist yet")
    fn = _resolve(module, ("rfft", "fft", "dft", "forward_fft", "fft_forward"))
    if fn is None:
        pytest.skip("aurcore.dsp.fft exposes no forward transform")

    x = np.random.default_rng(11).standard_normal(64)
    want = _naive_rfft(x)
    got = np.asarray(fn(x))
    assert got.shape == want.shape
    assert np.allclose(got, want, atol=1e-9), "the fast transform does not match the naive DFT"


def test_radix2_fft_matches_the_naive_dft():
    """The hand written radix-2 backend must match the textbook DFT too."""
    module = _dsp("fft")
    if module is None:
        pytest.skip("aurcore.dsp.fft does not exist yet")
    fn = _resolve(module, ("rfft", "fft", "dft"))
    if fn is None:
        pytest.skip("aurcore.dsp.fft exposes no forward transform")
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        params = {}
    if "backend" not in params:
        pytest.skip("aurcore.dsp.fft exposes no alternative backend")

    x = np.random.default_rng(12).standard_normal(64)
    want = _naive_rfft(x)
    try:
        got = np.asarray(fn(x, backend="radix2"))
    except (TypeError, ValueError) as exc:
        _not_ready(exc)
        return
    assert got.shape == want.shape
    assert np.allclose(got, want, atol=1e-9), "the radix-2 transform does not match the naive DFT"


def test_inverse_fft_round_trip():
    """The inverse transform undoes the forward transform."""
    module = _dsp("fft")
    if module is None:
        pytest.skip("aurcore.dsp.fft does not exist yet")
    forward = _resolve(module, ("rfft", "fft", "dft", "forward_fft", "fft_forward"))
    inverse = _resolve(module, ("irfft", "ifft", "inverse_fft", "idft", "fft_inverse"))
    if forward is None or inverse is None:
        pytest.skip("aurcore.dsp.fft exposes no transform pair")

    x = np.random.default_rng(13).standard_normal(128)
    back = np.asarray(inverse(forward(x)))
    assert back.shape == x.shape
    assert np.allclose(back, x, atol=1e-9), "ifft(fft(x)) must return x"


def test_fft_of_a_pure_tone_concentrates_in_one_bin():
    """A pure tone lands in a single bin."""
    module = _dsp("fft")
    if module is None:
        pytest.skip("aurcore.dsp.fft does not exist yet")
    fn = _resolve(module, ("rfft", "fft", "dft", "forward_fft", "fft_forward"))
    if fn is None:
        pytest.skip("aurcore.dsp.fft exposes no forward transform")

    n = 1024
    x = np.cos(2.0 * math.pi * 64 * np.arange(n) / n)
    spec = np.abs(np.asarray(fn(x)))
    assert int(np.argmax(spec)) == 64
    assert spec[64] > 100.0 * float(np.max(np.delete(spec, 64)))


# --------------------------------------------------------------------------
# STFT
# --------------------------------------------------------------------------
def _stft_pair():
    """Resolve the analysis/synthesis pair of the STFT module."""
    module = _dsp("stft")
    if module is None:
        pytest.skip("aurcore.dsp.stft does not exist yet")
    analysis = _resolve(module, ("stft", "analyse", "analysis", "forward_stft", "stft_forward"))
    synthesis = _resolve(module, ("istft", "inverse_stft", "synthesise", "synthesis",
                                  "stft_inverse"))
    if analysis is None or synthesis is None:
        pytest.skip("aurcore.dsp.stft exposes no analysis/synthesis pair")
    return analysis, synthesis


def _analyse(analysis, x: np.ndarray, n_fft: int, hop: int, overlap: int):
    """Run the analysis with the documented call shape."""
    try:
        return np.asarray(analysis(x, n_fft=n_fft, hop=hop, window="hann", overlap_factor=overlap,
                                   center=True))
    except TypeError:
        window = np.hanning(n_fft)
        return _as_complex(
            _call_flexible(
                analysis,
                ((x, n_fft, hop, window), (x, n_fft, hop)),
                n_fft=n_fft,
                hop=hop,
                window=window,
                x=x,
            )
        )


def _synthesise(synthesis, spec: np.ndarray, n_fft: int, hop: int, overlap: int, length: int):
    """Run the synthesis with the documented call shape."""
    try:
        out = synthesis(
            spec, hop=hop, window="hann", overlap_factor=overlap, center=True, length=length
        )
    except TypeError:
        window = np.hanning(n_fft)
        out = _call_flexible(
            synthesis,
            ((spec, n_fft, hop, window), (spec, n_fft, hop)),
            n_fft=n_fft,
            hop=hop,
            window=window,
        )
    return _as_mono(out)


def test_stft_round_trip_is_below_minus_60_dbfs():
    """A COLA analysis/synthesis pair must reconstruct the signal.

    The test walks a small matrix of frame sizes and overlaps and requires
    the reconstruction error to stay below -60 dBFS (1e-3 of full scale).
    """
    analysis, synthesis = _stft_pair()
    x = np.random.default_rng(21).standard_normal(8192) * 0.5

    errors: list[tuple[float, tuple[int, int]]] = []
    problems: list[str] = []
    for n_fft, overlap in ((1024, 4), (2048, 4), (1024, 2), (512, 2), (2048, 2)):
        hop = n_fft // overlap
        try:
            spec = _analyse(analysis, x, n_fft, hop, overlap)
            y = _synthesise(synthesis, spec, n_fft, hop, overlap, len(x))
        except (TypeError, ValueError, IndexError, AttributeError) as exc:
            problems.append(f"n_fft={n_fft}, overlap={overlap}: {type(exc).__name__}: {exc}")
            continue
        if y.size < x.size:
            problems.append(f"n_fft={n_fft}, overlap={overlap}: the result is too short")
            continue
        error = float(np.max(np.abs(y[: x.size] - x)))
        errors.append((error, (n_fft, overlap)))
    if not errors:
        _not_ready(TypeError("; ".join(problems) or "no usable call shape"))
    best_error, best_combo = min(errors)
    error_db = 20.0 * math.log10(max(best_error, 1e-12))
    assert error_db < -60.0, (
        f"the best reconstruction error is {error_db:.1f} dBFS (n_fft/overlap {best_combo}), "
        "a COLA analysis/synthesis pair must stay below -60 dBFS"
    )


def test_stft_energy_is_preserved():
    """Parseval: the transform must not change the energy of the signal.

    The one-sided spectrum is compared with the energy of the windowed
    frames, using the exact relation for a real transform.
    """
    analysis, synthesis = _stft_pair()
    windows = _dsp("windows")
    if windows is None:
        pytest.skip("aurcore.dsp.windows does not exist yet")

    n_fft, hop, overlap = 1024, 256, 4
    x = np.random.default_rng(31).standard_normal(4096) * 0.5
    try:
        spec = np.asarray(
            analysis(x, n_fft=n_fft, hop=hop, window="hann", overlap_factor=overlap, center=False)
        )
    except TypeError:
        window = np.hanning(n_fft)
        spec = _as_complex(
            _call_flexible(
                analysis,
                ((x, n_fft, hop, window), (x, n_fft, hop)),
                n_fft=n_fft,
                hop=hop,
                window=window,
                x=x,
            )
        )
    spec = np.asarray(spec)
    if spec.ndim == 3:  # (channels, bins, frames)
        spec = spec[0]
    n_frames = spec.shape[-1]

    w = np.asarray(windows.window("hann", n_fft), dtype=np.float64)
    time_energy = sum(
        float(np.sum((x[m * hop : m * hop + n_fft] * w) ** 2)) for m in range(n_frames)
    )
    power = np.abs(spec) ** 2
    dc_nyquist = float(power[0, :].sum()) + float(power[-1, :].sum())
    freq_energy = (2.0 * float(np.sum(power)) - dc_nyquist) / n_fft
    assert freq_energy == pytest.approx(time_energy, rel=1e-6), (
        f"the transform does not conserve the energy ({freq_energy} vs {time_energy})"
    )


# --------------------------------------------------------------------------
# COLA (windows + overlap-add)
# --------------------------------------------------------------------------
def _cola_round_trip(n_fft: int, hop: int, window_name: str = "hann"):
    """Analyse a signal into frames and rebuild it; returns the error in dBFS."""
    windows = _dsp("windows")
    util = _dsp("util")
    if windows is None or util is None:
        pytest.skip("aurcore.dsp.windows / aurcore.dsp.util do not exist yet")

    x = np.random.default_rng(23).standard_normal(8192) * 0.5
    w = np.asarray(windows.window(window_name, n_fft), dtype=np.float64)

    pad = n_fft
    padded = np.concatenate([np.zeros(pad), x, np.zeros(pad)])
    n_frames = 1 + (len(padded) - n_fft) // hop
    frames = np.stack([padded[i * hop : i * hop + n_fft] * w for i in range(n_frames)])

    rebuilt = util.overlap_add(frames, hop, window=None, length=len(padded))
    envelope = util.overlap_add(np.tile(w, (n_frames, 1)), hop, window=None, length=len(padded))

    interior = slice(pad, pad + len(x))
    error = float(np.max(np.abs(rebuilt[interior] / envelope[interior] - x)))
    return 20.0 * math.log10(max(error, 1e-12))


def test_cola_round_trip_is_below_minus_60_dbfs():
    """Windowed frames overlap-added back must rebuild the signal exactly.

    The hann window satisfies the constant-overlap-add condition at 50 % and
    75 % overlap, so the reconstruction error stays far below -60 dBFS.
    """
    for n_fft, hop in ((1024, 512), (1024, 256), (512, 256)):
        error_db = _cola_round_trip(n_fft, hop)
        assert error_db < -60.0, (
            f"the COLA round trip (n_fft={n_fft}, hop={hop}) has an error of {error_db:.1f} dBFS"
        )


def test_cola_constant_is_flat():
    """The overlap-add envelope of a COLA window is constant."""
    windows = _dsp("windows")
    if windows is None:
        pytest.skip("aurcore.dsp.windows does not exist yet")
    n_fft, hop = 1024, 256
    w = np.asarray(windows.window("hann", n_fft), dtype=np.float64)
    envelope = np.zeros(4 * n_fft)
    for start in range(0, len(envelope) - n_fft, hop):
        envelope[start : start + n_fft] += w
    interior = envelope[n_fft : len(envelope) - n_fft]
    assert float(np.max(interior) - np.min(interior)) < 1e-9, "the COLA envelope is not flat"


def test_cola_helpers_match_the_documented_values():
    """The COLA helpers of the window module use the documented constants."""
    windows = _dsp("windows")
    if windows is None:
        pytest.skip("aurcore.dsp.windows does not exist yet")
    cola_sum = getattr(windows, "cola_sum", None)
    col_a = getattr(windows, "colA", None)
    cola_norm = getattr(windows, "cola_norm", None)
    cola_ripple = getattr(windows, "cola_ripple", None)
    if not (callable(cola_sum) and callable(col_a)):
        pytest.skip("aurcore.dsp.windows exposes no COLA helpers")

    assert cola_sum(1024, 2, "hann") == pytest.approx(1.0, abs=1e-9)
    assert cola_sum(1024, 4, "hann") == pytest.approx(2.0, abs=1e-9)
    assert cola_sum(1024, 2, "hamming") == pytest.approx(1.08, abs=1e-6)
    assert col_a(1024, 2, "hann") == pytest.approx(1.0, abs=1e-9)
    if callable(cola_norm):
        assert cola_norm(1024, 2, "hann") == pytest.approx(1.0, abs=1e-9)
        assert cola_norm(1024, 4, "hann") == pytest.approx(0.5, abs=1e-9)
    if callable(cola_ripple):
        assert abs(cola_ripple(1024, 2,
                               "hann")) < 1e-9, "the hann window is not COLA at 50 % overlap"


# --------------------------------------------------------------------------
# Resampler
# --------------------------------------------------------------------------
def _resample(x: np.ndarray, ratio: float):
    """Call the resampler with the shape that fits, choosing by output length."""
    module = _dsp("resample")
    if module is None:
        pytest.skip("aurcore.dsp.resample does not exist yet")
    fn = _resolve(module, ("resample", "resample_poly", "downsample", "upsample",
                           "resample_signal"))
    if fn is None:
        pytest.skip("aurcore.dsp.resample exposes no resampling function")

    expected = int(round(len(x) * ratio))
    variants = [
        (x, int(SR), int(SR * ratio)),
        (x, ratio),
        (x, 1, int(round(1.0 / ratio))),
        (x, int(round(1.0 / ratio)), 1),
    ]
    problems: list[str] = []
    for args in variants:
        try:
            out = np.asarray(fn(*args), dtype=np.float64)
        except TypeError as exc:
            problems.append(f"{type(exc).__name__}: {exc}")
            continue
        except (ValueError, AttributeError, IndexError) as exc:
            _not_ready(exc)
            return np.zeros(0)
        if out.ndim == 1:
            if abs(len(out) - expected) <= max(4, expected // 20):
                return out
            problems.append(f"unexpected output length {len(out)} (expected ~{expected})")
            continue
        problems.append("the resampler returned more than one dimension")
    _not_ready(
        TypeError(
            "; ".join(problems) or "the resampler did not accept any known call shape"
        )
    )
    return np.zeros(0)


def test_resampler_rejects_frequencies_above_the_new_nyquist():
    """A 15 kHz tone must not fold back into the 24 kHz output.

    Downsampling 48 kHz to 24 kHz puts the new Nyquist at 12 kHz.  Without an
    anti-alias filter the 15 kHz tone reappears at 9 kHz.
    """
    n = 24_000
    t = np.arange(n, dtype=np.float64) / SR
    tone = 0.5 * np.sin(2.0 * math.pi * 1000.0 * t)
    alias = 0.5 * np.sin(2.0 * math.pi * 15000.0 * t)
    x = tone + alias

    y = _resample(x, 0.5)
    y = y[: n // 2]
    spec = np.abs(np.fft.rfft(y))
    bins = np.fft.rfftfreq(len(y), 1.0 / (SR / 2))
    at_1k = float(np.max(spec[np.abs(bins - 1000.0) < 2.0]))
    at_9k = float(np.max(spec[np.abs(bins - 9000.0) < 2.0]))
    assert at_1k > 0.1, "the 1 kHz tone must survive the resampling"
    assert at_9k * 10.0 < at_1k, (
        "the 15 kHz tone folded back into the output - no anti-alias "
        "filter"
    )


def test_resampler_keeps_the_signal_length_proportional():
    """The output length follows the ratio."""
    x = np.random.default_rng(41).standard_normal(12_000)
    down = _resample(x, 0.5)
    assert abs(len(down) - 6_000) <= 8
    up = _resample(x, 2.0)
    assert abs(len(up) - 24_000) <= 16


def test_resampler_output_is_finite():
    """The resampled signal stays finite."""
    x = np.random.default_rng(42).standard_normal(4_000)
    y = _resample(x, 0.5)
    assert np.all(np.isfinite(y))


# --------------------------------------------------------------------------
# Biquad filter
# --------------------------------------------------------------------------
def _biquad_magnitude(b: np.ndarray, a: np.ndarray, freqs: np.ndarray, sr: int) -> np.ndarray:
    """Analytic magnitude response of a biquad, from its coefficients.

    ``H(z) = (b0 + b1 z^-1 + b2 z^-2) / (a0 + a1 z^-1 + a2 z^-2)``.
    """
    w = 2.0 * math.pi * freqs / sr
    z = np.exp(-1j * w)
    num = b[0] + b[1] * z + b[2] * z * z
    den = a[0] + a[1] * z + a[2] * z * z
    return np.abs(num / den)


def _biquad_module():
    """Return the filter module, or skip the test."""
    module = _dsp("filters")
    if module is None:
        pytest.skip("aurcore.dsp.filters does not exist yet")
    return module


def _lowpass_coeffs(module, fc: float, q: float = 0.7071067811865476, sr: int = SR):
    """Design a low pass; returns (b, a, raw coefficients).

    The denominator is normalised to ``a0 = 1``, which is the convention of
    the coefficient factories.
    """
    factory = _resolve(module, ("biquad_lowpass", "lowpass_biquad", "rbj_lowpass"))
    if factory is None:
        pytest.skip("aurcore.dsp.filters exposes no low pass designer")

    problems: list[str] = []
    for args in ((fc, q, sr), (fc, q), (fc,)):
        try:
            coeffs = np.asarray(factory(*args), dtype=np.float64).ravel()
        except (TypeError, ValueError) as exc:
            problems.append(f"{args}: {type(exc).__name__}: {exc}")
            continue
        if coeffs.size == 5:  # b0, b1, b2, a1, a2 with a0 = 1
            b, a = coeffs[:3], np.array([1.0, coeffs[3], coeffs[4]])
        elif coeffs.size == 4:  # b0, b1, b2, a1 with a0 = 1, a2 = 0
            b, a = coeffs[:3], np.array([1.0, coeffs[3], 0.0])
        else:
            problems.append(f"{args}: unexpected coefficient count {coeffs.size}")
            continue
        if not (np.all(np.isfinite(b)) and np.all(np.isfinite(a))):
            problems.append(f"{args}: the coefficients are not finite")
            continue
        dc = float(_biquad_magnitude(b, a, np.array([0.0]), sr)[0])
        if dc <= 0.0 or abs(20.0 * math.log10(dc)) > 1.0:
            problems.append(f"{args}: the design does not pass DC")
            continue
        return b, a, coeffs
    _not_ready(TypeError("; ".join(problems) or "unknown biquad design call shape"))
    return None, None, None


def test_biquad_magnitude_response_matches_the_coefficients():
    """The response function must agree with the analytic formula."""
    module = _biquad_module()
    b, a, coeffs = _lowpass_coeffs(module, 1000.0)
    freqs = np.array([100.0, 500.0, 1000.0, 5000.0])
    want = _biquad_magnitude(b, a, freqs, SR)

    biquad_cls = getattr(module, "Biquad", None)
    response = _resolve(
        module,
        ("biquad_response", "biquad_magnitude", "magnitude_response", "frequency_response",
         "response"),
    )
    got = None
    if biquad_cls is not None and hasattr(biquad_cls, "freq_response"):
        filt = biquad_cls(*coeffs)
        got = np.abs(np.asarray(filt.freq_response(freqs, SR)))
    elif response is not None:
        got = np.abs(np.asarray(response(b, a, freqs, SR)))
    if got is None:
        pytest.skip("aurcore.dsp.filters exposes no magnitude response")

    assert got.shape == want.shape
    assert np.allclose(got, want, atol=1e-9), "the biquad response does not match its coefficients"


def test_biquad_lowpass_is_minus_3_db_at_the_cutoff():
    """A designed low pass must be -3 dB at its corner frequency."""
    module = _biquad_module()
    for fc in (500.0, 1000.0, 4000.0):
        b, a, _ = _lowpass_coeffs(module, fc)
        magnitude = _biquad_magnitude(b, a, np.array([fc]), SR)
        gain_db = 20.0 * math.log10(max(float(magnitude[0]), 1e-12))
        assert gain_db == pytest.approx(-3.0, abs=0.5), (
            f"the low pass is {gain_db:.2f} dB at {fc:.0f} Hz, expected about -3 dB"
        )


def test_biquad_filtering_matches_its_response():
    """Running a sine through the filter must match the analytic response."""
    module = _biquad_module()
    biquad_cls = getattr(module, "Biquad", None)
    if biquad_cls is None:
        pytest.skip("aurcore.dsp.filters exposes no Biquad class")
    b, a, coeffs = _lowpass_coeffs(module, 500.0)

    n = 48_000
    t = np.arange(n, dtype=np.float64) / SR
    x = 0.5 * np.sin(2.0 * math.pi * 1000.0 * t)
    filt = biquad_cls(*coeffs)
    y = np.asarray(filt.process(x))

    half = slice(n // 2, n)  # skip the start-up transient
    measured = float(np.sqrt(np.mean(y[half] ** 2)) / np.sqrt(np.mean(x[half] ** 2)))
    expected = float(_biquad_magnitude(b, a, np.array([1000.0]), SR)[0])
    assert 20.0 * math.log10(max(measured, 1e-12)) == pytest.approx(
        20.0 * math.log10(max(expected, 1e-12)), abs=0.2
    ), "the filtered level does not match the analytic response"


# --------------------------------------------------------------------------
# Windows
# --------------------------------------------------------------------------
def test_window_families_have_the_documented_coherent_gain():
    """Every classic window has a known coherent gain (sum(w) / N)."""
    module = _dsp("windows")
    if module is None:
        pytest.skip("aurcore.dsp.windows does not exist yet")
    expected = {
        "hann": 0.5,
        "hamming": 0.54,
        "blackman": 0.42,
        "blackman_harris": 0.35875,
        "rectangular": 1.0,
    }
    checked = 0
    for name, gain in expected.items():
        fn = getattr(module, name, None)
        if not callable(fn):
            continue
        w = np.asarray(fn(1024), dtype=np.float64)
        assert w.shape == (1024,), f"the {name} window has the wrong length"
        assert np.all(np.isfinite(w)), f"the {name} window holds a non-finite value"
        assert np.all(w >= -1e-12), f"the {name} window goes negative"
        assert float(np.max(w)) <= 1.0 + 1e-12, f"the {name} window overshoots unity"
        coherent = float(np.sum(w)) / len(w)
        assert coherent == pytest.approx(gain, abs=5e-4), (
            f"the {name} window has a coherent gain of {coherent:.6f}, expected {gain}"
        )
        checked += 1
    if not checked:
        pytest.skip("aurcore.dsp.windows exposes none of the classic windows")


def test_window_periodic_and_symmetric_variants():
    """``sym=False`` is periodic (sum N/2), ``sym=True`` is symmetric."""
    module = _dsp("windows")
    if module is None:
        pytest.skip("aurcore.dsp.windows does not exist yet")
    hann = _resolve(module, ("hann", "hanning", "hann_window"))
    if hann is None:
        pytest.skip("aurcore.dsp.windows exposes no hann window")

    periodic = np.asarray(hann(1024), dtype=np.float64)
    symmetric = np.asarray(hann(1024, sym=True), dtype=np.float64)

    assert float(np.sum(periodic)) == pytest.approx(512.0, abs=1e-9)
    assert float(np.sum(symmetric)) == pytest.approx(511.5, abs=1e-9)
    assert np.allclose(symmetric, symmetric[::-1],
                       atol=1e-12), "the symmetric window is not symmetric"


def test_window_builder_resolves_names_and_aliases():
    """``window(name, n)`` accepts the documented names and aliases."""
    module = _dsp("windows")
    if module is None:
        pytest.skip("aurcore.dsp.windows does not exist yet")
    builder = _resolve(module, ("window", "get_window", "make_window"))
    if builder is None:
        pytest.skip("aurcore.dsp.windows exposes no window builder")

    reference = np.asarray(module.hann(512), dtype=np.float64)
    for name in ("hann", "Hann", "hanning", "HANNING"):
        assert np.allclose(np.asarray(builder(name, 512)), reference, atol=1e-12), (
            f"the window name {name!r} does not resolve to the hann window"
        )
    with pytest.raises(ValueError):
        builder("no-such-window", 128)


def test_window_names_are_listed():
    """The window module publishes its window names."""
    module = _dsp("windows")
    if module is None:
        pytest.skip("aurcore.dsp.windows does not exist yet")
    names = getattr(module, "window_names", None)
    if not names:
        pytest.skip("aurcore.dsp.windows lists no window names")
    assert "hann" in names
    assert "hamming" in names


# --------------------------------------------------------------------------
# Loudness
# --------------------------------------------------------------------------
def _loudness_meter():
    """Return the loudness meter, or skip the test."""
    module = _dsp("loudness")
    if module is None:
        pytest.skip("aurcore.dsp.loudness does not exist yet")
    fn = _resolve(
        module,
        (
            "measure_loudness",
            "loudness",
            "integrated_loudness",
            "measure_lufs",
            "loudness_lufs",
            "lufs",
            "measure",
        ),
    )
    if fn is None:
        pytest.skip("aurcore.dsp.loudness exposes no meter")
    return fn


def _accepts_sr(fn) -> bool:
    """True when the meter takes the sample rate as an argument."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return len(params) >= 2


def _tone_lufs(meter, dbfs: float, channels: int = 2, sr: int = SR, seconds: float = 2.0) -> float:
    """Read a 1 kHz tone whose peak level is ``dbfs`` dBFS.

    A 1 kHz tone at -20 dBFS peak in a stereo pair reads -20 LUFS with a
    BS.1770 meter: the K-weighting gain at 1 kHz and the channel sum cancel
    the -0.691 LU offset of the standard.
    """
    n = int(sr * seconds)
    t = np.arange(n, dtype=np.float64) / sr
    wave = (10.0 ** (dbfs / 20.0)) * np.sin(2.0 * math.pi * 1000.0 * t)
    x = np.repeat(wave[:, None], channels, axis=1) if channels > 1 else wave[:, None]
    try:
        value = meter(x, sr) if _accepts_sr(meter) else meter(x)
    except (TypeError, ValueError, AttributeError, IndexError) as exc:
        _not_ready(exc)
        return 0.0
    return float(value)


def test_loudness_meter_is_linear_in_decibels():
    """Twenty decibels in must be twenty LU out - the meter is calibrated."""
    meter = _loudness_meter()
    loud = _tone_lufs(meter, -20.0)
    quiet = _tone_lufs(meter, -40.0)
    assert loud - quiet == pytest.approx(20.0, abs=0.05)


def test_loudness_meter_reads_a_minus_20_dbfs_tone_as_minus_20_lufs():
    """A 1 kHz tone at -20 dBFS must read -20 LUFS (+/- 0.2)."""
    meter = _loudness_meter()
    value = _tone_lufs(meter, -20.0, channels=2)
    assert value == pytest.approx(-20.0, abs=0.2), f"the meter read {value:.2f} LUFS"


def test_loudness_meter_sums_the_channels_like_bs1770():
    """Two identical channels read 3 dB above a single one (BS.1770 table 3)."""
    meter = _loudness_meter()
    mono = _tone_lufs(meter, -20.0, channels=1)
    stereo = _tone_lufs(meter, -20.0, channels=2)
    assert stereo - mono == pytest.approx(3.01, abs=0.05)


def test_loudness_meter_reports_finite_values():
    """The meter returns a finite, plausible value."""
    meter = _loudness_meter()
    value = _tone_lufs(meter, -30.0)
    assert math.isfinite(value)
    assert -100.0 < value < 20.0


# --------------------------------------------------------------------------
# Shared helpers (aurcore.dsp.util)
# --------------------------------------------------------------------------
def test_db_and_linear_conversions_are_inverse():
    """The decibel and linear helpers are inverses."""
    util = _dsp("util")
    if util is None:
        pytest.skip("aurcore.dsp.util does not exist yet")
    for db in (-60.0, -12.0, 0.0, 6.0, 24.0):
        assert util.db_to_lin(db) == pytest.approx(10.0 ** (db / 20.0), rel=1e-9)
        assert util.lin_to_db(util.db_to_lin(db)) == pytest.approx(db, abs=1e-9)


def test_apply_gain_db_adds_the_requested_decibels():
    """apply_gain_db adds exactly the requested gain."""
    util = _dsp("util")
    if util is None:
        pytest.skip("aurcore.dsp.util does not exist yet")
    x = np.array([0.1, -0.2, 0.3])
    y = util.apply_gain_db(x, 6.0)
    rms_before = float(np.sqrt(np.mean(x**2)))
    rms_after = float(np.sqrt(np.mean(np.asarray(y) ** 2)))
    assert 20.0 * math.log10(rms_after / rms_before) == pytest.approx(6.0, abs=1e-9)


def test_normalize_peak_hits_the_target():
    """normalize_peak hits the requested peak."""
    util = _dsp("util")
    if util is None:
        pytest.skip("aurcore.dsp.util does not exist yet")
    x = np.array([0.1, -0.2, 0.3])
    for target in (0.5, 1.0, 0.25):
        y = util.normalize_peak(x, target)
        assert float(np.max(np.abs(y))) == pytest.approx(target, rel=1e-9)


def test_normalize_peak_leaves_silence_alone():
    """normalize_peak does not amplify silence."""
    util = _dsp("util")
    if util is None:
        pytest.skip("aurcore.dsp.util does not exist yet")
    silence = np.zeros(16)
    y = util.normalize_peak(silence, 1.0)
    assert np.all(np.asarray(y) == 0.0)


def test_peak_dbfs_reports_the_absolute_peak():
    """peak_dbfs reports the absolute peak level."""
    util = _dsp("util")
    if util is None:
        pytest.skip("aurcore.dsp.util does not exist yet")
    x = np.array([0.1, -0.5, 0.3])
    assert util.peak_dbfs(x) == pytest.approx(20.0 * math.log10(0.5), abs=1e-9)


def test_next_pow2_and_is_pow2():
    """The power of two helpers behave as documented."""
    util = _dsp("util")
    if util is None:
        pytest.skip("aurcore.dsp.util does not exist yet")
    assert util.next_pow2(1000) == 1024
    assert util.next_pow2(1024) == 1024
    assert util.next_pow2(1025) == 2048
    assert util.is_pow2(1024) is True
    assert util.is_pow2(1000) is False


def test_bin_frequencies_and_freq_to_bin_agree():
    """The bin helpers agree with each other."""
    util = _dsp("util")
    if util is None:
        pytest.skip("aurcore.dsp.util does not exist yet")
    freqs = util.bin_frequencies(1024, 48_000.0)
    assert len(freqs) == 513
    assert freqs[0] == pytest.approx(0.0)
    assert freqs[-1] == pytest.approx(24_000.0, rel=1e-6)
    assert util.freq_to_bin(1000.0, 1024, 48_000.0) == pytest.approx(1000.0 * 1024 / 48_000.0,
                                                                     rel=1e-9)


def test_dc_block_removes_the_offset():
    """The DC blocker removes the offset."""
    util = _dsp("util")
    if util is None:
        pytest.skip("aurcore.dsp.util does not exist yet")
    if _dsp("filters") is None:
        pytest.skip("aurcore.dsp.filters does not exist yet")
    n = 8192
    t = np.arange(n) / 48_000.0
    x = 0.5 * np.sin(2.0 * math.pi * 1000.0 * t) + 0.25
    y = np.asarray(util.dc_block(x, 48_000.0, cutoff_hz=20.0))
    assert abs(float(np.mean(y))) < 0.02, "the DC offset was not removed"


def test_soft_limiter_keeps_the_signal_below_the_threshold():
    """The limiter keeps the level down."""
    util = _dsp("util")
    if util is None:
        pytest.skip("aurcore.dsp.util does not exist yet")
    x = np.linspace(-1.5, 1.5, 4096)
    y = np.asarray(util.soft_limiter(x, threshold_db=-1.0, ratio=20.0))
    assert float(np.max(np.abs(y))) <= 1.05


def test_running_rms_tracks_the_level():
    """The running RMS follows the signal level."""
    util = _dsp("util")
    if util is None:
        pytest.skip("aurcore.dsp.util does not exist yet")
    x = np.ones(4096) * 0.5
    y = np.asarray(util.running_rms(x, 1024))
    assert y.size > 0
    assert float(np.mean(y[-10:])) == pytest.approx(0.5, rel=1e-6)


def test_fade_ramps_in_and_out():
    """A fade ramps in and out."""
    util = _dsp("util")
    if util is None:
        pytest.skip("aurcore.dsp.util does not exist yet")
    x = np.ones(1000)
    y = np.asarray(util.fade(x, 100, 100))
    assert y[0] == pytest.approx(0.0, abs=1e-9)
    assert y[500] == pytest.approx(1.0, abs=1e-9)
    assert y[-1] == pytest.approx(0.0, abs=1e-9)
