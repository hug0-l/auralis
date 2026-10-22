# Auralis plug-in adapter layer

This directory holds the layer that turns Auralis modules into plug-ins.

The idea is short: **there is one adapter template per plug-in format, and the
module a binary implements is decided at build time.** The build stamps a
small identity record into a reserved slot inside the binary; the adapter
reads that record when the host creates the plug-in and configures itself
accordingly.

Two consequences follow, and they are the whole point:

* **Adding a module to the registry produces a plug-in for every format.**
  There is no per-module adapter code, no per-module parameter list, no
  per-module page table. The module list, the parameter list and the
  identifiers are all generated from the canonical parameter table.
* **The adapter never has to be recompiled when a module changes.** The
  identity record is data, not code.

```
                      aurcore/schema/params.tsv          (the source of truth)
                                |
                 tools/codegen_registry.py  ->  aurcore/schema/generated.py
                                |                              |
                                +------------------------------+
                                               |
                                     plugins/identity.py
                                               |
                     +-------------------------+-------------------------+
                     |                                                   |
             identity record                                    AuralisGenerated.h
             (stamped into the binary)                       (compiled into it)
                     |                                                   |
                     +-------------------------+-------------------------+
                                               |
                                      plugins/vst3native/AuralisVST3.cpp
                                               |
                                       one binary per module
```

### Which adapter is the one that ships

There are **two** VST3 adapters in this repository:

| Directory | What it is | State |
|---|---|---|
| `vst3native/` | The adapter written **straight against the VST3 SDK**, with no framework in between. | **This is the one that ships.** 48 binaries are built from it, and an independent host loads them, connects them, drives their parameters, processes audio through them and restores their state. |
| `vst3/` | The same idea implemented on top of a framework. | Kept as an alternative.  It builds, and it is where the C ABI header and the stamping tool live. |

The reason is the toolchain, not taste: the framework releases and the SDK layouts
that are available do not line up (a framework release needs a 3.6 era SDK layout,
the next one needs a 3.7 era layout that no SDK tag provides, and the SDK in this
repository is 3.8.1 in the split layout).  Writing the four interfaces against the
SDK removes the question.

Both adapters share the identity record, the parameter table and the state format,
so a session written by one loads in the other.

---

## The files

| File | What it is |
|------|------------|
| `identity.py` | The identity record: how it is derived from the registry, how it is written, stamped and read back, and how it is validated. |
| `identity.schema.json` | The JSON Schema of the record. Draft 2020-12. |
| `vst3native/AuralisVST3.h` / `.cpp` | The VST3 adapter that ships. One implementation, every module. |
| `vst3native/CMakeLists.txt` | The build: one target, the module chosen with `-DAURALIS_MODULE`. |
| `vst3native/stage_runtime.py` | The shared runtime of the family: it moves the core library and the interpreter out of the bundles into one runtime directory next to them, puts the home of the interpreter next to the core, and checks the result. |
| `vst3native/make_manifests.py` | Writes the VST3 manifest of the finished bundles. |
| `vst3/AuralisPlugin.h` / `.cpp` | The framework based VST3 adapter template. An alternative. |
| `vst3/aurcore_c_api.h` | The C ABI between an adapter and the DSP core. |
| `vst3/build_identity.py` | The build-time tool: generate the header, stamp the binaries, write the manifest. |
| `vst3/CMakeLists.txt` | The build of the framework based adapter. |
| `aax/AAX_ADAPTER_NOTES.md` | The AAX design: identifiers, parameter ids, pages, labels, and what still needs the SDK. |

### Building the family

```bat
python scripts\build_vst3.py
```

The script reads the module list from the registry, builds one module after another
(so the compilers do not all run at once) and prints the time each module took.  The
48 modules take about seven minutes that way.


---

## The identity record

The record is a small JSON document. It is written to disk next to the
plug-in as a human readable twin, and into the binary itself.

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

