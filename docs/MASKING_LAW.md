# The masking law — what makes a band wanted and what makes it noise

**Status:** measured, 2026.  **Series:** `stem_separation`, `band_restoration`.
**Companion file:** [`../qa/benchmark/reference_masking.py`](../qa/benchmark/reference_masking.py).

---

## 1. What the masking law is

Every module that removes something has to answer the same question in every
band, in every frame: **is what is in this bin wanted, or is it noise?**  The
answer is the masking law.  The law is what decides; the rules of
`qa/quality/quality_rules.py` only say what the answer has to be worth:

| rule | what it asks | value |
|---|---|---|
| `remove_db` | the artefact band loses at least this much | 12.0 dB |
| `keep_db` | the wanted partial moves by at most this much | 1.0 dB |
| `gain_var_db2` | the applied gain does not breathe more than this | 1.0 dB² |

This note records the law of the two reference products and of ours, the
numbers that were measured for them, and what we change because of them.

---

## 2. How the numbers were measured

A signal is rendered through the reference and the output is compared with the
input in the **long term average power spectrum** (Welch, 4096 point window,
1024 sample hop, the frames before 0.5 s dropped).  The gain inside a band is

```
G(f_lo, f_hi) = 10 * log10 ( sum_out P(f) / sum_in P(f) ),  f in [f_lo, f_hi)
```

A long term average is used because it does not move with the latency the
reference adds: a delay moves the frames around, it does not change their
average power.  The instrument was checked against known scalings before it was
used (a signal scaled by 0.5 measured −6.021 dB, by 0.316 −10.006 dB, by
0.1 −20.000 dB), and against a band limited case (only 8–20 kHz scaled:
−19.914 dB in that band, +0.000 dB in 200–4000 Hz).

### 2.1 Two repairs the instrument needed first

**The host never activated the buses.**  A VST3 host has to activate every
bus of the component before the processor is set up.  The study's host did
not, so both reference plug-ins left the host's output buffer untouched — the
host reported a peak of `1.00000001505e+30` (the sentinel it fills the buffer
with) and 512 unwritten samples — and every measurement of the audio was
impossible.  With `activateBus` added, both process.  The ablation is in the
study's report: the bus activation is necessary and sufficient; the order of
`setActive` and `setupProcessing` does not matter for writing.

**The exercise host moves a parameter of its own.**  It proves that it may
write a parameter by writing *one of them* to a different value, and it leaves
it there.  Which one it picks is what it picks: for one reference it is the
bypass, for the other it is the ambience gain, set to −10 dB.  Every
measurement taken with that host is therefore a measurement of a bypassed
plug-in or of a plug-in with its ambience gain at −10 dB, and never of the
default.  The symptom is unmistakable: a measured band gain of 0.00 dB
everywhere, because the plug-in is passing the input through.

**Every number in this note is therefore measured with the study's own
renderer**, which moves no parameter of its own, so the default it measures is
the plug-in's default.

---

## 3. The law of the `band_restoration` reference

The reference splits the signal into **four bands** at **three cutoffs**
(each 100 … 10000 Hz) and hands each band to one of **seven trained models**.

### 3.1 The decision is made per band, not per bin

A stationary bed, the default settings:

| bed | 40–90 | 90–250 | 250–1000 | 1000–4000 | 4000–8000 | 8000–20000 |
|---|---|---|---|---|---|---|
| white | −13.97 | −14.09 | −14.10 | −14.09 | −14.09 | −14.11 |
| the corpus' hiss clip (20–250, 5000–20000 Hz) | — | — | **−14.12** | — | — | **−14.11** |
| the corpus' pink hiss clip (20–250, 5000–20000 Hz) | — | — | **−14.15** | — | — | **−14.09** |
| the corpus' hum clip (45–55 Hz) | **−14.10** | — | — | — | — | — |

**The removal is a flat 14.1 dB, in every band, for every bed.**  It does
not depend on the material: not on the bed, not on the band, not on the
signal to noise ratio of the wanted content.  A flat removal is the signature
of a decision that is made **about the band**: the band is then attenuated as
a whole, and the wanted content inside the band goes with it.

### 3.2 The wanted part that moves

