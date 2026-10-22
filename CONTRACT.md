# AURALIS — Engineering Contract

> **Binding rules for every contributor (human or agent).** Read this before writing code.
> If a rule here conflicts with your own instinct, the rule wins.

---

### The digital ceiling

A module hands back a signal inside the digital ceiling, and the ground
truth of a clipped case is therefore stated at the digital ceiling too.

A hard clipper is memoryless, so the level of the programme it destroyed
cannot be read back from the material; the yard measures at the one level
the format can hold.  A repair module may only attenuate, so for it the
ceiling is the ceiling exactly (tolerance 1e-6, i.e. −120 dBFS).  A module
whose job is to lift makes room for itself instead of exceeding the ceiling,
and a module whose job is the user's own gain is exempt — the exemption is
the range that parameter declares, not a hidden clamp.


## 0. NAMING — ABSOLUTE RULE

**The Auralis codebase must never mention any third-party product, vendor, or trademark.**

- Never write the strings `RX`, `iZotope`, `RX 11`, `RX Audio Editor`, `R2R`, or any of their
  module/parameter display names in **source, comments, identifiers, UI strings, presets, docs,
  test names, filenames, or metadata**.
- Our own names only. Where this product needs a parameter that a competitor also uses, use a
  **generic, descriptive** name: `Sensitivity`, `Threshold`, `Strength`, `Bandwidth`,
  `Reduction`, `Attack`, `Release`, `Quality`, `Crossover` — not a branded one.
- Our module names are our own: `De-Click`, `De-Hum`, `De-Ess`, `De-Plosive`, `De-Crackle`,
  `De-Clip`, `De-Reverb`, `De-Rustle`, `De-Wind`, `De-Bleed`, `Breath Control`, `Mouth De-Click`,
  `Voice De-Noise`, `Spectral De-Noise`, `Spectral Repair`, `Spectral Recovery`, `Guitar De-Noise`,
  `Deconstruct`, `Dialogue Isolate`, `Music Rebalance`, `Ambience Match`, `Repair Assistant`,
  `Leveler`, `Loudness Control`, `Loudness Optimize`, `EQ`, `EQ Match`, `Gain`, `Fade`, `Normalize`,
  `Phase`, `Mixing`, `Center Extract`, `Azimuth`, `Dither`, `Resample`, `Time & Pitch`,
  `Variable Pitch`, `Variable Time`, `Wow & Flutter`, `Signal Generator`, `Streaming Preview`,
  `Find Similar`, `Connect`, `Monitor`.
- Code identifiers use our own prefix `aur_` / `Auralis` / `auralis`. Package root is `rxcore`
  ONLY as a legacy path alias — **prefer `aurcore`**; if you must touch `rxcore/`, keep the
  directory name for import compatibility but never mention the origin in comments.
- The reverse-engineering workspace (separate directory, outside this repo) is evidence only.
  Nothing from it is copied into this repo.

---

## 1. Repository layout

```
auralis/
  aurcore/            # THE DSP CORE  (library, no UI, no network, no plugin formats)
    __init__.py
    params.py         # parameter model + store          [CONTRACT OWNER: architect]
    registry.py       # module registry + factory        [CONTRACT OWNER: architect]
    api.py            # public facade                    [CONTRACT OWNER: architect]
    dsp/              # DSP primitives (fft, stft, windows, resample, filters, loudness, ...)
    modules/          # one file per module
    serial/           # typed_value.py, preset_json.py, chain_json.py, document.py
    schema/           # modules/*.json  (the single source of truth)
  aurserver/          # HTTP/WS API (FastAPI) — no DSP logic, only orchestration
  aurui/              # web UI (vanilla HTML/CSS/JS, no build step)
    index.html  css/  js/
  tools/              # codegen, validators, bench
  tests/              # pytest
  corpus/             # golden corpus (manifest + clips)
  docs/
  var/                # runtime scratch (uploads, renders) — gitignored
```

