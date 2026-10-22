# Auralis — why the modules are slow (the analysis behind the budget)

> **This is the analysis that was in `qa/performance/REPORT.md` before
> the guard was built.**  It is kept here because it explains *why* the
> modules cost what they cost and what to do about it.  The guard itself
> (the budgets, the measurement method, the chains and the allocation
> numbers) is in `qa/performance/REPORT.md`.
>
> The numbers in this document were measured with the old harness (a
> median wall-clock time on a machine that was 100 % busy) and are
> therefore about a factor of two worse than the calibrated numbers of
> the current report.  The *cause* is unchanged; the code of the modules
> has changed a lot since (see the current report).

Everything below was measured with the harness in this directory
(`qa/performance/harness.py`, `qa/performance/run_perf.py`).  No module or
DSP file was touched; the optimisations in section 7 are **proposals**.

---

## 1. Method

| item | value |
|---|---|
| sample rate | 48 000 Hz (the plug-in default) |
| channels | 2 (stereo) |
| block sizes | 64, 128, 256, 512 samples |
| budget per block | `block / 48000` seconds → 1333.3 / 2666.7 / 5333.3 / 10 666.7 us |
| timed blocks | 15 per block size, after 3 untimed warm-up blocks |
| runs | 3 per module, the fastest run of every block size is kept |
| input | deterministic speech-like signal (noise bed + harmonic stack + 2 clicks), −12 dBFS |
| input copies | every call gets a fresh copy, created **outside** the timed region |
| memory | one child process per module, `GetProcessMemoryInfo` peak working set |
| allocations | NumPy allocation counter (entry points wrapped) + `tracemalloc` peak churn |

Reference machine: AMD Ryzen 7 7840HS (8c/16t), Windows 11, Python 3.15.0,
NumPy 2.5.4, SciPy 1.18.1.  **The machine was 100 % busy with other work
during the sweep**, so the absolute numbers below are roughly a factor of
two worse than an idle machine.  That makes them a conservative estimate,
which is the right side to err on for a shipping requirement.

**Calibration** (machine independent yardstick): one 1024 point `rfft`
costs **13.3 – 15.6 us** on this machine.  The 64 sample budget of
1333 us is therefore worth about **85 – 100 transforms**.

Reproduce with:

```
python qa/performance/run_perf.py --all --rss --runs 3 --json var/perf/full.json
python qa/performance/run_perf.py --module AUR_SPECTRALDENOISE --profile
```

---

## 2. The measurement table

`us` is the median wall-clock time of one `process()` call, `%` is that
time as a share of the block budget (`⚠` = over budget).  `arrays/blk` is
the number of NumPy arrays the module creates inside `process()`, `churn
KiB/blk` is the peak traced memory of one block (the transient allocation
churn).  `RSS Δ` is what the module adds on top of a process that only
imports the core (103.6 MiB baseline).

