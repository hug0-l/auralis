# The benchmark — our modules against the recorded reference

**What this is.**  `qa/benchmark/` measures **our** modules on the cases of the
corpus and of the voice report, and puts the numbers next to the numbers a
study of two commercial restoration products recorded.  It is the instrument
that keeps the gap closed: a module that falls behind a recorded number is
**reported**, with the size of the gap.

**The rule.**  A reference is a **target, not a contract**.  The numbers were
measured on somebody else's build, on somebody else's machine, with somebody
else's settings.  A gate that failed on them would be a gate held by a machine
we do not control, so the runner reports a module that is behind and only
fails when the instrument itself is broken.

```
python qa/benchmark/run_benchmark.py                    # the table
python qa/benchmark/run_benchmark.py --case hiss        # one case
python qa/benchmark/run_benchmark.py --json out.json    # machine readable
```

The code: `cases.py` (the cases), `reference.py` (the recorded numbers),
`reference_surface.py` (the recorded surfaces), `harness.py` (the
measurement), `run_benchmark.py` (the runner).  The gate is
`tests/test_benchmark.py`.

The other `reference_*.py` files of this directory belong to the other
landing work of this phase: they hold the constants of the masking laws, of
the anatomy of a learned model, of the restoration study and of the voice
head to head.  They are read by the design note, not by the runner of this
report.

---

## 1. The reference numbers

Two reference series were recorded.  They are described by their shape, never
by their names:

| series | what it is | parameters |
|---|---|---|
| `stem_separation` | the reference that splits a recording into three stems and gives each of them a gain and three switches | 17 |
| `band_restoration` | the reference that picks an algorithm and gives it four bands | 22 |

The numbers live in `qa/benchmark/reference.py`, one constant per number, each
with the case it belongs to, the unit and the way it was measured.  The
surfaces live in `qa/benchmark/reference_surface.py`.

### 1.1 How they were measured

Every number was measured **with the ruler of this harness**, so that the two
sides of the table are measured with the same code:

* the corpus cases are measured with the bands of the corpus manifest and the
  quality instrument of the project (`qa/quality/measurement.py`);
* the voice cases are measured with the voice instrument of the project
  (`qa/qualityvoice/harness.py`);
* the reference was rendered through the study's independent VST3 host, which
  loads a plug-in the way a host does, and the render was then measured here.

The **setting** of the reference is part of the number.  The study's rule is
the one the project already writes down: a setting is taken when it removes
the most artefact *and* the wanted content stays inside the budget of our own
rules (1 dB for the spectral cases, 1.5 dB for the voice cases).  Where the
study had already made that choice, the number is the one the study recorded;
where it had not, the same rule was applied to the renders of the study's host.

The **cost** is the one exception: it cannot be counted for a plug-in we
cannot see inside, so the reference's number is a **timed** one, taken on the
**study machine**, while ours is **counted**, here.  The two sides were
measured on two different machines and they only share one **fixed
yardstick**: one 1024 point transform costs **8.300 us**, so a 64 sample block
of real time at 48 kHz (1333.3 us) may cost **160.6426** of them.  The
reference's timed figure is converted into that yardstick.  The yardstick is
fixed on purpose: a budget that moves with the machine that measures it is an
echo, not a budget.  See 1.4 and 4.3.

### 1.2 The corpus cases

The artefact is the band of the corpus manifest that carries nothing but the
artefact; the wanted content is the partial the manifest names.

