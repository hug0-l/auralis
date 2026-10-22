# Auralis — the restoration reference

> **What this document is.**  The finding of a study of two commercial restoration
> products, written down so that the tree can be held to it: what was measured, what
> the two references do, what we adopt, what we do not, and what could not be measured
> at all.
>
> **The numbers of the references** are in
> [`qa/benchmark/reference_restoration.py`](../qa/benchmark/reference_restoration.py).
> The design note that reads them is this file.
>
> **Every count in the *ours* column is checkable**, and the command that reproduces
> it is in the same paragraph.  The counts of the tree are dated: the tree moves, and a
> count without a date is a number nobody can check.
>
> **No third party name appears in this document.**  The two references are described by
> their shape, never by their names.  The gate is
> [`../tools/hygiene_check.py`](../tools/hygiene_check.py).
>
> The binding rules live in [`../CONTRACT.md`](../CONTRACT.md).  Where this document and
> the contract disagree, the contract wins.

---

## 1. What was measured

Two reference plug-ins were loaded into our own independent VST3 host and driven over
the same corpus and the same probes our own modules are driven over, with the same
instrument (`qa/quality/measurement.py`, and the voice instrument of
`qa/qualityvoice/harness.py`).  Their surfaces were read out of their controllers:
every parameter with its name, its unit, its steps, its flags, its default, and the
value it shows at 41 points across its travel.  The same instrument was then pointed at
our own plug-ins, so the two numbers in every row were produced by the same code on
the same machine.

The two references are:

| | what it is |
|---|---|
| **reference A** | a stem mixer: it splits a recording into three stems and gives each of them a gain and three switches (17 parameters, 6 factory presets, 3 global switches) |
| **reference B** | a band mixer over a model: it picks a trained model and gives it four bands (22 parameters, 7 trained models in 16 slots, 2 splitter designs) |

## 2. The two references

| | reference A (a stem mixer) | reference B (a band mixer over a model) | ours |
|---|---|---|---|
| parameters in one plug-in | 17 | 22 | 1 ... 25 (284 in all, over 51 plug-ins) |
| modes | 6 factory presets, 3 global switches | 7 trained models in 16 slots, 2 splitter designs | 43 enums, 151 choices, no dead slot |
| parameters that carry a unit | 0 | 0 | 109 of 295 |
| parameters that can be automated | 5 of 17 | 21 of 22 | 284 of 284 |
| a bypass control | yes | yes | yes — one per plug-in, id 1652125811 |
| a host preset list | 6 entries | 1 entry | yes — one unit (the root) and one program list |
| the latency it declares | 1470 samples at 48 kHz (30.6 ms), and it moves with the host's block size | 3993 samples (83.2 ms) with its model, 0 without | 0 ... 4096 samples, declared per module |
| the tail it declares | 0 (and its own sound runs 249-325 samples past it) | 0 | declared per module: 10 of the 51 declare a finite tail, 2 an infinite one |
| a wanted partial, measured | untouched, and a partial it does not want is removed by 96.3 dB | the magnitude is shaped by up to 17 dB and the level of the rest is kept to 0.04 dB | -3.73 dB on a 480 Hz partial |
| the decision it takes | 28.7 ms | 12.5 ms on top of 83.2 ms of latency | 1.3 - 4.0 ms, and 41.0 ms with the instrument of the study (see the note below) |
| the chatter of its gain | 2.518 dB^2 while a voice speaks | 0.421 dB^2 | 0.021 dB^2 while a voice speaks, 0.748 dB^2 on a bed |
| its best mode, on the hiss under a voice | - | removes 24.6 dB, moves the voice band 0.22 dB | our voice module removes 17.87 dB, moves it 0.01 dB |
| its factory default, on the same case | - | removes 27.3 dB, moves the voice band 3.03 dB | - |
| its cost | not measurable | 5.5 to 23 ms per 512 frames, not real-time safe (what that says about the granularity: §3.2) | 0.1 ... 2197 us per 64-sample block |
| the round trip of its resynthesis | -3 dB (the phase is not kept) | -3 dB (the phase is not kept) | 311 dB, the phase is kept |
| the answer at another block size | different | different | bit identical (the resynthesis path) |
| is it deterministic | yes | **no** | yes, 48 of the 51 modules the survey covered |

### 2.1 Where the *ours* column comes from

The column holds two kinds of number and they are not the same kind:

* **Read out of the code** — the counts of the canonical table, the curves the table
  carries, the bypass switch and the program list the adapter publishes.  These are
  true of the tree now and they move with it:

  ```bash
  python tools\validate_schema.py
  ```

  51 modules, 295 parameters, 40 of them enums with 151 choices, 105 of them carrying a
  unit, every one of them automatable, and between 1 and 25 parameters in one plug-in.
  The table also carries the three curves: 234 rows are `linear`, 25 are `log` and 25
  are `taper`.

* **Measured** — the wanted partial, the removal, the cost, the block coherence and the
  determinism come from a run of the module survey on 2026-10-21, taken with the command
  in the paragraph:

  ```bash
  # the two modules the table quotes
  python qa\survey\survey.py --module AUR_SPECTRALDENOISE --module AUR_VOICEDENOISE --no-report

  # the whole set: --all does not work today, because qa/survey/specs.py has no
  # specification for AUR_BANDRESTORATION, so the 48 that have one are named one
  # by one on the command line
  ```

  The survey covers **48** of the 51 modules: `qa/survey/specs.py` has no specification
  for `AUR_BANDRESTORATION`, so the newest module of the catalogue is not measured by
  the survey today.  The row that says "48 of the 51" is the survey, not the catalogue.
  The committed `qa/survey/REPORT.md` is an earlier run of the same day and carries the
  older numbers; the ones in this table are the later run.

* **Measured by the study** — the decision time (41.0 ms) and the chatter of the gain
  (0.059 dB^2) were measured by the study on our own voice module, with its own
  instrument, on the tree of the study's day.  They are numbers of ours, not of a
  reference, and the constants file files them under the series `ours` for that reason.

  **The instrument of the study pairs the input and the output the wrong way round.**
  A module that delays its output by `L` samples carries the content of `x[t - L]` at
  `y[t]`, so the pair that describes its gain is `y[t]` and `x[t - L]`.  The instrument
  shifts the *input* forward instead, which puts the two 2 L samples apart: 85.3 ms for
  our voice module and 61.2 ms for the reference of the stems.  Paired the right way,
  our module opens in **1.3 - 4.0 ms**, so the gap of 12.3 ms to the reference of the
  stems is an artefact of the instrument and not a property of either product.  The
  constants file carries the numbers of the study as they were measured, and the
  numbers of this table are the ones the product's own instruments give.

The round trip of our resynthesis is the figure of the transform report
(`qa/fft/REPORT.md`): the round trip is 311 dB below the signal.

### 2.2 What changed since the study was written

The tree moved while the study was being written down.  The corrections, all of them in
the *ours* column:

| the study said | the tree says now | why |
|---|---|---|
| 295 parameters over 51 plug-ins | 295 parameters over 51 plug-ins | the table gained the `curve`, `exponent`, `help` and `aliases` columns, and a module was added |
| 1 ... 19 parameters in one plug-in | 1 ... 25 | the band restoration module carries 25 |
| 39 enums, 143 choices | 43 enums, 151 choices | the same |
| 88 of 295 parameters carry a unit | 109 of 295 | the same |
| 253 of 253 automatable | 284 of 284 | the same |
| **no** bypass control | **yes**, one per plug-in, id 1652125811 | the adapter publishes the switch the format reserves, with `kCanAutomate \| kIsBypass`, one step, texts `Off` and `On` |
| **no** host preset list | **yes**, one unit (the root) and one program list | the controller implements `IUnitInfo`; the list carries the built-in presets of the module, and a program-change parameter is published when the module has more than one preset |
| the latency is 0 ... 2024 samples | 0 ... 4096 samples | the dialogue isolate declares 4096 |
| ours is linear in hertz | 22 of the 23 hertz parameters are `log` | the law was adopted |
| ours is a straight line in dB | 25 parameters carry the taper | the law was adopted |
| -3.86 dB on a 480 Hz partial | -3.73 dB | the spectral de-noise was rewritten |
| our voice module removes 17.9 dB, moves it 0.02 dB | removes 17.87 dB, moves it 0.01 dB | the same |
| 1 ... 1830 us per 64-sample block | 0.1 ... 2197 us per 64-sample block | the cost is a wall-clock number; it moves with the load of the machine |
| only 25 of the 51 modules are block coherent | 32 of the 51 the survey covered | the same |
| yes, 48/48 deterministic | yes, 48 of the 51 the survey covered | the same |

