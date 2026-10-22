# Auralis — the model reference

> **What this document is.**  What a **learned model** is in this product, what a
> model is made of, what it costs and — the part the tree had wrong — **at what
> rate** it costs it.  The anatomy was measured by reading a commercial model
> library from the outside; nothing in it was executed, so no number here is a
> claim about what a model sounds like.
>
> **The numbers are in**
> [`qa/benchmark/reference_model.py`](../qa/benchmark/reference_model.py).  The design
> note that reads them is this file.  That file holds two subjects: the numbers
> of this note, in the series `reference_model`, and the numbers of our own
> model path, in the series of the three configurations of ours.
>
> **No third party name appears in this document.**  The reference is described by
> its shape, never by its name, its file name, its parameter names or its
> framework.  The gate is [`../tools/hygiene_check.py`](../tools/hygiene_check.py).
>
> **What this document revises.**  The verdict in
> [`RESTORATION_REFERENCE.md`](RESTORATION_REFERENCE.md) §3.2 said that a learned
> model that ships as data is not affordable.  §3 below gives the arithmetic that
> shows **where** that verdict was true and **where** it was not.
>
> The binding rules live in [`../CONTRACT.md`](../CONTRACT.md).  Where this document and
> the contract disagree, the contract wins.

---

## 1. What a model is, in this product

A model is **one file with two parts**: the **weights** and the **manifest**.

* The **manifest** is the model's self description.  It says what the model expects
  to be fed, what it produces, how much history it reads, how long its state lives,
  which problem it solves, which version it is and what its own checksum is.
* The **weights** are arrays of `float32` in a file the project can read without a
  dependency the project does not have.

The library is a **directory**: one file per model, named after what the model does.
The loader lists the directory — the name, the version, the date, the problem —
**without reading a single weight**.

Where a model belongs is decided by the mode, not by the module.  The real time
modules are hand written: exact, cheap, deterministic, and they stay exactly as
they are.  A model belongs to the **offline** path today, and the seam hands a
model out in that mode only.

The **seam** in the module registry is the flag `ml` on a module class
(`aurcore/registry.py`).  It says *this module is model-backed*: the user
interface labels it, and the roadmap tracks it.  Today two modules carry it, and
both ship a hand written approximation behind the same contract.  The `ml` flag is
**metadata, not behaviour**, and it says nothing about *what kind* of model the
module wants — which is the subject of §4.

The format, the refusals and the determinism rules are written down in
[`../aurcore/models/__init__.py`](../aurcore/models/__init__.py); the seam itself is
described in [`ARCHITECTURE.md`](ARCHITECTURE.md) §7.

## 2. The anatomy, as it was measured

The reference was a **library of trained models**, shipped as data beside the
program that used it.  It was read from the outside: the files were parsed, the
strings were read, the code was disassembled, and **nothing was loaded and
nothing was executed**.

### 2.1 The weights are plain `float32`

This is the first finding and it is the one that changed the way the tree reads a
model artefact.

| | what was found |
|---|---|
| where the weights are | the **data section** of the library, not a container and not a section of their own; the section's entropy is **6.01 to 6.11**, which is the entropy of base64 text and not of ciphertext, which would sit near 8.0 |
| how they are written | **base64 text**, cut into fixed **500 character slots** at a **512 byte** stride, each slot padded with zeros to the slot boundary |
| what they decode to | **plain `float32`**: no encryption, no compression, no obfuscation |

The proof is **distributional, not entropic**.  The entropy of the decoded bytes
is near the random limit, so the payload *looks* encrypted — and that is exactly
why the distribution of the decoded **words** was measured instead.  Reading the
largest contiguous run (3,499 slots = 1,312,125 bytes) as `float32`:

```
n = 328,031 words,  99.67 % finite
|x| < 1e-6 :  33.6 %          |x| < 0.5 : 64.2 %
first words: -0.01268, 0.01168, -0.01155, 0.00307, 0.005579, -0.0196, -0.005008, -0.001122
```

A random byte string read as `float32` gives denormals (`3.8e-37, 1.0e-34, …`).
This gives small, roughly symmetric, decaying weights — the signature of a trained
network.  **So a model artefact is not a secret.**  It is a header and a blob, and
a reader who wants to know what it holds can read it without running anything.

### 2.2 The parameter counts

| class | parameters | the weights as they are |
|---|---|---|
| large | **7,530,937** | 28.73 MiB |
| medium | **4,205,625** | 16.04 MiB |
| small | **2,381,156** | 9.08 MiB |

