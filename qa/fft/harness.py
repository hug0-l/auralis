"""
qa.fft.harness — the measurement harness for the real FFT of the Auralis core.

The harness answers the questions the transform has to answer before any
spectral measurement in the project can be trusted:

* how exactly does a forward/inverse pair rebuild a signal, for **every**
  size the wrapper supports (not just the one the test suite uses)?
* how far is the fast transform from a textbook DFT?
* what happens when the input is shorter than one frame, or when its length
  is not a multiple of the frame?
* what does one transform cost, against the realtime budget of a block?
* how does the phase behave at the edges of a block, and does the transform
  assume that the block is zero padded?
* what happens to silence, a denormal, a huge value, an infinity and a NaN?

Everything here is measurement only — it never modifies ``aurcore``.
"""
from __future__ import annotations

import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aurcore.dsp import fft as fftmod  # noqa: E402

__all__ = [
    "DFT_SIZES",
    "EDGE_CASES",
    "FFT_SIZES",
    "LARGE_SIZES",
    "RADIX2_SIZES",
    "STFT_SIZES",
    "EdgeCase",
    "RoundTrip",
    "Timing",
    "dbfs",
    "direct_dft",
    "measure_edge_cases",
    "measure_edge_phase",
    "measure_denormal_cost",
    "measure_padding_assumption",
    "measure_radix2_axis",
    "measure_round_trip",
    "measure_short_and_ragged_input",
    "measure_stft_round_trip",
    "measure_structured_round_trip",
    "measure_timing",
    "naive_rfft",
    "worst_round_trip",
]

#: The sample rate the project runs at.
SAMPLE_RATE = 48_000

#: The realtime budget of one block, in microseconds, per block size.
#: A block of ``n`` samples has to be processed in ``n / sample_rate`` seconds.
BLOCK_BUDGET_US: dict[int, float] = {
    64: (64 / SAMPLE_RATE) * 1e6,
    128: (128 / SAMPLE_RATE) * 1e6,
    256: (256 / SAMPLE_RATE) * 1e6,
    512: (512 / SAMPLE_RATE) * 1e6,
    1024: (1024 / SAMPLE_RATE) * 1e6,
}

#: Every size from 1 to 1024, plus the sizes above it that the project uses.
FFT_SIZES: tuple[int, ...] = tuple(range(1, 1025))

#: The large sizes, measured separately because they are slow.
LARGE_SIZES: tuple[int, ...] = (1024, 1536, 2048, 3072, 4096, 8192, 16384, 32768)

#: The sizes a textbook DFT is compared against (the DFT is O(n^2)).
DFT_SIZES: tuple[int, ...] = (
    2, 3, 4, 5, 7, 8, 9, 15, 16, 17, 31, 32, 33, 63, 64, 65,
    100, 101, 127, 128, 129, 251, 256, 257, 500, 512, 1000, 1021, 1024,
)

#: The power-of-two sizes the hand written radix-2 backend accepts.
RADIX2_SIZES: tuple[int, ...] = (1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024)

#: The frame sizes the short-time transform is measured at: every power of
#: two up to 4096, the odd sizes around them, and the awkward ones.
STFT_SIZES: tuple[int, ...] = (
    2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 15, 16, 17, 20, 24, 31, 32, 33,
    48, 60, 63, 64, 65, 96, 100, 101, 120, 127, 128, 129, 192, 200, 250,
    251, 256, 257, 300, 383, 384, 400, 500, 511, 512, 513, 600, 768, 1000,
    1001, 1023, 1024, 1025, 1536, 2048, 3000, 4096,
)

#: The names of the numerical edge cases.
EDGE_CASES: tuple[str, ...] = (
    "silence",
    "denormal",
    "huge",
    "overflow",
    "infinity",
    "nan",
)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def dbfs(value: float) -> float:
    """Return ``value`` as a level in dBFS (1.0 = 0 dBFS)."""
    return 20.0 * math.log10(max(abs(float(value)), 1e-300))


def _full_scale_signal(n: int, seed: int = 20261021) -> np.ndarray:
    """A deterministic noise signal whose peak is exactly 1.0 (0 dBFS)."""
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(int(n))
    peak = float(np.max(np.abs(x)))
    if peak > 0.0:
        x = x / peak
    return x