## 3. The techniques worth taking, ranked by the gap they close

The ranking is by the size of the measurable gap, largest first.  Every gap is a
difference between a measured number of ours and a measured number of a reference, in
the units the product already uses.  The last column says where the technique stands
in the tree on 2026-10-21.

| # | technique | the gap it closes | what it costs | the risk | where it stands |
|---|---|---|---|---|---|
| 1 | the wanted/unwanted separation, per bin | 6.8 dB of removal to gain on the voice case (the reference's best mode removes 24.64 dB and moves the voice band 0.22 dB, ours removes 17.87 dB and moves it 0.01 dB), and 3.5 dB of collateral damage to lose on the noise case (ours removes 41.60 dB of the artefact and takes 3.73 dB of a wanted 480 Hz partial with it, the reference's best mode loses 0.22 dB, and the rule allows 1 dB) | one extra mask and one extra pass over the bins, over the analysis we already run: about 60 us per 64-sample block, 4.5 % of the 1333 us budget | medium-high | **not taken.**  This is the open defect of the catalogue.  The patch of the study (`qa/quality/spectral_de_noise.patch`) does not apply to the current file |
| 2 | the decision lag | 12.3 ms (28.7 against 41.0) | a shorter history means a noisier floor; nothing in the code changes size | medium | **taken, and the number it was measured with was wrong.**  The noise reference of the voice module is 29 ms deep now (it was 341 ms), and the gain opens in 1.3 - 4.0 ms when the input and the output are paired the right way.  The depth of the reference is the lag of the decision, so the target of the study is met.  The floor of a shallower window is a noisier one: the module removes 1.1 dB (white noise) to 2.7 dB (pink noise) less hiss, and the chatter of the gain on a bed falls from 1.664 dB^2 to 0.748 dB^2.  The rules of the gain application carry the numbers: `qa/quality/rules_extra.py` |
| 3 | a logarithmic frequency law | the law of the reference is `Hz = 100.24 + 9900.36 n^3.334`, with a largest error of 0.60 Hz over the travel, and it buys 0.02 to 0.09 octaves per 1 % of travel at the bottom of the range.  Ours was 1.5840 octaves per 1 % at 100 Hz against 0.0997 — 15.9 times finer at the bottom, 3.5 times coarser at the top, and the same at both ends instead of a 55:1 spread | one column in the table and the branch the adapter already has: **zero runtime cost** | low | **taken.**  22 of the 23 hertz parameters carry `log`; the twenty-first starts at zero and stays linear |
| 4 | a gain law with unity in the middle of the travel | the law of the reference is `dB = -59.89 + 71.90 n^0.264`, with a largest error of 0.11 dB; unity sits exactly in the middle of the travel, the span below unity is five times the span above it, and one percent of the travel is 0.316 dB at unity.  Ours was a straight line: 0.360 dB per 1 % on the equaliser gains, 0.480 on the stem gains, 0.600 on `Low Gain`, 0.840 on `Gain` | one more curve kind in the model, the exponent beside the range: **zero runtime cost** | low to medium | **taken, with one exception.**  25 parameters carry the taper, and two of them do not fit it — see §3.1 |
| 5 | a bypass control, and a bypass that is a copy | 0 of 51 plug-ins published one | 49 parameters and one branch in the engine's render path: **zero runtime cost** | low | **taken.**  The id the format reserves, and the engine hands the input through bit for bit at zero delay |
| 6 | a host preset list | 0 units and 0 program lists | the controller gains `IUnitInfo`: **zero runtime cost**, one interface | medium | **taken.**  One unit (the root) whose program list carries the built-in presets of the module |
| 7 | block coherence, an honestly declared latency, a covered tail | 16 of the 51 surveyed modules are not block coherent; both references declare a latency that is not the real one | one state array and one buffer per module | medium | **partly taken.**  Every module declares a tail now, and the latency is declared per module; the block coherence of 16 modules is not |
| 8 | the band mixer: split, audition, mix, bypass, and a broadband mix-back | no module of ours used to split a signal into bands and re-mix it | one crossover bank plus four masking passes, about 60 us per 64-sample block | medium-high | **taken.**  `AUR_BANDRESTORATION` is the module the technique describes |
| 9 | a model that ships as data, with its provenance | nothing of ours is learned | a training pipeline, a model format and an inference runtime | very high | **not taken yet — and the cost is not the reason.**  A model that runs per sample cannot run in a real time plug-in, and that is what the reference does; a model that runs per frame costs 4 % of the per-block budget, and that is the one we build.  Our own first model is being trained now — see §3.2 |
| 10 | the stable string key in the session and the preset | our parameter id is the position inside the module, so a parameter inserted in the middle renumbers the ones after it | one column in the session format and one in the preset format, and a migration | medium | **not taken** |
| 11 | the difference control (the audition of what was removed) | we have no control that shows the user what the module removed | one subtraction and one switch per stem | low | **not taken.**  The cheapest way to show a user what a mask did |
| 12 | a rule we are missing: a module that does not touch the material must not change its level | nothing in our rules measures the level of the material that is left alone | one measurement in the quality instrument, one criterion in the rules, one entry per module | low | **taken.**  The rule is in `qa/quality/rules_extra.py` (0.04 dB, the number of the reference of the band mixer) and it is asserted for every module that has it.  It caught a defect of our own: the voice module attenuated a strong partial by up to 14.7 dB, because the interpolation and the smoothing of the gain curve over the frequency axis carried the decision of a neighbouring band into the band that was left alone.  The partials of the voice now move by 0.359 dB instead of 4.884 dB |
| 13 | the two splitter designs, named | no module of ours trades phase linearity for efficiency | a second filter design in the bank of §8, and the latency it implies | medium | **not taken** |
| 14 | the smaller things | the help sentences, the `kIsList` flag, the mode names that say what they do | one column and a pass over the table | low | **mostly taken.**  Every one of the 295 parameters carries a help sentence and a group, and every enum is published with `kIsList`; the mode names still do not say what they do |

### 3.1 The gain law, and the two parameters it does not fit

The shape to copy is the pair, not the exponent: a wide span below unity, a narrow one
above it, and one exponent over the whole travel.  The exponent that puts unity in the
middle is fixed by the range, `p = log2((-lo) / (hi - lo))`, and the resolution at
unity follows from the span below unity: 0.00528 dB per 1 % of travel for every dB of
span.

Twenty-three of the 25 tapered parameters are right: unity sits at 50.1 % of the travel
and the resolution at unity is 0.314 dB per 1 % on a -60 ... +12 dB range, 0.126 on
-24 ... +4.8 dB, 0.094 on -18 ... +3.6 dB and 0.063 on -12 ... +2.4 dB.

Two are not:

| parameter | range | where unity sits | dB per 1 % at unity | the problem |
|---|---|---|---|---|
| `AUR_BANDRESTORATION` `Noise Threshold` | -12 ... +24 dB | 1.6 % of the travel | 1.677 | the taper makes it **4.7 times coarser** at unity than the straight line did (0.360), and a threshold is judged against the signal, not against unity, so it stays on a straight line |
| `AUR_BANDRESTORATION` `Restoration Depth` | 0 ... +40 dB | 0 %, at the very start of the travel | — | the range has no span below unity, so the rule that fixes the exponent does not apply to it; the taper puts 9.8 dB into the first percent of the travel |

This is the risk the study named, and it is open: *applying the taper to a control it
does not fit*.  The safeguard is the rule itself — the law is the pair, not the
exponent — and the acceptance test is the table.

### 3.2 The learned model: what the cost argument really says

**A learned model that runs per sample cannot run in a real time plug-in — and that is
what the reference does.  A learned model that runs per frame fits inside the budget,
at 4 % of it.  Our mechanism is the per frame one.**

