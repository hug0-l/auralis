# plugins/vst3native — the JUCE-free VST3 adapter

This directory holds the Auralis VST3 adapter that is written **directly
against the VST3 SDK**, with no JUCE in the picture.

## Why a second adapter

`plugins/vst3` holds the JUCE based VST3 adapter.  It needs a JUCE
checkout *and* a JUCE compatible VST3 SDK layout at the same time, and the
two do not line up:

| JUCE | VST3 SDK layout it wants |
|---|---|
| 6 | the 3.6 era SDK (it vendors one) |
| 7, 8 | a 3.7 era layout that no SDK tag provides |

The SDK in this repository is **3.8.1** in the new split layout:
`vendor/vst3sdk/pluginterfaces` and `vendor/vst3sdk/public.sdk` are two
separate trees, and the SDK's `base/` module is not part of it.

That rules out the SDK's own helper classes (`basewrapper`,
`SingleComponentEffect`, `EditControllerEx1`, …): they all include
`base/source/fobject.h`, and that module is not in the 3.8.1 split
layout.  So the adapter implements the four interfaces itself:

```
IPluginFactory    the class factory the host asks for first
IComponent        the bus layout and the state
IAudioProcessor   the audio path
IEditController   the parameter list and the parameter values
```

They live in **three** objects, the way the format wants them:

| Object | What it is |
|--------|------------|
| `AuralisComponent` | the buses, the state and the DSP engine |
| `AuralisProcessor` | the audio path; the component owns it |
| `AuralisController` | the parameter list and the parameter values |

The factory creates the component or the controller, depending on the
interface the host asks for, and the component's `getControllerClassId`
answers with the class the host is to ask the factory for.  The optional
`IEditController2` is implemented as well.

The JUCE adapter stays where it is and stays an alternative.  Nothing in
`plugins/vst3` was touched.

## What the adapter does

* **One template, every module.**  Which module a binary implements is
  decided at build time: the build stamps an identity record into a
  reserved slot inside the binary (see `plugins/identity.py` and
  `plugins/vst3/build_identity.py`), and the adapter reads that record
  when it starts.  No module name, parameter name or preset is written in
  the source.
* **One audio input, one audio output, stereo.**
* **The parameters of the identity record's `param_id_map`**, published
  with the host parameter id from the map and the canonical core
  parameter name as the display name.  The metadata (range, default,
  unit, curve, enum choices) comes from the generated table
  `AuralisGenerated.h`, which the build derives from the canonical
  registry — never from a hand written list.
* **The core's latency**, in samples, through
  `aur_engine_latency_samples`.
* **The core only through `plugins/vst3/aurcore_c_api.h`.**  The adapter
  never sees the core's implementation.
* **The parameter values travel over the connection point.**  The
  controller holds the values the host sees; the component holds the copy
  the audio thread reads.  When the host connects the two, the controller
  pushes every value over the connection, and every later change is
  pushed as well.  The component's copy is a plain array of atomics, so
  the audio thread reads it without a lock and without asking the core.

## The state chunk

The state chunk is the `auralis.state.v1` convention the JUCE adapter
uses, byte for byte, so a session saved by one adapter loads in the
other:

```
"auralis.state.v1" NUL
<core chain document> NUL
<host parameter state> NUL
```

Every field is NUL terminated, which is exactly what the JUCE adapter's
`juce::MemoryOutputStream::writeString` writes (raw UTF-8 plus a NUL).

The third field is the JUCE value tree document that holds the host
visible parameter state:

```xml
<?xml version="1.0" encoding="UTF-8"?>

<AURALIS>
  <PARAM id="1" value="3.5" />
  <PARAM id="2" value="0.25" />
</AURALIS>
```

* the entries are ordered by the parameter id **as text**, which is the
  order the JUCE adapter's parameter table uses;
* the `value` is the *unnormalised* (natural unit) value — dB, Hz, ms,
  % — rounded to the same precision the JUCE adapter stores, and written
  with the same shortest-round-trip decimal form;
* an enum parameter carries the index of its choice;
* an id the reader does not know is ignored, so a state written by an
  adapter that knows more parameters still loads.

The container is the same for both roles: `IComponent::getState` writes
the core chain and the host parameters, `IEditController::getState`
writes the host parameters alone (the controller has no engine), and
`IEditController::setComponentState` takes the host parameters out of
the component's blob.  A session written by the JUCE adapter loads in
both.

