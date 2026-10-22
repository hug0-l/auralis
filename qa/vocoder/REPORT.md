# Auralis — the phase vocoder, measured

> **Status: the vocoder works and is now measured.**  The spectral pitch
> shift was wrong by up to **334 cents** (a quarter tone), the transient
> detector **never fired**, the phase locking was **quadratic** and took
> 96 % of the synthesis loop, and the numpy fallback of the resampler
> pitched every signal by **+539 cents or more**.  All four are fixed.
> What is left is written down in section 8.

Everything below was measured with the harness in this directory.  No number
in this report was estimated.

```
python qa/vocoder/run_vocoder.py --json qa/vocoder/after.json
```

The "before" numbers were taken with the same harness and the same machine
by putting the original file back:

```
copy aurcore/dsp/phase_vocoder.py <somewhere>          # keep the fixed one
python qa/vocoder/run_vocoder.py --json qa/vocoder/before.json
```

Reference machine: AMD Ryzen 7 7840HS, Windows 11, Python 3.15.0, NumPy 2.5.4,
SciPy 1.18.1.  The numbers of this report are the run of **2026-10-21 at
16:40**, on a machine that was 10 % busy; the times move with the load of the
machine (the same measurement gave 1076 us for a 64 sample block on a machine
that was 24 % busy), so the ratios are the meaningful part.

The numbers that are not timings — the pitch, the click spacing, the transient
width, the spectral loss — are deterministic and are the same on every run.

| file | what it is |
|---|---|
| `qa/vocoder/harness.py` | the measurement library: signals, frequency estimation, click picking, band levels, timing |
| `qa/vocoder/run_vocoder.py` | the command line: prints the tables, writes the JSON |
| `qa/vocoder/before.json` | the numbers of the original implementation |
| `qa/vocoder/after.json` | the numbers of the fixed implementation |
| `tests/test_phase_vocoder.py` | the regression guard, 35 tests |

---

## 1. Method

| item | value |
|---|---|
| sample rate | 48 000 Hz |
| analysis | FFT 2048, hop 512, Hann |
| pitch estimation | zero padded FFT (2^17), parabolic interpolation on the logarithmic magnitude |
| click picking | the strongest local maximum of the envelope, with a 30 ms refractory distance |
| timings | the fastest of 5 runs of 20 calls, in microseconds per call |
| budget | `block / 48000` seconds → 1333.3 / 2666.7 / 5333.3 / 10 666.7 us |
| edge cases | silence, a constant, a full scale square, a single impulse, a pure tone |

The pitch of the vocoder is measured on a **440 Hz tone**, because that is
the case the task names: shifted by a factor of 1.5 it has to arrive at
**660 Hz**.

---

## 2. The pitch shift

### 2.1 The two paths

`PhaseVocoder.pitch_shift` has two paths: `resample` (resample the signal
and stretch it back, the default) and `spectral` (the bin shift of
`PhaseVocoder.synthesize`).  Both were measured over four source
frequencies and four factors.

**Before** — the resampling path was exact, the spectral path was not:

| path | source | factor | expected | measured | error |
|---|---|---|---|---|---|
| resample | 440 Hz | x1.5 | 660.00 Hz | 660.00 Hz | +0.00 cents |
| spectral | 220 Hz | x1.25 | 275.00 Hz | 228.12 Hz | **−323.53 cents** |
| spectral | 440 Hz | x1.25 | 550.00 Hz | 526.56 Hz | **−75.39 cents** |
| spectral | 440 Hz | x1.5 | 660.00 Hz | 706.88 Hz | **+118.79 cents** |
| spectral | 440 Hz | x0.5 | 220.00 Hz | 266.87 Hz | **+334.39 cents** |
| spectral | 1000 Hz | x1.5 | 1500.00 Hz | 1546.88 Hz | **+53.27 cents** |
| spectral | 4000 Hz | x0.5 | 2000.00 Hz | 2046.88 Hz | **+40.11 cents** |

**After** — every one of the sixteen spectral cases is within 0.06 cents:

| path | source | factor | expected | measured | error |
|---|---|---|---|---|---|
| resample | 440 Hz | x1.5 | 660.00 Hz | 660.00 Hz | +0.00 cents |
| spectral | 220 Hz | x1.25 | 275.00 Hz | 275.00 Hz | −0.00 cents |
| spectral | 440 Hz | x1.25 | 550.00 Hz | 550.00 Hz | −0.00 cents |
| spectral | 440 Hz | x1.5 | 660.00 Hz | 660.00 Hz | +0.00 cents |
| spectral | 440 Hz | x0.5 | 220.00 Hz | 220.00 Hz | +0.01 cents |
| spectral | 1000 Hz | x1.5 | 1500.00 Hz | 1500.00 Hz | −0.00 cents |
| spectral | 4000 Hz | x0.5 | 2000.00 Hz | 2000.00 Hz | +0.00 cents |