| Field | Meaning |
|-------|---------|
| `format`, `version` | Discriminator and schema version. |
| `module_code` | The registry key, e.g. `AUR_DECLICK`. The adapter passes it to the core. |
| `display_name`, `product_name`, `file_description`, `original_filename`, `internal_name`, `category` | What the host shows, and the stable name the host's session machinery uses. |
| `vst3_fuid` | The VST3 class id. See below. |
| `aax_effect_ids` | The three (manufacturer, product, plug) triples, one per channel variant. |
| `param_id_map` | Host parameter id to canonical core parameter name. |

The schema is `plugins/identity.schema.json`. It is enforced on the way in
(stamping) and on the way out (reading back), so a binary that carries a
malformed record is rejected rather than half-believed.

### The VST3 class id

The VST3 class id is not a separate scheme that has to be kept in step with
the binary — it is derived the way the plug-in wrapper derives it:

```
INLINE_UID (0x0101ABAB, 0xABCDEF01, JucePlugin_ManufacturerCode, JucePlugin_PluginCode)
```

with the manufacturer code `AURA` and the plugin code being the module's
four-character product code (`DECL` for De-Click). The four words are laid
out big-endian, so the class id of De-Click is the string
`0101abab-abcd-ef01-4155-52414445434c` — the value the record carries.

**The class id is that string.** The sixteen bytes a binary reports are the
string's *platform encoding*: the SDK lays them out COM compatible on
Windows and plain big endian everywhere else, so that the host reads the
same identifier back on every platform. An adapter therefore has to report
the bytes of *its* platform. The record's hex digits are the plain big
endian layout, which is a different class id on Windows.

The same four-character product code is the AAX product id, so one module has
one identity across formats.

> The codes produced here are *locally generated*. A shipping product has to
> register its class ids and product ids with the plug-in format bodies.

---

## The slot

The adapter reserves a slot in its binary:

```
+---------------------------+---------------------------+
| 13 marker bytes          | 4096 region bytes         |
+---------------------------+---------------------------+
```

* The **marker** is the ASCII sequence `AURIDENTITYv1`. The build-time
  stamper searches for it and refuses to continue if it occurs more than
  once, so the marker is assembled from individual bytes in the C++ and is
  deliberately **not** a string literal.
* The **region** holds the UTF-8 JSON record, padded with NUL bytes. The
  largest record today is 859 bytes (Spectral De-Noise, measured over the 48
  stamped binaries of the release tree), so 4 KiB is comfortable.

The slot lives in read-only data with a constant initialiser, which means
the linker stores it in the file. A zero-initialised object would go to
`.bss`, which is not part of the file on disk and could therefore not be
patched at all.

The stamping is a byte-level patch of the file after it has been linked. It
changes nothing else about the binary.

---

## The C ABI

An adapter never touches the core's internals. It calls five functions:

```c
AurEngine* aur_engine_create (uint32_t sample_rate, uint32_t channels);
int        aur_engine_add_module (AurEngine*, const char* module_code);
int        aur_engine_set_param (AurEngine*, uint32_t module_index,
                                  const char* param_name, double value);
int        aur_engine_process (AurEngine*, const float* const* input,
                                float* const* output, uint32_t frames);
void       aur_engine_destroy (AurEngine*);
```

plus a set of extensions a plug-in needs but the core API cannot answer on
its own: `aur_engine_set_param_text` (enum values are **choice strings**,
not indices), `aur_engine_get_param`, `aur_engine_set_bypass`,
`aur_engine_module_count`, `aur_engine_latency_samples`,
`aur_engine_tail_samples`, `aur_engine_serialize` /
`aur_engine_deserialize`, `aur_engine_free`, `aur_engine_last_error` and
`aur_engine_version`, and the `aur_shutdown` that ends the interpreter the
library started.

**The export table has eighteen entries** — the five required entry points,
`aur_shutdown` and the twelve extensions — and the version of the contract
is `AUR_ABI_VERSION 3` (`vst3/aurcore_c_api.h`).  The table is derived from
that header and from nowhere else: `plugins/abi/build_abi.py` reads the
declarations out of the header, writes them into the module definition file
the linker uses, and refuses to build when the implementation does not
define every one of them.

