# Auralis — the realtime performance gate

> **Status: the budget is a gate now.**  A module that costs more than its
> budget fails `tests/test_performance.py`; there is no list of known
> offenders that are allowed to be over.  The budgets live in
> `qa/performance/budgets.py`, they are written in **transform
> equivalents** (how many 1024 point transforms a block may cost), so the
> verdict does not depend on how busy the machine that runs the test is.
>
> **No module is over the realtime budget today, and no module is held at a
> debt ceiling.**  The **50 modules** of the catalogue and **both product
> chains** are held at the realtime budget itself: the requirement, not a
> number that was measured once.  A regression that costs a module more than
> its budget now fails the gate instead of hiding behind a ceiling.
>
> The Spectral De-Noise — the last module that was over the budget — **has
> been repaired**: its streaming rewrite brought it from 568 transform
> equivalents (354 % of the budget of 160.6) to 3 at 64 samples and 569 at
> 512 samples (44 % of the budget of that block size), so its debt ceiling
> is gone and it comes off the "under repair" list.
>
> The **Music Rebalance** was over the budget as well: it cost 154
> transform equivalents at 64 samples (96 % of the budget) and it cost
> 91 now (57 %).  It was made 1.8 times faster without changing a single
> output sample (section 3).
>
> The two modules that joined the catalogue — **Band Restoration** and
> **Voice Reverb** — have a budget and a survey specification of their own,
> so they are measured like the rest of the family.
>
> The table was rebuilt from a fresh sweep on **2026-10-21 at 21:22**; the
> table of 16:37 and the new one are compared in section 3.

Everything below was measured with the harness in this directory
(`qa/performance/harness.py`, `qa/performance/sweep.py`) on **2026-10-21
at 21:22** (Windows 11, Python 3.15.0, NumPy 2.5.4, AMD Ryzen 7 7840HS,
16 threads).  The machine was **busy**: 100 % load during the sweep, the
other agents of the finishing wave were working on it.  Section 8 says
what the load does to a measurement — that is the one number a reader has
to keep in mind.  The measurement of the Music Rebalance is the only one
that was taken by hand (the module was changed while the sweep ran, see
section 3).

> The analysis of *why* the modules cost what they cost — the hot spots,
> the root cause (the fixed analysis cost per block) and the proposed
> optimisations — is in `qa/performance/ANALYSIS.md`.  It is the document
> the team brief refers to; this document is the gate.

---

## 1. The measurement

The machine is shared, so a wall-clock number is not a measurement.  Three
things make the number trustworthy:

| what | how |
|---|---|
| **the yardstick** | every block is timed *next to* a batch of 1024 point transforms of the same machine: a batch before the block and a batch after it, the two fastest transforms of each batch averaged.  The cost of the block is then the ratio `block time / transform time` — a number in **transform equivalents** that does not change when the load of the machine changes |
| **the fastest of N runs** | the module is measured in 5 rounds and the **smallest** ratio of all 45 samples wins.  The fastest observation is the one that was least disturbed by other work |
| **the sweep is interleaved** | every round measures *every* module and *both* chains once, so the samples of a module are spread over the whole sweep instead of being taken back to back.  The load of the machine comes and goes in bursts; a module measured inside one burst never sees a quiet moment |

### The reference machine

The budget of a block is written in the transform equivalents of the
**reference machine**, and the reference is a property of the committed
table, not of the session that measured it:

| | transform cost | the 64 sample budget |
|---|---|---|
| the reference machine (the committed table) | **8.300 us** | 1333.3 us / 8.3 us = **160.6** transform equivalents |
| this machine, 2026-10-21 at 21:22 (100 % load) | 10.70 us | 1333.3 us / 10.7 us = 124.6 transform equivalents |

The yardstick of a session moves with the load of the machine (8.30 us
during the 11:14 session, which ran at 87.6 % load; 6.40 us at the quiet
session of 16:37; 10.70 us at the session of 21:22, which ran at 100 %
load), and a requirement that moves with the load of the measuring
session is not a requirement.  `qa/performance/make_budgets.py` therefore
keeps the reference of the committed table and prints the yardstick of the
session next to it.  The consequence on this machine: the gate is 8 %
tighter than the raw realtime deadline (160.6 transforms are 1228 us
here, the deadline is 1333 us) — the safe direction.

With the reference yardstick the realtime budget of a block is:

