# Packaging and release

This directory holds the shipping half of Auralis: the installer script and the
notes that go with it. The release builder lives in
[`scripts/build_release.py`](../scripts/build_release.py) and the tests that
keep the two honest live in
[`tests/test_packaging.py`](../tests/test_packaging.py) and
[`tests/test_installer.py`](../tests/test_installer.py).

```
packaging/
  installer.iss      the installation script (the single source of truth)
  README.md          this file
```

---

## 1. The two packages

| Package | What it contains | Budget |
|---|---|---|
| **full** | the workbench (`aurui`, `aurcore`, `aurserver`), the resources (`assets`), the help (`docs`), the shipped preset library (`var/presets`), the core C ABI library and the built plug-in bundles | 700 MB |
| **slim** | the same, minus the plug-in layer: no plug-in bundles, no core C ABI library | 145 MB |

The split is not cosmetic. The plug-in layer is the part that grows: one
bundle per module, and the bundles carry the whole DSP core behind the C ABI.
The slim package is the standalone workbench — everything a user needs to
restore audio, and nothing that a host has to load.

Both packages are produced by one command, and the builder prints the measured
size of each against its budget:

```
  profile      measured        budget      headroom  verdict
  full         19.6 MiB     700.0 MiB     680.4 MiB  PASS
  slim          3.2 MiB     145.0 MiB     141.8 MiB  PASS
```

A package over its budget fails the build. `--allow-oversize` turns that into
a warning, which is what a release manager wants when the budget itself is
being renegotiated.

---

## 2. Building a release

```bash
python scripts/build_release.py
```

That is the whole command. It:

1. **runs the check suite** — `tools/validate_schema.py` (the canonical
   parameter table and the generated artefacts), `qa/check_css_coverage.py`
   (every class the interface uses is styled) and `tests/test_plugin_abi.py`
   (the C ABI contract, driven the way a host drives it). **A failing check
   stops the build**; nothing is assembled and nothing is packaged.
2. **assembles the release tree** for each profile under `dist/<profile>/`.
   The tree mirrors the repository layout, so the installer script can be
   compiled from the repository or from a release tree with the same rules.
3. **writes a manifest** — `dist/<profile>/manifest.json` — with the SHA-256
   and the size of every file, plus the total size. The manifest never lists
   itself.
4. **writes a zip** — `dist/auralis-<version>-<profile>.zip`. The entries are
   sorted and carry a fixed timestamp, so the same tree always produces the
   same archive.
5. **compiles the installer** when the Inno Setup compiler is available, and
   says so plainly when it is not.
6. **prints the size budget report**.

Useful switches:

| Switch | What it does |
|---|---|
| `--profile full\|slim\|both` | build one profile or both (default: both) |
| `--out DIR` | the output directory (default: `dist`) |
| `--version X.Y.Z` | the release version (default: the version in `pyproject.toml`) |
| `--skip-checks` | build without the gate — for a local dry run, never for a release |
| `--no-zip` | assemble and manifest, but do not zip |
| `--no-installer` | do not look for the installer compiler |
| `--per-machine` | compile the machine-wide installer instead of the per-user one |
| `--iscc PATH` | the path to the installer compiler |
| `--plugins DIR` | where the built plug-in bundles are |
| `--allow-oversize` | report a package over its budget without failing |

The builder never touches the network and never needs Inno Setup.

### What the check suite is for

The gate is the same set of checks the QA runbook asks for, minus the ones
that need a browser. The schema validator is the important one: it proves
that the canonical parameter table, the generated registry and the shipped
documentation still agree, so the plug-in parameters in a bundle match the
parameters the core exposes.

---

## 3. The installer

`packaging/installer.iss` is an Inno Setup 6.x script. It is plain text on
purpose: `scripts/build_release.py` parses it, and
`tests/test_packaging.py` and `tests/test_installer.py` check it, so the
layout is verified on every machine that has the repository — with or
without Inno Setup installed.