**Dependency rule:** `aurcore` imports only `numpy` (+ `scipy` if present, but every scipy use
must be behind a numpy fallback). `aurcore` MUST NOT import `aurserver`, `fastapi`, or anything
from `aurui`.

---

## 2. Core public API (`aurcore/api.py`)

```python
# --- engine ------------------------------------------------------------------
def create_engine(sample_rate: int, channels: int = 2) -> "Engine"

class Engine:
    sample_rate: int
    channels: int
    def list_modules(self) -> list[ModuleInfo]        # from the registry
    def add_module(self, code: str) -> ModuleHandle   # raises KeyError on unknown code
    def remove_module(self, handle: ModuleHandle) -> None
    def clear_chain(self) -> None
    def chain(self) -> list[ModuleHandle]
    def set_param(self, handle: ModuleHandle, name: str, value) -> None
    def get_param(self, handle: ModuleHandle, name: str)
    def param_info(self, handle: ModuleHandle) -> list[ParamDesc]
    def set_bypass(self, handle: ModuleHandle, bypass: bool) -> None
    def process(self, x: np.ndarray) -> np.ndarray     # (n, channels) float64, in-place ok
    def process_sample(self, x: np.ndarray) -> np.ndarray   # per-block streaming path
    def latency_samples(self) -> int
    def tail_samples(self) -> int
    def reset(self) -> None
    def serialize(self) -> dict                        # chain state
    def deserialize(self, state: dict) -> None
```

`x` is always **2-D `(n_samples, n_channels)` float64** in the core API. The server converts
to/from interleaved int16/float32 for the wire.

`latency_samples` is the delay the chain adds, in samples, and the sum of the delays of its
modules.  `tail_samples` is how long the chain keeps ringing after the input stopped, in
samples, and the **longest** tail of its modules and not the sum: the modules of a chain are
clocked together, block by block, and a host that chains two plug-ins waits for the longer of
the two tails and not for their sum.  A module that never goes quiet reports `TAIL_INFINITE`
(`aurcore/params.py`), and so does a chain that holds one.

---

## 3. Parameter model (`aurcore/params.py`) — CANONICAL, DO NOT FORK

```python
ParamType = Literal["float", "int", "bool", "enum"]

@dataclass(frozen=True)
class ParamDesc:
    name: str                      # canonical, human-readable, used as the KEY everywhere
    type: ParamType
    default: float | int | bool | str
    minimum: float = 0.0
    maximum: float = 1.0
    unit: str = ""                 # "dB", "Hz", "ms", "%", "st"
    choices: tuple[str, ...] = ()  # for type == "enum"
    group: str = ""                # panel grouping label
    automatable: bool = True
    stepped: bool = False          # int params that behave as a selector
    curve: str = "linear"          # "linear" | "log"
    help: str = ""                 # one-line tooltip

class ParamStore:
    def __init__(self, descs: Iterable[ParamDesc]): ...
    def descs(self) -> list[ParamDesc]
    def get(self, name: str)
    def set(self, name: str, value) -> None      # clamps + type-coerces
    def reset(self) -> None
    def to_dict(self) -> dict[str, float|int|bool|str]
    def from_dict(self, d: dict) -> None
```

**Rules**
- Parameter identity is the **name**. There are no numeric parameter ids anywhere in the core.
- Values are stored in **natural units** (dB, Hz, ms, %). The UI converts to a 0..1 knob
  position; the core never sees normalised values.
- `set()` clamps to `[minimum, maximum]` and coerces to the declared type. Silent, no exceptions.
- `bool` params have `minimum=0.0, maximum=1.0`.
- `enum` params carry `choices`; the stored value is the **choice string**.
- Every module MUST expose its parameters through `ParamStore`. A module with a hidden knob is
  a bug.

---

## 4. Module base class (`aurcore/modules/base.py`) — CANONICAL

