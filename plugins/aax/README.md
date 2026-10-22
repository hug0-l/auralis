# The Auralis AAX adapter

The Auralis plug-in family ships in more than one plug-in format.  This
directory holds the **AAX** adapter: the plug-in that a DAW loads when the
user picks an Auralis module from a host that speaks the AAX format.

The adapter is written **directly against the AAX SDK**, in the same spirit
the VST3 adapter (`plugins/vst3native/`) is written directly against the
VST3 SDK.  The SDK is a separate download, so it is not in the tree; the
build is told where it is:

    python scripts\build_aax.py --sdk PATH
    set AURALIS_AAX_SDK_PATH=PATH

The copy in the tree is used when no other is named:

    vendor/JUCE8/modules/juce_audio_plugin_client/AAX/SDK

Either copy holds the format's interface headers (`Interfaces/`), the
format's own reference implementation of the plug-in side of the format
(`Libs/AAXLibrary/source/`, 36 translation units) and the licence
(`LICENSE.txt`).  The SDK's import library is **not** part of the download,
so the SDK's own sources are compiled into the plug-in — which is what the
framework's own client does as well.  There are 38 SDK sources in all:
the 36 of the library, the class factory and the exported entry points.
(The copy of revision 2.10 has 46 sources in the library, and the build
against it produces a plug-in of 285,696 bytes.)

---

## 1. The shape of the adapter

| File | What it is |
|---|---|
| `AuralisAAX.h` | the description types, the data model, the controller |
| `AuralisAAX.cpp` | the implementation: the description, the data model, the controller, the entry point |
| `AuralisAAXProbe.cpp` | the verification probe (see §6) |
| `CMakeLists.txt` | the build: the SDK's sources, the plug-in, the probe |
| `resources/` | the page table document of every module, in the format's own schema (generated) |

The adapter is **one template**.  It contains no module name, no parameter
name and no preset:

* the **module** is chosen by the identity record that the build stamps
  into the binary (`plugins/identity.py` owns the record and the stamp);
* the **page tables** and the **parameter list** come from the generated
  description, which `scripts/build_aax.py` writes from
  `docs/aax_page_tables.json` — the file `scripts/aax_tables.py` writes
  from the canonical parameter table (`aurcore/schema/params.tsv`);
* the **page table document** — the resource the host reads out of the
  bundle — is written by the same generator, into `resources/`, and the
  build copies it into the bundle.