This section used to say that a learned model that ships as data "is not affordable in
this phase".  That was true of one granularity and of one only, and the tree was about
to carry it as a general conclusion.  The arithmetic is below, and it is carried by the
**reference's own parameter count**, not by an estimate of ours.

**The two rates.**  The reference's model library holds seven trained models in three
size classes; the largest holds **7,530,937** float32 weights.  A convolution of `k`
taps over `in` input channels into `out` output channels holds `k` times `in` times
`out` weights and needs the same number of multiply-accumulates per output element, so
**one pass of the model costs one multiply-accumulate per weight** — 7,530,937 MAC per
network sample.

* **At the audio rate**, one pass per audio sample, a block of 64 samples costs 64
  passes: 481,979,968 MAC, which is **60 ms** at 8 G MAC/s.  The per-block budget is
  1333 us, so that is **45 times the budget**.  So **a model that runs per sample cannot
  run in a real time plug-in**.  This is the regime the reference is in: the plug-in
  that loads the library was measured at **5.5 to 23 ms per 512 frames** and was
  recorded as not real-time-safe on this machine.
* **At the frame rate**, one pass per spectral frame — one frame per 1024 samples —
  a block of 64 samples carries 1/16 of a pass: 470,684 MAC, **59 us**, which is
  **4 %** of the 1333 us budget.  The middle and the small model cost 33 us and 19 us.
  So **a model that runs per frame fits inside the budget**.

**The intelligence is not in the per sample cost.  It is in the granularity of the
inference.**  A verdict that says "a learned model is too expensive" has to say *at
what rate* it is too expensive.

**What this changes.**  The learned part of a model that runs per frame is affordable
here, at 4 % of the budget, and **our mechanism is the per frame one**: the model path
in `aurcore/models/` reads a frame, answers for that frame and moves on.  So the reason
this technique is not taken is **no longer the cost**.  It is the shape of the work, and
the risk:

* a trained model needs a training pipeline, a model format and an inference runtime,
  and the brief forbids a new dependency — the product runs on numpy and scipy only;
* the risk is **very high**: a model that ships as data has to be versioned, loaded per
  instance and run on its own thread, and every way it can fail has to be a named state.
  A plug-in whose payload is missing must be a pass-through rather than a black box, and
  the reference itself is the proof: without its payload it is inert, and the output is
  bit identical to the input.

**What is not measured yet, and what will settle it.**  **Our own first model is being
trained now, and its real cost is not measured yet.**  Nothing in this section says our
model is cheap.  The number that will settle it is **the cost of one inference of our
own model, in microseconds per 64-sample block**, taken with the instrument of the tree
(`aurcore/models/bench.py`), and filed next to the constants of
[`qa/benchmark/reference_model.py`](../qa/benchmark/reference_model.py) as a measurement
of ours.  Until that number exists, the arithmetic above is arithmetic on the
reference's own counted parameters.  The design note is
[`MODEL_REFERENCE.md`](MODEL_REFERENCE.md).

What we take from it is the **shape**, not the technique:

* a model is a file with a schema and a version;
* every way it can fail is a named state;
* a missing model is a pass-through, not a black box;
* a model either **filters** — it produces a mask — or **generates** — it produces a
  spectrum — and it says which one it is in the artefact, before it is loaded.  See
  [`MODEL_REFERENCE.md`](MODEL_REFERENCE.md) §4.

### 3.3 The bypass and the defaults: the yard

**What was measured.**  The bypass switch of the stem mixer was measured on the clean
musical mixture of the corpus at 48 kHz, with the float32 instrument of the study and
with the 24 bit harness, and the same measurement was then pointed at our own plug-in
with the same renderer on the same material.

| | the reference | ours |
|---|---|---|
| the bypass, largest difference of the samples | **exactly 0** | **exactly 0** |
| the delay the bypass carries | 1470 samples at a block of 256, 1726 at 512, 2238 at 1024 | **0 samples** |
| the same delay in milliseconds at 48 kHz | 35.96 ms at a block of 512 | **0 ms** |
| the defaults on a clean signal, largest difference of the samples | 0.000321984 | **0** |
| the same, below the peak of the input | -63.84 dB | **exactly 0** |
| the same, in steps of a 24 bit word | 2701 | **0** |
| the gain the defaults apply | -0.0055 dB, and the same at -6, -20, -40 and -60 dBFS | 0 dB |
| the gain over the material, peak to peak | 0.0073 dB (standard deviation 0.0012 dB) | 0 dB |
| the floor the default path puts in front of the programme | -103.10 dBFS | below -300 dBFS |

Two things follow, and they are the two rules the project adopts.

**The bypass is exact in the samples and wrong in the time.**  Every sample of a clean
signal comes back unchanged, and the delay of the bypassed path is the delay the
reference declares: 30 to 47 ms at 48 kHz, and it moves with the size of the block the
host hands over.  A host that does not compensate the declared latency plays the
bypassed programme late.  Ours adds no delay at all.

**The defaults are almost transparent and not transparent.**  Every sample of a clean
signal comes out changed, by up to 0.000321984 — 2701 steps of a 24 bit word, 63.84 dB
below the peak of the input.  It is inaudible; it is not zero.  Nothing is added to a
silent input, but the default path puts a floor of its own in front of the programme at
103.10 dBFS.

**What we do.**  The rule is not a change, it is a **guarantee**, and it is written down
as a gate: [`tests/test_safe_defaults.py`](../tests/test_safe_defaults.py).

* **The bypass of every module of the catalogue is sample exact and carries no delay.**
  The output is the input, sample for sample; the largest difference is exactly zero,
  and the chain reports no latency at all while the module is bypassed.
* **The default setting of a repair module leaves a clean signal exactly alone.**
  Measured against the input **delayed by the latency the module declares** — the host
  compensates the delay a module reports, so what the host hears is the signal the
  module produced — the largest difference has to stay at least 120 dB below the peak
  of the input.
* **A silent input comes out as silence**, at least 120 dB below full scale.
* **The gain a module applies does not depend on the level of the input.**  The same
  gain at -6, -20, -40 and -60 dBFS, to within a tenth of a decibel.  A module whose
  gain follows the level of the input gates a quiet recording away: the same setting
  does something else on a quiet recording than on a loud one.

**What to adopt: the yard.**  120 dB below the peak of the input.  It is 56 dB better
than the 63.84 dB of the reference, and it sits 200 dB above the arithmetic floor of a
double precision transform round trip, which is where the modules of ours that go
through a transform land (the measured figures of the transparent modules are between
312 dB and infinity).  The floor for a silent input is the same figure: the reference
puts 103.10 dBFS in front of the programme, ours puts nothing there.

**The line.**  Not every module may be transparent: a gain, an equaliser, a leveler, a
normaliser, a dither, a resampler and a signal generator change the signal as their
job, and asking them to be transparent would be asking them not to work.  The line is
drawn **by the category of the module**:

* the **repair** modules must be transparent by default — a repair module that changes
  a clean signal is either broken or has been told to;
* the **utility**, **measurement** and **system** modules may not be transparent by
  default — that is what they are for.

The gate draws the line the same way and holds both sides of it.  Today **12 of the 28
repair modules** are transparent by default and are held to the rule:

| module | what it is |
|---|---|
| `AUR_AMBIENCEMATCH` | Ambience Match |
| `AUR_AZIMUTH` | Azimuth |
| `AUR_CENTEREXTRACT` | Center Extract |
| `AUR_DECLICK` | De-Click |
| `AUR_DEHUM` | De-Hum |
| `AUR_DEPLOSIVE` | De-Plosive |
| `AUR_DEWIND` | De-Wind |
| `AUR_DIALOGUEISOLATE` | Dialogue Isolate |
| `AUR_MUSICREBALANCE` | Music Rebalance |
| `AUR_REPAIRASSISTANT` | Repair Assistant |
| `AUR_SPECTRALEDITOR` | Spectral Editor |
| `AUR_SPECTRALREPAIR` | Spectral Repair |

The other **16 repair modules** are named in the gate with the reason why their default
is not the absence of processing.  In every case the reason is the same: the canonical
table gives the module a default that **is** an amount of processing — a reduction of
half, a threshold, a level — so a clean signal is worked on whether it wants to be or
not.  The figures below are what the gate measured on 2026-10-21: the largest
difference between the clean probe and the output, in decibels **below the peak of the
probe**, against the input delayed by the latency the module declares.  The yard is
120 dB.

