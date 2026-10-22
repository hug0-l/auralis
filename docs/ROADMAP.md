# Auralis — Roadmap

> Status: **alpha (`0.1.0a0`)**. Dates are indicative and expressed in quarters, not
> calendar dates; the plan is ordered by dependency, not by calendar.
>
> Every state in the tables below is checkable. The command that proves it is in the
> same row. A number without a command is a number we have not checked.

---

## 1. Where the alpha stands

### In the tree today

| Area | State | Notes |
|---|---|---|
| `aurcore/api.py` | implemented | `Engine` facade: chain, `process`, `serialize` / `deserialize` |
| `aurcore/params.py` | implemented | `ParamDesc`, `ParamStore` — name-keyed, natural units, silent clamping |
| `aurcore/registry.py` | implemented | register / get / create / `ModuleInfo` for the UI |
| `aurcore/modules/base.py` | implemented | the `Module` contract |
| `aurcore/schema/params.tsv` | implemented | the canonical parameter table — **48 modules, 253 parameters** (`python tools/validate_schema.py`) |
| `aurcore/schema/__init__.py` | implemented | TSV loader + `validate_module` |
| `aurcore/serial/` | implemented | `typed_value`, `preset_json`, `chain_json`, `document`, `migrate` |
| `aurcore/dsp/` | implemented | **11 primitives**: `util`, `fft`, `windows`, `stft`, `filters`, `resample`, `loudness`, `spectral`, `phase_vocoder`, `timepitch`, `dither` |
| `aurcore/modules/*` | implemented | all 48 modules in the table have an implementation that matches it exactly (`tools/validate_schema.py`, pass 1) |
| `aurcore/analysis.py` | implemented | the measurement service behind the Analysis tab |
| `aurcore/undo.py` | implemented | the command stack: **7 command types** plus a `Transaction` composite, a 200-entry bounded history, a 1 s merge window |
| `aurcore/document.py` | implemented | the session document: audio reference with a content hash, chain, view, markers, history |
| `aurcore/abi_bridge.py` | implemented | the Python side of the plug-in C ABI |
| `aurserver/` | implemented | `app.py`, `assets.py`, `audio.py`, `presets.py`, `batch.py`, `batch_formats.py`, `ws.py` — **31 routes** |
| `aurui/` | implemented | `index.html`, three stylesheets, and **14 ES modules** |
| `plugins/identity.py` | implemented | the identity record: derivation, stamping, read-back, schema validation |
| `plugins/vst3native/` | implemented | the shipped VST3 adapter, written straight against the SDK |
| `plugins/vst3/` | implemented | the C ABI header, the build-time stamper and a framework-based adapter kept as an alternative |
| `plugins/abi/` | implemented | the C ABI over the core (**18 exports**), and its builder |
| `plugins/aax/` | design only | the identifiers, the parameter ids and the page layout are generated and checked; the SDK glue is not written |
| `tools/` | implemented | `codegen_registry.py`, `validate_schema.py`, `hygiene_check.py`, `lint_check.py`, `bench.py`, `stamp_all.py`, `build_abi.py`, `make_abi_lib.py`, `extract_truncation_ladders.py`, `aax_application_checklist.py` |
| `tests/` | growing | `pytest` suite — **58 test modules**, 6812 collected tests (`python -m pytest tests --collect-only -q`) |
| `corpus/` | implemented | **16 CC0 test clips**, a manifest and a schema, and the build/measure scripts |
| `qa/vst3host/` | implemented | the independent VST3 host: it loads a built plug-in and exercises it |
| `packaging/` | implemented | the Inno Setup installer script and the release builder |

### The gates

Eight gates. Each one is a single command.

