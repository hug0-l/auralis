# Auralis — Architecture

> Status: **alpha (`0.1.0a0`)**. This document describes what is in the tree today.
> Work that is designed but not built is marked *planned* and is listed in
> [`ROADMAP.md`](ROADMAP.md).
>
> Every count in this document is checkable. The command that reproduces it is in
> the same paragraph.
>
> The binding rules live in [`../CONTRACT.md`](../CONTRACT.md). Where this document and
> the contract disagree, the contract wins. The decisions behind the design, with the
> alternatives that were rejected, are in [`DECISIONS.md`](DECISIONS.md).

---

## 1. The shape of the system

Auralis is one DSP core with shells around it. The core knows nothing about how it is
reached — no HTTP, no plug-in format, no DOM. Every shell adapts the same engine.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  aurui/            web UI — vanilla HTML / CSS / JS, no build step           │
│  index.html · css/tokens.css · css/app.css · css/components.css              │
│  js/app.js · api.js · browser.js · chain.js · panel.js · view.js · widgets.js  │
│  js/session.js · preset_manager.js · spectral_editor.js · analysis.js        │
│  js/batch.js · keymap.js · i18n.js                                            │
│                                                                              │
│  Renders: module browser, composite view, inspector, chain strip, session bar │
└────────────────────────────────┬─────────────────────────────────────────────┘
                                 │ HTTP + JSON          ▲
                                 │ WebSocket (binary)   │ /ws/stream
┌────────────────────────────────▼─────────────────────────────────────────────┐
│  aurserver/       transport + orchestration only (FastAPI)                   │
│  app.py · assets.py · audio.py · presets.py · batch.py · batch_formats.py    │
│                   ws.py                                                         │
│                                                                              │
│  Owns: the session engine, file I/O, wire formats, scheduling. Owns no DSP. │
└────────────────────────────────┬─────────────────────────────────────────────┘
                                 │ aurcore public API
                                 │ Engine · ModuleHandle · ParamStore
┌────────────────────────────────▼─────────────────────────────────────────────┐
│  aurcore/         format-agnostic DSP core  (numpy only, scipy optional)     │
│                                                                              │
│  api.py        Engine facade: chain, process, serialize / deserialize        │
│  params.py     ParamDesc / ParamStore — name-keyed, natural units            │
│  registry.py   module registry + factory                                     │
│  analysis.py   the measurement service (spectrum, loudness, correlation)    │
│  undo.py       the command stack behind undo / redo                          │
│  document.py   the session document format                                   │
│  abi_bridge.py the Python side of the plug-in C ABI                          │
│  modules/      one file per module (base.py is the contract)                 │
│  dsp/          11 primitives: util, fft, windows, stft, filters, resample,  │
│                loudness, spectral, phase_vocoder, timepitch, dither          │
│  serial/       typed_value.py · preset_json.py · chain_json.py ·             │
│                document.py · migrate.py                                      │
│  schema/       params.tsv — the canonical parameter table                   │
└────────────────────────────────┬─────────────────────────────────────────────┘
                                 │ narrow C ABI
                                 │ plugins/vst3/aurcore_c_api.h
┌────────────────────────────────▼─────────────────────────────────────────────┐
│  plugins/       host-facing adapters. One template per plug-in format; the   │
│                 module a binary implements is stamped into it at build time. │
│                                                                              │
│  vst3native/   the shipped VST3 adapter — written straight against the       │
│                VST3 SDK, no framework in between                             │
│  vst3/         the C ABI header, the build-time stamper, the identity       │
│                tooling, and a framework-based adapter kept as an alternative │
│  abi/          the C ABI over the core, and its builder                     │
│  aax/          the identifier model and the page layout — design only       │
│  identity.py   the identity record: derivation, stamping, read-back          │
└────────────────────────────────┬─────────────────────────────────────────────┘
                                 │ loaded by
┌────────────────────────────────▼─────────────────────────────────────────────┐
│  qa/vst3host/  an independent host, written against the SDK, that loads a    │
│                built plug-in the way a DAW does and reports what it saw     │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Layer rules (enforced, not aspirational)

| Layer | May import | Must never import |
|---|---|---|
| `aurcore` | `numpy`, `scipy` (optional, always behind a numpy fallback) | `aurserver`, `fastapi`, `aurui`, anything network or file-system |
| `aurserver` | `aurcore`, `fastapi`, `uvicorn`, `soundfile` | `aurui` internals (it serves them as static files) |
| `aurui` | nothing — it talks HTTP to the server | — |
| `plugins` | the C ABI (`aurcore_c_api.h`) and the identity tooling | `aurcore` internals — the adapter never reaches past the ABI |
| `aurmcp` | `aurcore`, the standard library | anything that is not in the standard library — the server adds no dependency |

`aurcore` is the only layer with algorithmic content. If a change adds a decision to
`aurserver` about *what the audio should sound like*, that decision belongs in `aurcore`.
If a change adds a decision to a plug-in adapter about *what the audio should sound like*,
that decision belongs in `aurcore` too — the adapter is a bridge, not an implementation.

---

## 2. The parameter registry is the single source of truth

Every module's parameter set is declared **once**, in
[`aurcore/schema/params.tsv`](../aurcore/schema/params.tsv). Nothing else is allowed to
restate it. The file is parsed by [`aurcore/schema/__init__.py`](../aurcore/schema/__init__.py),
which produces `ModuleSpec` objects holding `ParamDesc` values.

The table is a 17-column TSV. A row is one parameter:

```
code  display_name  category  edition  element  param  type  default  min  max
curve  exponent  unit  choices  group  help  aliases
```

