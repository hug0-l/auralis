# Auralis — Decisions

Every entry is a decision record: **Context** (the forces), **Decision** (what we chose),
**Alternatives considered** (what we rejected and why), **Consequences** (what it costs
us) and **Evidence** (where a reader can check the claim).

The binding rules live in [`../CONTRACT.md`](../CONTRACT.md). This document explains *why*
they exist; the contract is what a contributor has to obey.

Every "Evidence" entry is a file, a function or a command. Nothing here is a matter of
opinion that cannot be checked.

Two entries carry a **correction** note, marked in the text: D15 (the host parameter id
is per module, not registry-wide) and D16 (the ABI has six entry points, not five, and
the plug-in binary is 178 kB, not 1.8 MB). Both were wrong in the first draft of this
document; the code was checked, and the entry now describes the code.

---

### The ceiling rule, and the truth of a clipped case

**Decision.**  A module hands back a signal inside the digital ceiling, and
the ground truth of a clipped case is stated at the digital ceiling too.

**Why.**  The writer hard-clips (`aurserver/assets.py`), so an over-ceiling
module output re-creates the damage the module was asked to remove; the rule
is therefore a product guarantee, not a preference.  And a hard clipper is
memoryless: the level of the programme it destroyed cannot be read back from
the material, so a truth above the ceiling was a convention rather than a
measurement — and the convention has to be the one the product can deliver.

**What was rejected.**  (a) Scaling the truth inside the instrument makes the
yard a processed truth computed at measurement time.  (b) Exempting the
de-clipper breaks the file guarantee and the module's own contract, which
says it does not claim the original peak was higher.

**What it bought.**  The de-clip rule now holds with its thresholds
untouched: **14.95 dB closer to the truth and 1.37 dB of damage** (it was
5.32 dB and 3.25 dB).  The rule also exposed a module defect: the order-7
Hermite reconstruction of a flat top ran from 0.480x to 1.758x the true
apex, over-claiming the destroyed peak by 3.73 dB, which made the family
rule scale the whole programme down.  The order is 5 now.


## D1 — Parameters are identified by name

**Context.** Every plug-in format, every preset format and every UI control needs a
stable handle for a parameter. The obvious handle is a number. Numbers are compact, they
survive renaming, and hosts like them. Names are verbose, they break when a parameter is
renamed, and they collide with display conventions.

**Decision.** Parameter identity in the Auralis core is the **human-readable name**, e.g.
`Sensitivity`, `Threshold`, `Strength`. There are no numeric parameter ids anywhere in
`aurcore`. `ParamDesc.name` is the key; `ParamStore` is a name-keyed dictionary.

**Alternatives considered.**

* **A numeric id per parameter, generated from the table.** Rejected as the *core*
  identity. It would have made the plug-in side simpler — the host's parameter id would
  have been the core's parameter id — but it would have put a lookup table between the
  reader and the file. A preset file would have said `"p7": 3.0` instead of
  `"Sensitivity": {"Type": "Float", "Value": 3.0}`, and nobody could tell what a preset
  did without the table.
* **A numeric id *and* a name, with the number as the identity and the name as
  decoration.** Rejected: it doubles the surface that can drift, and it makes a rename a
  silent breaking change rather than a visible one.
* **Names in the core, numbers only at the host boundary.** This is what we did. The host
  parameter id is an adapter-side concept, and it lives in the identity record's
  `param_id_map`.

**Consequences.**

* Presets, chains and documents are readable and diffable without a lookup table.
  Opening a chain file in a text editor tells you what it does.
* Renaming a parameter is a breaking change — but it is a *visible* breaking change, and
  the typed-value envelope (D5) plus the loader's "skip unknown fields" behaviour mean an
  old file still opens with the old parameter ignored rather than rejected.
* The plug-in adapters have to map host numeric ids to core names. That mapping lives in
  exactly one place per host — the identity record — which is the right place for it.
* The host id is scoped to one plug-in, and one plug-in is one module, so the id only has
  to be unique inside that module (see D15). The **name** is what crosses formats.
* The cost is bytes in JSON. For a chain with a few hundred parameters, that is
  irrelevant.

**Evidence.** `aurcore/params.py` (module docstring and `ParamStore`); `aurcore/serial/typed_value.py`;
`plugins/identity.py` → `param_id_map`; `plugins/vst3/aurcore_c_api.h` — *"Parameters are
addressed BY NAME"*; `tests/test_plugin_abi.py::test_parameters_are_addressed_by_name`
and `::test_enum_parameters_carry_the_choice_string`.

---

## D2 — One core, many shells

**Context.** A restoration tool can exist as a standalone application, as plug-ins for
several DAWs, and as a library. Implementing the DSP three times guarantees that the
three copies drift. Implementing it once and wrapping it requires deciding what the core
is allowed to know.

**Decision.** `aurcore` is a pure DSP library. It knows about sample rates, channel
counts, numpy arrays, parameters and state. It does not know about files, sockets, HTTP,
plug-in formats, or the DOM. `aurserver` and the plug-in adapters are thin shells over
the same `Engine`.

**Alternatives considered.**

* **A native desktop application as the primary shell.** Rejected for the alpha: it means
  committing to a UI toolkit, a canvas stack and a distribution story before the
  product's behaviour has settled.
* **A plug-in-first product.** Rejected: a plug-in cannot show a spectrogram, a module
  browser or an undo stack, and the product's value is in exactly those. The plug-in is
  the second surface, not the first.
* **One shell, written well.** Rejected: the whole point of the plug-in track is that the
  same modules reach a DAW.

**Consequences.**

* The same chain renders identically in the browser, in a plug-in and in a test. There is
  one implementation of "what a De-Click does".
* The core is testable without a server, a browser or a network.
* `aurcore` gains a hard dependency rule: numpy only (scipy optional, always behind a
  numpy fallback).
* The core cannot do file I/O, so every shell must supply the audio. That is a small tax
  on each shell and a large saving on testability.

**Evidence.** `aurcore/api.py`; the layer table in [`ARCHITECTURE.md`](ARCHITECTURE.md) §1;
`tests/test_schema_consistency.py` (the registry conformance check).

---

## D3 — The parameter table is the single source of truth, and code is generated from it

**Context.** The same module description is needed in at least five places: the module
implementation, the registry, the server's `/api/modules`, the UI's control factory, and
the documentation. When those five are written by hand they drift, and the usual failure
is a UI knob that does nothing.

