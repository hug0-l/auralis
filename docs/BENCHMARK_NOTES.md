# Auralis — Benchmark Notes

> Status: **alpha (`0.1.0a0`)**. This note records a study of two commercial
> restoration products: what was measured, what the numbers are, what the product
> changed as a result, what is still missing, and what has to be measured next.
>
> **No third party name appears in this file.** The rule in
> [`../CONTRACT.md`](../CONTRACT.md) is absolute, and the study was deliberately kept
> outside this tree for exactly that reason: the two products are called **the first
> measured product** and **the second measured product** here, and their controls are
> described by what they do, in our vocabulary.
>
> **Every number carries its source.** The files of the study live outside this tree;
> they are given as paths relative to the study root, which holds one folder per
> target (`target-a/`, `target-b/`) and one shared folder (`common/`). The full
> provenance table is in section 8.
>
> The permanent instrument that keeps the gap closed is
> [`../qa/benchmark/`](../qa/benchmark/): it measures our modules against the numbers
> recorded here. The full story of that instrument is in its
> [`README.md`](../qa/benchmark/README.md).
>
> The **adaptive behaviour** of the second measured product — a relative
> threshold, a re-estimate when the character of the material changes, and a
> state that belongs to the session — is written down in
> [`ADAPTIVE_BEHAVIOUR.md`](ADAPTIVE_BEHAVIOUR.md), together with the defect of
> ours it found: our threshold is not level invariant yet.

---

## 1. What was measured

### 1.1 The instrument is ours

The study set out to measure the two foreign products the way we measure our own
modules — with the corpus, the rules and the harness of this product:

| What | Where it lives | What it is |
|---|---|---|
| the corpus | `corpus/audio/test/`, `corpus/manifest.json` | 16 clips of 48 kHz material, with the ground truth that says what each clip contains |
| the rules | `qa/quality/quality_rules.py`, `qa/qualityvoice/rules.py` | what must be removed, what must be kept, how much the gain curve may vary |
| the harness | `qa/vst3host/` | an independent host, written against the plug-in SDK, that loads a plug-in the way a DAW does and reports what it saw |

**The harness is the part that has been used on the two foreign products so far.** The
study ran a copy of it outside this tree, and the JSON reports of the foreign numbers
below name it (`"host": "auralis-vst3host"`), so both foreign products were measured
with the instrument that measures ours — the same five sample rates (44100, 48000,
88200, 96000, 192000) and the same eight block sizes (16, 32, 64, 128, 256, 512,
1024, 4096) were offered to both. The corpus and the rules are the part that is
still pending, and section 5 says what is missing because of it.

**What the instrument has not done yet.** The harness of the product renders silence
and a 1 kHz tone; the copy of it in the study has grown a file mode, and the corpus
cases are being rendered through it now. What is still missing is the **tabulation**:
the same corpus, the same rules and the same metric names, measured on both sides
and written into the reference table of the benchmark. That is the subject of
section 7.

### 1.2 The first measured product — the one that picks an algorithm

A single installer executable of 244,817,727 bytes (234 MiB). It was never run and
nothing was installed on the machine.

The installer is itself a PE image — a 949,760-byte setup loader with 11 sections —
and the payload sits inside it. A scan of the file found **11 embedded PE images**,
of which **238,416,896 bytes** are inside them and **6,400,831 bytes** are not. The
payload was recovered **without installing anything**, by carving:

| What was recovered | Size |
|---|---|
| the plug-in of the format we ship (VST3) | 7,976,448 bytes, x64, 7 sections, maximum section entropy 6.4590 |
| the plug-in of a second desktop format (AAX) | 7,767,552 bytes, x64, 7 sections, maximum section entropy 6.4517 |
| a helper that enumerates the formats the installer lays down and the CPU features it asks for | 7,168 bytes, 15 exports |
| seven further format binaries | 15.43 MiB to 43.27 MiB each |
| seven preset documents | 795 bytes to 1.5 MB; the largest carries its 16 values beside 11,766 lines of binary payload |

The carve of the compressed blocks is itself a result: **18 candidate blocks** were
located and **all 18 failed** to decompress with the standard methods (lzma in four
forms, zlib), so the payload was recovered by carving the block boundaries directly —
18 files, 243,867,895 bytes.

### 1.3 The second measured product — the one that splits into three stems

A plug-in bundle of two files: a **260,096-byte** loader (254.0 KiB) and a
**25,308,728-byte** payload (24.14 MiB). The payload is **97.31 times** the loader.

**The payload is protected, and it cannot be read statically.** This is a measured
result, not a guess:

| Measurement | Value |
|---|---|
| the largest raw section of the payload | 25,292,288 bytes at an entropy of **7.950** |
| 64 KiB blocks of the payload at an entropy of 7.9 or more | **296 of 387** |
| virtual bytes with no data on disk (built at run time) | **34,898,875** |
| decompression attempts (zlib, raw deflate, gzip, lzma alone, lzma raw) | **5, all failed** |
| candidate deflate streams in the section that inflate to more than 256 bytes | **0** |

The consequence is stated once and for all: **the algorithms of the second measured
product are unknown.** Only its behaviour is measurable, and that is what section 3
reports.

### 1.4 The two names the tree uses

The permanent benchmark does not name the two products either. It calls them by
their shape, and those are the names to use in this tree:

| In this note | In `qa/benchmark/reference.py` | What it does |
|---|---|---|
| the first measured product | `band_restoration` | picks an algorithm and gives it four bands |
| the second measured product | `stem_separation` | splits a recording into three stems and gives each of them a gain |

---

## 2. The surfaces, measured with one instrument

Both foreign surfaces were read out of their controllers with the same host, and our
own plug-ins were read with it as well, so every number in this section was produced
by the same code on the same machine. For every continuous control, a straight line, a
straight line in the logarithm and a power law were each fitted over 41 sampled
points of the travel; a fit counts as linear when its largest error is under 1 % of
the span of the control.

### 2.1 The table

| | the second measured product | the first measured product | ours |
|---|---|---|---|
| parameters in one plug-in | 17 | 22 | 1 … 25 (295 in all, over 51 plug-ins) |
| kinds | 3 continuous, 14 stepped | 11 continuous, 11 stepped | 193 float, 54 bool, 43 enum, 5 int |
| modes | 6 host presets, 12 switches, 3 global switches | 7 algorithms (of 16 slots), 2 band-split modes, 4 band solo/mute | 43 enums, 151 choices |
| parameters that carry a unit | **0 of 17** | **0 of 22** | 109 of 295 |
| groups in the format | none | none | a column, 67 groups |
| the plain value a host stores | the normalised position, 0…1 | the normalised position, 0…1 | **the natural value** (dB, Hz, ms) |
| parameters that can be automated | **5 of 17** | 21 of 22 | **295 of 295** |
| a bypass control | yes | yes | **yes** (one per plug-in) |
| a host preset list (program list) | 6 entries | 1 entry | **one unit and one program list** per plug-in |
| the response curve of a control | a power law | a power law | a straight line in 245 of the 295; 27 `log`, 23 `taper` |
| short titles | cut to 8 characters | cut to 8 characters | the full name |
| a sentence of help per parameter | none | 2 sentences | **every one of the 295** |