| case | artefact band | wanted | module of ours | series | removed (dB) | kept (dB) | gain var (dB²) | latency (samples) |
|---|---|---|---|---|---|---|---|---|
| `hiss` | 20–20000 Hz, whole clip | — | Spectral De-Noise | stem separation | 21.91 | — | 0.118 | 2238 |
| | | | | band restoration | 49.19 | — | 0.019 | 3880 |
| `pink_hiss` | 20–20000 Hz, whole clip | — | Spectral De-Noise | stem separation | 19.81 | — | 2.711 | 2238 |
| | | | | band restoration | 42.61 | — | 0.228 | 3880 |
| `hum50` | 45–55 Hz and 95–105 Hz | 1000 Hz | De-Hum | stem separation | 0.04 | 0.006 | 0.0004 | 2238 |
| | | | | band restoration | 67.00 | 26.86 | 36.90 | 3880 |
| `rumble` | 20–80 Hz | 1000 Hz | De-Hum | stem separation | 0.04 | 0.006 | 8.884 | 2238 |
| | | | | band restoration | 53.87 | 44.73 | 17.62 | 3880 |
| `voice_hiss` | 5000–20000 Hz, side channel | 130 Hz | Spectral De-Noise | stem separation | 296.45 | 0.013 | 0.025 | 2238 |
| | | | | band restoration | 14.22 | 5.263 | 0.046 | 3880 |

The **296.45 dB** of the `voice_hiss` case is the floor of the instrument: the
reference silences the difference between the two channels, where the bed of
that clip lives, so the level of the band reaches the floor.  The number says
*the bed is gone*; it is not a figure of signal to noise, and the gap of
282 dB in the table below must be read that way.

The **67 dB** and **53.9 dB** of the band restoration reference on `hum50` and
`rumble` are bought with a large movement of the wanted content (26.9 dB and
44.7 dB): the reference takes the wanted tone with the artefact.  The two rows
have to be read together.

### 1.3 The voice cases

The voice is read in the voice band, the bed in the bed band, and the chatter
is the drift of the applied gain.

| case | voice band | bed band | module of ours | series | removed (dB) | kept (dB) | chatter (dB) | latency (samples) |
|---|---|---|---|---|---|---|---|---|
| `corpus_voice` | 100–4000 Hz | 5000–20000 Hz | Voice De-Noise | stem separation | 0.0005 | 0.0063 | 0.0003 | 2238 |
| | | | | band restoration | 14.30 | 2.917 | 0.203 | 3993 |
| `synthetic_voice` | 120–3000 Hz | 5000–14000 Hz | Voice De-Noise | stem separation | 8.96 | 0.039 | 0.449 | 2238 |
| | | | | band restoration | 13.42 | 0.371 | 0.238 | 3993 |
| `speech_shaped` | 120–3000 Hz | 5000–14000 Hz | Voice De-Noise | stem separation | 0.0002 | 0.0001 | 0.0004 | 2238 |
| | | | | band restoration | 14.14 | 5.665 | 0.005 | 3993 |

The stem separation numbers of this table are the numbers the study recorded
(`agent218/out/voice_head_to_head.json`, the sweep of 18:59); the benchmark
re-measures the same renders and gets the same figures to five decimals.

### 1.4 The cost

Both sides of the cost are on **one yardstick**: one 1024 point transform
costs **8.300 us** on the reference machine, so a 64 sample block of real
time at 48 kHz (1333.3 us) may cost **160.6426** of them.  Ours is counted in
that unit; the reference's was timed on the study machine and converted into
it.

| series | the cost of one 64 sample block | unit | how it was measured | the machine |
|---|---|---|---|---|
| stem separation | 36.9466 to 51.4056 | 1024 point transforms of 8.300 us | 0.23 to 0.32 times the real time: 306.7 to 426.7 us per 64 sample block | the study machine, converted into the yardstick above |
| band restoration | 82.8313 to 346.3855 | 1024 point transforms of 8.300 us | 5.5 to 23.0 ms per 512 frame block: 687.5 to 2875.0 us per 64 sample block | the study machine, converted into the yardstick above |

**The number the study wrote down in transforms is not here.**  It was
recorded as 0.42 transforms per 64 sample block for the stem separation
series and 0.31 for the band restoration series, but it was taken against
**the transform of the study machine**, and the cost of that transform is
recorded nowhere in this tree, so the figure could not be converted into the
yardstick above.  A number whose provenance cannot be established is not kept:
it was removed and marked **unknown**.  The measurements it came from are
recorded in their own unit, and the conversion is stated in the method string
of every cost constant of `qa/benchmark/reference.py`.

