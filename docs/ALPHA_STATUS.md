# Auralis — Alpha Status

> A new audio restoration workbench, built from a reverse-engineered architecture study.
> This file is the consolidated state of the alpha: what exists, what was measured, and
> what the next milestones are.

---

## 1. What this is

A spectral audio restoration workstation with a modular processing chain and a plug-in
ecosystem, implemented as:

| Layer | Technology | Contents |
|---|---|---|
| **Core DSP** (`aurcore/`) | Python 3.15 + NumPy 2.5 (SciPy optional) | 48 processing modules, a name-keyed parameter framework, a module registry, an STFT/spectral engine, a resampler, filters, loudness metering, serialisation |
| **Server** (`aurserver/`) | FastAPI + uvicorn | HTTP/WS API, asset store, analysis, render, preset library, batch processor, streaming preview |
| **UI** (`aurui/`) | Vanilla ES modules, no build step | Dark studio interface: module browser, composite spectrogram + waveform view, module inspector, chain strip, transport, status bar |
| **Codegen** (`tools/`) | Python | The canonical parameter table is the single source of truth; every surface is generated from it |
| **Plugins** (`plugins/`) | VST3 adapter written against the SDK + identity stamping | One template adapter, per-module identity record, stable host parameter ids, 48 built binaries |

### Run it

```powershell
cd <the repository root>
.\scripts\run_dev.ps1          # or: python -m aurserver --port 8731
```
Open `http://127.0.0.1:8731/`.

---

## 2. The architecture that was reverse-engineered, and what it changed

Three findings from the study of the reference product drove the design:

**2.1 One template adapter, identity stamped at build time.**
All 17 plug-in binaries of the reference product are *exactly the same size* (2,063,872 bytes)
yet have different hashes. They are one template with a per-module identity record patched in —
not a separately compiled plug-in per module. The same holds for the AAX side (20 bundles,
2,162,496 bytes each, differing only in a single PE resource). Auralis reproduces this:
`plugins/identity.py` derives a deterministic identity per module and stamps it into a
template binary, so **adding a module yields a plug-in for every format with no adapter work**.

**2.2 Parameters are addressed by name, everywhere.**
The reference's plug-in page tables address parameters by human-readable name
(`knobID="Sensitivity"`), and its presets use the same names. Auralis makes the name the
canonical identity everywhere, with a single registry that generates the C++ descriptor
table, the preset writer, the chain writer, the plug-in page tables, the host parameter id
map, the UI widget descriptors and the documentation.

**2.3 The plug-in holds no parameter data; the core does.**
The reference's 2 MB adapter template contains *no parameter names at all* — the list is
fetched from the core at run time through a versioned callback (`PluginCoreCallback`,
interface version 1097). Auralis follows the same split: the adapter is a thin bridge,
the core owns the registry.

Two further corrections were applied to the study itself, and they changed the spec:

- The AAX 4-character effect ids are **not** in the plug-in binary; they live in the core
  next to the page tables.
- The parameter-label truncation ladder is a **straight prefix cut** (`label[0:s]`), verified
  against 113 shipped ladders — not a word-aware rebalance. Auralis implements the prefix cut
  and locks it with a golden regression test.

---

## 3. State of the build

| Item | Count | Status |
|---|---|---|
| Canonical module definitions | 51 | complete |
| Modules implemented | **51** | complete |
| Parameters defined | 295 | complete |
| DSP primitives | 15 modules | complete, numerically verified |
| Core C ABI | implemented | **compiled, loadable, 18 exports, contract version 4** |
| VST3 plug-in family | **51 binaries** | **built, loaded by an independent host, tail and latency declared** |
| AAX plug-in family | **51 bundles** | **built against the format's own SDK and validated by the format's own conformance validator (11 of 11)** |
| Learned models | **5 artefacts** | **trained on our own material, deterministic to the bit; four per frame, one per sample** |
| MCP server | **11 tools** | **an AI agent can drive the product: list, describe, set the chain, switch the processing mode, render, measure** |
| Release packages | full and slim | **built, checksummed, reproducible, verified by their own manifest** |
| Automated checks | 108 test files, 2109 test functions | all green |

### 3.1 Verification status

The gates run with one command: `python scripts\run_gates.py`.

```
imports                PASS   4.0s
finite                 PASS   4.1s
edges                  PASS   18.1s
biquad                 PASS   1.2s
js-duplicates          PASS   0.6s
pytest                 PASS   1037.8s
schema                 PASS   3.5s
codegen-drift          PASS   1.2s
css-coverage           PASS   0.1s
verify-app             PASS   5.2s
hygiene                PASS   4.4s
lint                   PASS   1.9s
vst3-host              PASS   22.9s
journey                PASS   9.6s
benchmark              PASS   10.9s
```

Two of the gates exist because the measurement itself has to be trustworthy:
`qa/import_smoke.py` proves that every module of the package imports, and
`qa/check_module_finite.py` proves that no module ever returns a non-finite
sample.  Both were added after a defect that no other gate would have seen.

### 3.1.0 The VST3 family

The plug-in is written **straight against the VST3 SDK** (`plugins/vst3native`), with no
framework in between, because the framework and the SDK versions available do not line
up: a framework release needs a 3.6 era SDK layout, the next one needs a 3.7 era layout
that no SDK tag provides, and the SDK in this repository is 3.8.1 in the split layout.
One object implements the four interfaces the format asks for, and the module it
implements is decided by the identity record the build stamps into the binary.

| Measurement | Value |
|---|---|
| Plug-ins built | **48 / 48** in 8.1 min (9–11 s each, 2 jobs) |
| Plug-in binary | 184 kB each; the bundle carries the plug-in alone, and the core and the interpreter live once, in the runtime directory next to the bundles |
| Exported symbols | `GetPluginFactory`, `ModuleEntry`, `ModuleExit` |
| Class id | the one in the identity record, read back from the built binary |
| Parameters published | the module's own parameters, with names, steps, units and display strings |
| Independent host | `qa/vst3host` (written against the SDK) loads the module, enumerates the classes, connects the component with the controller, accepts the bus arrangement, sets a parameter and reads it back, processes 128 frames, processes a 1 kHz tone, and restores a 655 byte state in both directions |
| Six modules through that host | `exit 0`, **no fault**, `result: OK` each |
| Audio path | 1 kHz at −6 dBFS in → 0.500000 out (Spectral Editor), 0.502350 (De-Hum), 0.131693 (Spectral De-Noise, which is a quality problem, not a path problem), 0.000000 (Voice De-Noise, which is the quality problem of defect 3) |
| The gate | `tests/test_vst3_host.py` runs the host against three modules and fails on a fault, so the reference counting cannot regress unnoticed |

