# Auralis — what a model costs

> **What this document is.**  What a shipped model costs, what the budget it is
> held to is, and **why a model is an offline capability even though the
> measurement says it would fit into the real time path as well**.  That last
> part is a policy of the product, and a policy has to be written down, or it
> turns into a superstition in a later release.
>
> **The numbers are in**
> [`qa/performance/model_budgets.py`](../qa/performance/model_budgets.py), and they are
> measured by [`qa/performance/sweep_models.py`](../qa/performance/sweep_models.py).  The
> gate is [`tests/test_model_budget.py`](../tests/test_model_budget.py).  Every count in
> this document is checkable, and the command that reproduces it is next to it.
>
> **No third party name appears in this document.**  The gate is
> [`../tools/hygiene_check.py`](../tools/hygiene_check.py).
>
> The binding rules live in [`../CONTRACT.md`](../CONTRACT.md).  Where this document and
> the contract disagree, the contract wins.  The realtime budget of the hand written
> modules is in [`ARCHITECTURE.md`](ARCHITECTURE.md) §4 and
> [`qa/performance/REPORT.md`](../qa/performance/REPORT.md).

---

## 1. The rule

**A model belongs to the offline path.**  A module that runs in real time keeps its
hand written implementation: the hand written modules are exact, deterministic and
cheaper, so they stay the default, and a model is offered where the extra quality is
worth the wait for a render.

The rule is **a policy, not a technical necessity**.  The measurement below says that
every model we ship *except one* would fit into the real time budget of a 64-sample
block with room to spare.  We keep the rule because the hand written path is the better
default for a live signal — not because a model cannot keep up.

## 1b. Two budgets, because one rule was not enough

A model is held to one of **two** budgets, and the table says which:

| the budget | the rule | who is held to it |
|---|---|---|
| **`block`** | the cost of one 64-sample block must stay inside the realtime budget (160.6 transforms) | the models that are cheap enough to be measured reliably on a shared machine |
| **`speed`** | the render may take at most **2.0 times** the length of the audio | a model that is too close to the edge of the realtime budget to be measured on a machine whose load moves |

Why the second budget exists: the live check of the gate runs on a shared machine, and
the load of that machine moves a measurement by a factor of two.  A model that takes a
quarter of the realtime budget survives that.  A model that takes **63.3 %** of it does
not: it would fail the gate on a busy machine rather than on a model that grew.  So the
model above the share is held to the speed of the offline path instead, which it
clears with a margin of the same kind — **0.786 times** the real time against a ceiling
of **2.0**.

The share is a number of the project (`LIVE_SAFE_SHARE = 0.25`), not a phrase, and the
budget of each model is **committed with the table**: a model that grows past its
budget fails the gate, and it is never re-classified by the next sweep.

## 2. The yardstick, and it does not move

The realtime budget of a block is the time the block needs to be played: `block /
48000` seconds.  At 48 kHz a **64-sample block has a budget of 1333.3 µs**.

The budget is not written in microseconds of *this* machine, because the machine that
measures is not the machine the plug-in runs on.  It is written in **transform
equivalents**: how many 1024-point transforms a block may cost.

| | value | what it is |
|---|---|---|
| the reference transform | **8.300 µs** | the cost of one 1024-point transform on the reference machine |
| the budget of a 64-sample block | **1333.3 µs** | `64 / 48000` seconds |
| the budget in transform equivalents | **160.6 trf** | `1333.3 / 8.300` |

**8.300 µs is a constant of the project.**  The yardstick of a *session* moves with the
load of the machine — during the sweep below it was measured between 11.1 µs and
29.5 µs — and a requirement that moves with the load of the measuring session is not a
requirement.  The session yardstick is measured, kept next to the table
(`SESSION_TRANSFORM_US`) and used to convert a timing into transform equivalents; the
requirement stays on the reference.

The same yardstick is used by the module budget table
([`qa/performance/budgets.py`](../qa/performance/budgets.py)), so a model and a module
are held to the same stick.

## 3. The measurement

A model runs on the offline path: it is given a whole signal and it produces a whole
signal.  The cost of that path is therefore measured over a whole signal and then
amortised over the blocks of that signal, so that it can be compared with the budget
of a block.