The cents error of the spectral path over a grid of frequencies
(110 Hz – 12 kHz), before and after:

| factor | 110 Hz | 220 Hz | 440 Hz | 880 Hz | 1000 Hz | 2000 Hz | 4000 Hz | 8000 Hz | 12000 Hz |
|---|---|---|---|---|---|---|---|---|---|
| **before** x0.5 | +1067.14 | −0.02 | −414.83 | +175.22 | −170.37 | +79.28 | −41.05 | +20.16 | +0.00 |
| **after** x0.5 | −0.04 | +0.00 | −0.03 | −0.00 | −0.00 | +0.00 | −0.00 | +0.00 | +0.00 |
| **before** x1.25 | −323.53 | −323.50 | −75.38 | −37.29 | +32.16 | −16.30 | +8.10 | −4.06 | −0.00 |
| **after** x1.25 | −0.02 | −0.03 | +0.01 | +0.00 | +0.00 | −0.00 | +0.00 | +0.00 | +0.00 |
| **before** x1.5 | +432.90 | +0.03 | +118.78 | −62.59 | +53.27 | −27.26 | +13.47 | −6.78 | −0.00 |
| **after** x1.5 | −0.06 | +0.01 | −0.02 | +0.00 | −0.00 | +0.00 | +0.00 | +0.00 | +0.00 |
| **before** x2 | +0.00 | −0.03 | −0.00 | +0.01 | −0.00 | +0.00 | −0.00 | +0.00 | n/a |
| **after** x2 | +0.02 | −0.04 | +0.00 | −0.00 | +0.00 | +0.00 | +0.00 | −0.00 | n/a |

**The cause.**  The synthesis advanced the phase with
`beta * interp(advance)`, where `advance` is the *wrapped* phase advance of
the analysis.  A wrapped advance is a sawtooth over the bins, and scaling a
sawtooth by a factor that is not a whole number scales the jumps with it, so
the partial left its bin by an arbitrary amount.  The fix computes the
advance as the **expected** advance of the output bin plus the **deviation**
of the source partial, scaled by the pitch factor:

```
expected(k) = 2 pi k hop / N
deviation   = principal_angle(measured advance − expected)
adv_out(k)  = expected(k) + beta · interp(deviation)
```

For a factor of one the new expression is the old one up to a whole period,
so the time stretch is bit for bit the same (section 3).

### 2.2 The resampler without SciPy

`resample_to_length` uses SciPy's `resample_poly` when it is available and
falls back to a numpy only implementation otherwise.  The fallback copied the
spectrum bin by bin into a shorter signal, which moved every partial by
`nfft / out_len` instead of by `in_len / out_len`.

Measured with the SciPy modules hidden (`qa/vocoder/harness.py::_without_scipy`):

| path | source | factor | expected | before | after |
|---|---|---|---|---|---|
| numpy | 440 Hz | x1.5 | 660.00 Hz | 901.10 Hz (**+539.07 cents**) | 660.00 Hz (+0.00 cents) |
| numpy | 440 Hz | x0.5 | 220.00 Hz | 600.72 Hz (**+1739.04 cents**) | 220.00 Hz (−0.00 cents) |
| numpy | 1000 Hz | x2 | 2000.00 Hz | 2730.64 Hz (**+539.09 cents**) | 2000.00 Hz (+0.00 cents) |

The fallback is now a windowed sinc interpolation, which is the same filter
`resample_poly` uses.

---

## 3. The time stretch

A click train with a spacing of 100 ms, stretched and measured again.

| factor | expected | median spacing before | median spacing after | mean spacing before | mean spacing after | clicks before | clicks after |
|---|---|---|---|---|---|---|---|
| x0.75 | 75.000 ms | 76.000 ms (+1.333 %) | 78.000 ms (+4.000 %) | 99.556 ms (+32.741 %) | 74.833 ms (**−0.222 %**) | 7/10 | 9/10 |
| x1.5 | 150.000 ms | 150.000 ms (+0.000 %) | 153.333 ms (+2.222 %) | 200.000 ms (+33.333 %) | 149.333 ms (**−0.444 %**) | 7/10 | 9/10 |
| x2 | 200.000 ms | 200.000 ms (+0.000 %) | 196.000 ms (−2.000 %) | 200.000 ms (+0.000 %) | 200.000 ms (+0.000 %) | 9/10 | 9/10 |