### 3.1.0.1 What the quality work changed, with the numbers

| Module | Before | After |
|---|---|---|
| **Resample** (64-sample block) | 17 619 µs (1321 % of the budget) | **46 µs (3.4 %)**; the −46.7 dB error spike of the upward conversion is gone (−304.3 dB) |
| **Voice De-Noise** (64-sample block) | 4477 µs (336 %) | **5.3 µs (0.4 %)**; hiss −5.27 → **−14.50 dB**, the voice −0.68 → **−0.03 dB**, SNR **+14.47 dB**, block coherence −19.7 → **−218.3 dB** |
| **Deconstruct** (64-sample block) | 2478 µs (186 %) | **37.5 µs (2.8 %)**; the tonal isolation **+24.7 dB**, the noisy **+21.1 dB**, the transient **+19.8 dB**, and the output is now bit identical at every block size |
| **Repair Assistant** (64-sample block) | 3674 µs (276 %) | **1.1 µs** idle, 1027 µs with all six repairs at full strength; the detection is no longer repeated per block and the output is unchanged |
| **Guitar De-Noise** | 2054 µs (154 %) | **725 µs (54 %)**; the output differs by 8.9e−16 |
| **Ambience Match** | 2916 µs (219 %) | **808 µs (61 %)**; 193 → 55 arrays per block |
| **De-Click** | the programme lost **10.58 dB** at Sensitivity 6 | **0.000 dB at every sensitivity**, and the step between two sensitivities is 0.0014 dB instead of 10.24 dB |
| **De-Rustle** | the wanted part lost **5.63 dB** | **0.33 dB** |
| **De-Reverb** | the streaming path turned the whole signal down by **6.02 dB** | **0.03 dB** |
| **Spectral De-Noise** | the gain curve varied by 1.78 dB², a wanted partial lost 7.64 dB | the gain curve varies by **0.00 dB²**, the band reduction is **14.03 dB**, the partial still loses 2.60 dB (the remaining defect) |
| **FFT** | the inverse transform silently dropped a sample in 512 of 1024 sizes, with the level wrong by up to 1.6 dBFS | **0 of 1024**; the round trip is −298.3 dBFS at the worst size |
| **Filters** | the one-pole corner was **+18.95 %** high at 10 kHz | **0.000 %**; the file offered no linear-phase filter although a module promises one — it does now |
| **Loudness** | a 5.0 channel programme read **−22.36 LUFS** (the standard says −23.0), the loudness range read 30 LU instead of 15, the true peak read **+1.48 dBTP** | **−23.02 LUFS**, **15.0 LU**, **0.0000 dBTP** |
| **WebSocket preview** | 365 of 375 blocks underran and a parameter change never arrived | **0 underruns**, the change is heard after **11.1 ms**; the preview runs the fast path and says so |
| **The plug-in family** | 392 960 512 B (the interpreter copied 48 times) | **16 464 608 B** with the runtime shared and loaded by the plug-in itself |

The numbers are not comparable across the whole table: the "before" figures were taken
on a busy machine, and the performance study calibrates against a reference transform
so that the comparison survives a machine that is shared.  Each row is before and
after in the same process, on the same machine, interleaved.

### 3.1.1 The budget of the real time gate

The gate measures the cost of a block as a number of **transform equivalents**:
the block is timed next to a batch of 1024-point transforms of the same
machine, so the number does not move when the machine gets busier.  The
budget is **160.6 transforms** for a 64-sample block at 48 kHz (1333 us).

That reference is a **fixed** number (8.300 us per transform), not the
yardstick of the machine that happened to measure it.  A budget that moves
with the measuring machine is not a budget but an echo of it, and the
comparison between two machines would be meaningless.

The gate is a verdict on a machine that is **not saturated**: measured on a
quiet machine and on a saturated one, the memory-bound modules inflate by
2.9x to 7.9x while the transform the calibration is taken with does not
move.  The load during a measurement is therefore reported with every
table, and the numbers in this document were taken with the machine quiet.

### 3.1.0.2 The open defects, honestly