## The class id

The class id of a module is the **GUID string** in the identity record
(`vst3_fuid`), and it is the same identifier on every platform.  The
sixteen bytes a binary reports are that string's *platform encoding*: the
SDK's `INLINE_UID` macro lays a class id out **COM compatible on Windows**
(the first word little endian, the second one with its bytes swapped in
pairs, the last two big endian) and **plain big endian everywhere else**,
and `FUID::toString` turns the bytes back into the string on either
platform.

The adapter therefore asks the SDK for the bytes (`FUID::fromString`)
instead of taking the record's hex digits for them.  The hex digits are
the plain big endian layout, which is right on every platform *except*
Windows, where a host reads the reported bytes as a different class id:
the record says

    0101abab-abcd-ef01-4155-52414445434c

and a host that reads the plain big endian bytes says

    {ABAB0101-CDAB-01EF-4155-52414445434C}

Which of the two is the class id is decided in `plugins/identity.py`, and
it is decided like this: **the string**.  It is the same identifier in the
record, in the install manifest and in the session files of every platform.

## Building

### Prerequisites

* the VST3 SDK 3.8.1 — `vendor/vst3sdk` by default;
* a shared library that implements the C ABI in
  `plugins/vst3/aurcore_c_api.h` — `plugins/abi/build/aurcore_abi.dll`
  by default (`python plugins/abi/build_abi.py` builds it);
* Python, for the generated parameter table and the identity stamping;
* cmake and ninja.  On this machine they live in
  `%LOCALAPPDATA%\Python\pythoncore-3.15-64\Scripts`, and the MSVC
  environment is set up by `tools\msvc.bat`.

### One module

```bat
set PATH=%LOCALAPPDATA%\Python\pythoncore-3.15-64\Scripts;%PATH%

cmd /c "tools\msvc.bat cmake -S plugins\vst3native -B build\vst3native ^
          -G Ninja -DAURALIS_MODULE=AUR_DECLICK -DCMAKE_BUILD_TYPE=Release"
cmd /c "tools\msvc.bat cmake --build build\vst3native"
```

The build produces three things:

| Artefact | What it is |
|---|---|
| `build/vst3native/bin/Auralis De-Click.dll` | the linked module, before stamping |
| `build/vst3native/stamped/Auralis De-Click.vst3/Auralis De-Click.vst3.dll` | the shipped plug-in, with the identity record stamped in |
| `build/vst3native/stamped/Auralis De-Click.vst3/aurcore_abi.dll` | the core, next to the plug-in so it can be loaded; the staging takes it out again and the family shares one copy (see **The shared runtime**) |

and it verifies itself: after every link the build runs two checks
against the built module, both of which load it the way a host loads it:

| Check | What it proves |
|---|---|
| `check_vst3_export.py` | the module exports `GetPluginFactory` and `ModuleEntry`, the factory answers, and the class id it reports is the class id in the identity record |
| `check_vst3_state.py` | the controller publishes exactly the parameters of the record's `param_id_map`, the state chunk is the shared `auralis.state.v1` container, and a parameter survives a round trip through the state |

### The exports

```
GetPluginFactory     the class factory (this is what the host asks for)
ModuleEntry          the module entry point of the VST3 format
ModuleExit           its counterpart
```

### All the variants

There is **one target**; the module is chosen with `-DAURALIS_MODULE`.
Every module is therefore the same build with another value:

```bat
for %%M in (AUR_DECLICK AUR_DECRACKLE AUR_DECLIP AUR_DEHUM) do (
    cmd /c "tools\msvc.bat cmake -S plugins\vst3native -B build\vst3native\%%M ^
              -G Ninja -DAURALIS_MODULE=%%M -DCMAKE_BUILD_TYPE=Release"
    cmd /c "tools\msvc.bat cmake --build build\vst3native\%%M"
)
```

The script in the repository root does the same for the whole registry, one
module after another so that the compilers do not all run at once:

```bat
python scripts\build_vst3.py
python scripts\build_vst3.py --only AUR_DECLICK,AUR_DEHUM
```

It reads the module list from the registry, so it never goes stale, and it
prints the time each module took.

The module list comes from the registry, so it never goes stale:

```bat
python plugins\vst3\build_identity.py list --format cmake
```

The same list is what `build_identity.py stamp-all` uses, and it also
writes the install manifest:

```bat
cmake --build build\vst3native --target auralis_vst3native_manifest
```

The manifest lists every built plug-in with its identity and the hash of
its file.  The manifest tool recognises a plug-in by its file name and
only knows the flat ".vst3" suffix, while a Windows VST3 bundle is a
*folder* named "<name>.vst3" holding "<name>.vst3.dll", so the target
copies the plug-in to `stamped/flat/<name>.vst3` for the tool.  That
copy lives in a sub directory of its own, so a host that scans the build
output does not find the plug-in twice.

The registry currently holds **48** modules (the brief said 47; the
registry grew by one in the meantime).  Every one of them becomes a
plug-in with the loop above, and nothing in this directory has to change
when a module is added.

### Options

| Option | Meaning |
|---|---|
| `AURALIS_MODULE` | the module code this build implements, e.g. `AUR_DECLICK` |
| `AURALIS_VST3_SDK_PATH` | the VST3 SDK root (default `vendor/vst3sdk`) |
| `AURALIS_CORE_DLL` | the core shared library, copied next to the plug-in |
| `AURALIS_CORE_LIBRARY` | the library to link against (not needed on Windows, see below) |
| `AURALIS_STAMP_IDENTITY` | `ON` stamps the identity record after linking |
| `AURALIS_PYTHON` | the Python interpreter to use |

### The import library for the core

The core is a separate shared library.  On Windows the adapter needs an
*import* library for it, and this build **generates one from
`plugins/vst3/aurcore_c_api.h`** (the export list is read out of the
header, so the two can never drift apart):

```
build/vst3native/auralis_core.def   the export list, derived from the header
build/vst3native/auralis_core.lib   the import library, built from it
```

That keeps the build independent of whatever the core's own build tree
happens to contain.  On the other platforms the shared library itself is
linked against.

### Packaging note

The Windows loader searches the *host's* directory for a plug-in's
dependencies, not the plug-in's own directory.  The core therefore has to
be reachable when the plug-in is loaded: either next to the plug-in (the
build copies it there, and a host that loads the bundle with
`LOAD_WITH_ALTERED_SEARCH_PATH` finds it) or on the `PATH`.  A shipping
installer has to make sure of that; the adapter itself reports the
problem in the log and passes the audio through unchanged when the core
cannot be created.