**Decision.** One canonical table — `aurcore/schema/params.tsv` — declares every module's
identity and every parameter. Everything else is derived:

```
params.tsv ──► aurcore.schema.load() ──► ModuleSpec ──┬──► module implementations
                                                      ├──► registry.ModuleInfo
                                                      ├──► /api/modules (server)
                                                      ├──► UI control factory
                                                      ├──► schema/aur_modules.h (C)
                                                      └──► docs/MODULES.md
```

**Alternatives considered.**

* **Declare the parameters in Python, next to the module.** Rejected: then the parameter
  set of a module lives in two places (the module and the catalogue), and the catalogue
  is the one the UI reads.
* **Generate the module implementations from the table.** Rejected: a table can describe
  a parameter; it cannot describe an algorithm.
* **Keep the table but hand-write the derived artefacts.** Rejected: the artefacts are
  exactly where drift hides. `tools/validate_schema.py` fails the build when a committed
  artefact differs from a fresh run.

**Consequences.**

* `aurcore.schema.validate_module(spec, descs)` compares an implementation against the
  table and returns a list of differences. The test suite asserts that list is empty. A
  module with a hidden knob fails the build instead of shipping a dead control.
* The UI gets its control type, range, unit and choice list for free, exactly.
* Adding a module means touching two files: the table and the implementation. Adding a
  parameter means touching the table and the implementation. Both are reviewable diffs.
* The cost is a build step. The toolchain is small — a table loader and five writers —
  and the generated artefacts are committed so a fresh checkout runs without a build.

**Evidence.** `aurcore/schema/params.tsv`; `aurcore/schema/__init__.py` → `load`,
`validate_module`; `tools/codegen_registry.py`; `tools/validate_schema.py` (four checks);
`tests/test_schema_consistency.py`. Run `python tools/validate_schema.py` — it prints the
module and parameter counts and reports drift.

---

## D4 — The canonical table is TSV

**Context.** The parameter table has to be reviewed in a diff, read by a human, parsed by
several tools (the runtime loader, the doc generator, the registry codegen) and kept in a
repository that also contains Python.

**Decision.** The canonical table is a **tab-separated file**,
`aurcore/schema/params.tsv`, with a fixed 13-column layout and `#` comments.

**Alternatives considered.**

* **JSON.** Rejected: it is multi-line, so a one-parameter change is a five-line diff,
  and it is noisy in a review. It also has no comments, and the table needs a header that
  explains the columns.
* **CSV.** Rejected for the same reason, plus quoting rules that make the `|`-separated
  enum choice list ambiguous.
* **A Python module.** Rejected: see D3 — a data artefact that is reviewable without
  running anything is worth more than the convenience of importing it.

**Consequences.**

* The table is a data artefact, not code. It can be diffed, grepped and reviewed without
  running Python, and a malformed row is obvious in the diff.
* TSV is unambiguous for values that contain spaces, commas, `|` and `&`. The enum choice
  list is `|`-separated inside a single field.
* The downside of TSV is that it is unforgiving: a missing tab silently shifts every
  following column. `aurcore.schema.load()` therefore rejects any row that does not have
  exactly 13 fields, so a broken table fails loudly at load time rather than producing a
  wrong document.
* TSV has no types. The four parameter types are encoded as text in a fixed column and
  parsed by one shared function, so there is exactly one place where a type name is
  interpreted.

**Evidence.** `aurcore/schema/params.tsv` (header comment); `aurcore/schema/__init__.py`
→ `_parse_type`, `_parse_value` and the 13-field check.

---

## D5 — A typed-value interchange format

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

**Alternatives considered.**

* **Plain JSON, with the type implied by the field.** Rejected: the moment a field's
  type changes — an `int` becomes a `float`, a string becomes an enum — every reader has
  to be updated, and the file cannot describe its own contents.
* **A binary format.** Rejected: it would make the files unreadable and unmergeable, and
  the sharing story depends on a preset being something a user can email and a colleague
  can read.
* **JSON with a per-format schema version.** Rejected as *sufficient*: the version helps
  the writer, but the reader still cannot tell an `int` from a `float` without the
  schema. The envelope carries the type with the value.

**Consequences.**

* Artefacts are self-describing: a reader that meets an unknown type can skip it rather
  than guess.
* Binary state — a learned noise profile, a filter history, a future neural model —
  travels in the same `"state"` slot as everything else, so there is one serialisation
  mechanism rather than three.
* A future schema change can add a type without breaking old readers.
* The cost is verbosity: every value carries a wrapper. For a preset file of a few
  kilobytes, that is irrelevant; for a large document with thousands of values it is
  noticeable but still acceptable, and the alternative — losing information on
  round-trip — is worse.

**Evidence.** `aurcore/serial/typed_value.py`; the format definitions in
`aurcore/serial/preset_json.py`, `chain_json.py` and `document.py`; the migrations in
`aurcore/serial/migrate.py`; `tests/test_serial.py`.

---

## D6 — numpy-first DSP, scipy optional

**Context.** The DSP core needs FFTs, STFTs, resampling, filters and loudness metering.
These have excellent numpy implementations and good scipy implementations. scipy is a
heavy dependency with a large binary footprint.

**Decision.** The core is numpy-first. numpy is a hard dependency; scipy is optional and
may only be used **behind a numpy fallback**. Every scipy call site has a pure-numpy path
that produces the same result within a stated tolerance.

**Alternatives considered.**

* **scipy required.** Rejected: it makes the core heavier to install and harder to embed,
  for a modest gain in speed on a handful of primitives.
* **numpy only, no scipy at all.** Rejected: some primitives (the resampler's design
  filter, the resampling windows) are both slower and less accurate when hand-rolled in
  numpy, and the code would be worse.

**Consequences.**

* The core installs cleanly anywhere numpy runs, including locked-down studio machines and
  embedded hosts.
* Some code paths are duplicated. That duplication is the price of the optional
  dependency, and it is contained to the `aurcore/dsp/` primitives.
* The fallback paths need numeric tests with explicit tolerances so the two
  implementations do not drift.
* Performance-critical paths can use scipy when it is there without the core ever requiring
  it.

**Evidence.** `aurcore/dsp/` (11 primitives); the numeric tests in `tests/test_dsp.py` and
`tests/test_dsp_layer.py`.

---

## D7 — The alpha UI is a web application

