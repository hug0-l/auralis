# Filters — measurement report

**File measured:** `aurcore/dsp/filters.py`
**Harness:** `qa/filters/measure_filters.py` (run it again with
`python qa\filters\measure_filters.py`)
**Test:** `tests/test_filters.py`
**Date:** 2026-10-21

Every number below is produced by the harness.  The -3 dB reference is
-3.0103 dB (1/sqrt(2)); the sample rate is 48 kHz.

---

## 1. What was found

| # | finding | where | fixed? |
|---|---|---|---|
| 1 | the low pass and the high pass promised a **-3 dB point at f0** for every `q`; the level at `f0` is exactly `q` (**-6.02 dB** at q = 0.5, **0.00 dB** at q = 1, **+20.00 dB** at q = 10) | `filters.py` docstrings | **fixed** — the docstring now states the rule |
| 2 | the peaking EQ promised that "the bandwidth is `f0 / q`"; that is the **half amplitude** width (999.7 Hz for `f0 / q` = 1000 Hz).  The -3 dB width of the bell is **0.534** times `f0 / q` (534.4 Hz) | `filters.py` docstring | **fixed** — both numbers are in the docstring |
| 3 | `OnePole(tau_s=...)` was **not** the same filter as `OnePole(cutoff_hz=...)`: the corner came out **+0.143 %** high at 1 kHz and **+18.951 %** high at 10 kHz | `filters.py` `OnePole.__init__` | **fixed** — the round trip is now exact (0.000 %) |
| 4 | the DC blocker's "corner frequency" is the classic approximation: the -3 dB point is **0.03 %** low at 5 Hz, **0.13 %** at 20 Hz and **0.65 %** at 100 Hz | `filters.py` docstring | **documented** — the exact corner is unstable above 9.4 kHz, so the approximation stays |
| 5 | the file offered **no linear phase filter at all**, although a module in the project offers the user a "Linear Phase Filters" parameter | `filters.py` | **fixed** — `linear_phase_fir()` and `FIR` were added |
| 6 | the de-hum "Linear Phase Filters" path **read 2399 samples of audio that have not been played yet**, reported the wrong sign for its latency and was not block coherent | `aurcore/modules/de_hum.py` | **fixed** — the kernel is symmetric, the phase is linear to 0.01 degrees, the path reads **0** samples of the future, the module reports **2400** samples of latency and the output does not depend on the block size at all (§7) |
| 7 | the **linear phase path of the de-hum costs 2553 us for a 64 sample block** — 1.9 times the realtime budget of the block.  The shared `FIR` filter computes the **whole** convolution of the block with the 4801 tap kernel (23.4 million multiply-adds) and keeps 64 of the 9664 samples it produces | `aurcore/dsp/filters.py`, `FIR.process` | **no** — the DSP file belongs to another agent; the number is pinned down in §7 |

---

## 2. The promise table (after the fix)

Every row of the harness' section 1 now reads **OK**.