| # | Defect | Where | State |
|---|---|---|---|
| 1 | The module faulted when the host released it | the adapter's reference counting | **fixed**.  The component and the controller are now separate objects, the values travel over the connection point, and every object is deleted by its last release.  2000 create-and-release cycles grow the private memory by **0 bytes**. |
| 2 | Spectral De-Noise takes 10.9 dB out of the noise band but also 7.6 dB out of a wanted partial, and its gain curve varies by 1.8 dB² | `aurcore/modules/spectral_de_noise.py` | **partly fixed**: the gain curve now varies by **0.00 dB²** and the band reduction is 14.03 dB, but a wanted partial still loses 2.60 dB, and the module is still over the block budget. |
| 3 | Voice De-Noise attenuates the voice by 2.2 dB and is not block coherent | `aurcore/modules/voice_de_noise.py` | **fixed**: the hiss is now removed by 14.50 dB, the voice moves by **0.03 dB**, the block coherence is **−218 dB**, and the block cost fell from 4477 µs to 5.3 µs. |
| 4 | Modules need more than the block budget at 64 samples | `qa/performance/REPORT.md` | **mostly fixed**: 46 of 51 modules are inside the budget; the rest are held at a "debt" ceiling (measured + 50 %) until they are repaired. |
| 5 | The De-Click detector fires on steady material at Sensitivity 5–8 and eats the programme | `aurcore/modules/de_click.py` | **fixed**: the programme is now bit exact at every sensitivity, and the step between two sensitivities is 0.0014 dB instead of 10.24 dB. |
| 6 | The analysis payload's frequency axis is off by a factor of 1.5 | `aurserver/audio.py` | **fixed**: a 1 kHz tone is reported at 984.4 Hz (a third of a row width) and no row copies another. |
| 7 | There was no session document route | `aurserver/app.py` | **fixed**: the routes exist, the document carries the module state, and the undo of a removal brings the module back **with** its state (220 bytes before and after; it was 144). |
| 8 | The EQ Match module produced an output **297.9 dB louder** than the input in a 64-sample host | `aurcore/modules/eq_match.py` | **fixed**: the band level is now the mean square of a windowed transform, the correction is split into a broadband part and a spectral shape, and every correction is bounded at 24 dB. The output is now the input within 1 dB, and a reference 6 dB quieter is matched to -6.00 dB. |
| 9 | The guitar de-noise removed the guitar's own fundamental by 11.96 dB | `aurcore/modules/guitar_de_noise.py` | **fixed**: the run length that decides whether a narrow band component is a burst was looping over the channel axis instead of the time axis, so every peak was a burst. The fundamental now moves by 0.08 dB and the mains detector, which was inert, removes a 50 Hz hum by 48.7 dB. |
| 10 | The dialogue isolate took 10.32 dB of the voice with the noise gain | `aurcore/modules/dialogue_isolate.py` | **fixed**: the transform size now resolves a 110 Hz fundamental (4096 points, 9.4 bins between the harmonics). The voice moves by 0.47 dB and the noise by 11.75 dB. |
| 11 | The de-construct module put 38 % of a steady noise bed into the transient stem, so the noisy gain removed only 3.76 dB | `aurcore/modules/deconstruct.py` | **fixed**: a transient is now measured against the band's own history over the previous nine frames. The bed goes to the noisy stem (98.2 %) and the noisy gain removes 8.23 dB. |
| 12 | The ambience match noise gate could never open (a Rayleigh magnitude against 1.5x the 20th percentile) | `aurcore/modules/ambience_match.py` | **fixed**: the floor is the rms of the quiet frames of a rolling window and the gate is 3x that floor. The gate now opens on 100 % of the bed and 0 % of the programme, and the bed is removed by 10.76 dB. |
| 13 | The core has **no tail concept**, so a plug-in cannot report that it rings | `aurcore/api.py`, the C ABI, the adapter | **fixed**.  A module declares its tail next to its latency, the chain takes the **maximum** over its modules (not the sum), the ABI carries it as the export that grew the table to seventeen (`aur_engine_tail_samples`; the table has eighteen entries today and the contract is at version 3) and the adapter answers `getTailSamples()` from the core.  The declared tail now matches the measured ring-out for every module: the de-construct 1214 samples, the voice de-noise 1536, the guitar de-noise 600, and the ambience match and the signal generator declare an infinite tail because the bed is of their own making.  The gate holds the declaration to 1024 samples (21.3 ms) of the measured ring-out. |
| 14 | The time and pitch modules carry a **second, independent phase vocoder**, so they did not get the repair the shared one received | `aurcore/modules/time_pitch.py` | **fixed**: the second vocoder is gone, the modules use the repaired one. A stretched block used to reach +70.41 dBFS, and now reaches -4.53 dBFS. |
| 15 | Three modules delay the output by 125 to 137 samples while reporting a latency of zero | `aurcore/modules/resample.py`, `variable_pitch.py`, `eq.py` | **being fixed** (the tail agent).  Measured by the host's sweep. |
| 16 | The de-clip module moved the signal 7.77 dB *away* from the truth | `aurcore/modules/de_clip.py` | **fixed**: the module treated any loud sample as clipped. It now finds the flat tops, rebuilds the waveform under them and gives the programme the level the clipping chain took away: **14.95 dB closer to the truth, 1.37 dB of damage**.  The rule that measures it was settled at the same time: the ground truth of a clipped case is stated at the digital ceiling, because a hard clipper is memoryless and cannot tell us the level it destroyed (`aurcore/ceiling.py`, `qa/quality/ground_truth.py`). |
| 17 | The shared FIR did not delay the signal by the latency it reported, and it answered in its pre-delay region | `aurcore/dsp/filters.py` | **fixed**: the output is read at the full length of the kernel (the convolution itself delays by the centre of the kernel) and the filter is silent until its delay has elapsed. The delay and the amplitude are now identical at block sizes 16384, 4096 and 64. |
| 18 | The resampler was not transparent at ratio 1: the kernel reached 48 samples right of the read position, so the first 47 and the last 48 samples of every block were computed from a truncated kernel (a constant 0.5 came out 0.5647 and 0.3056) | `aurcore/modules/resample.py` | **fixed**: every phase's kernel is normalised to sum to one, the resampler is a streaming filter that keeps the history the kernel needs, and the read position is counted from the start of the stream. A constant now comes out at 0.5 to 1.11e-16, one block and many blocks are bit identical, and the module declares the 48 output samples of look-ahead it needs. |
| 19 | The plug-in family cannot change the sample rate | the core's C ABI | **open**.  The ABI writes the engine's output into a buffer of the input's block size and zeroes the rest, so a rate-changing plug-in loses samples.  The latency is right for the core and for the server; a plug-in that changes the rate has to run at the target rate for the number to be the one the host expects. |
| 20 | The plug-in **did not work in a third party host**: it published only the bypass switch and passed the audio through bit for bit, because the adapter took the chain from the plug-in's state and a foreign host's state carries none | `plugins/vst3native/AuralisVST3.cpp` | **fixed**: with no state the plug-in builds the module's own default chain and publishes the module's full parameter set, and a host that sends a state afterwards can replace it. The gate drives the plug-in with an **empty** state. |
| 21 | Nine of the fifty modules **handed back a signal above the digital ceiling** (the ambience match 3.43, the interpolate 518.25) | `aurcore/ceiling.py`, the base class | **fixed**: the rule lives in `aurcore/ceiling.py`, so it touches no module file, and it is applied at the **output** of the product — the chain's output (`aurcore/api.py`) and the plug-in's output (`plugins/vst3native/AuralisVST3.cpp`) — and not inside a module.  The limit is the digital ceiling multiplied by the gain the chain's own level controls declare, so a module whose gain is the user's own is exempt — the exemption being the range that parameter declares.  230,238 samples over the ceiling became **0** at the output, and a file the product writes never exceeds the digital ceiling. |
| 22 | The **host never activated the audio buses**, so a plug-in that takes the specification seriously wrote nothing at all (512 unwritten samples, a peak of 1e30) | `qa/vst3host/main.cpp` | **fixed**: the buses are activated before the component is set active, and the report carries the written and unwritten sample counts, so a plug-in that writes nothing is caught by the gate. |
| 23 | The survey and the host searched **2048 samples of lag** for a delay, so every delay above about 43 ms was invisible — and the host **exited 0** while measuring nonsense | `qa/survey/harness.py`, `qa/vst3host/main.cpp` | **fixed**: the bound comes from the catalogue (the largest declared delay plus a margin, 6896 samples), and every module with a declared delay now measures exactly what it declares — 46 of them to the sample. |
| 24 | The **parameters of the table and the parameters of the modules were in a different order**, so the plug-in published one parameter at the id the host would drive | `aurcore/schema/params.tsv` | **fixed**: the table now lists a module's rows in the order the module declares them, and a new gate holds the two orders together (154 tests). |
| 25 | The **cost of the reference was measured on another machine** (0.31 and 0.42 transform-equivalents) and our own cost was a whole-clip figure scaled to a block, so the comparison said we were behind by 4.07 on every case | `qa/benchmark/reference.py`, `qa/benchmark/harness.py` | **fixed**: both sides are on the product's fixed yardstick (8.300 us per transform). The reference is 36.95 to 346.39 transform-equivalents, ours 1.08 to 4.95 ⇒ **every case is ahead by 32.0 to 35.9**. |
| 26 | A constants file **could not be imported at all** (five names carried a hyphen), so nothing in it was ever checked | `qa/benchmark/reference_bridge.py` | **fixed**, and a new gate makes every constants file of the benchmark parse, import and carry a method for every number. |
| 27 | The **applied gain followed the level of the input**: the gain moved 14.67 dB when the material's level moved 18 dB, and the gain curve varied 1.02 dB² against a budget of 1.0 | `aurcore/modules/voice_de_noise.py` | **fixed**: the decision and the reference moved into the ratio domain, and the reference carries the memory of the lowest balance the band has ever shown. The movement is now **0.01 dB**, the curve **0.0181 dB²**, the settling **0.0 ms** — and the removal went **up** (20.80 to 29.66 dB). The rule is now R9. |
| 28 | A parameter change was a **step**: the largest jump was 0.1228, which the reference's 0.0805 would call a click | `aurcore/api.py`, the ABI, the adapter | **fixed**: a change is applied over 20 ms (16 steps of 1.25 ms), the excess over the signal's own step is **0.000166**, and the ramp is stated in the parameter's own description and in the plug-in's report. |