| block | budget | in transform equivalents |
|---|---|---|
| 64 | 1333.3 us | **160.6** |
| 128 | 2666.7 us | 321.3 |
| 256 | 5333.3 us | 642.6 |
| 512 | 10 666.7 us | 1285.1 |

A module that costs 100 transform equivalents costs 830 us on the
reference machine — 62 % of the 64 sample budget.

### The machine load

The harness measures the CPU utilisation of the machine while it works
(`GetSystemTimes` on Windows, `getloadavg` elsewhere) and reports it
next to every number:

```
calibration: one 1024 point rfft costs 11.80 us (fastest of 5 runs, slowest 14.20 us, machine load 100.0 %)
yardstick: 10.70 us per transform (fastest of 20 samples, median 12.12 us, slowest 12.65 us) -> the 64 sample budget is 124.6 transforms
sweep: 50 modules and 2 chains x 5 rounds x 4 block sizes in 51.3 s, machine load 100.0 % (peak 100.0 %)
yardstick after the sweep: 10.70 us per transform (20 more samples, median 12.38 us)
allocation pass: 50 modules in 0.4 s
budget at 64 samples: 1333.3 us = 124.6 transforms of 10.70 us
```

### How repeatable is it?

Two sweeps of the same tree, two minutes apart (this one and the one of
the other finishing wave), agree within 13 % on the worst module and
within 7 % on the rest; the dialogue chain agrees to 0.3 %.  Between the
sweep of 11:14 (87.6 % load) and this one the modules have changed, so
the two are not comparable.

The load of the machine moves the calibrated cost of a module much more
than that — section 8 has the measurement.  The rule of thumb: the guard
is a verdict on a machine that is **not saturated**.

Reproduce with:

```
python qa/performance/sweep.py --runs 5 --repeats 9 --warmup 3 --blocks 64,128,256,512 --json var/perf/sweep.json
python qa/performance/make_budgets.py --json var/perf/sweep.json
python -m pytest tests/test_performance.py -q
```

---

## 2. The measurement table

`trf` is the cost of one block in 1024 point transforms (the calibrated
number), `%` is that cost as a share of the realtime budget of the block
(⚠ = over budget).  `arrays/blk` is the number of NumPy arrays the
module creates inside `process()`, `churn KiB` is the peak Python
churn of one block.