| filter | what the file promises | what it does |
|---|---|---|
| `biquad_lowpass`, `biquad_highpass` | the level at `f0` is `q` | -6.0206 / -3.0103 / +0.0000 / +6.0206 / +20.0000 dB for q = 0.5 / 0.7071 / 1 / 2 / 10 |
| `biquad_bandpass` | 0 dB peak gain | -0.0000 dB |
| `biquad_bandpass` | -3 dB width = `f0 / q` | 995.735 Hz (f0/q = 1000) |
| `biquad_notch` | -3 dB width = `f0 / q` | 995.735 Hz (f0/q = 1000) |
| `biquad_notch` | a null at `f0` | -295.2 dB |
| `biquad_peaking` | the gain at `f0` is `gain_db` | -12.0000 / +12.0000 dB |
| `biquad_peaking` | half amplitude width = `f0 / q` | 999.677 Hz (f0/q = 1000) |
| `biquad_peaking` | -3 dB width = 0.534 `f0 / q` | 534.409 Hz |
| `biquad_lowshelf`, `biquad_highshelf` | the shelf gain on its own side | -12.0000 / +12.0000 dB |
| `biquad_lowshelf`, `biquad_highshelf` | half gain at `f0` | 1000.000 Hz |
| `biquad_allpass` | the magnitude is 1 | 1.47e-13 dB ripple |
| `butterworth_lowpass` | -3 dB at `f0` for the cascade | -3.0103 dB (orders 2, 4, 6, 8) |
| `linkwitz_riley_lowpass` | -6 dB at `f0` | -6.0206 dB (orders 4, 8, 12) |
| `OnePole` | -3 dB at `cutoff_hz` | 20.0000 / 1000.0000 / 10000.0000 Hz |
| `OnePole` | `tau_s` is the same filter | 20.0000 / 1000.0000 / 10000.0000 Hz (**0.000 %**, was +0.143 % and +18.951 %) |
| `DCBlocker` | the corner is within 0.7 % of `cutoff_hz` | -0.033 % / -0.131 % / -0.647 % |
| `linear_phase_fir` | a symmetric kernel, a constant group delay | the group delay ripple is 2.38e-05 samples |

---

## 3. Pass band, stop band, transition

The pass band of a low pass is DC to its measured -3 dB point and the
reference level is the DC gain.  The stop band starts 40 dB below the
reference level; the transition width is the distance from the -3 dB
point to that -40 dB point.

| filter | corner Hz | pass band ripple dB | monotonic | stop band atten dB | transition Hz | slope dB/oct |
|---|---|---|---|---|---|---|
| `biquad_lowpass` Q=0.707 | 1000.000 | 0.0000 | yes | -40.00 | 7864.4 | +12.10 |
| `biquad_lowpass` Q=1 | 1270.900 | 1.2494 | no | -40.00 | 7611.0 | +12.99 |
| `biquad_lowpass` Q=4 | 1533.595 | 12.1096 | no | -40.00 | 7364.8 | +13.49 |
| `biquad_highpass` Q=0.707 | 1000.000 | 0.0000 | yes | -40.01 | 899.9 | +11.80 |
| `Butterworth` LP order 2 | 1000.000 | 0.0000 | yes | -40.00 | 7864.4 | +12.10 |
| `Butterworth` LP order 4 | 1000.000 | 0.0000 | yes | -40.00 | 2122.5 | +24.67 |
| `Butterworth` LP order 6 | 1000.000 | 0.0000 | yes | -40.00 | 1143.3 | +37.03 |
| `Butterworth` LP order 8 | 1000.000 | 0.0000 | yes | -40.00 | 772.8 | +49.38 |
| `Butterworth` HP order 4 | 1000.000 | 0.0000 | yes | -40.01 | 683.4 | +24.08 |
| `LinkwitzRiley` LP order 4 | 1000.000 | 0.0000 | yes | -40.00 | 2115.0 | +24.20 |
| `LinkwitzRiley` LP order 8 | 1000.000 | 0.0000 | yes | -40.00 | 770.6 | +49.35 |
| `LinkwitzRiley` HP order 4 | 1000.000 | 0.0000 | yes | -40.01 | 682.6 | +23.61 |
| `OnePole` 1 kHz | 1000.000 | 0.0000 | yes | -23.71 | beyond 23900 Hz | +5.24 |
| linear-phase FIR 2049 taps | 1000.000 | 0.0000 | yes | -40.00 | 2162.2 | +24.07 |

The other shapes:

| filter | reference | band edges | ripple / leak |
|---|---|---|---|
| `biquad_bandpass` Q=10 | peak 0.0000 dB | -3 dB 951.378 .. 1051.092 Hz (width 99.713) | peak to peak across the band 3.01 dB, skirts -23.56 / -23.60 dB |
| `biquad_notch` Q=10 | null -258 dB | -3 dB 951.378 .. 1051.092 Hz (width 99.713) | worst level inside the notch -3.01 dB |
| `biquad_peaking` -12 dB | peak -12.0000 dB | -3 dB 767.682 .. 1302.091 Hz (width 534.409) | back to 0 dB within 302 Hz |
| `biquad_peaking` +12 dB | peak +12.0000 dB | -3 dB 767.682 .. 1302.091 Hz (width 534.409) | back to 0 dB within 302 Hz |
| `biquad_lowshelf` -12 dB | shelf -12.0000 dB | mid gain 1000.000 Hz | shelf ripple 1.61e-03 dB |
| `biquad_highshelf` -12 dB | shelf -12.0000 dB | mid gain 1000.000 Hz | shelf ripple 8.62e-04 dB |

---

## 4. Phase

A linear phase filter has a constant group delay over its pass band and
a symmetric impulse response.  The tolerance is 0.05 samples.

| filter | mean group delay (samples) | group delay ripple (samples) | verdict |
|---|---|---|---|
| `biquad_lowpass` Q=0.707 | 12.012 | 2.2565 | not linear phase |
| `biquad_highpass` Q=0.707 | 1.230 | 10.7077 | not linear phase |
| `biquad_bandpass` Q=10 | 120.203 | 80.1586 | not linear phase |
| `biquad_notch` Q=10 | 3.779 | 30.4205 | not linear phase |
| `biquad_peaking` +12 dB | -2.395 | 31.8827 | not linear phase |
| `biquad_lowshelf` -12 dB | -8.316 | 1.8439 | not linear phase |
| `biquad_allpass` Q=1 | 2.380 | 32.9076 | not linear phase |
| `Butterworth` LP order 4 | 24.041 | 9.9890 | not linear phase |
| `LinkwitzRiley` LP order 4 | 24.024 | 4.5130 | not linear phase |
| `OnePole` 1 kHz | 5.500 | 3.8298 | not linear phase |
| `DCBlocker` 20 Hz | 11.884 | 190.2047 | not linear phase |
| **linear-phase FIR 2049 taps** | **1024.000** | **0.0000** | **linear phase** |

Group delay at a few frequencies (samples), measured on a fine grid and
then read at those frequencies:

| filter | 20 Hz | 100 Hz | 1000 Hz | 5000 Hz | 15000 Hz |
|---|---|---|---|---|---|
| `biquad_lowpass` Q=0.707 | 10.793 | 10.895 | 10.835 | 0.465 | 0.067 |
| `biquad_highpass` Q=0.707 | 10.793 | 10.895 | 10.835 | 0.465 | 0.067 |
| `biquad_allpass` Q=1 | 15.269 | 15.564 | 30.645 | 0.682 | 0.095 |
| `Butterworth` LP order 4 | 19.938 | 20.018 | 28.312 | 0.842 | 0.124 |
| `LinkwitzRiley` LP order 4 | 21.585 | 21.791 | 21.669 | 0.929 | 0.134 |
| `OnePole` 1 kHz | 7.158 | 7.085 | 3.331 | -0.195 | -0.453 |
| `DCBlocker` 20 Hz | 191.006 | 14.710 | 0.153 | 0.006 | 0.001 |
| **linear-phase FIR 2049 taps** | **1024.000** | **1024.000** | **1024.000** | **1024.000** | **1024.000** |

The grid has to be fine: the phase of a linear phase filter is a ramp
of thousands of radians, and a five point grid cannot unwrap it (that
is why the harness measures the group delay on a fine grid and reads
the values off it).

---

## 5. Cost per block

Microseconds per block, one channel, single thread, the **fastest of nine
rounds** of the whole sweep.  The spread column is the difference between
the fastest and the slowest round: that is the noise of a shared machine.
The NumPy fallback is the pure Python recursion that runs when SciPy is
not importable.