The **ours** column counts the canonical table of the project
(`aurcore/schema/params.tsv`, read through `aurcore/schema/modules.json`):
**51 modules and 295 parameters** at the time of writing (2026-10-21, 23:29).
The tree is still growing, so these counts move with it; the two foreign
columns do not move at all.

### 2.2 The first measured product — four bands and an algorithm

22 parameters, 21 of them automatable, 11 continuous and 11 stepped; one unit and one
program list that holds a single program.

| What the control does | How many | Range | Default |
|---|---|---|---|
| switches the module out of the path | 1 | on / off | off |
| trims the level of the incoming signal | 1 | −24.0 … +24.0 dB | 0.0 dB |
| trims the level of the outgoing signal | 1 | −24.0 … +24.0 dB | 0.0 dB |
| mixes the processed signal with the untouched one | 1 | 0.00 … 100.00 % | 50.00 % |
| chooses the restoration algorithm | 1 | **16 slots, of which 7 carry an algorithm and 9 report that they carry none** | the first slot |
| trims the level of one band | 4 | −100.00 … +100.00 | 0.00 |
| sets the cut-off between two bands | 3 | 100.0 … 10000.0 Hz | 1000.0 / 3000.1 / 4999.9 Hz |
| says how many bands take part | 1 | 1 … 4 | 1 |
| solos or mutes one band | 4 | none / solo / mute | none |
| chooses a fast band split or a linear-phase one | 1 | two states | the fast one |
| switches one band out of the processing | 4 | on / off | off |

Two further facts about the surface:

- The parameter catalogue is clean: **0 problems, 0 text round-trip failures, 0 plain
  round-trip failures, 0 duplicate identifiers** over the 22 parameters.
- The shipped presets store **16 of the 22 parameters**. The algorithm, the band-split
  mode and the four per-band bypass switches are not stored with a preset.

### 2.3 The second measured product — three stems

17 parameters, 5 of them automatable, 3 continuous and 14 stepped; one unit and one
program list that holds **6 programs**.

| What the control does | How many | Range | Default |
|---|---|---|---|
| sets the level of one stem | 3 | −60.0 … +12.0 dB | 0.0 dB |
| solos, mutes or isolates the difference of one stem | 9 | on / off | off |
| collapses the signal to one channel | 1 | on / off | off |
| asks the product to spend less computation | 1 | on / off | off |
| asks the product for a smaller build | 1 | on / off | off |
| switches the module out of the path | 1 | on / off | off |
| chooses a program | 1 | 6 programs | the first |

The three stems are the room, the voice and the reverberant part of the voice. Each
of them has a level and its own solo, mute and difference switch, which is the shape
our separation family already has. The level law is not a straight line: it is the
power law `dB = −59.89 + 71.90·n^0.264`, with a largest error of **0.11 dB** over the
41 points, so unity sits exactly in the middle of the travel and one percent of the
travel is **0.316 dB** there.

### 2.4 Where the foreign surface is better, by how much

Two rows of the table above are gaps, and both are measured on the same control:

| | the foreign control | ours | the gap |
|---|---|---|---|
| a frequency control over 100 Hz … 10 kHz | a power law, `Hz = 100.24 + 9900.36·n^3.334`, largest error 0.60 Hz | a straight line in hertz | **75.8 times finer at 100 Hz**, 3.1 times finer at 1 kHz, 1.8 times *coarser* at 10 kHz |
| a gain control | a power law, unity in the middle, 0.316 dB per 1 % of travel | a straight line: 0.360 dB per 1 % (equaliser), 0.480 (stems), 0.600 (low gain), 0.840 (the gain module, whose unity sits at 71.4 % of the travel) | **1.14 times finer at its best, 2.66 times finer at our worst** |

The frequency number is the one that matters: on our own band-frequency control, one
percent of the travel changes the frequency by **1.5840 octaves at 100 Hz** — the
control doubles the frequency for a one percent move — and by 0.0285 octaves at
10 kHz. Our control is 55 times finer at the top than at the bottom, and useless
where a 100 Hz region has to be set.

---

## 3. The behaviour, as measured

### 3.1 The second measured product

| Measurement | Value | Source |
|---|---|---|
| latency it declares | **1470 samples** | `target-b/agent2.3/out/report.json` |
| latency the sweep measures | **1470 samples** (correlation 0.905927, excess 0) | same |
| tail it declares | **0 samples** | same |
| tail the sweep measures | **1470 samples** at a level of **−62.485802 dB**, and the ring-out is not a decay (fall 0 dB) | same |
| state of the component | **716 bytes**, and it moves with the parameter | same |
| state of the controller | **0 bytes**, and it does **not** move with the parameter | same |
| parameters that change the sound | **7 of 17** | same |
| a 1 kHz tone at −6 dBFS in | out at a peak of **7.68448626332e-06**, rms **2.33189121374e-06** — that is 20·log10(7.684486e-06 / 0.5) = **−96.3 dB** | same |
| total time of the whole run | 2650 ms (component 575, setup 113, processing 2, agreement 1694, latency 223) | same |

Driven by a probe that activates the buses, the same binary writes every sample and
stays finite on every case of the study: **unwritten 0, finite yes, 1470 samples of
latency, 0 of tail, and as many frames out as in** on a hiss bed, a noise bed, a
1 kHz tone, an impulse, a chirp, a sweep, a music mix and a voice under hiss
(`agent3.9/out/b/*.txt`).

Three readings of that table matter to us:

- **The tone is removed entirely.** A pure 1 kHz tone is neither the room nor the
  voice, so the product takes it away down to −96.3 dB. Our rules ask for the
  opposite: the wanted part has to stay. A separator that decides by *category* and a
  restorer that decides by *wanted / unwanted* are two different products, and the
  difference shows up in the first second of listening.
- **The declaration and the reality agree on the latency and disagree on the tail.**
  The 1470 samples of latency are declared honestly; the 1470 samples of ring-out
  are not declared at all. Our core declares a tail next to the latency, and the
  chain takes the maximum over its modules.
