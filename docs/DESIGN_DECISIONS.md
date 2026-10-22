# Auralis — Design decisions

Each entry is an architecture decision record: **Context** (the forces), **Decision** (what
we chose), **Consequences** (what it costs us). The binding rules in
[`../CONTRACT.md`](../CONTRACT.md) are the summary version of ADR-9.

---

## ADR-1 — Parameters are identified by name

**Context.** Every plug-in format, every preset format and every UI control needs a stable
handle for a parameter. The obvious handle is a number. Numbers are compact, they survive
renaming, and hosts like them. Names are verbose, they break when a parameter is renamed,
and they collide with display conventions.

**Decision.** Parameter identity in the Auralis core is the **human-readable name**, e.g.
`Sensitivity`, `Threshold`, `Strength`. There are no numeric parameter ids anywhere in
`aurcore`. `ParamDesc.name` is the key; `ParamStore` is a name-keyed dictionary.

**Consequences.**

* Presets, chains and documents are readable and diffable without a lookup table. Opening a
  chain file in a text editor tells you what it does.
* Renaming a parameter is a breaking change — but it is a *visible* breaking change, and the
  typed-value envelope (ADR-4) plus the loader's "skip unknown fields" behaviour mean an
  old file still opens with the old parameter ignored rather than rejected.
* The plug-in adapters (M3) have to map host numeric ids to core names. That mapping lives
  in exactly one adapter file per host, which is the right place for it.
* The cost is bytes in JSON. For an alpha with a few hundred parameters per chain, that is
  irrelevant.

---

## ADR-2 — One core, many adapters

**Context.** A restoration tool can exist as a standalone application, as plug-ins for
several DAWs, and as a library. Implementing the DSP three times guarantees that the three
copies drift. Implementing it once and wrapping it requires deciding what the core is
allowed to know.

**Decision.** `aurcore` is a pure DSP library. It knows about sample rates, channel counts,
numpy arrays, parameters and state. It does not know about files, sockets, HTTP, plug-in
formats, or the DOM. `aurserver` and the future plug-in adapters are thin shells over the
same `Engine`.

**Consequences.**

* The same chain renders identically in the browser, in a plug-in and in a test. There is
  one implementation of "what a De-Click does".
* The core is testable without a server, a browser or a network.
* `aurcore` gains a hard dependency rule: numpy only (scipy optional, always behind a numpy
  fallback). Every scipy call needs a pure-numpy path, which costs a little duplicated
  code but removes a hard dependency from the core.
* The core cannot do file I/O, so every shell must supply the audio. That is a small tax on
  each shell and a large saving on testability.

---

## ADR-3 — The parameter registry is the single source of truth, and code is generated from it

**Context.** The same module description is needed in at least five places: the module
implementation, the registry, the server's `/api/modules`, the UI's control factory, and
the documentation. When those five are written by hand they drift, and the usual failure is
a UI knob that does nothing.

**Decision.** One canonical table — `aurcore/schema/params.tsv` — declares every module's
identity and every parameter. Everything else is derived:

```
params.tsv ──► aurcore.schema.load() ──► ModuleSpec ──┬──► module implementations
                                                      ├──► registry.ModuleInfo
                                                      ├──► /api/modules (server)
                                                      ├──► UI control factory
                                                      └──► docs/MODULES.md
```

`tools/codegen_registry.py` writes `aurcore/schema/modules.json` from the live registry so
the server can answer "what modules exist" without importing every module. That JSON is
generated output and is never hand-edited.

**Consequences.**

* `aurcore.schema.validate_module(spec, descs)` compares an implementation against the
  table and returns a list of differences. The test suite asserts that list is empty. A
  module with a hidden knob fails the build instead of shipping a dead control.
* The UI gets its control type, range, unit and choice list for free, exactly.
* Adding a module means touching two files: the table and the implementation. Adding a
  parameter means touching the table and the implementation. Both are reviewable diffs.
* The cost is a build step. The toolchain is small — a table loader and a JSON writer —
  and the generated artefacts are committed so a fresh checkout runs without any build.

---

## ADR-4 — A typed-value interchange format

**Context.** A restoration session holds floats, integers, booleans, strings and binary
blobs. Plain JSON cannot express the difference between `1` and `1.0`, cannot hold binary
at all, and turns a schema change into a migration.

