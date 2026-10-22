# Auralis — the voice head to head

> **What this document is.**  A head to head between our voice de-noise and a
> commercial voice cleaner, written down so that the tree can be held to it:
> what was measured, what the reference does, where we are ahead, where we are
> behind, and what we do not know.
>
> **The numbers** are in
> [`qa/benchmark/reference_voice.py`](../qa/benchmark/reference_voice.py).  The
> gate that holds **our** side to them is
> [`../tests/test_benchmark_voice.py`](../tests/test_benchmark_voice.py).
>
> **Every number in the *ours* column is checkable**, and the command that
> reproduces it is in §2.  The numbers of the reference were measured by the
> study that wrote them down; they do not depend on the tree.
>
> **No third party name appears in this document.**  The reference is described
> by its shape, never by its name.  The gate is
> [`../tools/hygiene_check.py`](../tools/hygiene_check.py).
>
> The binding rules live in [`../CONTRACT.md`](../CONTRACT.md).  Where this
> document and the contract disagree, the contract wins.

---

## 1. What was measured

Our voice module against the reference, on the three voice cases of the
project, with **one instrument on both sides**: the voice instrument of the
project (`qa/qualityvoice/harness.py`) measures the level of the hiss band
before and after, the level of the voice band before and after, the roughness
of the applied gain and the delay of the output.  The reference was rendered
through the study's own host; the render was then measured with the same
code, so the two numbers in every row were produced by the same instrument on
the same machine.

| case | the signal | the voice band | the hiss band |
|---|---|---|---|
| the corpus voice clip | `corpus/audio/test/voice_hiss.wav`, read in the mid channel | 100 Hz - 4 kHz | 5 kHz - 20 kHz |
| the synthetic voice | the synthetic voice of the voice instrument under a white bed | 120 Hz - 3 kHz | 5 kHz - 14 kHz |
| the speech shaped signal | the harmonic voice like stack of the quality report under hiss | 120 Hz - 3 kHz | 5 kHz - 14 kHz |

The clip is read in the **mid** channel because that is where the manifest
puts the voice (the hiss of the clip is in the side channel, which is what the
corpus was built to measure).

**The rules that decide the winner are our own.**  The wanted content may move
by at most **1.5 dB** (rule R1), the gain curve may be rougher than **1.0 dB**
(rule R3), the output may not depend on the block size of the host by more
than **-60 dB** (rule R5) and the applied gain may not follow the level of the
input by more than **0.5 dB** over the 18 dB the level of the input moves by
(rule R9).  The "best" setting of each side is the setting
that removes the most hiss among those that hold R1 and R3.  The reference was
swept over 23 settings; ours over 10.  The settings are part of the number and
are written down with it.

## 2. The tree the numbers of ours describe

The tree was being worked on while the study ran, so the numbers of ours are
tied to the tree they were measured on.  The module that carries them was
rewritten **five times** after the study measured it, so **the numbers were
taken again** on each of those six trees, with the instrument of the project,
and they came out the same every time.

Then the module was rewritten again.  **Its decision and its noise reference
moved into the ratio domain**: it no longer compares the level of a band with
a learned floor, it compares the *balance* inside the band (the noise of the
band against the band itself), and a balance does not carry the level of the
input.  The numbers moved with it, and they were taken again on the tree the
constants file names:

| | the tree of the study | five revisions later | the rewrite |
|---|---|---|---|
| sha256 of `aurcore/modules/voice_de_noise.py` | `d0bfa214…` | `f2ca3103…`, `b2346e8c…`, `2d48dbee…`, `aca38b17…`, `4b188c46…` | `42bae3b7…`, `9d1e841a…` |
| sha256 of `aurcore/params.py` | `ad13235b…` | `ad13235b…` (unchanged) | `c411dc2b…` |
| measured at | 2026-10-21 20:06 | 20:25, 20:43, 20:55, 20:56, 21:00 | 2026-10-21 23:08, 23:22, 23:34 |
| the hiss removed from the corpus voice clip | 20.72 dB | 20.72 dB, every time | **29.65 dB** |
| the movement of the wanted voice | 0.06 dB | 0.06 dB, every time | 0.06 dB |
| the chatter of the gain | 0.85 dB | 0.85 dB, every time | **0.03 dB** |

**What the rewrite is worth, measured with the instrument of the project:**

| the number | before the rewrite | after the rewrite |
|---|---|---|
| the hiss removed from the corpus voice clip | 20.72 dB | **29.65 dB** (+8.93 dB) |
| the hiss removed from the synthetic voice | 20.36 dB | **29.69 dB** (+9.33 dB) |
| the hiss removed from the speech shaped signal | 17.13 dB | **29.73 dB** (+12.60 dB) |
| the chatter of the gain, the corpus clip | 0.85 dB | **0.03 dB** |
| the applied gain over 18 dB of level | 24.16 dB | **0.01 dB** |