- **The controller holds nothing.** The parameters come back through the component
  and never through the controller. Our parameters live in the name-keyed registry
  of the core, so this is a shape we already do not have.

### 3.2 The first measured product

| Measurement | Value | Source |
|---|---|---|
| the message the host sends on the connection point | **refused** — the channel is closed to the host | `target-a/a216/out/host_a.json`, `a34/work/out.log.txt` |
| the catalogue | 22 parameters, **0 problems, 0 round-trip failures, 0 duplicate identifiers** | `target-a/a216/out/host_a.json` |
| the first driver of the study | **no output at all** — it stops right after the buses are activated | `a310/A_default.json` |
| the host build with a file mode | renders the corpus cases: 96,000 frames, a peak of 32768, 383,892 of 384,000 samples non-zero | `a34/work/sweep_a2/out.wav` |
| the release of the module | **the module faulted while it was being released** | `target-a/a216/out/host_a.json` |

### 3.3 What the harness itself can and cannot say

This is the part that has to be read carefully, because the harness of the study is
not the harness of the product.

In the build of the study's host that does **not** activate the buses, every module
leaves its output buffer untouched: the report says *512 output samples were not
written*, the peak of the unwritten buffer reads 1.0e+30, the latency sweep
measures 1980 samples with a correlation of 0.000257 and the ring-out reads 16384
samples at 606.020600 dB. **Our own plug-in shows exactly the same four numbers in
the same build** (`reports/host_TARGET-A.json`, `reports/host_TARGET-B.json`,
`reports/host_OURS.json`).

So those four numbers are a property of that harness build, not a verdict on the two
foreign products, and this note does not use them as one. What they do show is that
the study's instrument had a defect of its own, that the defect was found by reading
the same report of our own plug-in, and that the second product is clean once the
buses are activated.

### 3.4 The real time behaviour of the second measured product

A real time run: a minute of audio through a host that keeps the clock against the
blocks, 48 kHz, 256 frame blocks, the corpus clip with a voice under a hiss bed.

| Measurement | Value | Source |
|---|---|---|
| the delay it declares and the delay it really has | **1470 samples = 30.625 ms**, the same at every impulse of the run | `target-b/agent2.12/final.txt` |
| the delay at other block sizes | 1534 samples at 64 frames, 1470 at 128, 1726 at 512, 2238 at 1024, 5310 at 4096: above 256 frames the delay is 1214 samples plus the block size of the host | `target-b/agent2.12/extra.txt` |
| the latency from a parameter change to the changed audio | **0 samples = 0.000 ms** — the change is heard in the very block it is handed over | `target-b/agent2.12/final.txt` |
| the step a parameter change makes in the output | **0.0805** (about −22 dBFS), where the run without the change moves 0.027: the change is a step, not a ramp | same |
| underruns in a minute (a block not finished before its deadline) | **1486** of 11 250 blocks, the worst overrun 313 ms | `target-b/agent2.12/minute_*.json` |
| the total overrun inside that minute | **30.5 s** | same |
| the wall clock for the minute of audio | 60.2 s | same |
| the cost of a 256 frame block | median 1917 µs, mean 4836 µs, p99 57.8 ms: about 7 µs a sample, in bursts of 384 samples (8 ms) — this is the same product as the cost of the benchmark, measured in real time on the study machine, so it is the wall-clock figure of a loaded machine and not the yardstick figure (306.7 to 426.7 µs per 64 sample block, 4.8 to 6.7 µs a sample) | same |
| the same host on our own module | 0 samples of delay and 1 underrun in a minute, but the module does not engage its engine there and passes the audio through bit for bit: the number is of a pass-through | same |
| our own preview path | the change is heard after **22.0 ms**, **0 underruns** over 60 s | `docs/ALPHA_STATUS.md` |

These four numbers are constants of the benchmark now, in the series of the second
measured product (`stem_separation`), and they are properties of the **product in a
host** and not of a clip of the corpus, so they are recorded once, under the name that
means "every case": `latency_samples` (1470), `realtime_underruns_per_min` (1486),
`change_latency_ms` (0.0) and `change_step` (0.0805) in
[`qa/benchmark/reference.py`](../qa/benchmark/reference.py).

What we take from it:

* **Smooth every parameter the user moves.** The reference applies a new value to the
  whole of the material waiting in its delay line, so the user hears a step of
  −22 dBFS where our own path is smooth. A one pole on the parameters of the user
  interface, with a time constant of a few milliseconds, is the cheap way to turn a
  change into a fade.
* **Keep the block size out of the delay.** The reference delay grows with the block
  size of the host (1214 samples plus the block), so a host that asks for 1024 frames
  pays 46.6 ms. A module that keeps a delay line has to keep it as short as the job
  allows and to declare the same delay at every block size.
* **Count the underruns.** The number of blocks that were not finished before their
  deadline is the number of glitches a user hears, and it is the number our own
  preview keeps at 0.
* **A bus that is not activated is a silent bus.** A host that does not activate the
  audio buses of a module gets nothing back from it, and the module looks dead while
  it burns the processor. Our own host activates them; the rule is worth writing
  down.
* **And the same question the other way round: a module that is loaded by a foreign
  host has to do something sensible with the default state.** Our own VST3 module,
  loaded by an independent host, publishes one parameter (the Bypass switch) and
  passes the audio through bit for bit: the adapter takes the chain out of the
  state, and the state a foreign host hands it carries no chain, so nothing runs.
  Either the module runs on the default state, or it says in its log that it has
  nothing to do. Silence that looks like work is the worst of the three answers.

**One number of the table above has to be read with care.** The 2238 samples that
stand in the table of section 3.1 came from a cross correlation of a render, and a
cross correlation of a heavily modified signal lands near the delay the reference
really has, not on it. The impulse test — an impulse in, the same impulse out, the
distance measured on the waveform — gives the exact number, and it is the same for
every impulse of every clip: **1534 samples at 64 frames, 1470 at 128 and 256,
1726 at 512, 2238 at 1024 and 5310 at 4096**, that is 1214 samples plus the block
size of the host above 256 frames. 2238 samples is the delay the reference has at a
1024 frame block, and the block size of the render the cross correlation was taken
on is not written down in the table. The constant of the benchmark therefore carries
the delay at the 256 frame block, where the reference is at its smallest and where
the real time run of the study was made.