| module | 1 kHz tone | musical mixture |
|---|---|---|
| `AUR_WOWFLUTTER` | +0.9 | -58.9 |
| `AUR_VOICEREVERB` | -39.7 | -43.9 |
| `AUR_DEESS` | -312.2 | -38.0 |
| `AUR_DERUSTLE` | -27.8 | -19.8 |
| `AUR_SPECTRALRECOVERY` | -28.7 | -15.2 |
| `AUR_DECRACKLE` | -3.5 | -13.0 |
| `AUR_DECONSTRUCT` | -10.1 | -9.4 |
| `AUR_BREATH` | -312.2 | -7.5 |
| `AUR_DEREVERB` | -6.0 | -6.0 |
| `AUR_GUITARDENOISE` | -2.2 | -5.3 |
| `AUR_SPECTRALDENOISE` | -9.1 | -2.4 |
| `AUR_VOICEDENOISE` | -1.5 | -2.9 |
| `AUR_MOUTHDECLICK` | +2.5 | -1.3 |
| `AUR_DEBLEED` | -0.0 | -0.0 |
| `AUR_DECLIP` | -inf | 0.0 |
| `AUR_BANDRESTORATION` | +4.1 | +2.4 |

Four of the sixteen make the probe **louder**: `AUR_BANDRESTORATION` (+4.1 dB),
`AUR_MOUTHDECLICK` (+2.5 dB) and `AUR_WOWFLUTTER` (+0.9 dB) on the tone, and
`AUR_DECLIP` on the mixture, where the difference reaches the whole of the peak.  A
restoration module that makes a clean recording louder is the opposite of what it is
for.

For comparison, the twelve modules on the transparent side measure between 313 dB and
infinity below the peak of the probe, and five of them are exactly zero.

The **utility**, **measurement** and **system** modules — 22 of them — are on the other
side of the line: they may change the signal by default, because that is what they are
for.  Five of them are **level controllers**: their gain follows the level of the
input by design, because they carry a target level in the canonical table, and the
gate names them one by one.  One of them **generates a signal**, so a silent input
does not come out silent, and the gate names that one too.  Every one of them is held
to the bypass rule, to the rule that the output is whole and finite, and to the rule
that a silent input comes out as silence.

**This is the open half of the guarantee, and it belongs to the canonical table.**  The
defaults of those 17 modules are recorded in `aurcore/schema/params.tsv`, which has one
owner; moving a module to the transparent side is a change of that table and not of the
gate.  The gate is ready for it: the day a default is made neutral, the module leaves
the list of exceptions and the gate starts holding it to the rule.

## 4. What we do not adopt
* **A learned model that ships as data.**  See §3.2: it is **not affordable at the
  audio rate** — that is the regime the reference is in — and it **is affordable at the
  frame rate**, at 4 % of the per-block budget, which is the rate we build for.  It is
  not taken in this phase because the training pipeline and the payload are missing, not
  because of the cost, and our own first model is being trained now.  Its shape is
  adopted; the technique is not yet.
* **A surface of 22 parameters over a model.**  Ours is a model surface — 284
  parameters, 51 modules, the thresholds and the band structure the user declares.
  The technique that closes a gap of that kind is not fewer knobs; it is that our
  modules should carry their own quality.
* **The nine dead mode slots.**  Ours has none.
* **The short titles cut to eight characters.**  Ours keeps the full name.
* **The normalised position as the plain value.**  Ours answers with the natural value,
  and we are the only one of the three that does.

## 5. What could not be measured, and why

1. **The specifications of six of the study's own agents were not on disk** when the
   study's specification was written.  The tree was polled for 60 minutes; three
   agents landed inside that window and are integrated, and the sections that belong to
   the other six are derivations, marked as such where they appear.
2. **The chain of reference B can only be measured with its model payload in place.**
   Without the payload the plug-in is inert: the output is bit identical to the input
   (correlation 1.0000, difference 3e-9) and *no parameter has any effect at all*.
   Every number of the study that is marked "inert" has to be taken again with the
   payload in place before anything is built on it.
3. **The internals of reference A are unreadable.**  This is a legitimate result: the
   comparison is made on behaviour, not on anybody's code.
4. **The cost of reference A was not measured.**  The module faults when it is
   released, and the number would have to come from a host that survives the release.
5. **The corpus through the references with their payload in place is still missing.**
   That is the one measurement that would close the study.

## 6. The two places where the study's own instruments disagree

Both disagreements are about the **real latency of a reference**, and both were
measured two different ways.  Neither is resolved here: both are recorded, with the
numbers, because a disagreement that is papered over is a number nobody can trust.

### 6.1 Reference A: the cross-correlation probe against the impulse probe

| instrument | what it says |
|---|---|
| the cross-correlation probe (a chirp through the plug-in) | the delay is **1470 samples** at 48 kHz with a block of 256, which is exactly what the plug-in declares; the correlation is 0.906 |
| the impulse probe (an impulse through the plug-in) | the first copy of the impulse comes out at **2750 samples** and a second at 3006 — **1280 samples (26.7 ms) later than the declaration** — and the second copy is a feedback loop of one host block at a gain of 1.0 in the output path |

The two instruments agree on the **sign** — the declared latency under-reports — and
disagree on the **size**: 0 samples against 1280 samples.  The consequence for us is
the same either way: the declared latency is not the real one, and the output path of
the reference carries a 0 dB comb at the block rate that must not be inherited.

### 6.2 Reference B: the declared latency against the correlation probe

| instrument | what it says |
|---|---|
| the declaration of the plug-in, read out of its controller with the model loaded | **3993 samples**, 83.19 ms at 48 kHz |
| the cross-correlation probe of the shared host | **2015 samples** with a correlation of **0.001**; the same probe on the inert plug-in measures 1980 samples with a correlation of 0.000257 |

Here the disagreement is not about the size but about **the instrument**: a chain that
re-synthesises the signal cannot be measured by cross-correlation, because the output
is not the input any more.  The envelope of the output is the only honest probe for
such a chain.  So the probe is not wrong, it is the wrong instrument, and the two
numbers are not comparable at all.  Every latency number of reference B that comes
from a correlation is marked, and the declared figure is the one that is used.

## 7. The numbers, as constants

The machine-readable form is
[`qa/benchmark/reference_restoration.py`](../qa/benchmark/reference_restoration.py).
Every constant there carries the case it belongs to, the unit it is in and the way it
was measured, in the shape the benchmark already uses.

