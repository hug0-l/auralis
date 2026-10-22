# The Spectral De-Noise study — what was measured, what was proposed, what landed

> This is the study that grew into the quality instrument of the project.  The
> instrument itself is documented in `qa/quality/REPORT.md`; this file keeps the
> study: the diagnosis of the module, the levers that were tried, the proposed
> change and what it measures.
>
> **Status:** the proposed change has been applied to
> `aurcore/modules/spectral_de_noise.py` by the owner of the module (the
> `Noise Reduction` default is now 15 dB).  `qa/quality/spectral_de_noise.patch`
> is the historical diff of that change; `python qa/quality/make_patch.py` says
> so instead of failing.

---

## 1. Method

### 1.1 The ground truth

The cases live in `qa/quality/ground_truth.py` (they used to live in this file):

| case | content | noise-only band | wanted partials |
|---|---|---|---|
| `tone_noise` | 1 kHz tone over a stationary white bed | 4–8 kHz | 1 kHz (SNR +43.8 dB) |
| `tone_hum` | 1 kHz tone over 50/100/150 Hz mains hum on a quiet bed | 4–8 kHz | 1 kHz (SNR +49.0 dB) |
| `voice_hiss` | harmonic voice-like stack (f0 = 120 Hz, 12 partials, slow envelope) under low-passed hiss | 6–10 kHz | 120/240/360/480 Hz |
| `music_mixture` | music-like chord (220/277/330/440 Hz) plus a plucked 660 Hz note over a bed | 8–12 kHz | 220…660 Hz |
| `bed_only` | stationary white bed, no wanted content | 1–16 kHz | — |
| `clipped` | 1 kHz + 1.5 kHz programme hard clipped at full scale | 2.5–8 kHz | 1 kHz, 1.5 kHz |
| `transient` | steady programme with four impulsive bursts | 2–16 kHz | 250 Hz, 1 kHz |

Metrics per case:

* **band reduction** — the mean-square energy of the noise-only band before and
  after (Welch average, 4096/1024).  This *is* the SNR improvement on a
  stationary noise bed: the band contains no wanted content, so losing *X* dB
  in the band is *X* dB of SNR, provided the tone survives.
* **tone level** — the level of a wanted partial before and after, measured with
  a short-time DFT **in the frames where the partial is provably above the
  noise** (the clean part is at least 10 dB above the noise part in that
  frame).  A partial that is *at* the noise level is supposed to be removed.
* **SNR improvement (projection)** — the classic figure: the clean part is the
  orthogonal projection of the output onto the known clean signal, everything
  else is error.
* **musical noise** — the temporal variance of the gain curve inside the noise
  band (dB²), measured on the curve the module actually applies.

### 1.2 The mirror (how the levers were driven)

`MirrorDenoiser` in `qa/quality/harness.py` is a faithful re-implementation of
the module's gain law, built directly on `aurcore.dsp.spectral`
(`decision_directed_snr`, `om_lsa_gain`, `spectral_subtract`,
`smooth_gain_curve`, `estimate_noise_profile`, `masking_model`, `whiten`) and
`aurcore.dsp.stft.STFT`.

**The mirror is bit-exact against the module — as long as the module carries
the implementation the study describes.**  `python qa/quality/validate.py`
checks that the two agree to within 1e-9 before any lever result is trusted.
Since the change of §4 landed, the mirror reproduces the gain law *before* that
change and the two no longer agree; the script says so and explains why.  The
lever results below describe the implementation the change was written for.

### 1.3 Targets

| target | value | why |
|---|---|---|
| SNR improvement on a stationary noise bed | ≥ 12 dB | the band carries nothing but noise, so every decibel lost there is a decibel of SNR; the module's own `Noise Reduction` cap is 15 dB, so the target is reachable with margin |
| movement of a wanted partial | ≤ 1 dB | 1 dB is at the edge of audibility, 3 dB is plainly audible |
| temporal variance of the gain curve in the noise band | ≤ 1.0 dB² (σ ≤ 1 dB) | a stationary bed must produce a steady attenuation; a gain curve that breathes is musical noise |