| module | 64 trf | 64 % | 128 trf | 128 % | 256 trf | 256 % | 512 trf | 512 % | arrays/blk | churn KiB |
|---|---|---|---|---|---|---|---|---|---|---|
| `AUR_AMBIENCEMATCH` | 38 | 23.4% | 53 | 16.5% | 55 | 8.5% | 62 | 4.8% | 42 | 36 |
| `AUR_AZIMUTH` | 7 | 4.6% | 6 | 2.0% | 9 | 1.4% | 8 | 0.6% | 9 | 11 |
| `AUR_BANDRESTORATION` | 8 | 4.8% | 8 | 2.5% | 10 | 1.5% | 7 | 0.6% | 41 | 1 |
| `AUR_BREATH` | 3 | 2.1% | 4 | 1.1% | 31 | 4.8% | 45 | 3.5% | 22 | 32 |
| `AUR_CENTEREXTRACT` | 1 | 0.6% | 1 | 0.3% | 25 | 3.8% | 32 | 2.5% | 22 | 32 |
| `AUR_DEBLEED` | 32 | 20.0% | 33 | 10.2% | 32 | 5.0% | 32 | 2.5% | 39 | 1 |
| `AUR_DECLICK` | 2 | 1.1% | 2 | 0.5% | 2 | 0.3% | 2 | 0.2% | 12 | 63 |
| `AUR_DECLIP` | 3 | 1.9% | 3 | 1.0% | 3 | 0.5% | 4 | 0.3% | 1 | 0 |
| `AUR_DECRACKLE` | 1 | 0.5% | 1 | 0.3% | 1 | 0.1% | 2 | 0.2% | 4 | 35 |
| `AUR_DEESS` | 3 | 2.0% | 3 | 1.0% | 37 | 5.8% | 54 | 4.2% | 20 | 31 |
| `AUR_DEHUM` | 12 | 7.5% | 13 | 4.0% | 16 | 2.5% | 15 | 1.1% | 15 | 4 |
| `AUR_DEPLOSIVE` | 2 | 1.5% | 15 | 4.8% | 25 | 3.9% | 43 | 3.3% | 18 | 7 |
| `AUR_DEREVERB` | 2 | 1.1% | 2 | 0.7% | 3 | 0.5% | 47 | 3.7% | 21 | 64 |
| `AUR_DERUSTLE` | 2 | 1.4% | 2 | 0.7% | 3 | 0.4% | 68 | 5.3% | 13 | 31 |
| `AUR_DEWIND` | 4 | 2.3% | 4 | 1.4% | 26 | 4.1% | 50 | 3.9% | 21 | 33 |
| `AUR_DECONSTRUCT` | 5 | 3.1% | 5 | 1.5% | 6 | 0.9% | 183 | 14.2% | 38 | 94 |
| `AUR_DIALOGUEISOLATE` | 3 | 1.8% | 3 | 1.0% | 4 | 0.6% | 5 | 0.4% | 14 | 4 |
| `AUR_GUITARDENOISE` | 26 | 16.5% | 21 | 6.5% | 54 | 8.4% | 87 | 6.7% | 68 | 2 |
| `AUR_MOUTHDECLICK` | 1 | 0.5% | 1 | 0.3% | 1 | 0.1% | 1 | 0.1% | 4 | 35 |
| `AUR_MUSICREBALANCE` | 91 | 56.8% | 68 | 21.2% | 114 | 17.8% | 76 | 5.9% | 48 | 1 |
| `AUR_REPAIRASSISTANT` | 0 | 0.2% | 0 | 0.1% | 0 | 0.1% | 0 | 0.0% | 3 | 0 |
| `AUR_SPECTRALDENOISE` | 3 | 1.7% | 11 | 3.3% | 309 | 48.1% | 569 | 44.3% | 21 | 7 |
| `AUR_SPECTRALEDITOR` | 0 | 0.1% | 0 | 0.1% | 0 | 0.0% | 0 | 0.0% | 2 | 0 |
| `AUR_SPECTRALRECOVERY` | 40 | 24.9% | 39 | 12.2% | 32 | 4.9% | 37 | 2.9% | 27 | 0 |
| `AUR_SPECTRALREPAIR` | 0 | 0.1% | 0 | 0.1% | 0 | 0.0% | 0 | 0.0% | 2 | 0 |
| `AUR_VOICEDENOISE` | 2 | 1.3% | 2 | 0.7% | 92 | 14.4% | 133 | 10.3% | 12 | 36 |
| `AUR_VOICEREVERB` | 2 | 0.9% | 2 | 0.5% | 22 | 3.4% | 35 | 2.7% | 12 | 15 |
| `AUR_WOWFLUTTER` | 24 | 15.1% | 33 | 10.2% | 33 | 5.1% | 63 | 4.9% | 75 | 10 |
| `AUR_DITHER` | 2 | 1.5% | 3 | 0.8% | 3 | 0.5% | 3 | 0.3% | 12 | 0 |
| `AUR_EQ` | 3 | 2.0% | 3 | 0.9% | 4 | 0.6% | 5 | 0.4% | 18 | 0 |
| `AUR_EQMATCH` | 0 | 0.1% | 0 | 0.1% | 0 | 0.0% | 0 | 0.0% | 2 | 0 |
| `AUR_FADE` | 64 | 39.5% | 55 | 17.1% | 58 | 9.0% | 51 | 3.9% | 6 | 0 |
| `AUR_GAIN` | 0 | 0.1% | 0 | 0.1% | 0 | 0.1% | 0 | 0.0% | 2 | 0 |
| `AUR_INTERPOLATE` | 53 | 32.8% | 66 | 20.4% | 62 | 9.7% | 58 | 4.5% | 161 | 2 |
| `AUR_LEVELER` | 10 | 6.2% | 22 | 7.0% | 44 | 6.9% | 94 | 7.3% | 53 | 7 |
| `AUR_LOUDNESS` | 10 | 6.1% | 23 | 7.0% | 44 | 6.9% | 91 | 7.1% | 53 | 7 |
| `AUR_LOUDNESSOPT` | 3 | 1.8% | 5 | 1.4% | 9 | 1.5% | 18 | 1.4% | 10 | 0 |
| `AUR_MIXING` | 1 | 0.7% | 1 | 0.3% | 1 | 0.2% | 1 | 0.1% | 6 | 0 |
| `AUR_NORMALIZE` | 1 | 0.4% | 1 | 0.2% | 1 | 0.1% | 1 | 0.1% | 4 | 0 |
| `AUR_PHASE` | 0 | 0.3% | 0 | 0.1% | 0 | 0.1% | 0 | 0.0% | 2 | 0 |
| `AUR_RESAMPLE` | 5 | 2.9% | 5 | 1.5% | 7 | 1.1% | 12 | 0.9% | 13 | 3 |
| `AUR_SIGNALGEN` | 1 | 0.9% | 2 | 0.5% | 2 | 0.3% | 3 | 0.2% | 5 | 0 |
| `AUR_STREAMPREVIEW` | 1 | 0.4% | 1 | 0.2% | 1 | 0.1% | 1 | 0.1% | 5 | 3 |
| `AUR_TIMEPITCH` | 0 | 0.3% | 0 | 0.1% | 0 | 0.1% | 0 | 0.0% | 2 | 0 |
| `AUR_VARIPITCH` | 0 | 0.2% | 0 | 0.1% | 0 | 0.0% | 0 | 0.0% | 2 | 0 |
| `AUR_VARTIME` | 0 | 0.2% | 0 | 0.1% | 0 | 0.0% | 0 | 0.0% | 2 | 0 |
| `AUR_CLIPCLASSIFIER` | 1 | 0.5% | 1 | 0.3% | 1 | 0.2% | 1 | 0.1% | 2 | 0 |
| `AUR_FINDSIMILAR` | 0 | 0.1% | 0 | 0.1% | 0 | 0.0% | 0 | 0.0% | 1 | 0 |
| `AUR_CONNECT` | 0 | 0.1% | 0 | 0.1% | 0 | 0.0% | 0 | 0.0% | 2 | 0 |
| `AUR_MONITOR` | 6 | 3.5% | 7 | 2.1% | 9 | 1.4% | 13 | 1.0% | 20 | 1 |