| constant | case | unit | what it is |
|---|---|---|---|
| `REFERENCE_VOICE_HISS_REMOVED_DB` | `voice_hiss` | dB | a partial the reference does not want, removed by this much |
| `REFERENCE_VOICE_HISS_KEPT_DB` | `voice_hiss` | dB | a partial the reference does want, moved by this much |
| `REFERENCE_UNTOUCHED_LEVEL_DB` | `voice_hiss` | dB | the level of the material the reference does not touch |
| `REFERENCE_MODE_BEST_REMOVED_DB` | `voice_hiss` | dB | its best mode, on our corpus: how much of the hiss under a voice it removes |
| `REFERENCE_MODE_BEST_KEPT_DB` | `voice_hiss` | dB | the same mode: how far the voice band moves |
| `REFERENCE_MODE_DEFAULT_REMOVED_DB` | `voice_hiss` | dB | its factory default, on the same case |
| `REFERENCE_MODE_DEFAULT_KEPT_DB` | `voice_hiss` | dB | the same setting: how far the voice band moves |
| `REFERENCE_MODE_REVERB_REMOVED_DB` | `reverb` | dB | the reverberant case |
| `REFERENCE_MODE_REVERB_KEPT_DB` | `reverb` | dB | the reverberant case |
| `REFERENCE_MODE_COST_MS_PER_512_FRAMES` | `*` | ms | what a block of 512 frames costs it, in the most expensive mode |
| `REFERENCE_MODE_COST_MS_PER_512_FRAMES_MIN` | `*` | ms | the same, in the cheapest mode |
| `REFERENCE_DECISION_MS` | `*` | ms | how long it takes to let a wanted voice through |
| `REFERENCE_DECISION_MS_OURS` | `*` | ms | the same, for our voice module |
| `REFERENCE_CHATTER_DB2` | `*` | dB^2 | how much its gain breathes while a voice speaks |
| `REFERENCE_CHATTER_DB2_OURS` | `*` | dB^2 | the same, for our voice module |
| `REFERENCE_SPLIT_ROUNDTRIP_DB` | `*` | dB | what its own band split costs the signal in rms |
| `REFERENCE_SPLIT_DELAY_SAMPLES` | `*` | samples | how many samples its band split delays |
| `REFERENCE_LATENCY_SAMPLES_48K` | `*` | samples | the latency it declares at 48 kHz |
| `REFERENCE_LATENCY_BLOCK_BASE_SAMPLES` | `*` | samples | the base of its rule: the declared latency is this plus the block size |
| `REFERENCE_LATENCY_MODEL_SAMPLES_48K` | `*` | samples | the latency it declares with its model loaded |
| `REFERENCE_DECISION_LAG_MS` | `*` | ms | the part of its opening that is not its latency |
| `REFERENCE_TAIL_SAMPLES` | `*` | samples | the tail it declares |
| `REFERENCE_TAIL_EXCESS_SAMPLES` | `*` | samples | how far its own sound runs past what it declares |
| `REFERENCE_STATE_COMPONENT_BYTES` | `*` | bytes | the state its component carries |
| `REFERENCE_STATE_CONTROLLER_BYTES` | `*` | bytes | the state its controller carries |
| `REFERENCE_SURFACE_PARAMETERS` | `*` | parameters | how many parameters it publishes |
| `REFERENCE_SURFACE_MODES` | `*` | modes | how many modes it really has |
| `REFERENCE_SURFACE_MODE_SLOTS` | `*` | slots | how many slots its mode selector offers |
| `REFERENCE_SURFACE_PROGRAM_LIST` | `*` | entries | how many entries its host preset list carries |
| `REFERENCE_MODELS_SHIPPED` | `*` | models | how many trained models it ships as data |
| `REFERENCE_MODEL_SIZE_BYTES_MIN` | `*` | bytes | the smallest of them, on disk |
| `REFERENCE_MODEL_SIZE_BYTES_MAX` | `*` | bytes | the largest of them, on disk |
| `REFERENCE_FREQUENCY_CURVE_P` | `*` | exponent | the law of its frequency control |
| `REFERENCE_CUTOFF_RANGE_LO_HZ` | `*` | Hz | the bottom of the range of that control |
| `REFERENCE_CUTOFF_RANGE_HI_HZ` | `*` | Hz | the top of it |
| `REFERENCE_GAIN_CURVE_P` | `*` | exponent | the law of its gain control |
| `REFERENCE_GAIN_RANGE_LO_DB` | `*` | dB | the bottom of the range of that control |
| `REFERENCE_GAIN_RANGE_HI_DB` | `*` | dB | the top of it |
| `REFERENCE_GAIN_DB_PER_PERCENT_AT_UNITY` | `*` | dB per 1 % | the resolution of that control at unity |
| `REFERENCE_PHASE_CORRELATION` | `*` | correlation | its phase does not survive the round trip |
| `REFERENCE_PHASE_CORRELATION_OURS` | `*` | correlation | ours does |
| `REFERENCE_BYPASS_BIT_EXACT` | `clean_music` | flag | the bypass hands the samples back untouched: one means the largest difference is exactly zero |
| `REFERENCE_BYPASS_MAX_DIFF` | `clean_music` | sample difference | the largest difference between the samples of the bypass and the samples of the input |
| `REFERENCE_BYPASS_DELAY_SAMPLES_256` | `*` | samples | the delay the bypassed path carries at 48 kHz with a block of 256 samples |
| `REFERENCE_BYPASS_DELAY_SAMPLES_512` | `*` | samples | the same, with a block of 512 samples |
| `REFERENCE_BYPASS_DELAY_SAMPLES_1024` | `*` | samples | the same, with a block of 1024 samples |
| `REFERENCE_BYPASS_DELAY_MS_AT_48K` | `*` | ms | the same delay in milliseconds at 48 kHz |
| `REFERENCE_DEFAULT_MAX_DIFF` | `clean_music` | sample difference | the largest difference between the samples of the default setting and the samples of the clean input |
| `REFERENCE_DEFAULT_MAX_DIFF_DB` | `clean_music` | dB | the same, below the peak of the input |
| `REFERENCE_DEFAULT_MAX_DIFF_24BIT_STEPS` | `clean_music` | steps of 24 bit | the same, in steps of a 24 bit word |
| `REFERENCE_DEFAULT_GAIN_DB` | `sine_probe` | dB | the static gain the default path applies to a steady tone |
| `REFERENCE_DEFAULT_GAIN_SPREAD_DB` | `sine_probe` | dB | how far that gain moves between the levels of the input |
| `REFERENCE_DEFAULT_GAIN_SD_DB` | `clean_music` | dB | the standard deviation of that gain over the material |
| `REFERENCE_DEFAULT_PRE_SIGNAL_NOISE_DBFS` | `clean_music` | dBFS | the floor the default path puts in front of the programme |
| `OWN_BYPASS_BIT_EXACT` | `clean_music` | flag | ours: the bypass hands the samples back untouched |
| `OWN_BYPASS_DELAY_SAMPLES` | `*` | samples | ours: the delay the bypassed path carries |
| `OWN_DEFAULT_BIT_EXACT_ON_CLEAN` | `clean_music` | flag | ours: the default setting leaves a clean signal exactly alone |
| `REFERENCE_HISS_REMOVED_DB_DEFAULT` | `hiss` | dB | the artefact of the hiss case, removed by the setting the reference comes with |
| `REFERENCE_PINK_HISS_REMOVED_DB_DEFAULT` | `pink_hiss` | dB | the artefact of the pink hiss case, removed by the setting the reference comes with |
| `REFERENCE_HUM50_REMOVED_DB_DEFAULT` | `hum50` | dB | the artefact of the hum case, removed by the setting the reference comes with |
| `REFERENCE_RUMBLE_REMOVED_DB_DEFAULT` | `rumble` | dB | the artefact of the rumble case, removed by the setting the reference comes with |
| `REFERENCE_VOICE_HISS_REMOVED_DB_DEFAULT` | `voice_hiss` | dB | the artefact of the voice under hiss case, removed by the setting the reference comes with |
| `REFERENCE_PLOSIVE_REMOVED_DB_DEFAULT` | `plosive` | dB | the artefact of the plosive case, removed by the setting the reference comes with |
| `REFERENCE_HISS_KEPT_DB_DEFAULT` | `hiss` | dB | the wanted content of the hiss case, moved by the setting the reference comes with |
| `REFERENCE_PINK_HISS_KEPT_DB_DEFAULT` | `pink_hiss` | dB | the wanted content of the pink hiss case, moved by the setting the reference comes with |
| `REFERENCE_HUM50_KEPT_DB_DEFAULT` | `hum50` | dB | the wanted content of the hum case, moved by the setting the reference comes with |
| `REFERENCE_RUMBLE_KEPT_DB_DEFAULT` | `rumble` | dB | the wanted content of the rumble case, moved by the setting the reference comes with |
| `REFERENCE_VOICE_HISS_KEPT_DB_DEFAULT` | `voice_hiss` | dB | the wanted content of the voice under hiss case, moved by the setting the reference comes with |
| `REFERENCE_PLOSIVE_KEPT_DB_DEFAULT` | `plosive` | dB | the wanted content of the plosive case, moved by the setting the reference comes with |
| `REFERENCE_HISS_REMOVED_DB_BEST_FLOOR` | `hiss` | dB | the artefact of the hiss case, at the floor of the measurement |
| `REFERENCE_PINK_HISS_REMOVED_DB_BEST` | `pink_hiss` | dB | the artefact of the pink hiss case, removed by the best setting the reference offers |
| `REFERENCE_HUM50_REMOVED_DB_BEST` | `hum50` | dB | the artefact of the hum case, removed by the best setting the reference offers |
| `REFERENCE_RUMBLE_REMOVED_DB_BEST_FLOOR` | `rumble` | dB | the artefact of the rumble case, at the floor of the measurement |
| `REFERENCE_VOICE_HISS_REMOVED_DB_BEST_FLOOR` | `voice_hiss` | dB | the artefact of the voice under hiss case, at the floor of the measurement |
| `REFERENCE_PLOSIVE_REMOVED_DB_BEST` | `plosive` | dB | the artefact of the plosive case, removed by the best setting the reference offers |
| `REFERENCE_HISS_KEPT_DB_BEST` | `hiss` | dB | the wanted content of the hiss case, moved by the best setting the reference offers |
| `REFERENCE_PINK_HISS_KEPT_DB_BEST` | `pink_hiss` | dB | the wanted content of the pink hiss case, moved by the best setting the reference offers |
| `REFERENCE_HUM50_KEPT_DB_BEST_FLOOR` | `hum50` | dB | the wanted content of the hum case, at the floor of the measurement |
| `REFERENCE_RUMBLE_KEPT_DB_BEST_FLOOR` | `rumble` | dB | the wanted content of the rumble case, at the floor of the measurement |
| `REFERENCE_VOICE_HISS_KEPT_DB_BEST` | `voice_hiss` | dB | the wanted content of the voice under hiss case, moved by the best setting the reference offers |
| `REFERENCE_PLOSIVE_KEPT_DB_BEST` | `plosive` | dB | the wanted content of the plosive case, moved by the best setting the reference offers |
| `REFERENCE_OUTPUT_LEVEL_CHANGE_DB_HISS_DEFAULT` | `hiss` | dB | how far the level of the whole output moves, with the setting the reference comes with |
| `REFERENCE_OUTPUT_LEVEL_CHANGE_DB_PINK_HISS_DEFAULT` | `pink_hiss` | dB | how far the level of the whole output moves, with the setting the reference comes with |
| `REFERENCE_OUTPUT_LEVEL_CHANGE_DB_HUM50_DEFAULT` | `hum50` | dB | how far the level of the whole output moves, with the setting the reference comes with |
| `REFERENCE_OUTPUT_LEVEL_CHANGE_DB_RUMBLE_DEFAULT` | `rumble` | dB | how far the level of the whole output moves, with the setting the reference comes with |
| `REFERENCE_OUTPUT_LEVEL_CHANGE_DB_VOICE_HISS_DEFAULT` | `voice_hiss` | dB | how far the level of the whole output moves, with the setting the reference comes with |
| `REFERENCE_OUTPUT_LEVEL_CHANGE_DB_PLOSIVE_DEFAULT` | `plosive` | dB | how far the level of the whole output moves, with the setting the reference comes with |
| `REFERENCE_OUTPUT_LEVEL_CHANGE_DB_HISS_BEST` | `hiss` | dB | how far the level of the whole output moves, with the best setting the reference offers |
| `REFERENCE_OUTPUT_LEVEL_CHANGE_DB_PINK_HISS_BEST` | `pink_hiss` | dB | how far the level of the whole output moves, with the best setting the reference offers |
| `REFERENCE_OUTPUT_LEVEL_CHANGE_DB_HUM50_BEST` | `hum50` | dB | how far the level of the whole output moves, with the best setting the reference offers |
| `REFERENCE_OUTPUT_LEVEL_CHANGE_DB_RUMBLE_BEST` | `rumble` | dB | how far the level of the whole output moves, with the best setting the reference offers |
| `REFERENCE_OUTPUT_LEVEL_CHANGE_DB_VOICE_HISS_BEST` | `voice_hiss` | dB | how far the level of the whole output moves, with the best setting the reference offers |
| `REFERENCE_OUTPUT_LEVEL_CHANGE_DB_PLOSIVE_BEST` | `plosive` | dB | how far the level of the whole output moves, with the best setting the reference offers |
| `REFERENCE_OUTPUT_SILENT_SHARE_HISS_BEST` | `hiss` | fraction | the share of the output that is digital silence, with the best setting the reference offers |
| `REFERENCE_OUTPUT_SILENT_SHARE_PINK_HISS_BEST` | `pink_hiss` | fraction | the share of the output that is digital silence, with the best setting the reference offers |
| `REFERENCE_OUTPUT_SILENT_SHARE_HUM50_BEST` | `hum50` | fraction | the share of the output that is digital silence, with the best setting the reference offers |
| `REFERENCE_OUTPUT_SILENT_SHARE_RUMBLE_BEST` | `rumble` | fraction | the share of the output that is digital silence, with the best setting the reference offers |
| `REFERENCE_OUTPUT_SILENT_SHARE_VOICE_HISS_BEST` | `voice_hiss` | fraction | the share of the output that is digital silence, with the best setting the reference offers |
| `REFERENCE_OUTPUT_SILENT_SHARE_PLOSIVE_BEST` | `plosive` | fraction | the share of the output that is digital silence, with the best setting the reference offers |
| `REFERENCE_OUTPUT_SILENT_SHARE_DEFAULT` | `*` | fraction | the share of the output that is digital silence, with the setting the reference comes with |
| `REFERENCE_GAIN_VAR_DB2_HISS_DEFAULT` | `hiss` | dB^2 | how much the gain curve breathes in the artefact band, with the setting the reference comes with |
| `REFERENCE_GAIN_VAR_DB2_PINK_HISS_DEFAULT` | `pink_hiss` | dB^2 | how much the gain curve breathes in the artefact band, with the setting the reference comes with |
| `REFERENCE_GAIN_VAR_DB2_HUM50_DEFAULT` | `hum50` | dB^2 | how much the gain curve breathes in the artefact band, with the setting the reference comes with |
| `REFERENCE_GAIN_VAR_DB2_RUMBLE_DEFAULT` | `rumble` | dB^2 | how much the gain curve breathes in the artefact band, with the setting the reference comes with |
| `REFERENCE_GAIN_VAR_DB2_VOICE_HISS_DEFAULT` | `voice_hiss` | dB^2 | how much the gain curve breathes in the artefact band, with the setting the reference comes with |
| `REFERENCE_GAIN_VAR_DB2_PLOSIVE_DEFAULT` | `plosive` | dB^2 | how much the gain curve breathes in the artefact band, with the setting the reference comes with |
| `REFERENCE_GAIN_VAR_DB2_HISS_BEST` | `hiss` | dB^2 | how much the gain curve breathes in the artefact band, with the best setting the reference offers |
| `REFERENCE_GAIN_VAR_DB2_PINK_HISS_BEST` | `pink_hiss` | dB^2 | how much the gain curve breathes in the artefact band, with the best setting the reference offers |
| `REFERENCE_GAIN_VAR_DB2_VOICE_HISS_BEST` | `voice_hiss` | dB^2 | how much the gain curve breathes in the artefact band, with the best setting the reference offers |
| `REFERENCE_GAIN_VAR_DB2_PLOSIVE_BEST` | `plosive` | dB^2 | how much the gain curve breathes in the artefact band, with the best setting the reference offers |
| `REFERENCE_REPRODUCIBLE` | `*` | flag | whether the same setting on the same input gives the same waveform: one means bit identical |
| `REFERENCE_REPRODUCIBILITY_SPREAD_DB` | `*` | dB | how much the figures move between two runs of the same setting |
| `REFERENCE_REPRODUCIBLE_OURS` | `*` | flag | ours: whether our own modules give the same waveform twice |
| `REFERENCE_FIRST_CROSSOVER_DEFAULT_HZ` | `*` | Hz | the frequency of the first band edge of the reference, as it comes |
| `REFERENCE_LATENCY_SAMPLES_MIN` | `*` | samples | the smallest latency it declares over the settings of the study |
| `REFERENCE_LATENCY_SAMPLES_MAX` | `*` | samples | the largest latency it declares over the settings of the study |
| `REFERENCE_HISS_REMOVED_DB_OURS` | `hiss` | dB | ours: the artefact of the hiss case, removed by our own module |
| `REFERENCE_PINK_HISS_REMOVED_DB_OURS` | `pink_hiss` | dB | ours: the artefact of the pink hiss case, removed by our own module |
| `REFERENCE_HUM50_REMOVED_DB_OURS` | `hum50` | dB | ours: the artefact of the hum case, removed by our own module |
| `REFERENCE_RUMBLE_REMOVED_DB_OURS` | `rumble` | dB | ours: the artefact of the rumble case, removed by our own module |
| `REFERENCE_VOICE_HISS_REMOVED_DB_OURS` | `voice_hiss` | dB | ours: the artefact of the voice under hiss case, removed by our own module |
| `REFERENCE_HISS_KEPT_DB_OURS` | `hiss` | dB | ours: the wanted content of the hiss case, moved by our own module |
| `REFERENCE_PINK_HISS_KEPT_DB_OURS` | `pink_hiss` | dB | ours: the wanted content of the pink hiss case, moved by our own module |
| `REFERENCE_HUM50_KEPT_DB_OURS` | `hum50` | dB | ours: the wanted content of the hum case, moved by our own module |
| `REFERENCE_RUMBLE_KEPT_DB_OURS` | `rumble` | dB | ours: the wanted content of the rumble case, moved by our own module |
| `REFERENCE_VOICE_HISS_KEPT_DB_OURS` | `voice_hiss` | dB | ours: the wanted content of the voice under hiss case, moved by our own module |
| `REFERENCE_HISS_REMOVED_DB_DEFAULT` | `hiss` | dB | the white bed, at the setting the reference comes with: how much of it goes |
| `REFERENCE_PINK_HISS_REMOVED_DB_DEFAULT` | `pink_hiss` | dB | the pink bed, at the same setting |
| `REFERENCE_HUM50_REMOVED_DB_DEFAULT` | `hum50` | dB | the hum, at the same setting |
| `REFERENCE_RUMBLE_REMOVED_DB_DEFAULT` | `rumble` | dB | the rumble, at the same setting |
| `REFERENCE_VOICE_HISS_REMOVED_DB_DEFAULT` | `voice_hiss` | dB | the hiss under the voice, at the same setting |
| `REFERENCE_PLOSIVE_REMOVED_DB_DEFAULT` | `plosive` | dB | the burst of the plosive, at the same setting |
| `REFERENCE_HISS_KEPT_DB_DEFAULT` | `hiss` | dB | the wanted content of the white bed, at the same setting: the bed carries none |
| `REFERENCE_PINK_HISS_KEPT_DB_DEFAULT` | `pink_hiss` | dB | the wanted content of the pink bed, at the same setting |
| `REFERENCE_HUM50_KEPT_DB_DEFAULT` | `hum50` | dB | the wanted tone of the hum case, at the same setting: it sits on the first band edge of the reference |
| `REFERENCE_RUMBLE_KEPT_DB_DEFAULT` | `rumble` | dB | the wanted programme of the rumble case, at the same setting: it sits on the first band edge of the reference |
| `REFERENCE_VOICE_HISS_KEPT_DB_DEFAULT` | `voice_hiss` | dB | the wanted voice under the hiss, at the same setting |
| `REFERENCE_PLOSIVE_KEPT_DB_DEFAULT` | `plosive` | dB | the wanted vowel of the plosive case, at the same setting |
| `REFERENCE_HISS_REMOVED_DB_BEST_FLOOR` | `hiss` | dB | **a floor of the measurement**, at the best setting the reference offers: the band of the output is exactly zero |
| `REFERENCE_PINK_HISS_REMOVED_DB_BEST` | `pink_hiss` | dB | the pink bed, at the best setting: a real removal |
| `REFERENCE_HUM50_REMOVED_DB_BEST` | `hum50` | dB | the hum, at the best setting: a real removal, bought with the wanted tone |
| `REFERENCE_RUMBLE_REMOVED_DB_BEST_FLOOR` | `rumble` | dB | **a floor of the measurement**, at the best setting: the band of the output is exactly zero |
| `REFERENCE_VOICE_HISS_REMOVED_DB_BEST_FLOOR` | `voice_hiss` | dB | **a floor of the measurement**, at the best setting: the band of the output is exactly zero |
| `REFERENCE_PLOSIVE_REMOVED_DB_BEST` | `plosive` | dB | the burst of the plosive, at the best setting: a real removal |
| `REFERENCE_HISS_KEPT_DB_BEST` | `hiss` | dB | the wanted content of the white bed, at the best setting |
| `REFERENCE_PINK_HISS_KEPT_DB_BEST` | `pink_hiss` | dB | the wanted content of the pink bed, at the best setting |
| `REFERENCE_HUM50_KEPT_DB_BEST_FLOOR` | `hum50` | dB | **a floor of the measurement**, at the best setting: the wanted tone is not moved, it is gone |
| `REFERENCE_RUMBLE_KEPT_DB_BEST_FLOOR` | `rumble` | dB | **a floor of the measurement**, at the best setting: the wanted programme is not moved, it is gone |
| `REFERENCE_VOICE_HISS_KEPT_DB_BEST` | `voice_hiss` | dB | the wanted voice under the hiss, at the best setting |
| `REFERENCE_PLOSIVE_KEPT_DB_BEST` | `plosive` | dB | the wanted vowel of the plosive case, at the best setting |
| `REFERENCE_OUTPUT_LEVEL_CHANGE_DB_HISS_DEFAULT` | `hiss` | dB | the level of the whole output against the level of the input, at the setting the reference comes with |
| `REFERENCE_OUTPUT_LEVEL_CHANGE_DB_PINK_HISS_DEFAULT` | `pink_hiss` | dB | the same, on the pink bed |
| `REFERENCE_OUTPUT_LEVEL_CHANGE_DB_HUM50_DEFAULT` | `hum50` | dB | the same, on the hum case |
| `REFERENCE_OUTPUT_LEVEL_CHANGE_DB_RUMBLE_DEFAULT` | `rumble` | dB | the same, on the rumble case |
| `REFERENCE_OUTPUT_LEVEL_CHANGE_DB_VOICE_HISS_DEFAULT` | `voice_hiss` | dB | the same, on the voice case |
| `REFERENCE_OUTPUT_LEVEL_CHANGE_DB_PLOSIVE_DEFAULT` | `plosive` | dB | the same, on the plosive case |
| `REFERENCE_OUTPUT_LEVEL_CHANGE_DB_HISS_BEST` | `hiss` | dB | the same, at the best setting: the output is 38 dB below the input |
| `REFERENCE_OUTPUT_LEVEL_CHANGE_DB_PINK_HISS_BEST` | `pink_hiss` | dB | the same, at the best setting |
| `REFERENCE_OUTPUT_LEVEL_CHANGE_DB_HUM50_BEST` | `hum50` | dB | the same, at the best setting: the output is 62 dB below the input |
| `REFERENCE_OUTPUT_LEVEL_CHANGE_DB_RUMBLE_BEST` | `rumble` | dB | the same, at the best setting |
| `REFERENCE_OUTPUT_LEVEL_CHANGE_DB_VOICE_HISS_BEST` | `voice_hiss` | dB | the same, at the best setting: the output keeps its level |
| `REFERENCE_OUTPUT_LEVEL_CHANGE_DB_PLOSIVE_BEST` | `plosive` | dB | the same, at the best setting |
| `REFERENCE_OUTPUT_SILENT_SHARE_HISS_BEST` | `hiss` | fraction | the share of the output that is digital silence, at the best setting: 81 per cent |
| `REFERENCE_OUTPUT_SILENT_SHARE_PINK_HISS_BEST` | `pink_hiss` | fraction | the same, at the best setting: 86 per cent |
| `REFERENCE_OUTPUT_SILENT_SHARE_HUM50_BEST` | `hum50` | fraction | the same, at the best setting: 86 per cent |
| `REFERENCE_OUTPUT_SILENT_SHARE_RUMBLE_BEST` | `rumble` | fraction | the same, at the best setting: every sample is not silent |
| `REFERENCE_OUTPUT_SILENT_SHARE_VOICE_HISS_BEST` | `voice_hiss` | fraction | the same, at the best setting: every sample is not silent |
| `REFERENCE_OUTPUT_SILENT_SHARE_PLOSIVE_BEST` | `plosive` | fraction | the same, at the best setting: every sample is not silent |
| `REFERENCE_OUTPUT_SILENT_SHARE_DEFAULT` | `*` | fraction | the same, at the setting the reference comes with: every sample of every case is not silent |
| `REFERENCE_GAIN_VAR_DB2_HISS_DEFAULT` | `hiss` | dB^2 | how much the gain curve breathes on the white bed, at the setting the reference comes with |
| `REFERENCE_GAIN_VAR_DB2_PINK_HISS_DEFAULT` | `pink_hiss` | dB^2 | the same, on the pink bed |
| `REFERENCE_GAIN_VAR_DB2_HUM50_DEFAULT` | `hum50` | dB^2 | the same, on the hum case |
| `REFERENCE_GAIN_VAR_DB2_RUMBLE_DEFAULT` | `rumble` | dB^2 | the same, on the rumble case |
| `REFERENCE_GAIN_VAR_DB2_VOICE_HISS_DEFAULT` | `voice_hiss` | dB^2 | the same, on the voice case |
| `REFERENCE_GAIN_VAR_DB2_PLOSIVE_DEFAULT` | `plosive` | dB^2 | the same, on the plosive case: five times ours |
| `REFERENCE_GAIN_VAR_DB2_HISS_BEST` | `hiss` | dB^2 | the same, at the best setting |
| `REFERENCE_GAIN_VAR_DB2_PINK_HISS_BEST` | `pink_hiss` | dB^2 | the same, at the best setting |
| `REFERENCE_GAIN_VAR_DB2_VOICE_HISS_BEST` | `voice_hiss` | dB^2 | the same, at the best setting |
| `REFERENCE_GAIN_VAR_DB2_PLOSIVE_BEST` | `plosive` | dB^2 | the same, at the best setting |
| `REFERENCE_REPRODUCIBLE` | `*` | flag | whether the same setting gives the same waveform: it does not |
| `REFERENCE_REPRODUCIBILITY_SPREAD_DB` | `*` | dB | how much the figures move between two runs of the same setting |
| `REFERENCE_REPRODUCIBLE_OURS` | `*` | flag | ours: two runs are bit identical |
| `REFERENCE_FIRST_CROSSOVER_DEFAULT_HZ` | `*` | Hz | the frequency of the first band edge of the reference, as it comes |
| `REFERENCE_LATENCY_SAMPLES_MIN` | `*` | samples | the smallest latency the reference declares over the settings of the table |
| `REFERENCE_LATENCY_SAMPLES_MAX` | `*` | samples | the largest one |
| `REFERENCE_HISS_REMOVED_DB_OURS` | `hiss` | dB | our side of the white bed: how much of it we remove |
| `REFERENCE_PINK_HISS_REMOVED_DB_OURS` | `pink_hiss` | dB | our side of the pink bed |
| `REFERENCE_HUM50_REMOVED_DB_OURS` | `hum50` | dB | our side of the hum case |
| `REFERENCE_RUMBLE_REMOVED_DB_OURS` | `rumble` | dB | our side of the rumble case |
| `REFERENCE_VOICE_HISS_REMOVED_DB_OURS` | `voice_hiss` | dB | our side of the voice case |
| `REFERENCE_HISS_KEPT_DB_OURS` | `hiss` | dB | our side of the wanted content of the white bed |
| `REFERENCE_PINK_HISS_KEPT_DB_OURS` | `pink_hiss` | dB | our side of the wanted content of the pink bed |
| `REFERENCE_HUM50_KEPT_DB_OURS` | `hum50` | dB | our side of the wanted tone of the hum case |
| `REFERENCE_RUMBLE_KEPT_DB_OURS` | `rumble` | dB | our side of the wanted programme of the rumble case |
| `REFERENCE_VOICE_HISS_KEPT_DB_OURS` | `voice_hiss` | dB | our side of the wanted voice under the hiss |