These three numbers are now **rules** in `qa/quality/quality_rules.py`, together with
five more for the other modules.

---

## 2. The baseline before the change

`python qa/quality/baseline.py` → `qa/quality/results/baseline.json`.
Default parameters of the implementation *before* the change (Quality B,
`Noise Reduction` 12 dB, `Masking` 0.5, `Noise Floor Synthesis` 0.5,
`Enhancement` 0.5, `Whitening` 0.5), profile learned from the case's own noise
part.

| case | band before → after | band reduction | gain var (dB²) | worst wanted partial |
|---|---|---|---|---|
| `tone_noise` | −8.60 → −19.47 dB | **10.87 dB** | **1.78** | 0.02 dB (1 kHz) |
| `tone_hum` | −14.66 → −22.96 dB | **8.31 dB** | **3.74** | 0.01 dB (1 kHz) |
| `voice_hiss` | −5.14 → −37.14 dB | 32.00 dB | 0.56 | **7.64 dB** (480 Hz, SNR +15.4 dB) |
| `music_mixture` | −13.05 → −22.97 dB | **9.92 dB** | 1.39 | **6.32 dB** (660 Hz, SNR +31.3 dB) |
| `bed_only` | −2.93 → −14.09 dB | 11.16 dB | 1.12 | — |