The defects that the last measurement of the study shows in our own modules are
listed in [`docs/OPEN_DEFECTS.md`](OPEN_DEFECTS.md): the rule every module
breaks, the number that shows it, and the gate that holds the rule.  Three of
them carry a strict `xfail` marker in that gate, so they are expected
failures and not passes; a fourth one carried no marker and failed the
gate, and the tree closed it while the list was being written.
| 29 | The **ceiling rule was applied per block**, so a module's answer depended on the block size: the resampler's one-block and many-block renders differed by 0.00932, and the spectral editor's surroundings changed between two renders of the same material | `aurcore/ceiling.py` | **fixed**: the rule is at the **output** of the product and not inside a module any more — the chain's output (`aurcore/api.py`) and the plug-in's output (`plugins/vst3native/AuralisVST3.cpp`) — and it is applied by **clipping**, not by scaling.  A clip is a per sample operation, so it is block independent by construction and it does not move the level of anything that was inside the limit: the spectral editor's surroundings are bit identical again, and the resampler's one-block and many-block renders are the same.  The limit is the digital ceiling multiplied by the gain the chain's own level controls declare, so a user who asks for +6 dB gets the +6 dB and only the samples that go over the limit are clipped — the gain is not scaled back.  The quality instrument measures the module and not a scaled signal, and the de-clipper's wanted partial moved from 1.043 dB to **0.3108 dB** while the restoration came from 16.03 dB to **20.76 dB** closer to the truth. |
| 30 | A **stale build directory** (`plugins/abi/build_main`, 76.8 MiB of a copied interpreter and its libraries) sat inside the product tree, and the ABI test loaded **the library it held** — an older one, with an older manifest — so the conformance tests failed against a library that was not the tree's | `tests/test_plugin_abi.py` | **fixed**: the directory is gone, and the test now looks for the build the tree actually uses (`$AURALIS_ABI_BUILD` or `plugins/abi/build`).  A scratch directory in the tree is not a scratch directory. |
| 31 | **Three transient modules had a dead knob.** The threshold range of the shared repair engine was 0.55 to 3.0, but the score of a real transient is 9.3 to 27.6 and the score of steady material is 0.43 to 5.0 ⇒ the range sat **below the whole population** and every setting gave the same output, bit for bit. The Mouth De-Click at its most sensitive setting also **damaged the steady programme** | `aurcore/modules/{de_click,mouth_de_click,de_crackle}.py` | **fixed**: the range is the module's own, and it runs through the population — the De-Click 1.2 to 6.0, the Mouth De-Click 6.0 to 11.0, the De-Crackle 0.6 to 1.1. The knob now moves the answer (the graded transients 1 of 6 to 2 of 6, the bursts 0 of 4 to 4 of 4) and the steady programme is **bit exact at every setting**. |
| 32 | **Four gates failed in the whole suite and passed on their own.** The registry is process-global, and a test that registered a dummy module without the isolation fixture left it there ⇒ every later test that compares the **live catalogue** with a **committed table** saw one module too many | `tests/conftest.py` | **fixed**: the registry is restored after **every** test, whatever the test did, so the leak is impossible by construction. A rule that only shows up when the whole suite runs is exactly what the suite is for. |
| 33 | The **delivered loudness** of a transient clip missed its target by 2.84 LU, and the report did not say why | `aurserver/batch.py` | **fixed**: the limitation is recorded before the plan gives up, the message names the gain the target needs, and every row outside the tolerance carries a reason. The miss itself is the truth: the clip carries a full scale impulse and the target's true peak ceiling would not allow the gain. |
| 34 | The **recorded loudness table** was stale by up to 2.14 dB on 74 of its 112 rows | `tests/test_batch.py` | **fixed**: the cause was measured (the loudness module's rewrite changed what it measures), the table was re-measured with the runner itself, and the docstring records the cause. |

Two defects were **fixed** during the integration and are worth naming, because both
were invisible until a number was measured:

- the voice de-noise module wrote only the bins inside its filter bank into an
  uninitialised array, so the bins outside the bank carried whatever the allocator
  handed out, and a single NaN there poisoned the whole output.  Eight of two hundred
  runs produced a completely silent module.  The same pattern in the wow-and-flutter
  module accumulated onto uninitialised memory instead of onto zero.
- the identity slot of the adapter is a `const` object, and the compiler is free to
  assume its contents never change — which would have folded the stamped record away
  and left every plug-in with the identity of the template.  The record is read through
  a `volatile` pointer for that reason.

### 3.1.1 Defects found and fixed during integration

| # | Defect | Where | Fix |
|---|---|---|---|
| 1 | Spectrogram rendered as a flat white field | view ↔ server contract | the server publishes the matrix as `uint8` with a scale; the view read it as decibels. The view now honours `spectrogram_encoding`. |
| 2 | Frequency ticks and the decibel legend were unreadable over bright content | `view.js` | labels get a translucent panel; the legend gets a bordered overlay panel |
| 3 | Duplicate routes: the flat preset store shadowed the real library, and the preview WebSocket could not be reached | `aurserver/app.py` | removed the duplicates, wired `presets`/`ws`/`batch` into the app |
| 4 | Every module reported itself as "learnable", so the UI offered a Learn button that could not work | `modules/base.py` | the capability flag was renamed `supports_learn` (it had been shadowed by the `learn()` method) |
| 5 | Channel-count mismatches in four modules when the input layout changed | 4 modules | buffers now adapt to the incoming channel count |
| 6 | A spectral frame-count mismatch in the interpolation and spectrum-recovery paths | 2 modules | side lengths are equalised; the per-bin level is computed on the correct axis and clamped |
| 7 | The acceptance harness mis-counted frames of a 24-bit WAV | `qa/verify_app.py` | the frame count now comes from the RIFF header |
| 8 | `create_engine().add_module(...)` raised unless the caller imported the module package | `aurcore/api.py` | the core imports its own module package |
### 3.1.2 The core C ABI is real now

The plug-in adapter had a contract but no implementation. It has one:

| Piece | What it is |
|---|---|
| `aurcore/abi_bridge.py` | the Python side: one buffer in, one buffer out |
| `plugins/abi/aurcore_abi.cpp` | the C ABI over the core; embeds CPython, drives the bridge |
| `plugins/abi/build_abi.py` | builds the shared library |
| `tests/test_plugin_abi.py` | 42 conformance tests that load it the way a host does |

The build needs **no installed toolchain** — it falls back to the compiler that ships
inside the `ziglang` wheel, then to `cc`/`gcc`/`clang`. The result is a 196 kB
`aurcore_abi.dll` that a host can load.

The tests pin down the whole contract: the five required entry points and the ten
extensions; success is `0` (or a non-negative index), failure is a negative `AurError`;
parameters are addressed **by name** in **natural units**; an enum value is its
**choice string**; the audio buffer is **planar**; a bypassed module is bit-transparent;
the chain serialises to `auralis.chain` and restores from it.

Writing it turned up four defects that a document could never have caught, all now
covered by the tests:

- the `Py_BuildValue` format string of `aur_engine_set_param` was one slot short, so
  every parameter write raised and left an exception pending;
- the `'y'` format code was used for the audio block, but it expects a C string plus a
  length, not a bytes object — the binary payload has to travel as `'O'`;
- the per-channel scatter loop copied the *whole* remaining buffer into each plane,
  which overran the caller's memory (it showed up as a heap corruption crash);
- the bridge returned the processed audio interleaved while the ABI is planar, so the
  right channel came out as garbage.



### 3.1.3 The last module, written by hand

De-Reverb was the one module an agent missed, so it was written directly.
It follows the reference's **four-band** design (the shipped page tables expose
`Band strength low / low-mid / high-mid / high` against a shared suppression
core), separates the sustained field from the direct one with a fast and a
slow envelope tracker per band — the slow tracker's release time is the
`Tail Length` control — and attenuates only the reverberant share.

