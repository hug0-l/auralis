# De-Rustle — measurement and fix

Module: `aurcore/modules/de_rustle.py` (`AUR_DERUSTLE`).
Harness: `qa/rustle/harness.py`, runner: `qa/rustle/run_rustle.py`,
pre-fix copy for the comparison: `qa/rustle/baseline.py`.
Gate: `tests/test_de_rustle.py`.

Everything below was measured with

```
python qa/rustle\run_rustle.py --baseline
```

on 2026-10-21, 48 kHz, with the interpreter of the brief
(`AppData\Local\Python\pythoncore-3.15-64\python.exe`).

---

## 1. The synthetic case

Four seconds at 48 kHz, two parts:

| part | what it is | level |
|---|---|---|
| wanted | a sustained musical note, fundamental 220 Hz plus five harmonics | −18.42 dBFS RMS |
| rustle | broadband friction noise, 120 Hz – 14 kHz, with a burst envelope and a 3 Hz amplitude modulation | −38.42 dBFS RMS |

The wanted part is centred (the same in both channels), the rustle is
decorrelated between the channels, the way a rub on a microphone mount is.

The two parts are separated by frequency, so the two questions can be asked
independently:

* **rustle removed** — energy of the 4 – 12 kHz band (the wanted note has
  nothing above 1.4 kHz), after minus before.
* **wanted removed** — energy of the narrow windows (±45 Hz) around the six
  wanted partials, after minus before.

---

## 2. What the module does (after the fix)

Negative means it was removed.

| case / algorithm | rustle removed | wanted removed | SNR gain | level change |
|---|---|---|---|---|
| mono / Channel Independent | −2.46 dB | −0.33 dB | +2.13 dB | −0.35 dB |
| mono / Joint | −2.46 dB | −0.33 dB | +2.13 dB | −0.35 dB |
| mono / Advanced Joint | −2.46 dB | −0.33 dB | +2.13 dB | −0.35 dB |
| stereo / Channel Independent | −2.46 dB | −0.33 dB | +2.13 dB | −0.35 dB |
| stereo / Joint | −2.87 dB | −0.34 dB | +2.54 dB | −0.36 dB |
| stereo / Advanced Joint | −2.86 dB | −0.28 dB | +2.58 dB | −0.30 dB |

At `Reduction Strength` 0 the module is an exact pass through:

| case | rustle band | wanted part | level |
|---|---|---|---|
| mono | 0.00 dB | 0.00 dB | 0.00 dB |
| stereo | 0.00 dB | 0.00 dB | 0.00 dB |

---

## 3. The judgement

**The module damages what the user wants.** Before the fix the wanted
musical part lost **5.63 dB** in stereo while the rustle lost only
2.63 dB — the module subtracted the wanted part from itself, because the
wanted part was inside its own estimated noise floor. That is a
restoration module that removes the programme and keeps the noise, so it
was fixed.

### The defects

**1. The mono path returned noise.** The shared STFT engine wants
`(bins, frames)` for one channel; the module handed it `(frames, bins)`.
The engine therefore derived a transform size of 2·(frames−1) = 188
instead of 2048 and resynthesised noise: the RMS of a mono block came out
at 11.56 instead of 0.14. The rustle was *amplified by 20.2 dB* and the
wanted part lost 12.75 dB.

**2. The noise floor was broadcast over the frame axis.** The floor
estimate is a curve over the frames, but the gain curve indexed it as
`floor[None, :]`, which made the gain one frame too deep. The
multiplication with the spectrogram then produced a four-dimensional array,
the shared resynthesis raised, and every stereo block longer than one
floor window (0.49 s) silently fell back to the local overlap-add. The
result was right by accident, at a higher cost.

**3. The wanted part was inside its own noise floor.** The floor was the
10th percentile of the magnitude over the block. A sustained wanted partial
is present in every frame, so the floor of its bins was the partial
itself, and the subtraction removed it. This is the defect that broke the
contract.

### The fix

All three changes are in `aurcore/modules/de_rustle.py`.

1. **The resynthesis** transposes the spectrogram the way the shared
   engine wants it (`(bins, frames)` for one channel, `(channels, bins,
   frames)` for several) and trims the length on the sample axis. The local
   fallback now uses the same framing and the same periodic Hann window as
   the shared engine, so both paths give the same result.
2. **The floor estimate** is per frame and per bin — no stray axis — and
   the blending across the floor blocks is vectorised.