The arrangement that replaced "next to the plug-in" is in **The shared
runtime** below: the build still copies the runtime into every bundle
(the build's own checks load the module the way a host loads it), and
the staging takes it out again afterwards, so that the family shares one
copy.

## The shared runtime

### The problem

The family is 48 plug-ins.  Every one of them is the same adapter with
another module stamped into it, and every one of them carried the same two
shared libraries:

    <name>.vst3.dll        the plug-in itself          ~177 kB
    aurcore_abi.dll        the core's C ABI             ~144-174 kB
    python315.dll          the interpreter the core      7.8 MB

The interpreter is the same file 48 times.  The family was therefore
**392,960,512 bytes (374.76 MiB)**, of which **376,465,920 bytes
(359.03 MiB)** were one file, copied.

### The three arrangements that were measured

The load path is the whole difficulty.  The Windows loader does **not**
look in the directory of a loaded module for that module's dependencies.
It looks in the directory the *host* gave it, and then in the system
directories and the `PATH`.

**A. The loader** (measured with `scripts/vst3_loader_probe.cpp`, which
is in the repository; it is a measurement instrument, not part of the
build):

| # | where the runtime is | how it is loaded | result |
|---|---|---|---|
| 1 | inside the bundle | plain `LoadLibrary` of the bundle | **fails**, error 126 |
| 2 | inside the bundle | `LOAD_WITH_ALTERED_SEARCH_PATH` | works; the search propagates down the chain, so the interpreter next to the core is found |
| 3 | a directory of its own | plain `LoadLibrary` | **fails**, error 126 |
| 4 | a directory of its own | `LOAD_WITH_ALTERED_SEARCH_PATH` on the *bundle* | **fails**, error 126 - the altered path is the bundle's directory, not the runtime's |
| 5 | a directory of its own | `SetDllDirectory` on the runtime | works |
| 6 | a directory of its own | the libraries are preloaded by full path (what a host does) | the preload of the core **fails** (error 126: its own dependency is not found), and the module then fails to load |
| 7 | a directory of its own | the plug-in loads the core itself | **works**, and the host needs to know nothing |

Rows 2 and 4 are the two halves of the problem: the altered search path
finds a library that is **in** the bundle, and it propagates to that
library's own dependencies, but it never reaches a directory that is not
the bundle's.  Row 6 is what a host does when it wants to satisfy a
module's imports by name, and it shows why: a library that is loaded by
full path does not find its own dependencies next to it.

**B. The host** (the independent host, `qa/vst3host`, against the three
arrangements; "the host is told" means `AURALIS_VST3HOST_DEPS` points at
the runtime directory):

| arrangement | the stock host, told where the runtime is | the host with `LOAD_WITH_ALTERED_SEARCH_PATH` in its preload | the stock host, told nothing |
|---|---|---|---|
| the runtime inside the bundle | works | works | fails, 126 |
| the runtime in a directory of its own | **fails, 126** | **works** | fails, 126 |
| the runtime inside the bundle, hard linked to a shared copy | works | works | fails, 126 |

The last row is the hard-link arrangement: it behaves exactly like the
arrangement the family had.

The arrangement that was chosen is row 7 of table A, because it is the
only one that leaves the host out of the picture.


### What the plug-in has to do

The plug-in cannot keep a static import on the core: the loader resolves
it when the plug-in is loaded, and the core is not in the bundle any more.
Three ways out were measured:

| way | what it costs | what it does to the binary | result |
|---|---|---|---|
| delay load (`/DELAYLOAD:aurcore_abi.dll` + a failure hook) | the import becomes a delay-load descriptor; the plug-in loads the core with `LoadLibraryExW(..., LOAD_WITH_ALTERED_SEARCH_PATH)` | 166,400 -> 169,984 bytes (+2.2 %) | works; a missing runtime raises 0xC06D007E in the host unless the plug-in checks the load itself |
| explicit `LoadLibraryExW` + `GetProcAddress` | 14 function pointers and 18 call sites in the adapter | a table of 14 pointers | works; the biggest change of the three |
| a loader shim in the bundle | a second module in every bundle, forwarding every call | a module of its own in all 48 bundles | works; the adapter does not change at all, but every bundle carries a second DLL |

The **delay load** was chosen: the ABI header stays the contract, the
adapter's call sites do not change, the binary grows by 2.2 %, and the
load is one call that can be *checked*.

Two things the measurement settled:

* the load must not happen in `DllMain`.  A `LoadLibraryEx` from `DllMain`
  is a loader-lock violation, and the module faulted at teardown
  (exception 0xC0000005, exit -1073741819).  The load belongs in the
  plug-in's own code, before the first call into the core.
* the load must be checked.  A delay load that fails raises 0xC06D007E in
  the *host*, which is much worse than a plug-in that passes the audio
  through.  The plug-in loads the core itself, checks the handle, and
  degrades to "no engine" when the runtime is not there.

The change is in `var/patches/a2_adapter.patch`.

### The layout

    <family root>/
        Auralis Runtime/            the shared runtime, one copy
            aurcore_abi.dll
            python315.dll
            runtime-manifest.json
        Auralis De-Click.vst3/      the plug-in, alone
            Auralis De-Click.vst3.dll

The runtime directory is a **sibling of the bundles**, so the whole family
is one directory tree, and the installer copies one tree.

The rule that finds the runtime, from the plug-in's own directory:

1. `AURALIS_RUNTIME_DIR`, when it is set;
2. a directory called `Auralis Runtime` next to the bundle;
3. one in the bundle's parent directory (the build layout, where the
   bundle sits in `<module>/stamped/`);
4. the bundle's own directory (the old layout, where the bundle still
   carries the runtime).

`plugins/vst3native/stage_runtime.py` implements the same rule for the
tooling, so the tool and the plug-in always agree.

### The numbers

The family, measured on the 48 module bundles of the build tree
(`build/vst3native/<module>/stamped/*.vst3`) and on the release tree
(`dist/full`), with the staging run on a copy:

| | before | after | saving |
|---|---|---|---|
| the 48 bundles | 392,960,512 B (374.76 MiB) | 16,464,608 B (15.70 MiB) | 376,495,904 B (359.05 MiB), 95.8 % |
| the whole release tree | 395,767,974 B (377.43 MiB) | 27,145,094 B (25.89 MiB) | 368,622,880 B (351.55 MiB), 93.1 % |