A 1 kHz partial over a white bed at a swept signal to noise ratio, the gain
measured at the partial:

| SNR | 0 dB | 6 dB | 12 dB | 18 dB | 24 dB | 30 dB |
|---|---|---|---|---|---|---|
| **the 1 kHz partial** | **−17.34 dB** | −14.86 | −14.80 | −13.81 | −12.11 | **−11.28** |

**T1 — there is no signal to noise ratio at which the wanted part is
protected.**  The partial is attenuated by 11 to 17 dB at every ratio between
0 dB and 30 dB.  The attenuation falls monotonically as the wanted part gets
louder relative to the bed — the law is monotone in the right direction — but
its floor is **11 dB of removal of the wanted content**, not 0 dB.

**T2 — the reference takes the wanted content with the noise.**  On the
corpus' hum case (a 50 Hz hum with its 100 Hz harmonic under a 1 kHz
programme) the wanted 1 kHz tone moves by **12.95 dB** while the hum loses
14.10 dB; on the voice case the wanted band (100–4000 Hz) moves by
**2.94 dB** while the hiss band loses 14.23 dB.

**T3 — the price of asking for more.**  The study's sweep of the sixteen
algorithm slots found a *best* setting that removes 269 dB of a white bed and
80 dB of the hum — and **throws the wanted 1 kHz tone away with it (232 dB
of movement)**.  The removal is bought with the programme.

### 3.3 The shape of the decision, and the floor of the target

**L1 — the unit of the decision is the band, not the bin.**

**L2 — the user reaches the decision only through the band edges and a per
band crossfade:**

```
global mix   0 … 100 %     0 = dry, 50 = half, 100 = fully processed
band mix   -100 … +100 %   0 = follow the global mix,
                             +100 = that band fully processed,
                             -100 = that band fully dry
```

The reference's own presets show the law: the low end preset asks for
`+50 / +25 / −25 / −50` on the four bands with the cutoffs at 201.7, 412.6
and 995.5 Hz, and the full preset asks for the global mix at 100 % with only
the band above 4015.1 Hz processed — a telephone band.

**L3 — the reference re-synthesises what it removes.**  A wanted signal with
no energy above 4 kHz comes out with **+41 to +116 dB** more energy in the
bands that had none.  The restoration does not leave a hole; it fills it.

---

## 4. The law of the `stem_separation` reference

The reference splits a recording into **three stems** — a wanted stem, an
unwanted stem and a reverb stem — and gives each of them a gain, a solo and a
mute.  The dry signal is not mixed in: with the bypass on the output is the
input, otherwise the output is the sum of the three stems.

### 4.1 At the default the reference removes nothing — and adds a room tone

| bed | 40–90 | 90–250 | 250–1000 | 1000–4000 | 4000–8000 | 8000–20000 |
|---|---|---|---|---|---|---|
| white | −0.08 | −0.04 | +0.01 | −0.00 | −0.03 | +0.00 |
| pink | −0.10 | −0.05 | +0.01 | −0.00 | −0.03 | +0.00 |
| hum 50 Hz | +0.01 | +0.00 | +0.00 | +0.16 | **+20.19** | **+44.51** |
| the corpus' hiss clip (20–250, 5000–20000 Hz) | — | — | **−0.01** | — | — | **−0.01** |

The high bands of the hum bed come out 20 and 45 dB **above** the input: the
reference adds a room tone.  The `Bypass` switch is a true bypass (±0.11 dB
in every band).

### 4.2 Every cell goes to exactly one stem

A white bed, in −20.09 dBFS rms, the default settings:

| stem | out rms | 40–90 | 90–250 | 250–1000 | 1000–4000 | 4000–8000 | 8000–20000 |
|---|---|---|---|---|---|---|---|
| the mix, default | −20.09 dBFS | −0.08 | −0.04 | +0.01 | −0.00 | −0.03 | +0.00 |
| **the unwanted stem alone** | −20.09 dBFS | −0.08 | −0.05 | +0.01 | −0.00 | −0.03 | +0.00 |
| **the wanted stem alone** | −91.35 dBFS | −77.95 | −77.38 | −79.12 | −75.48 | −67.15 | −76.32 |
| **the reverb stem alone** | −85.80 dBFS | −80.92 | −83.30 | −85.33 | −82.32 | −83.58 | −87.72 |