`type` is one of `float`, `int`, `bool`, `enum`. `default`, `min` and `max` are in
**natural units** (dB, Hz, ms, %, st). `choices` is a `|`-separated list, used only by
`enum` rows. The loader rejects any row that does not have exactly 13 fields, so a
malformed table fails loudly at load time rather than producing a wrong document.

**What the table holds today** — `python tools/validate_schema.py`:

| | Count |
|---|---|
| modules | **49** |
| parameters | **284** |
| enum parameters | **40** |

The four categories: Repair 27, Utility 18, Measurement 2, System 2. The two editions:
standard 38, advanced 11.

```bash
python -c "import sys,collections;sys.path.insert(0,'.');from aurcore.schema import load;s=list(load().values());print(collections.Counter(x.category for x in s));print(collections.Counter(x.edition for x in s))"
```

### The pipeline

```
                     aurcore/schema/params.tsv            ← hand-edited, canonical
                                │
                                │  aurcore.schema.load()
                                ▼
               ModuleSpec{code, display_name, category, edition, element, params[]}
                                │
        ┌──────────────┬───────┴────────┬──────────────┬────────────────┐
        ▼              ▼                ▼            ▼                ▼
  aurcore/modules/*  registry.py   aurserver    aurui panel     docs/MODULES.md
  (ParamDesc lists)  (ModuleInfo)  (JSON API)   (control grid)  (the reference)
        │              │                │            │
        │              ▼                ▼            ▼
        │       schema/modules.json  /api/modules  control factory
        │       schema/generated.py  schema/aur_modules.h
        │       schema/modules.schema.json
        ▼
   runtime values ──────────────► serialized artefacts (presets / chains / documents)
```

`tools/codegen_registry.py` renders five artefacts from the canonical table plus the live
registry:

| Artefact | Purpose |
|---|---|
| `aurcore/schema/modules.json` | the static registry — the server answers "what modules exist" without importing every module file |
| `aurcore/schema/modules.schema.json` | a JSON Schema (draft 2020-12) for that registry, for editors and validators |
| `aurcore/schema/generated.py` | the same table as Python, plus the parameter index the adapters use |
| `aurcore/schema/aur_modules.h` | the module table as a C header — the bridge the plug-in adapters compile against |
| `docs/MODULES.md` | the human-readable module reference |

All five are generated output and are never hand-edited.
`python tools/codegen_registry.py --check` reports *CODEGEN DRIFT: none — 5 file(s) up
to date.*

`tools/validate_schema.py` checks the tree against them, in four passes:

| Check | What it asserts |
|---|---|
| live registry conformance | every implemented module matches the canonical table exactly |
| JSON Schema conformance | `modules.json` validates against `modules.schema.json` |
| codegen drift | the committed artefacts are byte-identical to a fresh run of the generator |
| static registry checks | unique codes, unique parameter names, enum parameters carry choices, defaults inside their range, no whitespace in names, no forbidden trademark token |

### Why the table is hand-written TSV and not Python

* It is a *data* artefact: reviewable in a diff, greppable, and diffable against a module
  implementation without running anything.
* It keeps module implementations honest. `aurcore.schema.validate_module(spec, descs)`
  compares an implemented module's `describe()` against the table and returns a list of
  differences; the test suite asserts that list is empty for every module.
* It gives the UI a free, exact parameter contract: type, default, range, unit, choices,
  group — everything a control needs to be generated.

### Naming rules inside the table

* **Parameter identity is the name.** There are no numeric parameter ids in the core.
* Values are stored in **natural units** (dB, Hz, ms, %, st, LUFS). The UI maps a value to a
  `0..1` knob position; the core never sees a normalised value.
* Shared names mean shared semantics: `Sensitivity`, `Threshold`, `Strength`, `Bandwidth`,
  `Reduction`, `Attack`, `Release`, `Quality`, `Crossover` are reused verbatim across
  modules so the UI can offer consistent tooltips and the preset layer stays generic.
* Module `code` (`AUR_DECLICK`) is stable and is what presets, chains and plug-in
  parameters are keyed on. `element` (`DeClicker`) names the runtime implementation and may
  change when an implementation is swapped.

### The host parameter id

The core has no numeric parameter id. The **host** does, because a host wants a compact
integer per control, and the plug-in layer has to give it one.

The rule as implemented: **a host parameter id is the parameter's position inside its own
module**, `0 … n−1`, in the order the parameters appear in the table. The index comes from
the generated registry, `aurcore/schema/generated.py` → `PARAM_ID` (which is the same
object as `PARAM_INDEX`), and it is mirrored into the C header as
`AUR_PARAM_<MODULE>_<n>`:

```
python -c "import sys;sys.path.insert(0,'.');from aurcore.schema import generated;
           print(generated.PARAM_ID['AUR_SPECTRALEDITOR'])"
{'Mode': 0, 'Strength': 1, 'Region Length': 2, 'Direction': 3,
 'Multi Resolution': 4, 'Before Weighting': 5, 'After Weighting': 6}
```

The same numbers travel in the identity record's `param_id_map`, which is what the adapter
uses to turn a host id back into a canonical core parameter name.

Four rules keep the ids stable:

1. **The id is the position inside the module.** A module's parameters are laid out in
   table order and numbered from zero.
2. **New parameters are appended to the end of their module.** A parameter inserted in the
   middle of a module shifts the ids of the parameters after it, and every session that
   refers to the old ids breaks.
3. **A parameter that is renamed keeps its id.** The name in the table changes, the id
   does not.