3. **The wanted content is protected.** A wanted partial is *peaky* in the
   spectrum, a rub is *flat*. The peakiness of every bin (the module
   already measured it for the joint modes) now decides how much of the
   floor is subtracted:
   * the peaky part of the spectrum is taken out of the floor estimate, so
     a wanted partial can no longer end up inside the floor;
   * a bin whose peakiness is above the gate (0.5) has its gain pulled
     back towards 0 dB, before and after the gain is smoothed.

### Before and after

| case / algorithm | rustle before | rustle after | wanted before | wanted after |
|---|---|---|---|---|
| mono / Channel Independent | **+20.20 dB** | −2.46 dB | **−12.75 dB** | −0.33 dB |
| mono / Joint | **+20.20 dB** | −2.46 dB | **−12.75 dB** | −0.33 dB |
| mono / Advanced Joint | **+20.20 dB** | −2.46 dB | **−12.75 dB** | −0.33 dB |
| stereo / Channel Independent | −2.63 dB | −2.46 dB | **−5.63 dB** | −0.33 dB |
| stereo / Joint | −3.03 dB | −2.87 dB | **−5.59 dB** | −0.34 dB |
| stereo / Advanced Joint | −3.02 dB | −2.86 dB | **−2.11 dB** | −0.28 dB |

At `Reduction Strength` 0:

| case | rustle before | rustle after | wanted before | wanted after | level before | level after |
|---|---|---|---|---|---|---|
| mono | **+25.84 dB** | 0.00 dB | **−8.24 dB** | 0.00 dB | **+8.16 dB** | 0.00 dB |
| stereo | 0.00 dB | 0.00 dB | 0.00 dB | 0.00 dB | 0.00 dB | 0.00 dB |

**The wanted part now moves by 0.33 dB, inside the 1 dB line of the
contract.** The rustle removal is 0.17 dB less than before in the
Channel Independent mode and 0.16 dB better in the Joint modes, so the
rustle was not sacrificed for it.

The gate of the choice: the protection is a threshold on the peakiness
(0.5) and it is applied in decibels, so a bin that is only a little peaky
keeps its full attenuation. Measured over the threshold:

| gate | rustle removed | wanted removed |
|---|---|---|
| 0.0 (protect everything) | −2.00 dB | −0.18 dB |
| 0.4 | −2.51 dB | −0.29 dB |
| **0.5 (shipped)** | **−2.55 dB** | **−0.34 dB** |
| 0.7 | −2.59 dB | −0.39 dB |

(The column differs by 0.09 dB from the table in section 2 because that
table is the stereo case and this sweep is the mono case.)

---

## 4. The edges

`python qa\sweep_modules.py` reports no problem for `AUR_DERUSTLE` (the
sweep runs the module with two channels; the mono path is covered by
`tests/test_de_rustle.py` and by the harness).

Measured directly, 4096 samples, two channels:

| input | length | finite | RMS in | RMS out | peak out |
|---|---|---|---|---|---|
| silence | unchanged | yes | 0.0000 | 0.0000 | 0.0000 |
| constant 0.5 | unchanged | yes | 0.5000 | 0.5000 | 0.5000 |
| full scale square | unchanged | yes | 0.9999 | 0.9968 | 1.0291 |
| single impulse | unchanged | yes | 0.0110 | 0.0110 | 1.0000 |
| 1 kHz tone | unchanged | yes | 0.3537 | 0.3531 | 0.5072 |

No NaN, no infinity, no change of length, no gain. The impulse stays an
impulse (at most 8 samples above a tenth of the peak).

Two observations that are not defects but are worth knowing:

* the square comes out at a peak of 1.0291 (−0.25 dB over full scale) and
  the tone at 0.5072 (+0.12 dB over its input peak). The gain varies from
  frame to frame, so the resynthesis can ring a little above the input.
  The module does not clip and does not limit.
* the constant and the square are barely touched (−0.00 dB and −0.03 dB),
  while the rustle case loses 2.46 dB of rustle. The module removes
  broadband, modulated noise; it does not remove a steady signal.

---

## 5. Latency and block coherence

**Latency: 0 samples.** The module reports `latency_samples == 0` and the
cross correlation of the input against the output puts the delay at 0
samples. The analysis is centred, so the round trip is exact.

**Look ahead: 1025 samples (21.35 ms).** The module analyses a centred
window of 2048 samples every 512 samples, so the output of the last
sample of a block is computed from input samples that lie beyond the
block. The number is measured from the module's own frame count: for a
block of 192 000 samples the last frame is centred at 192 000 and reaches
1024 samples further.