The whole bed is in the **unwanted** stem; the wanted stem is 67–79 dB below
the input (empty) and the reverb stem 80–88 dB below (empty).

### 4.3 The noise that survives

The floor of the removal is the **gain of the unwanted stem**, not a
threshold.  With that gain at its end stop:

| bed | 40–90 | 90–250 | 250–1000 | 1000–4000 | 4000–8000 | 8000–20000 |
|---|---|---|---|---|---|---|
| white | −73.24 | −74.15 | −76.99 | −73.43 | −67.36 | −76.61 |
| pink | −64.58 | −64.07 | −64.80 | −60.82 | −60.12 | −61.84 |
| hum 50 Hz | **−89.48** | −90.43 | −66.74 | (+16.88) | (+47.65) | (+63.22) |

**59 … 90 dB of removal, and nothing is left of the bed.**  There is no level
at which the reference gives up.

### 4.4 The wanted part that moves — and where the decision changes

A formant shaped harmonic stack (the corpus' own voice generator, f0 = 130 Hz,
30 harmonics) over a white bed at a swept signal to noise ratio.  The
unwanted stem is taken away; what is left is what the reference decided was
**wanted**; the gain is measured at each partial.

| SNR | 130 Hz | 260 | 390 | 520 | 650 | 780 | 1300 | 2600 |
|---|---|---|---|---|---|---|---|---|
| **0 dB** | −87.59 | −85.26 | −76.82 | −61.52 | **−14.47** | −28.31 | −43.20 | **−18.82** |
| **3 dB** | −88.67 | −89.40 | −81.28 | −71.43 | **−19.68** | −37.19 | −49.91 | **−16.11** |
| **6 dB** | −89.78 | −91.44 | −83.02 | −72.49 | **−22.51** | −51.03 | −74.99 | **−35.64** |
| **9 dB** | −90.11 | −104.59 | −107.13 | −96.04 | **−59.33** | −78.29 | −87.05 | **−76.03** |
| **12 dB** | −88.86 | −104.80 | −108.88 | −98.43 | **−81.66** | −89.27 | −90.42 | −84.07 |
| **18 dB** | −84.55 | −105.86 | −103.91 | −97.67 | −91.31 | −91.98 | −97.63 | −86.81 |
| **24 dB** | −76.20 | −101.90 | −99.51 | −97.12 | −89.95 | −92.14 | −98.45 | −86.21 |

**T4 — the transition is between +3 dB and +9 dB of SNR.**  At 3 dB the wanted
part is still there (the strongest partial −19.7 dB, the second −16.1 dB); at
6 dB it is half gone (−22.5 dB and −35.6 dB); at 9 dB it is entirely gone
(−59.3 dB and −76.0 dB); at 12 dB and above nothing of it survives
(−76 … −108 dB).

**T5 — the wanted part is protected only while the bed is at least as loud
as it is.**  At 0 dB of SNR the wanted part is at −14.5 dB; at 9 dB of SNR
the *same* partial is at −59.3 dB; at 24 dB of SNR it is at −90.0 dB.
**The ordering is the opposite of a threshold**: the cleaner the wanted part
is, the more completely it is thrown away.

**T6 — inside the wanted part the partials go in the order of the spectrum's
shape.**  The two that survive best are the 650 Hz and the 2600 Hz partials —
the first and the third formant region of the wanted spectrum — while the
partials between them (390, 520, 780 Hz) are 25 to 75 dB further down.  The
decision is about the *shape* of the spectrum, not about its level.

**T7 — the law is not level-dependent.**  The wanted signal on its own at
−40, −20 and −6 dBFS gives the same answer (−9.3 … −9.9 dB in the voice
bands), so the trigger is the *relationship* between the wanted part and the
bed and never the absolute level.

**The law of the `stem_separation` reference, in one rule:**

> A cell is **wanted** (it goes to the wanted stem) when the bed beside it is
> at least as loud as it is, and when it sits in a formant of the wanted
> spectrum.  It is **noise** (it goes to the unwanted stem) when the bed is
> more than about 3 dB below it.  Nothing is a threshold on the wanted part's
> own level, and nothing is a threshold on the wanted part's frequency.