| module | name | 64 smp us | 64 smp % | 128 smp us | 128 smp % | 256 smp us | 256 smp % | 512 smp us | 512 smp % | arrays/blk | churn KiB/blk | RSS MiB | RSS Δ MiB |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `AUR_AMBIENCEMATCH` | Ambience Match | 4854 ⚠ | 364.1% | 4717 ⚠ | 176.9% | 4593 | 86.1% | 4922 | 46.2% | 193 | 520 | 106.1 | +2.5 |
| `AUR_AZIMUTH` | Azimuth | 102 | 7.7% | 116 | 4.3% | 111 | 2.1% | 123 | 1.2% | 8 | 302 | 103.7 | +0.0 |
| `AUR_BREATH` | Breath Control | 537 | 40.3% | 563 | 21.1% | 600 | 11.2% | 770 | 7.2% | 46 | 325 | 104.0 | +0.3 |
| `AUR_CENTEREXTRACT` | Center Extract | 10 | 0.8% | 14 | 0.5% | 466 | 8.7% | 580 | 5.4% | 41 | 450 | 104.0 | +0.4 |
| `AUR_DEBLEED` | De-Bleed | 601 | 45.1% | 604 | 22.7% | 662 | 12.4% | 648 | 6.1% | 35 | 322 | 104.0 | +0.4 |
| `AUR_DECLICK` | De-Click | 1128 | 84.6% | 1068 | 40.1% | 1020 | 19.1% | 1190 | 11.2% | 160 | 482 | 104.2 | +0.5 |
| `AUR_DECLIP` | De-Clip | 16 | 1.2% | 16 | 0.6% | 17 | 0.3% | 20 | 0.2% | 1 | 289 | 103.6 | −0.0 |
| `AUR_DECRACKLE` | De-Crackle | 1683 ⚠ | 126.2% | 1400 | 52.5% | 1600 | 30.0% | 1848 | 17.3% | 172 | 506 | 104.3 | +0.7 |
| `AUR_DEESS` | De-Ess | 530 | 39.7% | 535 | 20.1% | 411 | 7.7% | 623 | 5.8% | 42 | 320 | 104.1 | +0.5 |
| `AUR_DEHUM` | De-Hum | 334 | 25.1% | 348 | 13.1% | 448 | 8.4% | 518 | 4.9% | 42 | 315 | 104.1 | +0.5 |
| `AUR_DEPLOSIVE` | De-Plosive | 371 | 27.8% | 376 | 14.1% | 437 | 8.2% | 521 | 4.9% | 32 | 320 | 103.7 | +0.1 |
| `AUR_DEREVERB` | De-Reverb | 1557 ⚠ | 116.8% | 1496 | 56.1% | 1568 | 29.4% | 1659 | 15.6% | 108 | 384 | 103.8 | +0.2 |
| `AUR_DERUSTLE` | De-Rustle | 1305 | 97.9% | 1255 | 47.1% | 1214 | 22.8% | 1327 | 12.4% | 79 | 367 | 104.6 | +1.0 |
| `AUR_DEWIND` | De-Wind | 597 | 44.8% | 609 | 22.8% | 666 | 12.5% | 817 | 7.7% | 44 | 318 | 104.1 | +0.4 |
| `AUR_DECONSTRUCT` | Deconstruct | 4968 ⚠ | 372.6% | 5081 ⚠ | 190.5% | 5892 ⚠ | 110.5% | 7001 | 65.6% | 197 | 570 | 104.4 | +0.8 |
| `AUR_DIALOGUEISOLATE` | Dialogue Isolate | 1887 ⚠ | 141.5% | 1852 | 69.5% | 2284 | 42.8% | 2624 | 24.6% | 102 | 383 | 104.9 | +1.3 |
| `AUR_GUITARDENOISE` | Guitar De-Noise | 2174 ⚠ | 163.1% | 2710 ⚠ | 101.6% | 3027 | 56.7% | 3693 | 34.6% | 185 | 545 | 104.3 | +0.7 |
| `AUR_MOUTHDECLICK` | Mouth De-Click | 1819 ⚠ | 136.4% | 1418 | 53.2% | 1305 | 24.5% | 1524 | 14.3% | 172 | 507 | 104.2 | +0.6 |
| `AUR_MUSICREBALANCE` | Music Rebalance | 2300 ⚠ | 172.5% | 2404 | 90.1% | 2766 | 51.9% | 2887 | 27.1% | 97 | 384 | 104.9 | +1.3 |
| `AUR_REPAIRASSISTANT` | Repair Assistant | 3372 ⚠ | 252.9% | 3376 ⚠ | 126.6% | 3179 | 59.6% | 4395 | 41.2% | 125 | 417 | 105.2 | +1.5 |
| `AUR_SPECTRALDENOISE` | Spectral De-Noise | 31161 ⚠ | 2337.1% | 34374 ⚠ | 1289.0% | 31411 ⚠ | 589.0% | 35204 ⚠ | 330.0% | 10082 | 14465 | 105.1 | +1.4 |
| `AUR_SPECTRALEDITOR` | Spectral Editor | 1 | 0.1% | 1 | 0.0% | 1 | 0.0% | 2 | 0.0% | 1 | 281 | 103.9 | +0.2 |
| `AUR_SPECTRALRECOVERY` | Spectral Recovery | 863 | 64.7% | 859 | 32.2% | 890 | 16.7% | 872 | 8.2% | 31 | 291 | 104.1 | +0.5 |
| `AUR_SPECTRALREPAIR` | Spectral Repair | 0 | 0.0% | 0 | 0.0% | 0 | 0.0% | 0 | 0.0% | 1 | 278 | 103.6 | −0.1 |
| `AUR_VOICEDENOISE` | Voice De-Noise | 4419 ⚠ | 331.4% | 5254 ⚠ | 197.0% | 5336 ⚠ | 100.1% | 7300 | 68.4% | 611 | 1253 | 104.5 | +0.8 |
| `AUR_WOWFLUTTER` | Wow & Flutter | 2157 ⚠ | 161.7% | 2252 | 84.4% | 2599 | 48.7% | 3383 | 31.7% | 91 | 384 | 104.2 | +0.6 |
| `AUR_DITHER` | Dither | 30 | 2.3% | 34 | 1.3% | 39 | 0.7% | 48 | 0.5% | 11 | 281 | 103.7 | +0.1 |
| `AUR_EQ` | EQ | 46 | 3.4% | 50 | 1.9% | 59 | 1.1% | 67 | 0.6% | 17 | 286 | 103.8 | +0.1 |
| `AUR_EQMATCH` | EQ Match | 0 | 0.0% | 0 | 0.0% | 0 | 0.0% | 0 | 0.0% | 1 | 280 | 104.2 | +0.5 |
| `AUR_FADE` | Fade | 16 | 1.2% | 20 | 0.7% | 24 | 0.5% | 35 | 0.3% | 5 | 281 | 104.2 | +0.5 |
| `AUR_GAIN` | Gain | 0 | 0.0% | 0 | 0.0% | 0 | 0.0% | 0 | 0.0% | 1 | 278 | 103.8 | +0.2 |
| `AUR_INTERPOLATE` | Interpolate | 998 | 74.9% | 979 | 36.7% | 1159 | 21.7% | 1285 | 12.0% | 160 | 472 | 104.2 | +0.6 |
| `AUR_LEVELER` | Leveler | 124 | 9.3% | 125 | 4.7% | 167 | 3.1% | 215 | 2.0% | 30 | 293 | 104.3 | +0.7 |
| `AUR_LOUDNESS` | Loudness Control | 169 | 12.7% | 179 | 6.7% | 200 | 3.7% | 300 | 2.8% | 45 | 717 | 104.1 | +0.5 |
| `AUR_LOUDNESSOPT` | Loudness Optimize | 37 | 2.8% | 62 | 2.3% | 140 | 2.6% | 297 | 2.8% | 63 | 288 | 103.8 | +0.2 |
| `AUR_MIXING` | Mixing | 15 | 1.1% | 15 | 0.5% | 16 | 0.3% | 19 | 0.2% | 5 | 281 | 103.8 | +0.1 |
| `AUR_NORMALIZE` | Normalize | 6 | 0.5% | 7 | 0.3% | 8 | 0.1% | 8 | 0.1% | 3 | 278 | 103.8 | +0.2 |
| `AUR_PHASE` | Phase | 2 | 0.1% | 2 | 0.1% | 2 | 0.0% | 2 | 0.0% | 1 | 279 | 104.0 | +0.4 |
| `AUR_RESAMPLE` | Resample | 19973 ⚠ | 1498.0% | 23212 ⚠ | 870.4% | 26981 ⚠ | 505.9% | 33892 ⚠ | 317.7% | 1061 | 2294 | 104.6 | +1.0 |
| `AUR_SIGNALGEN` | Signal Generator | 21 | 1.6% | 24 | 0.9% | 29 | 0.5% | 36 | 0.3% | 5 | 278 | 103.8 | +0.2 |
| `AUR_STREAMPREVIEW` | Streaming Preview | 75 | 5.6% | 86 | 3.2% | 99 | 1.9% | 129 | 1.2% | 27 | 705 | 104.4 | +0.8 |
| `AUR_TIMEPITCH` | Time & Pitch | 3 | 0.2% | 3 | 0.1% | 2 | 0.0% | 3 | 0.0% | 1 | 279 | 103.9 | +0.2 |
| `AUR_VARIPITCH` | Variable Pitch | 1 | 0.1% | 1 | 0.0% | 1 | 0.0% | 1 | 0.0% | 1 | 279 | 103.7 | +0.1 |
| `AUR_VARTIME` | Variable Time | 1 | 0.1% | 1 | 0.0% | 1 | 0.0% | 1 | 0.0% | 1 | 279 | 104.0 | +0.3 |
| `AUR_CLIPCLASSIFIER` | Clip Classifier | 9 | 0.7% | 10 | 0.4% | 12 | 0.2% | 13 | 0.1% | 1 | 279 | 111.3 | +7.6 |
| `AUR_FINDSIMILAR` | Find Similar | 0 | 0.0% | 0 | 0.0% | 0 | 0.0% | 0 | 0.0% | 0 | 278 | 103.8 | +0.2 |
| `AUR_CONNECT` | Connect | 0 | 0.0% | 0 | 0.0% | 0 | 0.0% | 0 | 0.0% | 1 | 279 | 103.7 | +0.1 |
| `AUR_MONITOR` | Monitor | 103 | 7.7% | 114 | 4.3% | 156 | 2.9% | 223 | 2.1% | 19 | 298 | 104.1 | +0.4 |

