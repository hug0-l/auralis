# The voice shaped modules — quality report

**Harness:** `qa/qualityvoice/` (`voice_signal.py`, `harness.py`, `rules.py`)
**Runner:** `qa/qualityvoice/run_voice.py`
**Test:** `tests/test_quality_voice.py`
**Date:** 2026-10-21

Reproduce the numbers of this report with one command:

```
python qa/qualityvoice/run_voice.py
python qa/qualityvoice/run_voice.py --json qa/qualityvoice/results.json
```

The runner measures every probe of `qa/qualityvoice/rules.py`, the block
coherence of every probe, the calibration of the chatter measurement and the
properties of the synthetic voice.  The gate reads the same harness and the
same rules, so the numbers of this report and the numbers of the gate are the
same numbers.

---

## 1. Verdict

| | |
|---|---|
| **Modules the harness measures** | **5** — Voice De-Noise, Breath Control, Mouth De-Click, De-Ess, Dialogue Isolate |
| **The instrument** | drives the public `process` entry point, reads no internal state; one set of measurements, one set of rules |
| **The synthetic voice** | a fundamental with a jitter, four formants, a spectral tilt and pauses; **all ten** measured properties that have a reference range fall inside the range of a real voice |
| **The chatter measurement** | the roughness of the gain curve; **0.000 dB** for a steady gain, **0.615 dB** for an injected 2 dB jitter, **0.134 dB** for a gain that follows a content that moves by **35.8 dB** |
| **The rules** | 8 rules with their numbers, asserted for every module that has one (§5) |
| **The gate today** | **13 passed, 5 xfailed, 3 xpassed, 0 failed**.  The markers are **not** strict: a module that reaches its rule shows up as an XPASS and the suite stays green.  **5 of the 70 rule checks still fail** (§6) |
| **The Voice De-Noise** | the fix of the module owner has landed: the hiss band loses **14.50 dB**, the voice band moves by **0.03 dB**, a clean voice by **0.00 dB**, and the module is block coherent to **−218 dB** |
| **The open defects** | the Breath Control moves the voice band by **3.65 dB** (the rule is 1.5 dB), and four modules are not block coherent |

---

## 2. The instrument

`qa/qualityvoice/harness.py` measures **any** module that works on a voice
shaped signal.  It drives `process` and compares the output with the input;
it never looks inside a module, so it survives a rewrite of the module
underneath it.

| measurement | definition |
|---|---|
| `band_<name>_change_db` | the energy of a named band after − before |
| `region_<name>_<band>_change_db` | the same, restricted to a named stretch (the breath, the click, the sibilance) |
| `chatter_db` | the roughness of the gain curve — see §4 |
| `latency_reported` / `latency_measured` | the delay the module claims and the delay it really adds (cross correlation) |
| `block_rms_diff_db` | the RMS difference between the one block render and the 4096 sample blocks |
| `output_level_db` | the level of the output against the level of the input |
| `output_max_diff` | the largest difference between the input and the output |

The gain curve is recovered from the output and the input alone:
`gain(t) = level_of_band(output, t) − level_of_band(input, t)`.  **The
wanted content cancels out in that difference**, so it cannot hide in the
measurement.

### 2.1 The cases

Seven cases, all built from the same synthetic voice, so the numbers of
the cases can be compared with each other.

| case | content | bands | regions |
|---|---|---|---|
| `voice_clean` | the voice alone, without any background | voice 120 Hz–3 kHz, hiss 5–14 kHz, low 150–600 Hz | — |
| `voice_hiss` | the voice under white noise (-45 dBFS) | as above | — |
| `voice_pink_hiss` | the voice under pink noise | as above | — |
| `voice_hum` | the voice with a 50 Hz mains hum | as above plus hum 40–90 Hz | — |
| `voice_breath` | the voice with a breath (200 ms, 400 Hz–6 kHz) in one of its pauses | voice, breath 1–8 kHz, hiss | breath |
| `voice_click` | the voice with two mouth clicks (1.5 ms, 500 Hz–12 kHz): one over a vowel, one in a pause | voice, click 1–14 kHz, hiss | click_a, click_b |
| `voice_sibilance` | the voice with a sibilant burst (180 ms, 6–12 kHz) | voice, low 300 Hz–3.5 kHz, sib 6–12 kHz, hiss | sib |

