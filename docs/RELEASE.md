# Auralis - the release

This document is what a release is, how it is built, what is in it, how a
user checks that it is intact and what has to happen for the plug-ins to
work.

The release builder is `scripts/build_release.py`.  It is the only thing
that decides what a package contains.

---

## 1. The two packages

| package | what it is | budget |
|---|---|---|
| `full` | the application, the preset library, the documentation, the core C ABI library and the whole plug-in family (48 bundles and the runtime they share) | 700 MB |
| `slim` | the application, the preset library and the documentation - no plug-in layer, no core library | 145 MB |

The plug-in family is the bulk of the full package: 48 bundles and the
shared runtime, 17 047 342 bytes (16.26 MiB) together.  Carried inside
every bundle the runtime would be 48 copies of the same 7.8 MB file, and
the family would be 394 332 672 bytes (376.06 MiB).

The slim package is the application on its own.  It runs the workbench, it
serves the interface and it holds the preset library, but it installs no
plug-in.

---

## 2. Building a release

    python scripts\build_release.py

That builds both packages into `dist/`, writes the trees, writes the
manifest and the checksum list of every tree, writes the two zip files and
prints the size report.  It ends with `RESULT: PASS` or `RESULT: FAIL`.

The build runs four gates before it assembles anything.  A gate that fails
stops the build; nothing is written.

| gate | what it proves |
|---|---|
| `schema` | the canonical parameter table and the generated artefacts agree |
| `css-coverage` | every style the interface uses is covered by a rule |
| `plugin-abi` | the core C ABI behaves as the contract says |
| `plug-in-family` | every module of the registry has exactly one bundle, and every bundle carries a stamped identity |

The last gate is the one that keeps a broken package out of the world: a
module without a bundle, a bundle without a stamped identity and a bundle
that cannot load (no core library, no interpreter) all stop the build.
The build says what is wrong:

    the plug-in for De-Hum (AUR_DEHUM) is missing
    the plug-in for De-Hum (AUR_DEHUM) is not stamped: <the bundle>
    the plug-in for De-Hum (AUR_DEHUM) is incomplete: it carries no core library: <the bundle>

The bundle that ships for a module is the one that carries the identity of
that module.  When a build tree holds more than one bundle for a module,
the one that was built last ships - the family is rebuilt module by
module, and the last build is the current one - and the build says how
many the others were.  A bundle that cannot be loaded comes last whatever
its age: a bundle that is not stamped may not be shipped.

Useful options:

| option | what it does |
|---|---|
| `--profile full` / `--profile slim` | build one package instead of both |
| `--out DIR` | write the packages somewhere else (default `dist`) |
| `--no-zip` | do not write the zip files |
| `--no-installer` | do not look for the installer compiler |
| `--per-machine` | compile the machine-wide installer instead of the per-user one |
| `--plugins-dir DIR` | take the plug-in bundles from `DIR` instead of the build trees |
| `--skip-checks` | do not run the gate - the build is then not a release |
| `--verify TREE` | check a package against its own manifest and stop |

The installer is compiled only when the Inno Setup compiler (`iscc`) is on
the machine.  Without it the build still produces the packages and says
that the installer was not compiled.

---

## 3. What is in a package

The build prints this table for every package it assembles; the manifest
carries the same numbers.

    CONTENTS (full)
    --------------------------------------------------------------------
      category              files          size    share
      application             109       2.4 MiB    12.1%
      assets                    1       1.5 KiB     0.0%
      documentation            13     691.9 KiB     3.4%
      presets                  10       11.0 KiB     0.1%
      launchers                 3       1.8 KiB     0.0%
      runtime                   3       7.7 MiB    39.1%
      plug-ins                 48       8.6 MiB    43.9%
      core library              1     180.0 KiB     0.9%
      tools                     1     103.3 KiB     0.5%
      total                   189      19.6 MiB  100.0%
    --------------------------------------------------------------------

The full package is 20 543 213 bytes (19.6 MiB) of a budget of 734 003 200
bytes (700 MiB), so 713 459 987 bytes (680.4 MiB) of headroom; the slim
package is 3 311 551 bytes (3.2 MiB) of a budget of 152 043 520 bytes
(145 MiB), so 148 731 969 bytes (141.8 MiB) of headroom.  The zip of the
full package is 7.6 MiB, the zip of the slim one 832.9 KiB.

| category | what it holds |
|---|---|
| `application` | `aurui`, `aurcore` and `aurserver` |
| `assets` | the artwork |
| `documentation` | `docs`, this file among them |
| `presets` | the shipped preset library (`var/presets/builtin`) |
| `launchers` | `bin/auralis.cmd`, `bin/auralis-server.cmd`, `bin/auralis.sh` |
| `runtime` | the shared runtime of the plug-in family (the core library, the interpreter and the manifest of the runtime) |
| `plug-ins` | the 48 plug-in bundles, one file each |
| `core library` | `lib/aurcore_abi.dll` |
| `tools` | `scripts/build_release.py`, the verifier the user runs |

The runtime is shared by the whole family: every bundle carries the
plug-in alone, and the core library and the interpreter live once, in the
directory `Auralis Runtime` next to the bundles.  The plug-in looks for it
there.  Carried inside every bundle it would be 48 copies of the same
7.8 MB file.

The preset *index* is not shipped.  It is written by the preset manager
from the files it finds on disk, it names the presets of the machine the
build ran on and it carries the moment it was written; the application
writes a fresh one on the first start.

### The plug-in family