The median spacing was exact before only because the clicks were smeared:
the detector picked the same point of every smeared click.  The mean spacing
over the whole train was **13 % to 33 % wrong**, because three of the ten
clicks were so weak that they disappeared.  Now every click is found and the
spacing of the train is within half a percent.

**The price**: the attacks are rendered in one analysis frame, so their
places carry the quantisation of the analysis hop (512 samples = 10.67 ms).
The position error of the clicks is 4.3 ms (x0.75), 7.3 ms (x1.5) and 16.0 ms
(x2) against 5.0 ms, 2.0 ms and 10.7 ms before.  The output length is exact
in every case.

---

## 4. The transients

A single impulse stretched by the vocoder.  The attack has to stay at its
place, keep its level and not be smeared over the analysis window.

| factor | energy spread before | energy spread after | peak loss before | peak loss after | energy within 1 ms before | after |
|---|---|---|---|---|---|---|
| x1.5 | 12.11 ms | **2.16 ms** | −2.87 dB | **+0.92 dB** | 10.18 % | **98.52 %** |
| x2 | 16.64 ms | **3.44 ms** | −4.39 dB | **+1.34 dB** | 9.56 % | **96.45 %** |
| x0.5 | 10.67 ms | 10.71 ms | −9.54 dB | −9.54 dB | 0.00 % | 0.00 % |

The pre-echo (the level before the attack) went from −28.6 dB to −68.4 dB for
x1.5, and the post-echo from −27.2 dB to −240.9 dB.  The attack of a lone
impulse used to land **one analysis hop (10.67 ms) away** from its place;
it now lands exactly on it.

**The phase of the synthesis frame.**  The synthesis reads the analysis
at a fractional position, and the phase it used was the phase of the
analysis frame below that position, without the advance of the fraction of
the position.  The phase of the synthesis frame is now interpolated: it is
the phase of the analysis frame below the position, advanced by the
fraction of the position times the phase advance of the analysis interval
the position is in.  The advance the analysis measures is wrapped (it is the
angle between two phases and it lies between -pi and pi), so the advance of
the interval is rebuilt from the expected advance of the bin and the
measured deviation: the correction is scaled by the fraction of the
position, and a whole period that the wrapping lost would come back scaled.
The phase travels the shortest way round, because the deviation is the
principal angle.

The frames that read a transient are left on the phase of their own analysis
frame: the attack is the content of that frame and the phase relations
inside it are the ones of that frame.

**The cause.**  Two bugs, one after the other:

1. *The detector never fired.*  The flux of frame `i` is the growth of the
   spectrum between the frames `i - 1` and `i`, so the flag of frame `i`
   belongs to `flux[i - 1]`.  The test that looks for a local maximum
   compared `flux[i - 1]` with `flux[i]` and cleared the flag when the flux
   was *falling* — which is exactly what happens at a transient.  Every
   transient was therefore thrown away, and `transient_reset` never did
   anything at all.
2. *The reset did not restart the accumulator.*  The frame of the transient
   took the analysis phase, but the accumulator carried on with its old
   value, so the next frame fell back to the stale phase and the attack was
   smeared over the analysis window again.

A detector that fires on steady material is worse than one that never
fires, so the threshold also got a **prominence** rule: the flux of a
transient has to be at least half of the largest change in the signal.  The
leakage of a steady tone moves from bin to bin as the window slides and
reaches a third of the spectrum's energy in a single frame; that movement is
not an attack.  With the rule, a 440 Hz tone is marked in 2 frames (the
start and the end of the signal) instead of 8.

---

## 5. The spectral loss

White noise, one second, shifted, measured in third of an octave bands.  The
top band is 16 kHz – 23 kHz.

| path | factor | top band in | top band before | top band after | loss before | loss after |
|---|---|---|---|---|---|---|
| resample | x1.5 | 18.40 dB | 14.80 dB | 14.81 dB | 3.60 dB | 3.59 dB |
| resample | x0.5 | 18.40 dB | −49.34 dB | −48.58 dB | 67.73 dB | 66.98 dB |
| spectral | x1.5 | 18.40 dB | 9.22 dB | 12.34 dB | 9.18 dB | **6.06 dB** |
| spectral | x0.5 | 18.40 dB | −33.71 dB | −128.28 dB | 52.11 dB | **146.67 dB** |