**Context.** Auralis needs a dense, professional interface: a module browser, a
spectrogram canvas, a waveform canvas, dozens of knobs, a chain strip, live parameter
feedback. It also needs to reach plug-in hosts later (D2). Building a native desktop UI
first means committing to a UI toolkit, a canvas stack and a distribution story before
the product's behaviour has settled.

**Decision.** The alpha UI is a **web application**: vanilla HTML, CSS and JavaScript, no
build step, served by `aurserver` as static files and driven by a JSON/WebSocket API. The
core is reachable only through that API — the browser shares no memory with the DSP.

**Alternatives considered.**

* **Electron / a native shell.** Rejected for the alpha: it adds a packaging problem and
  a second language runtime before the product's behaviour has settled.
* **A framework — React, Vue, Svelte.** Rejected: a build step would make "clone, run,
  open a browser" false, and it would add a dependency tree to a tool whose selling point
  is that it installs cleanly.
* **A native toolkit (Qt, JUCE).** Rejected for the same reason, and because the plug-in
  adapter would then share its UI code with the standalone — which would couple the two
  surfaces we deliberately kept separate.

**Consequences.**

* Zero build tooling: clone, install, run, open a browser. No bundler, no node, no
  transpiler, no lockfile to rot.
* The UI can be replaced. Because the core is only reachable over HTTP, a native shell
  can drive the same engine with a different front end and nothing in `aurcore` changes.
  That is the property that makes D2 cheap.
* A local HTTP server is required to use the tool, and it binds to loopback only.
* Canvas performance is browser-dependent; a very large file may need tiled rendering,
  which is a known follow-up.

**Evidence.** `aurui/index.html`; `aurui/js/` (fourteen ES modules, no bundler
configuration anywhere in the repository); `scripts/run_dev.ps1` and `run_dev.sh`; the
layer rule in [`ARCHITECTURE.md`](ARCHITECTURE.md) §1.

---

## D8 — The keyboard map is data, not code

**Context.** Shortcuts are usually written as `if (e.key === " ") …` scattered through the
event handlers. Then the help overlay is a second, hand-written list, and the two drift.
The overlay also has to follow the active locale.

**Decision.** The bindings live in one declarative table, `aurui/js/keymap.js` →
`COMMANDS`. Every entry carries an id, a label, a description, a **context** and a
binding specification. The resolver prefers the command of the focused region and falls
back to the global commands. The help overlay renders the very same table, and the
catalogue supplies the label and the description, so the overlay follows the locale.

**Alternatives considered.**

* **Key handlers in each module.** Rejected: the help overlay would be a second list, and
  the "no duplicate binding" rule would not be checkable.
* **A key-binding library.** Rejected: it would be a dependency in a project whose whole
  point is having none, and the table we need is forty lines.

**Consequences.**

* The documentation cannot drift away from the behaviour: the help overlay *is* the
  documentation.
* A command declares where it applies, so the same key can mean different things in
  different regions — `T` toggles the time selection in the view and cycles the
  inspector tabs in the panel.
* The matcher rules are explicit and testable: nothing is swallowed while the user types,
  and a command fires only when exactly the modifiers it expects are held.
* The cost is an indirection: a new shortcut is a table entry plus a handler, not a
  handler alone.

**Evidence.** `aurui/js/keymap.js` → `COMMANDS`, `parseBinding`, `matches`, `resolve`,
`describe`; `tests/test_ui_contracts.py` → `test_the_keymap_offers_every_required_command`,
`test_the_keymap_has_no_duplicate_binding_inside_a_context`,
`test_the_keymap_ignores_typing_and_foreign_modifiers`,
`test_the_keymap_resolves_the_command_of_the_focused_region`.

---

## D9 — The string catalogue is a plain dictionary per locale

**Context.** The interface needs more than one language, and the catalogue has to be
usable by a translator who does not read JavaScript.

**Decision.** `aurui/js/i18n.js` holds one plain dictionary per locale. `en` is the
reference; every other locale carries exactly the same key set. A missing key falls back
to English — never to the raw key — so a half-finished translation still produces a
usable interface. Markup is translated by attribute (`data-i18n`, `data-i18n-title`,
`data-i18n-aria`, `data-i18n-placeholder`).

**Alternatives considered.**

* **A gettext / `.po` workflow.** Rejected: it needs a compilation step and a tool the
  project would otherwise not need.
* **A JSON catalogue fetched at run time.** Rejected: it would break the "no network
  request at run time" rule and add a loading failure mode.
* **Interpolating the strings into the JavaScript at build time.** Rejected: that is a
  build step, and D7 rules those out.

**Consequences.**

* A translator can be handed a copy of one locale object and fill it in; nothing else in
  the code base has to change.
* The keyboard help table follows the locale, because the command labels and
  descriptions are catalogue entries too.
* The cost is that every string has to be written and reviewed — including the ones in
  the keymap — and a new string that nobody translated silently falls back to English.

**Evidence.** `aurui/js/i18n.js` → `CATALOGUES`, `t`, `setLocale`, `translateDocument`;
`aurui/index.html` (the `data-i18n*` attributes); `tests/test_ui_contracts.py` →
`test_every_locale_covers_the_same_keys`, `test_every_key_resolves_to_a_translation`,
`test_every_command_has_a_translated_label_and_description`.

---

## D10 — DSP-first approximations for the model-backed modules, behind a clean seam

**Context.** Several modules in the catalogue are conceptually model-backed: dialogue
isolation, music rebalancing. Shipping trained models in an alpha is not realistic — it
multiplies the artefact size, adds an inference runtime, makes results non-deterministic
across machines, and blocks offline use unless the model is bundled.

**Decision.** Every model-backed module ships a **deterministic DSP approximation** that
satisfies the identical contract: same parameter names, same ranges, same output shape,
same `learn()` semantics. The registry marks the module with `ml = True` so the UI and
the roadmap can track it. The learned artefact is **state**, not parameters, so it travels
through `serialize()` / `deserialize()` as a base64 blob.

**Alternatives considered.**

* **Ship the models now.** Rejected: the offline guarantee and the determinism guarantee
  both go, and the artefact size becomes the product's size.
* **Leave the model-backed modules out of the catalogue until the models exist.** Rejected:
  the parameter contract is part of the product's design and has to be settled while the
  table can still be changed cheaply.
* **A parameter that only makes sense with a model, added now.** Rejected by the rule
  below.

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
  parameter vocabulary generic so a model's controls can map onto the existing names.