**The honest reading of the two columns.** The reference shows the change at once and
we show it after 22.0 ms, and the reference's change is a **step** of 0.0805 while
ours is a smooth fade: the thing to take from the reference is the smoothing, not
the speed. The reference misses the deadline in 13 % of the blocks of a minute and
spends 30.5 s of overrun inside it, and our own preview misses none. The reference's
delay is smaller than ours at a small host block (1470 samples against our 2047 on
the voice cases) and larger at a large one (2238 samples at 1024 frames), which is
why the constant of the benchmark is the smallest one it has and why the metric says
so.

### 3.5 The signal behaviour of the first measured product

The corpus rendered through the first measured product and measured with the ruler of
this project — the bands of the corpus manifest and the quality instrument — at the
setting the product comes with and at the best setting it offers.

| Measurement | Value | Source |
|---|---|---|
| the removal on the cases of the corpus, at the setting it comes with | **14.11 dB** (white), **14.09 dB** (pink), **14.08 dB** (hum), **14.13 dB** (rumble), **14.12 dB** (voice under hiss), **13.71 dB** (plosive) | `target-a/a215/out/results.json` |
| the wanted content on the same cases, at the same setting | **0.00 dB** (white), **0.00 dB** (pink), **13.31 dB** (hum), **16.18 dB** (rumble), **5.79 dB** (voice under hiss), **14.69 dB** (plosive) | same |
| the removal at the best setting it offers | **269.29 dB** (white) †, 46.30 dB (pink), 80.15 dB (hum), **302.38 dB** (rumble) †, **296.47 dB** (voice under hiss) †, 41.29 dB (plosive) | same |
| the wanted content at that best setting | 0.00 dB (white), 0.00 dB (pink), **232.37 dB** (hum) †, **232.45 dB** (rumble) †, 1.28 dB (voice under hiss), 0.17 dB (plosive) | same |
| the level of the whole output against the level of the input, at the best setting | −38.36 dB (white), −43.74 dB (pink), −62.00 dB (hum), −28.12 dB (rumble), **−0.41 dB** (voice under hiss), −1.09 dB (plosive) | `target-a/a215/out/output_levels.json` |
| the share of the output that is digital silence, at the best setting | **81.2 %** (white), **86.2 %** (pink), **85.6 %** (hum), 0 % (rumble), 0 % (voice under hiss), 0 % (plosive) | same |
| the same share, at the setting it comes with | 0 % on every case: every sample of the output is not silent | same |
| the first band edge of the product, as it comes | **1000 Hz** — exactly where the wanted partial of the hum case and of the rumble case sits | `target-a/a215/out/results.json` |
| the spread of the figures between two runs of the same setting | 0.22 dB (removal, white bed), 0.80 dB (wanted content, hum case): **the product is not bit-deterministic** | `target-a/a215/out/repeatability.json` |
| ours, measured with the same instrument | 13.54 dB (white), 17.30 dB (pink), 38.03 dB (hum), 11.44 dB (rumble), 17.70 dB (voice under hiss); wanted content moved by 0.00, 0.00, **0.04**, **0.03**, 0.02 dB; **two runs are bit identical** | same |

**† the figure is the floor of the measurement, not a result.** A band whose level is
exactly zero gives an infinite number of decibels, and the instrument prints the
floor of its own range (about 232 dB with it). Where the mark is on the "kept" side
it is the wanted partial that is **gone**, not moved. The tables above are the way to
tell the two apart: at the best setting the product is 38 to 44 dB below its input on
the noise beds and 81 to 86 per cent of its output is digital silence. **It has not
removed the bed; it has stopped emitting anything where there was nothing to emit.**
The constants of the benchmark carry the mark in their names:
`REFERENCE_HISS_REMOVED_DB_BEST_FLOOR`, `REFERENCE_RUMBLE_REMOVED_DB_BEST_FLOOR`,
`REFERENCE_VOICE_HISS_REMOVED_DB_BEST_FLOOR`, `REFERENCE_HUM50_KEPT_DB_BEST_FLOOR`
and `REFERENCE_RUMBLE_KEPT_DB_BEST_FLOOR` in
[`qa/benchmark/reference_restoration.py`](../qa/benchmark/reference_restoration.py),
next to the level of the output and the share of it that is silence.

What the honest reading is:

* **At the setting it comes with, it ties us on the noise beds.** It removes 14.08
  to 14.13 dB of them and moves their wanted content by 0.00 dB, where we remove
  13.54 dB and 17.30 dB and move it by 0.00 dB as well. The large removal figures at
  the best setting are floors, and the figure that describes them is the silence:
  81 to 86 per cent of the output is digital silence, so the removal is a
  replacement of the signal and not a removal of the artefact.
* **On the hum case and on the rumble case it damages the wanted content by 13.31 dB
  and 16.18 dB, where we damage it by 0.04 dB and 0.03 dB.** The reason is in
  `REFERENCE_FIRST_CROSSOVER_DEFAULT_HZ`: the first band edge of the product comes at
  1000 Hz, and the wanted partial of those two cases sits at 1000 Hz, so the partial
  is split between two bands. That is a configuration accident and not a defect, but
  it is the whole of the 13 to 16 dB.
* **It is not bit-deterministic, and we are.** The same setting on the same input
  gives another waveform and the figures move by up to 0.8 dB between two runs; our
  own two runs are bit identical. A number of ours is a number we can reproduce, and
  a number of the reference is one we can only quote with its spread.
* **At its best setting on the voice case it is simply better than we are.** It keeps
  the level of the output (−0.41 dB), silences the hiss band under the voice
  completely and moves the voice partials by 1.28 dB. A restoration network gets the
  noise floor out from under a voice where a spectral gate cannot. **That is the gap
  worth chasing: not the decibels on a noise bed, but the noise floor under a
  voice.**
* **What we take from it is the structure, not the network.** A band split with a
  per-band mix, solo, mute and bypass in front of the processor; a wet/dry mix as a
  first-class control; a read-out of what the tool is doing; and, the technique the
  measurement points at, **an estimator of what the signal under the noise was,
  applied as a subtraction** — so that the removal can be large without the output
  turning into silence where the input had none.

---

## 4. What we changed as a result

