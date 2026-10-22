# Auralis — Models

> Status: **alpha (`0.1.0a0`)**. This document describes what is in the tree
> today: the model format, the library, the seam a module asks a model
> through, and the one module of ours that uses one.
>
> Every count in this document is checkable. The command that reproduces it is
> in the same paragraph.
>
> The binding rules live in [`../CONTRACT.md`](../CONTRACT.md). Where this
> document and the contract disagree, the contract wins.

---

## 1. What a model is

A model is **one file** with two parts: the **weights** and the **manifest**.
The file is a NumPy archive (`.npz`), read with `numpy.load` and
`allow_pickle=False` — the project gains no dependency to read a model.

| Part | What it is |
|---|---|
| `__manifest__` | the manifest: the UTF-8 bytes of a JSON document |
| `w0`, `b0`, `w1`, `b1`, … | the weights: one matrix and one bias per layer of the network |

Every weight array is `float32`; the arithmetic of the inference is `float64`.
The file name of a model is its name, and the file name **is** what it does:

| File | What it does |
|---|---|
| `models/voice_denoise_mask.aurm` | splits a frame of a voice into the wanted voice and the unwanted hiss and the ambience, and answers a gain per band |
| `models/voice_enhance.aurm` | keeps the band of the voice and takes the rumble and the hiss away |
| `models/voice_reverb_reduce.aurm` | takes the reverberant tail of a voice away |
| `models/ambience_match.aurm` | describes the ambience of a frame as a spectrum |
| `models/voice_sample_mask.aurm` | the **per sample** model: a window of 128 samples and a hop of one sample, so the gain is decided 48 000 times a second and a transient is not smeared over a frame |
| `models/voice_sample_memory.aurm` | the **per sample** model with a memory: the same window and hop, and a stack of causal convolutions over the time axis, so the decision is a function of the samples before it |

The library is a **directory**: one file per model, named after what the
model does. The directory is the shipping unit — it is copied, it is
versioned, it is listed.

```bash
python -c "import sys;sys.path.insert(0,'.');from aurcore import models;\
[print(i.name, i.version, i.trained_on, i.problem, i.path) for i in models.open_library().list()]"
```

### The manifest

The manifest is the model's self description. It is a JSON object, and the
parser is **strict on purpose**: a missing field is a refusal, an unknown
field is a refusal, a value outside its enumeration is a refusal, and a
number that does not add up is a refusal. Every refusal names the field and
says what was expected.

| Field | What it says |
|---|---|
| `name` | the name the model publishes — also the name of the file |
| `version` | the version of the model, `major.minor.patch` |
| `feature_layout_version` | **the version of the feature layout the weights were trained on** — the scale the features are read in; a model of another layout is refused |
| `epoch` | the number of passes over the training data the training ended at |
| `trained_on` | the date the model was trained, `YYYY-MM-DD` |
| `variant` | the variant inside the family (`base`, `small`, `large`, …) |
| `problem` | **the problem the model solves**: `voice`, `voice_reverb` or `ambience` |
| `task` | what the model does, in one sentence |
| `input` | what the model expects: the sample rate, the channel count, the frame size, the hop and the features |
| `output` | what the model produces: a spectral mask or a spectrum |
| `context` | **how much history the model reads** before it can answer |
| `state` | the scope of the state the model carries and when it is dropped |
| `network` | the shape of the network the weights belong to |
| `weights` | one entry per array of the weights |
| `checksum` | the checksum of the weights, `sha256:` and 64 hex digits |
| `deterministic` | whether the model promises the same output for the same input |
| `notes` | free text: what it is good for and what it is not |

The **provenance** of a model is therefore part of the model: the name, the
version, the epoch, the date it was trained and the checksum of its weights
travel inside the file. A model that was copied somewhere still describes
itself, and a reader can tell whether the weights are the ones the manifest
describes.

The checksum is taken over the **content** of the weights and not over the
bytes of the file: for every array, in the order of the names, the name, the
data type, the shape and the raw values are hashed. The checksum therefore
does not depend on the order the arrays were written in, and it does not
change when the archive is rewritten.