Measured on a 440 Hz tone convolved with a 0.8 s synthetic impulse response:

| | before | after |
|---|---|---|
| reverberant tail (0.3–0.9 s) | 23.32 dB | **14.55 dB** (−8.77 dB) |
| direct part (0–0.2 s) | 32.83 dB | 31.05 dB (−1.77 dB) |
| `Reduction = 0` | — | transparent to **5.7e−14** |

It also exposes the analysis output: `Output Reverb Only` renders the removed
component (22.36 dB tail against 14.55 dB of residual in the cleaned signal).

Four defects were found and fixed while writing it — all of the same family,
and all of them the kind that only show up when a module is run in blocks
shorter than its transform:

- the attenuation gain had the **wrong sign** (it amplified the tail by
  +54 dB instead of attenuating it);
- the overlap-add normalisation divided by a near-zero weight sum at the block
  edges, which blew the head up by 46 dB;
- the output was cropped without compensating the centred-transform offset, so
  the block came back shifted by one transform length;
- `numpy`'s `convolve(..., "same")` returns the length of the **longer** input,
  so the smoothing kernel overflowed the frame axis on short blocks.


### 3.2 Measured DSP numbers

| Measurement | Target | Measured |
|---|---|---|
| STFT round trip (COLA, Hann 50 %) | < âˆ’60 dBFS | **âˆ’307.6 dBFS** |
| FFT vs naive DFT | < 1eâˆ’9 | **9.4eâˆ’16** |
| Resampler alias suppression | suppressed | **âˆ’118 dBFS** (ultra) |
| Resampler passband gain | unity | **±0.00005 dB** |
| Biquad at cutoff | −3 dB ± 0.1 | **−3.0103 dB** |
| De-Click, single click | â‰¥ 20 dB | **194.8 dB** |
| De-Click, 50 Hz periodic buzz | â‰¥ 20 dB | **82.1 dB** |
| De-Click, "Output Clicks Only" | concentrated | **99.4 %** inside the click window |
| De-Crackle, crackle in noise | — | **12.4 dB** |
| Mouth De-Click | — | **58.2 dB** (98 % concentrated) |
| De-Clip, clipped tone THD | < âˆ’12 dB | **âˆ’29.38 dB** |
| Interpolate, 200-sample dropout | — | **193.3 dB** |
| 50 Hz hum attenuation | â‰¥ 20 dB | **âˆ’59.9 dB** (1 kHz kept âˆ’0.117 dB) |
| Sibilance reduction | â‰¥ 6 dB | **âˆ’13.7 dB** |
| Plosive transient reduction | — | **−11.3 dB** (consonant body −1.33 dB) |
| Learned hum fundamental | exact | **50.00001 Hz** |
| Voice De-Noise, hiss under a voice | â‰¥ 8 dB | **9.04 dB** (voice unchanged) |
| Spectral De-Noise, SNR gain | â‰¥ 6 dB | **+11.9 dB** |
| De-Reverb, reverberant tail | — | **−8.77 dB** (direct part −1.77 dB) |
| Centred-extract rejection (hard-panned) | near-silent | **âˆ’113 dB** |
| Find-Similar recall | > 0.9 | **0.9891** (non-matches â‰¤ 0.33) |
| Monitor loudness (1 kHz, −20 dBFS mono) | −20 LUFS ± 0.3 | **−19.71 LUFS** |
| Module throughput | realtime | **47–105× realtime** |
| Spectrogram view | 60 fps | **6.18 ms/frame @1920×900** (162 fps headroom) |
| Analysis payload | < 2 MB | **1.6 MB** worst case (60 s noise) |