**Evidence.** `aurcore/modules/base.py` → `ml`; `aurcore/registry.py` → `ModuleInfo.ml`;
`aurcore/modules/dialogue_isolate.py`, `aurcore/modules/music_rebalance.py`; the seam in
[`ARCHITECTURE.md`](ARCHITECTURE.md) §7. Check which modules carry the flag with
`python -c "import sys;sys.path.insert(0,'.');from aurcore import registry;print([m.code for m in registry.all() if m.ml])"`.

---

## D11 — No third-party product, vendor or trademark appears anywhere in the codebase

**Context.** A restoration tool inevitably needs parameters that other products also
have: a sensitivity, a threshold, a bandwidth, a de-esser. The temptation is to use the
incumbent's exact display names, because users recognise them. That is a legal liability
and it muddies the product's identity — and in a codebase that is meant to be clean-room,
it is a correctness problem as much as a legal one.

**Decision.** The codebase — source, comments, identifiers, UI strings, presets, tests,
filenames, documentation and metadata — contains **only our own names**. Parameters that
are common get **generic, descriptive** names: `Sensitivity`, `Threshold`, `Strength`,
`Bandwidth`, `Reduction`, `Attack`, `Release`, `Quality`, `Crossover`. Modules carry our
own names and our own stable codes (`AUR_DECLICK`). Identifier prefixes are `aur_`,
`Auralis`, `auralis`.

**Alternatives considered.**

* **Use the market's names for recognition.** Rejected: legal exposure, and it makes the
  product a derivative in the reader's mind.
* **Ban the names in the source but allow them in the UI.** Rejected: the UI string is
  the part users see, and it is the part that carries the legal risk.
* **A rename pass later.** Rejected: a rename that touches every file at once is a
  rename that never happens. The rule is cheaper to keep than to fix.

**Consequences.**

* Every user-visible string has to be written and reviewed rather than copied. That is
  slower up front and it is exactly the point.
* The engine has to build its own vocabulary and earn recognition for it. The generic
  names are deliberately the names an engineer would guess, which helps discoverability.
* The rule is mechanically checkable — a single scan over the tree must return nothing —
  and it is part of the definition of done for every task.
* Nothing from any reverse-engineering workspace is copied into this repository; that
  workspace is evidence, kept outside the tree.

**Evidence.** `tools/hygiene_check.py` (the scanner; the forbidden tokens are assembled
from fragments at run time so the scanner never flags itself);
`tools/validate_schema.py` → *no forbidden trademark token*;
`CONTRACT.md` §0. Run `python tools/hygiene_check.py` — it reports **0 findings** over the
whole tree (the file count moves as the tree grows; the finding count does not), and
`python tools/validate_schema.py` reports *no forbidden trademark token*. The
scanner found the legacy directory aliases, some absolute paths and three unlabelled
generated artefacts in an earlier pass; those are gone, and the rule now holds over the
whole tree.

---

## D12 — Offline-first, no telemetry, no network at runtime

**Context.** Audio restoration deals with unreleased recordings, private conversations and
client material. A tool that phones home, uploads, or requires an account creates a
confidentiality problem for its users and an operational burden for its authors. The
web-UI decision (D7) introduces a server, and a server can send data anywhere.

**Decision.** Auralis runs **entirely on the user's machine**. At runtime it makes **no
outbound network connections**. There is **no telemetry, no crash reporting, no analytics,
no account system and no auto-update check**. The local server binds to loopback only. The
UI ships no web fonts and no remote assets, so rendering the UI never triggers a network
request. The project depends on no external service at runtime.

**Alternatives considered.**

* **Anonymous telemetry, opt-out.** Rejected: for this product the data being processed
  is the sensitive part, and a telemetry endpoint is a permanent obligation to protect
  something nobody asked to have collected.
* **A licence server.** Rejected: it would make the tool stop working when the network
  does, which is exactly the failure mode the offline promise exists to avoid.
* **A hosted render service for the heavy modules.** Rejected: it would break the offline
  promise for the modules that matter most — the separation and the spectral work.

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

**Evidence.** `scripts/run_dev.ps1` and `run_dev.sh` (both bind to `127.0.0.1`);
`aurui/css/tokens.css` (the font stacks are fallbacks, not bundled fonts);
`aurserver/app.py` (no outbound client anywhere in the server).

---

## D13 — One adapter template per format; the module is stamped in at build time

**Context.** The obvious way to ship a plug-in per module is to write a plug-in per
module. That is a per-module parameter list, a per-module page table, a per-module
build target, and a compile every time a parameter changes.

**Decision.** There is **one adapter template per plug-in format**, and the module a
binary implements is decided at build time by stamping an identity record into a reserved
slot inside the linked binary. Adding a module to the canonical table therefore yields a
plug-in for every format, with no adapter work and no recompilation of the template.

**Alternatives considered.**

* **One plug-in per module, compiled from one source with a `-D` per module.** Rejected:
  it needs a compile per module per change, and the parameter list still has to be
  generated per module.
* **One plug-in that hosts every module and switches at run time.** Rejected: hosts expect
  one class id per effect, and a single mega-plug-in cannot be inserted per module.
* **Read the parameter list from the core at run time through a versioned callback.** This
  is what the reference architecture does, and it is a real alternative. Rejected for us
  because it requires a live core connection to enumerate the plug-in's own controls,
  which is exactly the dependency we are trying to remove.

**Consequences.**

* Adding a module is a table edit and a rebuild. Nothing under `plugins/` is edited.
* The adapter never has to be recompiled when a module changes. The identity record is
  data, not code.
* The VST3 format still requires a per-module compile, because the plug-in wrapper bakes
  the class id and the class name into the binary at compile time and they cannot be
  patched afterwards without breaking the binary's identity in a way that is hard to see.
  So the build compiles the same template once per module, with those two values taken
  from the generated identity record. The *module* is still chosen by the stamped record.
* The stamp is a byte-level patch of the linked file. It lives in read-only data with a
  constant initialiser, so the linker stores it in the file — a zero-initialised object
  would go to `.bss`, which is not part of the file on disk and could not be patched at
  all.
* **The slot has to be read through a `volatile` pointer.** A compiler is allowed to
  assume that the contents of a `const` object never change, so it will fold a read of
  the slot back to its link-time value and throw the stamped record away in an optimised
  build. The shipped adapter reads it that way; the framework-based one does not, and is
  therefore exposed to the same folding.
