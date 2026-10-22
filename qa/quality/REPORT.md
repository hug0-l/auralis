# QA — the quality instrument: the ground truth, the rules and the measurements

> Scope: `qa/quality/` and `tests/test_quality_spectral.py`.  **No module and no DSP
> primitive was modified.**  The instrument only reads the modules; the rules are
> targets that the module owners have to reach.  (The addendum below is the one
> exception: the De-Clip's *High* preset, which the rule itself caught.)
>
> Artefacts: `qa/quality/ground_truth.py` (the ground truth),
> `qa/quality/measurement.py` (the measurement), `qa/quality/quality_rules.py`
> (the rules), `qa/quality/measure_all.py`
> (the runner), `qa/quality/harness.py` (the spectral study that grew out of it),
> `qa/quality/STUDY.md` (the study itself: the diagnosis, the levers, the change
> that landed), `tests/test_quality_spectral.py` (the gate).

> **Addendum — 2026-10-21, 23:20.**  The ceiling rule of the family
> (`aurcore/ceiling.py`) and this report disagreed about the level of the
> `clipped` case.  The disagreement is settled, and the numbers of the De-Clip
> below are the numbers after the settlement:
>
> * The ground truth of the `clipped` case is stated **at the digital
>   ceiling** — its peak is 1.000000, it was 1.242148.  A hard clipper is
>   memoryless, so the level of the programme it destroyed cannot be read
>   back from the material, and the case states the truth at the one level
>   the format can hold, which is the level the family's rule forces every
>   module's output into.  The material is untouched: the same samples are
>   clipped, by the same amount, as before (the programme is driven 2.981155
>   times past the rail).
> * The De-Clip is unchanged in its default preset; its **High** preset is
>   not.  The interpolation order 7 makes the Hermite reconstruction
>   oscillate — the rebuilt apex of a flat top runs from 0.480x to 1.758x the
>   true apex, against 0.764x to 1.253x at order 5 — so the module
>   over-claimed the destroyed peak by 3.73 dB, and the family's rule scaled
>   the whole programme down for it.  The order is 5 now; the band the
>   reconstruction carries is unchanged.
> * The De-Clip therefore comes **14.95 dB** closer to the truth and moves
>   the wanted partial by **1.37 dB** (the rule asks for 6.00 dB and
>   2.50 dB).  Before the settlement the same measurement said 5.32 dB and
>   3.25 dB, and the difference was the family's rule: the module's own
>   output peaks at 1.196291 and the rule scales it 1.56 dB down to
>   1.000000, while the truth was stated 1.88 dB above the rail — a level no
>   file could hold and no module may return.

Everything below was measured on **2026-10-21 at 16:05** with

```
python qa/quality/measure_all.py
python -m pytest tests\test_quality_spectral.py -q
```

The runner writes `qa/quality/results/measure_all.json`; `python
qa/quality/measure_all.py --all-cases` measures every module on every case
and is what the numbers of §7 were taken from.

---

## 1. What the instrument is

The harness was a study of one module: four synthetic cases, one module, three
targets that were not met, and tests that were marked `xfail`.  It is now the
instrument of the whole project:

| | before | now |
|---|---|---|
| modules measured | 1 (Spectral De-Noise) | **48 (every module of the registry)** |
| ground truth cases | 4 | **7** |
| rules | 3 numbers inside the test file | **8 rules in one file**, with the reasoning |
| the targets | a study | a gate: the test asserts every rule |
| the markers | `xfail(strict=False)` | **`xfail(strict=True)`** |

**The gate today: 60 passed, 1 skipped, 4 xfailed, 2 failed.**  The four
markers are the four rules that are not met (§6.1); the two failures are the
two rules of the Spectral De-Noise that the rewrite of the module broke
(§5.2).  The yard has not been moved to make them green.

---

## 2. The ground truth (`qa/quality/ground_truth.py`)

Every case carries three separate arrays — the wanted part, the unwanted part and
the signal that is fed to the module — so a measurement can say what a module
removed and what it kept without guessing.  All of them come from a seeded
generator: two runs of the same case give the same samples.

| case | content | artefact band | wanted partials | truth |
|---|---|---|---|---|
| `tone_noise` | 1 kHz tone over a stationary white bed | 4–8 kHz | 1 kHz | — |
| `tone_hum` | 1 kHz tone over 50/100/150 Hz mains hum | 4–8 kHz | 1 kHz | — |
| `voice_hiss` | harmonic voice stack (f0 = 120 Hz) under hiss | 6–10 kHz | 120/240/360/480 Hz | — |
| `music_mixture` | chord (220/277/330/440 Hz) + plucked 660 Hz note over a bed | 8–12 kHz | 220…660 Hz | — |
| `bed_only` | a stationary bed, no wanted content | 1–16 kHz | — | — |
| **`clipped`** | 1 kHz + 1.5 kHz programme at the digital ceiling, driven 2.981155× past it and hard clipped | 2.5–8 kHz (clipping harmonics only) | 1 kHz, 1.5 kHz | the programme **before** the clipping, at the digital ceiling |
| **`transient`** | steady 1 kHz programme with four impulsive bursts | 2–16 kHz | 250 Hz, 1 kHz | the programme **without** the bursts, and the sample range of every burst |

The two new cases are the ones the other modules need:

* **`clipped`** — the truth is a *reference*: the error against the programme
  before the clipping says whether a de-clipper brought the signal closer to the
  truth or further away.  The wanted partials must stay where they are.
* **`transient`** — the truth carries the *sample range* of every artefact, so
  the instrument can measure what happened **inside** the artefact (how much of
  it is left) and what happened to the programme **around** it.

The cases are shared: the study, the rules and the acceptance test all read them
from `qa/quality/ground_truth.py`, so there is one yard for the whole project.

---

## 3. The instrument (`qa/quality/measurement.py`)

The instrument asks the same five questions of **any** module of the registry:

| question | metric | how it is measured |
|---|---|---|
| **what is removed** | `removed_db` | the energy of the band that carries nothing but the artefact, before and after |
| **what is kept** | `kept_db` | the largest movement of a partial that is provably above the noise, measured in the frames where it is |
| **the gain curve's variance** | `gain_var_db2`, `internal_gain_var_db2` | the temporal variance of the applied gain inside the band, in dB² |
| **the latency** | `latency_samples` | the delay of the output against the input, from the cross-correlation of the amplitude envelopes |
| **the block coherence** | `block_coherence_db` | the RMS difference between rendering the signal in one block and in blocks of 4096 samples |

and two more for the modules whose work is not a plain removal:

| question | metric | how it is measured |
|---|---|---|
| **what is left of an impulsive artefact** | `event_reduction_db` | the RMS of the difference from the truth inside the artefact, before and after |
| **what happened to the programme around it** | `programme_shift_db` | the level of the band outside the artefact, before and after |
| **how close to the truth** | `reference_error_db` | the RMS error against the reference, before and after |

### 3.1 The gain curve of a module that does not publish one

The study measured the gain curve of the Spectral De-Noise on the curve the
module applies internally (it is captured with a subclass that only observes).
Most modules do not publish such a curve, so the instrument also computes the
**effective** gain: the band energy of the output against the band energy of
the input, frame by frame,

    g(t) = 10 log10( E_out(t) / E_in(t) )

That needs nothing from the module but its input and its output, which is what
makes the measurement usable for every module.  Two figures are reported:

* `gain_var_db2` — the raw curve.  It carries the fluctuation of the material
  that is left behind as well as the drift of the gain.
* `gain_var_smooth_db2` — the same curve after a 107 ms average of the two
  energy curves.  It carries the *drift* of the gain only, which is the figure a
  rule uses when the module does not publish its own curve.

A module that publishes its own curve is measured on that curve
(`internal_gain_var_db2`); the Spectral De-Noise is the only one that does today.

### 3.2 A module that cannot take a whole block

The instrument does not give up when a module refuses a block: it falls back to
rendering the signal in blocks of 4096 samples, records the reason in the
measurement and reports the block coherence as undefined.

**No module needs the fallback today.**  The Deconstruct, which used to raise
`ValueError` (*"186 frames are waiting to be synthesised, the engine can hold 12
of them"*), renders the whole case now.

---

## 4. The rules (`qa/quality/quality_rules.py`)

The rules are the yard.  Every rule says how much the module has to remove, how
much of the wanted content it may not damage and how steady its gain has to be;
the reasoning is written next to the number.

| # | module | case | remove | keep | gain variance | verdict now |
|---|---|---|---|---|---|---|
| 1 | `AUR_SPECTRALDENOISE` | `tone_noise` | ≥ 12 dB | ≤ 1 dB | ≤ 1.0 dB² (own curve) | **not met** (the module does not publish a gain curve any more) |
| 2 | `AUR_SPECTRALDENOISE` | `tone_hum` | ≥ 12 dB | ≤ 1 dB | ≤ 1.0 dB² (own curve) | **not met** (the module does not publish a gain curve any more) |
| 3 | `AUR_SPECTRALDENOISE` | `voice_hiss` | ≥ 12 dB | ≤ 1 dB | — | **not met** (9.17 dB of wanted partial) |
| 4 | `AUR_SPECTRALDENOISE` | `music_mixture` | ≥ 12 dB | ≤ 1 dB | — | **not met** (1.73 dB of wanted partial) |
| 5 | `AUR_VOICEDENOISE` | `voice_hiss` | ≥ 10 dB | ≤ 1.5 dB | ≤ 1.0 dB² (drift) | **not met** (4.88 dB of voice) |
| 6 | `AUR_DECLICK` | `transient` | ≥ 20 dB of the artefact | ≤ 1 dB of the programme | — | **not met** (6.75 dB of artefact left) |
| 7 | `AUR_DECLIP` | `clipped` | ≥ 6 dB closer to the truth | ≤ 2.5 dB | - | **holds** (14.95 dB closer, 1.37 dB) |
| 8 | `AUR_DEHUM` | `tone_hum` (40–90 Hz) | ≥ 20 dB | ≤ 1 dB | ≤ 5.0 dB² (drift) | **holds** (47.86 dB, 0.12 dB, 4.45 dB²) |

**Three of the eight rules hold.**  The De-Clip is the one that changed: it held
its rule already before the repair of the module and it still holds it — the
restoration comes **14.95 dB closer** to the truth, where it used to come
7.77 dB *further away*.  (The addendum at the top of this report explains what
moved the number: the truth of the case is now stated at the digital ceiling.)

**The Spectral De-Noise was rewritten while this report was written**, and the
rewrite changed what the instrument measures:

| | before the rewrite | now |
|---|---|---|
| the noise band of the `tone_noise` case | 14.03 dB | 14.35 dB |
| the noise band of the `voice_hiss` case | 34.91 dB | 42.54 dB |
| the wanted partial of the `voice_hiss` case | 6.80 dB | **9.17 dB** |
| the wanted partial of the `music_mixture` case | 2.60 dB | **1.73 dB** |
| the gain curve the module publishes | 0.0000 dB² | **none** — the module does not publish one any more |
| the block coherence of the `tone_noise` case | −24.73 dB | **−0.61 dB** |
| the cost of a 64 sample block (the module survey) | 4461 us | **95 us** |

The rewrite made the module faster and it removed more noise, and it made the
module damage the wanted partial more, and it lost the gain curve that the
first two rules are measured on.  The numbers of this table are a snapshot of
a file that was in motion: the owner has to run the instrument again when the
rewrite lands.

### 4.1 Why these numbers

* **12 dB of noise band reduction.**  The bed sits in a band that carries no
  wanted content, so every decibel that leaves the band is a decibel of SNR.
  12 dB is the target the study set (`qa/quality/STUDY.md`, section 1.3) and it
  sits below the module's own `Noise Reduction` cap of 15 dB, so it is reachable
  with margin.
* **1 dB of wanted content.**  A partial that moves by 1 dB is at the edge of
  audibility; 3 dB is plainly audible.  The partial is measured only in the
  frames where it is provably at least 10 dB above the noise — below that a
  de-noiser is *expected* to remove it.
* **1.0 dB² of gain variance.**  1.0 dB² is a standard deviation of 1 dB.  A
  stationary bed must produce a steady attenuation; a gain curve that breathes
  from frame to frame is musical noise.
* **10 dB of hiss and 1.5 dB of voice** for the Voice De-Noise: the number of
  its own report (`qa/qualityvoice/REPORT.md` §5).  A voice de-noiser works
  in a band where the wanted content and the noise share the same bins, so its
  budget is half a decibel wider than the spectral de-noiser's.
* **20 dB of artefact for the De-Click.**  A click is 10 dB above the
  programme; a repair that leaves less than 20 dB of it behind leaves it
  audible.  The second criterion (the programme must not move by more than
  1 dB) is the defect the team brief lists for the module: at some
  sensitivities the detector fires on steady material.
* **6 dB closer to the truth for the De-Clip.**  The truth of the clipped case
  is the programme before it was clipped; a de-clipper has to move towards it.
  The wanted partial of this case is the truth's own content, so it is measured
  against the reference: the clipping inflated the 1 kHz partial by 3.34 dB,
  and the truth itself sits 3.34 dB below the input, so a budget of 1 dB could
  not be held by any module that reaches the truth's level.  2.5 dB is the room
  a module needs to restore the programme's level (the convention it uses for
  the height the clipper took away) without touching the partial.
* **20 dB of hum and 5.0 dB² for the De-Hum.**  The gain criterion is looser
  than the 1 dB² of the de-noisers because the band that is left behind is the
  noise bed itself, whose own fluctuation would otherwise be counted as a drift
  of the gain: 5.0 dB² is a drift of 2.2 dB over 107 ms.

---

## 5. The gate (`tests/test_quality_spectral.py`)

| test | what it pins down |
|---|---|
| `test_the_module_holds_its_rule[8 rules]` | every rule of every module that has one |
| `test_the_rules_cover_the_modules_they_claim_to` | every rule names a registered module, a known case, and says why it exists |
| `test_the_instrument_runs_for_every_module[48 modules]` | the instrument measures **every** module of the registry without an exception (a module that crashes is skipped with the reason; the QA runner reports it) |
| `test_the_measurement_is_deterministic` | the same measurement twice gives the same numbers |
| `test_the_measurement_is_deterministic_for_every_rule` | the same for all eight rules |
| `test_the_ground_truth_cases_are_stable` | the fingerprint of every case: the yard does not move under the feet |
| `test_every_case_carries_its_truth` | every case carries a truth that can be checked |
| `test_the_spectral_de_noise_is_stable_for_every_quality_preset` | A–D produce a usable result |
| `test_the_spectral_de_noise_output_is_finite_and_length_preserving` | the module does not damage the signal |

### 5.1 The markers are strict

A rule that is not met yet is marked `xfail(strict=True)`.  A strict marker turns
an unexpected pass into a **failure**, so the day a module reaches the target the
suite goes red with

    [XPASS(strict)] the rule is not met: ...

and the marker has to be removed.  A green suite therefore always means "the
rule is not met yet", never "nobody looked".

The *reason* of a marker is measured when the test file is collected, so it
always quotes the numbers of the moment; the *decision* (which rule carries a
marker) is static and is a snapshot of the 21st of October 2026.  The four
rules that carry a marker today are the four rules that are not met: the two
dense cases of the Spectral De-Noise, the transient case of the De-Click and the
voice case of the Voice De-Noise.

### 5.2 The gate is red: the Spectral De-Noise lost its gain curve

The two rules of the Spectral De-Noise that carried **no** marker — the
`tone_noise` and the `tone_hum` cases — **fail today**.  The module was
rewritten while this report was written and the rewrite took away the one
thing the two rules are measured on: the module does not publish its gain
curve any more, so the instrument answers "the gain curve could not be
measured" and the rules do not hold.

| rule | before the rewrite | now | why |
|---|---|---|---|
| `AUR_SPECTRALDENOISE` / `tone_noise` | 14.03 dB removed, 0.02 dB of wanted content, 0.0000 dB² of gain variance | 14.35 dB removed, 0.016 dB of wanted content, **the gain curve could not be measured** | the module publishes no curve |
| `AUR_SPECTRALDENOISE` / `tone_hum` | 12.42 dB removed, 0.01 dB of wanted content, 0.0000 dB² of gain variance | 12.30 dB removed, 0.011 dB of wanted content, **the gain curve could not be measured** | the module publishes no curve |

The effective gain curve of the rewritten module — the one the instrument
computes from the input and the output alone — is 0.1247 dB² on the
`tone_noise` case and 0.0121 dB² on its drift, well inside the 1.0 dB²
budget.  **The yard has not been changed to make the gate green.**  The
choice belongs to the owner of the module:

* publish the gain curve again (the module had one, and the rule was written
  around it), or
* re-point the two rules at the effective gain curve — that is one word,
  `gain_metric="effective_smooth"`, in `qa/quality/quality_rules.py`, and it
  is a change of the yard, not a measurement.

Until one of the two happens, `python -m pytest tests\test_quality_spectral.py`
exits with two failures:

```
FAILED tests/test_quality_spectral.py::test_the_module_holds_its_rule[aur_spectraldenoise_tone_noise]
FAILED tests/test_quality_spectral.py::test_the_module_holds_its_rule[aur_spectraldenoise_tone_hum]
E  AssertionError: ('Spectral De-Noise (AUR_SPECTRALDENOISE on tone_noise):
   the gain curve could not be measured',)
```

---

## 6. The measurements

`python qa/quality/measure_all.py` → `qa/quality/results/measure_all.json`.

### 6.1 The rules

| rule | removed | kept | gain var | latency | block | verdict |
|---|---|---|---|---|---|---|
| `AUR_SPECTRALDENOISE` / `tone_noise` | 14.35 dB | 0.016 dB | 0.1247 dB² (the module publishes no curve) | 0 | −0.61 dB | not met (the gain curve could not be measured) |
| `AUR_SPECTRALDENOISE` / `tone_hum` | 12.30 dB | 0.011 dB | 0.1467 dB² (the module publishes no curve) | 0 | −0.73 dB | not met (the gain curve could not be measured) |
| `AUR_SPECTRALDENOISE` / `voice_hiss` | 42.54 dB | **9.17 dB** | 0.0406 dB² | 640 | 3.49 dB | not met |
| `AUR_SPECTRALDENOISE` / `music_mixture` | 12.82 dB | **1.73 dB** | 0.0553 dB² | 768 | 4.72 dB | not met |
| `AUR_VOICEDENOISE` / `voice_hiss` | 17.97 dB | **4.88 dB** | 0.1079 dB² | 1152 | −200 dB | not met |
| `AUR_DECLICK` / `transient` | 1.53 dB | 0.003 dB | 0.0000 dB² | 0 | −34.54 dB | not met (6.75 dB of artefact left) |
| `AUR_DECLIP` / `clipped` | 19.02 dB | 1.37 dB | 1.1949 dB² | 0 | −13.23 dB | **holds** (14.95 dB closer to the truth) |
| `AUR_DEHUM` / `tone_hum` | 47.86 dB | 0.117 dB | 4.4538 dB² | 0 | −200 dB | holds |

The gain variance of the Voice De-Noise is 0.1079 dB² on the raw curve and
0.0095 dB² on the drift — the rule uses the drift, which is the tighter of
the two for a module that does not publish its own curve.

**The Spectral De-Noise no longer publishes its own gain curve.**  The first
two rules are measured on that curve (`gain_metric="internal"`), so the
instrument cannot answer them any more: it says "the gain curve could not be
measured" and the rule does not hold.  The module publishes nothing that the
instrument can read; the effective gain curve of the module is 0.1247 dB² on
the `tone_noise` case and 0.0121 dB² on its drift — well inside the 1.0 dB²
budget — but that is a different measurement.

**The De-Wind could not be measured at all** while this report was written:
`ValueError: the stream is out of step: 765 frames were analysed but 16 were
given`.  The module is being rewritten; the runner reports the failure and
carries on with the other 47 modules.

### 6.2 Every module

The instrument runs over all 48 modules of the registry.  The table is in
`qa/quality/results/measure_all.json`; the modules with a rule are in §6.1.
The rest are measured on the `tone_noise` case so that a module owner can see
where their module stands.

### 6.3 The determinism

| check | result |
|---|---|
| the same measurement twice, in one process | **identical** for all 48 modules and all 8 rules |
| the same measurement in three separate processes | **identical** on every number of every rule |
| the source of the module during the measurement | fingerprinted (SHA-256); a change is recorded in `source_changed` |
| the ground truth | the cases are seeded; their fingerprint is pinned by a test |

The numbers that used to vary, and what was done about them:

| what varied | why | what it is now |
|---|---|---|
| the gain curve of the Spectral De-Noise (1.78 dB² one run, 0.00 dB² the next) | the module's source and the **byte code cache** of the tree disagreed: `__pycache__/spectral_de_noise.cpython-315.pyc` held the code of the patched module while the source on disk was the unpatched one, and the cache header (mtime + size) still matched | the cache is regenerated from the source; the instrument fingerprints the source of every module it measures, so a measurement always says which program produced it |
| the gain variance of the transient case (18.4 dB²) | the moving average of the energy curves padded its ends with a step, which showed up as a jump in the gain curve | the average runs over the valid range only and the edges hold the value they already have |
| the latency of some modules (−384, −6400 samples) | the envelope correlation was allowed to peak at a negative lag | only delays of zero or more are considered, and a peak whose normalised correlation is below 0.5 is reported as no delay |
| the artefacts of the De-Click (the click energy did not move) | the event window was measured as a band energy, which the programme dominated | the artefact is now measured as a difference from the truth inside the event |

---

## 7. What belongs to other modules

The instrument found five things that belong to the owners of other modules.  They
are written down in `var/patches/qa_quality_findings.md`.  **Three of them have
been fixed since the last revision of this report**; the table says what the
instrument measures now.

| module | what was measured | what it measures now |
|---|---|---|
| `AUR_DEHUM` | returned a **non-finite output** for every non-silent input during the session (22942 of 24000 samples) | **finite**: the run of 2026-10-21, 17:05, reports 1 problem over 48 modules, and it is not this one |
| `AUR_DEESS` | moved the wanted partial of the voice case by **59.8 dB** — the voice is annihilated | **0.000 dB** — the voice is left alone |
| `AUR_DEPLOSIVE` | moved the wanted partial of the voice case by **214.8 dB** | **1.400 dB** |
| `AUR_GUITARDENOISE` | raised `IndexError` on every input (the module hands a one-dimensional array to a filter that expects two axes) | **no longer raises**; the wanted partial of the voice case moves by 0.049 dB |
| `AUR_DECLIP` | the restoration moved **7.77 dB away** from the truth, and the 1.5 kHz partial by 6.63 dB | the restoration comes **14.95 dB closer** to the truth and the partial moves by 1.367 dB - the rule holds |
| `AUR_DECONSTRUCT` | raised `ValueError` (*"186 frames are waiting to be synthesised, the engine can hold 12 of them"*) when it was given a block of more than a few hundred milliseconds | **no longer raises**; the instrument renders the whole case |
| `AUR_GUITARDENOISE`, `AUR_DERUSTLE`, `AUR_DEREVERB` | moved the wanted partials of their cases by 11.7 dB, 5.0 dB and 2.9 dB | 1.383 dB (the plucked note of the music case), **5.013 dB** (the 120 Hz partial of the voice case) and **3.899 dB** (the 250 Hz partial of the transient case).  They have no rule yet because the cases do not carry the ground truth their work needs |

Two measurements that the instrument made in this run and that are worth a
second look, because they are the numbers of a module that does not fit the
case it was measured on:

| module | the case | the measurement | why the number has to be read with care |
|---|---|---|---|
| `AUR_AMBIENCEMATCH` | `voice_hiss` | the wanted partial of the voice moves by **7.997 dB** | the case carries no reference to match: the module is measured without `learn()`, so it has nothing to align the voice with.  The survey, which learns the reference first, measures 0.01 dB (`qa/survey/REPORT.md`) |
| `AUR_DEHUM` | `voice_hiss` | the wanted partial of the voice moves by **9.044 dB** | the de-hum removes 50 Hz and its first four harmonics (50/100/150/200/250 Hz); the skirts of the 100 Hz and the 150 Hz notch reach the 120 Hz fundamental of the voice case |

---

## 8. Reproducing

```powershell
cd <repo>
python qa\quality\measure_all.py      # every module and every rule -> results/measure_all.json
python qa\quality\measure_all.py --all-cases   # every module on every case
python -m pytest tests\test_quality_spectral.py -q
python qa\quality\validate.py         # the mirror is bit-exact against the module
python qa\quality\baseline.py         # the study's baseline       -> results/baseline.json
python qa\quality\run_experiments.py  # the lever sweep            -> results/experiments.json
python qa\quality\final.py            # the candidate configurations-> results/final.json
python qa\quality\tradeoff.py         # the reduction/artefact curve-> results/tradeoff.json
python qa\quality\make_patch.py       # builds and verifies the patch -> spectral_de_noise.patch
```

The study itself (the mirror, the lever sweep, the trade-off curve and the
proposed patch of the Spectral De-Noise) is unchanged; it now reads the ground
truth from `qa/quality/ground_truth.py` instead of carrying its own copy.

**`python qa/quality/make_patch.py` no longer succeeds**, and that is not a
defect of the tool: the module has been rewritten since the study, so neither
the old code nor the patched code is in the file any more.  It says

```
edit does not apply exactly once (0):
        sm_db = self._smooth_magnitude(mag, state, cfg)
        broad_db = broadband_floor(sm_db)
        tonal_mask = n
```

The patch `qa/quality/spectral_de_noise.patch` is the **historical diff** of the
change that was proposed, not a change that can be applied.  The defect it
proposed to fix is **still open** (§6.1): the 480 Hz partial of the Spectral
De-Noise still loses 3.86 dB, measured by `qa/survey/survey.py --all` — it was
6.46 dB before the module was rewritten.

## 9. Status of the repository

* `aurcore/**` — **not modified**.
* `aurcore/schema/params.tsv` — **not modified**.
* `qa/quality/ground_truth.py`, `qa/quality/measurement.py`,
  `qa/quality/quality_rules.py`, `qa/quality/measure_all.py` — added.
* `qa/quality/harness.py` — the ground truth moved into `cases.py`; the mirror
  and the metrics are unchanged.
* `tests/test_quality_spectral.py` — rewritten around the rules.
* **No shared contract file was changed.**