### 3.2.1 Two defects the verification caught in the shipped design

- **The minimum-statistics tracker was clamped the wrong way.** It was written as
  `min(tracker + 0.05, lowest)`, which pins the tracker to the *first* floor it ever
  saw and lets it creep up by only 0.05 dB per frame — so a signal that opens on a loud
  passage left the noise reference far below the real floor, the Wiener term never
  engaged and the hiss was left alone (measured âˆ’10.5 dB, i.e. it got *louder*). The
  correct form, `min(lowest, tracker + 0.05)`, follows a lower floor immediately and
  only creeps back up. With that fixed, Voice De-Noise measures **9.04 dB** of hiss
  reduction with the voice untouched.
- **The hiss test measured the wrong thing.** A raw, unwindowed band sum over a signal
  that contains a strong voice measures the voice's spectral leakage, not the hiss — and
  the module's 512-sample look-ahead delay changes that leakage pattern, which is why the
  test reported the hiss getting 10.5 dB *louder*. The test now measures a band **noise
  floor** (a low percentile of the short-time frame energies), which is both immune to
  leakage and the quantity a listener actually hears.

### 3.3 The interface

The UI is a dark, dense, professional audio tool — a three-row shell with a module browser
on the left, the composite view in the middle, and the module inspector on the right.

- **Module browser** — grouped by category, collapsible, searchable with match highlighting,
  an `ADV` badge on the edition-gated modules, and a capability dot (analysis / learn).
- **Composite view** — a spectrogram over a waveform, sharing a time axis. Frequency ticks
  and the decibel legend are overlay panels so they stay readable over bright content.
  Pan never rebuilds the raster (one blit), so it holds frame rate.
- **Module inspector** — power button, preset chip with a dirty marker, Learn, Compare, a
  `…` menu, then the controls grouped by parameter group: rotary knobs (drag, shift for fine,
  double-click to reset), toggles, enum selects, and an analysis footer for the
  "output only" modes.
- **Chain strip** — one chip per step in signal order with a power toggle, a drag handle for
  reordering, and a remove button.
- **Status bar** — cursor time, selection, latency in samples, level in dBFS, engine state.


### 3.4 What the competitive study changed, and how it was measured

The product was measured against two commercial plug-ins over several months.
The measurements live in a workspace outside this repository; **nothing in this
repository names either product**, and a hygiene gate checks that.  What came
back was not a list of features to copy but a list of **rules with numbers**.

| What | The reference | Ours | Where the measurement is |
|---|---|---|---|
| the round trip through the engine | −3.0 dB | **+311.2 dB** (4.4e−16) | `docs/SYNTHESIS_REFERENCE.md` |
| the same answer at every block size | 0.87 apart | **bit identical** | `qa/benchmark/reference_synthesis.py` |
| the declared delay against the real one | 1470 declared, **2750 real** (1280 short) | **exact** | `qa/benchmark/reference_latency.py` |
| the delay against the block size | 1470 → 5310 samples | **constant** | `qa/benchmark/reference.py` |
| feedback in the output path | a one-block comb at unity gain | **none** | `qa/benchmark/reference_synthesis.py` |
| two runs of the same input | 0.7 to 1.7 % apart | **bit identical** | `qa/benchmark/reference_bridge.py` |
| the bypass | sample exact, but **30 to 47 ms late** | **bit exact, no delay** | `qa/benchmark/reference_restoration.py` |
| the defaults on a clean signal | every sample changes (−63.84 dB), a −103 dBFS floor | **bit identical** | `qa/benchmark/reference_restoration.py` |
| the noise on a bed | 24.5 dB | **28.9 to 35.0 dB** | `qa/benchmark/reference_bridge.py` |
| the mains hum, with the tone kept | 0.07 dB (no setting keeps both) | **57.98 dB, the tone moves 0.04 dB** | `qa/benchmark/reference_bridge.py` |
| the voice under hiss | 8.98 dB, 0.04 dB of voice | **13.41 dB, 0.0013 dB of voice** | `qa/benchmark/reference_voice.py` |
| the applied gain against the level of the input | 0.05 dB of movement | **0.01 dB** | `qa/benchmark/reference_voice.py` |
| the settling after a level step | 0 to 21.3 ms | **0.0 ms** | `qa/benchmark/reference_voice.py` |
| the gain curve's variance | 0.00 to 0.43 dB² | **0.018 dB²** | `qa/benchmark/reference_voice.py` |

Two differences are the other way, and the tree says so:

- **a bed with no wanted content**: the reference removes 22.69 dB of it, we
  remove 12.19 to 13.56 dB — a separator can throw a bed away and a noise
  remover cannot.  The rule we took is *"on a bed that carries no wanted
  content, let the estimator go to the floor of the bed"*.
- **spatially separated material**: with the wanted content in the middle and
  the noise on the side, the reference removes 83.36 dB — not by filtering but
  by folding the stereo image to mono and discarding the side.  We do not do
  that, and we do not want to: on a clean speech the reference takes 24.80 dB
  out of the speech's own air and its gain curve reaches 3.79 dB of roughness
  against our 1.0 dB budget.