The tail is one of those entries.  `aur_engine_tail_samples` is the export
that grew the table from sixteen entries to seventeen, and
`aur_engine_output_sample_rate` took it to eighteen.  A module declares its
own tail, the chain takes the **longest** of them, and a module that makes
a sound of its own answers with the infinite tail the plug-in formats use.

The full contract, including threading and units, is in
`vst3/aurcore_c_api.h`.

Two rules the whole layer obeys:

* **Parameters are addressed by name.** There are no numeric parameter ids in
  the core. The host parameter id is an adapter-side concept; the adapter
  translates it through the record's `param_id_map`.
* **Values are in natural units** — dB, Hz, ms, %. The core never sees a
  normalised 0..1 knob position.

---

## The build tool

`vst3/build_identity.py` is the build-time half of this layer.

```
python plugins/vst3/build_identity.py header --out build/AuralisGenerated.h
python plugins/vst3/build_identity.py stamp --template build/Auralis.vst3 \
                                                --module AUR_DECLICK
python plugins/vst3/build_identity.py stamp-all --template build/Auralis.vst3 \
                                                --outdir build/stamped \
                                                --manifest build/stamped/install-manifest.json
python plugins/vst3/build_identity.py manifest --dir build/stamped \
                                                --out build/stamped/install-manifest.json
python plugins/vst3/build_identity.py verify --binary build/stamped/Auralis.vst3
python plugins/vst3/build_identity.py list --format cmake
```

| Command | What it does |
|---------|--------------|
| `header` | Writes `AuralisGenerated.h`: the parameter table with the parameter ids, the choice pool, the module table and the slot geometry. |
| `stamp` | Stamps one module's identity into one binary. Without `--output` the binary is stamped in place. |
| `stamp-all` | Stamps every module from one template and writes the install manifest. |
| `manifest` | Reads a set of stamped binaries back and writes the install manifest. |
| `verify` | Reads one identity back out of a binary. |
| `list` | Lists the registry; `--format cmake` emits the variables the build iterates over. |

The header deliberately does **not** contain the marker bytes: the marker
has to occur exactly once in the linked binary, or the stamper cannot tell
which occurrence is the slot.

---

## Building

```sh
cmake -S plugins/vst3 -B build/vst3 \
      -DAURALIS_JUCE_PATH=<path to a JUCE checkout> \
      -DAURALIS_CORE_LIBRARY=<path to the core library>
cmake --build build/vst3 --config Release
```

The build needs two things this repository does not contain:

* **JUCE** (7.0.9 or newer), pointed at with `AURALIS_JUCE_PATH`.
* **A library that implements the C ABI** in `vst3/aurcore_c_api.h`, pointed
  at with `AURALIS_CORE_LIBRARY`. The DSP core is a separate deliverable;
  this build only knows its interface. The build fails with an explanation
  when the library is missing, rather than silently producing a plug-in that
  does nothing.

Useful options:

| Option | Default | Meaning |
|--------|---------|---------|
| `AURALIS_BUILD_VST3` | `ON` | Build the VST3 format. |
| `AURALIS_BUILD_AU` | `OFF` | Build the Audio Unit format. |
| `AURALIS_BUILD_STANDALONE` | `OFF` | Build the Standalone format. |
| `AURALIS_BUILD_AAX` | `OFF` | Build the AAX format (needs `AURALIS_AAX_SDK_PATH`). |
| `AURALIS_STAMP_IDENTITIES` | `ON` | Stamp the identity into every plug-in. |
| `AURALIS_MACOS_CODESIGN_IDENTITY` | empty | Re-signs the plug-ins after stamping, because patching the binary invalidates the signature. |

### Why the VST3 build compiles the template once per module