### The refusals

| Refusal | What it means |
|---|---|
| `ManifestError` | the manifest is malformed, incomplete, carries an unknown field, names a feature layout this build does not read, or describes something the engine cannot run |
| `WeightsMismatchError` | the manifest does not match the weights: an array is missing, or it has another shape or type |
| `ChecksumError` | the weights do not carry the checksum the manifest publishes |
| `ConfigMismatchError` | the model does not run at the configuration the engine asked for: the sample rate, or the number of channels |
| `ModelNotFoundError` | no file of the library carries that name |

**A missing model is not an error.** The product works as if no model
existed, and it says why in a message. The same holds for a library that is
empty, for a model whose manifest does not match the engine, and for a
model whose file is corrupt.

---

## 2. Where a model belongs: the offline path

The product has **two processing modes**:

| Mode | What it is | Who sets it |
|---|---|---|
| `realtime` | the path that has to keep inside the budget of a real time block: the live preview | the server, from the path of the render |
| `offline` | the path with no deadline: the file render | the server, from the path of the render |

A model belongs to the **offline** path. The modules of the real time path
are hand written: they are exact, they are cheap and they are deterministic,
so they stay exactly as they are.

A model that does not fit the realtime budget of a block is not refused: it
is held to the **offline budget** instead — a render may take at most twice
the length of the audio. That budget is a number in the table of
[`MODEL_COST.md`](MODEL_COST.md) §1b, and the gate
([`../tests/test_model_budget.py`](../tests/test_model_budget.py)) holds every
model to the one that fits it.

**The real time path does not load a model at all.** The load costs time and
memory, and the path that has to keep inside the budget of a block may not
pay for it. The seam reads the mode of the module it is asked about and
hands a model out in the offline mode only, and the module does not even
ask in the other mode.

Measured, with the loader of the library counted while the module runs in
the real time mode with a model asked for by name: **0 loads**, and the
output is the output of the module without the model seam, sample for sample.

```bash
python var\w35\measure.py      # the bit identity of the real time path
```

What the real time path does instead: the **hand written decision** of the
module. For the Voice De-Noise that is a bank of 64 psychoacoustically
spaced bands, a Wiener term per band, per band group thresholds and a
smoothed gain curve. It is exact, it is cheap and it is deterministic, and
it is the path the product has always taken.

---

## 3. The seam a module asks a model through

```python
from aurcore import models

binding = models.request(module, "voice")   # a model, or nothing
if binding is None:
    ...  # the module does what it does today
```

The answer is **either a model or nothing**, and nothing is a normal answer:
the module then does exactly what it does today, sample for sample.

Two rules the seam enforces:

* **A model belongs to the offline path.** The seam reads the
  `processing_mode` of the module it is asked about. A module that does not
  carry the attribute is treated as a real time module, which is the safe
  reading: the hand written path is the one that always works.
* **A control reads the model that is actually running.** The resolver
  records the model it bound to every module instance, and the control reads
  that record. It never reads "the first model of the library": a library may
  hold several models, and the one that is bound is the one that runs.

The module of ours that uses the seam is the **Voice De-Noise**
(`aurcore/modules/voice_de_noise.py`). It asks for the model that solves the
**voice** problem, and it uses the mask the model produces on top of the gain
its own decision makes. The name of the model travels with the state of the
module, so a session that names a model reopens with that model named — and
a session whose model file is gone says so instead of rendering something
else.

---

## 4. What a model costs

| What | How much | Unit |
|---|---|---|
| the load of the model file | 12.57 | ms |
| one frame of inference (64 frames at a time) | 18.17 | ms (284 µs a frame) |
| the share of the wall clock time of a file render | 39.4 | % |

The numbers are **timed**, not counted: a model cannot be counted the way a
transform can, so they move with the load of the machine. They are the
numbers of the measurement of wave 35 and they live in
[`../qa/benchmark/reference_model.py`](../qa/benchmark/reference_model.py),
where the case, the unit and the way they were measured travel with them.

