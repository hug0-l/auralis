# De-Reverb — measurement report

**Module:** `aurcore/modules/de_reverb.py` (`AUR_DEREVERB`, element `Dereverb`)
**Harness:** `qa/reverb/` (`harness.py`, `measure_reverb.py`)
**Test:** `tests/test_de_reverb.py`
**Date:** 2026-10-21

---

## 1. Verdict

| | |
|---|---|
| **The wanted part (the 440 Hz starter), one block render** | **-0.0001 dB** — the module does not damage the sound the user wants. The 1 dB rule is met with a margin of 10 000:1. |
| **The wanted part, 64/128/256/512 sample blocks** | **-6.02 dB before the fix, -0.45 dB after it.** The shipped module turned the whole signal down when the host chopped the stream; that was a defect and it is fixed (§5). |
| **The reverberant tail, one block render** | **-6.02 dB** removed (the maximum the `Reduction = 0.5` default allows). |
| **The reverberation time** | **+0.00007 s** (1.20211 s -> 1.20219 s). The module turns the tail down; it does not shorten the decay. |
| **Latency** | **0 samples**, reported and measured. |
| **Block coherence** | **not block coherent** before the fix (-6.35 dB at 64 samples), **-14.13 dB** after it — better, but still not coherent (§7). |
| **Cost of one block** | 592 us at 64 samples on an idle machine = **44.4 % of the 1333 us budget**; the cost is flat over the block size, so the share of the budget falls with the block size. |
| **Edges** | no NaN, no infinity, no change of length, at every block size. **One finding: the module can return samples above full scale** (§8). |

---

## 2. The material and the method

The golden corpus clip `reverb` (`corpus/audio/test/reverb.wav`) is the
material, because its ground truth is written down:

| | |
|---|---|
| sample rate / channels / length | 48 000 Hz / 2 / 2.0 s (96 000 samples) |
| the wanted sound | a **440 Hz starter**, 5 ms long, starting at 0.200 s (`starter_start_s`, `starter_length_s` in the manifest) |
| the reverberant field | the starter convolved with an impulse response whose decay constant is `RT60 / ln(1000)`, so the room has an **RT-60 of 1.2 s by construction** |
| measured RT-60 of the clip | **1.20211 s** (Schroeder, over the `rt60` region 0.35 s .. 1.75 s the manifest reserves for it) |
| the dry starter | the 440 Hz burst alone; its amplitude in the wet clip is the corpus normalisation gain of 0.481627, and the least squares fit of a 440 Hz sinusoid over the starter window reads **0.4960** (the direct sound plus the first milliseconds of the ringing of the same partial) |

Everything is measured with the helpers of `corpus/measure.py`, so the
numbers are the same kind of numbers the rest of the project reports:

| figure | how it is measured |
|---|---|
| **tail removed** | the broadband level (20 Hz .. 20 kHz) over the `rt60` region 0.35 s .. 1.75 s, after minus before |
| **wanted part removed** | the level of the 410 Hz .. 470 Hz band over the window of the starter (0.200 s .. 0.205 s), after minus before; and, as a second and independent measurement, the amplitude of a 440 Hz sinusoid fitted to the starter window in the time domain |
| **change of the RT60** | the Schroeder reverberation time of the `rt60` region, after minus before |
| **time per block** | the median wall-clock time of one `process()` call, the fastest of five runs of fifteen timed blocks, against the budget `block / 48000` seconds |

Reproduce:

```
python qa\reverb\measure_reverb.py
python qa\reverb\measure_reverb.py --json var\reverb.json
python qa\reverb\measure_reverb.py --reduction 1.0
python -m pytest tests\test_de_reverb.py -q
```

---

## 3. The measurement table

### 3.1 One block render (the clip in one call)

| figure | before | after the fix | what it means |
|---|---|---|---|
| tail removed | **-6.021 dB** | **-6.021 dB** | the reverberant field is turned down by the maximum the `Reduction = 0.5` default allows |
| wanted part removed (band level) | **-0.0001 dB** | **-0.0001 dB** | the direct sound is untouched |
| wanted part removed (sinusoid fit) | **-0.0001 dB** | **-0.0001 dB** | the same, measured in the time domain |
| RT60 | 1.20211 s -> **1.20219 s** | 1.20211 s -> **1.20219 s** | the decay does not move (+0.00007 s) |

The one block render is **bit identical** before and after the fix: the
module's decision does not depend on the state it inherited, because there
is no earlier block to inherit it from.