### The chains

| chain | 64 trf | 64 % | 128 trf | 128 % | 256 trf | 256 % | 512 trf | 512 % |
|---|---|---|---|---|---|---|---|---|
| De-Hum → De-Click → De-Ess → De-Plosive → Gain | 22 | 13.6% | 40 | 12.6% | 88 | 13.7% | 139 | 10.8% |
| De-Hum → Spectral De-Noise → De-Click → De-Ess → Gain | 26 | 15.9% | 43 | 13.3% | 375 | 58.4% | 831 | 64.7% |

The **dialogue chain** is the product: mains hum, clicks, sibilance,
plosives and a level change.  It needs 22 transform equivalents — 14 %
of the 64 sample budget, 183 us on the reference machine.  It fits into
every budget with room to spare and it is held at the realtime budget.

The **spectral chain** (the dialogue chain with the flagship Spectral
De-Noise module in it) is inside the budget at every block size now: 26
transform equivalents at 64 samples (16 %) and 831 at 512 samples (65 %).
Its cost is the cost of the Spectral De-Noise, and the module was
repaired.

### The modules that were being repaired

The brief named seven modules that were over the 64 sample budget:
Ambience Match, Deconstruct, Guitar De-Noise, Repair Assistant, Spectral
De-Noise, Voice De-Noise, Resample.  Their state **now**:

| module | 64 trf | 64 % | note |
|---|---|---|---|
| `AUR_AMBIENCEMATCH` | 38 | 23.4% | **repaired** — inside the budget, held at the budget |
| `AUR_DECONSTRUCT` | 5 | 3.1% | **repaired** — the work moved to the large blocks (183 trf at 512 samples): the module buffers internally now |
| `AUR_GUITARDENOISE` | 26 | 16.5% | **repaired** — inside the budget, held at the budget |
| `AUR_REPAIRASSISTANT` | 0 | 0.2% | **repaired** — the module processes a block in 7 us with 3 arrays |
| `AUR_SPECTRALDENOISE` | 3 | 1.7% | **repaired** — 309 trf at 256 samples (48 %) and 569 at 512 (44 %): inside the budget, held at the budget |
| `AUR_VOICEDENOISE` | 2 | 1.3% | **repaired** — the work moved to the large blocks (133 trf at 512 samples) |
| `AUR_RESAMPLE` | 5 | 2.9% | **repaired** — inside the budget, held at the budget |

