# Adaptive behaviour - a relative threshold, a re-estimate, and a state that belongs to the session

**Status:** measured, 2026-10-21.  **Series:** `stem_separation`, `ours`.
**Companion file:** [`../qa/benchmark/reference_masking.py`](../qa/benchmark/reference_masking.py).
**Companion note:** [`MASKING_LAW.md`](MASKING_LAW.md) - what makes a band wanted and
what makes it noise.

---

## 1. What this note is about

A module that removes a defect has to make two decisions over and over: **is this
bin wanted or is it noise** (the masking law, see the companion note), and **what do
I still remember about the material** (the adaptive behaviour, this note).

The reverse study measured the second one.  It measured it on a reference that
splits a recording into a wanted part and a bed and gives them back separately,
and it measured our own modules on the same material.  The three things the
reference does are worth copying, and one thing ours does not do yet is a defect
that is now written down.

Every number of this note is a constant of the companion file, so that it can be
read, argued about and replaced when a better measurement arrives.

---

## 2. What the reference does

The reference is a **separator**, not a suppressor: at its default it leaves the
material alone (0.01 dB on the voice case) and the separation is only reachable
when the user asks for one of the two parts.  That is a difference of what the
default does, not a difference of quality.

Three things it does are worth copying.

### 2.1 A relative threshold: decide on a ratio, not on a level

The reference learns the bed of the material it is given and it keeps the same
**fraction** of that bed out of the wanted part, whatever the level of the
material is.

| what | number |
|---|---|
| the bed estimate when the material moves +12.00 dB | **+11.39 dB** |
| the same, from a cold start (a fresh instance, one file at a time) | +11.78 dB |
| the leak of the bed into the wanted part, at the quiet level | **-0.12 dB** |
| the same, at the loud level, before the step and after it | **-0.12 dB** |
| the separation: the wanted part over the bed, on the voice case | **25.4 dB** |

The step and the cold start agree to 0.43 dB, so the answer is a function of the
level in front of the reference and not of its history.  The sharpest statement of
it is the leak: **the bed that reaches the wanted part is 0.12 dB below the wanted
part, at every level and through the step**.

**What it means for us:** a module that learns the bed of the material it is
given has to keep that *fraction*, not a fixed number of decibels, and it has to
be measured at more than one level.  A gate that only measures one level cannot
see a threshold that is not level invariant.

### 2.2 A character change is worth a re-estimate, and it must cost the wanted part nothing

When the bed changes from white to pink in the middle of a file, the reference
looks again:

| what | number |
|---|---|
| the bed estimate when the character changes | **-2.41 dB** |
| the same, between the two characters from a cold start | -1.19 dB |
| the wanted part over the same change | 0.43 dB (the edge of the measuring window) |
| the time to cover 90 % of the excursion | **100 ms** (four blocks) |

**What it means for us:** a module that looks again has to look again *cheaply*.
A re-estimate that moves the wanted part is not a re-estimate, it is a second
decision.

### 2.3 The state belongs to the session, not to the file

The reference is converged **250 ms** after the first audio, it refreshes its
estimate every block (10.67 ms at a 512 sample block) and it **keeps what it
learned for the rest of the session**:

| comparison | difference |
|---|---|
| the loud file after the quiet file, against the loud file alone | **-61.10 dB** |
| the same, with a fresh instance for every file | -79.48 dB |
| the run-to-run floor of the reference (two fresh runs of one file) | -79.48 dB |

The state survives the file boundary inside one instance: the difference between
the two runs rises 18 dB above the floor, and a fresh instance puts it back on
the floor.  The refresh rate of the estimate is not the life of the state; the
**session** is.

**What it means for us:** a module that learns something from the material has to
be told where the material changes, or the learning has to be short enough to
matter less than the change itself.  A product that renders one file after
another in one session must not carry the bed of the first file into the second.

---

## 3. Ours: the threshold is not level invariant yet

**Our own defect, measured on the same material:** our spectral de-noise keeps
**0.79 dB more of the bed in the wanted part** when the material is 12 dB louder.

| module | the quiet level (-30 dBFS) | the loud level (-18 dBFS) | the difference |
|---|---|---|---|
| `AUR_SPECTRALDENOISE` | 10.84 dB of the bed removed | 10.05 dB of the bed removed | **0.79 dB** |
| `AUR_VOICEDENOISE` | 0.38 dB removed | 0.38 dB removed | 0.00 dB |

The reference's leak does not move at all: -0.12 dB before the step and -0.12 dB
after it, at the quiet level and at the loud one.  Ours moves by 0.79 dB over the
same 12 dB, so **our threshold is not level invariant yet**.

Our voice de-noise is level independent in the same way (0.38 dB at both levels,
and 0.48 dB across the step, so it is the level that does not move it) but it
removes almost nothing of this bed: the bed of this material sits 25.4 dB below
the wanted part at the reference, and our voice de-noise does not get near that.

The constant is `OUR_BED_LEAK_LEVEL_SENSITIVITY_DB` in the companion file.  It
is a number of **ours**, filed with our own numbers and not with the reference's;
it carries the name the study prepared for it
(`REFERENCE_BED_LEAK_LEVEL_SENSITIVITY_DB`) so that the prepared text can be
applied without renaming, and it is the same number.

---

## 4. The state of ours: what learns, and for how long