The case `*` means "every case": the cost, the latency, the surface and the laws of a
product are properties of the product, not of the material it is measured on.

### 7.1 The corpus head-to-head, and the floors in it

The corpus was rendered through reference B with the ruler of this project, at the
setting the reference comes with and at the best setting it offers, and the table of
the study was read with the same instrument our own modules are read with.

**The removal figures at the best setting are floors, and the constants say so in
their names.**  Five of them carry `FLOOR` in the name:

| constant | the figure | what the output is |
|---|---|---|
| `REFERENCE_HISS_REMOVED_DB_BEST_FLOOR` | 269.29 dB | 38.36 dB below the input, and **81.2 % of it is digital silence** |
| `REFERENCE_RUMBLE_REMOVED_DB_BEST_FLOOR` | 302.38 dB | 28.12 dB below the input, every sample not silent: a real removal, with the wanted programme gone |
| `REFERENCE_VOICE_HISS_REMOVED_DB_BEST_FLOOR` | 296.47 dB | 0.41 dB below the input, every sample not silent: a real removal of the hiss under the voice |
| `REFERENCE_HUM50_KEPT_DB_BEST_FLOOR` | 232.37 dB | the wanted tone of the case is not moved, it is **gone** |
| `REFERENCE_RUMBLE_KEPT_DB_BEST_FLOOR` | 232.45 dB | the wanted programme of the case is not moved, it is **gone** |