### 4.5 The wanted part that sounds like noise

The wanted part in the corpus is a **synthetic** vowel, and the reference does
not recognise it as wanted when it stands alone:

| input | 40–90 | 90–250 | 250–1000 | 1000–4000 |
|---|---|---|---|---|
| the vowel alone, **wanted stem** | −28.54 | −42.10 | −62.35 | −51.13 |
| the vowel alone, **unwanted stem** | −0.06 | −0.06 | −0.02 | −0.01 |
| the vowel at 0 dB SNR over white, **unwanted stem** | −84.77 | −86.02 | −16.70 | −21.78 |

**The same content is wanted in one case and noise in the other**, and the
difference is what else is in the signal.  A pure tone behaves the same way:
it is never protected.

---

## 5. Ours

Our law is in `aurcore/modules/spectral_de_noise.py` and
`aurcore/dsp/spectral.py`.  Per bin, per frame:

```
floor_db  = max( broadband_reference , minimum_statistics_tracker + floor_bias )
snr_db    = smoothed_magnitude_db - floor_db
weight    = 0.5 * (1 + cos( pi * clip( (snr_db - T) / K, 0, 1 ) ) )   # raised cosine
depth_db  = min( weight * R , R )
gain_db   = -depth_db
```

with a second branch for tonal content, judged against the broadband
reference instead of the per bin floor, and the depth refined by a decision
directed OM-LSA a priori SNR.

| parameter | default | meaning |
|---|---|---|
| `Noise Threshold` `T` | **0.0 dB** | the SNR below which a bin counts as noise |
| `Knee` `K` | **0.5 dB** | the width of the soft transition |
| `Noise Reduction` `R` | **15.0 dB** | the cap on the broadband attenuation |
| `Tonal Threshold` | 0.0 dB | the same, against the broadband floor |
| `Tonal Reduction` | 12.0 dB | the cap on the tonal attenuation |

**Our law, in one rule:** a bin is **noise** when its smoothed level is not
more than 0.0 dB above the local noise floor; the transition is a 0.5 dB
raised cosine, so a bin half a decibel above the floor is already half kept;
a bin that is noise is attenuated by up to 15.0 dB, a tonal bin by up to
12.0 dB; a bin above the floor is untouched.

---

## 6. The difference

|  | `band_restoration` | `stem_separation` | ours |
|---|---|---|---|
| unit of the decision | the band | the time-frequency cell | the bin |
| kind of law | a learned model inside the band | a classification into three stems | an explicit threshold |
| threshold on the wanted part | **none**: 11 … 17 dB of removal at every SNR from 0 dB to 30 dB | **none**: the wanted part is protected only while the bed is at least as loud as it is | **0.0 dB of SNR** |
| knee | none | none (−20 dB at 3 dB of SNR, −59 dB at 9 dB of SNR) | **0.5 dB** raised cosine |
| wanted part kept | −2.94 dB (the voice band), −12.95 dB (the 1 kHz tone on the hum case) | −14.5 dB at 0 dB of SNR, −90 dB at 24 dB of SNR | −0.00 … −1.15 dB |
| noise removed | a flat **14.1 dB** in every band, for every bed | 0.0 dB at the default; **59 … 90 dB** with the unwanted stem at its end stop | 3.24 … 19.99 dB, case by case |
| a wanted part that sounds like noise | taken with the noise (12.95 dB on the hum case) | **50 … 62 dB into the unwanted stem** | kept (−0.00 … −1.15 dB) |
| what fills the hole | a synthesised ambience (+41 … +116 dB in the empty bands) | a synthesised room tone (+20 … +58 dB) | the noise floor synthesis, at 0.5 by default |

### 6.1 On the corpus' own cases, side by side

The same ruler on both sides.

**`voice_hiss`** (in −17.7 dBFS rms):

| module | 100–4000 Hz (the voice) | 5000–20000 Hz (the hiss) |
|---|---|---|
| **ours** — Spectral De-Noise, defaults | **−1.15 dB** | **−13.60 dB** |
| `stem_separation`, default | −0.01 dB | −0.00 dB |
| `stem_separation`, unwanted stem at its end stop | −82.92 dB | −61.91 dB |
| `stem_separation`, wanted stem at +12 dB | −0.01 dB | −0.02 dB |
| `band_restoration`, default | **−2.94 dB** | **−14.23 dB** |