### Reading the table

* **The cost per block is flat.**  De-Click needs 1128 us for 64 samples and
  1190 us for 512 samples — eight times the audio, the same work.  The
  budget share therefore falls from 84.6 % to 11.2 % while the module does
  exactly the same amount of work.  Every module in the table behaves this
  way; the modules that are cheap at 64 samples and expensive at 512
  (Center Extract, Loudness Optimize) are the only ones whose cost is
  actually proportional to the block.
* **13 of 48 modules are over the 64 sample budget**, 7 of them are over
  it at 128 samples, 4 at 256 samples and 2 (Spectral De-Noise, Resample)
  are over it at every block size.
* **The heavy modules are heavy because of the number of analysis frames
  they run, not because of the maths** — see sections 6 and 7.

---

## 3. The worst offenders

| rank | module | 64 smp us | budget share | why it is slow |
|---|---|---|---|---|
| 1 | `AUR_SPECTRALDENOISE` | 31 161 | 2337 % | 18 280 `np.interp` calls and 9 020 `np.convolve` calls per 5 blocks (section 7.1) |
| 2 | `AUR_RESAMPLE` | 19 973 | 1498 % | a 96 tap Python loop that evaluates a Kaiser window (96 `np.i0` calls) per tap per block (section 7.2) |
| 3 | `AUR_DECONSTRUCT` | 4968 | 373 % | the whole stem analysis chain: 14 transforms, 16 median filters and 12 complex conversions per block (section 7.4) |
| 4 | `AUR_AMBIENCEMATCH` | 4854 | 364 % | stem analysis: an STFT round trip, `_synthesise` alone is half of the module (section 7.4) |
| 5 | `AUR_VOICEDENOISE` | 4419 | 331 % | `_band_levels`: a Python loop over bands plus `np.median` per frame (section 7.3) |
| 6 | `AUR_REPAIRASSISTANT` | 3372 | 253 % | `_analyse` — the analysis chain is 97 % of the module (section 7.4) |
| 7 | `AUR_MUSICREBALANCE` | 2300 | 173 % | `_analyse_stems` — 96 % of the module, plus a FIR low pass (section 7.4) |
| 8 | `AUR_GUITARDENOISE` | 2174 | 163 % | the analysis chain: STFT round trip, median filter, level tracking (section 7.4) |
| 9 | `AUR_WOWFLUTTER` | 2157 | 162 % | `_interpolate` is 79 % of the module and calls `np.sinc` 160 times per block (section 7.5) |
| 10 | `AUR_DIALOGUEISOLATE` | 1887 | 142 % | `_analyse_stems` — 95 % of the module (section 7.4) |
| 11 | `AUR_MOUTHDECLICK` | 1819 | 136 % | De-Click engine: 9 transforms of 1024 per block, detection score 39 % (section 7.7) |
| 12 | `AUR_DECRACKLE` | 1683 | 126 % | De-Click engine, 9 transforms of 1024 per block (section 7.7) |
| 13 | `AUR_DEREVERB` | 1557 | 117 % | the whole analysis chain: STFT, band ratios, overlap-add (section 7.6) |