* **What was run:** the shipped models, at their own rate of 48 kHz, over **10 seconds
  of audio** each, 8 rounds per model, the rounds interleaved so that every model is
  sampled over the whole sweep.
* **How it was timed:** every run is timed next to a batch of 1024-point transforms of
  the same machine, taken on both sides of the run.  The ratio of the two is the cost
  in transform equivalents and does not depend on how busy the machine was.
* **Which run is recorded:** the **fastest** round — the observation that was least
  disturbed by the other work on the machine.  The spread of the rounds is recorded in
  the note of every entry, so the noise is visible.
* **The signal:** the deterministic test signal of the harness, mono, speech-like.  A
  run with white noise instead lands in the same band, so the content of the material
  does not decide the number.

The commands:

```
python qa/performance/sweep_models.py --runs 8 --json var/perf/models.json
python qa/performance/make_model_budgets.py --json var/perf/models.json
```

## 4. The numbers

| model | problem | parameters | µs per 64-sample block | trf | µs on the reference machine | % of the budget | × the real time | the budget |
|---|---|---|---|---|---|---|---|---|
| `voice_enhance` | voice | 6 800 | 10.6 | 1.3 | 10.7 | **0.8 %** | 0.008 | `block` |
| `voice_denoise_mask` | voice | 18 608 | 11.9 | 1.4 | 11.8 | **0.9 %** | 0.009 | `block` |
| `voice_reverb_reduce` | voice reverb | 12 944 | 11.2 | 1.2 | 9.9 | **0.7 %** | 0.008 | `block` |
| `ambience_match` | ambience | 9 872 | 15.5 | 1.9 | 15.7 | **1.2 %** | 0.012 | `block` |
| **`voice_sample_mask`** | voice | 6 520 | 1060.1 | 116.5 | 966.9 | **72.5 %** | **0.795** | `speed` |
| **`voice_sample_memory`** | voice | 7 008 | 1577.7 | 164.4 | 1364.1 | **102.3 %** | **1.183** | `speed` |

The session that took these numbers measured the machine at **6.50 µs** per transform
and the load at **9.4 %**; the requirement stays on the reference machine (8.300 µs), so
the table and the machine do not have to agree.

**These numbers are eight to ten times better than the ones this document carried
before**, and the reason is not a faster machine: the offline path of a model now
evaluates the frames of a file **in one pass** instead of one Python call per frame
(`aurcore/models/engine.py`, `ModelRunner.process_batch`).  The batch keeps the
accumulation order of the frame-by-frame path, so the **output of every model is bit
for bit the output it produced before** — the models did not change, they were only
measured on a path that does the same arithmetic without paying for the interpreter
in between.

The two sessions that took the numbers of the table and of the previous session of this
document say the same thing about the frame-rate models — **they use between one and
twelve per cent of the realtime budget** — and they say different things about the
per-sample model, which is the one that is close to the edge.

## 4b. The per sample model

`voice_sample_mask` is the **per sample** model: a frame of 128 samples and a hop of
**one sample**, so it is asked 48 000 times a second.  It is the capability the
competitive study ruled out for the real time path, and the offline path can afford it:

| | the frame-rate model | the per sample model |
|---|---|---|
| the layout | 1024 / 256 | **128 / 1** |
| the questions a second | 187.5 | **48 000** |
| the cost | 0.008 × the real time | **0.795 × the real time** (mono), 1.11 × (stereo, the mix path) |
| the share of the realtime budget | 0.8 % | **72.5 %** |
| the budget it is held to | `block` | **`speed`** (2.0 × the real time) |

It fits inside the realtime budget of a block — 72.5 % of it — but it is held to the
offline budget instead, because 72.5 % is too close to the edge to be measured
reliably on a machine whose load moves (§1b).

What it buys, measured on a damaged phone memo (a voice over a mains hum, a rumble, a
hiss and a DC offset, all of them ours, built for this):

| band | the memo | the frame-rate model | the per sample model |
|---|---|---|---|
| the hum 45–55 Hz | −37.05 dBFS | −11.13 dB | **−15.62 dB** |
| the hiss 5–20 kHz | −47.84 dBFS | −1.02 dB | **−6.77 dB** |
| the rumble 20–80 Hz | −32.25 dBFS | −11.59 dB | **−14.54 dB** |
| the voice 120–3000 Hz | −37.90 dBFS | +2.49 dB | **−0.81 dB** |