The release tree holds 48 copies of the interpreter: 376,465,920 bytes,
359.03 MiB.  One copy is 7,843,040 bytes.

The numbers move when the family is rebuilt - the plug-in binaries grow
and shrink with the adapter - so they carry the day they were taken on.

### What was verified

The build, the staging and the independent host, on the real build
output of `AUR_DECLICK`:

| | host | result |
|---|---|---|
| the bundle as the build leaves it (the runtime inside) | stock host, `AURALIS_VST3HOST_DEPS` = the bundle's own directory | **exit 0, no fault** |
| the staged bundle (the runtime in a directory of its own) | stock host, `AURALIS_VST3HOST_DEPS` = the runtime directory | **fails, error 126** - see below |
| the staged bundle | the host with `LOAD_WITH_ALTERED_SEARCH_PATH` in its preload, `AURALIS_VST3HOST_DEPS` = the runtime directory | **exit 0, no fault** |
| the staged bundle | any host, with the adapter patch: the plug-in loads the runtime itself | **exit 0, no fault** |

The stock host cannot load the staged bundle, and that is not a detail of
the arrangement: the host loads the libraries of a directory before the
module, with a plain `LoadLibraryW` of the full path, and such a load
does **not** make the library's own dependencies findable in that
directory.  The preload of the core therefore fails (error 126), and the
module cannot be loaded afterwards.

That is also true of the arrangement the family had before: the host only
loaded it because `run_host.py` points `AURALIS_VST3HOST_DEPS` at
`plugins/abi/build`, which holds a complete runtime of its own.  The
bundle's own copy of the core was never the one that was loaded.

So the change needs the host's preload fixed (one line, the patch is in
`var/patches/runtime_integration.patch`) or the plug-in to load the
runtime itself (`var/patches/a2_adapter.patch`).  The second one is the
better answer: it makes the plug-in independent of the host.

### The tooling

    python scripts/vst3_runtime.py size     --family build/vst3native
    python scripts/vst3_runtime.py plan     --family build/vst3native
    python scripts/vst3_runtime.py stage    --family build/vst3native
    python scripts/vst3_runtime.py restore  --family build/vst3native
    python scripts/vst3_runtime.py verify   --family build/vst3native
    python scripts/vst3_runtime.py manifest --family build/vst3native
    python scripts/vst3_runtime.py deps     --family build/vst3native

* `stage` moves the runtime out of the bundles into the shared directory.
  It compares the copies first: a family whose bundles do not carry the
  same core is reported, not silently deduplicated.
* `restore` puts the runtime back.  The build writes it into every bundle
  again, so this is what undoes a staging run.
* `verify` checks that every bundle's imports are satisfied by the bundle
  or by the runtime.  It reads the import tables, so it does not guess.
* `manifest` writes `runtime-manifest.json` and `install-manifest.json`.
* `deps` prints the value `AURALIS_VST3HOST_DEPS` needs.

### What the host has to do

The independent host loads the libraries of a directory before it loads
the module, so that the module's imports are satisfied by name.  It does
that with a plain `LoadLibraryW` of the full path, and a plain
`LoadLibraryW` does **not** make the library's own dependencies findable
in that directory: the preload of the core fails, and the module then
cannot be loaded (error 126).

One line fixes it - `LoadLibraryExW` with `LOAD_WITH_ALTERED_SEARCH_PATH`
- and with it the host loads the staged family.  The patch is in
`var/patches/runtime_integration.patch`.

The value `AURALIS_VST3HOST_DEPS` needs is the runtime directory, which
is the directory the staging wrote:

    set "AURALIS_VST3HOST_DEPS=%CD%\build\vst3native\Auralis Runtime"

for the build tree, and

    set "AURALIS_VST3HOST_DEPS=%CD%\dist\full\plugins\vst3\build\stamped\Auralis Runtime"

for the release tree.  `python scripts/vst3_runtime.py deps --family ...`
prints it.

The host is not the only way in, and it should not have to be: with the
adapter patch the plug-in finds the runtime itself, and the host needs to
know nothing.  `AURALIS_VST3HOST_DEPS` is then only needed for a build
that still has the static import.

### The property that has to survive

*"A bundle loads on a machine with nothing else installed."*