Modules that are *close* to the line and would cross it on a busier
machine: `AUR_DERUSTLE` (97.9 %), `AUR_DECLICK` (84.6 %),
`AUR_INTERPOLATE` (74.9 %), `AUR_SPECTRALRECOVERY` (64.7 %).

---

## 4. The chain

| chain | 64 smp us | 64 smp % | 128 smp us | 128 smp % | 256 smp us | 256 smp % | 512 smp us | 512 smp % |
|---|---|---|---|---|---|---|---|---|
| De-Hum → De-Click → De-Ess → De-Plosive → Gain | 2888 ⚠ | 216.6 % | 2877 ⚠ | 107.9 % | 3147 | 59.0 % | 3199 | 30.0 % |
| De-Hum → Spectral De-Noise → De-Click → De-Ess → Gain | 30 415 ⚠ | 2281.1 % | 31 209 ⚠ | 1170.3 % | 30 970 ⚠ | 580.7 % | 42 954 ⚠ | 402.7 % |

The first chain is the realistic dialogue cleanup chain (mains hum, clicks,
sibilance, plosives, level).  **It needs 2.2x the 64 sample budget and
1.1x the 128 sample budget**, and it fits into the 256 sample budget
(59 %) and comfortably into the 512 sample budget (30 %).

The second chain adds the flagship Spectral De-Noise module and is over
the budget at every block size.

