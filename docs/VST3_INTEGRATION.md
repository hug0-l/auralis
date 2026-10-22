# Auralis VST3 integration

This is the document a host author or a user needs to install the Auralis
plug-in family and to know what it does when it is loaded.  Every number and
every file name in it was read out of the repository; the commands that
produce the numbers are given with them.

* The family, the names and the shape of a plug-in: section 1
* Where the bundles are installed and how a host is told about them: section 2
* What a plug-in needs at run time: section 3
* The parameters: section 4
* The state, and the two ways a session crosses the boundary: section 5
* The known limits, stated plainly: section 6
* How to rebuild the family and how to check it: section 7

---

## 1. What the family is

The Auralis DSP core has **48 modules**; `aurcore/schema/params.tsv` holds
**253 parameters** over them.  The VST3 family is **one plug-in per module**:
48 plug-ins, each of them the same binary template with a different module
stamped into it.

| | |
|---|---|
| Modules in the registry | 48 (`aurcore/schema/params.tsv`, 253 parameter rows) |
| Plug-ins in the family | 48, one per module |
| Categories | Repair 26, Utility 18, Measurement 2, System 2 |
| Audio | one stereo input, one stereo output, 32-bit float only (`canProcessSampleSize` answers `kSample32` and nothing else, `plugins/vst3native/AuralisVST3.cpp`) |
| Latency | whatever the module reports, through `aur_engine_latency_samples` |
| Tail | whatever the module declares, through `aur_engine_tail_samples`; a module that makes a sound of its own reports `kInfiniteTail` |
| Editor | none: `createView` returns `nullptr`, so the host shows its own generic parameter editor |

### 1.1 One template, 48 binaries

The adapter source is a single template, `plugins/vst3native/AuralisVST3.cpp`.
It contains no module name, no parameter name and no preset.  Which module a
binary implements is decided **at build time**: the build stamps a small JSON
identity record into a reserved slot inside the linked file (the marker
`AURIDENTITYv1` plus a 4096-byte region), and the adapter reads that record
when the host creates the plug-in.

The build has exactly one target; the module is chosen with a cache variable
(`plugins/vst3native/CMakeLists.txt`):

```
cmake -S plugins/vst3native -B build/vst3native/AUR_DECLICK -G Ninja \
      -DAURALIS_MODULE=AUR_DECLICK -DCMAKE_BUILD_TYPE=Release
```

The identity record carries the module code, the display name, the product
name, the category, the VST3 class id and the map from host parameter id to
canonical parameter name (`plugins/identity.py`, schema
`plugins/identity.schema.json`).  The parameter metadata (range, default,
unit, group, curve, enum choices) is compiled in from a generated header
`AuralisGenerated.h`, which the build derives from the canonical table.  The
record is authoritative about *which* parameters the binary has; the header
supplies their metadata.

### 1.2 The names

The plug-in is shown to the host under the product name `Auralis <module
name>`; the bundle folder is `Auralis <module name>.vst3` and the binary
inside it is `<module name>.vst3.dll`.  The class id is the one in the
identity record.

Two module names contain an ampersand.  The record keeps it (`Time & Pitch`),
but the build writes a safe file name, because an ampersand breaks a command
line (`plugins/vst3native/CMakeLists.txt`): the bundle folder is
`Auralis Time and Pitch.vst3` and `Auralis Wow and Flutter.vst3`.  The name
the host shows is the one in the record, with the ampersand.