**Ours is counted per 64 sample block**: the module is driven the way a host
drives it, block by block, over the signal of the case, and the transforms
the render performs are counted and spread over the blocks.  The figure an
earlier version of this table used — the cost of the whole clip render spread
over the blocks of that clip — is a different figure, and it hid the fixed
cost of the modules: the de-hum read 0.2959 transforms on it and really costs
4.9458 (41.1 us) per block, a factor of 16.7.

The cost belongs to the product and not to the material, so it is recorded
once, under the name `*`.

### 1.5 The delay

The delay of a product belongs to the product too, so it is recorded once per
series, under the name `*`:

| series | delay | unit | how it was measured |
|---|---|---|---|
| stem separation | 1470 | samples | the smallest delay the study's host measured on its renders (1470 at a block of 256, 2238 at a block of 1024) |
| band restoration | 3880 | samples | the delay the study's host measured on the renders (3880 to 4641, depending on the block) |

The per case delays are in `reference.py` as well; the benchmark holds a case
to the **smallest** delay of the two series, because that is the delay a host
with a small block sees.

### 1.6 Where the numbers come from

| the number | where it was taken |
|---|---|
| the surfaces (`reference_surface.py`) | the study's report of the surface comparison, with the constants it proposes to land |
| the voice cases of the stem separation series | the study's head-to-head on the voice, its measurement file of 18:59 |
| the cost of the reference | the study's cost measurement, on the study machine: 0.23 to 0.32 times the real time for the stem separation series and 5.5 to 23.0 ms per 512 frame block for the band restoration series, converted into the yardstick of the project (8.300 us per 1024 point transform) |
| the cost of ours | counted here, block by block, by the harness of this benchmark (`qa/benchmark/harness.py`), on the signal of the case and with the channels of the case |
| the delay | the delay the study's host measured on the render |
| the corpus cases | the study's head-to-head on the noise had not landed when this was written, so the numbers were measured from the study's renders: the two references were rendered through the study's own independent VST3 host and the renders were then measured with this harness's ruler, with the study's rule for the setting applied to them |

Every number is a number: no name of a product and no name of a parameter of
a product appears in the tree.  The two references are described by their
shape, the way the rest of the tree describes them.

The numbers of the table below belong to the tree of the moment they were
measured (2026-10-21, the evening of the landing phase).  The modules were
being changed in parallel while this was written, so the "ours" column moves
as the modules move; the reference column does not.  The runner prints the
table of the tree it runs in, so the instrument is always the current one.

---

## 2. The table

Our modules against the recorded references, measured on 2026-10-21 with the
tree of that moment.  A **positive** gap means we are ahead.