Note that the chain cost is again almost independent of the block size
(2888 us at 64 samples, 3199 us at 512 samples): the chain inherits the
fixed per-block analysis cost of every module in it.

---

## 5. Memory and allocations

### Peak resident memory

A process that only imports the core (NumPy + `aurcore`) already sits at
**103.6 MiB** — that is the interpreter, not the DSP.  The modules add:

| module | RSS Δ | note |
|---|---|---|
| `AUR_CLIPCLASSIFIER` | +7.6 MiB | runs a second analysis engine at 22.05 kHz (`_Analyzer(22050)`) with its own STFT |
| `AUR_REPAIRASSISTANT` | +1.5 MiB | learned profile buffers |
| `AUR_SPECTRALDENOISE` | +1.4 MiB | per-bin noise profiles |
| `AUR_DIALOGUEISOLATE`, `AUR_MUSICREBALANCE` | +1.3 MiB | stem buffers |
| everything else | ≤ +1.0 MiB | filter and envelope state |

No module leaks: the resident set does not grow over a run of blocks, and
the retained memory after the run is ≤ 285 KB (Spectral De-Noise, the
learned profiles).

### Allocation inside `process()`

**Every one of the 48 modules allocates inside `process()`.**  CONTRACT §4
says a realtime-safe `process` should not allocate in the steady state.
The numbers:

* 0 – 197 arrays per block for most modules, 611 for Voice De-Noise,
  1061 for Resample and **10 082 for Spectral De-Noise**.
* transient churn per block: 280 – 720 KiB for the normal modules,
  1.2 MiB for Voice De-Noise, 2.3 MiB for Resample and **14.1 MiB for
  Spectral De-Noise**.

At 750 blocks per second (64 sample blocks) the normal modules churn
~200 MiB of scratch per second of audio.  That is not a leak, but it is
allocator pressure and it is a large part of the measured time: for the
cheap modules (Gain: 0.4 us) the allocation *is* the cost.

---

## 6. Root cause: the fixed cost per block

The dominant finding of this report is not a slow module, it is a slow
*call pattern*.

`aurcore/modules/de_click.py::Stft.analyse` pads every block to a full
analysis window and transforms **every** frame of the padded block:

```python
xp = np.concatenate((np.zeros(self.pad), x, np.zeros(self.pad + self.n_fft)))
frames = np.lib.stride_tricks.sliding_window_view(xp, self.n_fft)[:: self.hop]
return np.fft.rfft(frames * self.window, axis=1)
```

With `n_fft = 1024`, `hop = 256` and `pad = 1024` that is:

| block | transforms of 1024 per block |
|---|---|
| 64 | 9 |
| 128 | 9 |
| 256 | 10 |
| 512 | 11 |

The shared engine `aurcore/dsp/stft.py` is better (2 transforms per block
for 64…256 samples) but still flat, because `center=True` always pads to
a full window and the frame count is `ceil(n / hop) + 1`.

A **streaming** STFT — keep the window in a ring buffer and transform only
the frames that overlap the new samples — would need `64 / 256 = 0.25`
transforms per 64 sample block, i.e. **one transform every four blocks**
instead of nine per block.  That is a factor of 36 less transform work for
the same audio, and it is the single change that moves the whole catalogue
from "over budget at 64 samples" to "comfortably inside the budget".

It also explains the shape of the profiles: because the same number of
frames is analysed for a 64 sample block and for a 512 sample block, the
cost of a module does not depend on how much audio it is given, and the
*share* of the budget that a module uses is decided by the block size the
host happens to use — not by the module.

Everything else in this report is secondary to that.

---

## 7. Proposed optimisations

None of these are applied — the modules and the DSP layer belong to other
agents.  The hot spots were located with `cProfile`
(`python qa/performance/run_perf.py --module CODE --profile`).

> **How the profiles were taken.**  `cProfile` measures wall-clock time, so
> on a saturated machine a single unlucky call can look like the hot spot.
> Every profile below is therefore the **fastest of seven runs** of the
> module (the run with the smallest total time), which removes the
> descheduling artefacts.  The numbers are the totals for **5 blocks**.

### 7.0 The structural fix: stream the analysis (all modules)

The fix of section 6 — **stop re-analysing the whole padded block** — is
the first item of the list because it applies to every module and because
it explains the flat profiles: there is no single hot spot, the cost is
spread over the STFT round trip (transform → magnitude/phase → filter →
complex → inverse transform → overlap-add) and over the many small NumPy
calls in between.  Cutting the number of frames cuts all of it at once.

**Expected effect**: 2–4x on every spectral module, and the difference
between "over budget" and "comfortably inside the budget" at 64 samples.

### 7.1 Spectral De-Noise (`AUR_SPECTRALDENOISE`) — the flagship module

Fastest profile, 5 blocks, 235.9 ms total:

| function | calls | time | share |
|---|---|---|---|
| `_resample_gain_2d` (→ `np.interp`) | 40 | 88 ms | 37 % |
| `np.apply_along_axis` (→ `np.convolve`) | 20 | 71 ms | 30 % |
| `np.interp` (the calls themselves) | 18 280 | 23 ms | 10 % |
| `np.convolve` (the calls themselves) | 9 020 | 14 ms | 6 % |