| Module code | Plug-in name | Category | Parameters | Bundle folder | VST3 class id |
|---|---|---|---|---|---|
| `AUR_DECLICK` | De-Click | Repair | 5 | `Auralis De-Click.vst3` | `0101abab-abcd-ef01-4155-52414445434c` |
| `AUR_DECRACKLE` | De-Crackle | Repair | 4 | `Auralis De-Crackle.vst3` | `0101abab-abcd-ef01-4155-524144454352` |
| `AUR_DECLIP` | De-Clip | Repair | 5 | `Auralis De-Clip.vst3` | `0101abab-abcd-ef01-4155-524144454353` |
| `AUR_DEHUM` | De-Hum | Repair | 19 | `Auralis De-Hum.vst3` | `0101abab-abcd-ef01-4155-524144454855` |
| `AUR_DEESS` | De-Ess | Repair | 8 | `Auralis De-Ess.vst3` | `0101abab-abcd-ef01-4155-524144454553` |
| `AUR_DEPLOSIVE` | De-Plosive | Repair | 3 | `Auralis De-Plosive.vst3` | `0101abab-abcd-ef01-4155-52414445504c` |
| `AUR_DEREVERB` | De-Reverb | Repair | 9 | `Auralis De-Reverb.vst3` | `0101abab-abcd-ef01-4155-524144455245` |
| `AUR_DERUSTLE` | De-Rustle | Repair | 3 | `Auralis De-Rustle.vst3` | `0101abab-abcd-ef01-4155-524144455255` |
| `AUR_DEWIND` | De-Wind | Repair | 4 | `Auralis De-Wind.vst3` | `0101abab-abcd-ef01-4155-524144455749` |
| `AUR_DEBLEED` | De-Bleed | Repair | 2 | `Auralis De-Bleed.vst3` | `0101abab-abcd-ef01-4155-52414445424c` |
| `AUR_BREATH` | Breath Control | Repair | 5 | `Auralis Breath Control.vst3` | `0101abab-abcd-ef01-4155-524142524541` |
| `AUR_MOUTHDECLICK` | Mouth De-Click | Repair | 4 | `Auralis Mouth De-Click.vst3` | `0101abab-abcd-ef01-4155-52414d4f5554` |
| `AUR_VOICEDENOISE` | Voice De-Noise | Repair | 12 | `Auralis Voice De-Noise.vst3` | `0101abab-abcd-ef01-4155-5241564f4943` |
| `AUR_SPECTRALDENOISE` | Spectral De-Noise | Repair | 19 | `Auralis Spectral De-Noise.vst3` | `0101abab-abcd-ef01-4155-524153504543` |
| `AUR_SPECTRALREPAIR` | Spectral Repair | Repair | 7 | `Auralis Spectral Repair.vst3` | `0101abab-abcd-ef01-4155-524153504559` |
| `AUR_SPECTRALRECOVERY` | Spectral Recovery | Repair | 5 | `Auralis Spectral Recovery.vst3` | `0101abab-abcd-ef01-4155-524153504542` |
| `AUR_GUITARDENOISE` | Guitar De-Noise | Repair | 8 | `Auralis Guitar De-Noise.vst3` | `0101abab-abcd-ef01-4155-524147554954` |
| `AUR_DECONSTRUCT` | Deconstruct | Repair | 6 | `Auralis Deconstruct.vst3` | `0101abab-abcd-ef01-4155-52414445434f` |
| `AUR_DIALOGUEISOLATE` | Dialogue Isolate | Repair | 5 | `Auralis Dialogue Isolate.vst3` | `0101abab-abcd-ef01-4155-52414449414c` |
| `AUR_MUSICREBALANCE` | Music Rebalance | Repair | 9 | `Auralis Music Rebalance.vst3` | `0101abab-abcd-ef01-4155-52414d555349` |
| `AUR_AMBIENCEMATCH` | Ambience Match | Repair | 6 | `Auralis Ambience Match.vst3` | `0101abab-abcd-ef01-4155-5241414d4249` |
| `AUR_REPAIRASSISTANT` | Repair Assistant | Repair | 7 | `Auralis Repair Assistant.vst3` | `0101abab-abcd-ef01-4155-524152455041` |
| `AUR_LEVELER` | Leveler | Utility | 6 | `Auralis Leveler.vst3` | `0101abab-abcd-ef01-4155-52414c455645` |
| `AUR_LOUDNESS` | Loudness Control | Utility | 6 | `Auralis Loudness Control.vst3` | `0101abab-abcd-ef01-4155-52414c4f5544` |
| `AUR_LOUDNESSOPT` | Loudness Optimize | Utility | 4 | `Auralis Loudness Optimize.vst3` | `0101abab-abcd-ef01-4155-52414c4f5556` |
| `AUR_EQ` | EQ | Utility | 14 | `Auralis EQ.vst3` | `0101abab-abcd-ef01-4155-524145513030` |
| `AUR_EQMATCH` | EQ Match | Utility | 1 | `Auralis EQ Match.vst3` | `0101abab-abcd-ef01-4155-524145514d41` |
| `AUR_GAIN` | Gain | Utility | 1 | `Auralis Gain.vst3` | `0101abab-abcd-ef01-4155-52414741494e` |
| `AUR_FADE` | Fade | Utility | 2 | `Auralis Fade.vst3` | `0101abab-abcd-ef01-4155-524146414445` |
| `AUR_NORMALIZE` | Normalize | Utility | 1 | `Auralis Normalize.vst3` | `0101abab-abcd-ef01-4155-52414e4f524d` |
| `AUR_PHASE` | Phase | Utility | 2 | `Auralis Phase.vst3` | `0101abab-abcd-ef01-4155-524150484153` |
| `AUR_MIXING` | Mixing | Utility | 2 | `Auralis Mixing.vst3` | `0101abab-abcd-ef01-4155-52414d495849` |
| `AUR_CENTEREXTRACT` | Center Extract | Repair | 4 | `Auralis Center Extract.vst3` | `0101abab-abcd-ef01-4155-524143454e54` |
| `AUR_AZIMUTH` | Azimuth | Repair | 3 | `Auralis Azimuth.vst3` | `0101abab-abcd-ef01-4155-5241415a494d` |
| `AUR_DITHER` | Dither | Utility | 4 | `Auralis Dither.vst3` | `0101abab-abcd-ef01-4155-524144495448` |
| `AUR_RESAMPLE` | Resample | Utility | 4 | `Auralis Resample.vst3` | `0101abab-abcd-ef01-4155-524152455341` |
| `AUR_TIMEPITCH` | Time & Pitch | Utility | 7 | `Auralis Time and Pitch.vst3` | `0101abab-abcd-ef01-4155-524154494d45` |
| `AUR_VARIPITCH` | Variable Pitch | Utility | 4 | `Auralis Variable Pitch.vst3` | `0101abab-abcd-ef01-4155-524156415249` |
| `AUR_VARTIME` | Variable Time | Utility | 3 | `Auralis Variable Time.vst3` | `0101abab-abcd-ef01-4155-524156415254` |
| `AUR_WOWFLUTTER` | Wow & Flutter | Repair | 4 | `Auralis Wow and Flutter.vst3` | `0101abab-abcd-ef01-4155-5241574f5746` |
| `AUR_INTERPOLATE` | Interpolate | Utility | 1 | `Auralis Interpolate.vst3` | `0101abab-abcd-ef01-4155-5241494e5445` |
| `AUR_SIGNALGEN` | Signal Generator | Utility | 5 | `Auralis Signal Generator.vst3` | `0101abab-abcd-ef01-4155-52415349474e` |
| `AUR_STREAMPREVIEW` | Streaming Preview | Utility | 4 | `Auralis Streaming Preview.vst3` | `0101abab-abcd-ef01-4155-524153545245` |
| `AUR_FINDSIMILAR` | Find Similar | Measurement | 1 | `Auralis Find Similar.vst3` | `0101abab-abcd-ef01-4155-524146494e44` |
| `AUR_CONNECT` | Connect | System | 2 | `Auralis Connect.vst3` | `0101abab-abcd-ef01-4155-5241434f4e4e` |
| `AUR_MONITOR` | Monitor | System | 1 | `Auralis Monitor.vst3` | `0101abab-abcd-ef01-4155-52414d4f4e49` |
| `AUR_CLIPCLASSIFIER` | Clip Classifier | Measurement | 5 | `Auralis Clip Classifier.vst3` | `0101abab-abcd-ef01-4155-5241434c4950` |
| `AUR_SPECTRALEDITOR` | Spectral Editor | Repair | 7 | `Auralis Spectral Editor.vst3` | `0101abab-abcd-ef01-4155-52415350454e` |

The table is generated from the canonical table, not written by hand:
`python plugins/vst3/build_identity.py list --format cmake` prints the same
module list the build iterates over, and `plugins/identity.py` derives the
records from it.

### 1.3 The class id

The class id is derived from the module's four-character product code with
the manufacturer code `AURA`, and it is **locally generated**.  A shipping
product has to register its class ids with the plug-in format body
(`plugins/README.md`, section "The VST3 class id").

The record holds the class id as a big-endian canonical GUID
(`0101abab-abcd-ef01-4155-52414445434c` for De-Click) and the binary reports
exactly those sixteen bytes — the build's own check compares the bytes the
binary reports with the bytes of the record and fails when they differ.  The
**string** a host shows is not that string: the SDK renders a class id COM
ordered on Windows, so the host prints
`{ABAB0101-CDAB-01EF-4155-52414445434C}` for the same sixteen bytes.  The
repository has a patch about the consequence of that
(`var/patches/f3_classid.patch`).