```python
class Module:
    code: str            # canonical code, e.g. "AUR_DECLICK"  (stable, used in presets + plugins)
    display_name: str    # "De-Click"
    category: str        # "Repair" | "Utility" | "Measurement" | "System"
    edition: str         # "standard" | "advanced"
    element: str         # runtime element id, e.g. "DeClicker"

    def __init__(self, sample_rate: int, channels: int): ...
    def describe(self) -> list[ParamDesc]:      # the module's full parameter list
        raise NotImplementedError
    def process(self, x: np.ndarray) -> np.ndarray:   # (n, ch) -> (n, ch)
        raise NotImplementedError
    def reset(self) -> None: ...
    def serialize(self) -> dict: return {}
    def deserialize(self, data: dict) -> None: ...
    def learn(self, x: np.ndarray) -> None: ...   # optional; "Learn" button support
    @property
    def latency_samples(self) -> int: return 0
    @property
    def tail_samples(self) -> int: return 0
```

**Rules**
- `process` is **realtime-safe**: no allocation in the steady state is *nice*, but no file I/O,
  no network, no unbounded loops, no exceptions escaping.
- `process` returns a new array or the same array; never mutate the caller's array in a way
  the caller cannot observe. Mutating in place is allowed and preferred for speed.
- Modules that need internal state (noise profiles, learned curves) keep it in `self` and
  expose it through `serialize`/`deserialize` (base64 blobs for binary state).
- Composite modules (one UI module, several sub-processors) implement sub-elements as inner
  objects and serialise them as a dict of sub-dicts. See `Deconstruct` / `Guitar De-Noise`.
- A module declares its **tail**: how long it keeps ringing after the input stopped, in samples,
  counted from the end of its own `latency_samples`. A module that makes a sound of its own and
  never goes quiet reports `TAIL_INFINITE` (`aurcore/params.py`). A host waits for the latency
  and the tail, so a module that rings and says nothing is a defect.

---

## 5. Module registry (`aurcore/registry.py`)

- Modules are **registered by importing** `aurcore.modules.<file>` and calling
  `register(ModuleClass)`.
- `registry.register(cls)` adds to a global list; `registry.get(code)` returns the class;
  `registry.all()` returns `ModuleInfo` records for the UI.
- A **static registry JSON** (`aurcore/schema/modules.json`) is generated by
  `tools/codegen_registry.py` from the live registry and is used by the server to answer
  "what modules exist" without importing everything. **The JSON is generated — do not hand-edit.**
- `ModuleInfo` fields: `code`, `display_name`, `category`, `edition`, `element`,
  `params: list[ParamDesc]`, `analysis: bool` (has a "output only" render mode),
  `learn: bool` (has a Learn button), `ml: bool` (needs a model).

---

## 6. Serialisation

### 6.1 Typed-value tree (the interchange format)

```jsonc
{ "Type": "Float",  "Value": 3.5 }
{ "Type": "Int",    "Value": 28 }
{ "Type": "UInt",   "Value": 8 }
{ "Type": "Bool",   "Value": true }
{ "Type": "String", "Value": "text" }
{ "Type": "Base64", "Value": "<b64>" }
```

Every scalar in every Auralis JSON artefact is wrapped in this shape. Implement it in
`aurcore/serial/typed_value.py` and use it for presets, chain files and document files.

**The identity of a parameter is its name.** The name is the key of the `params` map, so
a document written before a parameter was inserted in the middle of a module still loads
onto the right parameter. Beside the value an entry may carry two more fields:

| Field | Meaning |
|---|---|
| `Id` | the host parameter id the parameter had when the document was written. The id is the position of the parameter inside its module, and it is the number the host uses. It is advisory: a reader compares it with the id the parameter has now and reports a parameter that moved. |
| `Sig` | the meaning of the parameter: its type, its range and its unit (for example `float:0:10:dB`). A reader compares it with the meaning the parameter has now and reports a parameter that changed. |