`_resample_gain_2d` calls `np.interp` **once per bin** (365 bins) and then
**once per frame** (2–20 frames) — 18 280 `np.interp` calls for 5 blocks.
`_moving_average` calls `np.apply_along_axis` with a `np.convolve` lambda —
9 020 lambda invocations.

**Proposal**

* replace `_resample_gain_2d` with one 2-D interpolation: the source and
  destination grids are both regular, so a single
  `scipy.interpolate.RegularGridInterpolator` (or a hand written bilinear
  interpolation on the (frames, bins) array) replaces 18 280 calls with one.
* replace `_moving_average` with a cumulative-sum moving average
  (`np.cumsum` + two slices) or `scipy.ndimage.uniform_filter1d` — same
  result, one pass over the array.

**Expected effect**: 67 % of the module's time disappears → 31.2 ms →
~10 ms, i.e. **3x**.  The module would then fit into the 512 sample
budget (10.7 ms); the 256 sample budget (5333 us) still needs the
streaming fix of section 7.0.

### 7.2 Resample (`AUR_RESAMPLE`)

Fastest profile, 5 blocks, 112.8 ms total: `SincResampler.resample` runs a
**96 iteration Python loop** (`for offset in range(-a+1, a+1)`) and each
iteration evaluates the whole interpolation kernel from scratch:
`_weight` (95 ms cumulative) → `_symmetric_kernel` → `_kaiser` (68 ms) →
`np.i0` (35 ms, 480 calls = 96 per block).  The cost is numpy call
overhead on 64 element arrays, not arithmetic.

**Proposal**: build the kernel **once**, when the parameters change, as a
polyphase table, and apply it with one gather and one matrix product.
`aurcore/dsp/resample.py` already contains such a polyphase
`Resampler`; the module could use it instead of its own `SincResampler`
(that is a sound-design decision for the DSP owner, not a performance one).

**Expected effect — measured with a prototype** (bank of 512/4096/16384
phase entries, one `np.einsum` per block):

| block | current | precomputed bank | speed-up | max error |
|---|---|---|---|---|
| 64 | 28 484 us | 252 us | **113x** | 7.0e-04 (−63 dB) |
| 64 | 28 484 us | 232 us (4096 phases) | **123x** | 7.5e-05 (−83 dB) |
| 64 | 28 484 us | 253 us (16 384 phases) | **113x** | 1.6e-05 (−96 dB) |

The prototype lives in the scratch area, not in the repository.  With a
16 384 entry table the error is below −96 dB and the module goes from
1498 % of the budget to **19 %**.

### 7.3 Voice De-Noise (`AUR_VOICEDENOISE`)

Fastest profile, 5 blocks, 30.9 ms total: `_band_levels` alone is
**22 ms (71 %)**.  It loops over the bands in Python and computes
`np.sum(power[:, sel], axis=1)` per band, and `_broadband_floor` computes
a `np.median` per frame (2 200 ufunc reductions for 5 blocks).

**Proposal**: build a `(bins, BAND_COUNT)` 0/1 selector matrix once and
replace the per-band sums with one matrix product
(`power @ selector`); replace the per-frame `np.median` with
`scipy.ndimage.median_filter` along the frequency axis (the module already
uses SciPy elsewhere).

**Expected effect**: 2–4x on the module (4419 us → 1100–2200 us), which
puts it inside the 128 sample budget and close to the 64 sample budget.

### 7.4 Stem analysers — Deconstruct, Ambience Match, Repair Assistant, Music Rebalance, Dialogue Isolate, Guitar De-Noise, De-Rustle, De-Reverb

These modules share the same pattern: an `_analyse_stems` / `_analyse`
helper that produces a spectrogram and then filters it.  Their profiles
are flat, and the same entries appear again and again:

| entry | what it is |
|---|---|
| `scipy.ndimage._nd_image.rank_filter` | the median filter of `broadband_floor` / `_median_filter` |
| `numpy.ndarray.partition` | the `np.median` inside the same filters |
| `aurcore/dsp/fft.py:to_complex` | the magnitude/phase → complex conversion |
| `numpy.fft._raw_fft` | the transforms themselves |
| `aurcore/dsp/stft.py:_istft` | the overlap-add |

Deconstruct for example spends 5.5 ms per block on 8 transforms, 8 median
filters and 6 complex conversions — none of them expensive on its own, all
of them repeated because the block is analysed from scratch every time.

**Proposal**

* the streaming STFT of section 7.0 — this is the main win here;
* replace the per-frame `np.median` loop in `broadband_floor` with
  `scipy.ndimage.median_filter(arr, size=width, mode="nearest")` along the
  frequency axis (the same trick the Spectral De-Noise module already
  uses, and it is an order of magnitude faster);
* replace `np.percentile(chunk, 10.0, axis=0)` (De-Rustle, Ambience Match)
  with `np.partition` on the first axis, or with a running percentile that
  only updates every N frames;
* hoist the repeated `np.dtype(...)` lookups and the `to_complex` /
  `np.angle` pairs out of the per-frame loops.

**Expected effect**: 2–4x on these modules (Deconstruct 4968 → 1500–2500
us, Ambience Match 4854 → 1500–2500 us, Repair Assistant 3372 → 1200–1700
us, Music Rebalance 2300 → 800–1200 us, De-Rustle 1305 → 400–700 us,
De-Reverb 1557 → 500–900 us, Guitar De-Noise 2174 → 700–1200 us).

### 7.5 Wow & Flutter (`AUR_WOWFLUTTER`)

`_interpolate` is **79 % of the module** (10 ms of 12.6 ms for 5 blocks)
and it evaluates `np.sinc` 160 times per block (2 ms).  The interpolation
kernel does not change from block to block.

**Proposal**: evaluate the kernel once (it is a function of the fractional
read position only) and reuse it — the same idea as 7.2, on the
time-warp table.

**Expected effect**: 2157 → 500–800 us.

### 7.6 The De-Click family (`AUR_DECLICK`, `AUR_DECRACKLE`, `AUR_MOUTHDECLICK`, `AUR_INTERPOLATE`)

Four modules share `TransientRepair`.  The local `Stft` of `de_click.py`
transforms 9 frames of 1024 per 64 sample block (section 6), and
`Stft.synthesise` overlap-adds them in a Python loop.  The detection
score (`_detection_score`) is **58 % of De-Click's time** (3 `np.diff`
calls plus 2 `moving_average` calls, each a `np.cumsum` + `np.concatenate`,
per channel).

**Proposal**: use the streaming STFT of `dsp/stft.py` (or make the local
one streaming), cache the window sum, and replace the three chained
`np.diff` calls with one convolution against the third difference kernel
(the three differences are a single FIR filter).

**Expected effect**: with the streaming fix alone the transforms per block
drop from 9 to 0.25 → De-Click 1128 → ~300–400 us at 64 samples, i.e.
from 85 % of the budget to 25 %.

### 7.7 Allocation-free steady state (all modules)

Every module allocates inside `process()`.  The cheapest way to remove it
is to allocate the working buffers (spectrogram, detector arrays, filter
lines) once in the constructor and reuse them; the module base class
already keeps state in `self`, so the pattern exists.

**Expected effect**: the cheap modules become *much* cheaper (Gain: 0.4 us
is pure allocation overhead) and the allocation pressure on the audio
thread disappears — which matters more for glitching than the raw
microseconds.

### 7.8 Product level recommendation (no code change)

If the plug-in host can be asked for a larger block, **256 samples is the
minimum that makes the catalogue usable** (5.3 ms budget): the dialogue
chain fits with 41 % headroom, and after 7.1–7.7 every module except
possibly Spectral De-Noise fits too.  At 128 samples only the light
modules fit.