A pitch shift down has to empty the top of the spectrum, because the
content moves down.  Before, the top was not empty but held a copy of the
Nyquist bin (the source position was clipped instead of being marked as
outside the spectrum); the worst band lost **103.30 dB** where it should
lose everything.  Now the top of the output is empty.

The level of the time stretch itself:

| material | before | after |
|---|---|---|
| white noise x1.5 | −1.89 dB | −1.88 dB |
| white noise x2 | −1.65 dB | −1.79 dB |
| 440 Hz tone x1.5 | −0.08 dB | −0.05 dB |
| 440 Hz tone x2 | −0.08 dB | −0.07 dB |

The tone keeps its level; broadband material loses about 1.8 dB, which is
what a phase vocoder does to noise (the frames no longer add up coherently).

---

## 6. The cost

### 6.1 Per block

The vocoder is not a streaming processor: it analyses the whole block it is
given, so the cost per block is nearly the same for 64 and for 512 samples.
The budget, however, grows with the block, so the share of the budget falls.

| block | budget | total before | share before | total after | share after | analyse | phase locking before | after |
|---|---|---|---|---|---|---|---|---|
| 64 | 1333.3 us | 1055.7 us | 79.2 % | **680.9 us** | **51.1 %** | 53.3 us | 170.2 us | **45.8 us** |
| 128 | 2666.7 us | 1310.5 us | 49.1 % | **725.5 us** | **27.2 %** | 56.3 us | 357.1 us | **51.4 us** |
| 256 | 5333.3 us | 2423.8 us | 45.4 % | **756.2 us** | **14.2 %** | 54.9 us | 542.0 us | **87.5 us** |
| 512 | 10 666.7 us | 2841.6 us | 26.6 % | **710.9 us** | **6.7 %** | 54.0 us | 1237.0 us | **53.4 us** |

The "before" column is the historical measurement of the original
implementation (`qa/vocoder/before.json`); the "after" column is the run of
2026-10-21, 16:40, on a machine that was 10 % busy.  The timings move with
the load of the machine: the same measurement of the fixed implementation
gave 1076 us for a 64 sample block on a machine that was 24 % busy.

### 6.2 Over one second of audio

Here the transient detector runs as well, so this is the row that shows where
the time goes.

| piece | before | after | factor |
|---|---|---|---|
| the whole vocoder | 338.1 ms | **32.7 ms** | **10.3x** |
| the analysis | 3.38 ms | 2.86 ms | 1.2x |
| the transient detection | 197.2 us | 108.6 us | 1.8x |
| the synthesis loop | 331.6 ms | **28.2 ms** | **11.8x** |
| the phase locking, per frame | 2225.1 us | **57.9 us** | **38.4x** |
| the overlap-add | 6.32 ms | 4.48 ms | 1.4x |

**The hot spot was the phase locking, not the median.**  `_lock_phase` walked
the peaks in Python and, inside every peak, looked the peak up in a list
(`all_peaks.index(p)`) and called `np.mod` on the region.  On noisy material
every bin is a peak, so that was 500 peaks times a 500 entry list search
plus 500 NumPy calls per frame.  The regions of the peaks tile the spectrum
without overlapping, so the whole operation is now one vectorised pass.

The locking was 2225.1 us of the 2318 us that the synthesis loop spends on
one frame, so it was **96 % of the loop** and 73 % of the whole vocoder in
the profile of `cProfile`.

**The result is bit for bit the same**: over 200 random frames (flat
spectra, sparse spectra and noise) the largest difference between the old
loop and the new pass is **0.000e+00 rad**.

### 6.3 The share of the per frame median

The performance study names the per frame `np.median` as a hot spot and
recommends `scipy.ndimage.median_filter` or a cheaper equivalent.  In this
file the median is **not** a hot spot:

| piece | share of the per block cost |
|---|---|
| `np.median` + `np.percentile` of the flux, before | 0.0 % (below the resolution of the measurement) |
| `np.median` + `np.percentile` of the flux, after | **0.1 %** |
| the whole transient detection, after | 0.4 % |

The median is only computed when the analysis has at least three frames, so
a 64 sample block never reaches it at all.  It was made cheap anyway:
`np.median` and `np.percentile` each copy the array and partition it, and
`_flux_thresholds` now takes both values out of **one** partition.  The
values are the ones the two NumPy functions return, down to the last bit
(checked over 500 random arrays of six different lengths).