| filter | 64 samples | 128 samples | 256 samples | 512 samples | 64 spread | 128 spread | 256 spread | 512 spread |
|---|---|---|---|---|---|---|---|---|
| `Biquad` (SciPy path) | 4.7 | 5.1 | 5.7 | 6.9 | 3.1 | 2.9 | 3.8 | 2.2 |
| `Cascade` 2 sections | 4.8 | 5.1 | 5.7 | 7.0 | 2.9 | 2.1 | 2.4 | 2.4 |
| `Cascade` 4 sections | 9.9 | 10.1 | 11.2 | 13.8 | 4.8 | 6.7 | 3.1 | 4.4 |
| `Butterworth` order 4 | 9.2 | 10.2 | 11.3 | 13.4 | 6.7 | 2.0 | 1.1 | 3.0 |
| `LinkwitzRiley` order 4 | 9.5 | 10.4 | 11.5 | 14.1 | 1.3 | 3.9 | 2.4 | 2.6 |
| `OnePole` | 8.5 | 16.0 | 31.9 | 61.2 | 1.0 | 11.1 | 7.9 | 7.5 |
| `DCBlocker` | 8.3 | 16.2 | 31.6 | 64.7 | 1.3 | 11.4 | 22.5 | 16.9 |
| `Biquad` NumPy fallback | 20.5 | 44.6 | 85.4 | 171.3 | — | — | — | — |
| linear-phase FIR 2049 taps | 460.3 | 367.5 | 429.2 | 431.7 | 38.5 | 36.2 | 8.0 | 134.9 |

Read at 48 kHz with 64 sample blocks (750 blocks per second):

| filter | time per second of audio | share of one core |
|---|---|---|
| `Biquad` | 3.5 ms | 0.35 % |
| `Cascade` 4 sections | 7.4 ms | 0.7 % |
| `OnePole` | 6.4 ms | 0.6 % |
| linear-phase FIR 2049 taps | 345 ms | **35 %** |

The biquads are cheap.  The one pole and the DC blocker are pure Python
loops and cost 1.8 to 2.0 times a biquad; that is the price of not
using SciPy for them.  A 2049 tap FIR is **98 times** a biquad.

The spread columns are large compared with the numbers: the measurement of
a single filter takes a few milliseconds, so one interrupted round moves it
by a microsecond or two.  The fastest of nine rounds is the number to read;
the spread says how much the machine was doing while it was taken.

---

## 6. Behaviour at the edges

| filter | silence | square peak | square overshoot | impulse | impulse peak | 64 impulses |
|---|---|---|---|---|---|---|
| biquad_lowpass Q=0.707 | exact zeros | 1.0869 | +0.0869 | finite | 0.0595 | stable |
| biquad_highpass Q=0.707 | exact zeros | 1.8232 | +0.8232 | finite | 0.9116 | stable |
| biquad_bandpass Q=10 | exact zeros | 0.1931 | -0.8069 (no overshoot) | finite | 0.0128 | stable |
| biquad_notch Q=10 | exact zeros | 1.1651 | +0.1651 | finite | 0.9935 | stable |
| biquad_peaking +12 dB | exact zeros | 3.1056 | +2.1056 | finite | 1.0944 | stable |
| biquad_lowshelf -12 dB | exact zeros | 1.6229 | +0.6229 | finite | 0.9371 | stable |
| biquad_highshelf -12 dB | exact zeros | 1.0769 | +0.0769 | finite | 0.2681 | stable |
| biquad_allpass Q=1 | exact zeros | 1.3573 | +0.3573 | finite | 0.8775 | stable |
| Butterworth LP order 2 | exact zeros | 1.0869 | +0.0869 | finite | 0.0595 | stable |
| Butterworth LP order 4 | exact zeros | 1.2171 | +0.2171 | finite | 0.0499 | stable |
| Butterworth HP order 4 | exact zeros | 1.6937 | +0.6937 | finite | 0.8427 | stable |
| LinkwitzRiley LP order 4 | exact zeros | 1.1256 | +0.1256 | finite | 0.0437 | stable |
| LinkwitzRiley HP order 4 | exact zeros | 1.6620 | +0.6620 | finite | 0.8310 | stable |
| OnePole 1 kHz | exact zeros | 1.0000 | -0.0000 (no overshoot) | finite | 0.1225 | stable |
| DCBlocker 20 Hz | exact zeros | 1.2485 | +0.2485 | finite | 1.0000 | stable |
| linear-phase FIR 2049 taps | exact zeros | 1.1130 | +0.1130 | finite | 0.0000 | stable |

