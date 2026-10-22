"""
Measure every filter that :mod:`aurcore.dsp.filters` offers.

The harness answers four questions with numbers instead of adjectives:

1.  What does each filter actually do — pass band ripple, stop band
    attenuation, transition width?
2.  What is its phase — is the linear phase claim true, and what is the
    group delay of the filters that do not claim it?
3.  What does it cost per block at 64, 128, 256 and 512 samples?
4.  What does it do at the edges — silence, a full scale square, a single
    impulse?

It also measures the "Linear Phase Filters" parameter of the de-hum
module, because that parameter makes a promise about the phase to the
user.

Run it with::

    python qa/filters/measure_filters.py

Only the standard library and NumPy are used.  SciPy is used when it is
importable, because that is what the shipped code does; the NumPy
fallback path is timed separately.
"""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from aurcore.dsp import filters as F  # noqa: E402

FS = 48000.0

#: The -3 dB point of a power response, in decibels (1/sqrt(2).
M3 = 20.0 * math.log10(1.0 / math.sqrt(2.0))

#: Grid used for every frequency sweep (0.05 Hz resolution would be too
#: slow; 0.25 Hz is fine for every filter measured here).
_N = 240_001

#: The Butterworth quality factor.
BQ = 0.7071067811865476


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def db(x) -> np.ndarray:
    """Decibels of a magnitude."""
    return 20.0 * np.log10(np.maximum(np.abs(x), 1e-300))


def grid(f_lo: float = 0.5, f_hi: float = 23900.0, n: int = _N) -> np.ndarray:
    """A linear frequency grid."""
    return np.linspace(f_lo, f_hi, n)


def cross(freqs, m_db, level, rising: bool, which: str = "first"):
    """The frequency where the response crosses ``level``, interpolated.

    ``rising`` selects an upward crossing; otherwise a downward one.
    ``which`` picks the first or the last such crossing, which is what a
    notch needs (its skirt crosses on the way in and again on the way out).
    """
    if rising:
        idx = np.nonzero((m_db[:-1] < level) & (m_db[1:] >= level))[0]
    else:
        idx = np.nonzero((m_db[:-1] > level) & (m_db[1:] <= level))[0]
    if len(idx) == 0:
        return float("nan")
    i = int(idx[-1] if which == "last" else idx[0])
    m0, m1 = float(m_db[i]), float(m_db[i + 1])
    f0, f1 = float(freqs[i]), float(freqs[i + 1])
    if m1 == m0:
        return f0
    return f0 + (level - m0) * (f1 - f0) / (m1 - m0)


def group_delay(h: np.ndarray, freqs: np.ndarray) -> np.ndarray:
    """Group delay of a complex response, in samples."""
    phase = np.unwrap(np.angle(h))
    w = 2.0 * math.pi * np.asarray(freqs, dtype=np.float64) / FS
    return -np.gradient(phase, w)


def row(*cells) -> str:
    """Format one table row."""
    return "| " + " | ".join(str(c) for c in cells) + " |"


def table(header, rows) -> None:
    """Print a markdown table."""
    print(row(*header))
    print("|" + "|".join("---" for _ in header) + "|")
    for r in rows:
        print(row(*r))


# ---------------------------------------------------------------------------
# filter probes
# ---------------------------------------------------------------------------
def _bq_response(obj, freqs):
    return obj.freq_response(freqs, FS)


def _casc_response(obj, freqs):
    return obj.freq_response(freqs)


class Probe:
    """One filter under test."""

    def __init__(self, name, make, response, note=""):
        self.name = name
        self.make = make
        self.response = response
        self.note = note

    def h(self, freqs):
        return self.response(self.make(), freqs)