**Decision.** Every scalar in every Auralis JSON artefact is wrapped in a typed-value
envelope:

```jsonc
{ "Type": "Float",  "Value": 3.5 }
{ "Type": "Int",    "Value": 28 }
{ "Type": "UInt",   "Value": 8 }
{ "Type": "Bool",   "Value": true }
{ "Type": "String", "Value": "text" }
{ "Type": "Base64", "Value": "<b64>" }
```

**Consequences.**

* Artefacts are self-describing: a reader that meets an unknown type can skip it rather
  than guess.
* Binary state — a learned noise profile, a filter history, a future neural model — travels
  in the same `"state"` slot as everything else, so there is one serialisation mechanism
  rather than three.
* A future schema change can add a type without breaking old readers.
* The cost is verbosity: every value in a preset carries four extra bytes of type tag and
  a wrapper. For a preset file of a few kilobytes, that is irrelevant; for a large
  document with thousands of values it is noticeable but still acceptable, and the
  alternative — losing information on round-trip — is worse.

---

## ADR-5 — numpy-first DSP, scipy optional

**Context.** The DSP core needs FFTs, STFTs, resampling, filters and loudness metering.
These have excellent numpy implementations and good scipy implementations. scipy is a
heavy dependency with a large binary footprint.

**Decision.** The core is numpy-first. numpy is a hard dependency; scipy is optional and
may only be used **behind a numpy fallback**. Every scipy call site has a pure-numpy path
that produces the same result within a stated tolerance.

**Consequences.**

* The core installs cleanly anywhere numpy runs, including locked-down studio machines and
  embedded hosts.
* Some code paths are duplicated. That duplication is the price of the optional dependency,
  and it is contained to the `aurcore/dsp/` primitives.
* The fallback paths need numeric tests with explicit tolerances so the two
  implementations do not drift. Both are covered by the same test, run twice when scipy is
  present.
* Performance-critical paths can use scipy when it is there without the core ever requiring
  it.

---

## ADR-6 — The alpha UI is a web application

**Context.** Auralis needs a dense, professional interface: a module browser, a
spectrogram canvas, a waveform canvas, dozens of knobs, a chain strip, live parameter
feedback. It also needs to reach plug-in hosts later (ADR-2). Building a native desktop UI
first means committing to a UI toolkit, a canvas stack and a distribution story before the
product's behaviour has settled.

**Decision.** The alpha UI is a **web application**: vanilla HTML, CSS and JavaScript, no
build step, served by `aurserver` as static files and driven by a JSON/WebSocket API. The
core is reachable only through that API — the browser shares no memory with the DSP.

**Consequences.**

* Zero build tooling: clone, install, run, open a browser. No bundler, no node, no
  transpiler, no lockfile to rot.
* The UI can be replaced. Because the core is only reachable over HTTP, a native shell
  (desktop app, plug-in UI) can drive the same engine with a different front end and
  nothing in `aurcore` changes. That is the property that makes ADR-2 cheap.
* A local HTTP server is required to use the tool, and it binds to loopback only.
* Canvas performance is browser-dependent; a very large file may need tiled rendering,
  which is a known follow-up.
* The alternative — a native UI first — would have made every core API decision twice as
  expensive, because the UI would have to live in the same process as the DSP and would
  have influenced the core's shape.

---

## ADR-7 — DSP-first approximations for the model-backed modules, behind a clean seam

**Context.** Several modules in the catalogue are conceptually model-backed: dialogue
isolation, ambience matching, music rebalancing, spectral recovery. Shipping trained
models in an alpha is not realistic — it multiplies the artefact size, adds an inference
runtime, makes results non-deterministic across machines, and blocks offline use unless the
model is bundled.

**Decision.** Every model-backed module ships a **deterministic DSP approximation** that
satisfies the identical contract: same parameter names, same ranges, same output shape,
same `learn()` semantics. The registry marks the module with `ml = True` so the UI and the
roadmap can track it. The learned artefact is **state**, not parameters, so it travels
through `serialize()` / `deserialize()` as a base64 blob.

**Consequences.**

* The alpha is fully offline, fully deterministic and fully testable: the same input gives
  the same output on every machine, and a golden-audio case is meaningful.
* The UI and the file formats do not change when a model arrives. A model replaces the
  internals of `process()` and nothing else — same seam, same parameters, same state slot.