The full package carries one bundle per module of the canonical registry -
48 modules, 253 parameters.  The manifest lists every one of them with the
identifiers the host sees:

    "module_code": "AUR_DECLICK",
    "display_name": "De-Click",
    "product_name": "Auralis De-Click",
    "category": "Repair",
    "internal_name": "Auralis_De_Click",
    "vst3_fuid": "0101abab-abcd-ef01-4155-52414445434c",
    "original_filename": "Auralis De-Click.vst3",
    "param_count": 5,
    "bundle": "Auralis De-Click.vst3",
    "path": "plugins/vst3/build/stamped/Auralis De-Click.vst3",
    "binary": "plugins/vst3/build/stamped/Auralis De-Click.vst3/Auralis De-Click.vst3.dll",
    "sha256": "181708fc78e72fc2c5996e0c482ecd42f334c3cb2220dc8f114e4362ab0e4ceb",
    "size": 187904,
    "stamped": true

The class id is what a host stores in a session: two bundles with the same
class id are the same plug-in to the host.

The build prints the whole family as a table and lists the bundles it
found but did not ship - a build tree that still holds an older build of
the family is normal, and the report says how many there were.

The build also says when the bundles do not agree with each other: a
bundle is linked against the core library of the moment it was built, so
a family that was not rebuilt in one go carries more than one version of
it.  The manifest carries that warning as well, under `contents.warnings`.

---

## 4. Checking a package

Every package carries two files that describe it:

| file | what it is |
|---|---|
| `manifest.json` | every file of the package with its size and its SHA-256, the version, the build, the modules, the plug-in family, the size breakdown and the command that checks the package |
| `SHA256SUMS` | the same hashes in the form the standard checksum tool reads |

The manifest does not list itself and it does not list the checksum list;
the checksum list covers both, so the chain is complete.

### With the tool the user has

On a machine with the usual checksum tools, in the directory of the
package:

    sha256sum -c SHA256SUMS

On Windows:

    certutil -hashfile <file> SHA256

### With the verifier that ships with the package

The package carries the verifier (`scripts/build_release.py`), so a user can
check the whole package - every file, the checksum list and the build id -
with one command:

    python scripts\build_release.py --verify .

The command is written into the manifest itself, under `verify`:

    "verify": {
      "manifest": "manifest.json",
      "sha256sums": "SHA256SUMS",
      "command": "python scripts/build_release.py --verify .",
      "sha256sums_command": "sha256sum -c SHA256SUMS"
    }

The verifier prints `RESULT: PASS` when the package is intact and lists
every file that is missing, that changed or that is not in the manifest
when it is not.

### The zip

The build writes the digest of the zip next to it
(`auralis-<version>-<profile>.zip.sha256`), so the archive can be checked
before it is unpacked.

---

## 5. Installing

### With the installer

Run `Auralis-<version>-setup.exe`.  The installer is compiled by the
release build when the Inno Setup compiler is on the machine; it is
written to `dist/installer/`.

The per-user installer (the default) installs

* the application into the per-user programs directory,
* the plug-in bundles into the per-user VST3 directory,
* the shared runtime into the same VST3 directory, next to the bundles,
* the paths of the plug-in directories into the registry under
  `Software\Auralis\PluginPaths`,
* the start menu entries and the association for the session document.

The uninstall removes the bundles of this family and the runtime from the
VST3 directory, and leaves the directory itself: it belongs to every
product that installs plug-ins.

The machine-wide variant (`--per-machine`) does the same in the machine
programs directory and in the machine-wide VST3 directory, and it writes
the same keys into the machine hive.  The scope is fixed when the installer
is compiled, so the two can never disagree.

### Without the installer

1. Unpack the full package.
2. Copy `plugins/vst3/build/stamped/*.vst3` into the directory the host
   scans for plug-ins.  The bundle is a folder and it has to be copied as
   one, with its layout.
3. Copy `plugins/vst3/build/stamped/Auralis Runtime` into the same
   directory, next to the bundles: that is where the plug-in looks for the
   core and the interpreter it needs.
4. Copy `aurui`, `aurcore`, `aurserver`, `assets` and `var/presets` next
   to each other, and start the workbench with `bin/auralis.cmd` (or
   `bin/auralis.sh`).

### What the host needs

A plug-in bundle carries the plug-in.  The core library and the
interpreter it needs travel with the family: they are in the directory
`Auralis Runtime` next to the bundles, and the plug-in looks for it there.
The installer puts that directory next to the bundles in the plug-in
directory of the host, so the two are always found together.

The core *package* is not in the bundle either: the bundle finds it
through the `AURALIS_ROOT` environment variable, and falls back to the
directory it was loaded from.

So the host has to be started with `AURALIS_ROOT` pointing at the
installation directory - the directory that holds `aurcore`:

    set AURALIS_ROOT=<the directory the application was installed in>

The launcher of the application sets that variable for the workbench; a
host that is started on its own needs it in the environment.

---

## 6. Reproducibility

The same tree gives the same package.  The build sorts every list, gives
every file of the tree the same timestamp and writes the zip with a fixed
timestamp for every entry.

Measured on one tree, packaged three times:

| package | bytes that differ | entries that differ |
|---|---|---|
| the moment pinned (`SOURCE_DATE_EPOCH`) | 0 | 0 - the two archives have the same digest |
| the moment free | 68,898 | 2: the manifest and the checksum list |

Everything that moves is the moment the build was made: with the moment
free the manifest differs in three lines (`created_utc`, `timestamp_utc`
and `source_date_epoch`) and the checksum list differs because it holds
the hash of the manifest.

The moment can be pinned:

    set SOURCE_DATE_EPOCH=1700000000
    python scripts\build_release.py

With the moment pinned, two builds of the same tree are byte for byte
identical, the manifest included.  The build id in the manifest is the
fingerprint of the content of the package: two packages with the same build
id carry the same files.