So the chain is:

    aurcore/schema/params.tsv
        -> scripts/aax_tables.py
        -> docs/aax_page_tables.json      (the package)
        -> plugins/aax/resources/*.xml     (the page table document)
        -> scripts/build_aax.py
        -> AuralisAAXGenerated.h
        -> the plug-in
        -> build/aax/bundles/*.aaxplugin   (the bundle: plug-in + document)

and the two plug-in formats read the same canonical table, so they cannot
drift apart.

---

## 2. The interfaces

The SDK's own reference implementation shows what a plug-in has to provide.
`AAX_VComponentDescriptor.cpp` is what a plug-in fills the component
descriptor in with; `AAX_VPageTable.cpp` is the wrapper the plug-in writes
the page table through.

### The interfaces the adapter implements

| Interface | Where | What it does |
|---|---|---|
| `AAX_IEffectParameters` | `AuralisAAX_Processor` (derives from `AAX_CEffectParameters`) | the data model: the parameters, the state chunk, the page table |
| `AAX_IEffectGUI` | `AuralisAAX_Controller` (derives from `AAX_CEffectGUI`) | the controller: the view container and the host's questions about the plug-in |
| `AAX_IParameter` | one per parameter (the SDK's `AAX_CParameter`) | the value of one parameter, its range, its states |
| `AAX_IPageTable` | the page table the data model fills in | the pages and the parameters that are bound to them |
| `AAX_ITaperDelegate` | `AAX_CLinearTaperDelegate` | the range of a parameter, in natural units |
| `AAX_IDisplayDelegate` | `AAX_CNumberDisplayDelegate`, `AAX_CStateDisplayDelegate`, `AAX_CUnitDisplayDelegateDecorator` | how a value is drawn, and how it is read back |

### The interfaces the host owns

These are **host** interfaces: the host creates the objects, the plug-in
fills the descriptor in, reads the map or calls the delegate.  The SDK's
`AAX_V*` classes are the wrappers that do it, and the adapter uses them.

| Interface | How the adapter uses it |
|---|---|
| `AAX_IComponentDescriptor` | filled in through `AAX_VComponentDescriptor`: the audio in and out, the buffer length, the bypass port, the private data, the property map, the process procedure |
| `AAX_IEffectDescriptor` | filled in through `AAX_VEffectDescriptor`: the names, the category, the component, the creation procedures |
| `AAX_IPropertyMap` | read and written through `AAX_VPropertyMap`: the manufacturer id, the product id, the plug id, the stem formats, the bypass flag |
| `AAX_IAutomationDelegate` | called through `AAX_VAutomationDelegate`: the parameter changes the host is told about |
| `AAX_ICollection` | the collection the SDK hands to the entry point: the effects are added to it |

### The entry point

`GetEffectDescriptions(AAX_ICollection*)` is what the SDK calls: the
SDK's `AAX_Init.cpp` builds a collection and hands it over, and the
function describes the plug-in into it.  A binary describes one module in
three channel configurations, and each configuration is an **effect of its
own** with its own effect id.

The exported entry points of the format (`ACFRegisterPlugin`,
`ACFGetSDKVersion`, `ACFStartup`, `ACFShutdown`, …) come from the SDK's
`Interfaces/AAX_Exports.cpp`, which is compiled into the plug-in.

---

## 3. The identifiers

| Part | Value |
|---|---|
| Manufacturer id | `AURA` (four characters, shared with the other format) |
| Product id | the module's four character product code, e.g. `DECL` |
| Plug id | `MONO`, `M2ST` or `STER` |
| Effect id | `AURA:DECL:MONO` (and the two other channel configurations) |
| Parameter id | the registry id (the position of the parameter inside its module) plus `256` |

The manufacturer id, the product id and the plug id are written into the
property map of the component descriptor as the format's four character
codes (`AAX_eProperty_ManufacturerID`, `AAX_eProperty_ProductID`,
`AAX_eProperty_PlugInID_Native`).

The parameter id base of `256` is ours.  The format's own reserved control
identifiers are **strings** (`"PreviewID"`, `"MasterBypassID"`, see
`AAX_CEffectParameters.cpp`), not a numeric range, so a numeric base
cannot collide with them.

### The effect ids have to be registered

**The effect ids have to be registered with the format's vendor before a
host accepts them.**  The 144 ids this project generates (48 modules ×
three channel configurations) are well formed — four characters each, upper
case letters and digits — but well formed is not the same as registered.
The registration is not ours to make; until it is made, a host is not
expected to accept these plug-ins.

---

## 4. The licence

The SDK is used here under the terms of the **GPL v3**.  The SDK's own
licence file says so:

    vendor/JUCE8/modules/juce_audio_plugin_client/AAX/SDK/LICENSE.txt

    "Or: You may also use this code under the terms of the GPL v3
     (see www.gnu.org/licenses)."

The same file also offers the vendor's own commercial terms.  **This
project does not decide which of the two applies** — it states the
position, and the position is: the SDK may be used under the GPL v3, and a
plug-in that links the SDK carries that licence with it.  Anyone shipping
this plug-in has to settle the question for themselves before shipping.

---

## 5. Building

    python scripts\build_aax.py

The tool

1. checks that the application package is not drifted
   (`scripts/aax_tables.py --check`),
2. writes the generated description,
3. compiles the SDK's own sources and the adapter into the plug-in,
4. stamps the identity record of every module into its own copy of the
   template (`build/aax/plug-ins/`),
5. stages the **bundle** of every module (`build/aax/bundles/`), and
6. runs the probe and writes its report (`build/aax/aax_description.json`).

### The bundle

The format loads a plug-in from a **bundle directory**, not from a file, so
the build puts the shippable form of every plug-in together:

    build/aax/bundles/AUR_DECLICK.aaxplugin\
        Contents\
            x64\AUR_DECLICK.dll          the plug-in
            Resources\AuralisPages.xml   the page table document

The **page table is a resource of the bundle**.  The format's interface says
what that means (`AAX_Enums.h`): `AAX_eResourceType_PageTable` is *"the file
name of the page table xml file"*, and `AAX_eResourceType_PageTableDir` — the
directory the files are in — *"defaults to `*.aaxplugin/Contents/Resources`"*.
The plug-in declares the file to the host:

```cpp
descriptor->AddResourceInfo (AAX_eResourceType_PageTable, "AuralisPages.xml");
```

The document is generated from the canonical table and **copied** into the
bundle by the build, so the bundle cannot carry a hand-edited copy.  The
gate that says so is `python scripts\build_aax.py check`: it compares the
document inside every built bundle with a fresh run of the generator.

**The two names of a bundle have to agree.**  The plug-in inside
`Contents/x64` has to carry the name of the bundle directory:
`AUR_DECLICK.aaxplugin` holds `AUR_DECLICK.dll`.  A bundle whose two names
differ is one the host cannot load — it fails with an access violation
rather than with a message — so `build_aax.py check` reports it.

Useful switches:

    python scripts\build_aax.py --no-stamp         # the template only
    python scripts\build_aax.py --only AUR_DECLICK # one plug-in
    python scripts\build_aax.py generate --out F  # the description only
    python scripts\build_aax.py check              # the description is fresh

### The runtime

The core is not inside the plug-in.  The family shares one copy of it, in
the runtime directory next to the bundles, and the plug-in loads it
itself — the same rule the VST3 adapter uses:

1. `AURALIS_RUNTIME_DIR`, when it is set;
2. a directory called `Auralis Runtime` next to the plug-in;
3. one in the directory above that, and one above that.

**This build does not stage the runtime.**  The plug-in therefore runs
without the core — the audio passes through unchanged — unless the runtime
is where the rule above finds it.  Staging it is the release pipeline's
job, the same job it has for the other format.

The tests are

    python -m pytest tests\test_aax_adapter.py -q

### The numbers

| | |
|---|---|
| a clean build (the 46 SDK sources, the adapter, the probe) | 10.7 s with two compiler jobs, against the SDK of revision 2.10 |
| the plug-in (`build/aax/AuralisAAX.dll`) | 286,208 bytes (279.5 kB) |
| the stamped family (`build/aax/plug-ins/`) | 50 plug-ins, 13.6 MB |
| the bundles (`build/aax/bundles/`) | 50 bundles, 13.8 MB |
| the page table documents (`plugins/aax/resources/`) | 50 documents |
| the adapter tests | 22 tests, all green |

---

## 6. What is proved, and what is not

### Proved here

* the adapter **compiles** against the SDK, and the plug-in carries the
  format's exported entry points and the identity slot;
* the identity record is **stamped into every plug-in** of the family and
  reads back with the right module, the right effect ids and the right
  parameter map;
* the **component descriptors and the effect ids** the plug-in registers
  read back correctly (three channel configurations per module);
* the **parameter list** read back through the format's own parameter
  interface matches the canonical table: the names, the identifiers, the
  types, the ranges in natural units, the defaults, the number of steps
  and the states of the discrete controls;
* the **page table** read back through the format's own page table
  interface matches the canonical table: the pages, the order of the
  controls and the parameter that is bound to each of them;
* the **page table resource** the plug-in declares to the host is the
  document the generator wrote, and the document inside every built bundle
  is that document byte for byte;
* the **state chunk** carries the shared `auralis.state.v1` marker and the
  shared parameter state document, so a session written by one format
  loads in the other;
* the 150 effect ids are **well formed** and unique.

### The verdict of the format's own validator

The format ships a conformance validator, and it runs the plug-in in the
format's own host.  The whole suite is green on the bundle the build
produces:

| Test | Verdict |
|---|---|
| `test.describe_validation` | E_COMPLETED_PASS (1/1) |
| `info.support.general` | E_COMPLETED_PASS (3/3) |
| `info.productids` | E_COMPLETED_PASS (3/3) |
| `info.support.audiosuite` | E_COMPLETED_PASS (3/3) |
| `info.support.s6_feature` | E_COMPLETED_PASS (3/3) |
| `test.data_model` | E_COMPLETED_PASS (3/3) |
| `test.parameters` | E_COMPLETED_PASS (3/3) |
| **`test.page_table.load`** | **E_COMPLETED_PASS (3/3)** — it was E_COMPLETED_FAIL (0/3) with *"ERROR: Failed to load page tables library"* before the resource was shipped |
| `test.page_table.automation_list` | E_COMPLETED_PASS (3/3) |
| `test.load_unload` | E_COMPLETED_PASS (1/1) |
| `test.parameter_traversal.random.fast` | E_COMPLETED_PASS (3/3) |

The suite is run against `build/aax/bundles/AUR_DECLICK.aaxplugin` — the
bundle the build produced, not a copy of it.

### Not proved here

* **the plug-in is not loaded into a commercial host.**  It *is* loaded into
  the format's own host: the conformance validator that ships with the
  format's tooling loads it, reads the description back and runs the test
  suite on it.  That is the format's own verdict, not ours — but it is not
  a shipping host, so the audio path through a real session is still not
  shown;
* the **effect ids are not registered** (§3);
* the **licence question is not settled** (§4);
* the **plug-in is unsigned**, so a production host would not load it;
* the **audio path is not exercised** by the probe: it builds the data
  model and reads it back, but it does not run the process procedure,
  because there is no host to run it.

The probe (`AuralisAAXProbe.cpp`) is what does the reading back.  It
builds the very same data model the plug-in builds and drives it through
the format's own interfaces; the page table is the one object the host
owns, so the probe provides an in-memory stand-in for it — the same way a
host would — and the adapter fills that stand-in through the format's own
interface.