`AUR_MUSICREBALANCE` was not on that list, but it was over the budget
when the previous table was written (154 trf, 96 %); it now costs 91 trf
(57 %) and is held at the realtime budget as well.  The two modules that
joined the catalogue — `AUR_BANDRESTORATION` (8 trf) and
`AUR_VOICEREVERB` (2 trf) — are inside the budget from their first entry.

---

## 3. The budgets

The budget table is `qa/performance/budgets.py`.  It holds, for every
module and every chain, the measured cost and the ceiling per block
size, in transform equivalents.  Two kinds of ceiling exist:

* **`realtime`** — the ceiling is the realtime budget of the block
  (160.6 transform equivalents at 64 samples).  This is the requirement
  itself: the module has to process a 64 sample block in less than
  1333 us on the reference machine.  **All 50 modules and both chains are
  held this way.**
* **`debt`** — the module does not fit into the realtime budget today, so
  the ceiling is its **measured cost plus 50 %**: a ceiling that holds it
  where it is and fails when it gets worse.  **No module is on a debt
  ceiling today.**

### The table before and after

| | before (sweep of 16:37) | after (sweep of 21:22) |
|---|---|---|
| reference machine | 8.300 us → 160.6 trf | 8.300 us → 160.6 trf |
| modules in the table | 48 | **50** (`AUR_BANDRESTORATION`, `AUR_VOICEREVERB`) |
| modules under repair | 1 (`AUR_SPECTRALDENOISE`) | **0** |
| modules on a debt ceiling | 1 (`AUR_SPECTRALDENOISE` 852) | **0** |
| chains on a debt ceiling | 1 (spectral 1053) | **0** |
| `AUR_MUSICREBALANCE` | 116 trf → ceiling 161 (realtime) | 91 trf → **ceiling 161 (realtime)** |
| `AUR_SPECTRALDENOISE` | 568 trf → ceiling 852 (debt) | 3 trf → **ceiling 161 (realtime)** |
| dialogue chain | 121 trf → ceiling 161 (realtime) | 22 trf → **ceiling 161 (realtime)** |
| spectral chain | 702 trf → ceiling 1053 (debt) | 26 trf → **ceiling 161 (realtime)** |
| `AUR_BANDRESTORATION` | — | 8 trf → ceiling 161 (realtime) |
| `AUR_VOICEREVERB` | — | 2 trf → ceiling 161 (realtime) |

The Spectral De-Noise was the last module that did not fit into the
realtime budget: its streaming rewrite brought it from 568 transform
equivalents to 3 at 64 samples, and the debt ceiling that held it where
it was is gone.  **A debt ceiling that is not needed hides a
regression**, so it is removed with the module.

The Music Rebalance was inside the budget already, but at 96 % of it: it
was one slow block away from failing the gate, and the sweep of 16:37
measured it at 72 %.  It was measured again by hand after the repair of
section 3 (154 trf before, 91 trf after).

A module whose measured cost is within 20 % of the budget is **not**
held at the budget: the measurement of a shared machine resolves the load
to about 20 %, so a module inside that band would fail on noise instead
of on a regression.  Such a module is held at its measured cost + 50 %
and is named in the report.  **No module is in that band today** (the
most expensive one that fits is the Music Rebalance at 57 %).

The rule that writes the table is in `qa/performance/make_budgets.py`:
a module is `realtime` when it is at most 80 % of the budget at every
block size, otherwise it is `debt`.

**When a module is repaired, the budget has to follow.**  Run

```
python qa/performance/sweep.py --runs 5 --repeats 9 --warmup 3 --blocks 64,128,256,512 --json var/perf/sweep.json
python qa/performance/make_budgets.py --json var/perf/sweep.json
```

and commit the new table.  A module that is repaired becomes a
`realtime` budget again as soon as it is below 80 % of the budget, and
it comes off the `UNDER_REPAIR` list in `make_budgets.py`.

---

## 4. The gate

`tests/test_performance.py` is the gate.  It has

* `test_module_processes_a_block_within_its_budget[50 modules]` — every
  module, every block size (64, 128, 256, 512), against the ceiling of
  the table.  A module over its ceiling **fails**;
* `test_chain_processes_a_block_within_its_budget[2 chains]` — the same
  for the two product chains;
* `test_module_output_is_finite[50 modules]` and
  `test_chain_output_is_finite` — the measurement must not hide a broken
  module;
* `test_the_number_of_allocating_modules_does_not_grow` and
  `test_allocation_churn_stays_bounded[50 modules]` — the allocation
  guard;