The last row is the rule the rewrite delivered: **the applied gain does not
follow the level of the input any more.**  Before the rewrite the decision
and the noise reference of the module were a *level*, and a level carries the
level of the input, so the gain followed the material by 24.16 dB over the
18 dB the level of the input moves by.  After the rewrite both are a *ratio*,
so the gain moves by 0.01 dB.  (The number is read with the voice instrument
of the project, on the ground truth voice and hiss case of the quality
report, in the band that case names.)

The tree is still moving, so the hash in the constants file is the tree of the
**last** measurement.  The command below is what says whether the numbers still
hold, because it measures the module live:

```powershell
python -m pytest tests\test_benchmark_voice.py -q
```

The gate measures the module with the voice instrument of the project and
compares it with the numbers of the constants file.  A module that has
regressed fails the gate; the numbers are never loosened to fit.

## 3. The table

| case | product | the setting | the hiss removed | the voice kept | the chatter | the block coherence | the delay |
|---|---|---|---|---|---|---|---|
| the corpus voice clip | **ours** | reduction 30 dB | **29.65 dB** | 0.06 dB | 0.03 dB | -221.9 dB | 2047 |
| the corpus voice clip | the reference | the best that holds the voice budget | **0.09 dB** | 0.15 dB | 0.00 dB | -42.4 dB | 2238 |
| the corpus voice clip | the reference | the gain of its ambience stem at -60 dB (the most aggressive) | **23.87 dB** | 15.83 dB | 0.89 dB | — | 2238 |
| the synthetic voice | **ours** | reduction 30 dB | **29.69 dB** | 0.18 dB | 0.05 dB | -218.2 dB | 2047 |
| the synthetic voice | the reference | the best that holds the voice budget | **8.94 dB** | 0.04 dB | 0.45 dB | -60.8 dB | 2238 |
| the synthetic voice | the reference | the gain of its ambience stem at -60 dB (the most aggressive) | **8.92 dB** | 0.04 dB | 0.45 dB | — | 2238 |
| the speech shaped signal | **ours** | reduction 30 dB | **29.73 dB** | 0.12 dB | 0.03 dB | -213.9 dB | 2047 |
| the speech shaped signal | the reference | the best that holds the voice budget | **0.05 dB** | 0.20 dB | 0.00 dB | -136.2 dB | 2238 |
| the speech shaped signal | the reference | the gain of its ambience stem at -60 dB (the most aggressive) | **35.73 dB** | 16.59 dB | 0.89 dB | — | 2238 |

The gaps, computed from the rounded numbers of the table above.  The removal
is counted in our favour, the movement of the voice and the chatter against
us:

| case | the gap in the hiss removed | the gap in the voice kept | the gap in the chatter |
|---|---|---|---|
| the corpus voice clip | **+29.56 dB** | +0.09 dB | -0.03 dB |
| the synthetic voice | **+20.75 dB** | -0.14 dB | +0.40 dB |
| the speech shaped signal | **+29.68 dB** | +0.08 dB | -0.03 dB |

## 4. The number that matters

**We remove three times as much hiss as the reference, and we do it with a
tenth of its chatter.**  On the one case where the reference's technique
works at all (the synthetic voice) it removes **8.94 dB** of hiss for
**0.04 dB** of voice and it chatters 0.45 dB while it does it; at the setting
that holds the rules we remove **29.69 dB** of hiss for **0.18 dB** of voice
and we chatter 0.05 dB.  The rule asks for 10 dB of hiss removed and 1.5 dB
of voice kept.

| the synthetic voice, read two ways | the reference | ours |
|---|---|---|
| at the same hiss removed (about 8.9 dB) | 8.94 dB of hiss, **0.04 dB** of voice, 0.45 dB of chatter | 7.70 dB of hiss, **0.08 dB** of voice, 0.01 dB of chatter (filter type: gentle, reduction 12 dB) |
| at the same voice damage (about 0.05 dB) | 0.04 dB of voice, **8.94 dB** of hiss | 0.05 dB of voice, **3.85 dB** of hiss (filter type: gentle, reduction 6 dB) |
| at the setting that holds the rules | 8.94 dB of hiss, **0.04 dB** of voice, 0.45 dB of chatter | 29.69 dB of hiss, **0.18 dB** of voice, 0.05 dB of chatter (reduction 30 dB) |

**The middle row is the one that is read honestly.**  At the very gentle end
the reference is the more efficient of the two: it takes 0.04 dB of voice
damage and removes 8.94 dB of hiss, where the gentlest setting of ours takes
0.05 dB and removes 3.85 dB.  The gentle end is not where the comparison is
decided, though: the last row is.  At the setting that holds the rules we
remove **20.75 dB more hiss**, we move the wanted voice **0.14 dB more** and
we chatter **0.40 dB less**, and the reference has no setting that comes near
it.