The one gap the study found that was ours — the spatial separation — is closed by
the **spatial suppressor** (`aurcore/dsp/spatial.py`): on the corpus case where the
wanted content sits in the middle and the unwanted content in the sides, it removes
**83.36 dB** of the side content (the analyser reads the floor) while the wanted
content moves **0.072 dB**, and on the same material with the channels made
identical it is **transparent (0.000 dB)** — so the spatial and the spectral
techniques are complementary.  The competitor reaches the same removal by **folding
the stereo image to mono**, which is why it takes 24.80 dB out of the air of a clean
speech; the suppressor's rule is that the gain may only be a real number in [0, 1] —
it attenuates and never invents, so it cannot carry a separator's roughness (0.000 dB
on the case where the curve is finite).  The cost is **3.00 transform equivalents per
64-sample block, 1.9 % of the budget**, flat in the block size.

⇒ **The study's verdict, in one line: the engine is ours to keep — the
resynthesis is exact, the block size does not matter, the delay is honest and
constant, the answer is deterministic — and the one real gap is the model.**

#### 3.4.1 The model, and the verdict that changed

The reference ships one learned artefact per restoration mode: 16 to 45 MB
each, base64 text in the data section that decodes to plain float32, a
one-dimensional convolution stack with gated residual blocks and a recurrent
gate, evaluated **per sample**.

The study's first verdict was that a model was **not affordable**: 5.5 to
23 ms per 512 frames, forty to a hundred and sixty times the budget of a
64-sample block.  That verdict was **wrong about the model and right about
the granularity**: a model that runs per sample cannot run in a real time
plug-in, and a model that runs **per frame** costs 19 to 59 µs per 64-sample
block — 4 % of the budget by the arithmetic, and **0.7 to 1.2 % measured** on
the four frame-rate models this repository ships.

⇒ **The per sample model is the answer to the part of the verdict that was
right.**  `models/voice_sample_mask.aurm` (6,520 parameters, 31 kB) reads a
window of **128 samples** and is asked **once per sample** — 48 000 times a
second — and it costs **0.795 times** the real time.  It fits inside the
realtime budget of a 64-sample block as well (72.5 % of it), and it is held
to the **offline budget** instead (§ `docs/MODEL_COST.md` §1b): a render may
take at most twice the length of the audio.

⇒ What made it affordable is not a smaller model but a **different path**:
the frames of a file are evaluated **in one pass** instead of one Python
call per frame, and the accumulation order of the batch is the one of the
frame-by-frame path, so **the output of every model is bit for bit the output
it produced before**.  The four frame-rate models got eight to ten times
cheaper by the same change.

⇒ And the per sample model is **better than the frame-rate model on real
material**: on a damaged phone memo (a voice over a mains hum, a rumble, a
hiss and a DC offset) it takes 4.5 dB more of the hum, 5.8 dB more of the
hiss and 3.0 dB more of the rumble away, and costs 0.81 dB of the voice where
the frame-rate model *gains* 2.49 dB of it.  It runs as the **learned stage**
of a file render (`POST /api/render` with `"learned": "voice_sample_mask"`),
deciding on the mix of the channels so the image does not move.

⇒ **The memory over the material now exists.**  The manifest knows a second
kind of network — a stack of **causal** convolutions over the time axis — and
the engine runs it frame by frame and in one pass with the **same answer to
the bit**.  `models/voice_sample_memory.aurm` (7,008 parameters, 32 kB) is
asked once per sample like the per sample model, and it reads the **11.4 ms**
before a sample.  It costs **1.183 times** the real time in mono and **1.57
times** it in stereo, and it takes **4.4 MB** more memory; the peak of a
render is the frames of the whole file, not the convolution.  It does **not**
fit inside the realtime budget of a block (102.3 % of it), so it is held to
the offline budget like the per sample model before it.  On the same memo it
takes more of every artefact than the frame-rate model and less than the per
sample model, and it keeps 1.22 dB more of the voice than the per sample
model does — a third point on the same trade-off, not a better one.  The
measurement that tells the *memory* apart from the *material* it was taught
is in `docs/MODEL_COST.md` §4c.

⇒ The product has a model mechanism (`aurcore/models/`): a file with weights
and a manifest, the manifest naming the model, its version, the epoch, the
date it was trained, the variant, the problem it solves, the input it
expects, the output it produces, **the context it needs (which is its
latency)**, the scope of its state and its own checksum.  A model that does
not match the engine is **refused with a message that says which field is
wrong**, and a module with no model behaves exactly as it did before.

The first model we trained ourselves (`models/voice_denoise_mask.aurm`, 18,608
parameters, 79 kB) is **deterministic to the bit**: two independent training
runs produce the same checksum.  On held-out material it removes **27.47 dB**
of hiss against the hand written module's 17.85 dB, moves the voice by
0.01 dB against 0.13 dB — and **chatters 0.94 dB against 0.03 dB**, and fails
on material that is not a voice (a lone tone loses 7 to 21 dB).  Its limits
are written down in `docs/MODEL_REFERENCE.md`; a model is an **offline**
capability, and the real time path is the hand written DSP.

#### 3.4.2 The processing mode, and the two configurations

A module that cannot do its best job inside a deadline can usually do it
inside a bigger one.  The product therefore has **two processing modes**:

| | realtime | offline |
|---|---|---|
| what it is | the default; every module inside the budget of a 64-sample block | the module's best configuration, at the cost of a longer look-ahead |
| the voice de-noise | a 2048-sample window, 2047 samples of delay | a 4096-sample window, 4095 samples of delay — **15.92 dB of hiss removed against 13.41**, the chatter **0.169 against 0.314 dB** |
| the spectral de-noise | a 720-sample window, 1440 samples of delay | a 1080-sample window, 2160 samples of delay — **a wanted partial moves 2.73 dB against 9.16** |
| the repair assistant | a 2048-sample frame | an 8192-sample frame |
| the cost of the mode itself | **4.3 µs for a 48-module chain (0.32 % of the budget)** | the same |
| the proof that the real time path did not change | **the two paths are bit identical** (the largest difference 0.000e+00) | — |

The mode is a **parameter of the module**, so the host sets it like any other
parameter and the state carries it; the server decides it (a preview is
realtime, a file render is offline) and the plug-in's reported latency
follows it.

#### 3.4.3 The MCP server