### 1.4 The latency and the tail

A plug-in tells its host two things about the time it takes: the **latency**
it adds (`getLatencySamples`) and the **tail** it keeps ringing for
(`getTailSamples`).  A host waits for the latency, so that the plug-in sits
in the right place in the session, and for the tail, so that it does not
cut the plug-in off while it is still sounding.

Both come from the core.  A module declares its latency
(`Module.latency_samples`) and its tail (`Module.tail_samples`), and the
engine adds the latencies of the chain up and reports the **longest** tail
of its modules: the modules of a chain are clocked together, block by
block, and a host that chains two plug-ins waits for the longer of the two
tails and not for the sum.

The tail is counted from the end of the module's own latency, so the time
from the input stopping until the output of the plug-in is quiet is the
latency plus the tail.  The tail is counted in the samples of the **engine's**
own rate, like the latency: a tail of 600 samples at 44.1 kHz is 653
samples at 48 kHz, and a host that works at 48 kHz waits for 653.
Measured by the independent host (`qa/vst3host`,
2026-10-21) against the built family:

| Module | Latency | Tail | What the host measures |
|---|---|---|---|
| Deconstruct | 2048 | 1214 | 1214 samples of ring-out beyond the latency |
| Voice De-Noise | 2047 | 1536 | 1232 samples of ring-out beyond the latency, 1536 declared |
| Guitar De-Noise | 0 | 600 | 600 samples of ring-out |
| Ambience Match | 0 | `kInfiniteTail` | the bed of the module never stops |
| Signal Generator | 0 | `kInfiniteTail` | the tone of the module never stops |
| the other 43 | as the module reports | 0 | no ring-out |

A module that makes a sound of its own — the generator, the bed of the
ambience matcher — never goes quiet, and reports the infinite tail the
format defines.  A host that stops the stream cuts that sound off, so the
plug-in has to say so.

---

## 2. Where the bundles are installed, and how a host finds them

### 2.0 What the package contains, and how to install it

**The full package is 75.0 MiB** (78 605 375 bytes, 1753 files, against a
700 MiB budget) and the **slim package is 3.2 MiB** (3 351 535 bytes, 137
files, against a 145 MiB budget).  The difference is the plug-in layer: the
slim package has no bundles, no shared runtime and no core library.

What is in the full package, as `dist/full/manifest.json` counts it:

| Part | Files | Bytes | MiB |
|---|---:|---:|---:|
| the shared runtime `plugins/vst3/build/stamped/Auralis Runtime` | 1567 | 66 004 560 | 62.9 |
| the 48 plug-in bundles | 48 | 9 043 968 | 8.6 |
| the application (`aurcore`, `aurserver`, `aurui`, the launchers) | 109 | 2 512 561 | 2.4 |
| the documentation | 13 | 717 425 | 0.7 |
| the core C ABI library (`lib/aurcore_abi.dll`) | 1 | 205 312 | 0.2 |
| the preset library | 10 | 11 220 | 0.0 |
| the assets, the launchers and the tools | 5 | 110 329 | 0.1 |

The shared runtime is the big part, and it is big because it **is the home
of the interpreter**: it holds the core, `python315.dll`, `Lib/` (the
standard library and `site-packages/numpy`) and `DLLs/` (the extension
modules).  A user therefore needs no Python installation at all — the
package carries the interpreter it ships.

**To install:**

```
python scripts/build_release.py                  # per-user
python scripts/build_release.py --per-machine     # machine-wide
```

The builder assembles the release tree, writes the manifest with the
SHA-256 of every file, writes the zip, and compiles the Windows installer
when the installer compiler is on the machine.  The installer writes:

```
<install directory>\
    aurcore\            the DSP core
    aurserver\          the local server
    aurui\              the interface
    bin\                the launchers
    docs\               the documentation
    lib\aurcore_abi.dll the core C ABI library
    plugins\vst3\
        Auralis De-Click.vst3\Auralis De-Click.vst3.dll
        ...                              the other 47 bundles
        Auralis Runtime\                the shared runtime
    var\presets\builtin\                the preset library
```

The bundles and the runtime directory are **siblings**, and they have to
stay siblings: the plug-in looks for `Auralis Runtime` next to the bundle
(section 3.4), so copying a single bundle out of the tree leaves it
without a core and without an interpreter.

**To use the family in a host:** add

```
<install directory>\plugins\vst3
```

to the host's VST3 plug-in path, and the host finds the 48 bundles there.
Auralis does not write into a host's own plug-in path (section 2.3).

### 2.1 The two scopes

The install scope is fixed **when the installer is compiled**, never at run
time, so the install directory and the registry root can never disagree
(`packaging/installer.iss`, header comment):

| | Per-user (default) | Per-machine (`--per-machine`) |
|---|---|---|
| Application directory | `{localappdata}\Programs\Auralis` | `{autopf}\Auralis` |
| Plug-in directory | `{localappdata}\Programs\Auralis\plugins\vst3` | `{autopf}\Auralis\plugins\vst3` |
| Registry root | `HKCU` | `HKLM` |
| Elevation | none | administrator |

`{localappdata}` is the per-user application data directory (`%LOCALAPPDATA%`
on Windows) and `{autopf}` is the program files directory; both are the
installer constants, written as the installer spells them.

The release builder produces both installers:

```
python scripts/build_release.py                  # per-user
python scripts/build_release.py --per-machine     # machine-wide
```

### 2.2 What the installer does with the bundles

`packaging/installer.iss` copies the built bundles with one recursive entry
and nothing else:

```
Source: "{#PluginRoot}\*"; DestDir: "{code:PluginBundleDir('VST3')}"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
```