Against the targets: **the SNR improvement was not met** (10.87 dB instead of
12 dB), **the tone was not met in two cases** (the voice's 480 Hz harmonic and
the music's 660 Hz plucked note were crushed by 7.6 dB and 6.3 dB), and **the
musical noise was not met** (1.78 d² and 3.74 d²).

### 2.1 What the baseline numbers mean

* The **noise band** behaves: the module removed ~11 dB of stationary noise and
  kept the tone.  The mechanism is the soft-knee reduction law plus the
  `Noise Reduction` cap (12 dB) — the band reduction is capped by the parameter,
  and the psychoacoustic masking relaxation takes ~1 dB off it.
* The **musical noise** was *caused by* the masking relaxation.  The mask is
  computed from the instantaneous spectrum, so the limit it imposes moves from
  frame to frame; with `Masking` at 0 the temporal variance of the gain curve
  was **exactly 0.00 dB²** in every case.
* The **crushed wanted partials** were a tonal-decision failure: the tonal mask
  was measured against the *instantaneous broadband floor*, and that floor is
  polluted by the wanted partials next to it (the voice's partials are 120 Hz
  apart — 1.8 analysis bins at Quality B).

---

## 3. The levers

`python qa/quality/run_experiments.py` → `qa/quality/results/experiments.json`
(one lever at a time, starting from the module's own defaults).

| lever | range tried | effect on the band reduction | effect on the wanted partials | effect on the gain variance |
|---|---|---|---|---|
| over-subtraction factor (soft law) | 1 – 4 | **none** (0.00 dB) | none | none |
| over-subtraction factor (`spectral_subtract` law) | 1 – 4 | none — the `Noise Reduction` cap clips it | worse (music 4.36 → 10.6 dB) | none |
| decision-directed alpha | 0.80 – 0.995, with and without the OM-LSA branch | **none** | none | none |
| decision-directed alpha + power units | 0.80 – 0.995 | none | none | none |
| gain smoothing width | 1 – 13 bins | −0.05 dB | voice 8.69 → 5.82 dB at 2 bins, 12.70 dB at 9 | none |
| noise-profile percentile | 5 – 90 | ±0.05 dB | none | none |
| floor bias (`floor_bias_db`) | 0 – 20 dB | none below 12 dB, −0.6 dB at 20 | none | none |
| broadband-floor window (gap/span) | 2/8 … 12/48 bins | −0.1 … −5.9 dB | **voice 8.69 → 5.52 dB, music 4.36 → 1.76 dB** | none |
| masking amount | 0 – 0.75 | 10.87 → 10.95 dB at 0, 9.42 dB at 0.75 | none | **1.78 → 0.00 at 0, 8.79 at 0.75** |
| masking guard (mask may not undo the reduction) | on | 10.87 → 10.95 dB | none | **1.78 → 0.00** |
| noise-floor synthesis units | fix / off | 10.87 → 11.48 dB (off) | none | none |
| temporal smoothing from the first frame | on | none | none | none |
| tonal reference (broadband → learned profile) | swap | 10.87 → 10.97 dB | **music 6.32 → 2.33 dB, voice 7.64 → 6.01 dB** | none |
| release mapping (module ↔ swapped) | swap | −0.2 dB | voice 7.64 → 5.09 dB | none |

### 3.1 The findings that mattered

1. **The module had no working over-subtraction and no working decision-directed
   alpha.**  The soft-knee law saturates (`weight = 1` for every bin at or below
   the threshold), so multiplying the floor by any factor changes nothing; and
   the OM-LSA branch was fed the *magnitude* of the floor where the estimator
   expects a *power*.  `gamma` and `xi` came out orders of magnitude too low,
   the speech presence probability collapsed to zero and the branch saturated
   the reduction instead of refining it.
2. **The masking relaxation was the single biggest defect.**  It undid part of
   the noise reduction, it followed the instantaneous spectrum and therefore
   *was* the musical noise.
3. **The noise-profile percentile and the floor bias were inert**, because the
   instantaneous broadband floor is always at least as high as the learned
   profile.
4. **The broadband floor's window was the lever for the crushed partials**, but
   a fixed *bin count* means a different width in Hz for every quality preset.
5. **The release mapping was inverted for a de-noiser**: a wanted transient is
   muted for the whole release time because the gain recovers slowly when it
   *rises*.

### 3.2 The trade-off the old implementation forced

`python qa/quality/tradeoff.py` → `qa/quality/results/tradeoff.json`:

| `Noise Reduction` | band reduction | gain variance | voice partial | music partial |
|---|---|---|---|---|
| 12 dB (default) | 10.87 dB | 1.78 dB² | 7.64 dB | 6.32 dB |
| 14 dB | 12.76 dB | 2.72 dB² | 8.40 dB | 7.19 dB |
| 18 dB | 16.13 dB | 5.06 dB² | 9.93 dB | 8.89 dB |
| 24 dB | 19.78 dB | 6.93 dB² | 12.36 dB | 11.23 dB |

**More noise reduction always cost more musical noise and more damaged content.**

---

## 4. The change that was applied

`qa/quality/spectral_de_noise.patch` (279 diff lines, produced and verified by
`python qa/quality/make_patch.py`).  Seven changes, all inside
`aurcore/modules/spectral_de_noise.py`:

| # | change | why |
|---|---|---|
| **R1** | `_apply_masking` takes the per-bin SNR and only relaxes the attenuation of bins that carry wanted content | the psychoacoustic mask protects wanted content; applied to noise bins it undoes the reduction *and* is the musical noise |
| **R2** | the noise floor synthesis compares like with like: `target = (noise_level * neighbour_gain) ** 2` | the old code compared a *level* with a *power*, which made the synthesised floor far too loud |
| **R3** | `_temporal_smooth` starts smoothing with the first block | the musical-noise controls did **nothing** for a render that arrives in one piece |
| **R4** | the tonal mask and the tonal SNR are measured against the **learned noise profile** when one is available | the broadband floor is polluted by the wanted partials next to it |
| **R5** | the broadband floor's excluded zone and window are defined in **Hz** (500 Hz / 1500 Hz) | a fixed bin count means a different width for every quality preset |
| **R6** | the OM-LSA branch is fed the **power** of the floor, not its magnitude | the estimator compares powers; with a magnitude the branch saturates the reduction |
| **R7** | the `Noise Reduction` default was raised from 12 dB to 15 dB | 12 dB is the hard ceiling of the band reduction; the target cannot be met with margin without it |

### 4.1 What it measures now

The numbers of §2 are the numbers of the implementation *before* the change.
After the change (and with `Noise Reduction` at 15 dB) the same measurement
gives:

| case | band reduction | wanted partial | gain variance |
|---|---|---|---|
| `tone_noise` | **14.03 dB** | 0.02 dB | **0.00 dB²** |
| `tone_hum` | **12.42 dB** | 0.01 dB | **0.00 dB²** |
| `voice_hiss` | 34.91 dB | **5.25 dB** | **0.00 dB²** |
| `music_mixture` | 13.01 dB | **2.60 dB** | **0.00 dB²** |
| `bed_only` | 14.24 dB | — | **0.00 dB²** |

**The trade-off is broken.**  Raising `Noise Reduction` now buys noise reduction
*without* musical noise and *without* additional damage to the wanted
partials.

### 4.2 What the change does **not** fix

The wanted partials of the dense cases are still damaged (5.25 dB on the voice's
480 Hz harmonic, 2.60 dB on the music's 660 Hz note).  The remaining cause is
**analysis resolution**: at Quality B the window is 20 ms → 66.7 Hz per bin,
while the voice's partials are 120 Hz apart and the music's chord notes are
57–110 Hz apart.  A Hann main lobe is four bins wide, so those partials are not
separable at this resolution, and no decision rule can save them.

The measurements confirm it — with the longer windows most of it goes:

| quality | window | wanted partial voice / music | before the change |
|---|---|---|---|
| B (default) | 20 ms → 66.7 Hz/bin | 5.25 / 2.60 dB | 7.64 / 6.32 dB |
| C | 25 ms → 50 Hz/bin | **1.95 / 1.81 dB** | 31.79 / 11.42 dB |
| D | 30 ms → 33.3 Hz/bin | **1.63 / 1.99 dB** | 23.50 / 12.60 dB |

So the follow-up is a *default* change rather than a code change: `Quality`
B → C.  Note the right-hand column: the implementation *before* the change was
catastrophic at Quality C and D, because the OM-LSA branch saturated the
reduction on exactly the bins that carry wanted content.

---

## 5. The tests

The acceptance tests are now the rules of the project
(`tests/test_quality_spectral.py`, `qa/quality/quality_rules.py`); see
`qa/quality/REPORT.md` §5 for the list.  The four rules of the Spectral
De-Noise are the three targets of §1.3 plus the dense voice case.

---

## 6. Reproducing

```powershell
cd <repo>
python qa\quality\validate.py        # the mirror is bit-exact against the module
python qa\quality\baseline.py        # the baseline table           -> results/baseline.json
python qa\quality\run_experiments.py # the lever sweep              -> results/experiments.json
python qa\quality\final.py            # the candidate configurations  -> results/final.json
python qa\quality\tradeoff.py         # the reduction/artefact curve  -> results/tradeoff.json
python qa\quality\make_patch.py       # builds and verifies the patch -> spectral_de_noise.patch
python -m pytest tests\test_quality_spectral.py -q
```

Supporting probes: `diagnose.py` (per-feature attribution), `error_map.py`
(where the error energy lives), `trace.py` / `sidebands.py` (one partial
through the chain), `release_probe.py`, `tonal_ref_probe.py`,
`resolution_probe.py`.

---

## 7. Status of the repository

* `aurcore/modules/spectral_de_noise.py` — **changed by its owner** (the change
  of §4 is in it).
* `aurcore/dsp/spectral.py`, `aurcore/dsp/stft.py` — **not modified**; the
  harness calls them directly.
* `qa/quality/**` — the ground truth moved to `ground_truth.py`, the instrument
  to `measurement.py`, the rules to `quality_rules.py`; the mirror and the
  probes are unchanged.
* **No shared contract file was changed by the study.**  R7 (the default of
  `Noise Reduction`) is a change of the canonical parameter table and was made
  by the owner of that table.
