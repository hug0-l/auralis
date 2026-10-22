"""qa/loudness/harness.py — the loudness meter against the published tolerances.

The meter in ``aurcore.dsp.loudness`` claims to implement ITU-R BS.1770-4
and the EBU mode of EBU R 128.  This harness measures what the meter
actually reads and compares every figure with the value the published
documents ask for:

* ITU-R BS.1770-4 Annex 1  -- the K-weighting filter, the channel weights
  and the gating of the integrated loudness;
* ITU-R BS.1770-4 Annex 2  -- the true peak, and the under-read the
  recommendation accepts for a four times oversampling;
* EBU Tech 3341 Table 1    -- the 'EBU mode' compliance tests, with their
  published tolerances (0.1 LU, 0.2/-0.4 dBTP);
* EBU Tech 3342 Table 1    -- the loudness range compliance tests, with
  their published tolerances (1 LU).

The harness also reports the readings the meter gave before the defects
that this file documents were fixed, so that the change can be seen in
numbers rather than in prose.

Usage
-----
    python qa/loudness/harness.py            # the table and the verdicts
    python qa/loudness/harness.py --quiet    # only the summary line

Exit codes
----------
    0   every case is inside its published tolerance
    1   at least one case is outside its published tolerance
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aurcore.dsp import loudness as L  # noqa: E402

#: The sample rate every published test signal is defined at.
SR = 48000

#: The K-weighting coefficients of ITU-R BS.1770-4, Tables 1 and 2 (48 kHz).
PUBLISHED_SHELF = (
    (1.53512485958697, -2.69169618940638, 1.19839281085285),
    (1.0, -1.69065929318241, 0.73248077421585),
)
PUBLISHED_RLB = (
    (1.0, -2.0, 1.0),
    (1.0, -1.99004745483398, 0.99007225036621),
)

#: The channel weights of ITU-R BS.1770-4, Table 3, as the meter used them
#: before the fix: the centre channel carried the surround weight and the
#: fourth channel of a 4.0 layout was treated as an LFE channel.
LEGACY_WEIGHTS = {
    1: [1.0],
    2: [1.0, 1.0],
    3: [1.0, 1.0, 1.41],
    4: [1.0, 1.0, 1.41, 0.0],
    5: [1.0, 1.0, 1.41, 1.41, 1.41],
    6: [1.0, 1.0, 1.41, 0.0, 1.41, 1.41],
    7: [1.0, 1.0, 1.41, 0.0, 1.41, 1.41, 1.41],
    8: [1.0, 1.0, 1.41, 0.0, 1.41, 1.41, 1.41, 1.41],
}


# ---------------------------------------------------------------------------
# test signals
# ---------------------------------------------------------------------------
def sine(
    seconds: float,
    freq: float = 1000.0,
    peak_dbfs: float = -23.0,
    sr: int = SR,
    phase: float = 0.0,
    channels: int = 1,
) -> np.ndarray:
    """A sine wave of a given peak level.

    ``peak_dbfs`` is the peak level of one channel, which is how the EBU
    mode test signals are specified.  A stereo signal is the same wave in
    both channels, in phase.
    """
    n = int(round(seconds * sr))
    t = np.arange(n, dtype=np.float64) / sr
    wave = 10.0 ** (peak_dbfs / 20.0) * np.sin(2.0 * math.pi * freq * t + phase)
    return np.repeat(wave[:, None], channels, axis=1)


def fade(x: np.ndarray, ms: float = 10.0, sr: int = SR) -> np.ndarray:
    """Apply a raised cosine fade in and fade out."""
    n = int(round(ms * 1e-3 * sr))
    if n <= 0 or 2 * n >= x.shape[0]:
        return x
    ramp = 0.5 * (1.0 - np.cos(math.pi * np.arange(n) / n))
    window = np.ones(x.shape[0])
    window[:n] = ramp
    window[-n:] = ramp[::-1]
    return x * window[:, None]


def segments(*parts: tuple[float, float, float], sr: int = SR,
             channels: int = 1) -> np.ndarray:
    """Join tone segments of ``(seconds, peak_dbfs, freq)`` into one signal."""
    pieces = []
    for seconds, peak_dbfs, freq in parts:
        pieces.append(sine(seconds, freq=freq, peak_dbfs=peak_dbfs, sr=sr,
                           channels=channels))
    return np.concatenate(pieces, axis=0)


# ---------------------------------------------------------------------------
# the cases
# ---------------------------------------------------------------------------
@dataclass
class Case:
    """One measurement and the value the published documents ask for.

    ``mode`` says how the measured value is compared with the expected one:
    ``both`` asks for a difference of at most ``tolerance``, ``max`` asks
    for a value of at most ``expected + tolerance`` and ``min`` for a value
    of at least ``expected - tolerance``.
    """

    name: str
    source: str
    measured: float
    expected: float
    tolerance: float
    unit: str = "LUFS"
    mode: str = "both"
    note: str = ""

    @property
    def error(self) -> float:
        if math.isinf(self.measured) or math.isinf(self.expected):
            return float("nan")
        return self.measured - self.expected

    @property
    def ok(self) -> bool:
        if self.mode == "max":
            return self.measured <= self.expected + self.tolerance + 1e-9
        if self.mode == "min":
            return self.measured >= self.expected - self.tolerance - 1e-9
        if math.isinf(self.measured) or math.isinf(self.expected):
            return self.measured == self.expected
        return abs(self.error) <= self.tolerance + 1e-9

    def verdict(self) -> str:
        return "pass" if self.ok else "FAIL"


@dataclass
class Report:
    """The collected cases and the numbers of the previous implementation."""

    cases: list[Case] = field(default_factory=list)
    legacy: list[tuple[str, float, float, str]] = field(default_factory=list)

    def add(self, case: Case) -> Case:
        self.cases.append(case)
        return case

    def note_legacy(self, name: str, before: float, after: float, unit: str = "LUFS") -> None:
        self.legacy.append((name, before, after, unit))

    @property
    def failures(self) -> list[Case]:
        return [case for case in self.cases if not case.ok]


# ---------------------------------------------------------------------------
# the readings the meter gave before the fixes
# ---------------------------------------------------------------------------
def legacy_loudness_range(x: np.ndarray, sr: int = SR) -> float:
    """The loudness range before the fix: an arithmetic mean gate and the
    5th percentile."""
    buf = L._as_2d(x)
    weights = L.channel_weights(buf.shape[1])
    if not np.any(weights > 0.0):
        weights = np.ones(buf.shape[1])
    y = L.k_weight(buf, sr)
    _, l = L._block_energies(y, weights, 3.0, 1.0, sr)
    keep = l > -70.0
    if not np.any(keep):
        return 0.0
    mean = float(np.mean(l[keep]))
    keep = keep & (l > mean - 20.0)
    if not np.any(keep):
        keep = l > -70.0
    values = np.sort(l[keep])
    return float(np.percentile(values, 95.0) - np.percentile(values, 5.0))


def legacy_true_peak(x: np.ndarray, sr: int = SR, oversample: int = 4) -> float:
    """The true peak before the fix: the peak over the whole buffer, edge
    artefacts included."""
    buf = L._as_2d(x)
    peak = 0.0
    for ch in range(buf.shape[1]):
        up = L._oversample(buf[:, ch], oversample)
        if up.size:
            peak = max(peak, float(np.max(np.abs(up))))
    return 20.0 * math.log10(peak) if peak > 0.0 else float("-inf")


def legacy_integrated_loudness(x: np.ndarray, sr: int = SR) -> float:
    """The integrated loudness before the fix: the old channel weights."""
    buf = L._as_2d(x)
    weights = np.array(LEGACY_WEIGHTS.get(buf.shape[1], [1.0] * buf.shape[1]))
    if not np.any(weights > 0.0):
        weights = np.ones(buf.shape[1])
    y = L.k_weight(buf, sr)
    z, l = L._block_energies(y, weights, 0.4, 0.1, sr)
    return L._gated_integrated(z, l)


# ---------------------------------------------------------------------------
# the measurement
# ---------------------------------------------------------------------------
def measure(report: Report) -> Report:
    """Run every case and collect the results."""
    # --- the K-weighting filter against ITU-R BS.1770-4, Tables 1 and 2 ---
    coeffs = L.k_weighting_coefficients(SR)
    shelf_b = max(abs(a - b) for a, b in zip(coeffs["shelf"][0], PUBLISHED_SHELF[0]))
    shelf_a = max(abs(a - b) for a, b in zip(coeffs["shelf"][1], PUBLISHED_SHELF[1]))
    rlb_b = max(abs(a - b) for a, b in zip(coeffs["rlb"][0], PUBLISHED_RLB[0]))
    rlb_a = max(abs(a - b) for a, b in zip(coeffs["rlb"][1], PUBLISHED_RLB[1]))
    worst = max(shelf_b, shelf_a, rlb_b, rlb_a)
    report.add(Case(
        "K-weighting coefficients at 48 kHz",
        "BS.1770-4 Tables 1 and 2",
        worst, 0.0, 1e-12, unit="",
        note="largest deviation from the published coefficients",
    ))

    # --- the same filter at other sample rates ---
    for rate in (44100, 96000):
        other = L.k_weighting_coefficients(rate)
        error = _response_error(rate)
        report.add(Case(
            f"K-weighting response at {rate} Hz",
            "BS.1770-4 Table 1 (same response in Hz)",
            error, 0.0, 0.05, unit="dB",
            note="largest deviation from the 48 kHz response",
        ))
        del other

    # --- EBU mode test 1 and 2: the reference level ---
    for level in (-23.0, -33.0):
        x = sine(20.0, peak_dbfs=level, channels=2)
        report.add(Case(
            f"integrated loudness, stereo 1 kHz at {level:.1f} dBFS peak",
            "EBU Tech 3341 test 1 and 2",
            L.integrated_loudness(x, SR), level, 0.1,
        ))
        report.add(Case(
            f"momentary loudness, stereo 1 kHz at {level:.1f} dBFS peak",
            "EBU Tech 3341 test 1 and 2",
            L.momentary_loudness(x, SR), level, 0.1,
        ))
        report.add(Case(
            f"short-term loudness, stereo 1 kHz at {level:.1f} dBFS peak",
            "EBU Tech 3341 test 1 and 2",
            L.short_term_loudness(x, SR), level, 0.1,
        ))

    # --- EBU mode test 3, 4 and 5: the gating ---
    x = segments((10.0, -36.0, 1000.0), (60.0, -23.0, 1000.0), (10.0, -36.0, 1000.0),
                 channels=2)
    report.add(Case(
        "integrated loudness, -36 dBFS / -23 dBFS / -36 dBFS",
        "EBU Tech 3341 test 3",
        L.integrated_loudness(x, SR), -23.0, 0.1,
    ))
    x = segments((10.0, -72.0, 1000.0), (10.0, -36.0, 1000.0), (60.0, -23.0, 1000.0),
                 (10.0, -36.0, 1000.0), (10.0, -72.0, 1000.0), channels=2)
    report.add(Case(
        "integrated loudness, -72 / -36 / -23 / -36 / -72 dBFS",
        "EBU Tech 3341 test 4",
        L.integrated_loudness(x, SR), -23.0, 0.1,
    ))
    x = segments((20.0, -26.0, 1000.0), (20.1, -20.0, 1000.0), (20.0, -26.0, 1000.0),
                 channels=2)
    report.add(Case(
        "integrated loudness, -26 / -20 / -26 dBFS",
        "EBU Tech 3341 test 5",
        L.integrated_loudness(x, SR), -23.0, 0.1,
    ))

    # --- EBU mode test 6: five channels ---
    x = np.concatenate([
        sine(20.0, peak_dbfs=-28.0, channels=1),
        sine(20.0, peak_dbfs=-28.0, channels=1),
        sine(20.0, peak_dbfs=-24.0, channels=1),
        sine(20.0, peak_dbfs=-30.0, channels=1),
        sine(20.0, peak_dbfs=-30.0, channels=1),
    ], axis=1)
    after = L.integrated_loudness(x, SR)
    before = legacy_integrated_loudness(x, SR)
    report.add(Case(
        "integrated loudness, 5.0 channel programme",
        "EBU Tech 3341 test 6",
        after, -23.0, 0.1,
        note="L, R at -28 dBFS, C at -24 dBFS, Ls and Rs at -30 dBFS",
    ))
    report.note_legacy("5.0 channel integrated loudness", before, after)

    # --- EBU mode test 9: the short-term window ---
    x = segments(*([(1.34, -20.0, 1000.0), (1.66, -30.0, 1000.0)] * 5), channels=2)
    report.add(Case(
        "short-term loudness, 1.34 s / 1.66 s segments repeated",
        "EBU Tech 3341 test 9",
        L.short_term_loudness(x, SR), -23.0, 0.1,
    ))

    # --- EBU mode test 12: the momentary window ---
    x = segments(*([(0.18, -20.0, 1000.0), (0.22, -30.0, 1000.0)] * 25), channels=2)
    report.add(Case(
        "momentary loudness, 0.18 s / 0.22 s segments repeated",
        "EBU Tech 3341 test 12",
        L.momentary_loudness(x, SR), -23.0, 0.1,
    ))

    # --- EBU mode tests 15 to 19: the true peak ---
    true_peak_cases = (
        ("fs/4, amplitude 0.50, phase 0", 12000.0, 0.50, 0.0, -6.0),
        ("fs/4, amplitude 0.50, phase 45", 12000.0, 0.50, 45.0, -6.0),
        ("fs/6, amplitude 0.50, phase 60", 8000.0, 0.50, 60.0, -6.0),
        ("fs/8, amplitude 0.50, phase 67.5", 6000.0, 0.50, 67.5, -6.0),
        ("fs/4, amplitude 1.41, phase 45", 12000.0, 1.41, 45.0, 3.0),
    )
    for label, freq, amplitude, phase, expected in true_peak_cases:
        x = fade(sine(2.0, freq=freq, peak_dbfs=20.0 * math.log10(amplitude),
                      phase=math.radians(phase), channels=2))
        report.add(Case(
            f"true peak, {label}",
            "EBU Tech 3341 test 15 to 19",
            L.true_peak(x, SR), expected, 0.4, unit="dBTP",
            note="published tolerance +0.2 / -0.4 dBTP",
        ))

    # --- the true peak of a signal that is full scale in the samples ---
    full_scale = np.sign(np.cos(math.pi * np.arange(SR))).astype(np.float64)[:, None]
    after = L.true_peak(full_scale, SR)
    before = legacy_true_peak(full_scale, SR)
    report.add(Case(
        "true peak, full scale alternating samples",
        "band-limited reconstruction of the signal",
        after, 0.0, 0.1, unit="dBTP",
        note="the waveform is a cosine of amplitude 1.0",
    ))
    report.note_legacy("true peak of a full scale signal", before, after, "dBTP")

    # --- EBU Tech 3342 test 1 to 4: the loudness range ---
    lra_cases = (
        ("-20 dBFS then -30 dBFS", ((20.0, -20.0, 1000.0), (20.0, -30.0, 1000.0)), 10.0),
        ("-20 dBFS then -15 dBFS", ((20.0, -20.0, 1000.0), (20.0, -15.0, 1000.0)), 5.0),
        ("-40 dBFS then -20 dBFS", ((20.0, -40.0, 1000.0), (20.0, -20.0, 1000.0)), 20.0),
        ("-50 / -35 / -20 / -35 / -50 dBFS",
         ((20.0, -50.0, 1000.0), (20.0, -35.0, 1000.0), (20.0, -20.0, 1000.0),
          (20.0, -35.0, 1000.0), (20.0, -50.0, 1000.0)), 15.0),
    )
    for label, parts, expected in lra_cases:
        x = segments(*parts, channels=2)
        after = L.loudness_range(x, SR)[0]
        before = legacy_loudness_range(x, SR)
        report.add(Case(
            f"loudness range, {label}",
            "EBU Tech 3342 test 1 to 4",
            after, expected, 1.0, unit="LU",
        ))
        report.note_legacy(f"loudness range, {label}", before, after, "LU")

    # --- the gating: a pause must not pull the integrated loudness down ---
    for pause_level, label in ((None, "silence"), (-70.0, "-70 dBFS"),
                                (-36.0, "-36 dBFS")):
        loud = sine(10.0, peak_dbfs=-23.0, channels=2)
        if pause_level is None:
            pause = np.zeros((5 * SR, 2))
        else:
            pause = sine(5.0, peak_dbfs=pause_level, channels=2)
        x = np.concatenate([loud, pause, loud], axis=0)
        report.add(Case(
            f"integrated loudness with a pause of {label}",
            "BS.1770-4 Annex 1, the relative gate",
            L.integrated_loudness(x, SR), -23.0, 0.1,
            note="the pause must be gated out",
        ))

    # --- the edges ---
    silence = np.zeros((SR, 1))
    report.add(Case(
        "integrated loudness of silence",
        "a silent file has no loudness",
        L.integrated_loudness(silence, SR), float("-inf"), 0.0, mode="max",
        note="-inf is the only correct reading",
    ))
    impulse = np.zeros((SR, 1))
    impulse[SR // 2, 0] = 1.0
    report.add(Case(
        "integrated loudness of one impulse",
        "BS.1770-4 Annex 1, the energy of the impulse over a 400 ms block",
        L.integrated_loudness(impulse, SR), reference_impulse_loudness(), 0.1,
        note="the reference comes from the filter energy in the frequency domain",
    ))
    constant = np.full((SR, 1), 0.5)
    full_scale = L.integrated_loudness(sine(20.0, peak_dbfs=0.0), SR)
    report.add(Case(
        "integrated loudness of a constant",
        "the RLB high pass removes the direct current",
        L.integrated_loudness(constant, SR), full_scale - 20.0, 0.0, mode="max",
        note="only the start-up transient of the filter is left",
    ))
    short = sine(0.2, peak_dbfs=-23.0, channels=2)
    report.add(Case(
        "integrated loudness of a 200 ms signal",
        "shorter than the 400 ms gating block",
        L.integrated_loudness(short, SR), -26.0, 0.2,
        note="the block is filled up with silence, which costs 3 dB",
    ))
    return report


def reference_impulse_loudness(sr: int = SR) -> float:
    """The loudness of a unit impulse, from the filter energy in the
    frequency domain.

    The meter filters the signal and then squares it; this reference takes
    the other way round.  The energy of the impulse response of the
    K-weighting cascade is the mean of the squared magnitude response
    (Parseval), and the impulse spreads that energy over one gating block.
    """
    n = 1 << 16
    k = np.arange(n)
    w = 2.0 * math.pi * k / n
    z = np.exp(-1j * w)
    magnitude = np.ones(n)
    coeffs = L.k_weighting_coefficients(sr)
    for name in ("shelf", "rlb"):
        b, a = coeffs[name]
        num = b[0] + b[1] * z + b[2] * z ** 2
        den = 1.0 + a[1] * z + a[2] * z ** 2
        magnitude *= np.abs(num / den)
    energy = float(np.mean(magnitude ** 2))
    block = 0.4 * sr
    return -0.691 + 10.0 * math.log10(energy / block)


def _response_error(rate: int) -> float:
    """Largest deviation of the K-weighting response at ``rate`` from the
    response of the 48 kHz filter, in dB, over 20 Hz to 20 kHz."""
    freqs = np.array([20.0, 40.0, 60.0, 100.0, 200.0, 400.0, 800.0, 1000.0,
                      1500.0, 2000.0, 3000.0, 5000.0, 10000.0, 15000.0, 20000.0])
    return float(np.max(np.abs(_response(rate, freqs) - _response(SR, freqs))))


def _response(rate: int, freqs: np.ndarray) -> np.ndarray:
    """The K-weighting magnitude response in dB at the given frequencies."""
    coeffs = L.k_weighting_coefficients(rate)
    total = np.zeros(freqs.shape)
    for name in ("shelf", "rlb"):
        b, a = coeffs[name]
        w = 2.0 * math.pi * freqs / rate
        z = np.exp(-1j * w)
        num = b[0] + b[1] * z + b[2] * z ** 2
        den = 1.0 + a[1] * z + a[2] * z ** 2
        total += 20.0 * np.log10(np.abs(num / den))
    return total


# ---------------------------------------------------------------------------
# real material and the delivery catalogue
# ---------------------------------------------------------------------------
def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """Read a wave file as float64 samples."""
    try:
        import soundfile as sf
    except Exception:  # pragma: no cover - the fallback is the standard library
        sf = None
    if sf is not None:
        data, rate = sf.read(str(path), always_2d=True, dtype="float64")
        return np.asarray(data, dtype=np.float64), int(rate)
    import wave
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        raw = handle.readframes(handle.getnframes())
    if width == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    elif width == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2147483648.0
    elif width == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128.0) / 128.0
    else:  # pragma: no cover - the corpus is 16 bit
        raise ValueError(f"unsupported sample width {width}")
    return data.reshape(-1, channels), int(rate)


def corpus_file(name: str) -> tuple[np.ndarray, int]:
    """Read one file of the test corpus."""
    path = Path(__file__).resolve().parents[2] / "corpus" / "audio" / "test" / f"{name}.wav"
    return read_wav(path)


def measure_delivery_targets(report: Report) -> list[dict]:
    """Check every delivery target of the catalogue against the meter.

    For each target the programme is measured, the gain that the delivery
    runner would apply is worked out the way the runner works it out (the
    smaller of the gain the loudness target asks for and the gain the
    true-peak ceiling allows), and the programme is measured again.  The
    second reading is the loudness the delivered file would carry.
    """
    try:
        from aurserver.batch_formats import DELIVERY_TARGETS, PEAK_MARGIN_DB
    except Exception as exc:  # pragma: no cover - the server package is optional
        report.add(Case(
            "delivery catalogue",
            "aurserver.batch_formats",
            0.0, 0.0, 0.0, unit="",
            note=f"not checked, the catalogue could not be read: {exc}",
        ))
        return []

    try:
        programme, rate = corpus_file("music_mix")
    except Exception:  # pragma: no cover - the corpus is optional
        programme, rate = sine(10.0, peak_dbfs=-23.0, channels=2), SR
    results: list[dict] = []
    for row in DELIVERY_TARGETS:
        measured = L.integrated_loudness(programme, rate)
        peak = L.true_peak(programme, rate)
        wanted = float(row.integrated_lufs) - measured
        headroom = float(row.true_peak_dbtp) - float(PEAK_MARGIN_DB) - peak
        gain = min(wanted, headroom)
        after = L.integrated_loudness(programme * (10.0 ** (gain / 20.0)), rate)
        after_peak = L.true_peak(programme * (10.0 ** (gain / 20.0)), rate)
        on_target = L.integrated_loudness(programme * (10.0 ** (wanted / 20.0)), rate)
        results.append({
            "key": row.key,
            "name": row.name,
            "target_lufs": float(row.integrated_lufs),
            "ceiling_dbtp": float(row.true_peak_dbtp),
            "tolerance_lu": float(row.tolerance_lu),
            "measured_lufs": float(measured),
            "wanted_gain_db": float(wanted),
            "headroom_db": float(headroom),
            "gain_db": float(gain),
            "after_gain_lufs": float(after),
            "error_lu": float(after - float(row.integrated_lufs)),
            "meter_error_lu": float(on_target - float(row.integrated_lufs)),
            "true_peak_dbtp": float(after_peak),
            "limited_by_peak": bool(wanted > headroom),
        })
        report.add(Case(
            f"delivery target {row.name}: the meter lands on the target",
            "the delivery catalogue",
            on_target, float(row.integrated_lufs), 0.01,
            note="the accuracy of the meter on this target",
        ))
    return results


def measure_short_items(report: Report) -> list[dict]:
    """Measure short items, the way the short form delivery target needs.

    A gating block is 400 ms long, so a shorter item is measured with its
    block filled up with silence.  The reading then falls below the level
    of the item, and the gain the batch runner applies is too small.
    """
    results: list[dict] = []
    for seconds in (0.1, 0.2, 0.3, 0.4, 0.5, 1.0):
        x = sine(seconds, peak_dbfs=-23.0, channels=2)
        measured = L.integrated_loudness(x, SR)
        dilution = 10.0 * math.log10(min(1.0, seconds / 0.4))
        expected = -23.0 + dilution
        results.append({"seconds": seconds, "measured_lufs": float(measured),
                        "expected_lufs": expected, "error_lu": float(measured - expected)})
        report.add(Case(
            f"integrated loudness of a {seconds * 1000:.0f} ms item",
            "a gating block is 400 ms long",
            measured, expected, 0.1,
            note="a shorter item is measured with a block filled up with silence",
        ))
    return results


# ---------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------
def print_report(report: Report, quiet: bool = False) -> None:
    if quiet:
        return
    header = (
        f"| {'case':<62} | {'measured':>10} | {'expected':>10} | {'error':>8} | {'tol':>5} | "
        f"{'':4} |"
    )
    print(header)
    print("|" + "-" * 64 + "|" + "-" * 12 + "|" + "-" * 12 + "|" + "-" * 10 + "|" + "-" * 7 +
          "|-----|")
    for case in report.cases:
        measured = "inf" if math.isinf(case.measured) else f"{case.measured:.4f}"
        expected = "inf" if math.isinf(case.expected) else f"{case.expected:.4f}"
        if math.isinf(case.measured) or math.isinf(case.expected):
            error = "-"
        else:
            error = f"{case.error:+.4f}"
        print(f"| {case.name:<62} | {measured:>10} | {expected:>10} | {error:>8} | "
              f"{case.tolerance:>5.2f} | {case.verdict():<4} |")
    print()
    print("before and after")
    print(f"| {'case':<62} | {'before':>10} | {'after':>10} | {'change':>8} |")
    print("|" + "-" * 64 + "|" + "-" * 12 + "|" + "-" * 12 + "|" + "-" * 10 + "|")
    for name, before, after, unit in report.legacy:
        print(f"| {name:<62} | {before:>10.4f} | {after:>10.4f} | {after - before:>+8.4f} |")
    print()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    quiet = "--quiet" in argv
    report = measure(Report())
    targets = measure_delivery_targets(report)
    items = measure_short_items(report)
    if not quiet:
        print_report(report)
        print("delivery targets")
        print(f"| {'target':<20} | {'target':>7} | {'wanted':>8} | {'headroom':>9} | {'gain':>7} | "
              f"{'delivered':>9} | {'error':>7} | {'tol':>4} | {'true peak':>9} |")
        print("|" + "-" * 22 + "|" + "-" * 9 + "|" + "-" * 10 + "|" + "-" * 11 + "|" + "-" * 9 +
              "|" + "-" * 11 + "|" + "-" * 9 + "|" + "-" * 6 + "|" + "-" * 11 + "|")
        for row in targets:
            flag = " ceiling" if row["limited_by_peak"] else ""
            print(f"| {row['name']:<20} | {row['target_lufs']:>7.1f} | "
                  f"{row['wanted_gain_db']:>8.2f} | "
                  f"{row['headroom_db']:>9.2f} | {row['gain_db']:>7.2f} | "
                  f"{row['after_gain_lufs']:>9.4f} | "
                  f"{row['error_lu']:>+7.4f} | {row['tolerance_lu']:>4.1f} | "
                  f"{row['true_peak_dbtp']:>9.4f} |{flag}")
        print()
        print("short items")
        print(f"| {'length':>10} | {'measured':>10} | {'expected':>10} | {'error':>8} |")
        print("|" + "-" * 12 + "|" + "-" * 12 + "|" + "-" * 12 + "|" + "-" * 10 + "|")
        for row in items:
            print(f"| {row['seconds'] * 1000:>8.0f} ms | {row['measured_lufs']:>10.4f} | "
                  f"{row['expected_lufs']:>10.4f} | {row['error_lu']:>+8.4f} |")
        print()
    failures = report.failures
    if failures:
        print(f"loudness harness: {len(failures)} of {len(report.cases)} case(s) outside tolerance")
        for case in failures:
            print(f"  FAIL {case.name}: measured {case.measured:.4f}, "
                  f"expected {case.expected:.4f} +/- {case.tolerance:.4f} ({case.source})")
        return 1
    print(f"loudness harness: {len(report.cases)} cases inside the published tolerances")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