4. **The map is per plug-in, and the name is what crosses formats.** One Auralis plug-in
   implements exactly one module, so an id is unambiguous inside that plug-in. The id is
   *not* unique across the family — id `1` is `Sensitivity` in De-Click and `Gain` in
   Breath Control — so an automation lane that has to survive a change of plug-in is
   written against the **name**, which is what the identity record and the presets carry.

---

## 3. The module contract

[`aurcore/modules/base.py`](../aurcore/modules/base.py) defines `Module`. A module is:

```python
class Module:
    code: str          # stable identifier, e.g. "AUR_DECLICK"
    display_name: str  # "De-Click"
    category: str      # "Repair" | "Utility" | "Measurement" | "System"
    edition: str       # "standard" | "advanced"
    element: str       # runtime element id, e.g. "DeClicker"
    analysis: bool     # offers an "output only" analysis render
    supports_learn: bool  # offers a Learn action
    ml: bool           # is conceptually model-backed

    def describe(self) -> list[ParamDesc]: ...          # the full parameter list
    def process(self, x: np.ndarray) -> np.ndarray: ... # (n, ch) -> (n, ch)
    def reset(self) -> None: ...
    def serialize(self) -> dict: ...
    def deserialize(self, data: dict) -> None: ...
    def learn(self, x: np.ndarray) -> None: ...         # optional, "Learn" button
    @property
    def latency_samples(self) -> int: ...
    def state_base64(self) -> str: ...
    def load_state_base64(self, text: str) -> None: ...
```

The capability flag is named `supports_learn`, **not** `learn`. `learn` is the method
above; a class attribute of the same name would shadow it and report every module as
learnable. The registry exposes the same flag under the shorter name
`registry.ModuleInfo.learn` — **10** of the 51 modules have it, and **13** are marked
`analysis`.

Rules that the engine and the tests rely on:

* **Every parameter is visible.** A module with a hidden knob is a bug: `describe()` must
  return every parameter the module reacts to, and it must match `params.tsv` exactly.
* **Parameters live in a `ParamStore`.** The base class builds `self.params` from
  `describe()`. `ParamStore.set()` clamps to `[minimum, maximum]`, coerces to the declared
  type and never raises — an out-of-range value is silently clamped, not an error.
* **State is explicit.** Learned profiles, filter histories and envelopes live on `self` and
  are exported through `serialize()` / `deserialize()`; binary state travels as a base64
  blob through `state_base64()` / `load_state_base64()`.
* **Composite modules** (one UI module, several sub-processors) hold their sub-elements as
  inner objects and serialise them as a dict of sub-dicts — `Deconstruct` and
  `Guitar De-Noise` are the two current examples.
* **Engine-level behaviour.** `Engine.process()` walks the chain in signal order, skips
  bypassed steps, and repairs channel-count drift. `Engine.latency_samples` is the sum of
  the enabled modules' latencies and is reported in the status bar.

### The engine

[`aurcore/api.py`](../aurcore/api.py) is the public facade:

```python
engine = create_engine(sample_rate=48000, channels=2)
handle = engine.add_module("AUR_DECLICK")
engine.set_param(handle, "Sensitivity", 3.0)
y = engine.process(x)          # x is (n_samples, n_channels) float64
state = engine.serialize()      # an "auralis.chain" document
engine.deserialize(state)
```

`Engine` owns the chain and nothing else. It imports its own module package on load, so
a caller only has to import `aurcore` to be able to instantiate a module.

### The measurement service

[`aurcore/analysis.py`](../aurcore/analysis.py) is **not** a processing module: it is
registered nowhere, has no parameters and never touches a chain. It answers the questions
the inspector's *Analysis* tab asks — magnitude spectrum, waveform statistics (true peak,
sample peak, RMS, DC offset, possibly-clipped count), loudness (integrated, short-term,
momentary, loudness range, true peak, per ITU-R BS.1770-4 and EBU Tech 3342),
inter-channel correlation and a peak histogram. Every measurement honours an optional
selection in seconds.

---

## 4. Realtime safety

`Module.process()` is the audio path. It must behave like it runs on a callback thread.

**Allowed:** arithmetic, allocation of bounded buffers, `numpy` vectorised ops, state
updates that are `O(1)` per sample.

**Forbidden inside `process()`:**

| Forbidden | Why |
|---|---|
| File I/O | unbounded latency, thread-unsafe |
| Network access | same |
| Exceptions escaping the call boundary | a glitch becomes a crash |
| Loops whose length depends on the input duration | unbounded work per block |
| Growing caches | memory growth over a session |
| Global mutable state | two chains would interfere |

**Bounded work.** Any loop inside `process()` must have a bound that is a function of the
engine configuration (window size, band count), never of the number of samples in the
buffer. Modules that need an offline analysis — spectral repair, dialogue isolation,
music rebalancing — are marked `analysis = True` in the registry and are executed by the
server in a render job, not on the streaming path.

**Latency** is declared, not discovered: `latency_samples` returns the algorithmic delay of
the module (filter group delay, window overlap). The engine sums the enabled modules and
the server compensates on export.

**No exceptions.** A module that cannot handle an input must degrade, not raise. The engine
catches and reports module-level failures at the server boundary.

**The budget is met almost everywhere.** The realtime budget of a 64-sample block at
48 kHz is 1333 µs, and the budget table (`qa/performance/budgets.py`) holds **47 of the
51 modules** against it directly. Two are held at *debt* — their measured cost plus
50 % — because they do not fit today: `AUR_MUSICREBALANCE` (142 trf) and
`AUR_SPECTRALDENOISE` (1629 trf, 1014 % of the budget). The cause is measured in
[`qa/performance/REPORT.md`](../qa/performance/REPORT.md): the analysis windows are
fixed, so the cost does not fall with the block size. The fix is written down there and
is **not** applied.