A band whose level is exactly zero gives an infinite number of decibels, and the
instrument prints the floor of its own range.  **A benchmark that reports 269 dB of
removal without saying that the output was digital silence is worse than no
benchmark**, so the floor is in the name of the constant, in the comment above it and
in the method of the record.  The figures to read them with are the level of the
whole output and the share of it that is silence:
`REFERENCE_OUTPUT_LEVEL_CHANGE_DB_*` and `REFERENCE_OUTPUT_SILENT_SHARE_*`.

**At the setting the reference comes with, the reference is not ahead of us.**  It
removes 14.08 to 14.13 dB of the noise beds and moves their wanted content by
0.00 dB — a tie with our 13.54 dB and 17.30 dB.  On the hum case and on the rumble
case it moves the wanted partial by **13.31 dB and 16.18 dB**, where we move it by
**0.04 dB and 0.03 dB**.  The reason is in `REFERENCE_FIRST_CROSSOVER_DEFAULT_HZ`:
the first band edge of the reference comes at 1000 Hz, and the wanted partial of
those two cases sits at 1000 Hz, so the partial is split between two bands and its
level after the split is not its level before it.  That is a configuration accident
and not a defect, and it is the whole of the 13 to 16 dB.

**The reference is not bit-deterministic.**  `REFERENCE_REPRODUCIBLE` is false: the
same setting on the same input gives another waveform, and the figures move by up to
`REFERENCE_REPRODUCIBILITY_SPREAD_DB` (0.8 dB) between two runs.  Ours is bit
identical between two runs (`REFERENCE_REPRODUCIBLE_OURS`).