```
case             module               metric             ours   reference  gap       verdict     
---------------  -------------------  -----------------  -----  ---------  --------  ------------
hiss             AUR_SPECTRALDENOISE  removed_db         13.54  49.19      -35.66    behind      
hiss             AUR_SPECTRALDENOISE  kept_db            -      -          -         no reference
hiss             AUR_SPECTRALDENOISE  gain_var_db2       0.06   0.02       -0.04     behind      
hiss             AUR_SPECTRALDENOISE  latency_samples    0      1470       +1470.00  ahead       
hiss             AUR_SPECTRALDENOISE  cost_transform_eq  4.72   36.95      +32.23    ahead       
pink_hiss        AUR_SPECTRALDENOISE  removed_db         17.30  42.61      -25.30    behind      
pink_hiss        AUR_SPECTRALDENOISE  kept_db            -      -          -         no reference
pink_hiss        AUR_SPECTRALDENOISE  gain_var_db2       0.55   0.23       -0.32     behind      
pink_hiss        AUR_SPECTRALDENOISE  latency_samples    0      1470       +1470.00  ahead       
pink_hiss        AUR_SPECTRALDENOISE  cost_transform_eq  4.72   36.95      +32.23    ahead       
hum50            AUR_DEHUM            removed_db         39.64  67.00      -27.37    behind      
hum50            AUR_DEHUM            kept_db            0.04   0.01       -0.04     behind      
hum50            AUR_DEHUM            gain_var_db2       0.80   0.00       -0.80     behind      
hum50            AUR_DEHUM            latency_samples    0      1470       +1470.00  ahead       
hum50            AUR_DEHUM            cost_transform_eq  4.95   36.95      +32.00    ahead       
rumble           AUR_DEHUM            removed_db         11.44  53.87      -42.44    behind      
rumble           AUR_DEHUM            kept_db            0.03   0.01       -0.03     behind      
rumble           AUR_DEHUM            gain_var_db2       6.28   8.88       +2.60     ahead       
rumble           AUR_DEHUM            latency_samples    0      1470       +1470.00  ahead       
rumble           AUR_DEHUM            cost_transform_eq  4.95   36.95      +32.00    ahead       
voice_hiss       AUR_SPECTRALDENOISE  removed_db         13.62  296.45     -282.83   behind      
voice_hiss       AUR_SPECTRALDENOISE  kept_db            0.00   0.01       +0.01     ahead       
voice_hiss       AUR_SPECTRALDENOISE  gain_var_db2       0.04   0.03       -0.01     behind      
voice_hiss       AUR_SPECTRALDENOISE  latency_samples    2624   1470       -1154.00  behind      
voice_hiss       AUR_SPECTRALDENOISE  cost_transform_eq  4.72   36.95      +32.23    ahead       
corpus_voice     AUR_VOICEDENOISE     removed_db         17.84  14.30      +3.55     ahead       
corpus_voice     AUR_VOICEDENOISE     kept_db            0.05   0.01       -0.05     behind      
corpus_voice     AUR_VOICEDENOISE     chatter_db         0.01   0.00       -0.01     behind      
corpus_voice     AUR_VOICEDENOISE     latency_samples    2047   1470       -577.00   behind      
corpus_voice     AUR_VOICEDENOISE     cost_transform_eq  1.09   36.95      +35.86    ahead       
synthetic_voice  AUR_VOICEDENOISE     removed_db         17.85  13.42      +4.43     ahead       
synthetic_voice  AUR_VOICEDENOISE     kept_db            0.13   0.04       -0.10     behind      
synthetic_voice  AUR_VOICEDENOISE     chatter_db         0.03   0.24       +0.21     ahead       
synthetic_voice  AUR_VOICEDENOISE     latency_samples    2047   1470       -577.00   behind      
synthetic_voice  AUR_VOICEDENOISE     cost_transform_eq  1.08   36.95      +35.86    ahead       
speech_shaped    AUR_VOICEDENOISE     removed_db         17.86  14.14      +3.73     ahead       
speech_shaped    AUR_VOICEDENOISE     kept_db            0.10   0.00       -0.10     behind      
speech_shaped    AUR_VOICEDENOISE     chatter_db         0.02   0.00       -0.02     behind      
speech_shaped    AUR_VOICEDENOISE     latency_samples    2047   1470       -577.00   behind      
speech_shaped    AUR_VOICEDENOISE     cost_transform_eq  1.09   36.95      +35.86    ahead       
```

The **cost** of the two sides, on the one yardstick, as the runner prints it:

```
the cost, both sides on one yardstick: the cost of one 64 sample block, in 1024 point transforms of 8.300 us (the budget of a block is 160.6 transforms = 1333.3 us)
  ours is counted, the reference's is timed on the study machine and converted into the same yardstick
  our block is the signal of the case, with the channels of the case and the block size of the table; the study did not write down the channel count or the material of its own measurement, so the reference's figure is a block of the study's own rendering
    case             module                   ours trf    ours us      ref trf       ref us % budget channels
    hiss             AUR_SPECTRALDENOISE          4.72       39.2        36.95        306.7     2.9%        2
    pink_hiss        AUR_SPECTRALDENOISE          4.72       39.2        36.95        306.7     2.9%        2
    hum50            AUR_DEHUM                    4.95       41.1        36.95        306.7     3.1%        2
    rumble           AUR_DEHUM                    4.95       41.1        36.95        306.7     3.1%        2
    voice_hiss       AUR_SPECTRALDENOISE          4.72       39.2        36.95        306.7     2.9%        2
    corpus_voice     AUR_VOICEDENOISE             1.09        9.1        36.95        306.7     0.7%        1
    synthetic_voice  AUR_VOICEDENOISE             1.08        9.0        36.95        306.7     0.7%        1
    speech_shaped    AUR_VOICEDENOISE             1.09        9.1        36.95        306.7     0.7%        1

  the reference's cost, per series, per 64 sample block on the study machine:
    band_restoration      82.8313 trf =    687.5 us  to  346.3855 trf =   2875.0 us
    stem_separation       36.9466 trf =    306.7 us  to   51.4056 trf =    426.7 us
```