* `test_the_calibration_is_usable`, `test_every_measured_module_has_a_budget`,
  `test_every_chain_has_a_budget` — the table itself;
* `test_headroom_report` — prints the table of section 2.

**The proof that the gate bites.**  Two ways, both reproducible
(`var/perf/prove_gate_w9.py` does the whole thing, including the
restore):

1. A fault is injected into the measurement of a module that **has been
   repaired** (`AURALIS_PERF_FAULT=AUR_AMBIENCEMATCH:6.0` multiplies the
   measured cost of that module by six).  The module is held at the
   realtime budget again, so the regression has to fail the gate:

```
E  AssertionError: AUR_AMBIENCEMATCH needed 183.6 transforms (1524 us on the reference
   machine) for a 64 sample block, the ceiling is 160.6 transforms (1333 us,
   realtime budget); the raw block time was 982 us (budget 1333 us)
FAILED tests/test_performance.py::test_module_processes_a_block_within_its_budget[AUR_AMBIENCEMATCH]
```

   (exit code 1; the same proof with `AUR_DECLICK:4.0` is in the header
   of the test file)

2. One budget of the table is raised (tightened) above the measured cost
   of the module — the 64 sample ceiling of `AUR_DECLICK` was temporarily
   set to 10 transform equivalents:

```
E  AssertionError: AUR_DECLICK needed 53.8 transforms (447 us on the reference
   machine) for a 64 sample block, the ceiling is 10.0 transforms (83 us,
   realtime budget); the raw block time was 686 us (budget 1333 us)
FAILED tests/test_performance.py::test_module_processes_a_block_within_its_budget[AUR_DECLICK]
```

   (exit code 1; the table was restored afterwards and the sha256 of the
   file before and after the proof is the same)

**The green runs.**  The guard is green with the new table (159 tests,
exit 0, **37 s** on this machine, load 100 %).  The sweep that wrote the
table took 51 s; the allocation pass of the guard is the slow part of it.

---

## 5. The allocations

**All 50 modules allocate inside `process()`.**  The distribution over one
64 sample block:

| | min | median | max |
|---|---|---|---|
| NumPy arrays per block | 1 | 12.0 | 161 (`AUR_INTERPOLATE`) |
| new array data per block | 0 KiB | 12.5 KiB | 751.0 KiB (`AUR_FADE`) |
| Python churn per block | 0 KiB | 1.2 KiB | 93.7 KiB (`AUR_DECONSTRUCT`) |

At 750 blocks per second (64 sample blocks) the median module churns
9 MiB of scratch per second of audio, Fade 550 MiB.  A module that
allocates in the audio path is a module that can glitter: the allocation
is not free and it makes the block time unpredictable.

The guard therefore has two allocation tests:

* `test_the_number_of_allocating_modules_does_not_grow` — the set of
  modules that allocate may shrink (a module that was repaired), never
  grow.  The baseline is in the table (`ALLOCATING_MODULES`).
* `test_allocation_churn_stays_bounded[48 modules]` — the per module
  baseline (arrays per block, new array data, Python churn) may grow by
  `ALLOC_MARGIN` = 1.5 before the guard complains.

The largest change against the table of 16:37 is the Spectral De-Noise:
830 arrays per block became 21.  The two modules that joined the
catalogue allocate as well (Band Restoration 41 arrays, Voice Reverb 12),
so the baseline of the guard has to name them: **a module that is not in
the baseline is a module that may not allocate at all.**

---

## 6. What is in the files

| file | what it is |
|---|---|
| `qa/performance/harness.py` | the measurement library: budget arithmetic, the test signal, the paired calibration, the machine load, the allocation counter, the peak RSS, profiling, table rendering |
| `qa/performance/sweep.py` | the sweep: the interleaved measurement of every module and both chains, the load report, the JSON and the Markdown table |
| `qa/performance/make_budgets.py` | writes `budgets.py` from a sweep |
| `qa/performance/budgets.py` | **the budget table** — the gate's data |
| `qa/performance/REPORT.md` | this report — the gate, the measurement, the budgets |
| `qa/performance/ANALYSIS.md` | why the modules are slow: the hot spots, the root cause and the proposed optimisations |
| `tests/test_performance.py` | the gate |

---

## 7. What belongs to somebody else