The 5–14 kHz band carries background only: the synthetic voice has **58.23 dB**
less energy above 5 kHz than in total (§3), and its fricative bursts are band
limited to 3.5 kHz.  The hiss measurement and the voice measurement are
therefore independent.

---

## 3. The synthetic voice

A harmonic stack with a formant envelope is not a voice.  The generator
(`qa/qualityvoice/voice_signal.py`) builds one that has the properties a
voice has, and the harness **measures** them instead of asserting them.

| property | the design | **measured** | a voice has | verdict |
|---|---|---|---|---|
| the fundamental | 110.0 Hz | **110.05 Hz** | 85–255 Hz | inside |
| the spread of the fundamental (jitter + vibrato) | 1.10 % | **0.74 %** | — | — |
| the period jitter | 0.35 % | **0.26 %** | below 1 % | inside |
| the spacing of the partials | 110 Hz | **109.52 Hz** (0.48 % from the fundamental) | the fundamental | inside |
| the first formant | 520 Hz | **526.25 Hz** | 250–900 Hz | inside |
| the second formant | 1480 Hz | **1495.00 Hz** | 800–2800 Hz | inside |
| the third formant | 2480 Hz | **2482.50 Hz** | 2200–3200 Hz | inside |
| the fourth formant | 3450 Hz | **3450.00 Hz** | 2800–4200 Hz | inside |
| the radiated spectral tilt | −6.0 dB/octave | **−4.82 dB/octave** | −12 … −4 dB/octave | inside |
| the pause share | 0.30 | **0.30** (6 pauses) | 0.05–0.60 | inside |
| the energy above 5 kHz | — | **−58.23 dB** | at least 40 dB below the total | inside |

**All ten properties that have a reference range are inside the range of a
real voice.**  The reference ranges are in `voice_signal.py`
(`VOICE_REFERENCE`); they are general facts about human speech, not
measurements of a recording.

### 3.1 How the properties are measured

| property | the measurement |
|---|---|
| the fundamental | the normalised autocorrelation of every 2048 sample frame, parabolic interpolation of the peak, the frames that lock onto the wrong period rejected against the local median |
| the period jitter | the mean relative change of the period between two neighbouring frames |
| the spacing of the partials | the autocorrelation of the long term average spectrum: a harmonic stack is a comb, and the peak of the autocorrelation of its spectrum is the spacing of its teeth.  Unlike the peak picking this does not need every partial to be strong |
| the formants | the amplitudes of the partials (the **total** power of each tooth, so the vibrato does not change the measurement), fitted with the model `A(f) = (f/f0)^t · Σ gⱼ / (1 + ((f−Fⱼ)/BWⱼ)²)`; the centres `Fⱼ` come from a coordinate descent, the gains from a linear least squares |
| the spectral tilt | the exponent `t` of that fit, converted to dB per octave of the power spectrum |
| the pauses | the frames whose level is more than 40 dB below the loudest frame |
| the energy above 5 kHz | the share of the long term average spectrum above 5 kHz |

The long term average spectrum is built from the **periodically voiced**
frames only: a fricative burst is broadband noise and it would bury the weak
upper partials in the average even though it lives at another moment in
time.  The frames are selected with the autocorrelation of the frame itself,
so the selection does not need to know anything about the design.

---

## 4. The chatter measurement

### 4.1 What is measured

The gain the module applies to a band is the difference between the level of
the band in the output and its level in the input, frame by frame.  The
wanted content is in both, so it cancels out.

**The chatter is the roughness of that gain curve**: the median absolute
deviation of its frame to frame difference, scaled to a standard deviation
(`1.4826 · MAD(Δgain) / √2`).  Two properties make this the right measure.

* The wanted content moves **slowly** — the envelope of a syllable moves
  over tens of milliseconds, so the frame to frame difference of a gain that
  follows it stays small.
* The chatter moves **fast** — the gain of a denoiser that hunts from frame
  to frame produces a large frame to frame difference.

The median absolute deviation makes the measure **robust**: a single step (a
gate that opens once, a click that is removed) is not chatter and is not
counted.

### 4.2 The calibration — three reference modules

The harness carries three reference modules that calibrate the measurement
(`qa/qualityvoice/harness.py`).