The reference column of the cost rows of the table above is the **cheapest**
figure the study measured (the benchmark is held to the strongest reference),
so it is the 36.9466 transforms of the stem separation series.  The cost of
the band restoration series is 82.8313 to 346.3855 transforms, and every case
of ours is ahead of that figure by more as well.

When both series carry a number for a case and a metric, the **strongest** one
is the reference: the benchmark is held to the best of the references, because
that is the one worth closing the gap to.

---

## 3. The modules that are behind

20 of the 40 rows are behind a reference.  By module:

| module | behind on | by how much |
|---|---|---|
| **Spectral De-Noise** | `hiss` removal | 35.66 dB (13.54 against 49.19) |
| | `pink_hiss` removal | 25.30 dB (17.30 against 42.61) |
| | `hiss` gain variance | 0.04 dB² (0.06 against 0.02) |
| | `pink_hiss` gain variance | 0.32 dB² (0.55 against 0.23) |
| | `voice_hiss` removal | 282.83 dB, a floor artefact: the reference silences the bed of the clip, we attenuate it by 13.62 dB |
| | `voice_hiss` gain variance | 0.01 dB² |
| | `voice_hiss` latency | 1154 samples (2624 against 1470) |
| **De-Hum** | `hum50` removal | 27.37 dB (39.64 against 67.00) — but the reference moves the wanted tone by 26.86 dB to get there, we move it by 0.04 dB |
| | `hum50` damage | 0.04 dB |
| | `hum50` gain variance | 0.80 dB² |
| | `rumble` removal | 42.44 dB (11.44 against 53.87) — bought by the reference with 44.73 dB of damage to the wanted programme, we damage it by 0.03 dB |
| | `rumble` damage | 0.03 dB |
| **Voice De-Noise** | `corpus_voice` damage | 0.05 dB (0.05 against 0.01) |
| | `corpus_voice` chatter | 0.01 dB (0.01 against 0.00) |
| | `corpus_voice` latency | 577 samples (2047 against 1470) |
| | `synthetic_voice` damage | 0.10 dB (0.13 against 0.04) |
| | `synthetic_voice` latency | 577 samples (2047 against 1470) |
| | `speech_shaped` damage | 0.10 dB (0.10 against 0.00) |
| | `speech_shaped` chatter | 0.02 dB (0.02 against 0.00) |
| | `speech_shaped` latency | 577 samples (2047 against 1470) |

**What the gaps mean.**  Two of them are not gaps in the signal at all:

* the `hum50` and `rumble` removal of the band restoration reference is
  bought with the wanted content (26.9 dB and 44.7 dB of movement).  Our
  de-hum removes 39.6 dB of the hum and moves the tone by 0.04 dB: on the
  pair of rows together we are ahead, on the removal row alone we are behind.
  This is exactly why the benchmark keeps both rows;
* the `voice_hiss` removal of 296.45 dB is the floor of the instrument: the
  reference silences the difference between the channels, where the bed of
  that clip lives.

The gaps that are real:

* the spectral de-noise removes 13.5 dB of a steady white bed where the
  reference removes 49.2 dB, and 17.3 dB of a pink bed where the reference
  removes 42.6 dB.  The reference is a learned model with the bed in front of
  it; our module is an estimator with a cap.  The cap is the lever.