The adapter *source* is one template; there is no per-module adapter code.
But the VST3 format requires every plug-in to have its own class id and its
own class name, and the plug-in wrapper bakes **both** into the binary at
compile time (`JucePlugin_PluginCode` and `JucePlugin_Name`). They cannot be
patched afterwards without breaking the binary's identity in a way that is
hard to see.

So the VST3 build compiles the same template once per module, with those two
values taken from the generated identity record:

```cmake
juce_add_plugin(Auralis_${module_code}
    PLUGIN_MANUFACTURER_CODE "AURA"
    PLUGIN_CODE        "${module_product}"       # DECL, DECR, DECS, ...
    PRODUCT_NAME       "Auralis ${module_name}") # Auralis De-Click, ...
```

The *module* is still chosen by the stamped record, not by the compile-time
values: the plugin code and the name identify the plug-in to the host, the
stamped record tells the adapter what to load and how to map its parameters.

The AAX format has the same requirement (one effect id per channel variant,
per module) and is handled the same way; see `aax/AAX_ADAPTER_NOTES.md`.

---

## Adding a module

1. **Add the module to `aurcore/schema/params.tsv`** — its display name,
   category and parameters. This file is the single source of truth; the
   registry, the generated header, the identity record, the AAX page table
   and the plug-in itself all follow from it.
2. **Regenerate the registry**:
   ```sh
   python tools/codegen_registry.py
   ```
3. **Build.** The build reads the registry, so the new module is picked up:
   it gets a product code, a VST3 class id, three AAX effect ids, a plug-in
   binary and an entry in the install manifest.

Nothing under `plugins/` is edited. That is the point of the design.

---

## Parameter id stability

Host parameter ids are the **module-local** ids from the generated index
(`aurcore/schema/generated.py`, mirrored into `AuralisGenerated.h`). They are
stable across rebuilds, machines and build systems, and across changes to
the registry, which is what makes a session written by one build load in the
next one.

The rules that keep them stable:

1. **A parameter id is the parameter's position inside its own module**,
   counted from zero. The id space is per module: the ids of a module never
   depend on any other module, so a module can be added, removed or grown
   without renumbering anything else.
2. **New parameters are appended to the end of their module.** A parameter
   inserted in the middle of a module shifts the ids of the parameters after
   it, and every session that refers to the old ids breaks.
3. **Ids are never renumbered for cosmetic reasons.** A parameter that is
   renamed keeps its id; the name in the table changes, the id does not.

Every id-bearing record carries the module code next to the id, so the pair of
(module, id) is unambiguous, and the same ids are used by every format.

---

## The core's C ABI — implemented and verified

The interface in `vst3/aurcore_c_api.h` now has a **real implementation**:

| File | What it is |
|---|---|
| `abi/aurcore_abi.cpp` | the C ABI over the core; embeds CPython and drives `aurcore.abi_bridge` |
| `abi/build_abi.py` | builds `aurcore_abi.dll` / `.dylib` / `.so` |
| `abi/interpreter_home.py` | copies the home of the interpreter next to the library, so the library is a complete runtime |
| `../aurcore/abi_bridge.py` | the Python side of the bridge (one buffer in, one buffer out) |
| `../tests/test_plugin_abi.py` | 42 conformance tests that load the library the way a host does |

```powershell
python plugins/abi\build_abi.py        # -> plugins/abi/build/aurcore_abi.dll
python -m pytest tests\test_plugin_abi.py
```

The build needs no installed toolchain: it falls back to the compiler that
ships inside the `ziglang` wheel, and falls back again to `cc`/`gcc`/`clang`
from `PATH`. `AURALIS_ROOT` overrides where the core is found at run time.

**The contract the tests pin down**

* five required entry points: `aur_engine_create`, `aur_engine_add_module`,
  `aur_engine_set_param`, `aur_engine_process`, `aur_engine_destroy`, plus
  the `aur_shutdown` that ends the interpreter;