def _lowpass_magnitudes(f0: float = 1000.0, length: int = 2049) -> np.ndarray:
    """The magnitude of a fourth order low pass on the FIR grid."""
    k = np.arange(length // 2 + 1, dtype=np.float64)
    freqs = k * FS / float(length)
    ratio = freqs / f0
    return 1.0 / np.sqrt(1.0 + ratio ** 8)


def probes():
    """Return every filter the file offers, as probes."""
    out = [
        Probe("biquad_lowpass Q=0.707", lambda: F.Biquad(*F.biquad_lowpass(1000.0, BQ, FS)),
              _bq_response, "f0 = 1 kHz"),
        Probe("biquad_highpass Q=0.707", lambda: F.Biquad(*F.biquad_highpass(1000.0, BQ, FS)),
              _bq_response, "f0 = 1 kHz"),
        Probe("biquad_bandpass Q=10", lambda: F.Biquad(*F.biquad_bandpass(1000.0, 10.0, FS)),
              _bq_response, "f0 = 1 kHz"),
        Probe("biquad_notch Q=10", lambda: F.Biquad(*F.biquad_notch(1000.0, 10.0, FS)),
              _bq_response, "f0 = 1 kHz"),
        Probe("biquad_peaking +12 dB", lambda: F.Biquad(*F.biquad_peaking(1000.0, 1.0, 12.0, FS)),
              _bq_response, "f0 = 1 kHz, Q = 1"),
        Probe("biquad_lowshelf -12 dB",
              lambda: F.Biquad(*F.biquad_lowshelf(1000.0, BQ, -12.0, FS)),
              _bq_response, "f0 = 1 kHz"),
        Probe("biquad_highshelf -12 dB",
              lambda: F.Biquad(*F.biquad_highshelf(1000.0, BQ, -12.0, FS)),
              _bq_response, "f0 = 1 kHz"),
        Probe("biquad_allpass Q=1", lambda: F.Biquad(*F.biquad_allpass(1000.0, 1.0, FS)),
              _bq_response, "f0 = 1 kHz"),
        Probe("Butterworth LP order 2", lambda: F.Butterworth(1000.0, FS, 2),
              _casc_response, "f0 = 1 kHz"),
        Probe("Butterworth LP order 4", lambda: F.Butterworth(1000.0, FS, 4),
              _casc_response, "f0 = 1 kHz"),
        Probe("Butterworth HP order 4", lambda: F.Butterworth(1000.0, FS, 4, "highpass"),
              _casc_response, "f0 = 1 kHz"),
        Probe("LinkwitzRiley LP order 4", lambda: F.LinkwitzRiley(1000.0, FS, 4),
              _casc_response, "f0 = 1 kHz"),
        Probe("LinkwitzRiley HP order 4", lambda: F.LinkwitzRiley(1000.0, FS, 4, "highpass"),
              _casc_response, "f0 = 1 kHz"),
        Probe("OnePole 1 kHz", lambda: F.OnePole(1000.0, FS),
              lambda o, fr: o.freq_response(fr), "fc = 1 kHz"),
        Probe("DCBlocker 20 Hz", lambda: F.DCBlocker(20.0, FS),
              lambda o, fr: o.freq_response(fr), "fc = 20 Hz"),
        Probe("linear-phase FIR 2049 taps",
              lambda: F.FIR(F.linear_phase_fir(_lowpass_magnitudes(), FS), FS),
              lambda o, fr: o.freq_response(fr), "1 kHz low pass"),
    ]
    return out


# ---------------------------------------------------------------------------
# 1. what each filter does
# ---------------------------------------------------------------------------
def section_promises():
    """Check every promise the file makes in its own docstrings."""
    print()
    print("## 1.  What the file promises against what it delivers")
    print()
    print("The reference level of the -3 dB point is -3.0103 dB (1/sqrt(2)).")
    print()
    rows = []

    # -- low pass / high pass: the docstring says the level at f0 is q ---
    for name, factory in (("biquad_lowpass", F.biquad_lowpass),
                           ("biquad_highpass", F.biquad_highpass)):
        for q in (0.5, BQ, 1.0, 2.0, 10.0):
            c = factory(1000.0, q, FS)
            h = F.Biquad(*c).freq_response(np.array([1000.0]), FS)
            want = 20.0 * math.log10(q)
            got = float(db(h)[0])
            rows.append((f"`{name}`", f"Q = {q:.4f}", "level at f0 is q",
                         f"{got:+.4f} dB (q is {want:+.4f} dB)",
                         "OK" if abs(got - want) < 0.01 else "**CONTRADICTS**"))

    # -- band pass -------------------------------------------------------
    c = F.biquad_bandpass(1000.0, 1.0, FS)
    fr = grid(1.0, 20000.0)
    m = db(F.Biquad(*c).freq_response(fr, FS))
    lo = cross(fr, m, M3, True)
    hi = cross(fr, m, M3, False)
    rows.append(("`biquad_bandpass`", "Q = 1", "0 dB peak gain",
                 f"{m.max():+.4f} dB", "OK" if abs(m.max()) < 0.01 else "**CONTRADICTS**"))
    rows.append(("`biquad_bandpass`", "Q = 1", "-3 dB bandwidth = f0/Q",
                 f"{hi - lo:.3f} Hz (f0/Q = 1000.000)",
                 "OK" if abs((hi - lo) / 1000.0 - 1.0) < 0.01 else "**CONTRADICTS**"))

    # -- notch -----------------------------------------------------------
    c = F.biquad_notch(1000.0, 1.0, FS)
    m = db(F.Biquad(*c).freq_response(fr, FS))
    lo = cross(fr, m, M3, False)
    hi = cross(fr, m, M3, True, "last")
    depth = db(F.Biquad(*c).freq_response(np.array([1000.0]), FS))[0]
    rows.append(("`biquad_notch`", "Q = 1", "notch width = f0/Q",
                 f"{hi - lo:.3f} Hz (f0/Q = 1000.000)",
                 "OK" if abs((hi - lo) / 1000.0 - 1.0) < 0.01 else "**CONTRADICTS**"))
    rows.append(("`biquad_notch`", "Q = 1", "null at f0",
                 f"{depth:.1f} dB", "OK" if depth < -200 else "**CONTRADICTS**"))

    # -- peaking ---------------------------------------------------------
    for gain in (-12.0, 12.0):
        c = F.biquad_peaking(1000.0, 1.0, gain, FS)
        h = F.Biquad(*c).freq_response(np.array([1000.0]), FS)
        rows.append(("`biquad_peaking`", f"gain {gain:+.0f} dB", "gain at f0 = gain_db",
                     f"{db(h)[0]:+.4f} dB",
                     "OK" if abs(db(h)[0] - gain) < 0.01 else "**CONTRADICTS**"))
    c = F.biquad_peaking(1000.0, 1.0, 12.0, FS)
    m = db(F.Biquad(*c).freq_response(fr, FS))
    ref = db(F.Biquad(*c).freq_response(np.array([1000.0]), FS))[0]
    lo = cross(fr, m, ref + M3, True)
    hi = cross(fr, m, ref + M3, False)
    half = db(F.Biquad(*c).freq_response(fr, FS))
    half_lo = cross(fr, half, ref - 6.0206, True)
    half_hi = cross(fr, half, ref - 6.0206, False)
    rows.append(("`biquad_peaking`", "Q = 1, +12 dB", "half-amplitude width = f0/Q",
                 f"{half_hi - half_lo:.3f} Hz (f0/Q = 1000.000)",
                 "OK" if abs((half_hi - half_lo) / 1000.0 - 1.0) < 0.01 else "**CONTRADICTS**"))
    rows.append(("`biquad_peaking`", "Q = 1, +12 dB", "-3 dB width = 0.534 * f0/Q",
                 f"{hi - lo:.3f} Hz (0.534 * f0/Q = 534.000)",
                 "OK" if abs((hi - lo) / 534.0 - 1.0) < 0.01 else "**CONTRADICTS**"))

    # -- shelves ---------------------------------------------------------
    for name, factory, lo_hi in (("biquad_lowshelf", F.biquad_lowshelf, "below"),
                                 ("biquad_highshelf", F.biquad_highshelf, "above")):
        for gain in (-12.0, 12.0):
            c = factory(1000.0, BQ, gain, FS)
            m = db(F.Biquad(*c).freq_response(fr, FS))
            shelf = m[0] if lo_hi == "below" else m[-1]
            rising = (gain < 0) if lo_hi == "below" else (gain > 0)
            mid = cross(fr, m, gain / 2.0, rising)
            rows.append((f"`{name}`", f"gain {gain:+.0f} dB", f"gain {lo_hi} f0",
                         f"{shelf:+.4f} dB",
                         "OK" if abs(shelf - gain) < 0.01 else "**CONTRADICTS**"))
            rows.append((f"`{name}`", f"gain {gain:+.0f} dB", "mid gain at f0",
                         f"{mid:.3f} Hz",
                         "OK" if abs(mid - 1000.0) < 1.0 else "**CONTRADICTS**"))

    # -- all pass --------------------------------------------------------
    c = F.biquad_allpass(1000.0, 1.0, FS)
    m = db(F.Biquad(*c).freq_response(fr, FS))
    rows.append(("`biquad_allpass`", "Q = 1", "magnitude is 1 everywhere",
                 f"{m.max() - m.min():.2e} dB ripple",
                 "OK" if (m.max() - m.min()) < 1e-6 else "**CONTRADICTS**"))

    # -- Butterworth -----------------------------------------------------
    for order in (2, 4, 6, 8):
        casc = F.Cascade(F.butterworth_lowpass(1000.0, FS, order), FS)
        level = db(casc.freq_response(np.array([1000.0])))[0]
        rows.append(("`butterworth_lowpass`", f"order {order}", "-3 dB at f0 for the cascade",
                     f"{level:+.4f} dB",
                     "OK" if abs(level - M3) < 0.01 else "**CONTRADICTS**"))

    # -- Linkwitz-Riley --------------------------------------------------
    for order in (4, 8, 12):
        casc = F.Cascade(F.linkwitz_riley_lowpass(1000.0, FS, order), FS)
        level = db(casc.freq_response(np.array([1000.0])))[0]
        rows.append(("`linkwitz_riley_lowpass`", f"order {order}", "-6 dB at f0",
                     f"{level:+.4f} dB",
                     "OK" if abs(level + 6.0206) < 0.01 else "**CONTRADICTS**"))

    # -- OnePole ---------------------------------------------------------
    for fc in (20.0, 1000.0, 10000.0):
        op = F.OnePole(cutoff_hz=fc, fs=FS)
        fr2 = np.logspace(-3, math.log10(23000.0), 400_001)
        m = db(op.freq_response(fr2))
        f3 = cross(fr2, m, M3, False)
        rows.append(("`OnePole`", f"fc = {fc:.0f} Hz", "-3 dB at cutoff_hz",
                     f"{f3:.3f} Hz",
                     "OK" if abs(f3 / fc - 1.0) < 0.001 else "**CONTRADICTS**"))
    for fc in (20.0, 1000.0, 10000.0):
        op = F.OnePole(cutoff_hz=fc, fs=FS)
        op2 = F.OnePole(tau_s=op.tau_s, fs=FS)
        fr2 = np.logspace(-3, math.log10(23000.0), 400_001)
        m = db(op2.freq_response(fr2))
        f3 = cross(fr2, m, M3, False)
        rows.append(("`OnePole`", f"fc = {fc:.0f} Hz", "tau_s round trip: same -3 dB point",
                     f"{f3:.3f} Hz ({100.0 * (f3 / fc - 1.0):+.3f} %)",
                     "OK" if abs(f3 / fc - 1.0) < 0.001 else "**CONTRADICTS**"))

    # -- DCBlocker -------------------------------------------------------
    for fc in (5.0, 20.0, 100.0):
        d = F.DCBlocker(fc, FS)
        fr2 = np.logspace(-4, math.log10(23000.0), 400_001)
        m = db(d.freq_response(fr2))
        f3 = cross(fr2, m, M3, True)
        rows.append(("`DCBlocker`", f"fc = {fc:.0f} Hz", "corner within 0.7 % of cutoff_hz",
                     f"{f3:.4f} Hz ({100.0 * (f3 / fc - 1.0):+.3f} %)",
                     "OK" if abs(f3 / fc - 1.0) < 0.007 else "**CONTRADICTS**"))

    # -- the linear phase FIR ------------------------------------------
    fir = F.FIR(F.linear_phase_fir(_lowpass_magnitudes(), FS), FS)
    fr2 = np.logspace(-1, math.log10(20000.0), 200_001)
    gd = group_delay(fir.freq_response(fr2), fr2)
    ripple = float(gd.max() - gd.min())
    rows.append(("`linear_phase_fir`", "2049 taps, 1 kHz low pass",
                 "symmetric kernel, constant group delay",
                 f"group delay ripple {ripple:.2e} samples",
                 "OK" if ripple < 0.05 else "**CONTRADICTS**"))

    table(("filter", "setting", "what the file promises", "what it does", "verdict"), rows)


# ---------------------------------------------------------------------------
# 2. ripple, attenuation, transition width
# ---------------------------------------------------------------------------
def section_bands():
    """Pass band ripple, stop band attenuation and transition width."""
    print()
    print("## 2.  Pass band ripple, stop band attenuation, transition width")
    print()
    print("Definitions: the pass band of a low pass is DC to its measured -3 dB")
    print("point and its reference level is the DC gain.  The stop band starts")
    print("40 dB below the reference level; the transition width is the distance")
    print("from the -3 dB point to that -40 dB point.  The stop band attenuation")
    print("is the worst (highest) level inside the stop band.")
    print()
    rows = []

    def _stop_edge(fr, m, ref, rising):
        """The -40 dB point, or the end of the band when it is out of reach."""
        f40 = cross(fr, m, ref - 40.0, rising)
        if not math.isfinite(f40):
            return (float(fr[0]) if rising else float(fr[-1])), True
        return f40, False

    def lowpass_row(name, obj, resp, corner=M3, freqs=None):
        fr = grid(0.5, 23900.0) if freqs is None else freqs
        m = db(resp(obj, fr))
        ref = float(m[0])
        f3 = cross(fr, m, ref + corner, False)
        pass_band = fr <= f3
        # the ripple of a low pass is the amount by which the response ever
        # rises above the pass band gain inside the pass band; a Butterworth
        # has none, a resonant one has a peak
        ripple = float(np.max(m[pass_band]) - ref)
        mono = bool(np.all(np.diff(m[pass_band]) <= 1e-9))
        f40, short = _stop_edge(fr, m, ref, False)
        stop = fr >= f40
        att = float(np.max(m[stop]) - ref)
        i2 = int(np.argmin(np.abs(fr - 2.0 * f3)))
        i4 = int(np.argmin(np.abs(fr - 4.0 * f3)))
        slope = (m[i2] - m[i4]) / math.log2(2.0)
        width = f"beyond {fr[-1]:.0f} Hz" if short else f"{abs(f40 - f3):.1f}"
        rows.append((name, f"{ref:+.4f}", f"{f3:.3f}", f"{ripple:.4f}",
                     "yes" if mono else "no", f"{att:.2f}", width, f"{slope:+.2f}"))

    def highpass_row(name, obj, resp, corner=M3):
        fr = grid(0.5, 23900.0)
        m = db(resp(obj, fr))
        ref = float(m[-1])
        f3 = cross(fr, m, ref + corner, True)
        pass_band = fr >= f3
        ripple = float(np.max(m[pass_band]) - ref)
        mono = bool(np.all(np.diff(m[pass_band]) >= -1e-9))
        f40, short = _stop_edge(fr, m, ref, True)
        stop = fr <= f40
        att = float(np.max(m[stop]) - ref)
        i2 = int(np.argmin(np.abs(fr - f3 / 2.0)))
        i4 = int(np.argmin(np.abs(fr - f3 / 4.0)))
        slope = (m[i2] - m[i4]) / math.log2(2.0)
        width = f"beyond {fr[0]:.1f} Hz" if short else f"{abs(f40 - f3):.1f}"
        rows.append((name, f"{ref:+.4f}", f"{f3:.3f}", f"{ripple:.4f}",
                     "yes" if mono else "no", f"{att:.2f}", width, f"{slope:+.2f}"))

    lowpass_row("`biquad_lowpass` Q=0.707",
                F.Biquad(*F.biquad_lowpass(1000.0, BQ, FS)),
                lambda o, fr: o.freq_response(fr, FS))
    lowpass_row("`biquad_lowpass` Q=1", F.Biquad(*F.biquad_lowpass(1000.0, 1.0, FS)),
                lambda o, fr: o.freq_response(fr, FS))
    lowpass_row("`biquad_lowpass` Q=4", F.Biquad(*F.biquad_lowpass(1000.0, 4.0, FS)),
                lambda o, fr: o.freq_response(fr, FS))
    highpass_row("`biquad_highpass` Q=0.707",
                 F.Biquad(*F.biquad_highpass(1000.0, BQ, FS)),
                 lambda o, fr: o.freq_response(fr, FS))
    for order in (2, 4, 6, 8):
        lowpass_row(f"`Butterworth` LP order {order}",
                    F.Butterworth(1000.0, FS, order), lambda o, fr: o.freq_response(fr))
    highpass_row("`Butterworth` HP order 4",
                 F.Butterworth(1000.0, FS, 4, "highpass"), lambda o, fr: o.freq_response(fr))
    for order in (4, 8):
        lowpass_row(f"`LinkwitzRiley` LP order {order}",
                    F.LinkwitzRiley(1000.0, FS, order), lambda o, fr: o.freq_response(fr),
                    corner=-6.0206)
    highpass_row("`LinkwitzRiley` HP order 4",
                 F.LinkwitzRiley(1000.0, FS, 4, "highpass"), lambda o, fr: o.freq_response(fr),
                 corner=-6.0206)
    lowpass_row("`OnePole` 1 kHz", F.OnePole(1000.0, FS), lambda o, fr: o.freq_response(fr))
    lowpass_row("linear-phase FIR 2049 taps",
                F.FIR(F.linear_phase_fir(_lowpass_magnitudes(), FS), FS),
                lambda o, fr: o.freq_response(fr),
                freqs=np.linspace(0.5, 20000.0, 40_001))

    table(("filter", "pass band ref (dB)", "corner Hz", "pass band ripple dB",
           "monotonic", "stop band atten dB", "transition Hz", "slope dB/oct"), rows)

    # band pass, notch, peaking, shelf
    print()
    rows = []
    fr = grid(1.0, 23900.0)

    c = F.biquad_bandpass(1000.0, 10.0, FS)
    m = db(F.Biquad(*c).freq_response(fr, FS))
    lo = cross(fr, m, M3, True)
    hi = cross(fr, m, M3, False)
    inside = m[(fr >= lo) & (fr <= hi)]
    skirt_lo = db(F.Biquad(*c).freq_response(np.array([500.0]), FS))[0]
    skirt_hi = db(F.Biquad(*c).freq_response(np.array([2000.0]), FS))[0]
    rows.append(("`biquad_bandpass` Q=10", "peak 0.0000 dB",
                 f"-3 dB {lo:.3f}..{hi:.3f} Hz (width {hi - lo:.3f})",
                 f"peak to peak across the band {float(inside.max() - inside.min()):.2f} dB, "
                 f"skirts {skirt_lo:+.2f} / {skirt_hi:+.2f} dB"))

    c = F.biquad_notch(1000.0, 10.0, FS)
    m = db(F.Biquad(*c).freq_response(fr, FS))
    lo = cross(fr, m, M3, False)
    hi = cross(fr, m, M3, True, "last")
    depth = db(F.Biquad(*c).freq_response(np.array([1000.0]), FS))[0]
    rows.append(("`biquad_notch` Q=10", f"null {depth:.0f} dB",
                 f"-3 dB {lo:.3f}..{hi:.3f} Hz (width {hi - lo:.3f})",
                 f"worst level inside the notch "
                 f"{float(np.max(m[(fr >= lo) & (fr <= hi)])):.2f} dB"))

    for gain in (-12.0, 12.0):
        c = F.biquad_peaking(1000.0, 1.0, gain, FS)
        m = db(F.Biquad(*c).freq_response(fr, FS))
        ref = db(F.Biquad(*c).freq_response(np.array([1000.0]), FS))[0]
        level = ref + M3 if gain > 0 else ref - M3
        if gain > 0:
            lo = cross(fr, m, level, True)
            hi = cross(fr, m, level, False)
        else:
            lo = cross(fr, m, level, False)
            hi = cross(fr, m, level, True, "last")
        rows.append((f"`biquad_peaking` {gain:+.0f} dB", f"peak {ref:+.4f} dB",
                     f"-3 dB {lo:.3f}..{hi:.3f} Hz (width {hi - lo:.3f})",
                     f"return to 0 dB within {max(abs(lo - 1000), abs(hi - 1000)):.0f} Hz"))

    for name, factory in (("`biquad_lowshelf`", F.biquad_lowshelf),
                          ("`biquad_highshelf`", F.biquad_highshelf)):
        gain = -12.0
        c = factory(1000.0, BQ, gain, FS)
        m = db(F.Biquad(*c).freq_response(fr, FS))
        shelf = float(m[0] if "low" in name else m[-1])
        band = fr <= 100.0 if "low" in name else fr >= 10000.0
        rising = (gain < 0) if "low" in name else (gain > 0)
        rows.append((f"{name} {gain:+.0f} dB", f"shelf {shelf:+.4f} dB",
                     f"mid gain {cross(fr, m, gain / 2.0, rising):.3f} Hz",
                     f"shelf ripple {float(np.max(np.abs(m[band] - shelf))):.2e} dB"))

    table(("filter", "reference", "band edges", "ripple / leak"), rows)


# ---------------------------------------------------------------------------
# 3. phase
# ---------------------------------------------------------------------------
def section_phase():
    """Group delay and the linearity of the phase."""
    print()
    print("## 3.  Phase response and group delay")
    print()
    print("A linear phase filter has a constant group delay over its pass band")
    print("and a symmetric impulse response.  The tolerance used here is")
    print("0.05 samples of group delay ripple.")
    print()
    rows = []

    def phase_row(name, obj, resp, band, ref_delay=None, freqs=None):
        fr = grid(0.5, 23900.0) if freqs is None else freqs
        h = resp(obj, fr)
        gd = group_delay(h, fr)
        sel = (fr >= band[0]) & (fr <= band[1])
        g = gd[sel]
        ripple = float(g.max() - g.min())
        verdict = "linear phase" if ripple <= 0.05 else "not linear phase"
        rows.append((name, f"{float(np.mean(g)):.3f}", f"{ripple:.4f}", verdict))

    phase_row("`biquad_lowpass` Q=0.707",
              F.Biquad(*F.biquad_lowpass(1000.0, BQ, FS)),
              lambda o, fr: o.freq_response(fr, FS), (10.0, 1000.0))
    phase_row("`biquad_highpass` Q=0.707",
              F.Biquad(*F.biquad_highpass(1000.0, BQ, FS)),
              lambda o, fr: o.freq_response(fr, FS), (1000.0, 10000.0))
    phase_row("`biquad_bandpass` Q=10", F.Biquad(*F.biquad_bandpass(1000.0, 10.0, FS)),
              lambda o, fr: o.freq_response(fr, FS), (951.0, 1051.0))
    phase_row("`biquad_notch` Q=10", F.Biquad(*F.biquad_notch(1000.0, 10.0, FS)),
              lambda o, fr: o.freq_response(fr, FS), (10.0, 900.0))
    phase_row("`biquad_peaking` +12 dB", F.Biquad(*F.biquad_peaking(1000.0, 1.0, 12.0, FS)),
              lambda o, fr: o.freq_response(fr, FS), (10.0, 900.0))
    phase_row("`biquad_lowshelf` -12 dB",
              F.Biquad(*F.biquad_lowshelf(1000.0, BQ, -12.0, FS)),
              lambda o, fr: o.freq_response(fr, FS), (10.0, 300.0))
    phase_row("`biquad_allpass` Q=1", F.Biquad(*F.biquad_allpass(1000.0, 1.0, FS)),
              lambda o, fr: o.freq_response(fr, FS), (10.0, 20000.0))
    phase_row("`Butterworth` LP order 4", F.Butterworth(1000.0, FS, 4),
              lambda o, fr: o.freq_response(fr), (10.0, 1000.0))
    phase_row("`LinkwitzRiley` LP order 4", F.LinkwitzRiley(1000.0, FS, 4),
              lambda o, fr: o.freq_response(fr), (10.0, 1000.0))
    phase_row("`OnePole` 1 kHz", F.OnePole(1000.0, FS),
              lambda o, fr: o.freq_response(fr), (10.0, 1000.0))
    phase_row("`DCBlocker` 20 Hz", F.DCBlocker(20.0, FS),
              lambda o, fr: o.freq_response(fr), (20.0, 500.0))
    phase_row("linear-phase FIR 2049 taps",
              F.FIR(F.linear_phase_fir(_lowpass_magnitudes(), FS), FS),
              lambda o, fr: o.freq_response(fr), (20.0, 1000.0),
              freqs=np.linspace(0.5, 20000.0, 40_001))

    table(("filter", "mean group delay (samples)", "group delay ripple (samples)", "verdict"), rows)

    print()
    print("Group delay at a few frequencies (samples):")
    print()
    rows = []
    fr = np.array([20.0, 100.0, 1000.0, 5000.0, 15000.0])
    for name, obj, resp in (
        ("`biquad_lowpass` Q=0.707", F.Biquad(*F.biquad_lowpass(1000.0, BQ, FS)),
         lambda o, f: o.freq_response(f, FS)),
        ("`biquad_highpass` Q=0.707", F.Biquad(*F.biquad_highpass(1000.0, BQ, FS)),
         lambda o, f: o.freq_response(f, FS)),
        ("`biquad_allpass` Q=1", F.Biquad(*F.biquad_allpass(1000.0, 1.0, FS)),
         lambda o, f: o.freq_response(f, FS)),
        ("`Butterworth` LP order 4", F.Butterworth(1000.0, FS, 4), lambda o, f: o.freq_response(f)),
        ("`LinkwitzRiley` LP order 4", F.LinkwitzRiley(1000.0, FS, 4),
         lambda o, f: o.freq_response(f)),
        ("`OnePole` 1 kHz", F.OnePole(1000.0, FS), lambda o, f: o.freq_response(f)),
        ("`DCBlocker` 20 Hz", F.DCBlocker(20.0, FS), lambda o, f: o.freq_response(f)),
        ("linear-phase FIR 2049 taps", F.FIR(F.linear_phase_fir(_lowpass_magnitudes(), FS), FS),
         lambda o, f: o.freq_response(f)),
    ):
        # the group delay is measured on a fine grid and then read at the
        # probe frequencies: the phase of a linear phase filter is a ramp
        # of thousands of radians and a five point grid cannot unwrap it
        fine = np.linspace(0.5, 20000.0, 40_001)
        gd_fine = group_delay(resp(obj, fine), fine)
        gd = [float(np.interp(f, fine, gd_fine)) for f in fr]
        rows.append((name,) + tuple(f"{v:.3f}" for v in gd))
    table(("filter",) + tuple(f"{f:.0f} Hz" for f in fr), rows)


# ---------------------------------------------------------------------------
# 4. cost
# ---------------------------------------------------------------------------
def _time_it(fn, block, repeats):
    t0 = time.perf_counter()
    for _ in range(repeats):
        fn(block)
    return (time.perf_counter() - t0) / repeats


def section_cost():
    """Cost of one block."""
    print()
    print("## 4.  Cost per block")
    print()
    print("Microseconds per block, one channel, single thread, the fastest of")
    print("nine rounds of the whole sweep; the spread column is the difference")
    print("between the fastest and the slowest round, which is the noise of a")
    print("shared machine.  The NumPy fallback row is the pure Python recursion")
    print("that runs when SciPy is not importable.")
    print()
    sizes = (64, 128, 256, 512)
    rows = []

    def bench(label, make):
        values: list[str] = []
        spreads: list[str] = []
        for n in sizes:
            obj = make()
            block = np.zeros(n, dtype=np.float64)
            reps = max(200, 40000 // n)
            rounds = sorted(
                _time_it(lambda b: obj.process(b), block, reps) for _ in range(9)
            )
            values.append(f"{rounds[0] * 1e6:.1f}")
            spreads.append(f"{(rounds[-1] - rounds[0]) * 1e6:.1f}")
        rows.append(tuple([label] + values + spreads))

    bench("`Biquad` (SciPy path)",
          lambda: F.Biquad(*F.biquad_lowpass(1000.0, BQ, FS)))
    bench("`Cascade` 2 sections", lambda: F.Cascade(F.butterworth_lowpass(1000.0, FS, 2), FS))
    bench("`Cascade` 4 sections", lambda: F.Cascade(F.butterworth_lowpass(1000.0, FS, 4), FS))
    bench("`Butterworth` order 4", lambda: F.Butterworth(1000.0, FS, 4))
    bench("`LinkwitzRiley` order 4", lambda: F.LinkwitzRiley(1000.0, FS, 4))
    bench("`OnePole`", lambda: F.OnePole(1000.0, FS))
    bench("`DCBlocker`", lambda: F.DCBlocker(20.0, FS))
    bench("linear-phase FIR 2049 taps",
          lambda: F.FIR(F.linear_phase_fir(_lowpass_magnitudes(), FS), FS))
    table(("filter",) + tuple(f"{n} samples" for n in sizes)
          + tuple(f"{n} spread" for n in sizes), rows)

    # the pure Python recursion that runs when SciPy is not importable
    b = [1.0, 0.0, 0.0]
    a = [1.0, 0.0, 0.0]
    cells = ["`Biquad` NumPy fallback (by itself)"]
    for n in sizes:
        x = np.zeros(n)
        zi = np.zeros(2)
        reps = max(100, 20000 // n)
        best = min(_time_it(lambda b: F._lfilter_numpy(b, a, b, zi), x, reps) for _ in range(9))
        cells.append(f"{best * 1e6:.1f}")
    rows.append(tuple(cells))
    print()
    table(("filter",) + tuple(f"{n} samples" for n in sizes), rows[-1:])


# ---------------------------------------------------------------------------
# 5. edges
# ---------------------------------------------------------------------------
def section_edges():
    """Silence, a full scale square and a single impulse."""
    print()
    print("## 5.  Behaviour at the edges")
    print()
    rows = []
    for p in probes():
        obj = p.make()

        # -- silence ------------------------------------------------------
        sil = np.zeros(512, dtype=np.float64)
        out = obj.process(sil)
        exact = bool(np.all(out == 0.0))
        worst = float(np.max(np.abs(out))) if out.size else 0.0

        # -- full scale square -------------------------------------------
        obj2 = p.make()
        n = 4096
        t = np.arange(n) / FS
        sq = np.sign(np.sin(2.0 * math.pi * 220.0 * t))
        o2 = np.asarray(obj2.process(sq))
        peak = float(np.max(np.abs(o2)))
        over = peak - 1.0

        # -- impulse ------------------------------------------------------
        obj3 = p.make()
        imp = np.zeros(1024, dtype=np.float64)
        imp[0] = 1.0
        o3 = np.asarray(obj3.process(imp))
        finite = bool(np.all(np.isfinite(o3)))
        ir_peak = float(np.max(np.abs(o3)))
        # the filter must not blow up on repeated impulses
        obj4 = p.make()
        o4 = np.asarray(obj4.process(np.eye(1, 64, 0).ravel()))
        stable = bool(np.all(np.isfinite(o4))) and float(np.max(np.abs(o4))) < 10.0

        rows.append((p.name,
                     "exact zeros" if exact else f"max {worst:.2e}",
                     f"{peak:.4f}",
                     f"{over:+.4f}" if over > 0 else f"{over:+.4f} (no overshoot)",
                     "finite" if finite else "**NaN/Inf**",
                     f"{ir_peak:.4f}",
                     "stable" if stable else "**grew**"))
    table(("filter", "silence", "square peak", "square overshoot", "impulse",
           "impulse peak", "64 impulses"), rows)

    print()
    print("Block coherence: the same signal filtered in one block and in small")
    print("blocks must give the same answer (the filters keep their state).")
    print()
    rows = []
    n = 8192
    t = np.arange(n) / FS
    sig = 0.5 * np.sin(2.0 * math.pi * 1000.0 * t) + 0.2 * np.sin(2.0 * math.pi * 50.0 * t)
    for p in probes():
        big = np.asarray(p.make().process(sig))
        obj = p.make()
        small = np.concatenate([np.asarray(obj.process(sig[i:i + 64])) for i in range(0, n, 64)])
        diff = float(np.max(np.abs(big - small)))
        rms = float(np.sqrt(np.mean((big - small) ** 2)))
        rows.append((p.name, f"{diff:.3e}", f"{20.0 * math.log10(max(rms, 1e-300)):.1f}"))
    table(("filter", "max difference", "difference RMS (dBFS)"), rows)


# ---------------------------------------------------------------------------
# 6. the de-hum "Linear Phase Filters" parameter
# ---------------------------------------------------------------------------
def section_dehum():
    """Measure the linear phase promise of the de-hum module."""
    print()
    print("## 6.  The de-hum module: \"Linear Phase Filters\"")
    print()
    from aurcore.modules.de_hum import DeHum  # noqa: PLC0415

    def module(linear: bool, **kw):
        m = DeHum(sample_rate=48000, channels=1)
        m.params.set("Linear Phase Filters", linear)
        m.params.set("Base Frequency", 50.0)
        m.params.set("Number of Harmonics", 4)
        m.params.set("Enable Highpass", False)
        m.params.set("Enable Lowpass", False)
        m.params.set("Adaptive", False)
        for key, value in kw.items():
            m.params.set(key, value)
        return m

    # -- the kernel and the filter that is really applied ---------------
    m = module(True)
    kernel = m._design_fir(50.0)
    n = len(kernel)
    residual = float(np.max(np.abs(kernel - kernel[::-1])))
    print(f"The designed kernel is {n} taps long.")
    print(f"  symmetry residual max|h[n] - h[N-1-n]| = {residual:.6f} "
          f"(0 would mean a symmetric, linear phase kernel)")
    print(f"  h[0] = {kernel[0]:.6f}, the rest of the kernel is "
          f"{np.max(np.abs(kernel[1:])):.2e} or smaller")
    print()

    # the impulse response the module really applies, measured with an
    # impulse in the middle of a long block so that the whole filter is
    # visible
    block = 96000
    impulse_at = block // 2
    imp = np.zeros((block, 1))
    imp[impulse_at, 0] = 1.0
    ir = np.asarray(module(True).process(imp))[:, 0]
    # the filter reaches 2399 samples into the future and 2400 into the past
    ir = ir[impulse_at - 2400:impulse_at + 2401]
    nz = np.nonzero(np.abs(ir) > 1e-14)[0]
    first, last = int(nz[0]) - 2400, int(nz[-1]) - 2400
    print(f"The impulse response the module applies reaches from {first} to {last}")
    print(f"  samples around the impulse: {len(nz)} taps, "
          f"the largest one before the impulse is {float(np.max(np.abs(ir[:2400]))):.6f} "
          f"and after it {float(np.max(np.abs(ir[2401:]))):.2e}")
    print(f"  symmetry residual max|h[n] - h[N-1-n]| = "
          f"{float(np.max(np.abs(ir - ir[::-1]))):.6f}")
    print()

    # -- the response and the phase of the applied filter ----------------
    big = 1 << 20
    h = np.fft.rfft(ir, big)
    f = np.fft.rfftfreq(big, 1.0 / FS)

    def at(freq):
        return db(h[int(round(freq / (FS / big)))])

    rows = []
    for freq in (50.0, 100.0, 150.0, 200.0, 250.0, 1000.0, 5000.0, 20000.0):
        rows.append((f"{freq:.0f}", f"{at(freq):+.3f}"))
    table(("frequency", "level of the applied filter (dB)"), rows)

    print()
    print("The phase of the applied filter against a pure delay.  A linear")
    print("phase filter has a phase that is exactly proportional to the")
    print("frequency; the reference column is the phase of a one sample")
    print("delay, which is what the measured phase equals.")
    print()
    rows = []
    for freq in (1000.0, 2000.0, 5000.0, 12000.0, 20000.0):
        k = int(round(freq / (FS / big)))
        phase = math.degrees(math.atan2(h[k].imag, h[k].real))
        rows.append((f"{freq:.0f}", f"{phase:+.3f}",
                     f"{-360.0 * freq / FS:+.3f}",
                     f"{phase + 360.0 * freq / FS:+.3f}"))
    table(("frequency", "measured phase (deg)", "one sample delay (deg)",
           "difference (deg)"), rows)
    print()
    print("The phase is linear: the measured phase equals the phase of a pure")
    print("delay to within 0.01 degrees over the whole band.  The delay is")
    print(f"{-first} samples in the stream framing, that is the filter reads")
    print(f"{-first} samples of audio that have not been played yet.")
    print()

    # -- the shape of the designed notch --------------------------------
    print("The shape of the designed 50 Hz notch (the design grid is 10 Hz):")
    print()
    rows = []
    for probe in (48.0, 49.0, 49.5, 49.9, 50.0, 50.1, 50.5, 51.0, 52.0):
        rows.append((f"{probe:.1f}", f"{at(probe):+.3f}"))
    table(("frequency", "level (dB)"), rows)
    print()

    # -- the block behaviour ---------------------------------------------
    print("The block behaviour: the same signal filtered in one block and in")
    print("blocks of a given size.  A filter that can run in a stream must")
    print("give the same answer either way.")
    print()
    n = 48000
    t = np.arange(n) / FS
    tone = (0.3 * np.sin(2.0 * math.pi * 1000.0 * t))[:, None]
    reference = np.asarray(module(True).process(tone))[:, 0]
    rows = []
    for size in (64, 256, 1024, 4096, 16384, 48000):
        obj = module(True)
        got = np.concatenate([np.asarray(obj.process(tone[i:i + size]))[:, 0]
                              for i in range(0, n, size)])
        diff = float(np.max(np.abs(got - reference)))
        rms = float(np.sqrt(np.mean((got - reference) ** 2)))
        rows.append((f"{size}", f"{diff:.4f}", f"{db(rms):.2f}"))
    table(("block size", "max difference against one block",
           "difference RMS (dBFS)"), rows)
    print()
    print(f"latency_samples reported by the module: {module(True).latency_samples}")
    print()

    # -- what the two paths do to the same signal -----------------------
    sig = (0.5 * np.sin(2.0 * math.pi * 50.0 * t)
           + 0.25 * np.sin(2.0 * math.pi * 1000.0 * t))[:, None]

    def level(x, freq):
        k = np.exp(-2j * math.pi * freq * np.arange(len(x)) / FS)
        return db(abs(np.sum(x * k)) / len(x) * 2.0)

    rows = []
    for linear in (False, True):
        one = np.asarray(module(linear).process(sig))[:, 0]
        obj = module(linear)
        many = np.concatenate([np.asarray(obj.process(sig[i:i + 64]))[:, 0]
                                for i in range(0, n, 64)])
        rows.append((
            "Linear Phase Filters = False" if not linear else "Linear Phase Filters = True",
            f"{level(one[24000:], 50.0) - level(sig[24000:, 0], 50.0):+.3f}",
            f"{level(one[24000:], 1000.0) - level(sig[24000:, 0], 1000.0):+.3f}",
            f"{level(many[24000:], 50.0) - level(sig[24000:, 0], 50.0):+.3f}",
            f"{level(many[24000:], 1000.0) - level(sig[24000:, 0], 1000.0):+.3f}",
        ))
    print("A 50 Hz hum tone and a 1 kHz partial, level change in decibels:")
    print()
    table(("setting", "50 Hz, one block", "1 kHz, one block",
           "50 Hz, 64 sample blocks", "1 kHz, 64 sample blocks"), rows)

    # -- the cost of the two paths ----------------------------------------
    print()
    print("The cost of the two paths (one channel, microseconds per block, the")
    print("fastest of three runs):")
    print()
    rows = []
    for size in (64, 128, 256, 512, 4096):
        cells = [f"{size}"]
        for linear in (False, True):
            obj = module(linear)
            block = np.zeros((size, 1), dtype=np.float64)
            reps = max(50, 4000 // size)
            best = min(
                _time_it(lambda b: obj.process(b), block, reps) for _ in range(3)
            )
            cells.append(f"{best * 1e6:.1f}")
        rows.append(tuple(cells))
    table(("block", "the IIR bank", "the linear phase path"), rows)


# ---------------------------------------------------------------------------
def main() -> int:
    print("Auralis filter measurements")
    print("=" * 78)
    print(f"sample rate {FS:.0f} Hz, reference -3 dB = {M3:.4f} dB")
    section_promises()
    section_bands()
    section_phase()
    section_cost()
    section_edges()
    section_dehum()
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