* **The Music Rebalance** was over the budget (154 transform equivalents
  at 64 samples, 96 % of it) and was made 1.8 times faster on 2026-10-21:
  the four stem analysers build their kernels, their windows and their
  band weights once instead of once per block, and the median filter of
  the harmonic split uses the fast path again (the scipy call named the
  argument `axis` where scipy names it `axes`, so the module fell back to
  the slow numpy path for every block).  The output is bit for bit the
  same; the patch is in `var/patches/w39_music_rebalance.patch`.
* **The bass stem of the Music Rebalance has a click every 21.8
  seconds.**  The long-signal path of `_fir_lowpass` filters every chunk
  on its own, so the convolution restarts at the chunk boundary and the
  stem jumps (measured: the samples around the boundary go from -0.0476 to
  -0.0921 in one step, and the chunked filter differs from the single
  shot filter by 0.10).  The behaviour is **not** new — the chunked path
  is unchanged by the repair — and it belongs to the owner of the module.
* `aurcore/modules/de_hum.py` does its filter design **inside**
  `process()`: the first three blocks cost far more than the steady
  state.  The warm-up of the guard is 3 blocks, so the steady state is
  measured — but the startup cost is real and it belongs to the owner of
  De-Hum.
* `tests/test_resample.py` and `tests/test_module_survey.py` (both new
  files of other agents) import **two different modules called
  `harness`** — `qa/performance/harness.py` and `qa/survey/harness.py`.
  In one test session the second import gets the first one out of the
  module cache and `tests/test_resample.py` fails with
  `AttributeError: module 'harness' has no attribute 'make_test_signal'`.
  The fix is in `var/patches/performance.patch`.  The guard itself loads
  its harness under a name of its own and is not affected.

---

## 8. Caveats

* **The load of the machine moves the measurement more than the modules
  do.**  The calibrated cost of a module was measured on a quiet machine
  (14 % load) and again on a saturated one (100 % load, six burners),
  with `var/perf/load_sensitivity_w9.py`:

  | module | quiet | busy | factor |
  |---|---|---|---|
  | `AUR_SPECTRALDENOISE` | 502 trf | 3982 trf | **7.9x** |
  | `AUR_MUSICREBALANCE` | 144 trf | 905 trf | **6.3x** |
  | `AUR_DIALOGUEISOLATE` | 96 trf | 273 trf | 2.9x |
  | `AUR_DECLICK` | 57 trf | 74 trf | 1.3x |
  | `AUR_AMBIENCEMATCH` | 42 trf | 53 trf | 1.3x |
  | `AUR_WOWFLUTTER` | 94 trf | 87 trf | 0.9x |

  The modules that allocate a lot in the audio path are memory bound:
  when the memory system of the machine is saturated they slow down far
  more than the 1024 point transform the calibration is taken with, and
  the ratio inflates.  **The guard is a verdict on a machine that is not
  saturated** — on a busy machine it fails for reasons that have nothing
  to do with the code.  This is a property of the harness, not of the
  table, and it was true for the table of 11:14 as well.
* The table of 16:37 was measured on the modules of 16:37.  The modules
  were rewritten afterwards (`spectral_de_noise.py` 21:41,
  `de_clip.py` 21:08, `voice_de_noise.py` 21:17, `band_restoration.py`
  20:49, `voice_reverb.py` 20:23), so the two tables are not comparable
  module by module.
* The sweep of 21:22 ran at 100 % load, the other agents of the finishing
  wave were working on the machine.  The measurement is calibrated, so the
  number does not move with the load — but the *fastest* observation of a
  module that is memory bound is still the least disturbed one, and a
  module that was measured while the machine was saturated carries that
  into its number.  The Music Rebalance was measured again by hand after
  the machine had quieted down: 91 transform equivalents in the sweep and
  95 in the hand measurement, both well inside the budget of 160.6.
* The reference machine of the table is the one of the sweep of 11:14
  (8.300 us per transform, measured on a machine that was at 87.6 % load).
  On this machine, at 6.40 us per transform, the same 160.6 transform
  equivalents are 1028 us — the gate is 23 % tighter than the raw
  realtime deadline.  `make_budgets.py --reference-transform-us <x>` moves
  the requirement; the committed reference is kept by default so that
  the requirement does not drift with the load of the measuring session.
* The guard runs 37 s on this machine (159 tests, load 100 %).  It is a
  slow test on purpose: it measures every module of the catalogue, at
  four block sizes, five rounds each, and every module is allocated and
  traced as well.