* The rule that keeps this honest: **no parameter exists for the sake of a future model.**
  If a control only makes sense with a trained network, it does not go into `params.tsv`
  yet. The table describes what the shipped code actually does.
* The cost is that the DSP approximation is not the final behaviour of those modules, and
  the UI must be willing to change a control's meaning later. Mitigated by keeping the
  parameter vocabulary generic (`Sensitivity`, `Strength`, `Quality`) so a model's
  controls can map onto the existing names.

---

## ADR-8 — The canonical parameter table is TSV

**Context.** The parameter table has to be reviewed in a diff, read by a human, parsed by
three different tools (the runtime loader, the doc generator, the registry codegen) and
kept in a repository that also contains Python.

**Decision.** The canonical table is a **tab-separated file**,
`aurcore/schema/params.tsv`, with a fixed 13-column layout and `#` comments.

**Consequences.**

* The table is a data artefact, not code. It can be diffed, grepped and reviewed without
  running Python, and a malformed row is obvious in the diff.
* TSV is unambiguous for values that contain spaces, commas, `|` and `&`. The enum choice
  list is `|`-separated inside a single field.
* The downside of TSV is that it is unforgiving: a missing tab silently shifts every
  following column. `aurcore.schema.load()` therefore rejects any row that does not have
  exactly 13 fields, and the doc generator mirrors that strictness — a broken table fails
  loudly at load time rather than producing a wrong document.
* TSV has no types. The four parameter types are encoded as text in a fixed column and
  parsed by one shared function, so there is exactly one place where a type name is
  interpreted.

---

## ADR-9 — No third-party product, vendor or trademark appears anywhere in the codebase

**Context.** A restoration tool inevitably needs parameters that other products also have:
a sensitivity, a threshold, a bandwidth, a de-esser. The temptation is to use the
incumbent's exact display names, because users recognise them. That is a legal liability
and it muddies the product's identity — and in a codebase that is meant to be clean-room, it
is a correctness problem as much as a legal one.

**Decision.** The codebase — source, comments, identifiers, UI strings, presets, tests,
filenames, documentation and metadata — contains **only our own names**. Parameters that
are common get **generic, descriptive** names: `Sensitivity`, `Threshold`, `Strength`,
`Bandwidth`, `Reduction`, `Attack`, `Release`, `Quality`, `Crossover`. Modules carry our
own names and our own stable codes (`AUR_DECLICK`). Identifier prefixes are `aur_`,
`Auralis`, `auralis`.

**Consequences.**

* Every user-visible string has to be written and reviewed rather than copied. That is
  slower up front and it is exactly the point.
* The engine has to build its own vocabulary and earn recognition for it. The generic names
  are deliberately the names an engineer would guess, which helps discoverability.
* The rule is mechanically checkable — a single case-insensitive search for the forbidden
  strings over the tree must return nothing — and it is part of the definition of done for
  every task.
* Nothing from any reverse-engineering workspace is copied into this repository; that
  workspace is evidence, kept outside the tree.

---

## ADR-10 — Offline-first, no telemetry, no network at runtime

**Context.** Audio restoration deals with unreleased recordings, private conversations and
client material. A tool that phones home, uploads, or requires an account creates a
confidentiality problem for its users and an operational burden for its authors. The
web-UI decision (ADR-6) introduces a server, and a server can send data anywhere.

**Decision.** Auralis runs **entirely on the user's machine**. At runtime it makes **no
outbound network connections**. There is **no telemetry, no crash reporting, no analytics,
no account system and no auto-update check**. The local server binds to loopback only. The
UI ships no web fonts and no remote assets, so a rendering the UI never triggers a network
request. The project depends on no external service at runtime.

**Consequences.**

* A user can work on sensitive material with the network disconnected, and the tool keeps
  working. This is a selling point, not a limitation.
* No licence server, no key management, no telemetry pipeline, no privacy policy to write.
* There is no crash data and no usage data. Quality has to be judged by the issue tracker,
  by the test corpus and by users telling us — which is slower, and honest.
* The web-UI decision is only safe because of this one: a local server that never talks to
  the outside world is a tool, not a service.
* Sharing is file-based. A chain, a preset or a session document is a file the user can
  email, put on a network share, or keep.