On the other two cases the question has no valid setting at all: **the
reference has no setting that removes the hiss and keeps the voice.**  Its
voice stem is empty on both of them, so every setting that pulls the ambience
down takes the voice with it:

| the reference on the corpus voice clip | the hiss removed | the voice kept |
|---|---|---|
| the defaults | 0.00 dB | 0.01 dB |
| the gain of its ambience stem at -12 dB | 3.35 dB | 3.26 dB |
| the gain of its ambience stem at -24 dB | 9.69 dB | 9.03 dB |
| the gain of its ambience stem at -60 dB | 23.87 dB | **15.83 dB** |
| its voice stem alone | 23.45 dB | **16.28 dB** |

The rule asks for 10 dB of hiss removed and 1.5 dB of voice kept.  The
reference reaches 9.69 dB of hiss at the price of 9.03 dB of voice; we reach
**29.65 dB of hiss at the price of 0.06 dB of voice**.

## 5. The mechanism: a fader against a gate

**The reference is a three stem source separator mixed with faders, not a
gate.**  It splits the signal into a voice stem, an ambience stem and a voice
reverb stem, and the output is the mix of the three.  At the default settings
(every gain at 0 dB) the output is the input: the stems sum back to it.

Four consequences follow, and every one of them is in the table above.

1. **The hiss is removed by muting a stem.**  Pulling the ambience stem down
   removes the hiss the way a fader does: everything the stem holds goes with
   it.  There is no per band decision anywhere, so the removal is exactly as
   good as the separation — and on two of the three cases the separation has
   put the wanted content in the wrong stem.
2. **The gain curve does not chatter, because it is not computed per band.**
   It stays smooth (0.00 dB) as long as the mix does not change; it grows
   only when the stems themselves move, because the separation is recomputed
   per block.  A fader moves once.
3. **The separator only recognises speech-like material.**  On the synthetic
   voice (a jittering fundamental, a formant envelope, pauses) its voice stem
   holds the voice.  On the corpus clip — a 130 Hz vowel-like tone with thirty
   harmonics — the voice stem is **empty**: with the voice stem alone the
   output has lost 16.28 dB of the voice band, and the whole clip sits in the
   ambience stem.  This is why the reference cannot be used as a de-noiser on
   a synthetic tone.
4. **A separator is only as good as its idea of "content".**  The lesson is
   the opposite of what one would expect: a learned separator is *worse* than
   a spectral gate on material that is not speech.  We should not copy it.

## 6. Two more facts, both measured

**The stereo image is not preserved.**  The output of the reference carries the
same samples in the two channels: the side channel of the corpus clip (the
independent per channel hiss, the ground truth of the corpus) is exactly zero
in the output (the rms of the side goes from **0.0186** to **0.0000**).  The
hiss of a stereo file is not de-noised, it is discarded with the image.  We
measure the side channel separately, and our own module has to leave it alone.

**The delay depends on the block size of the host.**  The reference reports its
delay honestly (the reported delay and the measured delay agree to the
sample), but the delay itself moves with the block size:

| the block size of the host | 64 | 256 | 1024 | 4096 |
|---|---|---|---|---|
| the delay the reference adds (samples) | **1534** | **1470** | **2238** | **5310** |
| the delay our voice module adds (samples) | 2047 | 2047 | 2047 | 2047 |

A host that changes its block size (every DAW does, at the edge of a project
or when a plug-in asks for a bigger block) gets a different delay, and the
audio does not land on the same sample.  After the delay is taken out, the
output of the 4096 render and of the 64 render still differ by **42.4 dB** on
the corpus clip (our rule allows -60 dB), by **60.8 dB** on the synthetic
voice and by **136.2 dB** on the speech shaped signal.  Our module is coherent
to -213.9 dB or better, and its delay does not move with the block size.

## 7. The second reference cannot be compared here

The other product of the study **cannot compete in this comparison at all**:
its plug-in class publishes one of its modules through the plug-in interface
only, and the **noise module of that product is not reachable that way**.  The
module is chosen in the plug-in's own state and the interface exposes no way to
change it, so there is no de-noising setting of that product to measure on the
voice.  (The plug-in does process: it carries audio, with a delay of 3880 to
3993 samples, once the audio buses are activated.)

**The comparison in this document is therefore against the one product whose
voice handling is reachable.**  Nothing in it says anything about the other
one.

## 8. The honest reading: we are ahead here, and it is architectural

**In this comparison we are ahead.**  We remove 29.65 dB of hiss from the
corpus voice clip for 0.06 dB of voice movement; the reference removes 0.09 dB
for 0.15 dB, and the only setting of it that removes more than 10 dB of hiss
takes 9 dB of the voice with it.  The same holds on the speech shaped signal
(29.73 dB against 0.05 dB) and on the synthetic voice (29.69 dB against
8.94 dB, with 0.18 dB of voice movement against 0.04 dB and a tenth of the
chatter).