### 3.2 The block sizes a plug-in host uses

| block | wanted part removed, before | wanted part removed, after | tail removed, before | tail removed, after | RT60 after |
|---|---|---|---|---|---|
| 64 | **-6.0206 dB** | **-0.4478 dB** | -6.021 dB | -1.808 dB | 1.20308 s |
| 128 | **-6.0206 dB** | **-0.0296 dB** | -6.021 dB | -1.724 dB | 1.19441 s |
| 256 | **-6.0206 dB** | **-0.0280 dB** | -5.984 dB | -2.496 dB | 1.26152 s |
| 512 | **-3.1155 dB** | **-0.0269 dB** | -3.288 dB | -2.731 dB | 1.24312 s |
| one block | -0.0001 dB | -0.0001 dB | -6.021 dB | -6.021 dB | 1.20219 s |

The "tail removed" column of the *before* rows is not a success: at 64
samples the module removed 6.02 dB from the tail **and 6.02 dB from the
wanted starter** — it was a flat -6 dB gain, not a de-reverb.

### 3.3 The cost of one block against the realtime budget

Budget: `block / 48000` seconds, i.e. **1333.3 us** for 64 samples.

| block | budget | median (idle machine) | median (machine at 70 % load) | share of the budget |
|---|---|---|---|---|
| 64 | 1333.3 us | **592 us** | 861 .. 1363 us | **44.4 %** .. 102 % |
| 128 | 2666.7 us | 587 us | 862 .. 1452 us | 22 % .. 55 % |
| 256 | 5333.3 us | 593 us | 829 .. 1250 us | 11 % .. 23 % |
| 512 | 10 666.7 us | 624 us | 794 .. 1081 us | 6 % .. 10 % |