The destination is not a literal path: `PluginBundleDir` reads it back from
the registry, and the installer **refuses to continue** when the registry does
not carry it ("The VST3 plug-in directory is not registered.  The installer
will not guess a path.").  The same rule applies to every later use of the
path, and after the install every enabled format is resolved again through the
registry and checked to exist.

The registry keys the installer writes:

| Key | Per-user | Per-machine | Value |
|---|---|---|---|
| `Software\Auralis\PluginPaths\VST3` | `HKCU` | `HKLM` | `<install directory>\plugins\vst3` |
| `Software\Auralis\PluginPaths\AU` | `HKCU` | `HKLM` | `<install directory>\plugins\au` |
| `Software\Auralis\PluginPaths\AAX` | `HKCU` | `HKLM` | `<install directory>\plugins\aax` |
| `Software\Auralis\PluginFormats\VST3` | `HKCU` | `HKLM` | `1` (installed and enabled) |
| `Software\Auralis\PluginFormats\AU` | `HKCU` | `HKLM` | `0` (present but disabled) |
| `Software\Auralis\PluginFormats\AAX` | `HKCU` | `HKLM` | `0` (present but disabled) |

VST3 is the format that exists today and the only one that is enabled.  The
uninstall removes the registry keys it created, the `Software\Classes` entries
for the session document format, and the directories `{app}\plugins` and
`{app}\lib`.

### 2.3 What a host has to do

**A host does not find these bundles by itself.**  The installer registers
the directory with *Auralis*, under `Software\Auralis\PluginPaths`; it does
not write anything into a host's own plug-in path, and the Auralis registry
tree is not a discovery mechanism any host reads.  To use the family in a
host, the user adds the directory

```
<install directory>\plugins\vst3
```

to the host's VST3 plug-in path, and the host finds the 48 bundles there.
`packaging/README.md` says the same thing in section 8: "The host
registration is ours.  The bundle directories are registered under
`Software\Auralis`, which is where Auralis looks.  Registering the bundles
with a host's own discovery mechanism is a separate piece of work per host."

The uninstall removes the whole `{app}\plugins` directory, so uninstalling
Auralis removes the plug-ins from the host as well.

### 2.4 The two packages

`scripts/build_release.py` assembles two profiles.  The numbers below are
the ones the release builder writes into the manifest of the package it
just built (`dist/full/manifest.json`, `dist/slim/manifest.json`, built on
2026-10-21):

| Package | Contents | Budget | Measured |
|---|---|---|---|
| full | the workbench, the resources, the help, the preset library, the core C ABI library, the shared runtime and the plug-in bundles | 700 MiB (734 003 200 bytes) | **78 605 375 bytes (75.0 MiB)** in 1753 files — 11 % of the budget |
| slim | the same, without the plug-in layer: no bundles, no runtime, no core library | 145 MiB (152 043 520 bytes) | **3 351 535 bytes (3.2 MiB)** in 137 files — 2 % of the budget |

What the full package is made of is in section 2.0; the short version is
that the shared runtime is the part that grows, because it holds the
standard library of the interpreter and the packages the core imports.  The
slim package has none of it, which is why it is 3.2 MiB.

The two ends of the packaging meet in one path.  The release builder stages
the bundles in the release tree under `plugins/vst3/build/stamped`, and the
installer's default `PluginRoot` is
`{src}\..\dist\full\plugins\vst3\build\stamped` — the same place inside the
assembled tree, so the installer picks the staged bundles up without a
second path to configure.  The builder also refuses to assemble a package
whose plug-in family is incomplete or unstamped.

### 2.5 The layout inside a bundle, and next to it

The native build writes a **flat** bundle that holds the plug-in alone:

```
Auralis De-Click.vst3\
    Auralis De-Click.vst3.dll     the plug-in, 188 416 bytes
```

The core and the interpreter are **shared by the whole family** and live
once, in a directory next to the bundles:

```
plugins\vst3\
    Auralis De-Click.vst3\
        Auralis De-Click.vst3.dll   the plug-in
    ...                              the other 47 bundles
    Auralis Runtime\
        aurcore_abi.dll              the core C ABI
        python315.dll                the interpreter
        Lib\                         the standard library and site-packages/numpy
        DLLs\                        the extension modules
        runtime-manifest.json        what the runtime holds
```

The runtime directory is **the home of the interpreter** (section 3.2), and
the plug-in finds it by itself: it looks for a directory called
`Auralis Runtime` next to the bundle, in the directory above that, or in the
directory above *that* — four levels are walked, which covers the build
layout and the installed one.  `AURALIS_RUNTIME_DIR` overrides the search
when it is set.

(The comment in `packaging/installer.iss` describes the framework-based
bundle layout, `<name>.vst3\Contents\x86_64-win\<name>.vst3`.  The installer
copies the bundle recursively, so it installs whatever the build produced.)

---

## 3. What a plug-in needs at run time

### 3.1 The three things

| What | What it is | Where it comes from |
|---|---|---|
| the core library | `aurcore_abi.dll`, the implementation of the C ABI in `plugins/vst3/aurcore_c_api.h` | built by `python plugins/abi/build_abi.py --out plugins/abi/build_main`, the path `scripts/build_vst3.py` hands to the build |
| the interpreter | `python315.dll` on this machine — the core embeds CPython | copied next to the core by `plugins/vst3native/CMakeLists.txt`, and then into the shared runtime directory by the staging |
| the core package | `aurcore`, the Python package with the DSP | **not** in the bundle and **not** in the runtime: the core finds it in the directories above the runtime directory; see 3.3 |

The adapter reaches the core only through the five required entry points of
`plugins/vst3/aurcore_c_api.h` (`aur_engine_create`, `aur_engine_add_module`,
`aur_engine_set_param`, `aur_engine_process`, `aur_engine_destroy`) plus the
extensions it needs (`aur_engine_set_param_text` for the enums,
`aur_engine_get_param` to read the core back, `aur_engine_serialize` and
`aur_engine_deserialize` for the state, `aur_engine_free`,
`aur_engine_latency_samples`, `aur_engine_tail_samples`,
`aur_engine_output_sample_rate`, `aur_engine_last_error` and
`aur_shutdown`).
The adapter never sees the core's implementation.  The engine is always
created with two channels, which is the one stereo bus the plug-in offers.

The export table of the core has **eighteen** entries — the five required
entry points, `aur_shutdown` and the twelve extensions — and the version of
the contract is 3 (`AUR_ABI_VERSION` in
`plugins/vst3/aurcore_c_api.h`).  The table is derived from that header and
from nowhere else: `plugins/abi/build_abi.py` reads the declarations out of
it, writes them into the module definition file the linker uses, and
refuses to build when the implementation does not define every one of them.
`aur_engine_tail_samples` is the export that grew the table from sixteen
entries to seventeen, and `aur_engine_output_sample_rate` took it to
eighteen.

### 3.2 The home of the interpreter

**The core finds the home of the interpreter itself.**  The user does not
set an environment variable for the plug-in to work.

The core library is a shared library, so it knows the directory it was
loaded from, and the home is the first of these that holds a standard
library **of this interpreter** (`plugins/abi/aurcore_abi.cpp`,
`aur_find_python_home`):

1. the directory the core was loaded from — in the shipped arrangement that
   is the **runtime directory**, which *is* the home;
2. the directory next to it, for the arrangement where the runtime
   directory is a sibling of the core;
3. the `Python` directory next to it, for the arrangement where the home is
   a directory of its own;
4. the `PYTHONHOME` of the environment, for a deployment that carries no
   home of its own.

A candidate that holds the interpreter library of **another version** is
refused: the standard library of one version is not the standard library of
another.

The home is handed to the interpreter with `PyConfig.home`, **not** with the
`PYTHONHOME` of the process, so the environment of the host is left alone.

When there is no home, the interpreter is **not started at all**.  The core
says so on the error stream and in the message of the library, and it names
every directory it searched:

```
auralis: the interpreter could not be started: its standard library was not
found.  The core looked in "<runtime directory>", in "<its parent>" and in
"<runtime directory>/Python", and there is no PYTHONHOME in the environment.
The package has to carry the standard library of the interpreter next to it.
```

The host reads that message and puts it in its report; the host stays
alive, the exit code is 2 (a step of the exercise failed), and the plug-in
passes the audio through unchanged.  Before the core found the home itself
the same failure took the host down inside the interpreter's own fatal
handler, with no report and no word about the reason.

Measured with the independent host `qa/vst3host` against the built
De-Click bundle, with `PYTHONHOME`, `PYTHONPATH`, `AURALIS_RUNTIME_DIR` and
`AURALIS_VST3HOST_DEPS` all taken out of the environment: exit code 0, no
fault, the 1 kHz tone in at 0.5 peak and out at 0.5 peak.

### 3.3 The core package

The core package is the Python source of the DSP, and it is **not** in the
bundle and **not** in the runtime directory.  The core finds it by walking
up from the directory it was loaded from and taking the closest directory
that holds `aurcore/__init__.py` (`aur_add_core_package_path`, six levels
up).  In the installed tree the runtime directory is
`<install directory>\plugins\vst3\Auralis Runtime`, so the package is found
in `<install directory>` — four levels up — with **no environment variable
at all**.

Two overrides remain, and they are for a deployment that is deliberately
unusual:

* `AURALIS_ROOT` names the directory the core package is in;
* the directory the core library itself was loaded from, that directory plus
  `Python`, and the current directory are on the interpreter's path as
  well.

What the plug-in does when the core cannot be created: it reports the
problem in the log and passes the audio through unchanged
(`plugins/vst3native/AuralisVST3.cpp`, the `process` method).

### 3.4 The Windows loader

The Windows loader searches the *host's* directory for a plug-in's
dependencies, not the plug-in's own directory, so a bundle that no longer
carries the core cannot rely on the loader to find it.  The native adapter
does not rely on it: the import of the core is a **delay-load descriptor**
and the plug-in loads the core out of the runtime directory itself, with
`LOAD_WITH_ALTERED_SEARCH_PATH` (which also makes the loader find the
interpreter next to it).

The rule that finds the runtime directory, from the plug-in's own directory:

1. the `AURALIS_RUNTIME_DIR` of the environment, when it is set;
2. a directory called `Auralis Runtime` next to the bundle;
3. one in the bundle's parent directory (the build layout, where the bundle
   sits in `<module>/stamped/`);