| reference module | what it does | chatter | the spread of its gain | the spread of the content |
|---|---|---|---|---|
| the identity | nothing | **0.000 dB** | 0.00 dB | 35.8 dB |
| `SteadyGain` | a constant gain of −3 dB | **0.000 dB** | 0.00 dB | 35.8 dB |
| `FrameGain` | a gain that is redrawn every frame with a standard deviation of **2.0 dB** | **0.615 dB** | 1.04 dB | 35.8 dB |
| `ContentFollower` | attenuates the loud parts and leaves the quiet ones alone, the way a compressor does | **0.134 dB** | **5.40 dB** | **35.8 dB** |

The rows say three different things.

1. **The zero point.**  A gain that does not move is reported as 0.000 dB,
   even though the wanted content of the same signal moves by 35.8 dB peak to
   peak (14.6 dB standard deviation).  *The content's variation is not
   counted.*
2. **The scale.**  An injected jitter of 2.0 dB per frame is reported as
   0.615 dB of roughness and 1.04 dB of gain spread.  The number is smaller
   than the injected one because the measurement resolves the gain that
   reaches the output, and the overlap-add of the module smooths that gain
   over the measurement window (2048 samples, 42.7 ms).  A jitter that is
   faster than the window is not audible as a separate movement either.
3. **The proof.**  The `ContentFollower` moves its gain by 5.40 dB while the
   content of the signal moves by 35.8 dB — and the measurement reports
   **0.134 dB** of chatter.  A gain that follows the content is not chatter.

### 4.3 The measured chatter of the modules

| module | the band | the content's spread | the gain's spread | **the chatter** | the budget |
|---|---|---|---|---|---|
| Voice De-Noise | 5–14 kHz | 35.8 dB | 1.57 dB | **0.156 dB** | 1.0 dB |
| Breath Control | 4–8 kHz | 40.1 dB | 1.48 dB | **0.166 dB** | 1.0 dB |
| Mouth De-Click | 4–14 kHz | 39.5 dB | 5.43 dB | **0.000 dB** | 1.0 dB |
| De-Ess | 6–14 kHz | 39.1 dB | 8.89 dB | **0.567 dB** | 1.0 dB |
| Dialogue Isolate | 5–14 kHz | 35.8 dB | 1.25 dB | **0.373 dB** | 1.0 dB |

Every module of the catalogue meets the chatter rule today.  The Mouth
De-Click is the clearest case: its gain moves by 5.43 dB (it removes two
clicks) and the content moves by 39.5 dB, and the roughness is **0.000 dB** —
the two clicks are two steps, not chatter.

---

## 5. The rules

The rules live in `qa/qualityvoice/rules.py` and are asserted for every
module that has one.

| # | rule | number | what it means |
|---|---|---|---|
| R1 | the wanted content survives | **1.5 dB** | the band that carries the voice may not move by more than 1.5 dB |
| R2 | the module does its job | see below | the band the module is there for has to get quieter by the number in the table |
| R3 | the gain curve does not chatter | **1.0 dB** | the roughness of the gain curve (§4) |
| R4 | the latency is reported | **1 sample** | the delay the module reports has to be the delay it really adds |
| R5 | the output does not depend on the block size | **−60 dB** | the RMS difference between the one block render and the 4096 sample blocks |
| R6 | the mix of the stems is transparent | **1e-9** | a module that only mixes its own stems has to give the input back unchanged |
| R7 | the level survives | **12 dB** | the output may not be more than 12 dB quieter than the input |
| R8 | the clean signal survives | **0.5 dB** | a module that removes noise may not touch clean material: the voice band of a voice without any background may not move by more than 0.5 dB |

### 5.1 The numbers per module