**`hiss`** (in −60.0 dBFS rms) and **`pink_hiss`** (in −60.0 dBFS rms):

| module | hiss 20–250 | hiss 5000–20000 | pink 20–250 | pink 5000–20000 |
|---|---|---|---|---|
| **ours** | −8.50 dB | −14.08 dB | **−19.99 dB** | −13.81 dB |
| `stem_separation`, default | −0.01 dB | −0.01 dB | −0.01 dB | +0.01 dB |
| `stem_separation`, unwanted stem at its end stop | −20.61 dB | −24.34 dB | −28.46 dB | −22.62 dB |
| `band_restoration`, default | **−14.12 dB** | **−14.11 dB** | −14.15 dB | −14.09 dB |

**`hum50`** (in −10.5 dBFS rms) — the clearest single number of the
comparison:

| module | 45–55 Hz (the hum) | 95–105 Hz (the harmonic) | 950–1050 Hz (the wanted 1 kHz tone) |
|---|---|---|---|
| **ours** | −3.24 dB | +0.00 dB | **−0.00 dB** |
| `stem_separation`, default | +0.01 dB | +0.00 dB | −0.01 dB |
| `stem_separation`, unwanted stem at its end stop | −83.06 dB | +0.00 dB | **−70.10 dB** |
| `band_restoration`, default | **−14.10 dB** | +0.00 dB | **−12.95 dB** |

**`music_mix`** (in −23.9 dBFS rms):

| module | 55–85 Hz (the drum) | 100–120 Hz (the bass) | 200–500 Hz (the chord) |
|---|---|---|---|
| **ours** | **−1.38 dB** | **−0.89 dB** | **−1.12 dB** |
| `stem_separation`, default | +0.01 dB | −0.00 dB | −0.00 dB |
| `stem_separation`, unwanted stem at its end stop | −9.21 dB | −12.96 dB | −31.10 dB |
| `band_restoration`, default | **−14.02 dB** | −4.72 dB | −3.02 dB |

**The gap, in the direction that matters: on the wanted content we are 1.8 dB
better than the model product on the voice band** (−1.15 dB against −2.94 dB)
**and 13.0 dB better on the 1 kHz programme of the hum case** (−0.00 dB
against −12.95 dB).  On the noise we are 1.6 dB behind on the voice hiss
(−13.60 dB against −14.23 dB), 10.9 dB behind on the hum (−3.24 dB against
−14.10 dB) and 5.8 dB ahead on the pink bed (−19.99 dB against −14.15 dB).

### 6.2 What we change

**Nothing about the shape of the law.**  The reference laws are the two ends
of the trade, and one of them is a law we must not copy:

* the `band_restoration` reference removes a **flat 14.1 dB** and takes the
  wanted content with it (2.94 dB on the voice band, 12.95 dB on the 1 kHz
  programme, 232 dB at its best setting).  That is a **per band** decision:
  a band is judged as a whole and then attenuated as a whole;
* the `stem_separation` reference makes a **per cell classification** whose
  test is not on the level of the wanted part but on the level of the bed
  beside it, and whose survivors are the partials in the formants of the
  wanted spectrum.  It is monotone in the **wrong** direction: the cleaner
  the wanted part, the more completely it is thrown away.  This is the
  behaviour that must not be copied.

Ours is a **per bin level test**, and it is the only one of the three that
is monotone in the right direction.  So the two changes are ours, not theirs:

1. **Close the defect.**  A wanted partial that is above the local floor
   must move by at most the budget of `qa/quality/quality_rules.py` (1.0 dB).
   The reference's floor is 14.1 dB of removal for everything, wanted or not;
   ours is 0 dB for a partial above the floor and 15 dB for a partial below
   it.  Ours is the better shape.  The defect is that our *floor* is not
   reached for a partial that sits between the per bin tracker and the
   broadband reference: the two are judged separately and the partial is
   caught between them.  The measurement of the defect is the survey's
   exception list (3.86 dB of a 480 Hz partial); the fix belongs in the
   estimator of the spectral de-noiser and in the voice de-noiser.