```bash
python qa/make_demo_memo.py      # the material
python qa/measure_per_sample.py   # the cost and the table
```

⇒ The per sample model takes **4.5 dB more of the hum, 5.8 dB more of the hiss and
3.0 dB more of the rumble** than the frame-rate model, and costs **0.81 dB** of the
voice where the frame-rate model *gains* **2.49 dB** of it.  It is the first model in
the library that is *better* than the hand written path on real material rather than on
a synthetic case.

Two things are true at the same time and both belong here: **the model reads a
2.7 ms window**, so its estimate of a band is noisier than the estimate of a 21 ms
window, and **the decision is made on the mix** of the channels, so the image does not
move.  The second is a choice of the offline path (`run_offline_mix`), the first is the
price of the sample rate.

The cost of the table is the one of the **sweep** (mono, the quiet machine).  The
**stereo** path — the one a render of a stereo file takes — was measured at **1.11 ×**
the real time in the session of the table above and at **1.31 ×** while the machine was
busy with other work; both are inside the offline budget of 2.0 ×.

## 4c. The model with a memory

`voice_sample_memory` is the **second kind of network** the manifest knows: a
stack of three **causal** convolutions over the time axis.  The window is 128
samples and the hop is one sample, as in the per sample model; the reach of
the stack is **547 frames** — 11.4 ms at 48 kHz — and the context the
manifest publishes is the 546 frames before a frame.  7 008 parameters,
32 900 bytes on disk, `epoch 2000`, loss 34.21 → 1.688.

| | the per sample model | the model with a memory |
|---|---|---|
| the network | a fully connected stack over one frame and one slope | a causal convolution stack over the time axis |
| the history it reads | one frame (1.3 ms) | 546 frames (11.4 ms) |
| the parameters | 6 520 | **7 008** |
| the cost, mono (the sweep) | 0.795 × the real time | **1.183 ×** |
| the cost, stereo (the mix path) | 1.11 × the real time | **1.57 ×** |
| the share of the realtime budget of a block | 72.5 % | **102.3 %** — it does **not** fit in a real time host |
| the peak working set of a 6 s render | 1 836 MB | **1 840 MB** |

⇒ The memory costs **0.39 × the real time** over the per sample model in mono
(0.46 × in stereo) and **4.4 MB** of memory, and the peak is the frames of
the whole file rather than the convolution.  Both costs are inside the
**offline budget** of 2.0 ×; the realtime budget of a block is one the
model with a memory does **not** fit into, which is why it is held to the
offline one.

What it buys, on the same damaged phone memo:

| band | the memo | the frame-rate model | the per sample model | the model with a memory |
|---|---|---|---|---|
| the hum 45–55 Hz | −37.05 dBFS | −11.13 dB | **−15.62 dB** | −14.11 dB |
| the hiss 5–20 kHz | −47.84 dBFS | −1.02 dB | **−6.77 dB** | −5.78 dB |
| the rumble 20–80 Hz | −32.25 dBFS | −11.59 dB | **−14.54 dB** | −13.12 dB |
| the voice 120–3000 Hz | −37.90 dBFS | **+2.49 dB** | −0.81 dB | +0.41 dB |

⇒ The model with a memory takes **more of every artefact than the frame-rate
model** and **less than the per sample model**, and it keeps **1.22 dB more of
the voice** than the per sample model does.  It is a third point on the same
trade-off, not a better point.

**The two changes have to be told apart.**  The model with a memory differs
from the per sample model in the network *and* in the **time scale of the
material it was taught**: the per sample model was taught a voice whose
syllables last 40 to 160 samples, the model with a memory one whose
syllables last 60 to 250 milliseconds, because a memory of eleven
milliseconds cannot learn anything from a syllable that lasts one.
`qa/compare_memory.py` trains the per sample architecture **on the material
of the model with a memory** and renders the memo with all three, so the
difference between the two per sample models says what the *material* is
worth and the difference between the second and the third says what the
*memory* is worth:

| band | the memo | the per sample model | the same architecture, the new material | the model with a memory |
|---|---|---|---|---|
| the hum 45–55 Hz | −37.05 dBFS | −15.62 dB | **−16.07 dB** | −14.11 dB |
| the hiss 5–20 kHz | −47.84 dBFS | −6.77 dB | **−6.88 dB** | −5.78 dB |
| the rumble 20–80 Hz | −32.25 dBFS | −14.54 dB | **−14.97 dB** | −13.12 dB |
| the voice 120–3000 Hz | −37.90 dBFS | −0.81 dB | −1.05 dB | **+0.41 dB** |

⇒ **The material is worth a little more removal** — 0.45 dB of the hum, 0.11 dB
of the hiss, 0.43 dB of the rumble — and 0.24 dB more of the voice.

⇒ **The memory is worth steadiness, not more removal.**  With the same
material and a comparable number of parameters (7 008 against 6 520), the
convolution stack takes **1.96 dB less of the hum**, 1.10 dB less of the hiss
and 1.85 dB less of the rumble, and it keeps **1.46 dB more of the voice**.
A model that looks at the material before it does not over-react to a
transient, and a removal that does not over-react takes less away.

⇒ That is the honest verdict on this material: **the memory bought a
gentler decision, not a better one**, and it cost 0.39 × the real time for
it.  What it would take for the memory to pay for itself is a decision that
is *wrong* without it — a case where the material before the frame says
something the frame alone cannot.  The damaged phone memo does not have
such a case; a material where a wanted transient follows an artefact would.

**The head of the file is decided on an empty memory.**  The frames before
the file are not there, and a convolution reads them as zero: the first
546 frames (11.4 ms) of a render are decided without any memory.  On the
memo the head comes out **20.4 dB below** the rest of the file, against
**52.2 dB below** for the per sample model.

```bash
python qa/make_demo_memo.py      # the material
python qa/measure_memory.py       # the cost, the quality, the head
python qa/compare_memory.py       # what the memory is worth
```

## 5. The gate

**[`tests/test_model_budget.py`](../tests/test_model_budget.py)** is the gate.  It
checks four things, and it fails loudly when one of them does not hold.

| the gate | what it fails on |
|---|---|
| every model of the library has a budget | a model was added and nobody measured what it costs |
| every budget names a model the library holds | the table and the library have drifted apart |
| the offline budget is a number | a model is held to a budget nobody wrote down |
| the recorded cost fits inside the budget | a model grew past the budget it is held to |
| the cost measured **now** fits inside the budget | the same, without waiting for the table to be regenerated |

The recorded numbers are committed, exactly like the module budget table: a budget
that were regenerated on every test run would always equal the measurement and would
never catch anything.

The gate bites.  With a fault injected it names the model and says by how much:

```
AURALIS_MODEL_FAULT=voice_enhance:20.0 python -m pytest tests/test_model_budget.py -q
```

```
the model 'voice_enhance' needs 1479.8 us per 64 sample block, which is 146.5 us
too much: it is 11.0 % over the budget of 1333.3 us (160.6 transforms of 8.300 us)
```

A model that is added tomorrow and is twenty times as expensive fails the gate before
it reaches a user.  That is the whole point of writing the numbers down.

And on the model that is held to the offline budget the gate says the other thing:

```
AURALIS_MODEL_FAULT=voice_sample_mask:3.0 python -m pytest tests/test_model_budget.py -q
```

```
the model 'voice_sample_mask' takes 2.22 times the real time, which is 0.22 times
too much: the offline budget is 2.00 times the real time
```

## 6. What these models are **not**

The measurement above is about five small artefacts.  It is not a statement about
trained models in general, and it should not be read as one.

* **They are small.**  6 800, 9 872, 12 944, 18 608 and 6 520 parameters — small dense
  networks of two or three layers.  On disk they are **31, 44, 56, 78 and 31
  kilobytes** each.  They are one to two orders of magnitude away from anything that
  would be called a model in the wider sense.
* **Each one solves exactly one problem.**  `voice_enhance` produces a gain per
  frequency bin for a voice signal, `ambience_match` produces the ambience a signal
  should have, `voice_reverb_reduce` produces a gain per bin with two frames of
  history, `voice_sample_mask` produces the same kind of gain from a window of
  2.7 ms and decides every sample, `voice_sample_memory` produces the same kind of
  gain and reads the 11.4 ms before the sample as well.  None of them is a
  restoration engine; each is one narrow filter that happens to be learned.