These are lower bounds: the last word of each tensor is short.  The upper bound
is every character of the payload decoded, and the truth is inside a 0.13 % window.

### 2.3 The layer names are in the code, not in the data

The weight payload is a **flat `float32` stream with no shape table and no layer
names in it**.  The names live in the compiler's own run-time type information,
one distinct name per distinct (layer type, shape) pair — so the count of
distinct names is a direct measure of the **shape diversity** of the build.

### 2.4 The architecture

Read in our vocabulary, it is a **one dimensional convolutional stack**:

* **gated residual blocks** — a unit whose output is gated by a function of itself;
* a **down- and up-sampling bracket with skip connections** — the coarse scale is
  computed and then refined back up;
* **learned transposed convolutions** — an upsampler whose kernel was fitted, not
  designed;
* **batch normalisation** — two flavours, one of them over two axes;
* **four activation families**, one of them **periodic** (it has a trainable
  frequency, which is what makes a waveform generator work);
* a **recurrent gate** — a cell that carries a context;
* **13 to 18 delay lines** — fractional delays, i.e. the network is
  sample-synchronous and stateful.

The **depth** was counted from the code, not from the names: the number of
distinct layer objects one pass crosses is **12 / 23 / 47** for the three size
classes.

### 2.5 It is a hybrid, and the artefact says so itself

The library ships **one model** that does two kinds of job.  The model's own
descriptor is a small **XML block** that declares the **feature list**, a
**tooltip** for each feature, and — for each feature — **one flag** that says
whether the feature *filters* or *restores*.

* The **removal** features (noise, reverb, codec artefacts) carry the flag set:
  they are expressed as a **gain per time-frequency cell**, so the model
  **filters**.
* The **restoration** features (equalisation, low frequency restoration, high
  frequency restoration) carry the flag clear: they have to **add** spectrum that
  is not in the input, so the model **generates**.

And the code carries **both halves in the same binary**: the mask side is the
convolution stack with its gated residuals and delay lines; the generation side is
the learned upsampler, the up-sampling blocks and the periodic activation, and in
the newest generation the recurrent cell.  Blocks that are **conditioned** on the
requested feature are how one network serves several tasks.

### 2.6 The descriptor declares no rate, no channels and no transform size

The XML block is **397 to 708 bytes**.  It declares the feature list, a tooltip
each and the flag — and it declares **no sample rate, no channel count and no
transform size**.  Those live in the program that loads the model, not in the
model.

And it is **readable without loading anything**: the query that returns it is a
pure lookup of a string literal in a read-only section.  No weight is decoded, no
network is built, no thread is started.

⇒ **That is a design decision worth copying.**  A host can enumerate what a model
can do, and refuse to load it, **before paying for it**.

## 3. What it costs, and at what rate

### 3.1 One pass of the model

A convolution with `k` taps over `in` input channels and `out` output channels
holds `k × in × out` weights and needs `k × in × out` multiply-accumulates per
output element.  **So the number of multiply-accumulates per pass of the model is
the number of its weights**: for the largest model, **7,530,937 MAC per network
sample**.

### 3.2 The audio rate: it cannot run there

If the model runs **once per audio sample**, a block of 64 samples costs 64
passes:

```
64 × 7,530,937 = 481,979,968 MAC per 64-sample block
```

At an optimistic 8 G MAC/s (one core, a fused multiply-add) that is **60 ms per
64-sample block**.  The real time budget of this product is **1333 us**, so that
is **45 times the whole budget**.

⇒ **A model that runs per sample cannot run in a real time plug-in.**  This is
true of the reference, and it is true of every model of this size.

### 3.3 The frame rate: it fits

If the model runs **once per spectral frame**, the arithmetic changes completely.
With a hop of 1024 samples — one inference per 1024 samples, which is sixteen
blocks of 64 samples — a 64-sample block carries **1/16 of a pass**, i.e. 0.0625
network samples:

| class | MAC per 64-sample block | at 8 G MAC/s | share of the 1333 us budget |
|---|---|---|---|
| large | 470,684 | **59 us** | 4.4 % |
| medium | 262,852 | 33 us | 2.5 % |
| small | 148,822 | **19 us** | 1.4 % |

⇒ **A model that runs per frame fits inside the budget with room to spare**:
**19 to 59 us per 64-sample block**, **4 per cent** of the budget at the top of
the band.  That is the same order as the cheap end of our own hand written
modules — our per-block costs run from 0.1 us to 2197 us.