Both are optional. A document that carries neither is read by name alone, which is what
every document written before this change is. A parameter the module does not know is
reported and skipped; it is never handed to a neighbour.

### 6.2 Preset (single module)

```jsonc
{
  "format": "auralis.preset", "version": 1,
  "module": { "code": "AUR_DECLICK", "element": "DeClicker" },
  "params": { "Sensitivity": { "Type":"Float","Value":3.0, "Id":1, "Sig":"float:0:10:dB" } },
  "state":   { "Type":"Base64","Value":"<learned profile blob>" }
}
```

### 6.3 Chain (module chain document)

```jsonc
{
  "format": "auralis.chain", "version": 1,
  "steps": [
    { "code":"AUR_DECLICK", "enabled":true, "params":{...}, "state":{...} },
    { "code":"AUR_DENOISE", "enabled":true, "params":{...} }
  ]
}
```

A load answers with a report of the parameters: how many were applied, and which ones the
module does not know any more, which ones moved and which ones changed their meaning.

### 6.4 Document (session)

```jsonc
{
  "format": "auralis.document", "version": 1,
  "audio": { "path":"...", "sample_rate":48000, "channels":2, "frames":123456 },
  "chain": { ...same as 6.3... },
  "view":  { "zoom":1.0, "scroll":0.0, "spectrogram":true, "selection":null }
}
```

---

## 7. Server API (`aurserver`) — exact routes