```bash
python var\w35\measure.py      # the cost, the latency and the quality
```

The **latency** a model costs is its **context**: the history it reads
before it can answer. The context is latency, and the module publishes it —
the look ahead of the module plus the context of the model that runs.

| Configuration | Latency |
|---|---|
| real time, without a model | 2047 samples |
| offline, without a model | 4095 samples |
| offline, with the voice model of the library (it reads no history) | 4095 samples |
| offline, with a model that reads two frames of history | 4607 samples |

A host that asks the plug-in for the latency on every change therefore gets a
figure that moves when the mode moves, and that is legal: the value of a
parameter may change.

---

## 5. What a model buys

The quality of the offline path with a model, against the offline path
without one, measured with the voice instrument of the project on the three
voice cases of the benchmark:

| Case | Configuration | Removed (dB) | Kept (dB) | Gain variance (dB²) | Chatter (dB) |
|---|---|---|---|---|---|
| corpus voice | offline, no model | 17.99 | 0.066 | 0.001 | 0.001 |
| corpus voice | offline, with the model | 21.76 | 0.066 | 0.051 | 0.162 |
| synthetic voice | offline, no model | 17.92 | 0.055 | 0.021 | 0.005 |
| synthetic voice | offline, with the model | 21.08 | 0.055 | 0.172 | 0.153 |
| speech shaped | offline, no model | 17.98 | 0.225 | 0.004 | 0.001 |
| speech shaped | offline, with the model | 21.26 | 0.225 | 0.138 | 0.267 |

The model takes **3.2 dB to 3.8 dB more noise away** and the wanted content
does not move at all. The gain curve breathes more: the model is a second
opinion about the same bins, and the two together move the gain. The chatter
of the gain stays under 1 dB, which is the budget of the voice rules.

How the two opinions are combined: the model may only **deepen** a removal
the module already makes, and it may never touch a bin the module leaves
alone. A bin the module keeps keeps its level, however sure the model is.

The real time path is not in the table on purpose: it does not change.

---

## 5b. The learned stage of a render

The seam above is a module asking for a model **inside** the chain. There is a second
way a model runs, and it is the one that the per-sample model uses: the **learned
stage** of a file render.

```python
from aurcore import models

rendered, model = models.run_named("voice_sample_mask", rendered)
```

| What | How it runs |
|---|---|
| when | after the chain has rendered the file, over the **whole file** in one pass |
| what it decides on | the **mix** of the channels, and the gain is applied to every channel |
| what it costs | the cost of one channel, not of as many channels as the file has |
| how it is reached | `POST /api/render` with `"learned": "<name>"`, and the `render` tool of the MCP server |

Three things are deliberate here:

* **The whole file, in one pass.** A model that is asked once per sample has no
  business being asked at the edge of a block: the decision would be made on a
  window that crosses the edge.
* **The mix decides.** The same gain on every channel means the image does not move:
  what is taken away is taken away from the whole image by the same amount.
* **A name the library does not hold is a refusal.** A render that was asked for a
  model and did not get one is not the render that was asked for, so the server answers
  HTTP 400 and says which name it could not run.

The stage is measured on a damaged phone memo (a voice over a mains hum, a rumble, a
hiss and a DC offset — all of them ours, built for the measurement):

| band | the memo | the chain alone | the chain and the stage |
|---|---|---|---|
| the hum 45–55 Hz | −37.05 dBFS | −48.52 | **−48.92** |
| the rumble 20–80 Hz | −32.25 dBFS | −37.43 | **−38.90** |
| the hiss 5–20 kHz | −47.84 dBFS | −75.06 | **−77.36** |
| the voice 120–3000 Hz | −37.90 dBFS | −40.32 | **−39.05** |
| the DC offset | 13.24 mV | −1.26 | **−0.09** |

The stage gives back the 1.27 dB of voice the chain's own model took away, and takes
1.46 dB more of the rumble and 2.30 dB more of the hiss.

```bash
python qa/make_demo_memo.py      # the material
python qa/measure_per_sample.py   # the cost and the table
```