---

## 5. Threading model

The alpha is single-process. The rules exist so that adding threads later does not
require a redesign.

```
        ┌───────────────────────────────────────────┐
        │  uvicorn event loop (asyncio)             │  aurserver
        │  · WebSocket send / receive               │
        │  · the preview stream (its own Engine)    │
        └───────────────┬───────────────────────────┘
                        │  sync handlers run in Starlette's threadpool
        ┌───────────────▼───────────────────────────┐
        │  request threadpool                       │  aurserver
        │  · upload · analyse · render · presets    │
        │  · the session Engine, under one RLock    │
        └───────────────┬───────────────────────────┘
                        │
        ┌───────────────▼───────────────────────────┐
        │  Engine.process()                         │  aurcore
        │  one engine, one thread at a time          │
        └───────────────────────────────────────────┘
```

* **The engine is not thread-safe.** One `Engine` instance belongs to one session and is
  only touched from one thread at a time. The server serialises every access to the
  session engine with a single `threading.RLock` (`aurserver/app.py`).
* **The server never calls `Engine.process()` on the event loop.** The request handlers
  are plain `def`, so FastAPI runs them in Starlette's threadpool. A long render therefore
  costs a thread, not the loop. The one exception is the upload handler, which is `async`
  because it has to await the multipart body; it decodes the file on the event loop.
* **The UI is remote.** The browser never shares memory with the core. Every interaction is
  a request; every value update is a round trip. This is what keeps the "replaceable UI"
  decision cheap.
* **Audio streaming** (`/ws/stream`) uses a single producer and a single consumer with an
  `asyncio.Queue`. Each connection owns **its own** `Engine`, so a preview can never
  disturb the chain the user is working on. The stream is bounded: `BLOCK_SIZE = 1024`
  samples per block, `MAX_STREAM_SECONDS = 30.0` of audio, a `WALL_CLOCK_TIMEOUT = 60.0`
  second wall-clock budget and a `PRE_ROLL_SECONDS = 1.0` pre-roll so filters are settled
  before the first block (all five constants in `aurserver/ws.py`).
* **The undo history lives in the client.** `aurcore/undo.py` holds the command stack, and
  `aurui/js/session.js` drives it. Every step is carried out on the server through the
  ordinary chain routes (`POST /api/chain`, `DELETE /api/chain/{index}`,
  `POST /api/chain/reorder`, `POST /api/chain/{index}/bypass`,
  `POST /api/chain/{index}/param`), so the server stays the single owner of the chain and
  the client never reverses a state on its own.

### The plug-in side

The plug-in has its own threading rules, and they are stricter:

* **One engine belongs to one audio thread.** `aur_engine_process` must not be called
  concurrently with anything else on the same engine (the rule is written into the ABI
  header).
* **The host parameter values are atomic.** The host writes the normalised parameter
  values from the user-interface thread and the audio thread reads them, so the adapter
  keeps them in an array of `std::atomic<double>`.
* **The component and the controller are one object** in the shipped adapter, so the
  parameter values are already shared; the connection point only has to be recorded.
  See §8 for the consequences.

---

## 6. Serialisation formats

All Auralis JSON artefacts use the **typed-value** envelope
([`aurcore/serial/typed_value.py`](../aurcore/serial/typed_value.py)):

```jsonc
{ "Type": "Float",  "Value": 3.5 }
{ "Type": "Int",    "Value": 28 }
{ "Type": "UInt",   "Value": 8 }
{ "Type": "Bool",   "Value": true }
{ "Type": "String", "Value": "text" }
{ "Type": "Base64", "Value": "<b64>" }
```

Every scalar in every artefact is wrapped. The envelope is self-describing, so a reader
that does not know a field can still interpret it, and unknown fields survive a
round-trip — which is what keeps old presets loadable after the schema changes.

| Format | Purpose | Where |
|---|---|---|
| `auralis.preset` | one module: its parameters and its state | `aurcore/serial/preset_json.py` |
| `auralis.chain` | an ordered module chain | `aurcore/serial/chain_json.py`; `Engine.serialize()` produces the same shape |
| `auralis.document` | a session: audio reference, chain, view state, markers, history | `aurcore/serial/document.py` and `aurcore/document.py` |

`aurcore/serial/migrate.py` holds the version-to-version migration path for older
artefacts, so a file written by an earlier build still opens: `migrate_preset`,
`migrate_chain` and `migrate_document` upgrade a v0 document to v1 and report which
migrations they applied.

```jsonc
// auralis.chain
{
  "format": "auralis.chain", "version": 1,
  "steps": [
    { "code": "AUR_DECLICK", "enabled": true,
      "params": { "Sensitivity": { "Type": "Float", "Value": 3.0 } },
      "state":  { "Type": "Base64", "Value": "" } }
  ]
}
```

`Engine.serialize()` produces exactly this shape; `Engine.deserialize()` reads it back and
skips steps whose module code is unknown, so a chain written by a newer build still loads
in an older one as long as the missing modules are optional.

### The session document

The session document is what the workbench writes when the user saves.
[`aurcore/document.py`](../aurcore/document.py) is the current implementation; it carries
the audio reference (path, sample rate, channels, frame count, and a **content hash** so
the workbench can notice that the file on disk changed), the chain, the view state (zoom,
scroll, frequency range, selection, view mode), the markers the user dropped on the
timeline, and a summary of the undo history.

The content hash digests the first and the last megabyte of the file together with its
size and modification time (`HASH_SAMPLE_BYTES = 1 << 20`). Hashing a 500 MB recording in
full on every save would be wasteful, and the combination is enough to catch the common
case — the file was replaced.