### 4.1 The modules that learn

The registry marks the modules that offer a Learn action with the flag
`registry.ModuleInfo.learn`.  **Ten** of the fifty modules carry it:

| module | what it learns |
|---|---|
| `AUR_AMBIENCEMATCH` | the reference ambience |
| `AUR_BANDRESTORATION` | the noise floor of the band |
| `AUR_DEBLEED` | the bleed of the other channel |
| `AUR_DEHUM` | the hum fundamental |
| `AUR_EQMATCH` | the reference curve |
| `AUR_FINDSIMILAR` | the reference spectrum |
| `AUR_REPAIRASSISTANT` | the profile of the material |
| `AUR_SPECTRALDENOISE` | the noise profile (per bin floor and tonal peaks) |
| `AUR_SPECTRALRECOVERY` | the spectrum to recover from |
| `AUR_VOICEDENOISE` | the noise profile |

The survey of the catalogue carries a `learn` flag of its own
(`qa/survey/specs.py`).  It flags **nine** of the ten: the same list without
`AUR_DEHUM`.  The two flags answer two different questions - the registry's says
that the module *offers* the action, the survey's says that the survey *calls*
it before it measures - so the difference is not a contradiction, but it is worth
knowing: the survey measures the de-hum without its learn action.  **The table is
not changed here**; it belongs to another agent.

### 4.2 Where the state is cleared

The state of a learning module lives on the module: `reset()` drops every
learned profile, every tracker and the analysis state.  How long that state
lives depends on the path:

| path | the scope of the learned state |
|---|---|
| the file render with a chain of its own | **per file**: a fresh engine is built for the request |
| the file render through the session engine (the default of the render route) | **per session**: the engine is the session's, so the state of the previous file is still in it |
| the unattended batch | **per file**: a fresh engine is built for every file |
| the live preview | **per session**: one engine per stream |
| the plug-in | **per session**: one instance |

So the product is not of one mind about it: two of the paths clear the learned
state at the file boundary and three do not.  The path that does not is the one
the study warns about: **a module that keeps what it learned will carry the wrong
bed into the next file.**  The session engine is where that can happen today.

### 4.3 What the table says, and where it and the tree disagree

The table that carries the `learn` flag (the survey of the catalogue) says
nothing about the scope of the state, so the scope above is what the code does,
and it is **not** the same on every path.  Where the table and the tree disagree,
the table wins here: the note records the disagreement and the table is left
alone.

---

## 5. The numbers, in one place

| what | constant | value | unit |
|---|---|---|---|
| the bed that reaches the wanted part of the reference | `REFERENCE_BED_LEAK_INTO_WANTED_DB` | -0.12 | dB |
| the separation of the reference on the voice case | `REFERENCE_SEPARATION_WANTED_OVER_BED_DB` | 25.4 | dB |
| the time to look again after a change of character | `REFERENCE_CHARACTER_SETTLING_MS` | 100.0 | ms |
| the time to settle after a step of the level | `REFERENCE_LEVEL_SETTLING_MS` | 25.0 | ms |
| the time to be converged after the first audio | `REFERENCE_CONVERGENCE_MS` | 250.0 | ms |
| the extra bed our own module keeps when the material is louder | `OUR_BED_LEAK_LEVEL_SENSITIVITY_DB` | 0.79 | dB |

The time constants to design against: **10.67 ms** is the refresh of the estimate
(one block of 512 samples at 48 kHz), **25 ms** is the settling after a level
step, **100 ms** is the settling after a character change, **250 ms** is the
convergence, and the **session** is the life of the state.

---

## 6. What we change

1. **The threshold of a learning module is relative.**  The decision is made on
   the ratio of the bin to the floor the module learned, and the fraction of the
   bed that reaches the wanted part is the same at every level.  The gate has to
   measure it at more than one level, and the number to beat is
   `REFERENCE_BED_LEAK_INTO_WANTED_DB`.
2. **A character change is re-estimated, and the re-estimate is cheap.**  The
   wanted part does not move while the floor is looked at again; the budget is
   `REFERENCE_CHARACTER_SETTLING_MS`.
3. **A module that learns is told where the material changes.**  Either the
   state is cleared at the boundary, or the learning is short enough that the
   change matters more than the learning.  The path that renders through the
   session engine is the one to look at.

Until 1 is done, our spectral de-noise is **0.79 dB** behind the reference on the
level sensitivity, and that number is written down in the companion file so that
the gap cannot be forgotten.

---

## 7. What could not be measured

* The reference is protected: its algorithms cannot be read statically, so
  everything above is measured behaviour and not a reading of the code.
* The reference is not bit deterministic: two fresh runs of the same file differ
  by -79.5 dB, and every difference below that floor is noise.  The floor is
  quoted with every comparison above.
* The material is synthetic: the corpus and the voice harness carry a harmonic
  stack and a bed, not real speech.  The reference does not recognise the
  synthetic voice as a voice, so the voice case of the harness is the material
  this note is meaningful on.
* The display, the meters and the controls of the reference were never opened.
* The scope of our own state was read from the code, not measured end to end
  through the interface: the render route, the batch path, the preview and the
  plug-in were each read, and the numbers of the state itself (how long a
  learned profile survives, how fast it follows a new level) are **not**
  measured for our modules yet.  That is the measurement the next wave should
  take, with the same instrument the study used.