def direct_dft(x: np.ndarray) -> np.ndarray:
    """The textbook O(n^2) discrete Fourier transform."""
    n = len(x)
    k = np.arange(n)
    matrix = np.exp(-2j * math.pi * np.outer(k, k) / n)
    return matrix @ np.asarray(x, dtype=np.complex128)


def naive_rfft(x: np.ndarray) -> np.ndarray:
    """The first ``n // 2 + 1`` bins of the textbook DFT."""
    n = len(x)
    k = np.arange(n // 2 + 1)
    matrix = np.exp(-2j * math.pi * np.outer(k, np.arange(n)) / n)
    return matrix @ np.asarray(x, dtype=np.float64)


def _median_us(func: Callable[[], object], repeats: int = 200) -> float:
    """Median wall-clock time of ``func``, in microseconds."""
    times: list[float] = []
    for _ in range(max(1, repeats)):
        start = time.perf_counter_ns()
        func()
        times.append((time.perf_counter_ns() - start) / 1000.0)
    return float(np.percentile(np.asarray(times, dtype=np.float64), 50.0))


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------
@dataclass
class RoundTrip:
    """The outcome of one forward/inverse round trip.

    Attributes
    ----------
    n:
        Transform size.
    backend:
        The backend that was used.
    error:
        The largest absolute difference between the input and the rebuild.
    error_dbfs:
        The same difference as a level in dBFS.
    out_length:
        The number of samples the inverse transform returned.
    in_length:
        The number of samples that went in.
    """

    n: int
    backend: str
    error: float
    error_dbfs: float
    out_length: int
    in_length: int
    raised: str | None = None
    level_error_db: float | None = None

    @property
    def length_lost(self) -> int:
        """Number of samples the round trip did not give back."""
        if self.out_length < 0:
            return self.in_length
        return self.in_length - self.out_length

    def to_dict(self) -> dict:
        """Return the record as a JSON-safe mapping."""
        return {
            "n": self.n,
            "backend": self.backend,
            "error": self.error,
            "error_dbfs": self.error_dbfs,
            "out_length": self.out_length,
            "in_length": self.in_length,
            "length_lost": self.length_lost,
            "raised": self.raised,
            "level_error_db": self.level_error_db,
        }


@dataclass
class Timing:
    """The cost of one transform.

    Attributes
    ----------
    n:
        Transform size.
    forward_us:
        Median time of one forward transform, in microseconds.
    inverse_us:
        Median time of one inverse transform, in microseconds.
    radix2_us:
        Median time of one forward transform in the radix-2 backend.
    budget_us:
        The realtime budget of a block of ``n`` samples, if it is one of the
        block sizes the project uses.
    """

    n: int
    forward_us: float
    inverse_us: float
    radix2_us: float | None = None
    budget_us: float | None = None

    def to_dict(self) -> dict:
        """Return the record as a JSON-safe mapping."""
        return {
            "n": self.n,
            "forward_us": self.forward_us,
            "inverse_us": self.inverse_us,
            "radix2_us": self.radix2_us,
            "budget_us": self.budget_us,
        }


@dataclass
class EdgeCase:
    """What one numerical edge case does to the transform.

    Attributes
    ----------
    name:
        The name of the case.
    spec_finite:
        True when every bin of the spectrum is finite.
    back_finite:
        True when every sample of the rebuild is finite.
    spec_is_zero:
        True when the spectrum is exactly zero.
    mag_db:
        The magnitude of the first bin in dB.
    phase_first:
        The phase of the first bin in radians.
    note:
        A short description of the behaviour.
    """

    name: str
    spec_finite: bool
    back_finite: bool
    spec_is_zero: bool
    mag_db: float
    phase_first: float
    note: str = ""

    def to_dict(self) -> dict:
        """Return the record as a JSON-safe mapping."""
        return {
            "name": self.name,
            "spec_finite": self.spec_finite,
            "back_finite": self.back_finite,
            "spec_is_zero": self.spec_is_zero,
            "mag_db": self.mag_db,
            "phase_first": self.phase_first,
            "note": self.note,
        }


# ---------------------------------------------------------------------------
# measurements
# ---------------------------------------------------------------------------
def measure_round_trip(
    sizes: Iterable[int] = FFT_SIZES,
    backend: str = "numpy",
) -> list[RoundTrip]:
    """Round trip a full-scale signal through the transform at every size.

    The signal is rebuilt with an **explicit** transform length, so the
    measurement isolates the transform itself and not the length default.

    Parameters
    ----------
    sizes:
        The transform sizes to measure.
    backend:
        The backend to use (``"numpy"`` or ``"radix2"``).

    Returns
    -------
    list[RoundTrip]
        One record per size.
    """
    records: list[RoundTrip] = []
    for n in sizes:
        n = int(n)
        x = _full_scale_signal(n)
        spec = fftmod.rfft(x, n=n, backend=backend)
        back = fftmod.irfft(spec, n=n)
        error = float(np.max(np.abs(back - x))) if back.size == x.size else math.inf
        records.append(
            RoundTrip(
                n=n,
                backend=backend,
                error=error,
                error_dbfs=dbfs(error),
                out_length=int(back.size),
                in_length=int(x.size),
            )
        )
    return records


def measure_default_length_round_trip(sizes: Iterable[int] = FFT_SIZES) -> list[RoundTrip]:
    """Round trip through the *default* length of the inverse transform.

    The forward transform is called without a length and the inverse without
    a length as well, i.e. the way a caller that does not know the length
    uses the module.  This is where a length default that does not match the
    one-sided spectrum shows up.

    The record also carries the **level error**: a spectrum that is inverted
    with the wrong length is not only short by a sample, it is also scaled
    by ``n_wrong / n_right``, because the inverse transform divides by the
    length.
    """
    records: list[RoundTrip] = []
    for n in sizes:
        n = int(n)
        x = _full_scale_signal(n)
        try:
            spec = fftmod.rfft(x)
            back = fftmod.irfft(spec)
        except Exception as exc:  # noqa: BLE001 - the failure is the result
            records.append(
                RoundTrip(
                    n=n,
                    backend="numpy/default-length",
                    error=math.inf,
                    error_dbfs=-math.inf,
                    out_length=-1,
                    in_length=int(x.size),
                    raised=f"{type(exc).__name__}: {exc}",
                )
            )
            continue
        if back.size == x.size:
            error = float(np.max(np.abs(back - x)))
        else:
            error = math.inf
        if back.size and x.size:
            common = min(back.size, x.size)
            in_rms = float(np.sqrt(np.mean(x[:common] ** 2)))
            out_rms = float(np.sqrt(np.mean(back[:common] ** 2)))
            level = dbfs(out_rms / in_rms) if in_rms > 0.0 else 0.0
        else:
            level = None
        records.append(
            RoundTrip(
                n=n,
                backend="numpy/default-length",
                error=error,
                error_dbfs=dbfs(error),
                out_length=int(back.size),
                in_length=int(x.size),
                level_error_db=level,
            )
        )
    return records


def measure_structured_round_trip(sizes: Sequence[int] = (5, 101, 1001)) -> list[dict]:
    """Round trip structured signals through the default inverse length.

    A signal whose top bin is *exactly real* cannot be told apart from an
    even-length signal by the spectrum alone, so it is the case where the
    length inference of :func:`aurcore.dsp.fft.irfft` is ambiguous.
    """
    rows: list[dict] = []
    for n in sizes:
        n = int(n)
        for name, x in (
            ("dc", np.ones(n, dtype=np.float64)),
            ("impulse", np.eye(n, dtype=np.float64)[0]),
            ("zeros", np.zeros(n, dtype=np.float64)),
            ("noise", _full_scale_signal(n)),
        ):
            spec = fftmod.rfft(x)
            top_is_real = bool(np.all(np.isreal(spec[-1])))
            try:
                back = fftmod.irfft(spec)
            except Exception as exc:  # noqa: BLE001 - the failure is the result
                rows.append(
                    {
                        "n": n,
                        "case": name,
                        "top_is_real": top_is_real,
                        "out_length": -1,
                        "raised": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue
            common = min(back.size, x.size)
            error = (
                float(np.max(np.abs(back[:common] - x[:common]))) if common else math.inf
            )
            rows.append(
                {
                    "n": n,
                    "case": name,
                    "top_is_real": top_is_real,
                    "out_length": int(back.size),
                    "error": error,
                    "error_dbfs": dbfs(error),
                    "raised": None,
                }
            )
    return rows


def worst_round_trip(records: Sequence[RoundTrip]) -> RoundTrip | None:
    """Return the record with the largest error."""
    if not records:
        return None
    return max(records, key=lambda r: r.error)


def measure_dft_error(sizes: Iterable[int] = DFT_SIZES) -> list[dict]:
    """Compare the fast transform with a textbook DFT.

    The error is reported both as an absolute number and relative to the
    size of the spectrum, so it can be compared with the machine epsilon.
    """
    rows: list[dict] = []
    for n in sizes:
        n = int(n)
        x = _full_scale_signal(n)
        want = naive_rfft(x)
        got = np.asarray(fftmod.rfft(x, n=n))
        error = float(np.max(np.abs(got - want)))
        scale = float(np.max(np.abs(want)))
        radix = None
        if n in RADIX2_SIZES:
            radix_spec = np.asarray(fftmod.rfft(x, n=n, backend="radix2"))
            radix = float(np.max(np.abs(radix_spec - want)))
        rows.append(
            {
                "n": n,
                "error": error,
                "relative": error / scale if scale else 0.0,
                "radix2_error": radix,
                "max_bin": float(np.max(np.abs(got))),
            }
        )
    return rows


def measure_radix2_axis(sizes: Sequence[int] = (8, 16, 32, 64)) -> list[dict]:
    """Check the radix-2 backend against the numpy backend for every axis.

    The transform is run on a two-dimensional array along axis 0 and along
    axis -1, and the two backends are compared.
    """
    rows: list[dict] = []
    for n in sizes:
        n = int(n)
        rng = np.random.default_rng(4242)
        block = rng.standard_normal((6, n))
        for axis in (0, 1, -1):
            reference = np.asarray(fftmod.rfft(block, n=n, axis=axis, backend="numpy"))
            try:
                got = np.asarray(fftmod.rfft(block, n=n, axis=axis, backend="radix2"))
            except Exception as exc:  # noqa: BLE001 - the failure is the result
                rows.append(
                    {
                        "n": n,
                        "axis": axis,
                        "raised": f"{type(exc).__name__}: {exc}",
                        "shape_ok": False,
                        "error": math.inf,
                    }
                )
                continue
            shape_ok = got.shape == reference.shape
            error = (
                float(np.max(np.abs(got - reference))) if shape_ok else math.inf
            )
            rows.append(
                {
                    "n": n,
                    "axis": axis,
                    "raised": None,
                    "shape_ok": shape_ok,
                    "reference_shape": list(reference.shape),
                    "radix2_shape": list(got.shape),
                    "error": error,
                }
            )
    return rows


def measure_stft_round_trip(
    sizes: Iterable[int] = STFT_SIZES,
    overlaps: Sequence[int] = (2, 4),
    length: int = 8192,
) -> list[dict]:
    """Round trip a signal through the short-time transform at every size.

    The measurement goes through magnitude and phase, i.e. through the
    representation the spectral modules actually store and edit.
    """
    from aurcore.dsp.stft import STFT

    rows: list[dict] = []
    x = _full_scale_signal(length) * 0.5
    for n_fft in sizes:
        n_fft = int(n_fft)
        for overlap in overlaps:
            hop = max(1, n_fft // int(overlap))
            try:
                engine = STFT(n_fft, hop=hop, window="hann", overlap_factor=int(overlap))
                mag, phase_ = engine.analyze_block(x)
                back = engine.synthesize_block(mag, phase_, length=length)
            except Exception as exc:  # noqa: BLE001 - the failure is the result
                rows.append(
                    {
                        "n_fft": n_fft,
                        "overlap": int(overlap),
                        "error": math.inf,
                        "error_dbfs": -math.inf,
                        "raised": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue
            if back.size != x.size:
                error = math.inf
            else:
                error = float(np.max(np.abs(back - x)))
            rows.append(
                {
                    "n_fft": n_fft,
                    "overlap": int(overlap),
                    "error": error,
                    "error_dbfs": dbfs(error),
                    "raised": None,
                }
            )
    return rows


def measure_short_and_ragged_input(
    n_fft: int = 1024,
    lengths: Sequence[int] = (1, 63, 64, 100, 511, 1000, 1023, 1024, 1025, 2047, 2048, 3000, 4096),
    overlaps: Sequence[int] = (2, 4),
) -> list[dict]:
    """Measure what happens when the input does not fill whole frames.

    Three cases are covered: the input is shorter than one frame, the input
    is not a multiple of the hop, and the input is an exact multiple.
    """
    from aurcore.dsp.stft import STFT

    rows: list[dict] = []
    for overlap in overlaps:
        hop = max(1, n_fft // int(overlap))
        engine = STFT(n_fft, hop=hop, window="hann", overlap_factor=int(overlap))
        for length in lengths:
            length = int(length)
            x = _full_scale_signal(length) * 0.5
            for center in (True, False):
                try:
                    mag, phase_ = engine.analyze_block(x, center=center)
                    back = engine.synthesize_block(
                        mag, phase_, length=length, center=center
                    )
                except Exception as exc:  # noqa: BLE001 - the failure is the result
                    rows.append(
                        {
                            "n_fft": n_fft,
                            "overlap": int(overlap),
                            "hop": hop,
                            "length": length,
                            "center": center,
                            "raised": f"{type(exc).__name__}: {exc}",
                        }
                    )
                    continue
                error = (
                    float(np.max(np.abs(back - x))) if back.size == length else math.inf
                )
                rows.append(
                    {
                        "n_fft": n_fft,
                        "overlap": int(overlap),
                        "hop": hop,
                        "length": length,
                        "center": center,
                        "out_length": int(back.size),
                        "frames": int(mag.shape[-1]),
                        "error": error,
                        "error_dbfs": dbfs(error) if error != math.inf else -math.inf,
                        "raised": None,
                    }
                )
    return rows


def measure_timing(
    sizes: Sequence[int] = (64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384),
    repeats: int = 200,
    rounds: int = 5,
) -> list[Timing]:
    """Measure the cost of one forward and one inverse transform.

    The machine is shared, so every size is measured several times and the
    **fastest** round is kept: the fastest round is the one that was least
    disturbed by other work, so it is the best estimate of the transform's
    own cost.
    """
    records: list[Timing] = []
    for n in sizes:
        n = int(n)
        data = _full_scale_signal(n)
        spec = fftmod.rfft(data, n=n)
        fftmod.irfft(spec, n=n)  # warm up
        forward = min(
            _median_us(lambda: fftmod.rfft(data, n=n), repeats) for _ in range(rounds)
        )
        inverse = min(
            _median_us(lambda: fftmod.irfft(spec, n=n), repeats) for _ in range(rounds)
        )
        radix = None
        if n in RADIX2_SIZES:
            fftmod.rfft(data, n=n, backend="radix2")
            radix = min(
                _median_us(
                    lambda: fftmod.rfft(data, n=n, backend="radix2"),
                    max(3, repeats // 10),
                )
                for _ in range(rounds)
            )
        records.append(
            Timing(
                n=n,
                forward_us=forward,
                inverse_us=inverse,
                radix2_us=radix,
                budget_us=BLOCK_BUDGET_US.get(n),
            )
        )
    return records


def measure_edge_phase(n: int = 1024) -> list[dict]:
    """Look at the phase at the edges of a block.

    The lowest bin (DC) and the highest bin (Nyquist, or the last bin of an
    odd-length block) are the two places where the one-sided spectrum is
    special: DC is real, and the Nyquist bin is real for an even length
    while the top bin of an odd length is a genuinely complex bin.
    """
    rows: list[dict] = []
    for length in (n, n + 1):
        x = _full_scale_signal(length)
        spec = np.asarray(fftmod.rfft(x, n=length))
        ph = fftmod.phase(spec)
        rebuilt = fftmod.to_complex(fftmod.magnitude(spec), ph)
        top = spec.shape[-1] - 1
        rows.append(
            {
                "length": length,
                "bins": int(spec.shape[-1]),
                "even": length % 2 == 0,
                "dc_imag": float(spec[0].imag),
                "dc_phase": float(ph[0]),
                "top_bin": top,
                "top_value": [float(spec[top].real), float(spec[top].imag)],
                "top_phase": float(ph[top]),
                "top_is_real": bool(spec[top].imag == 0.0),
                "mag_phase_rebuild_error": float(np.max(np.abs(rebuilt - spec))),
            }
        )
    return rows


def measure_padding_assumption(n: int = 1024) -> list[dict]:
    """Quantify the implicit zero padding of a block.

    A block whose content is periodic over its own length (a tone at a bin
    frequency) is transformed exactly; a block whose edges do not match
    leaks, because the transform assumes that the signal continues as zeros
    outside the block.  The measurement reports the leakage floor relative
    to the peak bin.
    """
    rows: list[dict] = []
    bin_index = 64
    tone = np.cos(2.0 * math.pi * bin_index * np.arange(n) / n)

    def leakage(x: np.ndarray) -> tuple[float, float]:
        spec = np.abs(np.asarray(fftmod.rfft(x, n=n)))
        peak = float(np.max(spec))
        rest = np.delete(spec, bin_index)
        floor = float(np.max(rest))
        return peak, floor

    peak, floor = leakage(tone)
    rows.append(
        {
            "case": "periodic (tone at a bin frequency)",
            "peak": peak,
            "leakage_floor": floor,
            "leakage_db_below_peak": dbfs(floor / peak) if peak else -math.inf,
        }
    )

    for cut in (1, 8, 64, 256):
        gated = tone.copy()
        gated[:cut] = 0.0
        gated[-cut:] = 0.0
        peak, floor = leakage(gated)
        rows.append(
            {
                "case": f"edge discontinuity ({cut} samples gated at each end)",
                "peak": peak,
                "leakage_floor": floor,
                "leakage_db_below_peak": dbfs(floor / peak) if peak else -math.inf,
            }
        )

    ramp = np.linspace(0.0, 1.0, n)
    peak, floor = leakage(ramp)
    rows.append(
        {
            "case": "ramp (discontinuity of 1.0 at the last sample)",
            "peak": peak,
            "leakage_floor": floor,
            "leakage_db_below_peak": dbfs(floor / peak) if peak else -math.inf,
        }
    )
    return rows


def measure_denormal_cost(
    sizes: Sequence[int] = (1024, 4096),
    repeats: int = 300,
    rounds: int = 5,
) -> list[dict]:
    """Measure the cost of a transform of denormal samples.

    Denormals are the one input class that makes an otherwise linear
    transform slower, because the arithmetic leaves the normal range.
    """
    rows: list[dict] = []
    for n in sizes:
        n = int(n)
        rng = np.random.default_rng(11)
        normal = rng.standard_normal(n)
        denormal = np.full(n, 1e-320)
        rows.append(
            {
                "n": n,
                "normal_us": min(
                    _median_us(lambda: fftmod.rfft(normal, n=n), repeats)
                    for _ in range(rounds)
                ),
                "denormal_us": min(
                    _median_us(lambda: fftmod.rfft(denormal, n=n), repeats)
                    for _ in range(rounds)
                ),
            }
        )
    for row in rows:
        row["ratio"] = (
            row["denormal_us"] / row["normal_us"] if row["normal_us"] else math.nan
        )
    return rows


def measure_edge_cases(n: int = 64) -> list[EdgeCase]:
    """Run the numerical edge cases through the transform."""
    import warnings

    records: list[EdgeCase] = []
    cases: dict[str, np.ndarray] = {
        "silence": np.zeros(n, dtype=np.float64),
        "denormal": np.full(n, 1e-320, dtype=np.float64),
        "huge": np.full(n, 1e300, dtype=np.float64),
        "overflow": np.full(n, 1e307, dtype=np.float64),
        "infinity": np.concatenate([np.zeros(n - 1), [np.inf]]),
        "nan": np.concatenate([np.zeros(n - 1), [np.nan]]),
    }
    for name, x in cases.items():
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            spec = np.asarray(fftmod.rfft(x, n=n))
            back = np.asarray(fftmod.irfft(spec, n=n))
            mag_db = fftmod.magnitude_db(spec)
            ph = fftmod.phase(spec)
        warned = any("invalid" in str(w.message) or "overflow" in str(w.message) for w in caught)
        spec_finite = bool(np.all(np.isfinite(spec)))
        back_finite = bool(np.all(np.isfinite(back)))
        note = ""
        if name == "silence":
            note = "the spectrum is exactly zero, the magnitude sits on the floor"
        elif name == "denormal":
            note = "the denormal survives, the magnitude sits on the floor"
        elif name == "huge":
            note = (
                "the sum stays inside the float range"
                if spec_finite
                else "the sum overflows the float range of the spectrum"
            )
        elif name == "overflow":
            note = "the sum of 64 values of 1e307 overflows to infinity"
        elif name == "infinity":
            note = "an infinity in the input poisons the whole spectrum"
        elif name == "nan":
            note = "a NaN in the input poisons the whole spectrum"
        if warned:
            note = (note + "; a NumPy runtime warning was raised").strip("; ")
        records.append(
            EdgeCase(
                name=name,
                spec_finite=spec_finite,
                back_finite=back_finite,
                spec_is_zero=bool(np.all(spec == 0)),
                mag_db=float(mag_db[0]),
                phase_first=float(ph[0]),
                note=note,
            )
        )
    return records