The script is compiled from a release tree: the builder writes a wrapper
that points the build symbols (the source root, the preset library, the core
library, the plug-in directory) at the tree it is packaging, and includes the
real script. Compiling it from the repository alone works as well — the
default plug-in directory is the tree the last build assembled.

### Per-user and per-machine

The scope is decided **when the script is compiled**, never at run time, so
the install directory, the registry root and the plug-in directory can never
disagree.

| | Per-user (default) | Per-machine (`--per-machine`) |
|---|---|---|
| Directory | `{localappdata}\Programs\Auralis` | `{autopf}\Auralis` |
| Registry root | `HKCU` | `HKLM` |
| Plug-in directory | `{localappdata}\Programs\Common\VST3` | `{commonpf}\VST3` |
| Elevation | none | administrator |
| Typical use | a single workbench on a workstation | a shared install on a studio machine |

The plug-in directory is the one a host scans for VST3 plug-ins: the
per-user one under the user's own profile, the machine-wide one under the
program files. Those are the two places a host looks and nowhere else, which
is why the bundles go there and not into the application directory.

```bash
python scripts/build_release.py --per-machine
```

The scope is written into the registry (`Software\Auralis\Scope`) and the
installer checks it again during the uninstall, so a per-user uninstall can
never remove a machine-wide installation and the other way round.

### What is installed

| Into | What |
|---|---|
| `{app}\aurui` | the web interface (HTML, CSS, JavaScript — no build step) |
| `{app}\aurcore` | the DSP core and its canonical parameter table |
| `{app}\aurserver` | the application server |
| `{app}\assets` | the resources |
| `{app}\docs` | the help: the architecture, the interface and the module reference |
| `{app}\var\presets\builtin` | the shipped preset library |
| `{app}\bin` | the launchers |
| `{app}\lib` | the core C ABI library (full package only) |
| `{app}\plugins\au`, `{app}\plugins\aax` | the two formats that do not exist yet: registered, disabled, empty |
| `{localappdata}\Programs\Common\VST3` (or `{commonpf}\VST3`) | the plug-in bundles (full package only) |
| `{localappdata}\Programs\Common\VST3\Auralis Runtime` (or `{commonpf}\VST3\Auralis Runtime`) | the shared runtime of the family: the core C ABI and the interpreter, one copy for the whole family (full package only) |

### Start menu and file associations

The installer creates a start menu group with the workbench, a server-only
launcher and the documentation, plus an optional desktop shortcut (the
"desktopicon" task). It registers the session document format `.aurdoc` with
the program identifier `Auralis.SessionDocument` and an open command that
launches the workbench with the document.

### Silent install, repair and uninstall

```bash
# silent, per user
Auralis-0.1.0a0-setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART

# silent, machine wide (the machine-wide build asks for elevation itself)
Auralis-0.1.0a0-setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART

# repair: run the installer again over the same installation
Auralis-0.1.0a0-setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /REBUILDICONS
```

Repair is a re-run of the same installer: the `AppId` is a fixed GUID that
does not change between versions, so the compiler recognises the existing
installation, updates the files and leaves the user's session data alone.