Loading a document runs the migrations of [`aurcore/serial/migrate.py`](../aurcore/serial/migrate.py),
so a document written by an older build is upgraded on the way in.
[`aurcore/serial/document.py`](../aurcore/serial/document.py) holds the serialisation-level
builder for the same format.

The document reaches the client over three routes — `POST /api/session/save`,
`POST /api/session/load` and `GET /api/session/list` — which were added after the first
alpha pass and are exercised by `tests/test_journey.py`.

---

## 7. The ML seam

Some modules are conceptually model-backed. The alpha ships **three trained
models** as versioned artefacts (`models/*.aurm`, read through the seam in
`aurcore/models/`) beside the deterministic DSP approximations: the seam
hands a model out in the **offline** mode only, and a module with no model
present behaves exactly as it did before. Today two modules
carry `ml = True`: **Dialogue Isolate** and **Music Rebalance**.

The seam is the module boundary itself, and it has three parts:

1. **`ml = True` on the class.** The registry exposes this flag so the UI can label a module
   as model-backed and so the roadmap can track it. It is metadata, not behaviour.
2. **A DSP-first implementation.** Every model-backed module has a classical algorithm that
   satisfies the same contract: same parameter names, same ranges, same output shape, same
   `learn()` semantics. The approximation is *the* implementation for the alpha; the
   learned model, when it arrives, replaces the internals of `process()` and nothing else.
3. **A state boundary.** A model-backed module's learned artefact is state, not parameters.
   It travels through `serialize()` / `deserialize()` as a base64 blob. A neural model is
   therefore just another state blob, and swapping one for another changes no file format.

The rule: **no parameter is introduced for the sake of a future model.** If a parameter only
makes sense with a trained network, it does not go in `params.tsv` yet. The table stays
honest about what the shipped code actually does.

---

## 8. The plug-in adapter layer

The full description of the identity layer lives in [`../plugins/README.md`](../plugins/README.md)
and the adapter's own in [`../plugins/vst3native/README.md`](../plugins/vst3native/README.md).
This section describes what the code does today.

### The shipped adapter is written against the SDK