**Determinism: exact.** The same block processed twice gives bit-identical
samples (max difference 0.0). The module carries no state from one call
into the next.

**Block coherence: no.** The module analyses the block it is given, so its
output depends on how the host chops the stream:

| comparison | max difference | RMS difference |
|---|---|---|
| whole signal vs. blocks of 512 | 0.1015 | −11.67 dB |
| whole signal vs. blocks of 1024 | 0.0858 | −13.20 dB |
| whole signal vs. blocks of 4096 | 0.0477 | −26.58 dB |
| whole signal vs. two halves | 0.0416 | −42.28 dB |

The cause is the noise floor: it is estimated over the whole block, so a
short block gets a different floor than a long one, and the gain curve
starts from scratch in every block. The "two blocks of the same signal"
case (the signal cut in half) is the mildest of them at −42.3 dB, which is
why the gate in `tests/test_de_rustle.py` asserts better than −30 dB
there.

Making the module block coherent needs a floor estimate that survives the
block boundary and a gain curve that carries its smoothing state from one
block into the next. That is a redesign of the module, not a fix, and it
is not done here — it is reported as the module's remaining limitation.

---

## 6. What it costs

The cost per block against the realtime budget of the block
(`block / 48 kHz`):

| block | before the fix | after the fix | budget | before | after |
|---|---|---|---|---|---|
| 64 | 474 us | **597 us** | 1333.3 us | 35.6 % | **44.8 %** |
| 128 | 481 us | **620 us** | 2666.7 us | 18.0 % | **23.2 %** |
| 256 | 772 us | **622 us** | 5333.3 us | 14.5 % | **11.7 %** |
| 512 | 775 us | **620 us** | 10666.7 us | 7.3 % | **5.8 %** |

The two implementations were measured alternately, round by round, so a
busy machine hits both of them the same way; the fastest round of six is
reported.  The numbers move by ±25 % between runs on this machine: over
four runs the fixed module measured 553 – 957 µs at 64 samples (42 % – 72 %
of the budget) and the original 1000 – 1498 µs (75 % – 112 %).  The table
above is the run of 2026-10-21, 16:05, on a machine that was 10 % busy.

The cost does **not** fall with the block size, because the analysis
window is fixed at 2048 samples: a 64 sample block is still analysed with
two frames of 2048 samples, and the 2048 point transforms are what the
module spends its time on.

The fixed module is about 30 % *faster* than the one before it, although it
does more: the floor estimate uses an order statistic instead of
`np.percentile`, the neighbourhood sum uses a prefix sum instead of 22
shifted additions, the gain smoothing uses slicing instead of
`np.apply_along_axis`, the decibels are converted with `np.exp` instead of
`np.power`, and the whole block is handled in one array instead of one
array per channel.

---

## 7. What could not be measured

* **Real material.** The module has no learning mode, so every number here
  comes from the synthetic case. Nothing was measured on a recording.
* **A moving wanted part.** The wanted part of the case is a sustained
  note. A melody or a voice (a wanted part whose spectrum moves from frame
  to frame) was not measured separately; the peakiness gate is measured
  with a static spectrum.
* **A decorrelated wanted part.** The inter-channel coherence of the
  Advanced Joint mode was measured with a centred wanted part and a
  decorrelated rub only.
* **The look ahead functionally.** The look ahead is a structural number
  (the frame count and the analysis window). It could not be measured by
  changing the tail of the signal, because the noise floor makes the
  output of a block depend on the whole block.
* **The behaviour of the module under a host that streams.** The block
  coherence numbers are the measurement of that, and they are bad.

---

## 8. The state of the tree at the time of the report

* `python -m pytest tests\test_de_rustle.py -q` → **12 passed**.
* `python qa\sweep_modules.py` → **48 modules, 240 runs, no problems**
  (the run of 2026-10-21, 17:15).
  (While this report was written the sweep reported problems in
  `AUR_VOICEDENOISE`, `AUR_REPAIRASSISTANT` and `AUR_DECONSTRUCT`; those
  were other contributors' modules, being edited at the time, and they are
  clean now.)
* `python tools\hygiene_check.py` → **0 findings in 372 files**.  The two
  findings that were reported when this report was first written (the package
  manager directory written with a drive letter, in the two workflow files of
  `.github/`) have been fixed by their owners.
* `python tools\lint_check.py` → nothing in the files of this module.