⇒ ⇒ **THE FINDING.**  **The intelligence is not in the per sample cost.  It is in
the granularity of the inference.**  A verdict that says "a learned model is too
expensive" is incomplete until it says **at what rate** the model runs.  The
verdict in [`RESTORATION_REFERENCE.md`](RESTORATION_REFERENCE.md) §3.2 was true of a
model that runs per sample, which is what the reference does.  **Our mechanism is
being built to run per frame**, and at that rate the cost is 4 per cent of the
budget.

### 3.4 The number the study measured, and the number this document measured

The study drove the **plug-in that loads this library** through its own host and
timed it: **5.5 to 23 ms per 512 frames**, recorded as *not real-time-safe* on that
machine.  That is the cost of the **whole plug-in** — the band split, the masking
passes and the model together — and it is the number an earlier verdict in this
project was built on.

The arithmetic above is what that verdict missed.  **It says nothing about the
granularity**, and the granularity is the whole of the difference: the same
weights, run once per frame instead of once per sample, cost **19 to 59 us per
64-sample block** instead of **60 ms**.

### 3.5 What is *not* measured, and what will settle it

**Honesty about our own model.**  Our own first model **exists** (§3.6), and
its cost is measured (§3.6.3).  It is **not in the constants file** yet:
nothing in §6 is a measurement of ours.

The number that will settle it is **the cost of one inference of our own model, in
microseconds per 64-sample block**, measured with the instrument of the tree
(`aurcore/models/bench.py`, which reports `per_block_us` and `budget_fraction`).
That number has to be recorded next to the constants of §6 as a measurement of
ours, in the series of our own path, so that it can never be mistaken for a
number of the reference.

Until it exists, the arithmetic of §3.3 is **arithmetic on the reference's own
measured counts**.  The test that holds it is
[`../tests/test_benchmark_model.py`](../tests/test_benchmark_model.py), and it says
so in its own docstring.

**It exists now.**  The model of §3.6 was trained, and the instrument of the
tree measures it: **90.9 to 108.9 µs per 64-sample block**, which is **6.8 %
to 8.2 %** of the budget of a block.  The numbers of §3.6 are the ones that
say what the model does, and what it does not do.

### 3.6 Our own model: what it is, and what it is not

**What it is.**  `models/voice_denoise_mask.aurm` is the first model of ours
that is **trained**: a frame based spectral mask predictor for the voice
de-noise.  It reads the magnitude spectrum of one frame of 1024 samples, a
hop of 256, and answers a gain per frequency band; the mask is applied to the
magnitude and the phase is kept.  18,608 parameters, 79,510 bytes on disk,
`epoch 3000`, checksum `sha256:ae265eae…c33c`.

**The layout it was trained on.**  The features are the levels of the 64 mel
bands of the frame, in **dBFS**: 0 dB is a full scale sinusoid in the
transform, the floor is −100 dBFS, the ceiling is +12 dBFS, and a band that
carries nothing at all reads 0.0 — the quietest value of the layout.  The
model of the 1.0.0 build was trained on a layout that **clipped the levels at
the top of their range**: it could read the shape of a spectrum but not its
height, and a band without a bin read as the loudest value of the layout.
The model of this build was trained again, on the layout the engine reads.

#### 3.6.1 What it buys

The quality instrument of the project (`qa/qualityvoice/harness.py`) measures
the model and the hand written Voice De-Noise on the same material.  The
columns are **before → after** the retraining on the corrected layout.

| case | the hiss removed | the wanted content | the chatter |
|---|---|---|---|
| `voice_hiss` | −27.47 → **−29.39 dB** | −0.01 → −0.02 dB | 0.94 → **0.86 dB** |
| `voice_pink_hiss` | −18.24 → **−19.90 dB** | −0.01 → −0.01 dB | 1.34 → 1.33 dB |
| `voice_hum` | −22.34 → **−28.86 dB** | 0.00 → −0.01 dB | 1.22 → **1.72 dB** |
| `voice_clean` | −0.00 → −0.00 dB | −0.02 → −0.00 dB | 0.00 → 0.00 dB |
| `voice_breath` | −0.93 → −2.68 dB | −0.30 → −0.33 dB | 0.84 → 0.69 dB |
| the hand written module, `voice_hiss` | −17.85 dB | −0.13 dB | **0.03 dB** |