The uninstall removes the files the installer wrote, the start menu entries
and the registry keys it created — the `Software\Auralis` tree and the
`Software\Classes` entries for the document format — and the plug-in bundles
it put into the shared VST3 directory. The workbench's own data
(`{app}\var`: uploads, renders, the user's own presets) is **left in place**;
it is the user's work, not the installer's.

---

## 4. How a host finds the plug-ins

There are two answers to that question, and the installer gives both.

**A third-party host** looks in the two directories it always looks in. The
per-user build puts the bundles in

```
%LOCALAPPDATA%\Programs\Common\VST3
```

and the machine-wide build puts them in the common files folder of the
program files directory:

```
{commonpf}\VST3
```

(`{commonpf}` is the compiler's name for that folder; the installer uses
the constant, so the path follows the installation.)

A bundle is a **directory**, not a file: `<name>.vst3` holding
`<name>.vst3.dll` (and, on macOS, `<name>.vst3\Contents\MacOS\<name>.vst3`).
The installer copies the whole directory, with the layout it was built with,
so the files inside a bundle keep their own sub-directories. A bundle that
is missing from the build is skipped, not an error: the install goes on with
the bundles that are there.

The bundle holds the plug-in alone.  The core C ABI and the interpreter it
needs are shared by the whole family and live once, in the directory
`Auralis Runtime` next to the bundles — in the plug-in directory of the
host, which is where the plug-in looks for them.  Carried inside every
bundle they would be 48 copies of the same 7.8 MB file.

**Auralis itself** reads the directories out of the registry, under the
`PluginPaths` key the project already uses. The value is named after the
format:

```
HKCU\Software\Auralis\PluginPaths     "VST3" = %LOCALAPPDATA%\Programs\Common\VST3
HKCU\Software\Auralis\PluginFormats   "VST3" = 1
```

and the same pair of keys for the two formats that do not exist yet:

```
HKCU\Software\Auralis\PluginPaths     "AU"   = <install directory>\plugins\au
HKCU\Software\Auralis\PluginFormats   "AU"   = 0
HKCU\Software\Auralis\PluginPaths     "AAX"  = <install directory>\plugins\aax
HKCU\Software\Auralis\PluginFormats   "AAX"  = 0
```

The machine-wide build writes the same keys under `HKLM` and points
`PluginPaths` "VST3" at `{commonpf}\VST3`.

**VST3 is the format that exists today** and is the only one that is enabled.
The other two are *present but disabled*: their paths are registered and their
directories exist, but the format flag is `0`, so no host picks them up.
Enabling one is a matter of flipping the flag and shipping the bundles — not
of inventing a new registry layout.

The rule the installer follows is that **a path is never guessed**. The
directories are written into the registry during the install, and every later
use reads them back from there:

* the file destination of the bundles is resolved from the registry, and the
  install is refused if the registry does not carry the path;
* after the install, every enabled format is resolved again through the
  registry and the directory is checked to exist. A format that cannot be
  resolved stops the install with an error rather than a silent fallback.

### The uninstall

The uninstall removes what the install added:

| What | How |
|---|---|
| the application files | the uninstaller removes the files it wrote |
| `{app}\plugins` and `{app}\lib` | the `[UninstallDelete]` section removes the two directories |
| the registry keys | every `[Registry]` entry carries `uninsdeletekey`, and the uninstall code deletes the keys of the scope it belongs to, deepest first |
| the plug-in bundles | the uninstall code reads `PluginPaths\VST3` back and deletes the bundles of this family from it |
| the shared runtime | the uninstall code deletes the `Auralis Runtime` directory next to the bundles; it holds the runtime of this family and nothing else |

The shared VST3 directory is **not** deleted: it belongs to every product
that installs plug-ins, and the bundles of the other products live there too.
Only the bundles whose name carries the family prefix are removed, and the
directory itself stays.

---

## 5. Code signing

**The alpha ships unsigned.** Signing is a release-manager task and it has an
order, because patching a binary invalidates its signature.

The order that works:

1. **Build** the plug-in template and the core C ABI library.
2. **Stamp** the identity into each plug-in bundle
   (`plugins/vst3/build_identity.py stamp-all`). Stamping is a byte level
   patch of the linked file, so anything signed before this point is broken.
3. **Sign** the plug-in bundles and the core C ABI library — *after* stamping.
   On Windows that is `signtool sign /fd SHA256 /tr <tsa> /td SHA256
   /f <cert.pfx> <file>`; on macOS the codesign identity is the
   `AURALIS_MACOS_CODESIGN_IDENTITY` option of the plug-in build, which
   re-signs after stamping for exactly this reason.
4. **Build the installer** from the signed tree.
5. **Sign the installer** last, and publish the signature next to it.

Two consequences worth writing down:

* A plug-in bundle whose signature does not match its bytes will not load in
  a host that verifies signatures. Verify with `signtool verify /pa <file>`
  (Windows) or `codesign --verify --strict <file>` (macOS) after step 3.
* The installer copies the bundles as they are. It does not re-sign anything,
  so the signature in the repository is the signature in the installation.

Ship the certificate and the timestamping service with the release notes, and
record the SHA-256 of the installer next to the download. The release manifest
already carries the SHA-256 of every file in the package; the manifest of the
installer itself is the file to publish.

---

## 6. What a release contains

```
dist/
  full/                          the assembled tree
    manifest.json                SHA-256 and size of every file
    SHA256SUMS                   the checksums of the tree
    aurcore/ aurui/ aurserver/ assets/ docs/ var/ bin/ lib/
    plugins/vst3/build/stamped/
      <name>.vst3/               the 48 plug-in bundles, one file each
      Auralis Runtime/           the shared runtime: the core, the interpreter
    scripts/                     the builder that produced the tree
  slim/                          the same, without the plug-in layer
    manifest.json
    SHA256SUMS
  auralis-<version>-full.zip
  auralis-<version>-slim.zip
  installer/
    Auralis-<version>-setup.exe  (when the compiler is available)
```

Publish the zip, the installer and the manifest. The manifest is the thing to
check the download against: one SHA-256 per file, and a total size that the
budget report already showed.

The manifest, the checksum list and the build script are installed with the
application, so that the installation can be checked on the machine it was
made on:

    python scripts\build_release.py --verify .

They are published with the package as well.

---

## 7. Verifying the packaging without Inno Setup

```bash
python -m pytest tests/test_packaging.py tests/test_installer.py -q
```

The tests need neither Inno Setup nor the network. They check that

* the installer script parses — balanced sections, every source path present
  in the repository (or a known build artefact), and an uninstall that removes
  every registry key the install creates;
* the release builder produces a manifest whose hashes match the files, and
  notices a file that changed after the manifest was written;
* the size budget is reported against the targets;
* the installer script agrees with the tree the release builder assembles:
  every path it names is in that tree, no path points outside it, the
  plug-in bundles are installed as the directories they are, the registry
  key is the `Software\Auralis\PluginPaths` one the project already uses,
  the bundles go into the directory a host scans, and the uninstall removes
  what the install adds.

The test that compiles the installer with `iscc` skips itself when the
compiler is not installed, so the suite is green everywhere.

---

## 8. Known limitations

These are the things the alpha does **not** do yet. They are listed here so
that nobody discovers them at a customer's desk.

* **No bundled Python runtime.** The packages carry the application, not an
  interpreter. The launcher uses the Python 3.11+ that is on `PATH`
  (`py -3`, then `python`). Bundling an embeddable interpreter is the
  remaining work for the platform packages milestone.
* **The document hand-off is one sided.** The `.aurdoc` association passes
  the document to the launcher, which exports it as `AURALIS_OPEN`. The
  server side of that hand-off — reading the variable and opening the
  session — is not implemented yet, so today the association opens the
  workbench with the path in the environment rather than with the document
  loaded.
* **No custom icon.** The repository carries `assets/logo.svg`, and the
  installer cannot use an SVG as a program or shortcut icon. The build ships
  with the default icon until an `.ico` is added.
* **The shared plug-in directory is not removed.** The uninstall deletes the
  bundles of this family from it and leaves the directory in place: it
  belongs to every product that installs plug-ins.
* **The installer has not been compiled on the build machine.** The compiler
  is not installed there, so the script is verified by reading it and by
  checking it against the assembled tree
  (`python -m pytest tests/test_installer.py -q`). The one test that
  compiles it skips itself when the compiler is missing, so the first
  machine that has one is the first machine that sees the executable.
* **The host registration is ours.** The bundle directories are registered
  under `Software\Auralis`, which is where Auralis looks. Registering the
  bundles with a host's own discovery mechanism is a separate piece of work
  per host.