`aurmcp/` is an **MCP server** so that an AI agent can operate the product:
it speaks JSON-RPC 2.0 over stdio, written against the **standard library
only** — the product gains no dependency for it.  Eleven tools: list the
modules, describe one, set the chain, read it back, set a parameter, switch
the processing mode, render, measure, list the corpus cases, list the models
and run the benchmark; and the documents of the tree as resources.  Every
tool's description says what it does **and what it does not do**, every
schema is complete, and a refused call answers with the **names that would
have worked**.

### 3.4.4 What the study gave us, and what it did not

Ten techniques were taken.  Each one is a rule with a number, and each one is
held by a gate:

| The technique | What it changed | The number |
|---|---|---|
| a **relative threshold** — decide on a ratio, not on an absolute level | the voice de-noise, the breath control, the dialogue isolate | the gain moved **14.67 dB → 0.01 dB** when the input's level moved 18 dB; the settling **4736 ms → 0.0 ms** |
| a **frame-level inference** | the model mechanism | a model that costs 45 times the budget per sample costs **4 to 7 %** per frame |
| a **spatial suppressor** | the spatial suppressor module | **83.36 dB** of the side content, with the voice moving **0.072 dB**, at **1.9 %** of the budget |
| a **weighted overlap-add filter bank** | the linear-phase path of the de-hum | **5515 µs → 63 µs** (4 % of the budget), the two paths agreeing to **3.3e-16** |
| a **floor rule** — on a bed with no wanted content, the estimate is the bed | the voice de-noise | the bed goes from 16.94 dB to **40.00 dB** of removal, and the voice cases stay bit identical |
| a **removed-signal output** | six modules | the "what was taken away" tap, exact at both ends of its travel |
| a **bypass that is a copy** | every plug-in | the output is bit identical to the input, with no delay |
| a **stable parameter name** | the documents | a session that names an old parameter still loads |
| a **parameter ramp** | the core | the step at a change **0.079989 → 0.006480** |
| a **surface** — the log law, the taper, the units, the help sentences, the program list | the whole table | a frequency control is **15.9 times** finer at 100 Hz; **295 of 295** parameters carry a sentence; **51 of 51** plug-ins publish a bypass |

Four things were **deliberately not taken**, and the documents say why:

- **the learned separation itself.**  Their model costs 5.5 to 23 ms per 512
  frames — forty to a hundred and sixty times the budget of a 64-sample
  block.  Ours is a frame-level model and it fits; theirs is a per-sample
  model and it does not.  Copying the architecture would buy nothing the
  frame-level design does not already have.
- **the virtualiser.**  Closing that gap means shipping code we cannot read.
- **the per-sample inference.**  The same network, amortised over a frame,
  costs a fraction of the same network run per sample.
- **the "fold the stereo image to mono" trick.**  It is why their 83.36 dB of
  "removal" is not a removal: it takes 24.80 dB out of a clean speech's own
  air, and its gain curve reaches 3.79 dB of roughness against our 1.0 dB
  budget.

And one number of theirs was **not a measurement of the product** but of the
instrument: the 83.36 dB came from the stereo image being discarded.  The
study's own rule — a number that is a measurement floor is marked as one — now
lives in the constants files, so a reader cannot meet it without the silence
that produced it.

---

## 4. Milestone map

The alpha is the first gate of the full plan; the plan itself lives in `SPEC/08`.

| Gate | Scope | State |
|---|---|---|
| **G0** Foundations | repo, CI, contracts, canonical parameter table, codegen | **done** |
| **G1** DSP primitives | FFT, windows, STFT, resampler, filters, loudness, dither, phase vocoder | **done** |
| **G2** Core services | registry, chain, serialisation, migrations, batch, streaming | **done** |
| **G3** First module | De-click end to end (detection → repair → preset → golden test) | **done** |
| **G4** Plug-in ecosystem | template adapter, identity stamping, VST3 target | **done** — the C++ adapter is written straight against the format's SDK and the family of 50 binaries loads in an independent host |
| **G5** Classic repair batch | De-hum, De-ess, De-plosive, De-rustle, De-wind, De-bleed, De-reverb, De-crackle, De-clip, Deconstruct | **done** |
| **G6** Spectral De-noise | learned noise print, OM-LSA / Wiener, artifact control | **done** |
| **G7** Voice De-noise | 64-band psychoacoustic gate, adaptive learning | **done** — the gain no longer follows the level of the input (0.01 dB of movement over 18 dB) and the model path is in place |
| **G8** Separation | Music Rebalance, Dialogue Isolate, Band Restoration, Voice Reverb, Spatial Suppressor | **done** — the hand written path ships, the model seam is in place and six models are trained and shipped (four per frame, two per sample) |
| **G9** Advanced editing | Spectral Editor, Find Similar, Ambience Match, Repair Assistant | **done** (DSP approximations) |
| **G10** AAX adapter | needs a copy of the format's SDK at build time — **the build takes its path** | **built and validated against the format's own validator** |
| **G11** Batch & delivery | batch jobs, export formats, delivery presets | **done** |
| **G12** Licensing & hardening | entitlement model, edition gating, stress, installer | design in `SPEC/07` |

### Known risks

- **The format's SDK is licensed by a vendor and cannot be assumed.** The architecture is
  already format-agnostic, so AAX is additive: the product ships on VST3 (and CLAP/AU in
  the production track) and AAX is added when the registration and the signature are in
  place. A copy of the SDK is a download, not a blocker.
- **The separation models are third-party weights.** A DSP approximation ships now with a
  clean seam where a model drops in; the replacement models must be trained on
  permissively-licensed data, and every model needs a licence entry.
- **Parity is functional, not sample-exact.** Objective metrics per module family are the
  acceptance criterion.

---

## 5. Repository map

```
auralis/
  aurcore/           the DSP core: params, registry, api, dsp/, modules/, serial/, schema/
  aurserver/         FastAPI: app, audio, assets, presets, ws, batch
  aurui/             the interface: index.html, css/tokens.css, css/app.css, js/*.js
  schemas/           canonical tables (aurcore/schema/params.tsv is the single source)
  plugins/           identity layer, VST3 template, AAX design notes
  tools/             codegen, validators, benchmark, QA harnesses
  tests/             pytest suite, golden fixtures
  qa/                end-to-end acceptance, CSS coverage, screenshots, runbook
  docs/              architecture, UI, modules, roadmap, design decisions
  scripts/           dev launchers
```

`qa/RUNBOOK.md` is the operating procedure: how to run the functional pass, how to drive the
interface in a browser for the visual pass, and the nine acceptance gates.