On the 66 held out mixtures: the removal went from −7.60 to −5.71 dB on
average (−26.86 to −26.24 dB on the mixtures that carry something to
remove), the movement of the voice from −0.34 to −0.45 dB on average and
from −5.99 to −3.70 dB in the worst case, and the number of mixtures that
break the 1.5 dB budget of the voice went from **4 to 7**.

⇒ **The model removes more than the hand written module and it chatters more.**
The hand written module smooths its gain over 51 ms; the engine applies the
mask of every frame on its own, and the model can only answer from the frame
it is given.  The chatter of the model is 0.86 dB against 0.03 dB, and on two
of the cases it is over the 1.0 dB budget of the project (1.33 dB on the pink
hiss, 1.72 dB on the hum).

#### 3.6.2 What it is not

**It fails on material that is not a voice.**  This is the case the ground
truth instrument of the project measures (`qa/quality/measurement.py`): a
wanted partial that is not a voice, over a bed.

| case | the removal | the wanted partial |
|---|---|---|
| `tone_noise` (a lone tone over a bed) | +26.35 → +27.39 dB | −7.34 → **−32.50 dB** |
| `tone_hum` (a lone tone over a hum) | +26.11 → +32.07 dB | −21.39 → **−47.10 dB** |
| `voice_hiss` (a dense harmonic stack without pauses) | +0.63 → **+0.00 dB** | −0.01 → **−5.24 dB** |
| `bed_only` (a bed and nothing else) | +19.22 → +27.18 dB | 0.00 dB |

⇒ **A lone tone is taken with the bed** — 32.5 dB of it on `tone_noise` and
47.1 dB on `tone_hum`, against 7.3 dB and 21.4 dB for the 1.0.0 model, so
the retraining on the corrected layout did not repair this failure and made
it louder.  **A dense harmonic stack without pauses is not separated at
all**: nothing is removed, and the wanted partial moves by 5.2 dB.  The
hand written module keeps the partial (−0.34 dB) and removes 17.9 dB of the
hiss on the same case.  Both are the same failure: the model reads the shape
of a spectrum, and a spectrum that does not look like a voice is out of its
range.

**It is not a transient detector and it does not remove a breath.**  The
model separates the *voice* from the *hiss and the ambience*; it keeps the
breath (the `breath_control_breath` probe: −0.71 dB, where the rule of the
harness asks for 6 dB and the hand written module takes 11.52 dB away), and
it does not touch a mouth click (−0.72 dB, where the rule asks for 10 dB).  It
removes the sibilance (5.15 dB on the `de_ess_sibilance` probe, where the rule
asks for 6 dB).

**It is an offline artefact.**  The model renders a whole file in one pass.  A
host that chops the file into blocks restarts the framing in every block, and
the result moves by 36 dB.  The rule R5 (the output does not depend on the
block size) is a rule for the real time modules; the model does not live on
that path.

#### 3.6.3 What it costs

| | the number |
|---|---|
| the cost of one inference | 435.8 µs per frame (`aurcore.models.bench`, 200 frames, 5 rounds) |
| the cost of a 64-sample block | **108.9 µs**, 8.17 % of the budget of a block |
| the cost of a second of audio | 81.71 ms, the model alone |
| the cost of the reader | 15.1 to 22.4 µs per frame; the layout change added one division per bin, **+1.7 to +6.0 µs** per frame |
| the artefact | 79,510 bytes |

The wall clock numbers move with the load of the machine: the same code
measures 363 µs and 436 µs per frame on a machine that was busy with other
work, and the head to head runs an hour apart report 0.669 ms and 0.810 ms
per frame.  **The part that is a measurement of the change** is the reader:
the layout divides the magnitude by the reference of the full scale, and that
is one division per bin.

#### 3.6.4 How the numbers are produced

```
python var\model_training\train_voice_mask.py --epochs 3000 --delta-frames 2 --hidden 64 48 --consistency 1.0 --min-gain 0.03 --loss logit --per-voice 12 --frame-stride 2
python var\model_training\evaluate_head_to_head.py --json var\model_training\results.json
python -m aurcore.models.bench
python -m pytest tests\test_trained_model.py -q
```

The training is deterministic: a fixed seed, full batch epochs, no
shuffling, and nothing that reads the clock.  The gate
([`../tests/test_trained_model.py`](../tests/test_trained_model.py)) trains
twice and compares the two sets of weights and the two checksums.

### 3.7 The per sample model: what it is, and what it is not