* The cost is a build step, and the fact that the identity record is a second
  representation of the module's identity that has to stay in step with the table. It
  does not drift by construction: it is derived from the table and validated on the way
  in and on the way out.

**Evidence.** `plugins/identity.py` (derivation, stamping, read-back, validation);
`plugins/identity.schema.json`; `plugins/vst3native/AuralisVST3.cpp` → `makeIdentitySlot`
and `readEmbeddedIdentity` (the `volatile` read); `plugins/vst3/AuralisPlugin.cpp` →
`makeIdentitySlot`;
`plugins/vst3/build_identity.py` → `header`, `stamp`, `stamp-all`, `manifest`, `verify`,
`list`; `plugins/vst3native/CMakeLists.txt` (the per-module loop reads the module list
from the registry, so a new module needs no change to the build file);
`scripts/build_vst3.py` (the whole family, one module after another);
`plugins/README.md`.

---

## D14 — The identity record is embedded in the binary, and it is derived, not written

**Context.** The stamped record has to carry the module's identity: its code, its display
name, its product name, its host class id, its effect ids and its parameter id map. If
any of that were written by hand, it would be a second source of truth and would drift.

**Decision.** The record is derived deterministically from the canonical table by
`plugins/identity.py` and is validated against `plugins/identity.schema.json` on the way
in (stamping) and on the way out (reading back). The VST3 class id is not a separate
scheme: it is derived the way the plug-in wrapper derives it, from the manufacturer code
`AURA` and the module's four-character product code, so the value in the record *is* the
class id the shipped binary reports.

**Alternatives considered.**

* **A separate registry of class ids and product codes.** Rejected: a second list to
  keep in step, and a class id that does not match what the binary reports.
* **A hash of the module code.** Rejected: the class id would not match the id the
  plug-in wrapper bakes in, so the host would see one id and the record another.
* **A hand-written JSON file per module, committed.** Rejected: it is the second source
  of truth this whole decision exists to avoid.

**Consequences.**

* A module has one identity across formats: the same four-character product code is the
  VST3 plugin code (and therefore part of the VST3 class id) and the AAX product id.
* The product code is assigned over the *whole* registry, so a module keeps the same code
  no matter which subset of modules is asked for. When two modules want the same natural
  code, the first in registry order keeps it and the others get a code derived from a
  hash of their own module code — which never takes the natural code of another module.
* The class ids are *locally generated*. A shipping product has to register them with the
  plug-in format bodies, and the ones produced here must never be re-used for a different
  module.
* The cost is a hard constraint on renaming: a rename changes the display name, not the
  product code.

**Evidence.** `plugins/identity.py` → `derive_fuid`, `_product_codes`, `identities_for`,
`validate_identity`; `plugins/identity.schema.json`; `plugins/aax/AAX_ADAPTER_NOTES.md`
(the identifier model). Read a record back with
`python plugins/vst3/build_identity.py verify --binary <path-to-a-built-vst3>`.

---

## D15 — A host parameter id is the parameter's position inside its own module

**Context.** Hosts want a compact integer per parameter. The core wants a name. Something
has to bridge them, and the bridge has to be stable across rebuilds — otherwise a
session written by one build does not load in the next.

**Decision.** The host parameter id is the parameter's **index within its own module**,
`0 … n−1`, in the order the parameters appear in the canonical table. The index is
generated (`aurcore/schema/generated.py` → `PARAM_ID`), it is mirrored into the C header
as `AUR_PARAM_<MODULE>_<n>` (`aurcore/schema/aur_modules.h`), and the identity record
carries the map from host id to core parameter name in its `param_id_map`.

The rule that keeps the ids stable is **local**: a parameter's id only changes when a
parameter is inserted *before* it in the *same* module. Nothing that happens to another
module can renumber it.

> **Correction.** This entry used to say the ids were *registry-wide* positions, with a
> `PARAM_BASE` offset per module. That is not what the code does: `PARAM_ID` and
> `PARAM_INDEX` are the same per-module object, there is no `PARAM_BASE` in the generated
> registry, the C header names the constants per module, and the identity record's
> `param_id_map` runs from 0 for every module. The description has been corrected to
> match the code.

**Alternatives considered.**

* **A registry-wide id space** — one counter for the whole catalogue, so that a module's
  first parameter is not id 0. Rejected: the id of a parameter would then depend on how
  many parameters the modules *before* it happen to have, so adding one parameter to any
  early module would renumber every parameter of every later module and break every
  stored session. The per-module rule confines the damage to the module that changed.
  (This is the scheme the earlier version of this entry described; it is not the one the
  code implements.)
* **A hash of the parameter name.** Rejected: stable, but not human-readable, and it
  collides in principle.
* **A global counter stored in a file.** Rejected: a mutable file in the build is one more
  thing to keep in step, and it is not derived from the table.

**Consequences.**

* A session written by one build loads in the next, and it also loads in the build after
  the registry grew: adding or removing a module, or growing another module, moves no id.
* The ids are readable in the C header and in the identity record, which makes a mismatch
  between the two obvious rather than mysterious.
* Every id-bearing record carries the module code next to the id (the identity record, the
  AAX page table, the C header macro), so a pair of (module, id) is unambiguous.
* **An id is only meaningful inside one plug-in.** One Auralis plug-in implements exactly
  one module, so the id is unambiguous there — but id `1` is `Sensitivity` in De-Click and
  `Gain` in Breath Control. An automation lane that has to survive a change of plug-in is
  therefore written against the **name**, which is what the identity record and the
  presets carry.
* The cost is a discipline: the table has to be edited append-only within a module. That
  is a rule people break by accident, so it is written down here and in
  `plugins/README.md`. The drift gate (`python tools/codegen_registry.py --check`) reports
  every id that moved, so a renumbering cannot reach a surface unnoticed.

**Evidence.** `aurcore/schema/generated.py` → `PARAM_ID` (which is `PARAM_INDEX`);
`aurcore/schema/aur_modules.h` → `AUR_PARAM_AUR_DECLICK_0 … _4`,
`AUR_PARAM_AUR_MOUTHDECLICK_0 … _3`; `aurcore/schema/modules.json` → the `id` of a
parameter; `plugins/identity.py` → `param_index` (its `source`
string is `aurcore/schema/generated.py (PARAM_ID)`), `module_param_ids`;
`plugins/vst3/build_identity.py` → `render_generated_header`;
`plugins/README.md` → *Parameter id stability*;
`tests/test_codegen.py` → *the ids are stable*. Check it with