`plugins/vst3native/` holds the Auralis VST3 adapter, written **straight against the VST3
SDK**, with no framework in between. The reason is concrete: the framework release that
would have been used wants a 3.6-era SDK layout, the next one wants a 3.7-era layout that
no SDK tag provides, and the SDK in this repository is **3.8.1** in the new split layout
(`vendor/vst3sdk/pluginterfaces` and `vendor/vst3sdk/public.sdk` are two separate trees
and the SDK's `base/` module is not part of it). The SDK's own helper classes
(`basewrapper`, `SingleComponentEffect`, `EditControllerEx1`, …) all include
`base/source/fobject.h`, which is not in that layout, so the adapter implements the four
interfaces itself:

```
IPluginFactory    the class factory the host asks for first
IComponent        the bus layout and the state
IAudioProcessor   the audio path
IEditController   the parameter list and the parameter values
```

`IEditController2` and `IConnectionPoint` are implemented as well. All of them live in
**one** object (`AuralisEffect`), the "combined component" shape the SDK's own
`SingleComponentEffect` uses: an Auralis plug-in is a single module, so there is nothing
to split between a processor and a controller.

`plugins/vst3/` still holds a framework-based adapter (`AuralisPlugin.{h,cpp}`) and the
identity tooling. It is an alternative, not the shipped path; nothing in it was touched by
the move.

### One template, every module

Which module a binary implements is decided at **build time**: the build stamps an identity
record into a reserved slot inside the linked binary, and the adapter reads that record
when the host creates the plug-in. No module name, parameter name or preset is written in
the source.

Two consequences follow, and they are the whole point:

* Adding a module to the canonical table produces a plug-in for every format. There is no
  per-module adapter code, no per-module parameter list and no per-module page table.
* The adapter is never recompiled when a module changes. The identity record is data, not
  code.

```
                  aurcore/schema/params.tsv          (the source of truth)
                            │
              tools/codegen_registry.py  ->  aurcore/schema/generated.py
                            │                              │
                            +------------------------------+
                                           │
                                 plugins/identity.py
                                           │
                     +-------------------------+-------------------------+
                     |                                                   │
             identity record                                    AuralisGenerated.h
             (stamped into the binary)                       (compiled into it)
                     |                                                   |
                     +-------------------------+-------------------------+
                                           │
                              plugins/vst3native/AuralisVST3.cpp
                                           │
                               one binary per module
```

### The identity record

The record is a small JSON document. The build writes it **into the binary**; the
tooling can also write it next to the binary as a human-readable twin
(`<MODULE_CODE>.identity.json`) and into an install manifest that lists every plug-in with
its identity and the hash of its file.

```json
{
  "format": "auralis.identity",
  "version": 1,
  "module_code": "AUR_DECLICK",
  "display_name": "De-Click",
  "product_name": "Auralis De-Click",
  "file_description": "Auralis De-Click - Audio Restoration",
  "original_filename": "Auralis De-Click.vst3",
  "internal_name": "Auralis_De_Click",
  "category": "Repair",
  "vst3_fuid": "0101abab-abcd-ef01-4155-52414445434c",
  "aax_effect_ids": [["AURA", "DECL", "MONO"],
                     ["AURA", "DECL", "M2ST"],
                     ["AURA", "DECL", "STER"]],
  "param_id_map": {"0": "Algorithm",
                   "1": "Sensitivity",
                   "2": "Frequency Skew",
                   "3": "Click Widening",
                   "4": "Output Clicks Only"}
}
```

The schema is [`plugins/identity.schema.json`](../plugins/identity.schema.json). It is
enforced on the way in (stamping) and on the way out (reading back), so a binary that
carries a malformed record is rejected rather than half-believed. Read a record back with

```bash
python plugins/vst3/build_identity.py verify --binary <path-to-a-built-vst3>
```

### The slot

The adapter reserves a slot in its binary:

```
+---------------------------+---------------------------+
| 13 marker bytes          | 4096 region bytes         |
+---------------------------+---------------------------+
```

* The **marker** is the ASCII sequence `AURIDENTITYv1` (`IDENTITY_MAGIC` in
  `plugins/identity.py`). The build-time stamper searches for it and refuses to continue
  if it occurs more than once, so the marker is assembled from individual bytes in the
  C++ and is deliberately **not** a string literal.
* The **region** holds the UTF-8 JSON record, padded with NUL bytes
  (`IDENTITY_REGION_SIZE = 4096`).
* The slot lives in read-only data with a constant initialiser, which means the linker
  stores it in the file. A zero-initialised object would go to `.bss`, which is not part
  of the file on disk and could therefore not be patched at all.
* **The slot is read through a `volatile` pointer.** A compiler is allowed to assume that
  the contents of a `const` object never change — writing to one is undefined behaviour —
  so it will happily fold a read of the slot back to its link-time value, which would
  throw the stamped record away in an optimised build.
* The stamping is a byte-level patch of the file after it has been linked. It changes
  nothing else about the binary.

### The state format

The state chunk is the `auralis.state.v1` convention, byte for byte the same one the
framework-based adapter uses, so a session saved by one adapter loads in the other:

```
"auralis.state.v1" NUL
<core chain document> NUL
<host parameter state> NUL
```

Every field is NUL terminated. The third field is the value-tree document that holds the
host-visible parameter state:

```xml
<?xml version="1.0" encoding="UTF-8"?>

<AURALIS>
  <PARAM id="1" value="3.5" />
  <PARAM id="2" value="0.25" />
</AURALIS>
```

* the entries are ordered by the parameter id **as text**;
* the `value` is the *unnormalised* (natural unit) value — dB, Hz, ms, % — rounded to the
  same precision and written with the same shortest-round-trip decimal form;
* an enum parameter carries the index of its choice;
* an id the reader does not know is ignored, so a state written by an adapter that knows
  more parameters still loads.

`IComponent::getState` and `IEditController::getState` return the same blob, and
`setState` restores both the host parameters and the core chain from it. A state written
by a built plug-in is a few hundred bytes: **563** for De-Crackle and **655** for De-Click
on the build measured here.

### The reference counting

A host asks the factory for an object and holds it as the component and as the
controller. This adapter answers both roles from one object, so the host releases it once
more than it was counted: every `queryInterface` adds a reference and the host releases
each of those pointers as well.

Deleting the object on the last reference therefore left the host with a pointer to freed
memory, and the release that followed walked into it. The objects are **not** deleted on
the last release: the memory belongs to the module, and the host unloads the module right
after the last release, so the operating system reclaims it anyway. The factory is a
static object for the same reason.

The proper arrangement is one object for the component and one for the controller, with
the connection point carrying the parameter values from the controller to the component.
That is what the format asks for, and it is what a plug-in with its own editor needs
anyway; it is written down in `plugins/vst3native/README.md` so that the work is not lost.

### The interpreter is shut down explicitly

The last plug-in object to go calls `aur_shutdown()` (see §9). A host unloads the module
at a moment of its own choosing, and the interpreter's own exit handler then runs during
that unload, where it walks structures that are already being torn down. Ending the
interpreter while everything is still alive leaves that handler nothing to do.

The call is explicit because the same library is used inside a program that embeds it —
the application, the test suite — and there the interpreter belongs to that program.

### The build

```bat
set PATH=%LOCALAPPDATA%\Python\pythoncore-3.15-64\Scripts;%PATH%

cmd /c "tools\msvc.bat cmake -S plugins\vst3native -B build\vst3native ^
          -G Ninja -DAURALIS_MODULE=AUR_DECLICK -DCMAKE_BUILD_TYPE=Release"
cmd /c "tools\msvc.bat cmake --build build\vst3native"
```

The whole family, one module after another, so that the compilers do not all run at once:

```bat
python scripts\build_vst3.py
python scripts\build_vst3.py --only AUR_DECLICK,AUR_DEHUM
```

The script reads the module list from the registry, so it never goes stale, and it prints
the time each module took. The result is **48 stamped plug-ins**, one per module, under
`build/vst3native/AUR_<CODE>/stamped/`.

Each build also verifies itself: after every link two checks run against the built
module, both of which load it the way a host loads it — `check_vst3_export.py` (the
module exports `GetPluginFactory` and `ModuleEntry`, the factory answers, and the class id
it reports is the class id in the identity record) and `check_vst3_state.py` (the
controller publishes exactly the parameters of the record's `param_id_map`, the state
chunk is the shared `auralis.state.v1` container, and a parameter survives a round trip
through the state).

**What the family costs.** One bundle is **184 kB**: the plug-in alone. The core
and the interpreter are shared by the whole family and live once, in the runtime
directory next to the bundles (the core 201 kB, the interpreter 7.48 MB and the standard
library that goes with it 51.5 MB in the package). The 48 bundles are therefore
**8.6 MB** together and the runtime is **62.9 MB** — a package of **75.0 MB** in all.
The interpreter is still the bulk of it, and that is the reason the production track is a
native core behind the same ABI (§9).

### AAX

The format's SDK is a separate download, so the build is told where it is
(`python scripts\build_aax.py --sdk PATH`); the copy in the tree is the fallback. The
adapter is written against the SDK's interfaces and compiles against it. The identifiers,
the parameter ids, the page layout and the label rules are generated from the canonical
table and verified, and the built plug-in has been run through **the format's own
conformance validator**, which loads it in the format's host and reads the description
back. See [`aax/AAX_ADAPTER_NOTES.md`](../plugins/aax/AAX_ADAPTER_NOTES.md).

---

## 9. The C ABI

[`plugins/vst3/aurcore_c_api.h`](../plugins/vst3/aurcore_c_api.h) is the thin boundary
between an adapter and the core. An adapter never touches the core's internals. It calls
six functions:

```c
AurEngine* aur_engine_create (uint32_t sample_rate, uint32_t channels);
int        aur_engine_add_module (AurEngine*, const char* module_code);
int        aur_engine_set_param (AurEngine*, uint32_t module_index,
                                  const char* param_name, double value);
int        aur_engine_process (AurEngine*, const float* const* input,
                                float* const* output, uint32_t frames);
void       aur_engine_destroy (AurEngine*);
void       aur_shutdown (void);
```

plus ten extensions a plug-in needs but the core API cannot answer on its own:

| Extension | Why it exists |
|---|---|
| `aur_engine_set_param_text` | enum values are **choice strings**, not indices |
| `aur_engine_get_param` | read a parameter back, by name |
| `aur_engine_set_bypass` | the host's bypass switch |
| `aur_engine_module_count` | how many modules are in the chain |
| `aur_engine_latency_samples` | the latency figure the host asks for |
| `aur_engine_serialize` / `aur_engine_deserialize` | save and restore a host session |
| `aur_engine_free` | release a buffer `aur_engine_serialize` handed out |
| `aur_engine_last_error` | why the last call failed |
| `aur_engine_version` | the version of the core behind the ABI |

`aur_shutdown` is the sixth entry point: it ends the embedded interpreter, and the last
plug-in object calls it (see §8).

The contract the ABI fixes:

* success is `0` (or a non-negative index or length); failure is a **negative** `AurError`;
* parameters are addressed **by name**, values are in **natural units** (dB, Hz, ms, %),
  and an enum value is its **choice string**;
* the audio buffer is **planar**: one `float*` per channel;
* a bypassed module passes the audio through untouched;
* the chain serialises to the `auralis.chain` document and restores from it;
* the engine is **not thread-safe**: one engine belongs to one audio thread, and
  `aur_engine_process` must not be called concurrently with anything else on it.

### The implementation

The interface is not a contract on paper — it has a working implementation.

| Piece | What it is |
|---|---|
| [`aurcore/abi_bridge.py`](../aurcore/abi_bridge.py) | the Python side: one buffer in, one buffer out, and the reshaping happens here |
| [`plugins/abi/aurcore_abi.cpp`](../plugins/abi/aurcore_abi.cpp) | the C ABI over the core; embeds CPython and drives the bridge |
| [`plugins/abi/build_abi.py`](../plugins/abi/build_abi.py) | builds `aurcore_abi.dll` / `.dylib` / `.so` |
| [`tests/test_plugin_abi.py`](../tests/test_plugin_abi.py) | 42 conformance tests that load the library the way a host does |

The built library exports **18** symbols — the six entry points and the twelve extensions
(`plugins/abi/build_main/aurcore_abi.def`).

```powershell
python plugins\abi\build_abi.py        # -> plugins/abi/build/aurcore_abi.dll
python -m pytest tests\test_plugin_abi.py
```

The build needs no installed toolchain: it falls back to the compiler that ships inside the
`ziglang` wheel, and falls back again to `cc` / `gcc` / `clang` from `PATH`.

**Why the core is embedded rather than re-implemented.** The DSP is written in Python.
Re-implementing it in C++ would fork the algorithms and guarantee that the two drift. The
adapter therefore talks to the one core through a narrow, stable, versioned boundary. The
cost is that the host pays for an embedded interpreter — 7.48 MB of interpreter and 51.5 MB
of standard library, shared by the whole family, so 62.9 MB of runtime for 8.6 MB of
plug-ins — and the benefit is that there is exactly one implementation of "what a
De-Click does".

---

## 10. Data flow: one render

```
browser                     aurserver                         aurcore
───────                    ─────────                          ───────
POST /api/upload
  file bytes ──────────────► asset store
                             decodes to (n, ch) float64
                             keeps a handle ────────────────►

POST /api/render
  {asset_id, chain, start, end, learned}
                         ───► validate chain
                             build Engine(sample_rate, ch)
                             apply chain steps ──────────────► registry.create(code)
                                                                module.params.set(...)
                                                                module.process(...)
                             learned: run a model over the whole file
   ◄── {asset_id, url, models}
GET /api/audio/{id}
  ◄── WAV bytes
```

Step by step:

1. **Upload.** `POST /api/upload` stores the file under `var/`, decodes it (WAV, FLAC, OGG
   and MP3 can be read; WAV is what is written), and returns an `asset_id` plus the sample
   rate, channel count, frame count and duration. The server owns the file; the core never
   touches the file system.
2. **Chain validation.** The request carries a chain description. The server maps each step
   to a module code it knows and rejects unknown codes with `{"error": ...}` and HTTP 4xx.
3. **Engine construction.** One `Engine` per render job, at the asset's sample rate and
   channel count. Parameters are applied through `ParamStore.set()`, which clamps and
   coerces — a hostile or stale client cannot push a parameter out of range.
4. **Processing.** `Engine.process()` walks the chain in order over the requested region.
   Bypassed steps are skipped. The result is written back to `var/` as a WAV and served by
   `GET /api/audio/{asset_id}`.
5. **The learned stage (optional).** `learned` names a model of the library. It runs
   **after** the chain, over the **whole file** in one pass, deciding on the **mix** of
   the channels and applying the gain to every channel — so the image does not move and
   the cost is the cost of one channel. A name the library does not hold is a refusal
   (HTTP 400), never a silent fall back. The answer's `models` list names the model that
   ran.
6. **Comparison.** `POST /api/compare` runs two chains over the same asset and returns two
   asset ids, so the UI can A/B them without a second upload.
7. **Preview.** The preview renders a short window of the same engine and plays the
   returned WAV, or streams it over `/ws/stream`. It is the same code path as a render, so
   there is no second DSP path and no separate preview engine.

The important property: **steps 3–5 touch only the public core API.** Swapping the server
for a different shell — a plug-in, a command line tool — changes step 1 and step 5 only.

---

## 11. The HTTP surface

31 routes. The table is the live one: it is what
`python -c "import sys;sys.path.insert(0,'.');from aurserver.app import app;print(len(app.routes))"`
reports, minus the two documentation routes the framework adds.

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/modules` | the module catalogue |
| GET | `/api/session/list` | the session documents on disk |
| POST | `/api/session/save` | write a session document |
| POST | `/api/session/load` | read a session document back |
| GET | `/api/chain` | the current chain |
| POST | `/api/chain` | append a module |
| DELETE | `/api/chain/{index}` | remove a module |
| POST | `/api/chain/reorder` | move a module |
| POST | `/api/chain/{index}/bypass` | bypass a module |
| POST | `/api/chain/{index}/param` | set one parameter by name |
| GET | `/api/chain/{index}/params` | the parameter descriptors and values |
| POST | `/api/chain/{index}/learn` | capture a profile |
| POST | `/api/chain/{index}/state` | write a module's state back into it |
| POST | `/api/chain/{index}/region` | hand the spectral editor's region to the module |
| POST | `/api/upload` | decode and store an audio file |
| POST | `/api/analyze` | waveform envelope and spectrogram |
| POST | `/api/analysis` | the measurement service behind the Analysis tab |
| GET | `/api/assets` | the assets the server holds |
| POST | `/api/render` | run a chain and store the result |
| GET | `/api/audio/{asset_id}` | the rendered WAV |
| POST | `/api/compare` | render two chains over one asset |
| GET | `/api/health` | liveness |
| GET | `/api/presets` | the preset library |
| POST | `/api/preset/save` | store a preset |
| POST | `/api/preset/load` | read a preset |
| DELETE | `/api/preset` | delete a preset |
| POST | `/api/batch` | start a batch job |
| GET | `/api/batch/{job_id}` | poll a batch job |
| POST | `/api/batch/{job_id}/cancel` | stop a batch job |
| GET | `/api/delivery-targets` | the delivery target table |
| WS | `/ws/stream` | the live preview stream |

All responses are JSON; errors are `{"error": "message"}` with HTTP 4xx/5xx. The static
interface is served at `/`.

**The spectral editor's region is module state, and it has a route of its own.** The
rectangle the user paints is not a parameter — it is `SpectralEditor.set_region()`, and
it travels as the module's state. `POST /api/chain/{index}/region` carries it, and
`POST /api/chain/{index}/state` carries any module's state back (the profile it learned,
the region of the spectral editor), so that an undo that puts a module back into the
chain does not lose it.

---

## 12. What the alpha deliberately does not do

* No registered plug-in identifiers. The class ids and product ids the identity layer
  produces are locally generated; they have to be registered with the plug-in format
  bodies before shipping.
* No plug-in editor. The adapter returns no editor, so the host shows its own generic
  parameter editor. The Auralis interface belongs to the user interface layer.
* No trained models in the **real time** path. The seam hands a model out in the offline
  mode only (§7); the real time path is the hand written DSP, which is exact, cheap and
  deterministic.
* No non-destructive *edit layers*. The spectral region survives a chain round trip as
  module state, but there is no layer stack.
* No session persistence beyond chains, presets and the document format itself — there is
  no workspace, no autosave and no crash recovery.
* No multi-user anything. One process, one user, one machine.
* No network access of any kind at runtime, and no telemetry.

---

## 13. The agent surface: the model context server

`aurmcp/` is a model context server: it speaks JSON-RPC 2.0 over the standard
input and the standard output, so an agent can drive the product — list the
modules, build a chain, set the parameters, render the audio, measure the
result against the rules of the product, and read the documentation.

    python -m aurmcp

**The server adds no dependency.** The protocol is JSON-RPC 2.0 over a newline
delimited stream, and that is built from the standard library alone: nothing is
installed, nothing is vendored, and the requirements of the project do not
change. The tree still runs on numpy, scipy, fastapi, uvicorn, soundfile,
websockets and pytest.

The package holds four files:

| File | What it is |
|---|---|
| `aurmcp/protocol.py` | the JSON-RPC layer: the tool and the resource records, the error codes, the envelopes |
| `aurmcp/server.py` | the transport and the dispatch: the eight methods, the reading of the stream, the writing of the answers |
| `aurmcp/tools.py` | the tools the agent calls: the modules, the chain, the parameters, the render, the measurement |
| `aurmcp/resources.py` | the resources the agent reads: the module list, the parameter table, the documentation |

The output stream carries the protocol and nothing else, and every diagnostic
goes to the standard error: a stray print on the output corrupts the stream and
the client stops understanding the server. A tool that fails comes back as a
result with `isError` set and a message that says what went wrong, never as a
JSON-RPC error object, because the client reads the message.