With the runtime in a directory of its own, the **family** is the unit of
installation, not the bundle: the bundle needs the runtime directory
next to it.  That is true of every plug-in family that shares a runtime,
and it is what the installer produces.  What is *not* true any more is
that a single bundle, copied on its own into a host's plug-in folder,
loads.  The alternative - a copy of the runtime in every bundle - is what
the 359 MiB of duplicated interpreter was, and it is the only arrangement
that keeps a lone bundle self-contained.

### The arrangement that was not chosen

**Hard links.**  The runtime could stay in the bundle and the family
could still store one copy: the runtime directory holds the file, and
every bundle holds a hard link to it.  The bytes on the volume are the
same as with the arrangement above (16,464,608 bytes), the bundle keeps
its three files, and nothing has to change in the adapter or in the host.

It was measured (the loader matrix in `scripts/vst3_loader_probe.cpp`
and the host runs): the behaviour is exactly the behaviour of today's
bundle - the host loads it, no fault.  It is a good arrangement, and it
is the one to pick when the bundle has to stay self-contained *and* the
volume has to store one copy.

It was not chosen because it does not shrink the **package**: a zip holds
48 entries, and an installer that recreates the links is a second thing
to get right on every platform.  It is also a property of the volume and
not of the tree: a FAT32 or a network volume cannot hold hard links, and
the family falls back to 48 copies without saying so.  The arrangement
above has neither problem: the runtime is one file in one directory, and
every file system agrees.

The tool knows about it: `stage_runtime.runtime_members` finds the
runtime in a bundle by the import graph, so a bundle that holds a hard
link is staged like any other.

### The files

| File | What it is |
|---|---|
| `plugins/vst3native/stage_runtime.py` | the layout, the staging, the restore, the check, and a small PE reader that finds the imports |
| `plugins/vst3native/make_manifests.py` | the install manifests of the family and of the runtime |
| `scripts/vst3_runtime.py` | the command line of all of it |
| `scripts/vst3_loader_probe.cpp` | the measurement instrument: how the loader resolves a plug-in's dependencies |
| `var/patches/a2_adapter.patch` | the plug-in loads the runtime itself |
| `var/patches/runtime_integration.patch` | the host's preload, and the staging in the build and in the release |



## The reference counting

Every object is an ordinary COM object: the last release deletes it.

The host holds the component and the controller as two separate pointers
and releases each of them once, which is why they are two objects.  The
processor is a third one: the host takes the processor interface from the
component through `queryInterface`, and a host that holds an alias of the
component's own interface must not be able to bring the component down
with it.

The factory is a static object of the module, so it is not deleted when
the last reference goes: the module is unloaded right after that.

---

## A note on the identity slot, and a bug it hides

The identity slot is a `const` object with a constant initialiser, so the
linker stores it in the file where the build-time stamper can patch it.
**A compiler is allowed to assume that the contents of a `const` object
never change** — it is undefined behaviour to write to one — so it will
happily fold a read of the slot back to its link-time value.  In an
optimised build that silently throws the stamped record away.

The adapter therefore reads the slot through a `volatile` pointer, which
stops the folding:

```cpp
const volatile char* const region = gIdentitySlot.region;
```

`plugins/vst3/AuralisPlugin.cpp` reads the same slot in the same way
(`const char* const region = gIdentitySlot.region;` followed by a scan
for the terminating NUL) and is therefore **exposed to the same
optimisation**.  That file is not mine to change, so it is reported here
instead: the JUCE adapter should read its slot through a volatile
pointer as well, or the identity record will not be found in an
optimised build.

## Files

| File | What it holds |
|---|---|
| `AuralisVST3.h` | the identity record, the host parameters, the state chunk and the plug-in class |
| `AuralisVST3.cpp` | the implementation: the identity slot, a small JSON reader, the shared number format, the state document, the plug-in and the class factory |
| `CMakeLists.txt` | the one target, the generated parameter table, the import library, the stamping and the two checks |
| `stage_runtime.py` | the shared runtime: the layout, the staging, the restore and the check |
| `make_manifests.py` | the install manifests of the family and of the runtime |

The two check scripts are written into the build tree by
`CMakeLists.txt`, so the repository holds no file beyond the four above.

## Third party names

None.  The only third party code this build compiles is the VST3 SDK's
own `pluginterfaces` implementation files (`funknown.cpp`, `coreiids.cpp`,
`conststringtable.cpp`, `ustring.cpp` and `vstinitiids.cpp`), which are
part of the SDK in `vendor/vst3sdk` and are not copied into this
repository.