| What the study showed | What changed in our tree | Where |
|---|---|---|
| the first product ships 9 of its 16 algorithm slots empty, and stores only 16 of its 22 parameters in a preset | our parameter table has no empty choice, and our preset writer stores the module's own state completely | `aurcore/schema/params.tsv`, `aurcore/serial/preset_json.py` |
| the second product's controller holds no bytes, so a parameter moved in the controller does not come back through it | the parameters live in the name-keyed registry of the core and the adapter is a bridge; the state of a module is the core's | `aurcore/registry.py`, `aurcore/params.py` |
| the second product declares 1470 samples of latency and no tail, and rings for 1470 samples at −62.49 dB | the core declares a tail next to the latency, the chain takes the maximum over its modules, and the tail travels through the C ABI as its own export | `aurcore/api.py`, `plugins/abi/aurcore_abi.cpp` |
| the first product is a four-band restorer: three cut-offs, a per-band decision, a fast or a linear-phase split | our de-reverb module follows the same four-band design, and the linear-phase option the foreign product offers as a switch is the one our filter file was missing | `aurcore/modules/de_reverb.py`, `aurcore/dsp/filters.py` |
| the second product is a three-stem separator with a level and a difference switch per stem | our separation family has the same three-stem shape, and the difference output is the analysis output our modules already expose | `aurcore/modules/music_rebalance.py`, `aurcore/modules/dialogue_isolate.py`, `aurcore/modules/ambience_match.py` |
| the block that decides must not depend on the size of the block the host hands over | the block coherence gate is shared by the modules instead of being one test per module | `tests/block_coherence.py` |
| a module must write every output sample, keep the length it was given and never return a non-finite sample | the finiteness gate runs over every module and four signals; the declared tail is checked against the measured ring-out | `qa/check_module_finite.py`, `tests/test_tail.py` |

### 4.1 What the study says we should change next, and where it stands

The surface study produced five concrete changes. Three of them are in the tree
now, and the tree's own state is given so that the gap is visible:

| Change | What it buys | State of the tree |
|---|---|---|
| put `log` on every parameter whose unit is hertz and whose range spans more than one decade — 17 of the 18 hertz parameters | the band-frequency control goes from 1.5840 octaves per 1 % of travel at 100 Hz to 0.0997 octaves per 1 % everywhere: 15.9 times finer at the bottom, 3.5 times coarser at the top, the same at both ends | the model carries the field (`ParamDesc.curve`, `linear` or `log`) and the adapter implements it in both directions; **the canonical table sets `log` on 22 of the 23 hertz parameters** (the twenty-third starts at zero and stays linear) |
| give the gain controls the taper, and re-range them so that unity sits in the middle | 0.840 dB per 1 % of travel becomes 0.317 on the gain module, 0.480 becomes 0.127 on the stem gains, 0.360 becomes 0.095 on the equaliser gains | **implemented**: 25 parameters carry the taper with the exponent 0.264; the exponent that puts unity in the middle is fixed by the range, `p = log2((−lo)/(hi − lo))`.  Two of the parameters do not fit the law, and both are recorded in [`RESTORATION_REFERENCE.md`](RESTORATION_REFERENCE.md), section 3.1 |
| publish a bypass control in every plug-in, with the id the format reserves for it | a host that draws a bypass switch has something to drive | **every plug-in publishes one**, with the id the format reserves |
| publish the program list of the module's own presets | the presets of the app reach the host's preset menu | **the controller implements the unit interface**: one unit (the root) and one program list, carrying the built-in presets of the module |
| key the session and the preset by a stable string id instead of by the position of the parameter | inserting a parameter in the middle of a module stops renumbering the ones after it | the host id stays a number; the document has to carry the name beside it |

Three of those five are in the tree. The taper is one of them, and it did not come
for free: it is a change of the law *and* of the range, and two controls must be left
alone on purpose — the makeup gain of the clipper (two thirds of its range is above
unity, so a taper would make it 2.4 times coarser) and every threshold (a threshold
is judged against the signal, not against unity).  The two parameters that do not fit
the law are recorded in [`RESTORATION_REFERENCE.md`](RESTORATION_REFERENCE.md),
section 3.1.

---

## 5. The remaining gap

**The dB head-to-head on the corpus does not exist yet.** The question the study was
asked — *by how many decibels are the commercial products better than we are, and in
which direction* — is still unanswered for the audio, because the foreign products
have not been rendered over our corpus with our rules.

The permanent benchmark measures our side of that table, with the ruler of our own
reports. Run against the tree as it stands (`python qa/benchmark/run_benchmark.py`),
it says:

| Case | Module of ours | Metric | Ours | The reference | The gap |
|---|---|---|---|---|---|
| hiss | spectral de-noise | `removed_db` | **13.54 dB** | not yet recorded | unknown |
| hiss | spectral de-noise | `gain_var_db2` | **0.06 dB²** | not yet recorded | unknown |
| pink hiss | spectral de-noise | `removed_db` | **17.30 dB** | not yet recorded | unknown |
| pink hiss | spectral de-noise | `gain_var_db2` | **0.55 dB²** | not yet recorded | unknown |
| mains hum | de-hum | `removed_db` | **39.64 dB** | not yet recorded | unknown |
| mains hum | de-hum | `kept_db` | **0.04 dB** | not yet recorded | unknown |
| mains hum | de-hum | `gain_var_db2` | **0.80 dB²** | not yet recorded | unknown |
| rumble | de-hum | `removed_db` | **11.44 dB** | not yet recorded | unknown |
| rumble | de-hum | `kept_db` | **0.03 dB** | not yet recorded | unknown |
| rumble | de-hum | `gain_var_db2` | **6.28 dB²** | not yet recorded | unknown |
| voice under hiss | spectral de-noise | `removed_db` | **13.62 dB** | not yet recorded | unknown |
| voice under hiss | spectral de-noise | `kept_db` | **0.00 dB** | not yet recorded | unknown |
| voice under hiss | spectral de-noise | `gain_var_db2` | **0.04 dB²** | not yet recorded | unknown |
| corpus voice | voice de-noise | `removed_db` | **17.84 dB** | not yet recorded | unknown |
| corpus voice | voice de-noise | `kept_db` | **0.05 dB** | not yet recorded | unknown |
| corpus voice | voice de-noise | `chatter_db` | **0.01 dB** | not yet recorded | unknown |
| synthetic voice | voice de-noise | `removed_db` | **17.85 dB** | not yet recorded | unknown |
| synthetic voice | voice de-noise | `kept_db` | **0.13 dB** | not yet recorded | unknown |
| synthetic voice | voice de-noise | `chatter_db` | **0.03 dB** | not yet recorded | unknown |
| speech shaped | voice de-noise | `removed_db` | **17.86 dB** | not yet recorded | unknown |
| speech shaped | voice de-noise | `kept_db` | **0.10 dB** | not yet recorded | unknown |
| speech shaped | voice de-noise | `chatter_db` | **0.02 dB** | not yet recorded | unknown |

The **ours** column of this table is the tree of 2026-10-21, 23:29, measured by
`python qa/benchmark/run_benchmark.py`.  The modules are still being changed in
parallel, so these numbers move with the tree; the reference column is a
constant of the benchmark and does not.