**The reason is architectural, not a tuning accident.**  We are a spectral
gate: it decides, band by band and frame by frame, what is wanted and what is
not, so it can empty a band that carries nothing but hiss while leaving a band
that carries the voice alone.  The reference is a separator with faders: it
decides once, per stem, what is "content" and what is "background", and then
it moves a fader.  On material its model does not recognise as speech, the
decision is wrong and no setting can undo it — which is exactly what the
corpus clip shows.  A different default, a different threshold or a different
model would not change that; the shape of the machine decides it.

Two of our numbers are the same kind of statement: our module is block
coherent to -213.9 dB or better and its delay does not follow the block size,
because it is built out of overlapping analysis windows rather than a
separator that is recomputed per block; and it keeps the stereo image, because
it works on the channels it is given.

**There is one number where the reference is ahead, and it is the price of its
architecture.**  Its chatter is **0.00 dB** where the mix does not change and
**0.45 dB** where the separation moves, against our 0.03 dB on the corpus
clip.  A fader moves once; a gate moves per frame.  Our own budget (1.0 dB) is
not exceeded, and 0.03 dB is 3 % of it, but the chatter still grows with the
reduction (0.01 dB at the defaults, 0.03 dB at 30 dB of reduction).

**What we take from this.**  Not the separation: on the material of our own
cases a learned separator is worse than a spectral gate.  The two things worth
keeping are the ones that are cheap and that we already have — the **honest
delay** (the reference declares its delay to the sample) and the **smooth gain
of a stem that does not move**.  The lesson for the gate is the chatter: a gain
curve that moves once cannot chatter, and a gate that decides per bin has to
pay for every decision it makes.

## 9. What we do not know

Stated plainly, because a head to head is only worth as much as its edges.

* **The separation quality of the reference in general was not measured.**  The
  study swept 23 settings on three cases; it did not measure the reference
  against our whole rule set on other material.  We know nothing about it on
  music, on reverberant speech, on a stereo bed, on a wind instrument or on a
  room tone.  The three cases of this document are the cases of our own
  product, and they are the cases that are hardest for a separator that
  expects speech.
* **A stem based workflow might suit some users better than a gate.**  The
  family the reference belongs to is organised around three stems, and for a
  user who wants to hear the room, to keep a reverb tail, or to work on the
  dialogue stem of a film mix, the stems *are* the product.  Our module does
  not separate, so it cannot offer that: a gate that removes the hiss cannot
  hand the ambience back.  This document does not settle which workflow is
  better; it says that on the cases of our own product, a gate wins.
* **The cost of the separation was measured on a different machine.**  The
  study timed the engine of the reference on the study machine; the figure is
  converted into the yardstick of the project (8.300 us per 1024 point
  transform, so a 64 sample block of real time at 48 kHz may cost 160.6426 of
  them) and is then comparable with our own counted cost: **36.9466 to
  51.4056 transform equivalents per 64 sample block** for this reference,
  against the **1.08 to 1.09** our voice de-noise is counted at.  The two
  sides were measured on two different machines, and the yardstick is fixed
  on purpose: a budget that moves with the machine that measures it is an
  echo, not a budget.  The conversion is stated in the method string of the
  cost constants in [`../qa/benchmark/reference.py`](../qa/benchmark/reference.py).
  The block size, the channel count and the material of the study's own
  measurement are not written down, so its figure is a block of the study's
  own rendering, and the reference remains a **yardstick, not a threshold**:
  the budget the product is held to is the budget of a block.
* **The model behind the reference cannot be read.**  Everything above is
  behaviour.
* **The corpus clip is stereo and the reference is not.**  Both sides of the
  table were measured in the mid channel for that reason; the collapse of the
  image is reported as a finding of its own (§6).

## 10. The files

| file | what it is |
|---|---|
| [`../qa/benchmark/reference_voice.py`](../qa/benchmark/reference_voice.py) | the numbers: every one of them with its case, its unit and the way it was measured |
| [`../tests/test_benchmark_voice.py`](../tests/test_benchmark_voice.py) | the gate: the properties of the constants, and our side measured live against them |
| [`../qa/benchmark/reference.py`](../qa/benchmark/reference.py) | the earlier numbers of the same reference on the same cases, from the study before this one |
| [`../qa/qualityvoice/rules.py`](../qa/qualityvoice/rules.py) | the rules the numbers are held to (R1 the voice, R3 the chatter, R5 the block coherence) |
| [`../docs/RESTORATION_REFERENCE.md`](RESTORATION_REFERENCE.md) | the same study, read over the other cases of the benchmark |
