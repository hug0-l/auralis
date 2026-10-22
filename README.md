<p align="center">
  <img src="assets/logo.svg" alt="Auralis — audio restoration workbench" width="340" />
</p>

# Auralis

**An offline audio restoration workbench.** One DSP core, a local web interface, and
plug-in adapters built from a single template. Audio never leaves the machine; there is
no account, no upload and no telemetry.

> **Status: alpha (`0.1.0a0`, `pyproject.toml`).** The DSP core, the canonical parameter
> table, the HTTP/WebSocket server, the web interface, the undo history, the session
> document and the plug-in C ABI are in the tree and covered by tests. The VST3 adapter is
> written straight against the SDK and the family of **51 plug-in binaries** is built; an
> independent host loads every one of them, and a **second, foreign** host — one that
> strips the environment and runs from a temporary directory — proves they work outside
> this repository. The AAX adapter is **built against the format's own SDK and passes the
> format's own conformance validator** (11 of 11) — see [Plug-ins](#plug-ins).
>
> Three capabilities are new since the last pass: a **processing mode** that lets every
> module use its best configuration when the host is not real time
> ([below](#the-two-processing-modes)), a **library of learned models** with a versioned,
> self-describing format ([below](#learned-models)), and an **MCP server** that lets an
> AI agent drive the product itself ([below](#the-mcp-server)).

Every number in this document can be checked against the repository. Where a count
appears, the command that reproduces it is given next to it.

<!-- screenshot placeholder: assets/screenshot.png, 1280x800, to be captured once the
     module browser, composite view and inspector are populated. -->

---

## What Auralis is

Auralis is a chain-based restoration tool. You load a file, look at it as a waveform and
a spectrogram, stack restoration modules into a chain, audition the result, and export a
WAV. Every module in the chain is the same object in every host: the browser, the test
suite and the plug-in adapters all drive the identical DSP code.

Three properties shape everything else:

* **Offline and deterministic.** No network at runtime, no telemetry, no model downloads.
  The same input produces the same output on every machine.
* **One source of truth for parameters.** Every module's parameters are declared once, in
  [`aurcore/schema/params.tsv`](aurcore/schema/params.tsv). The runtime, the API, the UI
  controls, the C header the plug-in adapters compile against and the documentation are
  all generated from it — a control cannot exist without a parameter behind it.
* **Replaceable surface.** The core is a plain library. The web UI is a client of a local
  API, not the owner of the DSP. The plug-in adapter reaches the same core through a
  narrow C ABI.

---

## Features

**Analysis**

* Waveform (peak envelope) and spectrogram views, log or linear frequency scale.
* Frequency-band and time-range selection, with a live readout of cursor time, frequency
  and level.
* A measurement service behind the inspector's *Analysis* tab — spectrum, waveform
  statistics, loudness (integrated / short-term / momentary, LRA, true peak), inter-channel
  correlation and a peak histogram (`aurcore/analysis.py`).
* A/B comparison of two chains over the same file.

**Restoration** — the catalogue is the canonical table, not a hand-written list:

| | Count | How to check |
|---|---|---|
| Modules | **51** | `python tools/validate_schema.py` → *unique module codes* |
| Parameters | **295** | `python tools/validate_schema.py` → *unique parameter names* |
| Parameters with a unit | 109 | the `unit` column of the canonical table |
| Parameters with a help sentence | **295 of 295** | the `help` column |
| Learned models shipped | **4** | `models/*.aurm`, listed by `python -m aurcore.models.bench` |

```bash
python tools/validate_schema.py      # prints both counts and checks them against the tree
```

The four categories:

* *Repair* (215) — de-click, de-crackle, de-clip, de-hum, de-ess, de-plosive, de-reverb,
  de-rustle, de-wind, de-bleed, breath control, mouth de-click, voice de-noise, voice
  reverb, spectral de-noise, spectral editor, spectral repair, spectral recovery, guitar
  de-noise, deconstruct, dialogue isolate, music rebalance, ambience match, band
  restoration, spatial suppressor, repair assistant, center extract, azimuth, wow &
  flutter.
* *Utility* (71) — leveler, loudness control, loudness optimize, EQ, EQ match, gain, fade,
  normalize, phase, mixing, dither, resample, time & pitch, variable pitch, variable
  time, interpolate, signal generator, streaming preview.
* *Measurement* (6) — find similar, clip classifier.
* *System* (3) — connect, monitor.

Thirty-nine of them are *standard* and twelve *advanced*.

**Workflow**

* A searchable, category-grouped module browser; modules are added to a chain with one
  click.
* A chain strip in signal order with per-module bypass and drag-to-reorder.
* Module panels whose every control is generated from the canonical parameter table:
  knobs for continuous values, toggles for booleans, selects for enums, sliders for
  percentages.
* An undo/redo history over every chain edit — the history folds a knob drag into a
  single step (`aurcore/undo.py`, `aurui/js/session.js`). The controller is loaded by the
  shell, its toolbar is mounted in the top bar, and `Ctrl+Z` / `Ctrl+Shift+Z` are bound.
* Session documents that carry the audio reference, the chain, the view state, the markers
  and a summary of the history (`aurcore/document.py`). They reach the client over
  `POST /api/session/save`, `POST /api/session/load` and `GET /api/session/list`.
* Presets, chain files and session documents in a self-describing JSON format that stores
  types explicitly and survives schema changes.
* Learn buttons on the modules that support noise or profile capture.
* A keyboard map that is data, not code: the help overlay (`F1`) renders the same table,
  so it cannot drift away from the behaviour.
* A localised interface with a language switch in the top bar and a remembered choice.
  The catalogue ships English, 简体中文 and Deutsch — 401 keys each — and every interface
  module now renders its text through it.
* Preview of a short rendered buffer, then a full render and WAV export.
* Batch processing over a folder with per-file delivery targets
  (`aurserver/batch.py`, `aurserver/batch_formats.py`).

---

## The two processing modes

A module that cannot do its best job inside a deadline can usually do it inside a bigger
one.  Every module therefore has **two configurations**, and the host says which one it
is:

| | `realtime` | `offline` |
|---|---|---|
| what it is | the default: every module inside the budget of a 64-sample block (1333 us) | the module's best configuration, at the cost of a longer look-ahead |
| the voice de-noise | a 2048-sample window, 2047 samples of delay | a 4096-sample window, 4095 samples of delay — **15.92 dB of hiss removed against 13.41**, the chatter **0.169 against 0.314 dB** |
| the spectral de-noise | a 720-sample window, 1440 samples of delay | a 1080-sample window, 2160 samples of delay — **a wanted partial moves 2.73 dB against 9.16** |
| the repair assistant | a 2048-sample frame | an 8192-sample frame |
| the cost of the mode itself | **4.3 us for a 48-module chain (0.32 % of the budget)** | the same |
| the proof that the real time path did not change | **the two paths are bit identical** (the largest difference 0.000e+00) | — |

The mode is a **parameter of the module**, so the host sets it like any other parameter
and the state carries it; the server decides it (a preview is real time, a file render
is not) and the plug-in's reported latency follows it.  `docs/MODEL_COST.md` and
`docs/ALPHA_STATUS.md` carry the measurements.

### The preview and the file are two paths

The preview runs a **fast profile** — the cheap configuration of the cost parameters —
and the file render runs the full one.  The interface says which path is playing, which
parameters the fast path moved (`Quality B → A`, `Multi Resolution On → Off`) and what
the latency of the path that plays is; the user can ask for the full path in the
preview.  The file render is **bit identical** with and without the profile machinery.

---

## Learned models

A model is **one file** with two parts: the weights and a manifest.  The manifest names
the model, its version, the epoch it was trained for, the date, the variant, **what
problem it solves**, the input it expects, the output it produces, **the context it
needs (which is its latency)**, the scope of its state and its own checksum.  A model
that does not match the engine is **refused with a message that says which field is
wrong**.

```bash
python -m aurcore.models.bench        # the cost of every model
python -m aurcore.models.train --all  # rebuild the library, reproducibly
```

The six models that ship are ours — trained on our own synthesised material, small
(31 to 80 kB), and **deterministic to the bit**: two independent training runs produce
the same checksum.  A model belongs to the **offline** path, and the real time path
never loads one — the cost of the four frame-rate models is 0.7 to 1.2 % of the block
budget, the **per sample** model (`voice_sample_mask`: a window of 128 samples and a hop
of one sample, so the gain is decided 48 000 times a second) takes 72.5 % of it and is
held to the **offline budget** instead — a render may take at most twice the length of
the audio, and it takes 0.795 times it.  A second per sample model
(`voice_sample_memory`) reads the **11 ms before** a sample through a stack of causal
convolutions, and it takes 1.183 times the real time.  The real time path of the voice
de-noise is **bit identical** whether a model is present or not.

A model runs in two places: inside a module, through the seam, and as the **learned
stage** of a file render — `POST /api/render` with `"learned": "voice_sample_mask"`
runs it over the finished file, deciding on the mix of the channels so the image does
not move.  On a damaged phone memo that stage takes 4.3 dB more of the hum, 2.3 dB
more of the hiss and 1.5 dB more of the rumble away, and gives 1.3 dB of the voice
back.

`docs/MODEL_REFERENCE.md` carries what a model is and, just as importantly, **what it is
not**: our first model removes more hiss than the hand written module (29.39 against
17.85 dB) and protects the voice better, but it chatters more (0.86 against 0.03 dB) and
**it fails on material that is not a voice**.

---

## The MCP server

`aurmcp/` is an **MCP server**: it speaks JSON-RPC 2.0 over stdio, written against the
**standard library only** — the product gains no dependency for it.  Eleven tools let an
agent operate the software: list the modules, describe one, set the chain, read it back,
set a parameter, switch the processing mode, render, measure, list the corpus cases,
list the models, run the benchmark.  Nine resources let it read the product's
documentation, the parameter table and the corpus.

```bash
python -m aurmcp            # the server, on stdio
```

Every tool's description says what it does **and what it does not do**; every schema is
complete; a refused call answers with the **names that would have worked**.  The
measurement a tool reports is the product's own — the same instrument the benchmark
uses, and the same numbers.


---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  aurui/        web UI — vanilla HTML / CSS / JS, no build step                │
│                module browser · composite view · inspector · chain strip     │
│                session history · preset library · batch panel · keyboard map  │
└────────────────────────────────┬─────────────────────────────────────────────┘
                                  │ HTTP + JSON  /  WebSocket (binary frames)
┌────────────────────────────────▼─────────────────────────────────────────────┐
│  aurserver/   transport and orchestration only (FastAPI)                    │
│                routes · asset store · render jobs · preset library · batch    │
└────────────────────────────────┬─────────────────────────────────────────────┘
                                  │ aurcore public API — Engine, ModuleHandle
┌────────────────────────────────▼─────────────────────────────────────────────┐
│  aurcore/     format-agnostic DSP core (numpy only, scipy optional)         │
│                api.py · params.py · registry.py · modules/ · dsp/ · serial/  │
│                analysis.py · undo.py · document.py · abi_bridge.py           │
│                schema/params.tsv — the canonical parameter table            │
└────────────────────────────────┬─────────────────────────────────────────────┘
                                  │ narrow C ABI (plugins/vst3/aurcore_c_api.h)
┌────────────────────────────────▼─────────────────────────────────────────────┐
│  aurmcp/      an MCP server: an AI agent drives the product through it     │
│               (JSON-RPC over stdio, standard library only)                  │
│  models/      the library of learned models, one file per model              │
└────────────────────────────────┬─────────────────────────────────────────────┘
                                   │ the same core, the same instruments
┌────────────────────────────────▼─────────────────────────────────────────────┐
│  plugins/     one adapter template per plug-in format; the module a binary  │
│               implements is stamped into it at build time (identity.py)      │
│               vst3native/ · vst3/ · abi/ · aax/                             │
└────────────────────────────────┬─────────────────────────────────────────────┘
                                  │ loaded by
┌────────────────────────────────▼─────────────────────────────────────────────┐
│  qa/vst3host/ an independent host, written against the SDK, that loads a    │
│               built plug-in the way a DAW does and reports what it saw      │
└──────────────────────────────────────────────────────────────────────────────┘
```

The dependency rule is one-way: `aurcore` never imports `aurserver`, `fastapi` or
anything from `aurui`, and it never touches the file system or the network. The plug-in
layer reaches the core only through the C ABI, never through the Python API. Details in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Quick start

Requirements: Python 3.11 or newer, and the dependencies in `requirements.txt`. The UI has
no build step — there is no bundler, no node and no lockfile.

```bash
git clone <this repository> auralis
cd auralis

python -m venv .venv
# Linux / macOS
source .venv/bin/activate
# Windows (PowerShell)
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

Start the server from the repository root:

```bash
# Linux / macOS
PYTHONPATH=. python -m uvicorn aurserver.app:app --port 8731

# Windows (PowerShell)
$env:PYTHONPATH = "."
python -m uvicorn aurserver.app:app --port 8731
```

Then open <http://127.0.0.1:8731/> in a browser.

`PYTHONPATH=.` is what makes `aurcore` and `aurserver` importable without an editable
install. If you prefer to install the package instead, `pip install -e .` puts the
`auralis-server` console script on your path:

```bash
pip install -e .            # runtime
pip install -e ".[dev]"     # runtime + pytest, ruff, black
auralis-server --port 8731
```

There are also launcher scripts that set `PYTHONPATH` and add auto-reload for you:

```bash
# Linux / macOS
chmod +x scripts/run_dev.sh      # once, if the file is not already executable
./scripts/run_dev.sh

# Windows (PowerShell 7 or Windows PowerShell 5.1)
pwsh ./scripts/run_dev.ps1
```

Both bind to **127.0.0.1 only** and listen on port 8731. Override the port with
`AURALIS_PORT` (or `-Port` on PowerShell).

Check that it works:

```bash
python -m pytest tests -q          # the unit suite
python qa/verify_app.py            # 30 end-to-end checks against a real server
python tools/validate_schema.py    # the canonical table against the tree
python tools/codegen_registry.py --check   # the generated artefacts against the generator
python tools/hygiene_check.py      # the naming rule over the whole tree
python tools/lint_check.py         # the style gate
```

---

## Modules

The catalogue is generated from [`aurcore/schema/params.tsv`](aurcore/schema/params.tsv),
the single source of truth. `code` is the stable identifier used by presets, chains and
plug-in parameters; `element` names the runtime implementation.

| Module | Code | Category | Edition | Element | Parameters |
|---|---|---|---|---|---|
| Ambience Match | `AUR_AMBIENCEMATCH` | Repair | advanced | `MatchAmbience` | 6 |
| Azimuth | `AUR_AZIMUTH` | Repair | advanced | `ChannelOperations` | 3 |
| Band Restoration | `AUR_BANDRESTORATION` | Repair | standard | `BandRestoration` | 25 |
| Breath Control | `AUR_BREATH` | Repair | standard | `DeBreath` | 6 |
| Center Extract | `AUR_CENTEREXTRACT` | Repair | advanced | `ChannelOperations` | 4 |
| Clip Classifier | `AUR_CLIPCLASSIFIER` | Measurement | standard | `ClipClassifier` | 5 |
| Connect | `AUR_CONNECT` | System | standard | `Connect` | 2 |
| De-Bleed | `AUR_DEBLEED` | Repair | standard | `DeBleed` | 2 |
| De-Click | `AUR_DECLICK` | Repair | standard | `DeClicker` | 6 |
| De-Clip | `AUR_DECLIP` | Repair | standard | `Declipper` | 5 |
| De-Crackle | `AUR_DECRACKLE` | Repair | standard | `DeClicker` | 4 |
| De-Ess | `AUR_DEESS` | Repair | standard | `DeEsser` | 8 |
| De-Hum | `AUR_DEHUM` | Repair | standard | `HumRemoval` | 20 |
| De-Plosive | `AUR_DEPLOSIVE` | Repair | standard | `DePlosive` | 3 |
| De-Reverb | `AUR_DEREVERB` | Repair | standard | `Dereverb` | 10 |
| De-Rustle | `AUR_DERUSTLE` | Repair | advanced | `DeRustle` | 3 |
| De-Wind | `AUR_DEWIND` | Repair | advanced | `DeWind` | 5 |
| Deconstruct | `AUR_DECONSTRUCT` | Repair | advanced | `Deconstruct` | 6 |
| Dialogue Isolate | `AUR_DIALOGUEISOLATE` | Repair | advanced | `DialogueExtract` | 5 |
| Dither | `AUR_DITHER` | Utility | standard | `Dither` | 4 |
| EQ | `AUR_EQ` | Utility | standard | `ParametricEQ` | 14 |
| EQ Match | `AUR_EQMATCH` | Utility | advanced | `MatchEQ` | 1 |
| Fade | `AUR_FADE` | Utility | standard | `GainStage` | 2 |
| Find Similar | `AUR_FINDSIMILAR` | Measurement | standard | `FindSimilar` | 1 |
| Gain | `AUR_GAIN` | Utility | standard | `GainStage` | 1 |
| Guitar De-Noise | `AUR_GUITARDENOISE` | Repair | standard | `GuitarDenoiser` | 8 |
| Interpolate | `AUR_INTERPOLATE` | Utility | standard | `Interpolate` | 1 |
| Leveler | `AUR_LEVELER` | Utility | advanced | `Leveler` | 6 |
| Loudness Control | `AUR_LOUDNESS` | Utility | standard | `Loudness` | 6 |
| Loudness Optimize | `AUR_LOUDNESSOPT` | Utility | standard | `LoudnessOptimize` | 4 |
| Mixing | `AUR_MIXING` | Utility | standard | `ChannelOperations` | 2 |
| Monitor | `AUR_MONITOR` | System | standard | `Monitor` | 1 |
| Mouth De-Click | `AUR_MOUTHDECLICK` | Repair | standard | `MouthDeclick` | 4 |
| Music Rebalance | `AUR_MUSICREBALANCE` | Repair | standard | `MusicSourceSeparate` | 9 |
| Normalize | `AUR_NORMALIZE` | Utility | standard | `GainStage` | 1 |
| Phase | `AUR_PHASE` | Utility | standard | `ChannelOperations` | 2 |
| Repair Assistant | `AUR_REPAIRASSISTANT` | Repair | standard | `RepairAssistant` | 8 |
| Resample | `AUR_RESAMPLE` | Utility | standard | `Resampler` | 4 |
| Signal Generator | `AUR_SIGNALGEN` | Utility | standard | `SignalGenerator` | 5 |
| Spatial Suppressor | `AUR_SPATIALSUPPRESSOR` | Repair | advanced | `ChannelOperations` | 2 |
| Spectral De-Noise | `AUR_SPECTRALDENOISE` | Repair | standard | `Denoiser` | 20 |
| Spectral Editor | `AUR_SPECTRALEDITOR` | Repair | standard | `SpectralEditor` | 7 |
| Spectral Recovery | `AUR_SPECTRALRECOVERY` | Repair | advanced | `SpectralRecovery` | 5 |
| Spectral Repair | `AUR_SPECTRALREPAIR` | Repair | standard | `SpectralRepair` | 7 |
| Streaming Preview | `AUR_STREAMPREVIEW` | Utility | standard | `StreamingPreview` | 4 |
| Time & Pitch | `AUR_TIMEPITCH` | Utility | standard | `TimeStretchPitchShift` | 7 |
| Variable Pitch | `AUR_VARIPITCH` | Utility | standard | `PitchContour` | 4 |
| Variable Time | `AUR_VARTIME` | Utility | standard | `VariableTime` | 3 |
| Voice De-Noise | `AUR_VOICEDENOISE` | Repair | standard | `DenoiserZL` | 14 |
| Voice Reverb | `AUR_VOICEREVERB` | Repair | standard | `Dereverb` | 6 |
| Wow & Flutter | `AUR_WOWFLUTTER` | Repair | advanced | `WowAndFlutter` | 4 |

Full parameter tables — type, default, range, unit, choices, group — are in
[`docs/MODULES.md`](docs/MODULES.md), which is generated from the same table:

```bash
python tools/codegen_registry.py     # regenerates the five artefacts listed below
```

The five generated artefacts, none of which is ever hand-edited:

| Artefact | What it is |
|---|---|
| `aurcore/schema/modules.json` | the static registry the server answers "what modules exist" with |
| `aurcore/schema/modules.schema.json` | a JSON Schema (draft 2020-12) for that registry |
| `aurcore/schema/generated.py` | the same table as Python, plus the parameter index the adapters use |
| `aurcore/schema/aur_modules.h` | the module table as a C header with stable parameter ids |
| `docs/MODULES.md` | the human-readable module reference |

Reading the tables:

* **Code** — the stable identifier (`AUR_DECLICK`). Presets, chain files, session
  documents and plug-in parameters are keyed on it. It does not change.
* **Element** — the runtime implementation (`DeClicker`). Several modules can share one
  element — `ChannelOperations` backs Phase, Mixing, Center Extract and Azimuth;
  `GainStage` backs Gain, Fade and Normalize. The element may change when an
  implementation is swapped; the code may not.
* **Edition** — `standard` modules are the fast, predictable ones; `advanced` modules are
  the heavier algorithms (separation, ambience matching, spectral recovery) and are
  expected to run as offline render jobs rather than on the streaming path.
* **Parameters** — a parameter's *name* is its identity; there are no numeric parameter
  ids in the core. Values are stored in natural units (dB, Hz, ms, %) and clamped to the
  declared range.
* **Output-only modules** — a module with an `Output … Only` parameter can render just the
  component it removes (hum, breath, ess, crackle), which is what the comparison view uses.
* **Learn** — a module with a `Learn` or `Enable Learning` parameter supports the Learn
  action: the server feeds it a sample of the source and it captures a profile into its
  state.

---

## Plug-ins

The core is host-agnostic. The adapter layer is written, and the VST3 family is built.

**The design.** There is **one adapter template per plug-in format**, and the module a
binary implements is decided at build time by stamping an identity record into a reserved
slot inside the binary. Adding a module to the canonical table therefore yields a plug-in
for every format, with no adapter work and no recompilation of the template. The
mechanism lives in [`plugins/identity.py`](plugins/identity.py) and is described in
[`plugins/README.md`](plugins/README.md).

| Piece | Where | State |
|---|---|---|
| The shipped VST3 adapter | `plugins/vst3native/AuralisVST3.{h,cpp}` | written **straight against the VST3 SDK**, no framework in between — one object answers the four interfaces the format asks for |
| The framework-based VST3 adapter | `plugins/vst3/AuralisPlugin.{h,cpp}` | written and built as well; kept as an alternative, nothing in it was touched |
| The identity record | `plugins/identity.py`, `plugins/identity.schema.json` | done, validated on the way in and on the way out |
| The build-time stamper | `plugins/vst3/build_identity.py`, `plugins/vst3native/CMakeLists.txt` | done — `header`, `stamp`, `stamp-all`, `manifest`, `verify`, `list` |
| The C ABI between adapter and core | `plugins/vst3/aurcore_c_api.h` | done, with a real implementation — six entry points and ten extensions |
| The C ABI implementation | `plugins/abi/aurcore_abi.cpp`, `plugins/abi/build_abi.py`, `aurcore/abi_bridge.py` | built and loadable; the conformance tests are in `tests/test_plugin_abi.py` |
| The independent host | `qa/vst3host/` | written against the SDK; loads a built plug-in, connects it, drives a parameter, processes audio and restores the state |
| AAX | `plugins/aax/AAX_ADAPTER_NOTES.md`, `docs/AAX_APPLICATION.md` | **built against the format's own SDK and validated by the format's own conformance validator: 11 of 11 tests pass**, the page tables ship as a resource in every bundle.  What is left is outside the tree: the effect ids have to be registered with the format's vendor, and the plug-in is unsigned. |

**VST3.** The adapter source is one template, but the VST3 format requires every plug-in
to have its own class id and class name, and the plug-in wrapper bakes both into the
binary at compile time. The build therefore compiles the same template once per module
with the module's product code and name taken from the generated identity record. The
*module* is still chosen by the stamped record.

The shipped adapter is `plugins/vst3native/`, and it is written **against the SDK rather
than on a framework**, because the framework and the SDK versions available do not line
up: the framework release that would have been used wants a 3.6-era SDK layout, the next
one wants a 3.7-era layout that no SDK tag provides, and the SDK in this repository is
3.8.1 in the split layout. The SDK's own helper classes all include a module that is not
part of that layout, so the adapter implements the four interfaces itself:

```
IPluginFactory    the class factory the host asks for first
IComponent        the bus layout and the state
IAudioProcessor   the audio path
IEditController   the parameter list and the parameter values
```

The build needs two things, and both are in the tree: **the VST3 SDK** (3.8.1, vendored
at `vendor/vst3sdk`) and **a library that implements the C ABI** (built by
`plugins/abi/build_abi.py`). It fails with an explanation when either is missing, rather
than producing a plug-in that does nothing.

```bash
# 1. build the core ABI library the adapter links against
python plugins/abi/build_abi.py          # -> plugins/abi/build/aurcore_abi.{lib,dll}

# 2. build the whole family, one module after another
python scripts/build_vst3.py             # -> build/vst3native/AUR_<CODE>/stamped/
```

One module on its own:

```bat
set PATH=%LOCALAPPDATA%\Python\pythoncore-3.15-64\Scripts;%PATH%
cmd /c "tools\msvc.bat cmake -S plugins\vst3native -B build\vst3native ^
          -G Ninja -DAURALIS_MODULE=AUR_DECLICK -DCMAKE_BUILD_TYPE=Release"
cmd /c "tools\msvc.bat cmake --build build\vst3native"
```

The result is **51 stamped plug-ins**, one per module, under
`build/vst3native/AUR_<CODE>/stamped/`. Read one back:

```bash
python plugins/vst3/build_identity.py verify \
  --binary "build/vst3native/AUR_DECLICK/stamped/Auralis De-Click.vst3/Auralis De-Click.vst3.dll"
```

**What it costs.** One bundle is **7.8 MB** with the runtime staged next to the plug-in: a
178 kB plug-in binary, a 174 kB core library and a 7.48 MB interpreter. A family of 48 is
therefore **48 × 7.8 MB ≈ 375 MB**. The interpreter is 96 % of it — the production track
is a native core behind the same C ABI, which
[`docs/ROADMAP.md`](docs/ROADMAP.md) §2.3 describes.

**That it works is checked by an independent host.** `qa/vst3host/` is a program written
against the SDK, not a test of the plug-in build: it loads the binary the plug-in build
produced, talks to it over the VST3 interfaces and reports what it saw.

```bash
python qa/vst3host/run_host.py "build\vst3native\AUR_DECLICK\stamped\Auralis De-Click.vst3"
```

It enumerates the classes, creates the component and the controller, connects them,
accepts the bus arrangement, drives a parameter and reads it back, processes a block of
audio, and saves and restores the state in both directions — a 655-byte state for De-Click.

**The gate over the whole family is red at the moment.** `tests/test_vst3_host.py` runs
the host against **every** module of the family, and the modules whose binary in
`build/` was built before the adapter's current revision fail it. The fix is one rebuild:

```bash
python scripts/build_vst3.py      # one module after another, ~17-40 s each
```

See [`docs/ROADMAP.md`](docs/ROADMAP.md) §2.7.

**AAX.** The format's SDK is a separate download, so the build is told where it is
(`python scripts\build_aax.py --sdk PATH`, or the `AURALIS_AAX_SDK_PATH` environment
variable); the copy in the tree is the fallback. The adapter is written against that
SDK, builds against it, and the built plug-in has been run through **the format's own
conformance validator**: the plug-in loads in the format's host, describes itself
correctly, and passes the description, parameter, load/unload, parameter-traversal and
page-table checks. What is still open is the registration of the effect ids and the
digital signature a production host wants — see
[`plugins/aax/README.md`](plugins/aax/README.md).

**What the adapter is not.** It holds no parameter data of its own. It reads the identity
record, maps the host's parameter ids onto core parameter names through the record's
`param_id_map`, and calls the core through the C ABI. Parameters are addressed **by
name** and values are in **natural units**; an enum value is its **choice string**; the
audio buffer is **planar**.

---

## Repository map

```
auralis/
  aurcore/            the DSP core — library, no UI, no network, no plug-in formats
    api.py            Engine facade: chain, process, serialize / deserialize
    params.py         ParamDesc / ParamStore — name-keyed, natural units
    registry.py       module registry and factory
    analysis.py       the measurement service behind the Analysis tab
    undo.py           the command stack behind undo / redo
    document.py       the session document format
    abi_bridge.py     the Python side of the plug-in C ABI
    dsp/              11 primitives: util, fft, windows, stft, filters, resample,
                      loudness, spectral, phase_vocoder, timepitch, dither
    modules/          one file per module; base.py is the contract
    serial/           typed_value.py, preset_json.py, chain_json.py, document.py, migrate.py
    schema/           params.tsv — the canonical parameter table — plus the generated artefacts
  aurserver/          HTTP/WS API (FastAPI) — orchestration only, no DSP
    app.py            the routes
    assets.py         the asset store
    audio.py          decode, encode, analyse
    presets.py        the preset library
    batch.py          the batch processor
    batch_formats.py  the delivery targets
    ws.py             the streaming preview socket
  aurui/              web UI — index.html, three stylesheets, fourteen ES modules
  plugins/            the plug-in adapter layer — identity.py, vst3native/, vst3/,
                      abi/, aax/
  vendor/             vendored build dependencies — the VST3 SDK 3.8.1 (vendor/vst3sdk
                      and vendor/VST3_SDK) and JUCE 6 / 7.0.9 / 8.0.4
  tools/              codegen, validators, benchmarks, hygiene gate
  tests/              pytest suite — 58 test modules
  qa/                 end-to-end acceptance harness, the accessibility audit, CSS
                      coverage, the independent VST3 host, the runbook
  corpus/             golden-audio corpus — 16 CC0 test clips, a manifest and a schema
  docs/               architecture, UI, modules, roadmap, decisions
  scripts/            run_dev.ps1, run_dev.sh, build_vst3.py, build_release.py
  packaging/          installer definition
  assets/             logo and media
  var/                runtime scratch — uploads and renders (git-ignored)
```

---

## Documentation

| Document | What it is |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | how the layers fit together and what each one may import |
| [`docs/ALPHA_STATUS.md`](docs/ALPHA_STATUS.md) | the state of the build, the numbers and the defects that were found and fixed |
| [`docs/BENCHMARK_NOTES.md`](docs/BENCHMARK_NOTES.md) | the benchmark: our modules against the reference numbers a study recorded |
| [`docs/RESTORATION_REFERENCE.md`](docs/RESTORATION_REFERENCE.md) | what the reference products do, in our vocabulary, with the numbers |
| [`docs/VOICE_HEAD_TO_HEAD.md`](docs/VOICE_HEAD_TO_HEAD.md) | the voice case, side by side, with the mechanism behind the difference |
| [`docs/MODEL_REFERENCE.md`](docs/MODEL_REFERENCE.md) | what a model is here, what it costs and what it is not |
| [`docs/MODEL_COST.md`](docs/MODEL_COST.md) | the cost of the models and the rule that keeps a bigger one honest |
| [`docs/MODELS.md`](docs/MODELS.md) | the model library: the manifest, the refusals and the five artefacts |
| [`docs/MASKING_LAW.md`](docs/MASKING_LAW.md) | how a module decides what is wanted and what is not |
| [`docs/EDGE_CONTRACT.md`](docs/EDGE_CONTRACT.md) | the rules at the edges of the material, and which of them are gates |
| [`docs/ADAPTIVE_BEHAVIOUR.md`](docs/ADAPTIVE_BEHAVIOUR.md) | the relative threshold, the re-estimate and the session state |
| [`docs/OPEN_DEFECTS.md`](docs/OPEN_DEFECTS.md) | the rules our own modules do not meet yet, with the number of each |
| [`docs/AAX_APPLICATION.md`](docs/AAX_APPLICATION.md) | the AAX package: the identifiers, the ids and the page tables |
| [`docs/AAX_PAGE_TABLES.md`](docs/AAX_PAGE_TABLES.md) | the page table of every module |
| [`docs/VST3_INTEGRATION.md`](docs/VST3_INTEGRATION.md) | the VST3 adapter, the identity record and the host's contract |
| [`docs/DESIGN_DECISIONS.md`](docs/DESIGN_DECISIONS.md) | the decisions that are not obvious from the code |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | the decision log |
| [`docs/RELEASE.md`](docs/RELEASE.md) | how the packages are built, verified and installed |
| [`docs/CI.md`](docs/CI.md) | how the checks run |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | the milestone map |
| [`docs/UI.md`](docs/UI.md) | the interface |
| [`docs/MODULES.md`](docs/MODULES.md) | the generated catalogue |

---

## The rules that matter

[`CONTRACT.md`](CONTRACT.md) is binding. The short version:

1. **Own names only.** No third-party product, vendor or trademark appears in source,
   comments, identifiers, UI strings, presets, tests, filenames, docs or metadata. Use
   generic parameter names — `Sensitivity`, `Threshold`, `Strength`, `Bandwidth`,
   `Reduction`, `Attack`, `Release`, `Quality`, `Crossover`. Module codes use the `AUR_`
   prefix; identifier prefixes are `aur_` / `Auralis` / `auralis`.
   *Checked by* `python tools/hygiene_check.py` and, for the table itself, by the
   *no forbidden trademark token* check in `python tools/validate_schema.py`.
2. **Do not fork the parameter table.** Declare a parameter in
   `aurcore/schema/params.tsv` and nowhere else. The runtime, the API, the UI, the C
   header and the docs are derived from it.
   *Checked by* `python tools/validate_schema.py` — the *live registry conformance* and
   *codegen drift* checks.
3. **Keep the core clean.** `aurcore` imports numpy (scipy only behind a numpy fallback).
   It never imports `aurserver`, `fastapi` or `aurui`, and never does file or network I/O.
4. **Realtime safety.** `Module.process()` does no I/O, no network, no unbounded loops, no
   growing caches, and never raises through the call boundary.
5. **Every parameter is visible.** If a module reacts to a parameter, the module exposes it
   through `ParamStore`. `aurcore.schema.validate_module` must report no differences for
   your module.
6. **Every module gets tests** — parameter presence, range clamping and a process smoke
   test, in `tests/test_modules_generic.py` and `tests/test_modules_specific.py` — plus a
   golden-audio case in `corpus/`.
7. **Public functions have docstrings.**
8. **Say loudly if you change a shared contract file**: `params.py`, `registry.py`,
   `modules/base.py`, `api.py`, `serial/typed_value.py`, `tokens.css`. Other contributors
   depend on them.
9. **The order of a module's parameters is part of the contract.** A parameter's id is its
   **position inside its module**, so the canonical table has to list a module's rows in
   the order the module declares them. If the two orders differ, the plug-in publishes one
   parameter at the id the host will drive and the automation lands on the wrong control.
   *Checked by* `python -m pytest tests/test_param_order.py -q`.
10. **A module's declared latency and tail must be the ones it has.** A module may
    declare **more** than it needs; it may never declare less. Neither the declaration nor
    the measured ring-out may move with the block size. *Checked by* the `tail` gate in
    `tests/test_tail.py` and by `qa/check_module_edges.py`.
11. **No module hands back a signal above the digital ceiling.** A repair module may only
    attenuate; a module that lifts makes room for itself; a module whose gain is the
    user's own is exempt, and the exemption is the range that parameter declares.
    *Checked by* `tests/test_module_robustness.py`.
12. **The budget is fixed, not measured.** A 64-sample block at 48 kHz may cost
    **1333 us**, which is **160.6 transforms** of 1024 points at **8.300 us** each. A
    budget that moves with the machine that measures it is an echo, not a budget, so a
    measurement is always reported next to the yardstick it was taken on. *Checked by*
    `qa/performance/budgets.py` and `tests/test_benchmark_cost.py`.
13. **A model declares the layout its weights were fitted on.** Two models of different
    layouts have the same weight shapes, so a manifest without the field is refused
    rather than read. *Checked by* `tests/test_models.py`.
14. **A rule is applied where the promise is made, and it must not disturb another
    rule.** The digital ceiling is a promise about what leaves the plug-in and what
    is written to a file, so it is applied at those two boundaries — not inside every
    module, where a rule that scales or clips a block changes the module's own answer
    and breaks the block-coherence rule. When you add a rule, say **where** it is
    applied and **which other rule it could break**; a rule that is applied per block
    is almost always wrong. *Checked by* `tests/test_ceiling.py`,
    `tests/test_module_robustness.py` and `tests/block_coherence.py`.
15. **A range has to run through the distribution it acts on, or the control is
    dead.** The transient repair scored a steady tone at 0.43, a half-millisecond
    burst at 10.0 and a click at 27.0, while the shared threshold range was 0.55 to
    3.0 — below the whole population, so **every setting gave the same output, bit
    for bit**. The same shape appeared three times: a threshold range under the
    scores it judges, a cost yardstick taken on another machine, and a delay search
    window shorter than the delays it has to find (that one failed *silently*).
    ⇒ **If two settings of a control give the same bytes, the control is dead; the
    range is the reason.** *Checked by* `tests/test_de_click.py` and the knob sweeps
    in the module's own test.
16. **A shortcut is not a different answer, and the offline path has a budget of its
    own.** The batched path evaluates the frames of a file in one pass instead of one
    interpreter call per frame; it keeps the accumulation order of the frame-by-frame
    path, so **the output is bit for bit the output the frame-by-frame path produced**.
    A path that is faster because it does less work is a bug; a path that is faster
    because it does the same work in fewer round trips is an optimisation. And a
    capability that does not fit the realtime budget of a block can still ship, as
    long as it is held to the **offline budget**: a render may take at most twice the
    length of the audio. *Checked by* `tests/test_models.py` (the batch is the same
    answer) and `tests/test_model_budget.py` (the offline budget is a number).

Definition of done: the code runs, the tests pass (or you state which fail and why), no
third-party trademark appears in anything you added, and public functions are documented.

---

## Contributing

1. Read [`CONTRACT.md`](CONTRACT.md). It is binding.
2. Declare the module or the parameter in `aurcore/schema/params.tsv`.
3. Implement it against `aurcore/modules/base.py`.
4. Add the module's parameter, range and process tests to `tests/test_modules_generic.py`
   and `tests/test_modules_specific.py`, and a golden-audio clip to `corpus/`.
5. Run the gates:

```bash
python scripts/run_gates.py        # every gate, in one run
```

Or one at a time:

```bash
python tools/validate_schema.py    # the table against the tree — must pass
python tools/codegen_registry.py --check   # the generated artefacts — must pass
python -m pytest tests -q          # the unit suite — must be green
python qa/verify_app.py            # 30 end-to-end checks against a real server
python qa/a11y/audit_a11y.py        # the accessibility audit of the effective interface
python qa/check_css_coverage.py     # every class the UI uses is styled
python tools/hygiene_check.py      # the naming rule over the whole tree
python tools/lint_check.py         # the style gate
```

And the ones that hold the properties this product promises:

```bash
python qa/check_module_edges.py    # every module over the edges of the material
python -m pytest tests/test_tail.py -q            # the declared latency and tail are real
python -m pytest tests/test_param_order.py -q     # the order of the parameters is the contract
python -m pytest tests/test_module_robustness.py -q  # no module hands back a signal over the ceiling
python -m pytest tests/test_block_coherence.py -q # one block and many blocks agree
python -m pytest tests/test_models.py -q          # a model declares the layout its weights were fitted on
python -m pytest tests/test_safe_defaults.py -q   # the bypass is exact and the defaults are transparent
python -m pytest tests/test_foreign_host.py -q    # the plug-in works in a host that is not ours
python -m pytest tests/test_mcp_tools.py -q       # the MCP server's tools and their refusals
python qa/benchmark/run_benchmark.py   # our modules against the recorded reference numbers
```

---

## Licence

Not yet declared. The alpha is unpublished and internal; the licence will be set before
the beta. Everything Auralis writes — presets, chains, session documents — is plain JSON
with an open schema, and that will not change.