| module | the case | R2 the target | R1 the protected band | R3 | R4 | R5 | R6 | R7 | R8 |
|---|---|---|---|---|---|---|---|---|---|
| `AUR_VOICEDENOISE` | `voice_hiss` | the hiss band (5–14 kHz) loses **10 dB** | the voice band (120 Hz–3 kHz) **1.5 dB** | 1.0 dB | 1 sample | −60 dB | — | 12 dB | — |
| `AUR_VOICEDENOISE` | `voice_pink_hiss` | the hiss band loses **10 dB** | the voice band **1.5 dB** | 1.0 dB | 1 sample | −60 dB | — | 12 dB | — |
| `AUR_VOICEDENOISE` | `voice_clean` | — | the voice band **1.5 dB** | 1.0 dB | 1 sample | −60 dB | — | 12 dB | **0.5 dB** |
| `AUR_BREATH` | `voice_breath` | the breath loses **6 dB** | the voice band **1.5 dB** | 1.0 dB | 1 sample | −60 dB | — | 12 dB | — |
| `AUR_BREATH` | `voice_clean` | — | the voice band **1.5 dB** | 1.0 dB | 1 sample | −60 dB | — | 12 dB | **0.5 dB** |
| `AUR_MOUTHDECLICK` | `voice_click` | a click loses **10 dB** | the voice band **1.5 dB** | 1.0 dB | 1 sample | −60 dB | — | 12 dB | — |
| `AUR_MOUTHDECLICK` | `voice_clean` | — | the voice band **1.5 dB** | 1.0 dB | 1 sample | −60 dB | — | 12 dB | **0.5 dB** |
| `AUR_DEESS` | `voice_sibilance` | the sibilance loses **6 dB** | the band below the cutoff (300 Hz–3.5 kHz) **1.5 dB** | 1.0 dB | 1 sample | −60 dB | — | 12 dB | — |
| `AUR_DIALOGUEISOLATE` | `voice_hiss`, noise gain −12 dB | the hiss band loses **6 dB** | the voice band **1.5 dB** | 1.0 dB | 1 sample | −60 dB | — | 12 dB | — |
| `AUR_DIALOGUEISOLATE` | `voice_hiss`, every gain 0 dB | — | the voice band **1.5 dB** | 1.0 dB | 1 sample | −60 dB | **1e-9** | 12 dB | — |

The number of R2 is the number the module has to reach: the de-noiser has to
remove 10 dB of hiss, the breath control 6 dB of breath (its own `Gain`
parameter is −6 dB), the mouth de-click 10 dB of click, the de-esser 6 dB of
sibilance, the dialogue isolate 6 dB of hiss with the noise gain at
−12 dB.

R8 does not apply to the De-Ess (it is there to tame the sibilance of a
voice, which is part of the wanted content) and to the Dialogue Isolate
(it separates, so it does not have to leave the signal alone).

---

## 6. The results

Measured on 2026-10-21 with
`python qa/qualityvoice/run_voice.py`; the gate
(`python -m pytest tests\test_quality_voice.py -q`) reports
**13 passed, 5 xfailed, 3 xpassed, 0 failed**.

| probe | R2 the target | R1 the content | R3 the chatter | R4 the latency | R5 the block | R6 | R7 the level | R8 the clean signal |
|---|---|---|---|---|---|---|---|---|
| `voice_denoise_hiss` | **−14.50 dB** | **0.03 dB** | 0.16 dB | 2047 vs 2047 | **−218.3 dB** | — | −0.05 dB | — |
| `voice_denoise_pink_hiss` | **−16.28 dB** | **0.07 dB** | 0.11 dB | 2047 vs 2047 | **−218.2 dB** | — | −0.10 dB | — |
| `voice_denoise_clean` | — | 0.00 dB | 0.00 dB | 2047 vs 2047 | −218.3 dB | — | −0.00 dB | **0.00 dB** |
| `breath_control_breath` | **−6.00 dB** | **3.65 dB** | 0.17 dB | 0 vs 0 | **−22.2 dB** | — | −3.79 dB | — |
| `breath_control_clean` | — | 0.00 dB | 0.00 dB | 0 vs 0 | **−14.0 dB** | — | +0.00 dB | **0.00 dB** |
| `mouth_de_click_click` | **−36.00 dB** | 0.04 dB | 0.00 dB | 0 vs 0 | **−22.1 dB** | — | −0.10 dB | — |
| `mouth_de_click_clean` | — | 0.00 dB | 0.00 dB | 0 vs 0 | −218.3 dB | — | +0.00 dB | **0.00 dB** |
| `de_ess_sibilance` | **−22.38 dB** | 0.00 dB | 0.57 dB | 1024 vs 1024 | **−218.3 dB** | — | −0.83 dB | — |
| `dialogue_isolate_noise_gain` | **−8.28 dB** | 0.25 dB | 0.37 dB | 0 vs 0 | **−22.0 dB** | — | −0.27 dB | — |
| `dialogue_isolate_transparent` | — | 0.00 dB | 0.00 dB | 0 vs 0 | −218.3 dB | 2.8e-17 | +0.00 dB | — |