* the extensions: `set_param_text`, `get_param`, `set_bypass`,
  `module_count`, `latency_samples`, `tail_samples`, `output_sample_rate`,
  `serialize`, `deserialize`, `free`, `last_error`, `version` — eighteen
  exports in all, at contract version 3;
* success is `0` (or a non-negative index or length), failure is a **negative**
  `AurError`;
* parameters are addressed **by name**, values are in **natural units**
  (dB, Hz, ms, %), and an enum value is its **choice string**;
* the audio buffer is **planar**: one `float*` per channel;
* a bypassed module passes the audio through untouched;
* the chain serialises to the `auralis.chain` document and restores from it.

**Why the core is embedded rather than re-implemented.** The DSP is written
in Python. Re-implementing it in C would fork the algorithms and guarantee
the two drift. The adapter therefore talks to the one core through a narrow,
stable, versioned boundary — the same shape the reference product uses, where
the plug-in holds a versioned callback into the core and the core owns every
parameter.

---

## The runtime, and the home of the interpreter

The family is 48 plug-ins and **one** core.  The core is a shared library, so
it has to be reachable when the plug-in is loaded, and the interpreter it is
linked against has to be reachable with it.  The arrangement is:

```
<family root>/
    Auralis Runtime/          the shared runtime, one copy
        aurcore_abi.dll        the core
        python315.dll          the interpreter
        Lib/                   the standard library and site-packages/numpy
        DLLs/                  the extension modules
    Auralis De-Click.vst3/    the plug-in, alone
        Auralis De-Click.vst3.dll
```

**The runtime directory IS the home of the interpreter.**  The core finds it
itself, from where it is running: the directory it was loaded from, the
directory next to it, the `Python` directory next to it, and finally the
`PYTHONHOME` of the environment, for a deployment that carries no home of
its own.  A candidate that holds the interpreter library of another version
is refused, because the standard library of one version is not the standard
library of another.  The home is handed to the interpreter with
`PyConfig.home`, so the environment of the host is left alone — a user does
not have to set a variable for the plug-in to work.

When there is no home the core does **not** start the interpreter: it says
so, names every directory it searched, and the host reports it.  The
interpreter's own fatal handler would take the host down without a word.

Measured on the release tree (`python scripts\build_release.py`, 2026-10-21):

| | |
|---|---|
| the runtime directory of the build tree | 78 316 471 bytes (74.7 MiB) in 1974 files — the figure moves with every build, because the interpreter writes its byte code into the home |
| the runtime directory in the package | 66 004 560 bytes (62.9 MiB) in 1567 files |
| the 48 bundles together | 9 043 968 bytes (8.6 MiB) — 188 416 bytes each |
| the full package | 78 605 375 bytes (75.0 MiB) against a 700 MiB budget |
| the slim package | 3 351 535 bytes (3.2 MiB) against a 145 MiB budget |

The package's runtime is 13.2 MiB smaller than the same runtime in a build
tree whose interpreter has written its byte code into it — 11.8 MiB of that
is in numpy alone — because the release builder leaves the `__pycache__`
directories out.

The plug-in finds the runtime itself, in this order: the `AURALIS_RUNTIME_DIR`
of the environment when it is set, and then a directory called
`Auralis Runtime` next to the bundle, in the directory above that, or in the
directory above *that* — four levels are walked, which covers the build
layout and the installed one.  The import of the core is a delay-load
descriptor, and the plug-in loads the core out of the runtime directory
itself, so the arrangement works whatever the host's search path is.

---

## What is not here

* **The AAX SDK glue.** The identifiers, the parameter ids, the page layout
  and the label rules are generated and specified in
  `aax/AAX_ADAPTER_NOTES.md`; the SDK-facing code is written against the
  format's SDK and builds against a copy of it that the build is told
  about.
* **The editor.** The adapter returns no editor, so the host shows its own
  generic parameter editor. The Auralis interface belongs to the user
  interface layer.
* **Registered identifiers.** The class ids and product ids produced here
  are locally generated. They have to be registered with the plug-in format
  bodies before shipping.