* our gain curve breathes more than the reference's on the noise beds
  (0.06 against 0.02 dB² on white, 0.55 against 0.23 dB² on pink).  The
  reference's gain is steady because its bed is separated, not estimated.
* the voice de-noise moves the voice of the corpus clip by 0.05 dB where the
  references move it by 0.006 dB and 2.92 dB, and it chatters 0.01 dB where
  the references chatter 0.00 dB and 0.20 dB.  The chatter is the defect the
  voice report already names: the gain of the module follows the material.
* the voice de-noise is late: 2047 samples on the voice cases, against 1470
  for the reference; 2624 samples on the corpus voice clip for the spectral
  de-noise, which is the estimator finding a peak in a signal it should not
  have moved (the module declares 0).  The voice de-noise is the one number
  of this benchmark that is ours to change without a new technique: it is the
  window of the analysis, not the algorithm.

**Where we are ahead.**  The latency on every noise case (0 samples against
1470 and 3880), the removal on the three voice cases (17.84 against 14.30,
17.85 against 13.42 and 17.86 against 14.14), the gain variance of the
de-hum on the rumble case (6.28 against 8.88), the chatter of the voice
de-noise on `synthetic_voice` (0.03 against 0.24) and **the cost, on every
case**: 1.08 to 4.95 counted transforms per 64 sample block against the
36.9466 timed ones of the cheapest reference setting, a lead of 32.00 to
35.86 transforms on every case.

---

## 4. The instrument

### 4.1 It is deterministic

Two runs of the runner give the same numbers, character for character:

```
python qa\benchmark\run_benchmark.py > a.txt
python qa\benchmark\run_benchmark.py > b.txt
fc a.txt b.txt      # identical
```

Nothing in the measurement moves: the cases are read from the corpus or built
from a fixed seed, the analysis geometry is a constant, no wall clock and no
unseeded random number take part, and the **cost is counted, not timed**.

### 4.2 It measures our modules with the product's own engine

The module is built from the registry (`aurcore.registry`), set with its own
parameters (the defaults — that is what a user gets) and driven through its
own `process` entry point.  The measurement is the product's own instrument:
the corpus yard (`corpus/measure.py`) and the quality instrument
(`qa/quality/measurement.py`) for the corpus cases, the voice instrument
(`qa/qualityvoice/harness.py`) for the voice cases.

### 4.3 The cost: counted here, timed there, on one yardstick

Ours is **counted**: the module is driven the way a host drives it, block by
block, over the signal of the case, and the number of 1024 point transforms
the render performs, weighted by `n log2(n)`, is spread over the 64 sample
blocks of the clip.  A plug-in we cannot see inside cannot be counted, so the
reference's number is **timed**, on the study machine, and then converted into
the same yardstick.

**The yardstick is fixed.**  One 1024 point transform costs **8.300 us** on the
reference machine, so a 64 sample block of real time at 48 kHz may cost
**1333.3 us = 160.6426 transform equivalents**.  The yardstick is a constant of
the project (`qa/performance/budgets.py`), and it is fixed on purpose: a
budget that moves with the machine that measures it is an echo, not a budget.
The reference's microseconds are converted with that fixed stick, and so is
the requirement.  The stick of the **session** — the cost of a transform on
the machine of the moment — is measured beside every timing of the
performance harness and is used to turn a wall-clock number into transform
equivalents; it is what the numbers of `qa/performance/budgets.py` are built
on, and it is not the requirement.

The two sides of the comparison were measured on **two different machines**,
and they only share that yardstick now:

| side | what the number is | how it was measured | on which machine |
|---|---|---|---|
| ours | counted, per 64 sample block | the module driven block by block over the signal of the case, with the channels of the case; the transforms counted | this machine |
| the reference | timed, per 64 sample block | 0.23 to 0.32 times the real time (306.7 to 426.7 us per block) and 5.5 to 23.0 ms per 512 frame block (687.5 to 2875.0 us per block) | the study machine, converted into the yardstick above |