**5 of the 70 rule checks fail.**  They are:

| probe | the rule | the measured value | the budget |
|---|---|---|---|
| `breath_control_breath` | R1 the wanted content | the voice band moves by **3.65 dB** | 1.5 dB |
| `breath_control_breath` | R5 the block coherence | **−22.2 dB** | −60 dB |
| `breath_control_clean` | R5 the block coherence | **−14.0 dB** | −60 dB |
| `mouth_de_click_click` | R5 the block coherence | **−22.1 dB** | −60 dB |
| `dialogue_isolate_noise_gain` | R5 the block coherence | **−22.0 dB** | −60 dB |

The markers of the gate are **`xfail(strict=False)`** for the modules that
are not the Voice De-Noise, so a module that reaches its rule shows up as an
XPASS and the suite stays green.  Three probes are in that state today:
`mouth_de_click_clean`, `dialogue_isolate_transparent` and `de_ess_sibilance`
— the De-Ess was rewritten while this report was written and it is block
coherent now.  **A green suite therefore does not mean "every rule is
met"** — the markers of §6 are the list of what is still open.

Three results are worth reading twice:

* **The Voice De-Noise meets all of its rules.**  It removes 14.50 dB of
  hiss, it moves the voice band by 0.03 dB, it leaves a clean voice at
  0.00 dB, its gain curve is 0.16 dB rough, it reports its 2047 samples of
  latency honestly and it is block coherent to **−218 dB** — the same number
  as the identity, because the rewrite analyses the stream frame by frame.
* **Dialogue Isolate is transparent.**  With every gain at 0 dB the output
  differs from the input by **2.8e-17** — the three stems add up to the
  input to machine precision.
* **The Breath Control is the one module that still damages the wanted
  content.**  It removes the breath by 6.00 dB — the whole of its own
  `Gain` parameter — but it takes 3.65 dB of the voice band with it, where
  the rule allows 1.5 dB.  It is the only R1 failure in the catalogue.

### 6.1 The Voice De-Noise, in parts

The voice band of the voice de-noise, in parts, on the case `voice_hiss`
(the numbers are the change of the band energy, the module is the shipped
one with the fix of §8):

| band | the change |
|---|---|
| 120–400 Hz | −0.33 dB |
| 400–700 Hz | −0.01 dB |
| 700–1200 Hz | −0.05 dB |
| 1200–1800 Hz | −0.02 dB |
| 1800–2400 Hz | −0.15 dB |
| 2400–3000 Hz | −0.13 dB |
| 3000–4000 Hz | −0.97 dB |
| 4000–5000 Hz | −10.54 dB |
| 5000–8000 Hz | −14.69 dB |
| 8000–14000 Hz | −14.41 dB |

The voice is left alone to within 0.33 dB, the hiss band loses 14 to 15 dB.
The transition is between 3 and 5 kHz, where the voice has nothing left
anyway: its harmonics are 60 dB below the fundamental there.

---

## 7. What the harness found in modules that are not mine

### 7.1 The transpose bug of the shared inverse transform — fixed

Six modules used to build their spectrogram with the shared transform of
`aurcore/dsp/stft.py` and hand it to the shared inverse transform **turned
around**:

`aurcore/modules/breath_control.py`, `_resynthesize()` (the same three
lines were in `de_bleed.py`, `de_ess.py`, `de_hum.py`, `de_plosive.py` and
`de_wind.py`):

```python
shaped = spec[:, :, 0] if spec.shape[2] == 1 else np.transpose(spec, (2, 1, 0))
out = _sp_istft(shaped, hop=hop, window="hann", length=length)
```

`spec` has the shape `(frames, bins, channels)`, so `spec[:, :, 0]` is
`(frames, bins)`.  The shared `istft` expects `(bins, frames)` — it derives
the transform size from `2 * (bins - 1)`, so it read the frames as if they
were bins.