| Method | Route | Request | Response |
|---|---|---|---|
| GET | `/api/modules` | — | `{"modules":[ModuleInfo]}` |
| GET | `/api/chain` | — | `{"chain":[...]}` — the chain in signal order |
| POST | `/api/chain` | `{"code":"AUR_DECLICK"}` | `{"chain":[...]}` — appends a module |
| DELETE | `/api/chain/{index}` | — | `{"chain":[...]}` |
| POST | `/api/chain/reorder` | `{"from":0,"to":2}` | `{"chain":[...]}` |
| POST | `/api/chain/{index}/bypass` | `{"bypass":true}` | `{"chain":[...]}` |
| POST | `/api/chain/{index}/param` | `{"name":"Sensitivity","value":3.0}` | `{"ok":true,"name":"Sensitivity","value":3.0}` |
| GET | `/api/chain/{index}/params` | — | `{"params":[ParamDesc...],"values":{...}}` |
| POST | `/api/chain/{index}/learn` | — | `{"ok":true,"message":"..."}` |
| POST | `/api/chain/{index}/state` | `{"state":"<base64>"}` | `{"ok":true,"index":0,"code":"AUR_DEHUM","state":"<base64>"}` — writes the state of a module back into it |
| POST | `/api/chain/{index}/region` | `{"start":1.25,"end":1.75,"low":800.0,"high":6400.0}` | `{"ok":true,"index":1,"code":"AUR_SPECTRALEDITOR","region":{"start":…,"end":…,"low":…,"high":…},"region_length":0.5}` — the rectangle of the spectral editor, in seconds and hertz |
| POST | `/api/chain/{index}/model` | `{"model":"voice_enhance"}` | `{"ok":true,"index":0,"code":"AUR_VOICEDENOISE","model":"voice_enhance","message":"…"}` — chooses the model a module runs; an empty name takes the model away and the module runs its hand written path again |
| GET | `/api/models` | — | `{"models":[{"name":…,"version":…,"trained_on":…,"problem":…,"task":…,"cost_transform_eq_per_64_block":…}],"running":{…}}` — the model library, and the models that run |
| GET | `/api/assets` | — | `{"assets":[...],"current_asset_id":"..."}` — every asset the store holds |
| POST | `/api/upload` | multipart file (or the raw file) | `{"asset_id":"...","sample_rate":48000,"channels":2,"frames":N,"duration":S}` |
| POST | `/api/analyze` | `{"asset_id":"...","start":0,"end":0}` | `{"waveform":[[...]],"spectrogram":[[...]],"freq_bins":N,"frames":M,"max_db":0,"min_db":-120}` |
| POST | `/api/analysis` | `{"asset_id":"...","start":0,"end":0}` | `{"spectrum":[...],"waveform_stats":{...},"loudness":{...},"correlation":[...],"histogram":[...]}` — the measurement service |
| POST | `/api/render` | `{"asset_id":"...","chain":[...],"start":0,"end":0,"learned":""}` | `{"asset_id":"out_...","url":"/api/audio/...","models":[...]}` — `learned` names a model of the library that runs over the rendered file; it is an **offline** capability and it is refused with HTTP 400 when the library does not hold that name |
| GET | `/api/audio/{asset_id}` | — | WAV bytes (`audio/wav`) |
| POST | `/api/compare` | `{"asset_id":"...","chain_a":[...],"chain_b":[...],"start":0,"end":0}` | `{"a":url,"b":url}` |
| GET | `/api/health` | — | `{"ok":true}` — the liveness probe |
| GET | `/api/session/list` | — | `{"sessions":[{"name":"...","path":"...","saved":"...","steps":N}]}` |
| POST | `/api/session/save` | `{"name":"...","view":{...},"markers":[...],"audio":{...},"history":{...},"chain":{...}}` | `{"path":"...","document":{...}}` — the document of §6.4 |
| POST | `/api/session/load` | `{"path":"..."}` (or `{"name":"..."}`) | `{"path":"...","document":{...},"chain":{...},"summary":[...]}` |
| GET | `/api/presets` | — | `{"presets":[...]}` |
| POST | `/api/preset/save` | `{"name":"...","category":"...","module_code":"AUR_DECLICK","params":{...},"state":{...}}` | `{"path":"...","preset":{...}}` |
| POST | `/api/preset/load` | `{"path":"...","module_code":"AUR_DECLICK"}` — the module code is optional; the server refuses a preset that belongs to another module | preset JSON |
| DELETE | `/api/preset` | `{"path":"..."}` | `{"ok":true,"path":"..."}` |
| POST | `/api/batch` | `{"paths":["..."],"chain":[...],"format":"wav","bit_depth":24,"target_lufs":-23.0,"loudness_normalise":false,"out_dir":"..."}` | `{"job_id":"...","state":"queued","total":N}` |
| GET | `/api/batch/{job_id}` | — | `{"job_id":"...","state":"...","total":N,"done":N,"failed":N,"results":[...]}` |
| POST | `/api/batch/{job_id}/cancel` | — | `{"job_id":"...","state":"...","cancelled":true}` |
| GET | `/api/delivery-targets` | — | `{"targets":[{"key":"...","name":"...","integrated_lufs":-23.0,"true_peak_dbtp":-1.0,"format":"wav","bit_depth":24}]}` |
| WS | `/ws/stream` | `{"type":"configure","asset_id":"...","chain":[...],"start":0,"end":0}` | binary frames (int16) and `{"type":"end","frames":N}` |
| GET | `/` | — | the single page (`text/html`) |
| GET | `/favicon.ico` | — | the icon, when the project ships one; 404 otherwise |

Conventions: all responses JSON; errors `{"error":"message"}` with HTTP 4xx/5xx.
Static UI served at `/`.

### The rules the routes follow

A request body is a promise: a body that carries a field the route does not
know is refused with the error envelope instead of being half-ignored.  The
fields every route understands are listed in `ROUTE_CONTRACT` in
`aurserver/app.py`, and `tests/test_routes.py` walks that table: the method
exists, the body is accepted, a wrong body is refused with the error
envelope and the answer carries the keys the interface expects.

Three lines of this table used to be vague; they now say what the route
really does:

* `POST /api/preset/save` — the route reads `name`, `category`,
  `module_code`, `module`, `element`, `display_name`, `description`,
  `params` and `state`; a body with any other field is refused.