**What it is.**  `models/voice_sample_mask.aurm` is the **per sample** model
of ours: a window of **128 samples** and a hop of **one sample**, so it is
asked 48,000 times a second.  6,520 parameters, 30,834 bytes on disk,
`epoch 2000`, checksum `sha256:7dac27de…a40`, and a mask that is decided
every sample.

**Why it exists.**  §3.2 measured that the model of the study cannot run at
the audio rate: 60 ms per 64-sample block, forty-five times the budget of
the block.  That verdict was about **their** network — 7.53 M parameters,
evaluated per call.  Ours is two orders of magnitude smaller, and the
difference between "too slow" and "affordable" turned out to be the **path**,
not the size:

| | the frame rate model | the per sample model |
|---|---|---|
| the questions a second | 187.5 | **48,000** |
| the cost of a render | 0.008 × the real time | **0.80 × the real time** (mono), 1.11 × (stereo, the mix path) |
| the share of the realtime budget of a block | 0.8 % | **72.5 %** |
| the budget it is held to | `block` | `speed` — 2.0 × the real time |

⇒ **The per sample model fits inside the realtime budget of a block** (72.5 %
of it), and it is held to the **offline budget** instead, because 72.5 % is
too close to the edge to be measured reliably on a shared machine whose load
moves by a factor of two (`docs/MODEL_COST.md` §1b).

**What makes it affordable is the batch.**  The engine evaluates the frames
of a file in **one pass** instead of one Python call per frame, and the
accumulation order of the batch is the one of the frame-by-frame path — so
the batch is a shortcut and not a different answer, and the output of every
model is **bit for bit** the output it produced before.  Before the batch,
the same per sample path took 1.50 × the real time; after it, 1.11 ×.

**What it buys.**  On a damaged phone memo of ours (a voice over a mains
hum, a rumble, a hiss and a DC offset), against the frame rate model:

| band | the memo | `voice_enhance` | `voice_sample_mask` |
|---|---|---|---|
| the hum 45–55 Hz | −37.05 dBFS | −11.13 dB | **−15.62 dB** |
| the hiss 5–20 kHz | −47.84 dBFS | −1.02 dB | **−6.77 dB** |
| the rumble 20–80 Hz | −32.25 dBFS | −11.59 dB | **−14.54 dB** |
| the voice 120–3000 Hz | −37.90 dBFS | +2.49 dB | **−0.81 dB** |

⇒ It takes **more of every artefact** — 4.5 dB more of the hum, 5.8 dB more of
the hiss, 3.0 dB more of the rumble — and it costs 0.81 dB of the wanted content
where the frame rate model *gains* 2.49 dB of it.

**What it is not.**  It is **not** a better model: it is the same kind of
model (a band curve, fitted on synthetic material) at a finer time
resolution, and the price of the finer resolution is a **noisier estimate** —
a band of a 2.7 ms window holds few bins.  It is **not** a separator that
knows about a voice (§3.6.2 holds): it is a per band curve, and a band that
does not look like a voice is out of its range in exactly the same way.  And
it is **not** a real time capability: at 48,000 questions a second it is
affordable only where there is no deadline.

**What it does not have: memory over the material.**  The model of the study
reads a *sequence* — a conv1d stack with gated residual blocks and a
recurrent gate (§2.4), so its decision for one frame is a function of the
frames before it.  The model of §3.7 is an MLP over one frame and one
slope: its history is **one frame**, which at a hop of one sample is **one
sample** (1.3 ms).

⇒ The second kind of network now exists: the manifest names a `sequence`
kind, a stack of **causal** convolutions over the time axis, and the engine
runs it frame by frame and in one pass, bit for bit the same answer
(§3.8).  A model with a memory is shipped with it, and §3.8 says what the
memory is worth on real material.

### 3.8 The model with a memory: what the memory is worth

**What it is.**  `models/voice_sample_memory.aurm` is the model of the
**second kind of network** the manifest knows: a stack of three **causal**
convolutions over the time axis, so the answer for a sample is a function of
the samples before it.  The window is 128 samples and the hop is **one
sample**, as in §3.7; the reach of the stack is **547 frames** — 11.4 ms at
48 kHz — and the context the manifest publishes is the 546 frames before a
frame.  7 008 parameters, 32 900 bytes on disk, `epoch 2000`, loss 34.21 →
1.688, checksum `sha256:cf0a56d5…072`.

The stack is 64 → 16 (kernel 3, dilation 1), 16 → 16 (kernel 3, dilation 16)
and 16 → 64 (kernel 3, dilation 256).  The **dilation** is what makes the
memory affordable: a kernel of three taps that reaches 256 frames back holds
as much of the past as a kernel of 256 taps would, at a fraction of the
parameters.