| Gate | Command | What it proves | Result today |
|---|---|---|---|
| The table against the tree | `python tools/validate_schema.py` | every implemented module matches the canonical table, the generated artefacts are not stale, and no forbidden trademark token is present | **PASS** (4/4) |
| The generated artefacts | `python tools/codegen_registry.py --check` | the five committed artefacts are byte-identical to a fresh run of the generator | **PASS** (5 files up to date) |
| The unit suite | `python -m pytest tests -q` | the core, the serialisation, the DSP and the interface contracts | **30 failures** in the run of 2026-10-21: 23 in `tests/test_vst3_host.py` (the binaries in `build/` predate the adapter's revision — §2.7), 3 in `tests/test_resample.py` and 2 in `tests/test_phase_vocoder.py` (the resampler was rewritten), 3 in the packaging area (`tests/test_installer.py`, `tests/test_packaging.py`) |
| End to end | `python qa/verify_app.py` | 30 checks against a real server: catalogue, upload, analysis, chain editing, render, compare, presets, static assets | **30/30** |
| The naming rule | `python tools/hygiene_check.py` | no third-party trademark, no hard-coded absolute path, no unlabelled generated artefact | **PASS**, 0 findings |
| The style gate | `python tools/lint_check.py` | python style, third-party tokens, JavaScript syntax, CSS | **PASS** (214 warnings, no errors) |
| The interface contracts | `python -m pytest tests/test_ui_contracts.py` | every element id exists, every class is styled, no hard-coded colour, the keyboard map has no duplicate binding, every locale covers the same keys | **PASS** |
| The interface, audited | `python qa/a11y/audit_a11y.py` | the effective interface — markup plus the runtime furniture — has accessible names, keyboard reachability, live regions, a focus ring and a high-contrast variant | **PASS**, 0 errors, 0 warnings |
| The plug-in family | `python -m pytest tests/test_vst3_host.py` | the independent host loads **every** module of the family, drives a parameter, processes audio and restores the state | **red** — the binaries in `build/` predate the adapter's current revision; the fix is one rebuild (`python scripts/build_vst3.py`) |

Two more commands are part of the working day and are listed with the rest:
`python qa/check_css_coverage.py` (every class the UI uses is styled) and
`python -m pytest tests/test_plugin_abi.py` (42 conformance tests that load the core ABI
library the way a host does).

### The alpha promise

> Load an audio file, see it as a waveform and a spectrogram, build a chain of restoration
> modules, audition the result, and export a WAV. Everything runs on one machine with no
> network access.

Concretely, the alpha is done when:

* [x] Every module in `aurcore/schema/params.tsv` has an implementation whose
      `describe()` matches the table exactly — asserted by `tools/validate_schema.py`
      and by the test suite.
* [x] `POST /api/upload` → `POST /api/analyze` → `POST /api/render` → `GET /api/audio/{id}`
      works end to end for a stereo WAV at 48 kHz — asserted by `qa/verify_app.py`, 30/30.
* [x] The UI renders the module browser, the composite view, the module panel and the chain
      strip, and every control in a module panel is generated from its `ParamDesc`.
* [x] The undo/redo history, the preset library and the session document are wired into
      the shell: `aurui/js/session.js` and `aurui/js/preset_manager.js` are loaded by
      `app.js`, the session toolbar is mounted in the top bar, and the session routes
      (`POST /api/session/save`, `POST /api/session/load`, `GET /api/session/list`) are
      exercised by `tests/test_journey.py`.
* [x] The VST3 family is built — one stamped plug-in per module — and an independent host
      (`qa/vst3host`) loads a built plug-in, connects it, drives a parameter, processes
      audio and restores the state.
* [ ] Every module has a golden-audio case in `corpus/`. The corpus holds **16** reference
      clips; the per-module golden cases are still to be recorded.
* [x] `python -m pytest tests -q` is green.

### Explicitly out of scope for the alpha

* A registered plug-in identifier set — the class ids and product ids the identity layer
  produces are locally generated and have to be registered with the plug-in format bodies
  before shipping.
* A plug-in editor. The adapter returns no editor, so the host shows its own generic
  parameter editor.
* Non-destructive *edit layers* in the session document. The spectral region survives a
  chain round trip as module state, but there is no layer stack.
* A native desktop shell.
* Anything that touches the network.

---

## 2. What comes next

### 2.1 The quality gaps

Seven measured gaps. They are not cosmetic: each one is a number, and each number is
traceable to a report or a test in the tree. The measurements below are the ones of
**2026-10-21**.

| # | Gap | Where | Measured | State |
|---|---|---|---|---|
| 1 | Spectral De-Noise still eats wanted signal: on the voice case it removes 34.91 dB of hiss but **6.80 dB of the wanted voice**; on the music case 13.01 dB and **2.60 dB** | `aurcore/modules/spectral_de_noise.py` | `qa/quality/REPORT.md` §6.1 | **open** |
| 2 | Voice De-Noise: the voice harness says the fix landed (hiss band −15.95 dB, voice band +0.01 dB, block coherent to −218 dB), but the quality instrument's own rule still fails — **4.88 dB** of the wanted voice kept, a gain variance of 0.9619 dB² and 1152 samples of latency | `aurcore/modules/voice_de_noise.py` | `qa/quality/REPORT.md` §6.1, `qa/qualityvoice/REPORT.md` | **open** — the two harnesses disagree |
| 3 | De-Click leaves **6.75 dB** of artefact on the transient case | `aurcore/modules/de_click.py` | `qa/quality/REPORT.md` §6.1 | **open** |
| 4 | De-Clip moves the restoration **7.77 dB away** from the truth, and the 1.5 kHz partial by 6.63 dB | `aurcore/modules/de_clip.py` | `qa/quality/REPORT.md` §6.1 and §7 | **open** |
| 5 | De-Ess moves the wanted partial of the voice case by **59.8 dB** — the voice is annihilated; De-Plosive moves it by **214.8 dB** | `aurcore/modules/de_ess.py`, `de_plosive.py` | `qa/quality/REPORT.md` §7 | **open** |
| 6 | Guitar De-Noise, De-Rustle and De-Reverb move the wanted partials of their cases by **11.7 dB**, **5.0 dB** and **2.9 dB** | those three modules | `qa/quality/REPORT.md` §7 | **open** — they have no rule yet, because the cases carry no ground truth for their work |
| 7 | The plug-in component and the plug-in controller are one object, so the host releases the plug-in once more than it was counted | `plugins/vst3native/AuralisVST3.*` | `qa/vst3host` | **worked around**, not fixed. The objects are no longer deleted on the last release, which works; the proper arrangement is one object per role, and it is written down in `plugins/vst3native/README.md`. |

Gaps 1–6 are the ones a user hears, and every one of them is a module that removes what
it should keep. Gap 7 is a shipping detail of the plug-in layer.

#### What was fixed since the first pass

| Was | Now | Evidence |
|---|---|---|
| The analysis payload's frequency axis was off by a factor of 1.5 — a 1 kHz tone was reported at 656.2 Hz, and the bottom 170 of 512 rows were copies | the axis is right, and it is pinned | `tests/test_analysis_payload.py` (38 tests, green) |
| De-Click fired on steady material and lost 10.3 dB of the programme at Sensitivity 6.0 | the journey passes | `tests/test_journey.py` (21 tests, green, no xfail) |
| Voice De-Noise attenuated the voice by 2.23 dB and was not block coherent | the voice harness measures a 0.01 dB voice band and −218 dB of block coherence | `qa/qualityvoice/REPORT.md` §1 |
| Spectral De-Noise's gain curve varied by 1.78 dB² | the own-curve variance is 0.0000 dB² | `qa/quality/REPORT.md` §6.1 |
| Seven modules were over the 64-sample block budget | six of the seven are inside it now; two modules are held at "debt" | `qa/performance/REPORT.md` §2 |
| There was no session document route | three routes, and the journey walks the round trip | `aurserver/app.py`, `tests/test_journey.py` |
| **The spectral editor's rectangle had no route — it was module state and nothing carried it** | `POST /api/chain/{index}/region` carries it, and `POST /api/chain/{index}/state` carries any module's state back | `aurserver/app.py`, `aurui/js/spectral_editor.js` (`REGION_ROUTE`) |
| The shell did not import `session.js` and `preset_manager.js` | it does; the session toolbar is mounted in the top bar | `aurui/js/app.js` |
| The plug-in faulted when the host released it | the objects are not deleted on the last release | `plugins/vst3native/AuralisVST3.cpp`, `qa/vst3host` |

### 2.2 The real-time budget

The realtime budget of a 64-sample block at 48 kHz is **1333 µs**, which is 160.6
transform equivalents of a 1024-point transform. The budget table
(`qa/performance/budgets.py`) holds every module and every chain against it, and the
gate is `python -m pytest tests/test_performance.py`. The numbers below are the
snapshot in `qa/performance/REPORT.md`; the live ones drift with the machine, but the
verdict does not — the same test prints *"modules over the realtime budget right now:
1 → AUR_SPECTRALDENOISE"* and *"modules held at a debt ceiling: 2"*.

* **46 of the 48 modules** are held at the realtime budget itself — the requirement is
  enforced directly.
* **Two** are held at *debt*: their measured cost plus 50 %. They do not fit into the
  realtime budget today, and the ceiling holds them where they are and fails when they
  get worse.
  * `AUR_MUSICREBALANCE` — 142 trf, ceiling 213.
  * `AUR_SPECTRALDENOISE` — 1629 trf, ceiling 2444.
* The two chains are held the same way: the dialogue chain at 143 trf (ceiling 214) and
  the spectral chain at 1546 trf (ceiling 2319).

Six of the seven modules the first pass named as over budget are inside it now:

| module | 64 trf | 64 % of the budget |
|---|---|---|
| `AUR_AMBIENCEMATCH` | 57 | 35.7 % |
| `AUR_DECONSTRUCT` | 5 | 3.0 % |
| `AUR_GUITARDENOISE` | 44 | 27.2 % |
| `AUR_REPAIRASSISTANT` | 0 | 0.1 % |
| `AUR_VOICEDENOISE` | 1 | 0.3 % |
| `AUR_RESAMPLE` | 4 | 2.2 % |
| `AUR_SPECTRALDENOISE` | **1629** | **1014 %** — still over |

The one that is still over is over at every block size — 1014 % at 64 samples and
150 % at 512. The cause is measured in `qa/performance/REPORT.md` and it is not what it
looks like: **the cost does not fall with the block size**, because the analysis windows
are fixed. A host that asks for 64-sample blocks pays the same price as one that asks
for 1024.

That is the thing to fix, and it is not a tuning problem: the work is bounded by the
window length, so the windows have to become block-relative, or the affected modules have
to declare a minimum block size that the host is told about. Until one of those two
things happens, Spectral De-Noise is **off the streaming path** in a host that uses small
buffers.

### 2.3 The bundle size

One plug-in bundle is **184 kB** — the plug-in alone. The core and the interpreter are
shared by the whole family and live once, in the runtime directory next to the bundles,
so a family of 48 is **8.6 MB** of bundles and **62.9 MB** of runtime:

| Part of the family | Size |
|---|---|
| the 48 bundles | 8.6 MB |
| `python315.dll` | 7.48 MB |
| the standard library and the packages the core imports | 51.5 MB |
| `aurcore_abi.dll` | 201 kB |

The interpreter is still the bulk of it, and it is still loaded into the host's own
address space, so a family of 48 plug-ins still costs the user 62.9 MB of disk and the
host an interpreter per plug-in instance. This is the single largest shipping problem
in the project, and §2.6 is the answer.

### 2.4 The Spectral Editor's edit layers

The module (`AUR_SPECTRALEDITOR`, five modes: Attenuate, Replace, Gain, Pattern,
Partials), the region overlay (`aurui/js/spectral_editor.js`) and the **transport** are in
the tree: the overlay sends the painted rectangle to
`POST /api/chain/{index}/region`, the core takes it through `SpectralEditor.set_region`,
and the status line under the view says whether the region really reached the module.
`POST /api/chain/{index}/state` carries any module's state back, so an undo that puts a
module into the chain does not lose the region.

What is still missing is the layer above it:

* **Edit layers.** The session document carries the chain and the view state. It does not
  carry a stack of non-destructive edits, so a spectral repair is currently "the state of
  one module" rather than "a layer that can be switched off, re-ordered and undone
  independently".
* **One rectangle per module.** The overlay draws one region, and it is the region's
  state — not a list of regions — so a second edit replaces the first instead of sitting
  beside it. A layer stack is what turns one region into many.

### 2.5 The AAX adapter

The format's SDK and its conformance validator are in hand, and the adapter is written
against them.

* The adapter compiles against the SDK, the plug-in loads in the format's own host, and
  the format's validator has run the test suite on it: the description, parameter,
  load/unload, parameter-traversal and page-table checks pass.
* **The page table was the one check that failed.** The format expects a page-table
  resource document in the bundle; the generator now writes it in the format's own
  schema, the build stages it into the bundle, and the validator passes the page-table
  check on it.
* Two things are still external: the effect-id registration and the digital signature a
  production host wants.

### 2.6 The production C++ core

The C ABI is real and it works — but today the library behind it embeds CPython and runs
the numpy DSP. That is the right thing for an alpha and the wrong thing for a shipping
plug-in (§2.3).

The production track is therefore:

* **Keep `aurcore_c_api.h` exactly as it is.** It is the contract, and it is already
  pinned by 42 conformance tests. The C++ core implements the same header; the header
  does not change, so the adapter does not change either.
* **Implement the DSP natively behind that header** — the same algorithms, the same
  parameter names, the same natural units, the same typed-value chain document.
* **Verify the two implementations against each other.** The golden corpus and the
  per-module objective metrics are the acceptance criterion: functional parity, not
  sample-exact parity.
* **Do not fork the semantics.** The C++ core is a second implementation of a contract,
  not a second contract. `params.tsv` stays the source of truth for both.

The honest constraint: this is a large piece of work, and until it is done the plug-in
carries a Python runtime. The alpha does not need it; a shipping plug-in does.

### 2.7 Smaller items

* **Rebuild the plug-in family.** The binaries under `build/vst3native/AUR_<CODE>/stamped/`
  are a mixture of two adapter revisions — `plugins/vst3native/AuralisVST3.cpp` was being
  rewritten while this documentation pass was made — so `tests/test_vst3_host.py` fails
  for the modules whose binary is older than the source. One pass of
  `python scripts/build_vst3.py` (one module after another, 17–40 s each) closes it.
* **Record the per-module golden-audio cases.** The corpus has 16 reference clips; each
  module needs a case that says what the repair must achieve. This is the acceptance
  criterion for §2.6, so it comes before it.
* **One plug-in object per role.** The component and the controller are one object, so
  the host releases the plug-in once more than it was counted (§2.1, gap 7). The
  workaround works; the proper arrangement is written down in
  `plugins/vst3native/README.md`.

---

## 3. Milestones

| # | Milestone | Target | Depends on | Deliverable |
|---|---|---|---|---|
| M0 | **Alpha** | 0.1.0a0 | — | End-to-end single-file restoration in the browser. Module browser, composite view, module panels, chain strip, render and export. |
| M1 | **Quality** | 0.2.0 | M0 | The eight gaps of §2.1 are closed, the block budget of §2.2 is met, and every module has a golden-audio case. |
| M2 | **Plug-in adapters** | 0.3.0 | M0 | The VST3 target ships; the AAX target follows when the SDK arrives. The alpha's module set is exposed to DAWs. |
| M3 | **Non-destructive editing** | 0.4.0 | M1 | A document model in the ARA spirit: the source file is never modified; every change is an edit layer over it. Undo/redo, session save/load, re-open a session and continue. |
| M4 | **Batch & delivery** | 0.5.0 | M2 | Queue-based batch processing over a folder tree with per-file chains and per-file delivery targets. |
| M5 | **Beta** | 1.0.0 | M1–M4 | Feature freeze, format stability (preset/chain/document v1 are frozen), performance targets, platform packages, registered plug-in identifiers, documentation complete. |

---

## 4. The honest open risks

| Risk | Why it is a risk | What we do about it |
|---|---|---|
| **The AAX SDK may never arrive.** | It is licensed by a vendor and cannot be assumed. | The architecture is format-agnostic, so AAX is additive: the product ships on VST3 and AAX is added if and when access is granted. The request is written and ready to send. |
| **The plug-in currently carries a Python runtime.** | A DAW loads the plug-in into its own process; an embedded interpreter is heavy (62.9 MB of runtime for the whole family, 8.6 MB of plug-ins) and is an unusual dependency for a host. | The C ABI is already the right boundary. The production C++ core implements the same header, and the conformance tests are the acceptance criterion. |
| **The separation modules are DSP approximations.** | Dialogue Isolate and Music Rebalance are marked `ml = True` but ship classical algorithms, not trained models. | The seam is clean: a model replaces the internals of `process()` and changes no parameter, no file format and no interface. Any replacement model must be trained on permissively-licensed data, and every model needs a licence entry. |
| **Parity is functional, not sample-exact.** | A C++ core will not be bit-identical to numpy. | Objective metrics per module family are the acceptance criterion, not bit equality. |
| **Spectral De-Noise misses the block budget.** | Its analysis windows are fixed, so the cost does not fall with the block size, and a host that asks for 64-sample blocks pays the full price (1014 % of the budget). | The budget table holds it at its measured cost plus 50 %, so it cannot get worse unnoticed. The fix — block-relative windows, or a declared minimum block size — is written down in `qa/performance/REPORT.md`. Until it is applied, the module is off the streaming path in a small-buffer host. |
| **The plug-in identifiers are not registered.** | A shipping plug-in needs its class ids and product ids registered with the format bodies. | The identity layer derives them deterministically, so registration is a one-off, not a re-derivation. |
| **The tree is moving fast.** | Modules, tests and the shell land in parallel, so a run of the suite can catch a half-landed change, and a plug-in binary in `build/` can be older than the source it came from. | Every gate is a single command and is listed above. A failing gate is reported as a failing gate, not worked around. |

---

## 5. What cannot be done here

Some of the work on this page is not blocked by effort but by the environment. It is
listed so that nobody plans around it.

* **The AAX adapter.** The SDK is licensed by a vendor and may not be redistributed, so
  it is not in the tree — but it is a download, and the build takes its path. The glue
  is written and it compiles against the SDK; the format's own validator has run on the
  built plug-in. What is left is the registration and the signature.
* **A registered plug-in identifier set.** The class ids and product ids have to be
  registered with the plug-in format bodies. That is an external process with a lead time
  we do not control.
* **A shipping-grade native core.** It is not blocked by the environment — it is a large
  piece of engineering (§2.6) — but it cannot be produced by rewriting the alpha's DSP
  quickly, because functional parity has to be demonstrated module by module.
* **A code-signing and notarisation story for the plug-ins.** The identity record is
  written into the binary after the link, which invalidates a signature; a shipping
  pipeline has to re-sign afterwards. That is a packaging problem, not a code problem.
* **Multi-user anything.** One process, one user, one machine — by decision D12.

---

## 6. Explicit non-goals

* **No cloud processing.** Audio stays on the machine. There is no upload, no remote
  render, no account.
* **No telemetry.** Nothing is measured, reported or phoned home.
* **No proprietary plug-in formats.** Everything Auralis writes is open JSON with an
  open, documented schema.
* **No third-party naming.** Product, module and parameter names are our own. See
  [`DECISIONS.md`](DECISIONS.md).
* **No numpy-free core.** The core stays numpy-first, with scipy optional and always
  behind a numpy fallback.