The two performance metrics are measured on the same run, and the cost is
given in transform equivalents and in microseconds on the yardstick of the
project (8.300 us per 1024 point transform, so a block may cost 160.6426 of
them), counted here, block by block, the way a host drives the module:

| Case | Module of ours | `latency_samples` | `cost_transform_eq` | `cost_us_per_block` |
|---|---|---|---|---|
| hiss | spectral de-noise | 0 | 4.72 | 39.2 |
| pink hiss | spectral de-noise | 0 | 4.72 | 39.2 |
| mains hum | de-hum | 0 | 4.95 | 41.1 |
| rumble | de-hum | 0 | 4.95 | 41.1 |
| voice under hiss | spectral de-noise | 2624 | 4.72 | 39.2 |
| corpus voice | voice de-noise | 2047 | 1.09 | 9.1 |
| synthetic voice | voice de-noise | 2047 | 1.08 | 9.0 |
| speech shaped | voice de-noise | 2047 | 1.09 | 9.1 |

Two foreign numbers belong to that table now: **the second measured product
declares and measures 1470 samples of latency** (section 3.1), and **its cost
is 36.9466 to 51.4056 transform equivalents per 64 sample block** — 306.7 to
426.7 us on the study machine, 0.23 to 0.32 times the real time, converted
into the yardstick above.  The first measured product declares no latency, and
its cost is 82.8313 to 346.3855 transform equivalents per 64 sample block
(5.5 to 23.0 ms per 512 frames on the study machine, converted into the same
yardstick).

**Both sides of the cost are on one yardstick, and the yardstick does not
move.**  The two sides were measured on two different machines: ours is
counted here, the reference's was timed on the study machine and converted.
The yardstick is a constant of the project — 8.300 us per 1024 point
transform, so a 64 sample block of real time at 48 kHz (1333.3 us) may cost
160.6426 of them — because a budget that moves with the machine that measures
it is an echo, not a budget.  The conversion of every reference figure is
stated in the method string of its constant in
[`qa/benchmark/reference.py`](../qa/benchmark/reference.py).  On that yardstick
every module of ours is ahead of the reference on every case, by 32.00 to
35.86 transform equivalents, and the reference stays a **yardstick, not a
threshold**: the budget the product is held to is the budget of a block.


The **reference** column of the table above is the one the runner prints, and the
runner is where the gap is computed: the benchmark holds a reference to a *target*,
not to a contract, and the runner reports a module that is behind instead of failing
it. The reference numbers of the two foreign products are constants of the
benchmark now — the corpus head-to-head of section 3.5 and the real time behaviour
of section 3.4 among them — and the table of this section was written before they
were recorded, so the numbers of the reference column belong to a run of
`python qa/benchmark/run_benchmark.py`, not to this table.

Three gaps are narrower and can be stated exactly:

1. **The corpus renders of the first measured product are tabulated now.** The first
   driver of the study never got audio out of it; the host build with a file mode
   does render the corpus clips, and the renders are on disk. Section 3.5 reads them
   with our own instrument, at the setting the product comes with and at the best
   setting it offers.
2. **The algorithm of the second measured product is unknown and will stay unknown.**
   The payload is protected; the five decompression attempts and the deflate scan
   failed. What it does can be measured for ever; how it does it cannot be read.
3. **The first product's algorithm slots have not been swept against the corpus.**
   Seven slots carry an algorithm and nine carry none, but which of the seven is the
   best one, and what it does to the corpus, is unknown.

---

## 5.1 The provenance caveat: the study's exercise host moved a parameter and never put it back

**The study has more than one host, and one of them has a defect that changes
what it measures.**  This section says which is which, says which numbers of
this tree are affected, and says what was done about it.

### 5.1.1 The defect

The study's **exercise host** proves that it may write a parameter by writing
one of them to a different value, and it **leaves it there**.  It is the
study's own program that says so, in its own log, on the two products of this
tree:

```
parameter test  : id=773352680  "Bypass"         0.000000 -> 1.000000 ("Off" -> "On")   read back OK
parameter test  : id=155422557  "Ambience Gain"  0.500000 -> 0.250000 ("0.0" -> "-10.0") read back OK
```

| the reference | the parameter the exercise host moves | to |
|---|---|---|
| the first measured product (four bands, an algorithm) | `Bypass` | `On` |
| the second measured product (three stems) | `Ambience Gain` | `-10 dB` |

The host runs the step that moves the parameter **before** the step that
processes the audio and before the step that writes it out, and nothing puts
the value back afterwards.  A measurement taken with that host is therefore a
measurement of a **bypassed** plug-in, or of a plug-in with its ambience gain
at **-10 dB** — never of the default.

The symptom is unmistakable when it is looked for: a measured band gain of
0.00 dB everywhere, because the plug-in is passing the input through.

### 5.1.2 The three piles, and the suspect pile

Every number the tree carries says how it was measured, and the way it was
measured names the **instrument**.  Read that way, the 504 numbers of the six
constants files fall into three piles:

| the pile | the instrument | the numbers |
|---|---|---|
| measured with the study's instruments | the study's **renderer** (258), the study's **driver** (65), the study's **render host** (37), the study's **edge host** (23), the study's **surface host** (17), the study's **own cost model** (6), the study's **real time host** (4) | 410 |
| measured with our own instruments | our own **modules** (175), the **module of ours** (40), our own **reader** (15), our own **clock** (5), the **gate of the tree** (1) and the instruments of the project (the ruler, the quality instrument, the voice instrument, the edge contract instrument) | 94 |
| measured with the study's **exercise host** | the host of 5.1.1 | 0 |

A number can sit in both of the first two rows: the two sides of a table are
measured with one instrument, so its method names an instrument of the study
and one of ours.  The split above puts a number in the first row when its
method names an instrument of the study, and in the second row when it does
not.

**The suspect pile was the first row before the audit, and it could not be
told apart from the exercise host.**  Three hundred of the 504 method strings
said "the study's host" or "the study's own host" without saying *which*
host, so a reader could not tell whether the number was taken with the host
in its **exercising** state (parameter moved, never restored) or in its
**measuring** state.  The strings did not say, and the strings were the only
provenance the numbers carried.

### 5.1.3 What the audit found: the numbers are sound

The numbers were not taken with the exercise host in its exercising state.
This was established by measurement, not by reading the method strings, and
it held for every family of numbers in the tree.