* `POST /api/chain/{index}/param` — the route answers
  `{"ok":true,"name":"...","value":...}`: the name and the value the module
  really holds after the change.
* `WS /ws/stream` — the client sends one JSON message first,
  `{"type":"configure",...}`, and the server answers with the binary frames
  of the preview and a final `{"type":"end","frames":N}`.  A failure is
  reported as `{"type":"error","message":"..."}`.


---

## 8. UI design language (`aurui`) — the look we are matching

Dark, dense, professional audio tool. **Tokens live in `aurui/css/tokens.css` — never hard-code
colours in JS or HTML.**

```
--bg-0   #0b0d10   app background
--bg-1   #12151a   panel background
--bg-2   #181c22   raised surface
--bg-3   #1f242c   control surface
--line   #2a313b   hairline border
--txt-0  #e6e9ee   primary text
--txt-1  #9aa4b2   secondary text
--txt-2  #5f6b7a   tertiary text
--accent #2f9e6e   primary accent (green-teal)
--accent-2 #e0a13a amber (learn/attention)
--danger #d1544f   red (record/clip)
--sel    #24405c   selection
```

Layout (single screen, no scrolling of the whole page):
```
┌───────────────────────────────────────────────────────────────────────────┐
│ TOPBAR: logo | file name | transport ▶ ■ ● | zoom | sample rate | tabs   │
├──────────────┬────────────────────────────────────────────┬───────────────┤
│ LEFT RAIL    │  COMPOSITE VIEW                            │ RIGHT INSPECTOR│
│ module       │  ┌──────────────────────────────────────┐  │ module panel   │
│ browser      │  │ spectrogram canvas (freq x time)    │  │ (knobs,        │
│ (categories, │  ├──────────────────────────────────────┤  │  toggles,      │
│  searchable) │  │ waveform canvas (amp x time)        │  │  selects)      │
│              │  └──────────────────────────────────────┘  │ + chain strip  │
├──────────────┴────────────────────────────────────────────┴───────────────┤
│ STATUS BAR: cursor time/freq | selection | dBFS | latency | engine state  │
└───────────────────────────────────────────────────────────────────────────┘
```

Component rules:
- **Knob**: 44 px circular, drag vertically to change, shift = fine, double-click = default.
  Draw with a canvas or SVG; show the value on hover and while dragging.
- **Toggle**: small pill with an on/off state and a distinct accent when on.
- **Select**: compact dropdown, shows the current choice.
- **Slider**: horizontal, used for 0-100 % style params and for mixer channels.
- **Module panel**: a header row (name, power button, preset name with a `*` dirty flag,
  learn button, and a `⋯` menu), then a grid of controls grouped by `ParamDesc.group`.
- **Chain strip**: horizontal list of module chips in signal order, each with a power toggle;
  drag to reorder.
- The **preview** button (space bar) plays a ~0.5 s pre-rendered buffer of the chain output.
- Everything must work at 1280×800 and look correct at 2560×1440.

---

## 9. Testing

- `pytest` under `tests/`. Naming: `test_<area>_<subject>.py`.
- Every module MUST have:
  - `tests/test_modules/test_<module>.py` — param presence + range clamp + process smoke test
    (does not crash, output finite, length preserved).
  - a golden-audio case in `corpus/` (added by the corpus agent).
- DSP primitives need numeric tests with explicit tolerances.
- No test may import the internet or the network.

---

## 10. Definition of "done" for a task

1. Code runs.
2. `python -m pytest tests -q` passes (or your new test passes and you say which fail and why).
3. No third-party trademark in any added file (see §0).
4. Public functions have docstrings.
5. If you changed a shared contract file (`params.py`, `registry.py`, `modules/base.py`,
   `api.py`, `serial/typed_value.py`, `tokens.css`), say so loudly in your report — other
   agents are depending on them.