* **They are the first ones.**  This is the first generation of a library, not a
  family: six models, one per problem, trained on the same day.  There is **no
  training pipeline behind them** that a reader could rerun to get the next variant,
  and there is no evaluation set that says they are better than the hand written
  modules — except for the one measurement in §4b, which is a measurement on one
  damaged memo, not an evaluation set.
* **They are not better than the hand written modules.**  Nothing in this document
  says they are.  The hand written modules are the ones the product ships as the
  default, and the models are what the offline path may offer where the extra quality
  is worth a wait.
* **The per sample model reads a short window.**  2.7 ms is short: its estimate of a
  band is noisier than the estimate of a 21 ms window, and the price of the sample
  rate is paid in the estimate, not only in the cost.

### What would have to be true for a bigger model to be offered

1. **The cost has to stay inside the budget, or the rule has to change.**  A model
   that does not fit into the realtime budget is not a model for the real time path
   at all, and offering it means saying so — in this document, not in a release note.
2. **The artefact has to stay shippable.**  Tens of kilobytes is a file in the tree;
   tens of megabytes is a download, and then it is a decision about what the product
   fetches and when.
3. **There has to be a training pipeline behind it.**  A model that cannot be retrained
   is an artefact nobody can maintain: it cannot be fixed, it cannot be extended to a
   new problem, and it cannot be reproduced.  A pipeline is the price of the second
   generation.
4. **There has to be a way to say it is better.**  A measurement against the hand
   written path on material the product actually serves, with the numbers written
   down — the same discipline as this document, applied to quality instead of cost.

Until those are true, the models stay what they are: a working seam, three small
artefacts, and a cost that has been measured and written down.

## 7. The files

| file | what it is |
|---|---|
| [`qa/performance/model_budgets.py`](../qa/performance/model_budgets.py) | the budget table: the recorded cost of every shipped model, the yardstick and the two rules |
| [`qa/performance/sweep_models.py`](../qa/performance/sweep_models.py) | the sweep: the measurement the table is written from |
| [`qa/performance/make_model_budgets.py`](../qa/performance/make_model_budgets.py) | the writer: turns the sweep into the table |
| [`tests/test_model_budget.py`](../tests/test_model_budget.py) | the gate: every model fits inside the budget it is held to, and the table follows the library |
| [`qa/make_demo_memo.py`](../qa/make_demo_memo.py) | the material of §4b: the damaged phone memo, built from our own voice |
| [`qa/measure_per_sample.py`](../qa/measure_per_sample.py) | the measurement of §4b: the cost and the table, on that memo |
| [`../aurcore/models/__init__.py`](../aurcore/models/__init__.py) | the format, the engine and the seam the models are reached through |
| [`MODEL_REFERENCE.md`](MODEL_REFERENCE.md) | the format and the anatomy of a model artefact, in more detail |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) §4, §7 | the realtime budget of the hand written modules, and the ML seam |

## 8. What could not be measured, and why

1. **The machine was busy.**  The sweep ran with other work on the machine: the load
   was at 100 % and the session yardstick moved between 11.1 µs and 29.5 µs per
   transform.  The recorded number is the least disturbed observation of eight rounds,
   and the spread of the rounds is in the note of every entry.  A quiet machine would
   have moved the wall-clock numbers, not the conclusion.
2. **The per-round calibration is noisy.**  The cost of a model is spread over a
   one-to-two-second run, so the batch of transforms taken beside it cannot be as
   tightly paired as the one beside a single block of a module.  Sampling the
   yardstick *during* the run from a second thread was tried and was worse: the
   thread competes with the run for the machine.  The paired batch on both sides is
   kept, and the margin of the live check of the gate accounts for the rest.
3. **The offline path is not the real time path.**  What is measured is the cost of
   running a model over a whole file, amortised over the blocks of that file.  A
   model that ran inside the audio callback would also carry the per-block overhead of
   the streaming engine, and it would carry the latency its context implies — two
   frames of history for `voice_reverb_reduce`, which is 512 samples at 48 kHz.  The
   measurement answers "is it affordable", not "could it be put in the callback".
4. **Nothing here says what a model sounds like.**  This document is about cost.  The
   quality of these models is not measured anywhere in the tree.