```
python -c "import sys;sys.path.insert(0,'.');from aurcore.schema import generated;
           print(generated.PARAM_ID['AUR_SPECTRALEDITOR'])"
```

---

## D16 — The core is embedded behind a narrow C ABI, not re-implemented in C++

**Context.** A plug-in has to run inside a DAW, on the DAW's audio thread, in the DAW's
process. The DSP is written in Python. A host will not load a Python interpreter, and a
C++ re-implementation of the DSP would be a fork: two implementations of the same
algorithms, guaranteed to drift.

**Decision.** The adapter never touches the core's internals. It calls **six** C entry
points and **twelve** extensions, declared in `plugins/vst3/aurcore_c_api.h`, and nothing
else — **eighteen exports** in all, at contract version 3. The implementation embeds
CPython and drives `aurcore/abi_bridge`, which does the numpy work. The header is the
contract, and it is pinned by conformance tests that load the library the way a host
does.

The six entry points are `aur_engine_create`, `aur_engine_add_module`,
`aur_engine_set_param`, `aur_engine_process`, `aur_engine_destroy` and `aur_shutdown`.
The last one is the explicit interpreter shutdown of D22.

**Alternatives considered.**

* **Re-implement the DSP in C++ now.** Rejected for now, kept as the production track.
  It would fork the algorithms, and the two copies would drift — the exact failure the
  single core exists to prevent. It is the right *destination* and the wrong *first
  step*: a C++ core can be written against the same header later, with no change to the
  header and therefore no change to the adapter.
* **Let the adapter call the Python API directly (embed CPython in the adapter).** This is
  what the implementation does, but through the C ABI rather than by exposing the Python
  objects. The difference matters: the ABI is a narrow, stable, versioned surface with
  defined ownership, error codes and units, so an adapter written against it survives a
  change to the core's internals.
* **Expose the core as a network service.** Rejected: it would put the audio on a socket,
  which is both slower and a confidentiality problem, and it would break the offline
  promise (D12).

**Consequences.**

* There is one implementation of "what a De-Click does", and the plug-in and the browser
  are demonstrably the same code.
* The contract is explicit: parameters are addressed **by name**, values are in **natural
  units**, an enum value is its **choice string**, the audio buffer is **planar**, success
  is `0` and failure is a negative `AurError`, and the engine is not thread-safe.
* The cost is that the plug-in carries a Python runtime. The adapter binary itself is
  **184 kB**, and the bundle it ships in is **188 kB**: the core and the interpreter are
  shared by the whole family and live once, in the runtime directory next to the
  bundles. The 48 bundles are **8.6 MB** together and the runtime that carries the
  interpreter is **62.9 MB** — a package of **75.0 MB** in all. A shipping plug-in does
  not want that, which is why the production C++ core is on the roadmap.
* The production track is safe because the header is the contract: a native core
  implements the same header, the conformance tests are the acceptance criterion, and
  functional parity — not bit-exact parity — is the goal.

**Evidence.** `plugins/vst3/aurcore_c_api.h` (the six entry points, the twelve extensions,
the threading and error rules in the header comment); `plugins/abi/aurcore_abi.cpp`;
`aurcore/abi_bridge.py`; `plugins/abi/build_abi.py`;
`plugins/abi/build_main/aurcore_abi.def` (the 18 exported symbols, generated from the
header);
`tests/test_plugin_abi.py` (**42** conformance tests: version, create/destroy, bad
arguments, add module, unknown module, parameters by name, enum choice strings, unknown
parameter, process, transparent bypass, latency, state round trip, state through the ABI,
the header/export-list agreement, the export list derived from the header, a reproducible
build, every failure reporting, no pending exception, the message of the library, the
thread rule, the shutdown leaving the host's interpreter alone, and loading and
unloading the library in a loop).

---

## D17 — The parameter-label truncation is a straight prefix cut

**Context.** A host shows a parameter name in a narrow strip, and long names have to be
shortened. "Shorten intelligently" — by word, by abbreviation, by rebalancing — sounds
better than "cut". It is also more code, and the result is not what a host expects if
the host itself does the cut.

**Decision.** The truncation is a **straight prefix cut**: `ladder[s] == label[0:s]` for
`s` from 1 to `len(label) - 1`. The behaviour is locked by a golden regression test over
113 recorded ladders.

**Alternatives considered.**

* **A word-aware rebalance** — cut at a word boundary, keep the last word, or drop a
  vowel. Rejected: it produces labels a host does not recognise, and it is not what the
  host does when it has to truncate.
* **Leave the truncation to the host.** Rejected: the host has no way to know that a
  parameter is called `Output Clicks Only` and that the interesting part is at the end.

**Consequences.**

* The rule is one line and it is testable against a real corpus.
* A parameter name that is too long for its host strip is shortened predictably, and the
  ladder for it can be generated and shipped with the identity record.

**Evidence.** `tools/extract_truncation_ladders.py` (the rule in its docstring);
`tests/fixtures/truncation_ladders.json` (`count: 113`, `rule: ladder[s] === label.slice(0, s)`);
`tests/test_truncation_ladder.py`.

---

## D18 — The undo history lives in the client and replays inverse operations

**Context.** Undo and redo need a history of edits. The edits happen in the client; the
state lives on the server. Either side can hold the history, and the choice determines
what an undo means when the server's state has moved on.

**Decision.** The history is a command stack in the **interface** (`aurui/js/session.js`
over `aurcore/undo.py`). Every step is carried out on the server through the ordinary chain
routes, so the server stays the single owner of the chain and the client never tries to
reverse a state on its own. A command addresses a module by its **instance**, not by a
fixed index, so removing the module in front of it does not make the command point at the
wrong module. Two consecutive changes of the same parameter merge when they are less than
a second apart, so a knob drag is one entry rather than one per frame. The stack is
bounded at 200 entries.

The inverse operation is the command's own counterpart — a command knows how to undo
itself, so the stack replays the inverse rather than storing a snapshot of the server's
state. The session controller is loaded by `app.js` and mounted in the top bar, and it
talks to the session routes (`POST /api/session/save`, `POST /api/session/load`,
`GET /api/session/list`) as well as to the chain routes.

**Alternatives considered.**

* **The history on the server, one undo route.** Rejected: it adds two routes and a
  server-side history that duplicates state the client already has to model for the
  toolbar, and it makes "undo" a remote operation with a latency story.