**What it costs.**  1.183 times the real time in mono (the sweep) and 1.57
times it in stereo (the mix path), against 0.795 and 1.11 for the per sample
model of §3.7 — all of them under the **offline budget** of twice the real
time (`docs/MODEL_COST.md` §1b).  The model with a memory does **not** fit
inside the realtime budget of a block (102.3 % of it), so it is held to the
offline budget, like the per sample model before it.  The memory itself is
cheap in **memory** too: the peak working set of the render is 1 840 MB
against 1 836 MB for the per sample model, because the peak is the frames of
the whole file, not the convolution.

**What it buys.**  On the same damaged phone memo (§3.7):

| band | the memo | `voice_enhance` | `voice_sample_mask` | `voice_sample_memory` |
|---|---|---|---|---|
| the hum 45–55 Hz | −37.05 dBFS | −11.13 dB | **−15.62 dB** | −14.11 dB |
| the hiss 5–20 kHz | −47.84 dBFS | −1.02 dB | **−6.77 dB** | −5.78 dB |
| the rumble 20–80 Hz | −32.25 dBFS | −11.59 dB | **−14.54 dB** | −13.12 dB |
| the voice 120–3000 Hz | −37.90 dBFS | **+2.49 dB** | −0.81 dB | +0.41 dB |

⇒ It takes **more of every artefact than the frame rate model** and **less
than the per sample model**, and it keeps **1.22 dB more of the voice** than
the per sample model does.  It is a third point on the same trade-off, not a
better point.

**The two changes have to be told apart.**  The model with a memory differs
from the per sample model in two things at once: the network (a causal
convolution stack against a fully connected stack over one frame) and the
**time scale of the material it was taught**.  The per sample model was
taught a voice whose syllables last 40 to 160 **samples**; the model with a
memory was taught one whose syllables last 60 to 250 **milliseconds**
(`aurcore/models/train.py`, `_memory_training_set`).  A memory of eleven
milliseconds cannot learn anything from syllables that last one, so the
material had to change with it — and the table above therefore does not, on
its own, say what the memory is worth.

`qa/compare_memory.py` takes the two apart: it trains the **per sample
architecture on the material of the model with a memory** and renders the
memo with all three.  The difference between the two per sample models is
what the *material* is worth; the difference between the second and the
third is what the *memory* is worth.  The verdict, measured: the material
is worth 0.45 dB more of the hum, and the **memory is worth steadiness
rather than more removal** — with the same material and the same number of
parameters, the convolution stack takes 1.96 dB *less* of the hum and keeps
1.46 dB *more* of the voice.  A model that looks at the material before it
does not over-react to a transient.  The numbers are in
`docs/MODEL_COST.md` §4c.

**The head of the file is decided on an empty memory.**  The frames before
the file are not there, and a convolution reads them as zero: the first
546 frames (11.4 ms) of the render are decided without any memory.  Measured
on the memo, the head comes out **20.4 dB below** the rest of the file
against **52.2 dB below** for the per sample model — the model with a
memory lets more of the head through, because it has no past to lean on.
The artefact is confined to the first 11 ms of a file.

## 4. Filter or generate: the distinction the artefact has to carry

**A model either filters or it generates.**  There is no third thing.

| | a model that **filters** | a model that **generates** |
|---|---|---|
| what it produces | a **mask**: a gain per frequency bin, between 0 and 1 | a **spectrum**: the magnitudes the signal should have |
| what it can do | take something **away** | put something **back** that is not in the input |
| a mask can do it? | yes | **no** — a mask multiplies, and a multiplier cannot invent energy |

Getting this wrong is how a mask-shaped model ends up asked to restore a low end
it cannot invent.  The reference declares the distinction **per feature, in the
model's own descriptor**, and the code carries both halves in the same binary.

### 4.1 We already have the flag

Our registry carries `ml` on every module: *this module is model-backed*.  It is
the right place to say **that** a module wants a model.  It is **not** the place
to say **which kind** of model the module wants, because a module may want both
and because the kind is a property of the model, not of the module.

So the `ml` flag is the flag that says *a model belongs here*; the kind of model
belongs to the model itself.

### 4.2 And our own manifest already carries the kind

The manifest of our own format declares it, and the engine branches on it:

| where | what it is |
|---|---|
| `aurcore/models/manifest.py` | `OUTPUT_KINDS = ("spectral_mask", "spectrum")`, and the `output.kind` field of every manifest, which is refused if it is not one of the two |
| `aurcore/models/manifest.py` | a mask is checked against its transfer and its range: a mask between 0 and 1 needs the bounded transfer, and a mask whose range leaves `[0, 1]` is a refusal |
| `aurcore/models/engine.py` | the runner applies a mask as a gain and a spectrum as a replacement, and the output carries the kind it was produced with |
| `aurcore/models/__init__.py` | the format is documented: *a gain per frequency bin that is applied to the magnitude*, against *a spectrum the model produces* |

⇒ **The format requirement is met.**  A model in this product can say whether it
**filters** or **generates**, in the artefact, before it is loaded, and the
engine applies the two differently.  The one thing the library listing does not
yet surface is the kind: `aurcore/models/library.py` lists the name, the version,
the date and the problem, and it does not list the kind.  That is a small
addition to a file owned by the model mechanism, and it is **not** needed for the
distinction to exist.

## 5. Seven models in three generations

The reference shipped **seven** copies of its model library.  They are **seven
separately trained models**, in **three dated architecture generations**, and the
evidence is independent on several fronts:

| | what was found |
|---|---|
| the sizes | three distinct sizes, from 16.2 MiB to 45.4 MiB on disk |
| the digests | **seven distinct checksums** — no two are the same file |
| the generations | **three dated generations** — September 2023, February 2024, June 2024 — each with its own depth, its own frame constant and its own code size |
| the weights | **at most 1,233 shared float words between any two** models, out of millions: the weights are independently trained per variant |
| the feature sets | **four different capability sets**: one model offers three features, one four, one five, and three offer all six |

⇒ **They are not formats and they are not channel configurations.**  A channel
configuration would not change the feature list, and it would not change the
weight count by a factor of three.

### 5.1 What that says about versioning a model library

1. **A model library is a family, not a file.**  Seven entries, seven versions,
   seven capabilities.  A host that offers a *choice of algorithms* is offering a
   choice of **models**, and the choice does not need a host change: the model
   carries its own capability list.
2. **A generation is a format for the whole family.**  When the architecture
   changes, the depth changes, the frame constant changes and the code size
   changes — together.  The library is versioned as a family, and a model
   declares the generation it belongs to.
3. **The weights are not shared between models.**  1,233 words out of millions is
   noise, not a shared trunk.  Each model is trained on its own, so **each model
   is a separate artefact with its own checksum, its own version and its own
   lifecycle.**  A model is not a set of weights with a mode switched on.
4. **A library is a pipeline, not a delivery.**  Three generations in ten months
   is a **process**: models are re-trained as the corpus and the rules change,
   and the old ones stay on disk because the users still load them.
5. **The artefact has to be readable before it is loaded.**  The descriptor is a
   few hundred bytes and it is read without touching a weight.

### 5.2 What we take from it

* **Declare the capability set in the artefact**, not in the host: the features it
  implements, a version, and the flag that separates the removal features from
  the restoration ones.  Our manifest already declares the kind, per model.
* **Version the family and the model separately**: the format has a version, and
  the model has a version of its own.  Both are already in our manifest.
* **Budget the learned part like any other module.**  The cost of learning is in
  the **training** and in the **size of the artefact**, not in the audio thread —
  *provided the model runs per frame*.

## 6. The numbers, as constants

The machine readable form is
[`qa/benchmark/reference_model.py`](../qa/benchmark/reference_model.py).  Every
constant there carries the case it belongs to, the unit it is in and the way it
was measured, in the shape the benchmark already uses.  Every one of them is in
the series `reference_model`: the numbers of our own model path are in the
series of the three configurations of ours, so the two cannot be mixed.