**The one case where the reference is simply better is the voice.**  At its best
setting it keeps the level of the output (−0.41 dB), silences the hiss band under
the voice completely and moves the voice partials by 1.28 dB.  A restoration network
gets the noise floor out from under a voice where a spectral gate cannot.  **That is
the gap worth chasing: not the decibels on a noise bed, but the noise floor under a
voice.**

## 8. The files

| file | what it is |
|---|---|
| [`qa/benchmark/reference_restoration.py`](../qa/benchmark/reference_restoration.py) | the numbers of this document, as constants, with their case, their unit and the way they were measured |
| [`qa/benchmark/reference_model.py`](../qa/benchmark/reference_model.py) | the anatomy of a learned model and the arithmetic of §3.2, as constants, the same way |
| [`docs/MODEL_REFERENCE.md`](MODEL_REFERENCE.md) | what a model is in this product, what it is made of, and what it costs at each granularity |
| [`qa/benchmark/reference.py`](../qa/benchmark/reference.py) | the signal numbers the benchmark compares our modules against |
| [`qa/benchmark/reference_surface.py`](../qa/benchmark/reference_surface.py) | the surfaces of the references and of ours, the same way |
| [`qa/benchmark/README.md`](../qa/benchmark/README.md) | the benchmark itself |
| [`docs/BENCHMARK_NOTES.md`](BENCHMARK_NOTES.md) | the design note of the benchmark |
| [`qa/survey/REPORT.md`](../qa/survey/REPORT.md) | the module survey: what every module of ours does to a signal, and what it costs |
| [`tests/test_safe_defaults.py`](../tests/test_safe_defaults.py) | the gate of §3.3: the bypass of every module is sample exact and carries no delay, and the default of a repair module leaves a clean signal alone |
| [`qa/performance/REPORT.md`](../qa/performance/REPORT.md) | the cost of every module, in transform equivalents |
| [`qa/quality/REPORT.md`](../qa/quality/REPORT.md) | the quality rules, and the cases they are written against |