* **The client reverses the state locally.** Rejected: the client would have to know the
  server's canonical order and every module's internal state, and a drift between the two
  would be invisible until something rendered wrong.
* **A command per keystroke with no merging.** Rejected: a knob drag would fill the
  history with hundreds of entries.
* **A snapshot per step.** Rejected: the history would grow with the size of the chain
  instead of with the number of edits, and an undo would have to trust the snapshot.

**Consequences.**

* The server API stays small: no undo routes, no history routes, and the chain routes
  stay the single write path.
* The toolbar's undo/redo labels come from the same stack, so they cannot disagree with
  what undo would do.
* The history is per client, so it does not survive a page reload; the merge window is a
  heuristic; and the session document carries only a **summary** of the history, not the
  history itself.

**Evidence.** `aurcore/undo.py` → `Command`, `Transaction`, `UndoStack`,
`DEFAULT_DEPTH_LIMIT` (200), `DEFAULT_MERGE_WINDOW` (1.0 s), and the seven command types
`AddModuleCommand`, `RemoveModuleCommand`, `ReorderModuleCommand`, `SetParamCommand`,
`ToggleBypassCommand`, `ChangeRegionCommand`, `LoadPresetCommand`;
`aurui/js/session.js` → `MERGE_WINDOW_MS`, `HISTORY_LIMIT`;
`aurui/js/app.js` → `initSession` and `mountSessionBar`;
`aurui/js/keymap.js` → `session.undo` (`Ctrl+Z`), `session.redo` (`Ctrl+Shift+Z`);
`tests/test_undo.py`; `tests/test_session_ui.py`.

---

## D19 — The session document is a self-describing JSON file with a content hash

**Context.** A session is the audio, the chain, the view state and the markers. It has to
be saved, reopened and continued, and it has to notice that the audio underneath it has
changed.

**Decision.** The session document is a plain JSON file in the `auralis.document` format:
the audio reference (path, sample rate, channels, frame count and a content hash), the
chain (the ordinary `auralis.chain` document), the view state, the markers and a summary
of the history. The content hash digests the **first and the last megabyte** of the file
together with its size and modification time. Loading a document runs the migrations, so
a document written by an older build is upgraded on the way in.

**Alternatives considered.**

* **Hash the whole file.** Rejected: hashing a 500 MB recording on every save is
  wasteful, and the size and the modification time catch the common case.
* **Hash nothing.** Rejected: a session that silently points at different audio is worse
  than no session.
* **A binary session bundle (audio plus state).** Rejected: it duplicates the audio and
  makes the file unusable in a diff.

**Consequences.**

* A session is readable, diffable and shareable, and the file format is open.
* The hash is a fingerprint, not a proof: it catches a replaced file, not every possible
  change.
* The view state is a free-form dictionary — the known keys are kept, everything else is
  preserved — so a newer build can add fields without breaking an older one.

**Evidence.** `aurcore/document.py` → `DOCUMENT_FORMAT`, `HASH_SAMPLE_BYTES`,
`content_hash`, `file_signature`, `build_document`, `save_document`, `load_document`,
`document_migrations`; `aurcore/serial/migrate.py`; `tests/test_document.py`.

---

## D20 — The table, the code and the documentation are checked against each other

**Context.** Three artefacts describe the same thing: the canonical table, the module
implementations, and the generated documentation. Any two of them can drift.

**Decision.** One validator checks the whole set, and the build fails on drift. The
generated artefacts are committed, and a committed artefact that differs from a fresh run
of the generator is a failure, not a warning.

**Alternatives considered.**

* **Generate at run time.** Rejected: the server would have to parse the table on every
  start, and the committed artefacts would not exist for anyone who reads the repository
  without running it.
* **Trust the tests.** Rejected: the test suite checks the *modules* against the table,
  but only the validator checks the *generated artefacts* against the generator.
* **Warn on drift.** Rejected: a warning is a warning until the day nobody reads it.

**Consequences.**

* The five generated artefacts cannot be hand-edited by accident, and a change to the
  table that is not followed by a regeneration fails the build.
* The validator is also where the naming rule and the static rules live, so there is one
  place that knows what the table is allowed to contain.

**Evidence.** `tools/validate_schema.py` — four checks: live registry conformance, JSON
Schema conformance, codegen drift, and the static registry rules (unique codes, unique
parameter names, enum parameters carry choices, defaults inside their range, no
whitespace in names, no forbidden trademark token). Run `python tools/validate_schema.py`.

---

## D21 — The VST3 adapter is written against the SDK, not on a framework

**Context.** The VST3 format has an SDK and a set of frameworks that sit on top of it.
Writing the adapter on a framework is the comfortable route: the framework supplies the
plug-in wrapper, the parameter plumbing, the state handling and the editor scaffolding.
Writing it straight against the SDK is the bare route: the adapter owns the four
interfaces the format asks for, and nothing else.

The two routes are not interchangeable *in this repository*, because the versions
available do not line up:

| Framework | The SDK layout it wants |
|---|---|
| 6 | the 3.6 era SDK (it vendors one) |
| 7, 8 | a 3.7 era layout that no SDK tag provides |

The SDK that is actually in the tree is **3.8.1**, in the new split layout:
`vendor/vst3sdk/pluginterfaces` and `vendor/vst3sdk/public.sdk` are two separate trees,
and the SDK's `base/` module is not part of it.

That rules out the SDK's own helper classes — `basewrapper`, `SingleComponentEffect`,
`EditControllerEx1` and the rest all include `base/source/fobject.h`, which is not in
the 3.8.1 split layout. The adapter therefore implements the four interfaces itself:

```
IPluginFactory    the class factory the host asks for first
IComponent        the bus layout and the state
IAudioProcessor   the audio path
IEditController   the parameter list and the parameter values
```

**Decision.** The shipped adapter is `plugins/vst3native/AuralisVST3.{h,cpp}`, written
**directly against the VST3 SDK**, with no framework in between. It implements the four
interfaces above, plus the optional `IEditController2` and the `IConnectionPoint`, in
one object — the "combined component" shape the SDK's own `SingleComponentEffect` uses,
which is the right shape for a plug-in that is a single module.

**Alternatives considered.**

* **Keep the framework-based adapter as the shipped one.** Rejected on evidence, not on
  taste: it needs a JUCE checkout *and* a JUCE-compatible SDK layout at the same time,
  and the two do not line up. The framework-based adapter stays in `plugins/vst3/` as an
  alternative and a reference for the state format; it is not the shipped path.