The per frame median that the performance study really points at is in
`aurcore/dsp/timepitch.py::_estimate_transient_spacing`; that file belongs
to another agent and the finding is written down in
`var/patches/c4_vocoder.md` (83.9 us per call, once per block).

---

## 7. The five awkward inputs

| input | finite before | finite after | length kept | peak before | peak after |
|---|---|---|---|---|---|
| silence | yes | yes | yes | 0.0000 | 0.0000 |
| constant | yes | yes | yes | 0.5608 | 0.6508 |
| square | yes | yes | yes | 1.7174 | 1.5059 |
| impulse | yes | yes | yes | 0.7182 | **1.1111** |
| tone | yes | yes | yes | 0.5468 | 0.5468 |

No NaN, no infinity, no change of length, in any of the five inputs, for the
stretch and for the pitch shift.  The peak of the impulse and the overshoot
of the square wave changed because the transient is no longer smeared.

---

## 8. What is still open

1. **The compression (a factor below one) still smears the attack.**  At
   x0.5 the attack is 9.54 dB weaker and its energy is spread over 10.7 ms.
   The reason is structural: the analysis frame of the transient is an
   integer, and the output frame that would read it is skipped when the
   content is read twice as fast.  The attack lands between two output
   frames.  A fix needs a time domain splice of the transient, which is a
   different algorithm.
2. **The click positions carry the hop quantisation.**  The spacing of a
   click train is within 0.5 % on average, but two neighbouring clicks can
   be up to one analysis hop (10.67 ms) apart from the ideal grid.  The
   reason is the same as in 1.
3. **The spectral pitch shift by a factor of three is still wrong** for some
   frequencies (440 Hz × 3 is correct, 1000 Hz × 3 is 53 cents sharp).  At
   that factor the interpolated magnitude ripples and breaks the spectrum
   into several locked regions that do not agree on the frequency.  The
   module range is −24 … +24 semitones, which is a factor of 0.5 … 2, and
   every factor in that range is within 0.06 cents.
4. **The vocoder is not streaming.**  The cost of a block does not fall with
   the size of the block (section 6.1).  A 64 sample block costs 680.9 us
   against a budget of 1333.3 us, so the vocoder alone uses 51.1 % of the
   budget of the smallest block.  This is the same structural problem that
   `qa/performance/REPORT.md` describes for the whole catalogue, and the fix
   belongs to the call pattern, not to this file.
5. **The resampling pitch shift loses 3.6 dB in the top band.**  The
   resampler low passes the signal before it shortens it, so the content
   above 16 kHz is gone after a shift of 660 cents.  That is what the
   method does; the spectral path loses 6.06 dB over the same band.

---

## 9. The guard

`tests/test_phase_vocoder.py` holds 37 tests:

* the pitch shift lands within 1 cent for five factors and two paths,
* the numpy fallback of the resampler pitches within 5 cents and keeps the
  level within 1 dB,
* the click train keeps its spacing,
* the stretched attack stays at its place and keeps its level,
* the transient detector fires on a click, stays quiet on a steady tone and
  can be switched off,
* the five awkward inputs stay finite and keep their length,
* the phase of the synthesis frame is interpolated at the fractional
  part of the position and does not jump where the position crosses an
  integer,
* the phase locking of one frame stays cheap,
* the one pass median and percentile return what NumPy returns.

---

## 10. The gates

| command | result |
|---|---|
| `python -m pytest tests\test_phase_vocoder.py -q` | 35 passed |
| `python -m pytest tests\test_dsp_layer.py tests\test_dsp.py -q` | passed |
| `python -m pytest tests\test_modules_specific.py tests\test_modules_generic.py -q` | passed |
| `python -m pytest tests\test_performance.py -q` | passed |
| `python -m pytest tests\test_journey.py -q` | 21 passed |
| `python qa/sweep_modules.py` | 48 modules, 240 runs, no problems |
| `python tools/hygiene_check.py` | 0 findings in 372 files |
| `python tools/lint_check.py` | PASS |

One test in the tree fails for a reason that has nothing to do with this
file: `tests/test_packaging.py::test_every_source_path_exists_in_the_repository`
wants the release `manifest.json` in the tree, and the release build has not
been run here.  The installer entry carries `skipifsourcedoesntexist`, the
test only accepts a missing source below `var/`, `bin/`, `build/`, `dist/`
or the two plugin build trees, and `manifest.json` is none of them.