4. one in the directory above that (the family root of the build layout);
5. the bundle's own directory (the old layout, where the bundle still
   carried the runtime).

The first four are what the code does today; the fifth is the arrangement
that no longer exists.  The tooling stages the runtime the same way
(`plugins/vst3native/stage_runtime.py`), so the tool and the plug-in always
agree.

---

## 4. The parameters

### 4.1 Where the names come from

The canonical table `aurcore/schema/params.tsv` is the single source of truth.
Its `param` column is the **canonical core parameter name**; the same strings
are

* the names the modules implement (`aurcore/params.py`: "Parameter identity
  is the human-readable NAME.  There are no numeric parameter ids in the
  core."),
* the names the server hands to the interface (`/api/modules` and
  `/api/chain/{index}/params` return the `ParamDesc` list; the panel renders
  `desc.name` as the label of the control),
* the names in the plug-in's identity record (`param_id_map`), and
* the titles the host shows (`getParameterInfo` copies the name into
  `info.title` and `info.shortTitle`).

So a knob in the application and a knob in the host carry the **same name**.
Checked for all 48 modules: every parameter name in the canonical table is in
the identity record of the built plug-in for that module, and the title the
plug-in publishes is that name.

### 4.2 Units

The core works in **natural units** and never sees a normalised knob
position; the adapter converts (`plugins/vst3/aurcore_c_api.h`: "The values
are in NATURAL UNITS: dB, Hz, ms, %, semitones.  The core never sees a
normalised 0..1 knob position; the adapter converts."; the conversion itself
is `normalizedValue` / `naturalValue` in the adapter).  The unit of a
parameter is the `unit` column of the canonical table and is published to the
host in `info.units`.

| Unit | Parameters |
|---|---|
| none (dimensionless) | 165 |
| dB | 40 |
| Hz | 18 |
| s | 6 |
| dBFS | 5 |
| ms | 5 |
| LUFS | 5 |
| dBTP | 2 |
| LU | 1 |
| deg | 1 |
| % | 3 |
| st (semitones) | 2 |
| **total** | **253** |

### 4.3 Which are stepped and which are continuous

The host sees a stepped parameter as one with a `stepCount` above zero, and a
continuous one as one with `stepCount == 0`.  The adapter derives it from the
type of the parameter (`getParameterInfo`):

| Parameter type | Count | `stepCount` | Continuous or stepped |
|---|---|---|---|
| float | 169 | 0 | continuous |
| enum | 39 | number of choices − 1 | stepped |
| bool | 41 | 1 | stepped |
| int | 4 | maximum − minimum | stepped |
| **total** | **253** | | **169 continuous, 84 stepped** |

A float parameter is continuous even when its range is narrow; `De-Click`'s
`Sensitivity` (0 … 10) is continuous with a default of 3.0, published as the
normalised value 0.3000.

### 4.4 Enum values

An enum parameter carries its **choice string**, not an index, into the core
(`aur_engine_set_param_text`); the adapter converts between the host's
normalised position and the choice index.  The choices are the ones in the
canonical table, for example `Algorithm` for De-Click:
`Single Band | MB Periodic | MB Random | Low Latency`.

### 4.5 The host parameter ids

The host parameter id is an integer the *adapter* owns; the core has no
numeric ids.  The id comes from the generated registry index
(`aurcore/schema/generated.py`), and the identity record maps it to the
canonical parameter name (`param_id_map`).  The number is only meaningful
together with the module: a plug-in carries exactly one module, and the id
tells the host which parameter of that module it is.

As the registry stands, the ids are numbered **inside** the module (0 … n−1),
so they are unique inside a plug-in but not across the family: the ids of
De-Click are 0 … 4 and the ids of De-Hum are 0 … 18.  `plugins/README.md`
and the AAX notes describe them the same way — per module, never registry-wide
— and the AAX package generator checks its own numbering against the
registry index of the plug-in layer, so the two cannot drift apart
(`scripts/aax_tables.py`, `cross_check`).

**The binary and its identity record must come from the same snapshot of the
registry.**  The generated table is compiled into the binary and the record
is stamped into it; if the two disagree, the adapter finds no entry whose id
the record knows and the plug-in publishes **no parameters at all**.  The
check in section 7.2 catches exactly that, and the fix is a rebuild.

---

## 5. The state

### 5.1 The format

The plug-in writes one blob, the shared `auralis.state.v1` convention, which
is the same container the framework-based adapter writes, byte for byte:

```
"auralis.state.v1" NUL
<core chain document> NUL
<host parameter state> NUL
```

Every field is NUL terminated.  `getState` and `setState` on the component
and on the controller return and accept the same blob.

**Field 2 — the chain document.**  The JSON document of CONTRACT §6.3, the
one the application writes and reads:

```json
{"format": "auralis.chain", "version": 1,
 "steps": [{"code": "AUR_DECLICK", "enabled": true,
            "params": {"Sensitivity": {"Type": "Float", "Value": 3.0}},
            "state": {"Type": "String", "Value": ""}}]}
```

The plug-in produces it with `aur_engine_serialize` and consumes it with
`aur_engine_deserialize`; both go straight to `Engine.serialize()` and
`Engine.deserialize()` in `aurcore/api.py`.

**Field 3 — the parameter document.**  An XML document with one entry per
parameter, holding the **natural** value (dB, Hz, ms, %):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<AURALIS>
  <PARAM id="0" value="0.0" />
  <PARAM id="1" value="3.5" />
</AURALIS>
```

The entries are ordered by the parameter id **as text**, which is the order
the framework-based adapter's parameter table uses.  An enum parameter
carries the index of its choice.  An id the reader does not know is ignored,
so a state written by a build that knows more parameters still loads.

### 5.2 A session crosses the boundary in both directions

Both directions were exercised against the built De-Click plug-in, with the
plug-in loaded the way a host loads it:

* **plug-in → application.**  The state chunk the plug-in writes after
  processing a block is 655 bytes in three fields; the marker is
  `auralis.state.v1`, the chain field is a JSON document with
  `"format": "auralis.chain"`, `"version": 1` and one step (`AUR_DECLICK`,
  `enabled: true`, the five parameters in their natural units), and the
  third field is the XML parameter document.  Loading the chain field into an
  application engine (`Engine.deserialize`) restores the module: one module in
  the chain, `Sensitivity` 3.0, `Algorithm` `Single Band`.
* **application → plug-in.**  A chunk built from an application chain
  document (`Sensitivity` 7.5, `Algorithm` `MB Periodic`) plus a parameter
  document is handed to the plug-in with `setState`; it answers
  `kResultOk`, the host-visible parameters come back at 0.75 and 0.3333
  (7.5 out of 10, one of three choices), and the chain the plug-in writes out
  again is **equal to the application document**.

The core is authoritative: `setState` applies the host parameter state first
and then the chain document, and the values the core reports win afterwards
(`pullParamsFromCore`).  A parameter that is in both therefore keeps the
**chain's** value: in the measurement above the parameter document carried
`Frequency Skew = -0.25` and the chain carried `0.0`, and the plug-in
reported 0.5 — the normalised form of the chain's value, because the range is
−1 … 1.

### 5.3 What the ids in the state document mean

Field 3 addresses the parameters by their **host parameter id**, so a session
written by one build loads in another only as long as the ids have not
moved.  The rule that keeps them stable is in `plugins/README.md`,
"Parameter id stability": a new parameter is appended to the end of its
module, a module is never removed from the middle of the registry, and ids
are never renumbered for cosmetic reasons.

---

## 6. The known limits

These are the things the family does not do yet.  Every number below names
the file it was measured in.

### 6.1 The DSP quality gaps

The plug-in is a thin shell over the same DSP the application uses, so every
measured defect of the core is a defect of the plug-in.

**Spectral De-Noise** (`qa/quality/REPORT.md`, section 2).  On a stationary
noise bed it removes **10.87 dB** where the target is 12 dB, and the gain
curve in the noise band varies by **1.78 dB²** where the target is 1.0 dB².
On a voice-like signal it removes 32.00 dB of noise, but it takes **7.64 dB**
off a wanted partial (480 Hz) where the budget is 1 dB.  The proposed fix is
written down in `qa/quality/spectral_de_noise.patch` and is **not** applied.

**Voice De-Noise** (`qa/qualityvoice/REPORT.md`, sections 3 and 4.3).  With
the shipped defaults it removes **6.75 dB** of hiss where the target is 10 dB,
and it takes **6.51 dB** off the voice where the budget is 1.5 dB.  It is
also not block coherent: a 4096-sample block differs from a one-block render
by **20.4 dB** RMS.  The measurements, the cause and the recommended
defaults are in the same report, section 7.

**De-Click** (`var/TEAM_BRIEF.md`, defect 3).  It fires on steady material:
the brief records that at Sensitivity 6.0 a 1 kHz programme loses
**10.3 dB**, while Sensitivity 3.0, 7.0 and 9.0 are clean.  Measured on a
pure 1 kHz sine at 48 kHz with the shipped code, the level is unchanged at
Sensitivity 0.0, 3.0, 6.0, 7.0, 9.0 and 10.0 (−9.03 dBFS in and out), so the
damage the brief reports needs programme material, not a sine.

### 6.2 The real-time budget

The modules pay a **fixed analysis cost per block**, so their cost does not
fall with the block size.  Measured on the reference machine
(`qa/performance/REPORT.md`, section 2, budget = `block / 48000` seconds):

* **13 of the 48 modules are over the 64-sample budget**; 7 are over it at
  128 samples, 4 at 256, and 2 (Spectral De-Noise and Resample) are over it
  at every block size;
* the worst two need **23x** (Spectral De-Noise, 31 161 µs against a 1333 µs
  budget) and **15x** (Resample, 19 973 µs) the 64-sample budget;
* the realistic dialogue chain (De-Hum → De-Click → De-Ess → De-Plosive →
  Gain) needs **2.2x** the 64-sample budget and fits into the 256-sample
  budget with 41 % headroom;
* every module allocates inside `process()`, between 0 and 10 082 arrays per
  block.

The report's own recommendation: **256 samples is the minimum block size
that makes the catalogue usable** (section 7.8).  A host that renders in
64-sample blocks will glitch on the heavy modules.

### 6.3 The size of the family

The family used to carry the interpreter **48 times** — 374.5 MiB, 96 % of
it one file copied.  The interpreter and the core now live **once**, in the
shared runtime directory next to the bundles, so the bundles are small.

Measured on the release tree (`dist/full`, built 2026-10-21) and on the
build tree (`build/vst3native`), with a directory walk:

| | |
|---|---|
| the bundles (release tree) | 48 |
| one bundle (De-Click) | **188 416 bytes** (0.18 MiB): the plug-in alone |
| the size range over the 48 | 188 416 bytes (all 48 are the same size) |
| all 48 bundles together | **9 043 968 bytes** (8.6 MiB) |
| the bundles in the build tree | 189 952 bytes each, 9 117 696 bytes (8.7 MiB) together — the binary grew with the last change of the contract |
| the shared runtime in the package | 66 004 560 bytes (62.9 MiB) in 1567 files |
| the shared runtime in the build tree | 78 316 471 bytes (74.7 MiB) in 1974 files — the figure moves with every build, because the interpreter writes its byte code into the home |
| of which the home of the interpreter | 69 824 019 bytes (66.59 MiB) in 1971 files |
| the full package | 78 605 375 bytes (75.0 MiB) against a 700 MiB budget |

The package's runtime is 13.2 MiB smaller than the same runtime in a build
tree whose interpreter has written its byte code into it — 11.8 MiB of that
is in numpy alone — because the release builder leaves the `__pycache__`
directories out.

The price of the arrangement is that the runtime directory has to travel
with the bundles: a host that is given one bundle and nothing else has no
core and no interpreter.  The plug-in loads the core itself out of the
runtime directory (section 3.4), so the runtime only has to be *next to* the
bundles, not inside every one of them.

### 6.4 The plug-in shows the host's generic editor

`createView` returns `nullptr`
(`plugins/vst3native/AuralisVST3.cpp`), so the host opens **its own**
generic parameter editor: the parameter names, the units, the ranges and the
enums are there, the Auralis interface is not.  `openHelp` and
`openAboutBox` answer `kResultFalse`.  The Auralis interface belongs to the
user interface layer, which is not part of the adapter.

### 6.5 The core package is not in the bundle

See section 3.3: the core package (`aurcore`) is neither in the bundle nor in
the runtime directory.  The core finds it in the directories above the
runtime directory, so an installed package works with no environment
variable — but a bundle that is copied **on its own**, away from the
application, has no DSP behind it: the plug-in reports that it could not
create the core engine and passes the audio through unchanged.

### 6.6 A registry change needs a rebuild

See section 4.5: the parameter table is compiled into the binary and the
identity record is stamped into it, and the two have to agree.  A plug-in
built against one snapshot of the registry and stamped with another publishes
**no parameters**.  The check in section 7.2 catches it.

### 6.7 The identifiers are locally generated

The class ids and the AAX product ids are derived locally and have to be
registered with the plug-in format bodies before the family ships
(`plugins/README.md`; `plugins/aax/AAX_ADAPTER_NOTES.md`).  The alpha ships
unsigned (`packaging/README.md`, section 5).

### 6.8 The component and the controller are one object

The plug-in answers the component role, the processor role and the controller
role from one object.  The objects are therefore not deleted on the last
release; the memory belongs to the module and the operating system reclaims
it when the host unloads the module.  The proper arrangement — one object
for the component, one for the controller, with the connection point carrying
the parameter values — is written down in `plugins/vst3native/README.md`,
section "The reference counting, and why the objects are not deleted".

---

## 7. Rebuilding the family, and checking it

### 7.1 The build

```
python scripts/build_vst3.py                       # all 48
python scripts/build_vst3.py --only AUR_DECLICK    # one
python scripts/build_vst3.py --jobs 2              # two compilers at a time
```

The script reads the module list from the registry
(`plugins/vst3/build_identity.py list --format cmake`), so it never goes
stale, and it builds one module after another on purpose: a parallel fleet of
compilers costs more memory than the machine should give up.  The last full
run of the family took 8.1 minutes, 9–11 s per module
(`var/TEAM_BRIEF.md`, section 3).

What the build needs:

| Requirement | Where it comes from |
|---|---|
| the core C ABI library | `python plugins/abi/build_abi.py --out plugins/abi/build_main` → `plugins/abi/build_main/aurcore_abi.dll`, the path `scripts/build_vst3.py` hands to the build |
| the VST3 SDK 3.8.1 | `vendor/vst3sdk` (the default) |
| cmake and ninja | `%LOCALAPPDATA%\Python\pythoncore-3.15-64\Scripts` |
| the MSVC environment | `tools/msvc.bat` (the script runs every compiler through it) |
| the interpreter and its home | the ones next to `aurcore_abi.dll` (`python315.dll` on this machine); the build copies the home of the interpreter next to the library, and the staging puts the whole runtime in one directory |

The output of one module is

```
build/vst3native/AUR_DECLICK/bin/Auralis De-Click.dll                 the linked module
build/vst3native/AUR_DECLICK/stamped/Auralis De-Click.vst3/            the bundle
```

and the runtime of the family, next to the bundles:

```
build/vst3native/Auralis Runtime/                                     the shared runtime
```

### 7.2 The check that runs after every link

The build verifies each module it has just linked, by loading it the way a
host loads it (`plugins/vst3native/CMakeLists.txt` writes the two scripts
into the build tree):

| Check | What it proves |
|---|---|
| `check_vst3_export.py` | the module exports `GetPluginFactory` and `ModuleEntry`, the factory answers, exactly one class is reported, and the class id it reports is the class id in the identity record |
| `check_vst3_state.py` | the controller publishes exactly the parameters of the record's `param_id_map`, every published title is the name the record gives, the state chunk is the shared `auralis.state.v1` container with three fields, and a parameter survives a round trip through the state |

The second check is the one that catches the mismatch of section 4.5: it
fails when the published count is not the count of the record's map.

### 7.3 The host

`qa/vst3host` is an independent, DAW-free host written against the SDK.  It
loads a built module, enumerates the classes, creates the component and the
controller, connects them, accepts the bus arrangement, enumerates the
parameters, drives one of them, processes a block (silence, and with
`--tone` a 1 kHz tone), saves and restores the state, and writes a JSON
report on standard output.

```
python qa/vst3host/run_host.py                       # build the host, find a module, run it
python qa/vst3host/run_host.py "<bundle>" --tone     # a specific module
python qa/vst3host/run_host.py --list                # the modules it found
```

Exit codes: 0 everything worked, 1 the module could not be loaded or
instantiated, 2 a step of the exercise failed.  A module that faults while it
is being torn down is reported in `faults` and does not change the exit code.

### 7.4 The gate

```
python -m pytest tests/test_vst3_host.py -q          # the family under the host
python -m pytest tests/test_plugin_abi.py -q          # the C ABI contract
python -m pytest tests -q                             # the whole suite
python tools/validate_schema.py                       # no drift between the table and the modules
python tools/hygiene_check.py                         # 0 findings: no third-party name anywhere
python tools/lint_check.py                            # the style gate
python qa/sweep_modules.py                            # every module over five awkward inputs
```

`tests/test_vst3_host.py` drives three modules with different shapes —
`AUR_DECLICK`, `AUR_SPECTRALEDITOR` and `AUR_SIGNALGEN` — and asserts that
the host accepted them, that the parameters carry names and units, that the
audio path ran, that the state comes back in both directions, and that the
module did not fault on the way out.  The test skips itself when the host or
the plug-ins are not built.

`qa/sweep_modules.py` runs every module over silence, a constant signal, a
full-scale square, a single impulse and a pure tone, and reports every
module that returns a non-finite sample, a wrong length or raises.

The hygiene gate is the one that has to report **0 findings**: no
third-party product, vendor or trademark may appear anywhere in the tree,
this document included.

### 7.5 What the check said when this document was written

Everything in this section was measured on **2026-10-21**, against the
release tree that `scripts/build_release.py` assembled at 15:34 and the
build tree next to it.

| What | How | Result |
|---|---|---|
| the registry | `aurcore/schema/generated.py` | 48 modules, 253 parameters, 26 Repair / 18 Utility / 2 Measurement / 2 System |
| the family | a directory walk over `dist/full/plugins/vst3/build/stamped` | 48 bundles, 9 043 968 bytes (8.6 MiB) — 188 416 bytes each |
| the shared runtime | the same walk over `dist/full/.../Auralis Runtime` | 66 004 560 bytes (62.9 MiB) in 1567 files; 78 316 471 bytes (74.7 MiB) in the build tree |
| the packages | `dist/full/manifest.json`, `dist/slim/manifest.json` | full 78 605 375 bytes (75.0 MiB) of a 700 MiB budget; slim 3 351 535 bytes (3.2 MiB) of 145 MiB |
| every plug-in against the canonical table | the identity record of every shipped bundle against the canonical table | **48 of 48 records carry exactly the names of the table** |
| the family under the host | `qa/vst3host` against the built family (`var/TEAM_BRIEF.md`, section 3) | 48 bundles load, connect, publish their parameters, process audio and restore their state — exit 0, no fault |
| the state | the plug-in loaded the way a host loads it | `auralis.state.v1`, three fields, the chain field is the `auralis.chain` document; both directions of the hand-off work (section 5.2) |
| the core's home | the independent host with `PYTHONHOME`, `PYTHONPATH`, `AURALIS_RUNTIME_DIR` and `AURALIS_VST3HOST_DEPS` taken out of the environment | exit code 0, no fault, the 1 kHz tone in at 0.5 peak and out at 0.5 peak |
| the C ABI | the export table of the core that is built now (`plugins/abi/build_main/aurcore_abi.dll`) | **18 exports**, contract version 3, `aur_engine_tail_samples` and `aur_engine_output_sample_rate` among them |
| the AAX package | `python scripts\aax_tables.py --check` | green — 48 modules, 112 pages, 253 parameters |
| the hygiene gate | `python tools/hygiene_check.py` | **0 findings** in 363 files, this document included |

The parameter ids of section 4.5 are the same in every document now:
`plugins/README.md` and the AAX notes describe them as the ids inside a
module, and the AAX package generator checks its numbering against the
registry index of the plug-in layer, so the three cannot drift apart.

One snapshot caveat, stated plainly: the package in `dist/full` was
assembled at 15:34 and the contract changed after that (the export table
grew from seventeen entries to eighteen and the version went from 2 to 3),
so the core inside that package has seventeen exports.  A release build
made now carries the current one.

The family has to be rebuilt whenever the canonical table changes: the
parameter table is compiled into the binary and the identity record is
stamped into it, and the two have to come from the same snapshot (section
4.5).

---

## 8. Where the sources are

| Subject | File |
|---|---|
| the canonical parameter table | `aurcore/schema/params.tsv` |
| the registry the build reads | `aurcore/schema/generated.py` |
| the C ABI between the adapter and the core | `plugins/vst3/aurcore_c_api.h` |
| the implementation of the C ABI | `plugins/abi/aurcore_abi.cpp`, `plugins/abi/build_abi.py` |
| the home of the interpreter, copied next to the core | `plugins/abi/interpreter_home.py` |
| the shared runtime of the family | `plugins/vst3native/stage_runtime.py`, `scripts/vst3_runtime.py` |
| the adapter that ships | `plugins/vst3native/AuralisVST3.cpp` |
| the identity record | `plugins/identity.py`, `plugins/identity.schema.json` |
| the build of the family | `scripts/build_vst3.py`, `plugins/vst3native/CMakeLists.txt` |
| the installer | `packaging/installer.iss` |
| the release builder | `scripts/build_release.py` |
| the host | `qa/vst3host/` |
| the adapter layer overview | `plugins/README.md` |