* **Write a compatibility shim for the missing SDK module.** Rejected: it would mean
  carrying a patched copy of the SDK's `base/` module in this repository, which is both
  a maintenance burden and a licensing question.
* **Wait for a framework release that supports the 3.8.1 split layout.** Rejected as a
  plan: it has no date, and the alpha needs a plug-in now.
* **Implement only the two interfaces a plug-in strictly needs.** Rejected: the format
  requires the factory, the component, the audio processor and the edit controller.

**Consequences.**

* The adapter is **178 kB** of C++ and depends on nothing but the SDK's headers and the
  core's C ABI. There is no framework to license, vendor or keep in step.
* The adapter's own source of truth is the SDK, so the format's rules are visible in the
  code rather than hidden behind a framework's conventions.
* The cost is that the SDK's conveniences have to be written by hand — the identity slot,
  the state container, the parameter list, the class factory. The parameter values that
  the framework would have pushed through a connection point are pushed through one here
  as well, as a message the plug-in makes itself.
* The framework-based adapter and the shipped one have to agree on the **state format**,
  so a session saved by one loads in the other. That is why the `auralis.state.v1`
  container is a shared convention and not an implementation detail.

**Evidence.** `plugins/vst3native/README.md` → *Why a second adapter*;
`plugins/vst3native/AuralisVST3.h` (the four interfaces and the reason);
`plugins/vst3native/AuralisVST3.cpp` → `AuralisEffect`, `AuralisFactory`;
`plugins/vst3native/CMakeLists.txt` (the one target, the SDK's own `pluginterfaces`
implementation files, the generated parameter table and the two self-checks);
`vendor/vst3sdk/` (the split layout);
`plugins/vst3/AuralisPlugin.{h,cpp}` (the framework-based alternative).

---

## D22 — The embedded interpreter is shut down explicitly

**Context.** The core's C ABI embeds CPython (D16). A plug-in loads that library into the
host's address space, and the host unloads the module at a moment of its own choosing.
CPython's own exit handler runs during that unload, and it walks structures that are
already being torn down — which is how an access violation at unload time happens.

**Decision.** The ABI has an explicit shutdown entry point, `aur_shutdown(void)`, and
the adapter calls it when its **last plug-in object** goes. Ending the interpreter while
everything is still alive leaves the interpreter's exit handler nothing to do.

The call is explicit rather than automatic because the same library is used inside a
program that embeds it — the application, the test suite — and there the interpreter
belongs to that program. Ending it would take the program down with it.

**Alternatives considered.**

* **Let the interpreter end itself when the process ends.** Rejected: in a host, "when
  the process ends" is "while the module is being unloaded", which is exactly the moment
  the exit handler is unsafe.
* **Never initialise the interpreter at all** (a native core). Rejected for now: it is
  the production track (D16, [`ROADMAP.md`](ROADMAP.md) §2.6), not the alpha's answer.
* **Refuse to load the plug-in inside a host.** Rejected: the plug-in is the product.

**Consequences.**

* A host that loads and unloads the plug-in does not crash on the way out. This was one
  of the defects the independent host found: *"the module faulted while the component was
  being released"*.
* The shutdown is counted: the adapter keeps the number of live plug-in objects and only
  the last one shuts the interpreter down.
* The library keeps working inside a host program: `aur_shutdown` is a call the adapter
  makes, not something the library does on load.

**Evidence.** `plugins/vst3/aurcore_c_api.h` → `aur_shutdown` (with the reason in the
comment); `plugins/abi/aurcore_abi.cpp` → `aur_shutdown`;
`plugins/vst3native/AuralisVST3.cpp` → the object counter and the call in the
destructor; `tests/test_plugin_abi.py` → `test_the_shutdown_leaves_the_hosts_interpreter_alone`,
`test_a_host_that_is_not_python_can_unload_the_library`,
`test_a_host_that_is_not_python_can_load_the_library_again`;
`qa/vst3host/` (the independent host, which faults on a bad teardown).

---

## Appendix — Where each decision shows up

| Decision | The file that carries it |
|---|---|
| D1 Parameters by name | `aurcore/params.py`, `aurcore/serial/typed_value.py`, `plugins/vst3/aurcore_c_api.h` |
| D2 One core, many shells | `aurcore/api.py`, `aurcore/registry.py` |
| D3 One table, generated | `aurcore/schema/params.tsv`, `tools/codegen_registry.py` |
| D4 TSV | `aurcore/schema/params.tsv`, `aurcore/schema/__init__.py` |
| D5 Typed values | `aurcore/serial/typed_value.py`, `aurcore/serial/migrate.py` |
| D6 numpy-first | `aurcore/dsp/` |
| D7 Web UI, no build step | `aurui/`, `aurserver/app.py` |
| D8 Keyboard map as data | `aurui/js/keymap.js` |
| D9 Catalogue as data | `aurui/js/i18n.js` |
| D10 DSP-first ML seam | `aurcore/modules/base.py`, `aurcore/registry.py` |
| D11 No third-party names | `tools/hygiene_check.py`, `CONTRACT.md` §0 |
| D12 Offline, no telemetry | `scripts/run_dev.ps1`, `aurcore/` |
| D13 One template, stamped | `plugins/identity.py`, `plugins/vst3native/` |
| D14 Identity is derived | `plugins/identity.py`, `plugins/identity.schema.json` |
| D15 Stable host ids, per module | `aurcore/schema/generated.py`, `aurcore/schema/aur_modules.h` |
| D16 Narrow C ABI | `plugins/vst3/aurcore_c_api.h`, `plugins/abi/`, `aurcore/abi_bridge.py` |
| D17 Prefix-cut truncation | `tools/extract_truncation_ladders.py`, `tests/fixtures/truncation_ladders.json` |
| D18 Interface-side history | `aurcore/undo.py`, `aurui/js/session.js` |
| D19 Session document | `aurcore/document.py` |
| D20 The gates | `tools/validate_schema.py`, `qa/verify_app.py`, `tests/` |
| D21 The adapter is written against the SDK | `plugins/vst3native/AuralisVST3.{h,cpp}`, `plugins/vst3native/CMakeLists.txt` |
| D22 The interpreter is shut down explicitly | `plugins/vst3/aurcore_c_api.h`, `plugins/abi/aurcore_abi.cpp` |