(The table is the run of 2026-10-21, 16:05; the "machine at 70 % load"
column is the range that was measured on the busy machine of the first
session and is kept because it is the honest answer to "what does this cost
when the machine is busy".)

The cost **does not fall with the block size**: the module pays a fixed
analysis cost per block (a centred STFT of 2048 points with a hop of 512
plus the four band trackers), so the share of the budget falls from 44 % to
6 % while the module does exactly the same amount of work.  This is the
known catalogue-wide issue of `qa/performance/REPORT.md` §8; the module is
one of the modules that go over the 64 sample budget under load.

The machine independent calibration of the reference machine: one 2048 point
`rfft` costs 16 .. 24 us, so the 1333 us budget of a 64 sample block is
worth 55 .. 83 transforms, and the module spends 592 us = **25 .. 37
transforms' worth**.

### 3.4 The parameters

| setting | wanted part | tail | RT60 |
|---|---|---|---|
| `Reduction = 0.0` | +0.0000 dB | +0.000 dB | 1.20211 s |
| `Reduction = 0.25` | -0.0001 dB | -2.499 dB | 1.20214 s |
| `Reduction = 0.5` (default) | -0.0001 dB | -6.021 dB | 1.20219 s |
| `Reduction = 0.75` | -0.0002 dB | -12.041 dB | 1.20237 s |
| `Reduction = 1.0` | -0.0004 dB | -59.928 dB | 6.34574 s |
| `Tail Length = 0.1 / 0.3 / 1 / 3 / 10 s` | -0.0001 dB | -6.021 dB | 1.20211 .. 1.20219 s |
| `Enhance Dry Signal = on` | **+0.728 dB** | -5.292 dB | 1.20219 s |
| `Output Reverb Only = on` | the direct sound is gone (it renders the removed field) | the removed field is rendered at -20.11 dBFS peak, RT60 1.20208 s | 1.20208 s |

Two observations:

* the wanted part survives **every** setting of `Reduction`, including the
  maximum — the module separates the direct sound from the sustained field
  before it attenuates anything;
* **`Tail Length` has no measurable effect on this clip.**  The parameter
  sets the release rate of the two trackers, but the decision saturates
  (`late` reaches 1) long before the release rate matters, so the removal
  is the same -6.021 dB for every value from 0.1 s to 10 s.  The parameter
  only moves the *speed* of the transition, which is below the resolution
  of this measurement.  That is worth knowing before a user is told that
  the parameter sets the RT-60.

---

## 4. The judgement

**The wanted part is not damaged.**  Measured on the module's own path
(the clip rendered in one block, which is what the corpus measurement and
the file render of the server do):

| | value | rule |
|---|---|---|
| wanted part removed, band level | **-0.0001 dB** | within 1 dB |
| wanted part removed, sinusoid fit | **-0.0001 dB** | within 1 dB |

The starter window comes back with a sample-wise difference of **-87.7 dB**
relative to the input: the direct sound is passed through unchanged.

The tail is removed by **-6.021 dB** and the reverberation time does not
move (**+0.00007 s**).

---

## 5. The defect that was found and fixed

**The module was not block coherent, and a host that renders in small
blocks lost the wanted sound.**  Before the fix, at 64, 128 and 256 sample
blocks the module turned the **whole signal down by 6.02 dB** — the
wanted starter included.  The plug-in family drives the core with whatever
block size the host hands it (typically 64 .. 512 samples), so this is what
a user of the plug-in hears.

### The cause

`aurcore/modules/de_reverb.py` decided "direct sound or reverberant field"
with two one-pole envelope trackers, one fast (follows the transients) and
one slow (follows the sustained floor):

```python
fast = _envelope(energy, attack, min(0.9, release + 0.25))
slow = _envelope(energy, min(0.35, attack * 0.5), release * 0.25)
```

Both trackers were **recomputed from the first frame of the current
block**, every block: the state that was stored in `self._fast_env` and
`self._slow_env` was overwritten before it was ever read.  A tracker that
restarts at every block boundary can never run ahead of the other one, so
the ratio between them is always 0, `late` is always 1, and the module
reads the whole signal as reverberant.  At 64 samples there are only two
frames per block, so there is nothing left to decide with.

### The fix

The trackers carry their state from one block to the next, one state per
channel and band (`_envelope()` takes the value the tracker had when it
was last called, `_band_ratio()` stores it).  Twenty lines, no change of
the algorithm, no change of the parameter list, no change of the latency.

### Before and after

| measurement | before | after |
|---|---|---|
| wanted part removed, 64 sample blocks | **-6.0206 dB** | **-0.4478 dB** |
| wanted part removed, 128 sample blocks | **-6.0206 dB** | **-0.0296 dB** |
| wanted part removed, 256 sample blocks | **-6.0206 dB** | **-0.0280 dB** |
| wanted part removed, 512 sample blocks | **-3.1155 dB** | **-0.0269 dB** |
| wanted part removed, one block | -0.0001 dB | -0.0001 dB (bit identical) |
| sample-wise difference in the starter window, one block | -87.68 dB | -87.68 dB (bit identical) |
| sample-wise difference in the starter window, 64 sample blocks | -6.02 dB | **-24.29 dB** |
| tail removed, one block | -6.021 dB | -6.021 dB (bit identical) |
| tail removed, 64 sample blocks | -6.021 dB (flat gain, not a de-reverb) | -1.808 dB (selective) |
| block coherence at 64 samples | -6.35 dB | **-14.13 dB** |
| block coherence at 512 samples | -9.03 dB | **-21.24 dB** |
| impulse at full scale, 64 sample blocks | 0.5000 | **1.0000** |

The fix trades tail removal at the small block sizes for the wanted sound:
at 64 samples the module now removes 1.81 dB of the tail instead of 6.02 dB,
but it removes it **selectively** instead of turning the signal down.

### 5.1 The diff

```diff
--- a/aurcore/modules/de_reverb.py
+++ b/aurcore/modules/de_reverb.py
@@
-def _envelope(values: np.ndarray, attack: float, release: float) -> np.ndarray:
-    """One-pole envelope follower with separate attack and release rates."""
+def _envelope(
+    values: np.ndarray,
+    attack: float,
+    release: float,
+    state: float | None = None,
+) -> np.ndarray:
+    """One-pole envelope follower with separate attack and release rates.
+
+    ``state`` is the value the tracker had when it was last called; the
+    recursion continues from it, so a tracker that runs over a stream of
+    blocks behaves like a tracker that runs over the whole stream.
+    """
     if values.size == 0:
         return values
     out = np.empty_like(values)
-    prev = float(values[0])
+    prev = float(values[0]) if state is None else float(state)
@@
-        self._fast_env: list = []
-        self._slow_env: list = []
+        # the tracker state, one entry per channel and band
+        self._fast_env: list[dict[int, float]] = []
+        self._slow_env: list[dict[int, float]] = []
@@
-    def _band_ratio(self, energy: np.ndarray, ch: int, tail_s: float) -> np.ndarray:
+    def _band_ratio(self, energy: np.ndarray, ch: int, band: int, tail_s: float) -> np.ndarray:
         """Fraction of the band energy that looks sustained, per frame.
+
+        The two trackers are *stateful*: they continue where the previous
+        block left off, one state per channel and band.  A tracker that
+        restarted at every block boundary could never run ahead of the other
+        one, so a host that renders in small blocks would read the whole
+        signal as reverberant and turn the wanted sound down with it.
         """
         if energy.size == 0:
             return energy
         while len(self._fast_env) <= ch:
-            self._fast_env.append(None)
-            self._slow_env.append(None)
-        fast = self._fast_env[ch]
-        slow = self._slow_env[ch]
-        if fast is None or fast.shape != energy.shape:
-            fast = energy.copy()
-        if slow is None or slow.shape != energy.shape:
-            slow = energy.copy()
+            self._fast_env.append({})
+            self._slow_env.append({})
+        fast_state = self._fast_env[ch]
+        slow_state = self._slow_env[ch]
         # the fast tracker follows the transients, the slow one the sustained floor
         attack = 0.55
         release = float(min(0.95, 0.15 + 0.8 / max(0.05, tail_s)))
-        fast = _envelope(energy, attack, min(0.9, release + 0.25))
-        slow = _envelope(energy, min(0.35, attack * 0.5), release * 0.25)
-        self._fast_env[ch] = fast
-        self._slow_env[ch] = slow
+        fast = _envelope(energy, attack, min(0.9, release + 0.25),
+                         state=fast_state.get(band))
+        slow = _envelope(energy, min(0.35, attack * 0.5), release * 0.25,
+                         state=slow_state.get(band))
+        fast_state[band] = float(fast[-1])
+        slow_state[band] = float(slow[-1])
@@
-                ratio = self._band_ratio(energy, c, tail_s)
+                ratio = self._band_ratio(energy, c, bi, tail_s)
```

The state is reset by `reset()` and by the existing "the amount changed, so
this is a new setting" branch in `process()`, so a change of `Reduction`
still starts from a clean tracker.

---

## 6. The edges

`python qa\sweep_modules.py` -> **48 modules, 240 runs, no problems.**

The module on the awkward inputs, in one block and in 64 sample blocks:

| case | length in / out | non finite | peak, one block | peak, 64 sample blocks |
|---|---|---|---|---|
| silence | 4096 / 4096 | 0 | 0.0000 | 0.0000 |
| constant (0.5) | 4096 / 4096 | 0 | 0.4495 | 0.2500 |
| full scale square | 4096 / 4096 | 0 | **1.1057** | **1.0518** |
| single impulse | 4096 / 4096 | 0 | 1.0000 | 1.0000 |
| 1 kHz tone (0.5) | 4096 / 4096 | 0 | 0.5009 | 0.3147 |

No NaN, no infinity, no change of length — at any block size.

Two of these are worth a word:

* the **constant signal** comes back 0.92 dB down in a one block render.
  That is the algorithm working as designed: a constant signal *is* a
  sustained field, so the module treats it as reverberance.  It is also why
  the `Enhance Dry Signal` parameter exists.
* the **full scale square** comes back **above full scale** (§8).

---

## 7. Latency and block coherence

### Latency

| | |
|---|---|
| reported (`latency_samples`) | **0 samples** |
| measured (cross correlation against the input) | **0 samples** |

The module does not delay its output: the STFT is centred and the
overlap-add is normalised per sample, so the round trip is exact
(measured error of the bare round trip: 8.9e-16) and time aligned.

### Block coherence

A block coherent module returns the same samples no matter how the host
chops the stream.  The figure is the RMS of the difference between the
blocked render and the one block render, relative to the level of the one
block render:

| block | before the fix | after the fix |
|---|---|---|
| 64 | -6.35 dB | **-14.13 dB** |
| 128 | -6.35 dB | -14.09 dB |
| 256 | -6.35 dB | -15.92 dB |
| 512 | -9.03 dB | -21.24 dB |

**The module is still not block coherent.**  The fix improved the figure by
8 to 12 dB, but the residual difference is real: the module analyses the
block it is given, so the frames at the beginning and at the end of a block
are analysed with a truncated window.  A module that is coherent at every
block size needs the analysis to run over a continuous stream (the
streaming STFT of `qa/performance/REPORT.md` §7.0), which is a change of
the call pattern and not of this module.

The residual is also visible in the tail removal: at 64 samples the module
removes 1.81 dB of the tail instead of the 6.02 dB it removes from a one
block render, because the frames that carry the decision are the ones the
truncated window distorts.

---

## 8. What I could not fix, and what I could not measure

### The module can return samples above full scale

A full scale square comes back at **1.1057** (+0.87 dBFS) in a one block
render, **1.0518** at 64 sample blocks and **1.3021** (+2.29 dBFS) at 512
sample blocks.  The cause is not a bug in the state handling: it is the
band structure.  A square wave has its fundamental at 220 Hz (the band
below 250 Hz) and its harmonics above 660 Hz (the next band), and the
module attenuates the two bands differently, so the shape of the waveform
changes and the sum of the harmonics peaks above the original square.

It is present in the shipped code as well (1.1057 in a one block render,
1.0715 at 512 sample blocks); the fix only makes it visible at the block
sizes where the shipped code hid it by turning everything down by 6 dB.
The measured magnitude of the effect never exceeds the input peak by more
than 2.3 dB.  I did **not** fix it: a limiter is a design decision (it
would change the level of every render), and the module belongs to the
restoration catalogue, where the tail removal is the product.  The number
is pinned down here so that it is visible.

### Other findings that belong to somebody else

* the **cost of a block does not fall with the block size** (592 us for 64
  samples and 624 us for 512 samples).  This is the catalogue-wide issue
  of `qa/performance/REPORT.md` §8 and it needs the streaming analysis,
  which touches `aurcore/dsp/stft.py` — not my file.  I did not write a
  patch for it, because the change belongs to the DSP owner and to the
  performance work that is already written down.
* the module is **not block coherent** at the 10 % level (§7).  The same
  streaming analysis fixes it.
* **`Tail Length` has no measurable effect** (§3.4).  The parameter is
  documented as "the decay time used to separate the sustained field from
  the direct one", but the decision saturates before the release rate
  matters.

### What I could not measure

* **The absolute cost of a block** is only as good as the machine: the
  numbers in §3.3 were taken while the machine was 70 % busy with other
  work in the first session, so the median moves by a factor of 1.6 between
  two runs.  The figure I quote (592 us) is the median of the idle run of
  2026-10-21, 16:05; the table gives the range that was measured on the
  busy machine.  The number that does not move with the load is in
  `qa/performance/REPORT.md`: the module costs 77 transform equivalents at
  64 samples against a budget of 213.
* **The behaviour in a real room.**  The corpus clip is a synthetic room
  (an exponentially decaying noise impulse response convolved with a
  5 ms burst).  A real recording has a diffuse field, several direct
  sounds and programme material between them; the corpus clip says nothing
  about how the module behaves on a dialogue track, and the corpus has no
  such clip.
* **The behaviour of the module inside the VST3 plug-in.**  The plug-in
  host (`qa/vst3host`) was not rebuilt after the change, so the numbers
  here are the numbers of the Python core, not of the shipped binary.
  Building the plug-in is the adapter agent's work.

---

## 9. The files

| file | content |
|---|---|
| `qa/reverb/harness.py` | the measurement library: the wanted part, the tail, the RT-60, the edges, the latency, the block coherence, the realtime budget |
| `qa/reverb/measure_reverb.py` | the command line: `--reduction`, `--tail-length`, `--json` |
| `qa/reverb/REPORT.md` | this report |
| `tests/test_de_reverb.py` | the quality gate (16 tests) |
| `aurcore/modules/de_reverb.py` | the module; the fix is in `_envelope()` and `_band_ratio()` |

Reproduce:

```
python qa\reverb\measure_reverb.py
python -m pytest tests\test_de_reverb.py -q
python qa\sweep_modules.py
python tools\hygiene_check.py
```

---

## 10. The state of the tree while this was measured

The tree was being worked on in parallel while these measurements were
taken.  The measurements of this report are the run of **2026-10-21,
16:05**, on a machine that was 10 % busy:

* `qa\sweep_modules.py` reports **48 modules, 240 runs, no problems**.
* `tools/hygiene_check.py` reports **0 findings in 372 files**.  It reported
  findings in `.github/workflows/ci.yml`, `.github/workflows/release.yml`,
  `packaging/README.md`, `qa/fft/REPORT.md` and `tests/test_analysis_ui.py`
  while those files were being written; all of them have been fixed by their
  owners.
* `tests/test_modules_specific.py::test_voice_denoise_keeps_the_voice`
  failed with -7.46 dB while the Voice De-Noise module was being rewritten.
  It is rewritten now, and the module survey measures its voice band at
  0.02 dB (`qa/survey/REPORT.md`).

The numbers in this report were all taken with the tree in that state; none
of them depend on the modules that were moving.