The fix is one transpose in each file:
`shaped = np.transpose(spec[:, :, 0]) if spec.shape[2] == 1 else np.transpose(spec, (2, 1, 0))`.
The patch is in `var/patches/qualityvoice.patch`; the files belong to the
owners of those modules and were not touched by this report.

**The defect is gone.**  Inside the harness it showed up as a delay of
−2050 samples that the module reported as 0, and as a voice band that moved
by 10.0 dB through the Breath Control and by 4.8 dB below the cutoff
through the De-Ess.  Today the Breath Control and the De-Ess both report a
delay of **0 samples** and the measured delay is **0 samples**.

### 7.2 The Voice De-Noise was rewritten while this report was written

The module was rewritten (the streaming rewrite of the frame engine) while
the first version of this report was written.  In the state it was in between
the two, it returned silence, reported 2047 samples of latency and delayed
the signal by 4096 samples.  The shipped module now meets every rule it has
(§6).

One thing in the intermediate version is worth passing on, because it cost
7.2 GiB of memory and it is a one line mistake: in `_run_frames()`,

```python
view = np.lib.stride_tricks.sliding_window_view(data, _FFT_SIZE, axis=0)
frames = view[::_HOP][:count]
spec = np.fft.rfft(frames * self._engine.win[:, None], axis=1)
```

`sliding_window_view(data, _FFT_SIZE, axis=0)` returns
`(frames, channels, window)`, not `(frames, window, channels)`.  The window
was therefore multiplied onto the wrong axis and the array grew to
`(231, 2048, 2048)` — 7.22 GiB — and the module raised
`numpy._core._exceptions._ArrayMemoryError`.

One defect of the shipped module is still open: `_bin_positions_map()`
leaves the two analysis bins below 30 Hz on the **last** band instead of
the first one (`positions[outside] = BAND_COUNT - 1.0` overwrites the line
above it).  The test `test_voice_denoise_maps_every_analysis_bin` pins it
down.

### 7.3 The blocks of the Mouth De-Click, of the Breath Control and of the Dialogue Isolate

None of them is block coherent:

| module | the block difference | the cause |
|---|---|---|
| Breath Control | **−22.2 dB** (the clean case: −14.0 dB) | the module analyses the block it is given |
| Mouth De-Click | **−22.1 dB** | it keeps its noise statistics across the block boundary |
| Dialogue Isolate | **−22.0 dB** | it analyses the whole block it is given, so its noise reference is computed over a different window in every block |

The **De-Ess was rewritten while this report was written**: it reports 1024
samples of latency now (it reported 0 before) and it is block coherent to
**−218.3 dB**, where it was −42.4 dB.  It meets every rule it has.

A host that renders in 4096 sample blocks gets a different result than one
that renders in one go.  This is the catalogue-wide issue of
`qa/performance/REPORT.md` and of `qa/survey/REPORT.md`: the fix is a
streaming analysis, and it touches `aurcore/dsp/stft.py`, which this report
does not own.

---

## 8. The Voice De-Noise: the estimator and the defaults — a historical record

> **Everything in this section was measured with the *previous* version of
> this harness**, whose test signal was a harmonic stack without a jitter, a
> formant envelope or pauses, **and with the module as it was before the
> estimator fix**.  It is kept because it is the measurement the estimator
> fix was derived from.  The numbers of the *shipped* module, measured with
> the current harness, are in §6: **−14.50 dB** of hiss and **0.03 dB** of
> voice on the white noise case.

### 8.1 The baseline of the code before the estimator fix

| case | hiss reduction | the voice band | SNR gain | the chatter |
|---|---|---|---|---|
| white noise | −6.75 dB | **−2.23 dB** | +4.51 dB | 0.22 dB |
| pink noise | −9.09 dB | **−2.21 dB** | +6.88 dB | 0.19 dB |
| mains hum | −8.48 dB | **−6.51 dB** | +1.98 dB | 0.23 dB |
| a quiet gap | −6.76 dB | −0.15 dB | +6.60 dB | 0.22 dB |

The module removed the noise **and the voice**: it gave up 6.8 dB of hiss
reduction and took up to 6.5 dB of voice with it.  That was defect 2 of the
team brief.

### 8.2 The cause