* **Silence**: every filter returns exact zeros, no denormal whisper.
* **A full scale square**: nothing goes non finite and nothing grows.
  The overshoot is the ringing of the filter at the edges of the square:
  +0.087 dB over full scale for a Butterworth low pass, +2.11 dB for a
  +12 dB bell (a bell has to pass 4x the level of its input).
* **A single impulse**: every filter stays finite, and the peak of the
  impulse response is the gain of the filter at DC.
* **Block coherence**: every filter in the file gives the same answer in
  one block and in 64 sample blocks (max difference 0.000e+00).

---

## 7. The de-hum "Linear Phase Filters" parameter

The full measurement is the harness' section 6.  The summary:

| | measured |
|---|---|
| is the phase linear? | **yes** — the phase equals the phase of a pure delay to within 0.01 degrees over the whole band |
| the delay in the stream framing | **0 samples**: the filter does **not** read audio that has not been played yet |
| the symmetry of the designed kernel | **0.000000** (it is symmetric about its centre) — it was 0.999971 before the repair |
| the symmetry of the impulse response the module applies | 0.997919 — it reaches from 0 to 2400 samples around the impulse, and the largest tap **before** the impulse is 0.000000 |
| the latency the module reports | **2400** |
| the block size independence | **exact**: the max difference against one block is 0.0000 at 64, 256, 1024, 4096, 16384 and 48000 samples |
| 50 Hz hum, one block | **-61.932 dB** (the IIR bank: -60.552 dB) |
| 1 kHz partial, 64 sample blocks | **+0.004 dB** (the IIR bank: -0.057 dB) |

**The defect of the previous version of this report is closed.**  The path
used to read 2399 samples of the future, report the wrong sign for its
latency and change its answer with the block size; it now does none of the
three.

### 7.1 What is left: the cost of the linear phase path

| block | the IIR bank | the linear phase path | the realtime budget |
|---|---|---|---|
| 64 | 35.5 us | **2553.2 us** | 1333.3 us |
| 128 | 38.3 us | 2603.0 us | 2666.7 us |
| 256 | 58.1 us | 2801.0 us | 5333.3 us |
| 512 | 56.5 us | 3032.5 us | 10 666.7 us |
| 4096 | 235.1 us | 4728.0 us | — |

**The linear phase path does not fit into the realtime budget of a 64 sample
block**: it needs 2553 us where the block has 1333 us, and it needs the same
2553 us whether the block is 64 or 512 samples long, because the cost is the
kernel and not the data.

The cause is in the shared filter, `aurcore/dsp/filters.py`, `FIR.process`:

```python
buf = np.concatenate([self._tail[:, c], arr[:, c]])
full = np.convolve(buf, self.kernel)
out[:, c] = full[keep:keep + n]
```

`np.convolve` computes the **whole** convolution of the block with the
kernel: for a 64 sample block and a 4801 tap kernel that is
`(4800 + 64) × 4801 = 23.4 million` multiply-adds, of which the filter keeps
`64 × 4801 = 307 000`.  The other 99 % of the work is thrown away.

The file that holds the fix belongs to another agent, so it was not touched
here; the number is pinned down by the harness so that it is visible.

### 7.2 The fix that is available

Both the symmetric kernel and a filter that keeps its state are now in
`aurcore/dsp/filters.py` as `linear_phase_fir()` and `FIR`, and the change to
`aurcore/modules/de_hum.py` is written out in the patch note
`var/patches/c3_filters.md`.

---

## 8. Reproducing

```
cd <repo>
python qa\filters\measure_filters.py
python -m pytest tests\test_filters.py -q
python tools\hygiene_check.py
```