---

## 5c. The network that remembers

The models above are **per frame**: the answer for a frame is a function of that
frame and of nothing else.  The manifest knows a **second kind** of network,
`sequence`, which is a stack of **causal** one-dimensional convolutions over
the time axis — the answer for a frame is a function of the frames before it.

| | the `mlp` kind | the `sequence` kind |
|---|---|---|
| a layer | `(in, out, activation)` | `(in, out, kernel, dilation, activation)` |
| the weight of a layer | a matrix of `(in, out)` | a kernel of `(in, out, kernel)` |
| what it reads | the frame itself | the frame and the frames before it |
| the context it needs | the frames its features read | the frames its stack reaches |

The **dilation** is what makes the memory affordable.  A kernel of three taps
that reaches 256 frames back holds as much of the past as a kernel of 256 taps
would, and it costs a fraction of the parameters:

```
the reach of a stack = 1 + Σ (kernel − 1) × dilation
```

⇒ The manifest refuses a sequence network whose `context.frames` is not the
reach minus one, whose features read a history of their own (`delta_frames`
has to be 0 — the memory of the model is the memory of its stack), and whose
weights do not have the shape the stack needs.

The engine runs a sequence two ways, and they are the **same answer**:

* the **frame by frame path** keeps, for every layer, a ring of the inputs it
  reads: the frame being read and the ones it reaches back to;
* the **batched path** evaluates the frames of a block in one pass.

Both accumulate in the same order — the input channel first and then the tap —
so the two agree **bit for bit** (`tests/test_sequence_model.py`).  A block
that follows another one reads the history of the first, because the memory of
a sequence lives across the blocks.

The model that ships with it is `models/voice_sample_memory.aurm`: a window of
128 samples, a hop of one sample, and a stack that reaches 546 frames — 11.4 ms
at 48 kHz.  Like the per sample model it is chosen by name.

It costs **1.183 times the real time** in mono (the sweep) and **1.57 times**
it in stereo (the mix path), and it takes **4.4 MB** more memory than the
per sample model — the peak of a render is the frames of the whole file, not
the convolution.  On the damaged phone memo it takes more of every artefact
than the frame-rate model and less than the per sample model, and it keeps
1.22 dB more of the voice than the per sample model does.  The numbers and
the two changes that have to be told apart are in `docs/MODEL_COST.md` §4c.

---

## 6. What the format does not do

* A model is **not** a parameter. The choice of a model travels with the
  **state** of the module, not with its parameter list: the canonical
  parameter table stays honest about what the shipped code does.
* A model is **not** required. Every module of ours works without one, and
  the product ships a model library that may be empty.
* A model does not change the **meaning** of the audio of the real time
  path. It is an addition to the offline path, never a change.

---

## 7. Where the parts are

| File | What it is |
|---|---|
| [`../aurcore/models/format.py`](../aurcore/models/format.py) | the container: the file a model lives in |
| [`../aurcore/models/manifest.py`](../aurcore/models/manifest.py) | the manifest: the self description of a model |
| [`../aurcore/models/library.py`](../aurcore/models/library.py) | the library: the directory of the models, the listing and the load |
| [`../aurcore/models/engine.py`](../aurcore/models/engine.py) | the inference: the features, the network and the runner |
| [`../aurcore/models/resolve.py`](../aurcore/models/resolve.py) | the seam: the ask a module makes and the binding it gets |
| [`../aurcore/models/errors.py`](../aurcore/models/errors.py) | the refusals of the loader |
| [`../models/`](../models/) | the shipped library: one file per model |
| [`../tests/test_model_integration.py`](../tests/test_model_integration.py) | the gate of the seam, with the numbers |
| [`../tests/test_sequence_model.py`](../tests/test_sequence_model.py) | the gate of the network that remembers: the format, the reach, the batch and the gradient |
| [`../qa/benchmark/reference_model.py`](../qa/benchmark/reference_model.py) | the numbers of the model path, with the case, the unit and the way they were measured |