`_band_levels()` separated the wanted content from the noise with a tonal
mask: a bin counts as a harmonic when it stands more than 6 dB above the
local spectral floor.  The floor was estimated with a **median over 16 bins
on each side**; with a 110 Hz fundamental and a 2048 point window the
harmonics sit 4.7 bins apart, so every window that is wide enough to be
robust also contains two or three peaks — the median lands on the peak
skirts, not on the noise between them.

Measured on the voice band of the white noise case: the mask found **no
tonal bin at all** — 47 tonal bins in the whole spectrum, and all of them in
the noise region.  So `noise == total` in every voice band, the reference of
a voice band is the level of the harmonic itself, the Wiener term compares
the band power with 4× its own reference, finds the band "pure signal",
saturates and returns the full `Reduction`: **the voice is attenuated by
(almost) exactly the cap.**

The same mistake in the noise bands cost the reduction: the gate

```python
gate = np.clip((effective + 6.0 - total) / 6.0, 0.0, 1.0)
```

is only two thirds open there, so 12 dB of `Reduction` buy 6.8 dB.

### 8.3 The fix and its numbers

| # | the change | why |
|---|---|---|
| 1 | `_broadband_floor()` → `valley_floor()` (a running minimum, 4 bins per side, the bin itself and its neighbour skipped) | a median over a wide window cannot find the noise between two close harmonics; a running minimum lands in the valley |
| 2 | the tonal margin `> 6.0 dB` → `> 18.0 dB` | with 6 dB the noise itself is mistaken for a harmonic, the noise estimate collapses and nothing is reduced |
| 3 | `Threshold` −45 dB → **−15 dB** | the reference sits 4 dB below the mean band level, the gate is only two thirds open at −45 dB |
| 4 | `Reduction` 12 dB → **18 dB** | with the fixed estimator the voice is protected, so a higher cap is safe |

With the estimator fixed and the two new defaults, measured with the old
harness:

| case | hiss reduction | the voice band | SNR gain | the chatter |
|---|---|---|---|---|
| white noise | **−14.46 dB** | **−0.30 dB** | +14.16 dB | 0.22 dB |
| pink noise | **−14.19 dB** | **−0.32 dB** | +13.87 dB | 0.14 dB |
| mains hum | **−15.09 dB** | **−0.30 dB** | +14.79 dB | 0.21 dB |
| a quiet gap | **−14.25 dB** | **−0.07 dB** | +14.19 dB | 0.21 dB |
| **the worst case** | **−14.19 dB** | **0.32 dB** | | **0.22 dB** |

Against the baseline the hiss reduction goes from **−6.8 dB to −14.2 dB**
and the damage to the voice from **6.5 dB to 0.32 dB**.

**The parameters alone cannot reach the target.**  With the shipped
estimator every lever moves the two numbers in the same direction: the
trade is 1 : 1.3 against the voice.  `Reduction = 18` buys 9.88 dB of hiss
and costs 9.28 dB of voice.

---

## 9. The files

| file | content |
|---|---|
| `qa/qualityvoice/voice_signal.py` | the synthetic voice, the measurement of its properties, the reference ranges of a voice |
| `qa/qualityvoice/harness.py` | the instrument: the cases, the bands, the measurements, the three reference modules |
| `qa/qualityvoice/rules.py` | the rules with their numbers, the probes |
| `qa/qualityvoice/run_voice.py` | the runner: it measures every probe and writes the tables of §6 |
| `qa/qualityvoice/REPORT.md` | this report |
| `qa/qualityvoice/results.json` | the machine readable result of the runner |
| `qa/qualityvoice/experiments.log` | the record of the experiments of §8 (the previous version of the harness) |
| `tests/test_quality_voice.py` | the gate |
| `var/patches/qualityvoice.patch` | the fix for the six modules of §7.1 |

The previous version of this report also carried `qa/qualityvoice/variants.py`
(the experimental estimators of the Voice De-Noise) and
`qa/qualityvoice/experiments.py` (the runner of the experiments of §8).
They were removed: they drove the old harness through the internals of the
module, and the module was being rewritten underneath them.  The numbers they
produced are in `results.json` and in `experiments.log`, and they are quoted
in §8.

Reproduce:

```powershell
cd <repo>
python qa\qualityvoice\run_voice.py
python -m pytest tests\test_quality_voice.py -q
python tools\hygiene_check.py
```