| constant | case | unit | what it is |
|---|---|---|---|
| `REFERENCE_MODEL_PARAMETER_COUNT_LARGE` | `*` | parameters | the weights of the largest model of the reference |
| `REFERENCE_MODEL_PARAMETER_COUNT_MEDIUM` | `*` | parameters | the weights of the middle one |
| `REFERENCE_MODEL_PARAMETER_COUNT_SMALL` | `*` | parameters | the weights of the smallest one |
| `REFERENCE_MODEL_WEIGHT_BYTES_LARGE` | `*` | bytes | the decoded payload of the largest one |
| `REFERENCE_MODEL_WEIGHT_BYTES_MEDIUM` | `*` | bytes | the decoded payload of the middle one |
| `REFERENCE_MODEL_WEIGHT_BYTES_SMALL` | `*` | bytes | the decoded payload of the smallest one |
| `REFERENCE_MODEL_MAC_PER_NETWORK_SAMPLE` | `*` | MAC | what one pass of the largest model costs |
| `REFERENCE_MODEL_AUDIO_RATE_US_PER_BLOCK` | `audio_rate` | us | what the largest model costs per 64-sample block, running per sample |
| `REFERENCE_MODEL_FRAME_RATE_US_PER_BLOCK_MAX` | `frame_rate` | us | the same, running per frame, at the top of the band |
| `REFERENCE_MODEL_FRAME_RATE_US_PER_BLOCK_MIN` | `frame_rate` | us | the same, running per frame, at the bottom of the band |
| `REFERENCE_MODEL_BLOCK_BUDGET_US` | `*` | us | the per-block budget of the real time path: 64 samples at 48 kHz |
| `REFERENCE_MODEL_BUDGET_SHARE_PERCENT` | `frame_rate` | per cent | the share of the budget the model takes at the frame rate |
| `REFERENCE_MODEL_COUNT` | `*` | models | how many trained models the reference ships |
| `REFERENCE_MODEL_GENERATION_COUNT` | `*` | generations | how many dated architecture generations they come in |
| `REFERENCE_MODEL_MAX_SHARED_FLOAT_WORDS` | `*` | words | how many float words the two heaviest models have in common |
| `REFERENCE_MODEL_DEPTH_SMALL` | `*` | layers | how many layer objects one pass of the smallest model crosses |
| `REFERENCE_MODEL_DEPTH_MEDIUM` | `*` | layers | the same for the middle one |
| `REFERENCE_MODEL_DEPTH_LARGE` | `*` | layers | the same for the largest one |

The terms the arithmetic is built from:

| constant | unit | what it is |
|---|---|---|
| `REFERENCE_MODEL_MAC_PER_SECOND` | MAC/s | the rate the cost was computed at: one core with a fused multiply-add |
| `REFERENCE_MODEL_AUDIO_RATE_STEPS_PER_BLOCK` | steps | how many passes a 64-sample block costs at the audio rate: one per sample |
| `REFERENCE_MODEL_FRAME_RATE_STEPS_PER_BLOCK` | steps | how many passes a 64-sample block costs at the frame rate: one per 1024 samples |

The case `*` means "every case": the size of a model and the size of a library
are properties of the artefact, not of the material it runs on.  The case
`audio_rate` and the case `frame_rate` are the two **granularities**, and they
are the two ends of the revision.

## 7. The files

| file | what it is |
|---|---|
| [`qa/benchmark/reference_model.py`](../qa/benchmark/reference_model.py) | the numbers of this document, as constants, with their case, their unit and the way they were measured; and, in the series of our own path, the numbers of our model path |
| [`tests/test_benchmark_model.py`](../tests/test_benchmark_model.py) | the gate of those numbers, and the arithmetic the revision rests on |
| [`RESTORATION_REFERENCE.md`](RESTORATION_REFERENCE.md) | the restoration reference, whose §3.2 verdict this document revises |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) §7 | the ML seam: the `ml` flag and the rule that no parameter is introduced for the sake of a future model |
| [`../aurcore/models/__init__.py`](../aurcore/models/__init__.py) | our own model format, including the flag that says filter or generate |
| [`../aurcore/models/bench.py`](../aurcore/models/bench.py) | the instrument that will measure the cost of our own model |

## 8. What could not be measured, and why

1. **Nothing was executed.**  Every number in this document comes from reading
   bytes.  The library was never loaded, so **nothing here is a claim about what
   a model sounds like**.
2. **The per layer numeric shapes** — the channel widths, the kernel sizes, the
   dilations — are encoded in the compiler's own type descriptors and could not be
   decoded.  The **depth** could, and was counted from the code.
3. **The exact serialisation** of the weight file was not reconstructed: the
   tensor boundaries and the meaning of the pointer table are inferred.
4. **The spectral front end** is not in the artefact: the transform size, the hop
   and the width of the feature vector live in the program that loads the model.
   The frame rate of §3.3 therefore rests on the measured frame constant and an
   assumed overlap; the conclusion is not sensitive to it — anything from 50 % to
   90 % overlap moves the figure by a factor of two and keeps it under 10 % of
   the budget.
5. **Our own model is not measured.**  See §3.5: the number that will settle it
   is the cost of one inference of our model in microseconds per 64-sample block.