**At the level of the audio.**  The study left the renders behind in
`re/a45`.  Each of them was compared, sample by sample, with a fresh render
made with the study's own **renderer** — the renderer moves no parameter of
its own, and the study's runner drives it with only the parameters it was
asked for.  The two families that carry the numbers of this tree most
directly (`out_*` and `vout_*`) reproduce the renderer's render **at the
default** to 1.5e-07 to 3.6e-07, which is the noise floor of the file format
they are written in.  **None** of the renders matches a render made with the
moved parameter in effect: those errors run from 8e-04 to 5.7e-01, four to six
orders of magnitude above the noise floor.  The other families (`agg_*`,
`band_*`, `tmp_*`, `bandv_*`) are settings the audit did not reproduce, but
they are excluded from the moved value by the same margin.

**At the level of the figures.**  The numbers of this tree were then measured
again with the product's own ruler
([`qa/benchmark/harness.py`](../qa/benchmark/harness.py)) against fresh
renders made with the study's renderer, once at the default and once with the
moved parameter in effect.  The tree's number is the default, or the setting
the study's rule picked; it is never the moved value.

The second measured product, whose moved parameter is the ambience gain:

| the case | the figure | at the default | with the moved parameter | the tree |
|---|---|---|---|---|
| hum50 | kept_db | 0.00555 dB | 10.00048 dB | 0.00555 dB |
| voice_hiss | kept_db | 0.01280 dB | 9.96130 dB | 0.01281 dB |
| corpus_voice | kept_db | 0.00630 dB | 9.05366 dB | 0.0063 dB |
| speech_shaped | kept_db | 0.00011 dB | 8.52141 dB | 0.00011 dB |
| hiss | removed_db | 0.01838 dB | 10.39411 dB | 21.9129 dB (the setting the study's rule picked) |
| synthetic_voice | removed_db | 0.00027 dB | 5.52841 dB | 8.9585 dB (the setting the study recorded) |

The first measured product, whose moved parameter is the bypass:

| the case | the figure | at the default | with the moved parameter | the tree |
|---|---|---|---|---|
| hiss | removed_db | 14.22067 dB | 0.11974 dB | 49.1941 dB (the setting the study's rule picked) |
| hum50 | removed_db | 14.23037 dB | 0.12957 dB | 67.0024 dB (the setting the study's rule picked) |
| voice_hiss | removed_db | 14.22124 dB | 0.11718 dB | 14.2212 dB |
| corpus_voice | removed_db | 14.29351 dB | 0.00000 dB | 14.29502 dB |
| synthetic_voice | removed_db | 13.43085 dB | 0.00000 dB | 13.42147 dB |
| speech_shaped | removed_db | 14.13818 dB | 0.00000 dB | 14.13831 dB |

The moved value is excluded by a wide margin in every row, and the rows do not
depend on reproducing the study's exact render: a bypassed plug-in removes
0.12 dB where the tree's numbers remove 14 to 67 dB, and reads exactly
0.00000 dB of removal on the voice cases; a plug-in with its ambience gain at
-10 dB moves the wanted content by 8.5 to 10 dB, where the tree's numbers
move it by 0.00011 to 0.039 dB.  Neither is anywhere near the numbers this
tree carries.

### 5.1.4 The one family in the study that was contaminated, and did not land

The study did produce contaminated numbers, in `re/a32/work/measure/`.  Seven
renders of the same clip at seven different algorithm settings —
`alg0_hiss.wav` through `alg6_hiss.wav` — are **byte-identical** (the same
sha256), which is what a bypassed plug-in looks like.  The renders of
`re/a34/work/corpus_a/` and `re/a34/work/param_a/` carry the same signature:
every setting gives the same level, because the setting has no effect.

**None of it reached this tree.**  The constants file the a32 agent proposed
(`qa/benchmark/reference_estimator.py`, with its `ESTIMATOR_REFERENCES`) is
absent, and no `ESTIMATOR_` constant appears anywhere in `qa/benchmark/`.

### 5.1.5 What was done about it

* **The method strings now name the instrument.**  "The study's host" is gone
  from every method string of the six constants files; each says which
  instrument measured the number, and the study's instruments say what they do
  with the parameters of the plug-in.  No number was changed: only the
  sentence that says where the number came from.
* **The gate holds it in place.**
  [`tests/test_benchmark_provenance.py`](../tests/test_benchmark_provenance.py)
  asserts that every number carries a non-empty method, that every method
  names an instrument, that a number of the exercise host carries the caveat,
  and that the caveat is written down here.
* **The caveat that stands.**  A number whose method names the study's
  **exercise host** describes a plug-in with a moved parameter, and the method
  string has to say so.  The numbers of this tree do not name it, because
  they were not taken with it.

---

## 6. What could not be measured, and why

This section is the honest one. Five things could not be measured; each of them is a
result, not a gap in the work.

| What | Why not | What was measured instead |
|---|---|---|
| the algorithms of the second measured product | the payload is protected; 5 decompression attempts failed and 0 deflate streams inflate | the behaviour: the surface, the latency, the tail, the state, the response to a tone |
| the compressed blocks of the first product's installer | 18 candidate blocks, 18 failures with lzma and zlib | the payload was carved out of the file directly: 18 files, 243,867,895 bytes |
| the audio of the first measured product | the first driver of the study produced no output at all; the host build with a file mode renders the corpus, but the renders are not tabulated yet | the surface, the refusal of the host's message, the fault at the release, and the renders themselves |
| the corpus head-to-head | the renders exist, the measurement with our rules and our metric names does not | our own side of the table, measured by the permanent benchmark |
| the editors of the two foreign products | the harness reads the automatable surface, which is not necessarily the visible surface | the parameter surface, which is what a host drives |

---

## 7. The next measurement

**The head-to-head on the corpus.** One clip at a time:

1. Render every noise case of the corpus through the second measured product and
   through the first, and measure the result with `qa/quality/quality_rules.py` —
   the noise removed in dB, the wanted part moved in dB, the variance of the gain
   curve in dB². Those are the three metrics the benchmark already names:
   `removed_db`, `kept_db`, `gain_var_db2`.
2. Do the same on the voice cases with `qa/qualityvoice/rules.py`, and count the
   chatter while the voice speaks (`chatter_db`).
3. Sweep the seven algorithm slots of the first product and the six programs of the
   second, so that the best setting of each is compared with the best setting of
   ours and not with a default.
4. Measure the cost of one 64-sample block of each in transform equivalents
   (`cost_transform_eq`), the way `qa/performance/budgets.py` measures ours.
5. Write the numbers into `qa/benchmark/reference.py` as the reference series
   `stem_separation` and `band_restoration`. The gap column then appears by itself.

The instrument for steps 1 and 2 exists on our side already: `qa/quality/` measures
a module against the corpus and the rules, and `qa/qualityvoice/` does the same for
the voice shaped modules. What is missing on the other side of the pipe is the
**ruler**: the foreign renders have to go through the same rules and come out in the
same metric names, otherwise the two numbers are not comparable and the gap column
stays empty.

---

## 8. Provenance

Every number in this note, and where it comes from. The paths of the study are
relative to the study root, which is outside this tree and holds `target-a/`,
`target-b/`, `common/` and `reports/`. The paths of the product are relative to the
repository root.

### 8.1 The first measured product

| Number | Source |
|---|---|
| installer 244,817,727 bytes (234 MiB) | `target-a/carve_a18/pe_index.txt`, `target-a/installer_listing.txt` |
| setup loader 949,760 bytes, 11 sections | `target-a/carve_a18/triage.txt` |
| 11 embedded PE images; 238,416,896 bytes inside them; 6,400,831 bytes outside | `target-a/carve_a18/pe_index.txt` |
| plug-in of our format 7,976,448 bytes, entropy 6.4590; second desktop format 7,767,552 bytes, entropy 6.4517; helper 7,168 bytes with 15 exports; seven further binaries of 15.43 to 43.27 MiB | `target-a/carve_a18/triage.txt` |
| 18 candidate blocks, 18 decompression failures | `target-a/a19/blocks/block_results.json` |
| 18 files and 243,867,895 bytes recovered by carving; the list of the blocks | `target-a/a216/poll_a216.log` |
| 22 parameters, 21 automatable, 11 continuous, 11 stepped, one unit, one program list with one program | `target-b/a220/out/surface_a.json` |
| ranges and defaults of every control; the 16 algorithm slots of which 7 carry an algorithm | `target-a/a213/probe_params.tsv` |
| 16 of the 22 parameters stored in the presets | `target-a/extracted/block_1*_preset_*.xml` (16 `PARAM` elements each, 16 distinct identifiers) |
| the refusal of the host's message; the catalogue with 0 problems and 0 round-trip failures; the fault at the release | `target-a/a216/out/host_a.json`, `a34/work/out.log.txt` |
| the driver that produced no output | `a310/A_default.json` |
| the render of a corpus case by the host build with a file mode | `a34/work/sweep_a2/out.wav` (96,000 frames, a peak of 32768, 383,892 of 384,000 samples non-zero) |
| every row of the corpus head-to-head table of section 3.5: the removal and the wanted content of the six cases at the setting the product comes with and at the best setting it offers, the five floors, the level of the output and the share of it that is silence | `target-a/a215/REPORT_2_15.md`, `target-a/a215/out/results.json`, `target-a/a215/out/output_levels.json` |
| the spread of the figures of the product between two runs of the same setting | `target-a/a215/out/repeatability.json` |
| the constants of the corpus head-to-head, with their floor marks | `qa/benchmark/reference_restoration.py` |

### 8.2 The second measured product

| Number | Source |
|---|---|
| loader 260,096 bytes (254.0 KiB); payload 25,308,728 bytes (24.14 MiB) | `target-b/measurements.txt` |
| payload/loader ratio 97.31 : 1; largest raw section 25,292,288 bytes at entropy 7.950; 296 of 387 blocks at entropy 7.9 or more; 34,898,875 virtual bytes without data on disk; 5 failed decompression attempts; 0 deflate streams inflating to more than 256 bytes | `target-b/agent16/analysis_out.txt` |
| 17 parameters, 5 automatable, 3 continuous, 14 stepped, one unit, one program list with 6 programs | `target-b/a220/out/surface_b.json` |
| the ranges and defaults of the three level controls (−60.0 … +12.0 dB, default 0.0) | `target-b/agent2.3/out/report.json` |
| latency 1470 declared and 1470 measured, correlation 0.905927; tail 0 declared and 1470 measured at −62.485802 dB with a fall of 0 dB; state 716 bytes against 0 bytes; 7 of 17 parameters audible; the tone out at a peak of 7.68448626332e-06 and an rms of 2.33189121374e-06; 2650 ms in total | `target-b/agent2.3/out/report.json` |
| unwritten 0, finite yes, 1470 samples of latency, 0 of tail, frames in equal to frames out, on eight cases | `agent3.9/out/b/*.txt` |
| every row of the real time table of section 3.4: the delay at every block size, the change latency, the step a change makes, the underruns of the minute and the cost of a block | `target-b/agent2.12/REPORT-2.12.md`, `target-b/agent2.12/final.txt`, `target-b/agent2.12/extra.txt` |
| the 60 s runs behind the underrun counts | `target-b/agent2.12/minute_*.json` |
| the constants of the real time behaviour, with the block size they belong to | `qa/benchmark/reference.py` |

### 8.3 The surfaces, ours and theirs, on one instrument

| Number | Source |
|---|---|
| every row of the surface table of section 2.1; the fitted laws and their largest errors; the 75.8 / 3.1 / 1.8 frequency comparison; the 1.14 / 2.66 gain comparison; 27 of the 295 curve fields set to `log` and 23 to `taper`; 51 plug-ins with a bypass, one unit and one program list each; 222 distinct names over 295 parameters; 67 groups; 109 units | `target-b/a220/REPORT.md` (the surface study of the study) |
| the same surface, as raw dumps | `target-b/a220/out/surface_a.json`, `target-b/a220/out/surface_b.json` |

### 8.4 Our own numbers

| Number | Source |
|---|---|
| every row of the table of section 5 | `python qa/benchmark/run_benchmark.py` — the permanent benchmark, run against the tree as it stands |
| the eight cases and the six metrics behind that table | `qa/benchmark/cases.py`, `qa/benchmark/reference.py` |
| the reference numbers of the study, as constants | `target-a/FINDING_SURFACE.md` (22 parameters, 11 knobs, 6 switches, 5 lists, 7 modes over 16 slots, 0 units, 0 groups, 0 logarithmic curves, 1 read-only output, the preset keyed by a string id) |
| 16 clips of 48 kHz material with the ground truth | `corpus/manifest.json` |
| the rules of the corpus pass | `qa/quality/quality_rules.py`, `qa/qualityvoice/rules.py` |
| the harness | `qa/vst3host/` |

### 8.5 The derived numbers

Two numbers in this note are arithmetic on measured numbers, and are marked as such
where they appear:

- the −96.3 dB of the tone is 20·log10(7.68448626332e-06 / 0.5), from the two peaks
  in `target-b/agent2.3/out/report.json`;
- the 97.31 : 1 ratio of payload to loader is 25,308,728 / 260,096, from
  `target-b/measurements.txt`.