| module | ours, counted per block (trf) | ours (us) | the reference, timed and converted (trf) |
|---|---|---|---|
| Spectral De-Noise | 4.7209 | 39.2 | 36.9466 to 51.4056 |
| De-Hum | 4.9458 | 41.1 | 36.9466 to 51.4056 |
| Voice De-Noise | 1.0836 to 1.0912 | 9.0 to 9.1 | 36.9466 to 51.4056 |

The two instruments do not measure the same thing, and the difference is
worth naming.  The counted number is a **floor**: it counts the transforms
and nothing else, so a module whose cost is filters and arithmetic is counted
below what it costs.  The wall-clock figures of our own modules are in
`qa/performance/budgets.py`; they are a number of this machine, they move with
its load, and they are in the report and not in the table, because a table
that moves with the load of the machine cannot be a gate.

**What the amortised figure hid.**  An earlier version of this table took the
cost of the whole clip render and spread it over the blocks of that clip.
That is a different figure from the cost of a block, and for a module whose
analysis is a fixed cost per call it is a much smaller one: the de-hum read
0.2959 transforms per block on that figure and really costs 4.9458 (41.1 us),
a factor of 16.7, because its filter bank does the same work for every block
the host hands it.  The figure the table uses now is the per block one, the
same figure the reference's is.

**The reference is a yardstick, not a threshold.**  The rule of this report is
that a reference is a target and not a contract: it is there to show the size
of the gap, and the product is not required to cross it.  Our modules cost
**7.5 to 34 times less** than the reference's cheapest measured setting
(1.08 to 4.95 against 36.9466 transform equivalents), and every case of the
table is ahead by 32.00 to 35.86 transforms.  That is a fact about the two
measurements, not a target: the budget the product is held to is the budget of
a 64 sample block (160.6426 transforms), and every module of ours is inside
it by a factor of 32 to 148.

### 4.4 A module behind a reference is reported, not failed

The runner prints the table, prints the rows that are behind, and exits `0`.
It exits `1` only when a case could not be measured at all — that is the
instrument being broken, not a module being bad.  The gate
(`tests/test_benchmark.py`) asserts this: it moves a reference out of reach,
runs the gate and checks that the row says *behind* and that the gate still
passes.

---

## 5. The gate list after this change

`scripts/run_gates.py` gained one gate, at the end of the list:

```
imports        finite         biquad          js-duplicates
pytest         schema         codegen-drift   css-coverage
verify-app     hygiene        lint            vst3-host
journey        benchmark
```

The gate is a **reporting gate**: it prints the table and the list of the rows
that are behind, and it passes.

The patches of this change are in `var/patches/`:

| patch | what it does |
|---|---|
| `w16_run_gates.py.patch` | adds the benchmark gate to `scripts/run_gates.py` (it is applied; the patch is the record of the change) |
| `w16_lint_trailing_whitespace.patch` | strips the trailing whitespace the lint gate reported in nine module files that other work in this phase left behind (it is applied) |

---

## 6. What was not measured, and why

* **The best setting of every reference.**  The study's head-to-head sweeps
  the settings of each reference and picks the one that removes the most
  artefact while the wanted content stays inside our budget.  Where the study
  had recorded that choice, the number here is the study's; where it had not,
  the same rule was applied to the renders of the study's host, but the sweep
  of the corpus cases was not repeated here.  The reference numbers are
  therefore a floor, not a ceiling, except where the default already removes
  everything.
* **The editors of the references.**  Only the automatable surface was read.
* **What the references do with the other cases.**  The benchmark covers the
  noise cases and the voice cases; the click, the clip, the reverb and the
  rest of the corpus are not in it.
* **The tail.**  The benchmark measures the latency, not the ring-out.
* **The geometry of the reference's cost.**  The study did not write down the
  block size, the channel count or the material of its own cost measurement,
  so the reference's figure is a block of the study's own rendering and not a
  block of the same signal, with the same channels, as ours.
* **The block coherence.**  The quality instrument measures it and the rules
  hold our modules to it; the reference's block coherence was not measured
  here.