2. **Keep the law explicit.**  Our law is a threshold, a knee and a cap that
   a user can read and a test can hold.  A learned model that decides per
   cell would be a different product, not a better law; the references show
   what it costs (a wanted part that sounds like noise is 50 dB into the
   unwanted stem; the wanted partial of the model product moves 11 to 17 dB
   at every signal to noise ratio) and what it buys (a flat 14.1 dB of
   removal), and the second is worth having **without** the first.

### 6.3 What the benchmark now holds us to

`qa/benchmark/reference_masking.py` carries the numbers as plain constants:

| constant | value | what it is |
|---|---|---|
| `REFERENCE_MODEL_NOISE_REMOVAL_DB` | 14.1 | the flat removal of the model reference |
| `REFERENCE_MODEL_WANTED_MOVEMENT_DB` | 3.0 | the wanted movement of the model reference |
| `REFERENCE_MODEL_PARTIAL_ATTENUATION_MIN_DB` | 11.3 | the floor of the wanted attenuation |
| `REFERENCE_MODEL_PARTIAL_ATTENUATION_MAX_DB` | 17.3 | the ceiling of it |
| `REFERENCE_MODEL_BEST_SETTING_MOVEMENT_DB` | 232.0 | what the best setting does to the wanted partial |
| `REFERENCE_STEM_NOISE_REMOVAL_DB` | 0.0 | what the stem reference removes at its default |
| `REFERENCE_STEM_NOISE_REMOVAL_MAX_DB` | 90.0 | the deepest removal measured |
| `REFERENCE_STEM_WANTED_MOVEMENT_DB` | 14.5 | the wanted movement while its decision keeps it |
| `REFERENCE_STEM_DECISION_LOW_SNR_DB` | 3.0 | below this the wanted part is kept |
| `REFERENCE_STEM_DECISION_HIGH_SNR_DB` | 9.0 | above this it is taken away |
| `REFERENCE_STEM_PROGRAMME_MOVEMENT_DB` | 70.1 | the hum case: the 1 kHz programme |
| `OUR_NOISE_THRESHOLD_DB` / `OUR_KNEE_DB` | 0.0 / 0.5 | our own law |
| `OUR_VOICE_BAND_MOVEMENT_DB` | 1.15 | what we do on the corpus voice case |
| `OUR_HISS_BAND_REMOVAL_DB` | 13.60 | what we do on the corpus hiss case |
| `OUR_PROGRAMME_MOVEMENT_DB` | 0.0 | what we do to the wanted programme of the hum case |

The last row of the table is the one to watch: **our wanted movement
(1.15 dB, and 0.00 dB on the hum case) against the references' (2.94 dB and
12.95 dB) and against the budget (1.0 dB).**  We are inside the budget on
the voice case and outside it on the 480 Hz partial the survey lists; the
model reference is outside it on the voice case and much further outside it
on the partial.  Neither is the law to copy — the law to keep is ours, with
the defect closed.

---

## 7. What could not be measured

* **No threshold of the `band_restoration` reference.**  The decision is
  inside a trained model.  What could be measured is the *floor* it imposes
  (14.1 dB of removal in every band, 11 … 17 dB of movement of a wanted
  partial) and the *shape* of the decision (per band, crossfaded per band).
* **The decision of the `stem_separation` reference was measured on a
  synthetic vowel.**  The corpus' voice is a harmonic stack with three
  formants, not speech, and the reference was trained on speech.  The
  transition of §4.4 is therefore conditional on that material; with real
  speech the wanted part is expected to be kept at every signal to noise
  ratio and the transition would move.  **This is the measurement to repeat
  first.**
* The **reverb stem** was measured only as an empty stem (−80 … −88 dB below
  the input) on a stationary bed.
* The **program list** ("0. Default" … "5. Silence") was not swept; only
  the default was used.
* The **latency** of the references (3993 samples and 1726 samples) was not
  measured by this note.

---

*Measured by the study of the two reference restoration products.  The
numbers live in `qa/benchmark/reference_masking.py`; this note says what they
mean.*
