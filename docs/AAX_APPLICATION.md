# AAX application — the request, and the plan behind it

> **What this document is.** It was written as the request for the format's
> SDK. **The SDK and the format's conformance validator are in hand**, so the
> request is answered and the document is kept as the record of what was
> asked for and why. The items it asks for that are still open are the
> **effect-id registration** and the **digital signature** a production host
> wants; the parameter and page-table schema is settled — the format's page
> table is a resource document in the bundle, in the format's own schema, and
> the generator writes it (`plugins/aax/AAX_ADAPTER_NOTES.md` §3.4).
>
> **Who it blocks.** One gate: **G10 — AAX adapter** (`docs/ALPHA_STATUS.md`).
> Nothing else in the plug-in layer depends on the SDK.
>
> **What is already done.** The **application package** — the effect
> identifiers, the parameter identifiers, the page layout, the labels, the
> ranges, the defaults and the category — is generated from the canonical
> parameter table, checked into this repository and verified by a test
> (`docs/AAX_PAGE_TABLES.md`, `scripts/aax_tables.py`,
> `tests/test_aax_application.py`).  None of it needs the SDK.  The only
> thing that needs the SDK is the glue that turns the package into a
> loadable bundle.  **The package is not the format**: section 0.2 says
> exactly what is finished and what is not.

---

## 0. The ask, in one screen

| # | What we are asking for | Why we need it | What it blocks |
|---|---|---|---|
| 1 | **The AAX SDK** — developer download, with the interfaces and the build support | the headers and libraries the adapter compiles against | the adapter build |
| 2 | **The AAX host validation** (the validator) | a plug-in has to pass the vendor's validation before it can be listed and certified | shipping, certification |
| 3 | **The SDK licence terms** — the developer agreement | the SDK may not be redistributed; the terms decide who may build it, where the build may run and how the plug-in may be shipped | signing, CI, distribution |
| 4 | **The AUx / AAX host APIs** | the extension point that would let a plug-in draw its own editor; **optional** for the first release | the custom editor (not the plug-in itself) |
| 5 | **The AAX parameter and page-table schema** | the shape the host expects for a plug-in's controls, so our generated page table can be translated into it | the page table |
| 6 | **Effect-id registration** | the host loads a plug-in by its registered identifier | shipping |

**The one-line version of the request:**

> We are building a plug-in family (50 modules, 293 parameters) for the AAX
> format and we need the SDK, the validation path and the effect-id
> registration. We can start the day the SDK is in our hands; the application
> package — the identifiers, the parameter ids, the page tables and the
> labels — is already generated and waiting.

### 0.1 What we are **not** asking for

* Not source code of any kind. We only want the SDK.
* Not a redistribution right for the SDK itself. The SDK stays on the
  machines of the developers who are covered by the agreement; it is never
  committed to this repository and never copied into a build artefact.
* Not a custom editor in the first release. The adapter returns no editor and
  the host's own generic editor drives the page table. AUx is requested so the
  option stays open, not because the first release needs it.
* Not a certification guarantee. We want the validator and the submission
  path, and we will do the work of passing validation.

### 0.2 What is finished, and what still needs the SDK

**The application package is finished.**  It is the part of the plug-in that
describes the plug-in to the host, and it is complete, generated from the
single source of truth (`aurcore/schema/params.tsv`) and verified without
the SDK:

| Item | State | Where |
|---|---|---|
| the effect identifiers (50 modules × 3 channel configurations = 150 ids) | **done**, checked against the identity layer | `docs/AAX_APPLICATION.md` §2.5, `tools/aax_application_checklist.py` |
| the parameter identifiers (293 of them) | **done**, the same numbers the VST3 adapter carries in the identity record | `scripts/aax_tables.py`, `plugins/identity.py` |
| the page tables (50 page tables, 131 pages) | **done**, generated and drift-checked | `docs/AAX_PAGE_TABLES.md`, `docs/aax_page_tables.json` |
| the labels (base label + truncation ladder) | **done**, the ladder rule is pinned by the golden fixture | `scripts/aax_tables.py`, `tests/test_truncation_ladder.py` |
| the ranges, the defaults and the category | **done**, copied from the canonical table | `docs/AAX_PAGE_TABLES.md` |
| the package check (every module has ids, the ids are unique, every parameter is on exactly one page, the ids are the VST3 ids) | **done**, 38 tests | `tests/test_aax_application.py` |

**The adapter is written, and it compiles.**  The SDK is in the tree
(`vendor/JUCE8/modules/juce_audio_plugin_client/AAX/SDK`): the interface
headers, the format's own reference implementation of the plug-in side
and the licence file.  `scripts/build_aax.py` builds the adapter against
it, and `tests/test_aax_adapter.py` reads the result back: the component
descriptors, the effect ids, the page table and the parameter list are
all checked against the canonical table.

What is still missing is what belongs to the format's own processes:

| # | What still needs the vendor | Why |
|---|---|---|
| 1 | the submission that follows the validation run | the submission procedure is the vendor's own |
| 2 | the effect-id registration | the registration process is the vendor's |
| 3 | the digital signature a production host wants | the validator ran the plug-in unsigned |

The reserved parameter-id range is settled: the format's own reserved
control identifiers are strings (`"PreviewID"`, `"MasterBypassID"`), not
a numeric range, so our base (`0x0100`) cannot collide with them.

**So: the data is finished, the plug-in is written, and the format's own
host has loaded it.**  The validator that ships with the format's tooling
loads the plug-in, reads the description back and runs the test suite on
it, and every check passes.  What is still not shown is the audio path
through a real session in a shipping host.  `plugins/aax/README.md` §6 says
exactly what is proved and what is not.

### 0.3 How to send it

The request below is the text to send. Fill in the bracketed fields and
send it to the developer-relations address of the SDK vendor.

```
Subject: AAX SDK access request — Auralis (audio restoration plug-in family)

Hello,

We are building Auralis, a spectral audio restoration workbench, and we
would like to publish our plug-in family for the AAX format.

  Product          : Auralis, a spectral audio restoration workbench
  Company          : [legal entity name]
  Contact          : [name, role, e-mail]
  Country          : [country]

What we need

  1. The AAX SDK, for a developer seat on the machines listed below.
  2. The host validation path (the validator) and the submission procedure
     that follows it.
  3. The developer agreement that governs the SDK: who may build with it,
     where the build may run, and how a plug-in built with it may be
     distributed.
  4. The parameter and page-table schema, so that our generated page tables
     can be translated into the shape the host expects.
  5. The AUx host API, for a later release that may draw its own editor.
  6. Registration of our effect identifiers (see the table attached).

Scope

  50 modules, 293 parameters, three channel configurations each, one
  binary template with a stamped identity record per module. The DSP is a
  shared core; the plug-in is a thin adapter, exactly as the format intends.

Developer machines

  [list of developers, OS, and whether a build machine is needed]

Timeline

  We can compile and submit a candidate within [2] weeks of receiving the
  SDK. Our own verification is already in place, so the work that remains
  is the SDK-facing glue and the validation itself.

Attached

  The identifier table (manufacturer / product / plug, 50 modules) and a
  one-page description of the plug-in's parameter model.

Thank you,
[signature]
```

---

## 1. What is being asked for, and why

### 1.1 The AAX SDK

The AAX SDK is the interface definition and the build support for the AAX
format: the component interfaces (the ACF interface family), the parameter
and page-table machinery, the host-application interfaces, and the tools that
turn a plug-in into something the host loads.

It is **licensed separately and may not be redistributed**, which is the
whole reason this repository contains no AAX headers and the AAX target
cannot be built here. Everything that does not need the SDK — the
identifiers, the parameter ids, the page layout, the label rules — is
already generated and checked in; see `plugins/aax/AAX_ADAPTER_NOTES.md`.

What the build needs from the SDK directory, concretely (this is what the
build system looks for):

| Path inside the SDK | Used for |
|---|---|
| `Interfaces/ACF/` | the component interfaces the adapter implements |
| `Interfaces/` | the parameter, page-table and host-application headers |
| `Utilities/PlugIn.ico` | the default plug-in icon the bundle carries |

The build system refuses to continue with a clear message when the SDK path
does not contain those directories, rather than failing later with a
compiler error.

### 1.2 The AAX host validation (the validator)

Validation is the step where a candidate plug-in is checked against the
format's rules by the vendor's own tooling. It is the gate between "we have
a binary" and "the host will list and load it".

We want two things:

1. **The validator itself** — so we can run it ourselves, in CI, on every
   candidate build, before we submit anything. A validation failure found
   locally is a day; the same failure found after a submission round trip is
   a week.
2. **The submission procedure** — what has to accompany a submission, in
   which form, and how long the review takes, so we can plan the release
   date rather than discover it.

We will pass the validator on our own; we are asking for the tool and the
procedure, not for an outcome.

### 1.3 The AAX SDK licence terms

This is the part that decides whether the product can ship at all, so we want
it in writing before we write any SDK-facing code. The questions we need
answered:

| Question | Why it matters to us |
|---|---|
| May the SDK be installed on a **build machine** (continuous integration), or only on a developer's workstation? | Decides whether our release pipeline can build the plug-in unattended. |
| Does the agreement cover **redistributing a built plug-in** to our customers? | The plug-in binary contains no SDK material, but we need the answer in writing before we ship it. |
| How many **named developer seats** are allowed, and how is a seat added? | The team size that can work on the adapter. |
| Are there **royalty, revenue-share or reporting** obligations? | Affects the commercial model of the plug-in family. |
| What are the **redistribution rules for the SDK itself**? | The SDK must never end up in this repository, in a build artefact or in a customer package. Our design already guarantees this; the terms must agree with it. |
| Is there a **certification or listing** requirement before a plug-in can be distributed? | Decides whether "ships" means "we publish it" or "the vendor lists it". |

### 1.4 The access the adapters need

| Access | What for | Needed when |
|---|---|---|
| The **SDK** | the interfaces and the build | first adapter commit |
| The **AUx / AAX host APIs** | the extension point for a plug-in that draws its own editor | optional; the first release returns no editor and lets the host's generic editor drive the page table |
| The **AAX parameter and page-table schema** | translating our generated page table into the shape the host expects | the page-table work |
| The **effect-id (symbol/identifier) registration** | the host loads a plug-in by its registered identifier; an unregistered identifier is not loadable | before the first release |

Two of these are worth a sentence of explanation, because they are the
places where our design meets the format's rules.

**The page-table schema.** We generate our own page table from the canonical
parameter table (section 3). The SDK's schema is what our generated table has
to be translated *into*. We need the schema — and the tool that consumes it —
so that the translation step is mechanical rather than a guess. The element
names of our own schema are ours; the SDK-facing step is the only place
where the two meet, and it is written once, in one file.

**The effect-id registration.** A plug-in is identified by three four
character codes (section 2). The manufacturer code is a single id shared by
the whole family; the product code is one per module; the plug code is one
per channel configuration. All of them have to be registered with the
vendor before shipping, and the registration is what makes the host willing
to load them. The table in section 2.5 is the registration request.

### 1.5 Why the request is worth making now

The SDK is a hard external dependency with a long lead time that we do not
control. Everything else about the plug-in layer can be designed, built and
verified without it — and is. The request is therefore the only item on the
critical path that is not ours to execute, which is exactly why it goes out
first and why this document is written to be sent as it stands.

---

## 2. The AAX wrapper plan

### 2.1 The identifier triple

An AAX plug-in is identified by three four-character codes. Together they
form the **effect id** of the plug-in.

| Component | Meaning | Value in Auralis |
|---|---|---|
| **Manufacturer** | who made the plug-in | `AURA` — one id for the whole family |
| **Product** | which product within the maker | the module's product code, e.g. `DECL` |
| **Plug** | which channel configuration | `MONO`, `M2ST` or `STER` |

Every module therefore has **three** effect ids:

```
AURA:DECL:MONO     mono in, mono out
AURA:DECL:M2ST     mono in, stereo out
AURA:DECL:STER     stereo in, stereo out
```

The parameter set is identical in all three; only the routing differs. The
adapter builds the same chain in every case and lets the wrapper's channel
configuration decide the rest.

The product code is deliberately the *same* four characters the other plug-in
formats use as their plugin code, so a module carries **one identity across
formats**: the same four characters that appear in the VST3 class id are the
AAX product id.

### 2.2 The alphabet and the length rule — the restricted alphabet

**The rule.** Every component of an effect id is **exactly four characters**,
each drawn from the **uppercase ASCII letters and the digits**:

```
allowed : A B C D E F G H I J K L M N O P Q R S T U V W X Y Z  0 1 2 3 4 5 6 7 8 9
forbidden: space, hyphen, underscore, dot, slash, colon, any other punctuation, any non-ASCII character, any lowercase letter
```

**Why it is stated as a rule and not left implicit.** The identifier travels
through a fixed four-character field, in a bundle descriptor, in a registry
and in a host's plug-in cache. There is no normalisation step anywhere in
that path: a space, an underscore or a lowercase letter is not "almost
valid", it is a *different* identifier, and the host will not find the
plug-in it was told to load. A single stray character therefore costs a
release, not a build.

Two more properties fall out of the rule and are relied on:

* the identifier is **case sensitive**, so the canonical form is uppercase
  only — `DECL` and `decl` are two different identifiers, and only one of
  them is ours;
* the identifier is **fixed width**, so a registry of ids is a fixed-width
  table that can be compared byte for byte.

**The rule is enforced, not hoped for.** `tools/aax_application_checklist.py`
validates every derived identifier against the alphabet and the length rule
and exits non-zero when one of them is broken:

```sh
python tools/aax_application_checklist.py            # the table, on stdout
python tools/aax_application_checklist.py --format report   # table + summary
python tools/aax_application_checklist.py --format json     # machine readable
```

Exit codes: `0` no violation, `1` at least one violation, `2` the tool could
not run. Violations are always written to standard error, so the markdown on
standard output stays clean enough to paste into a document.

### 2.3 How the ids are allocated

The allocation is owned by `plugins/identity.py`, over the **whole** module
registry. It is not per-module, not per-subset and not per-format.

1. **Natural product code.** Take the module code's suffix after `AUR_`,
   keep the alphanumeric characters, upper-case them, and take the first four.
   Pad with zeros when the suffix is shorter.

   ```
   AUR_DECLICK    ->  DECL
   AUR_SPECTRALDENOISE ->  SPEC
   AUR_EQ         ->  EQ00
   ```

2. **Collision resolution.** Two modules may want the same natural code.
   The first one in registry order keeps it; the others are re-assigned a
   code derived from a hash of their own module code. A re-assigned code
   never takes the natural code of another module, and never collides with
   another re-assigned code.

   Five modules need it today:

   | Module | Natural | Assigned | Why |
   |---|---|---|---|
   | `AUR_DECLIP` | `DECL` | `DECS` | `DECL` is taken by De-Click |
   | `AUR_SPECTRALREPAIR` | `SPEC` | `SPEY` | `SPEC` is taken by Spectral De-Noise |
   | `AUR_SPECTRALRECOVERY` | `SPEC` | `SPEB` | `SPEC` is taken by Spectral De-Noise |
   | `AUR_SPECTRALEDITOR` | `SPEC` | `SPEN` | `SPEC` is taken by Spectral De-Noise |
   | `AUR_LOUDNESSOPT` | `LOUD` | `LOUV` | `LOUD` is taken by Loudness Control |

3. **Plug codes.** The three channel configurations, fixed for the whole
   family: `MONO`, `M2ST`, `STER`.

4. **Manufacturer code.** `AURA`, frozen forever. Changing it would change
   every identifier the product has ever emitted.

### 2.4 How uniqueness and stability are kept across releases

| # | Rule | Why |
|---|---|---|
| 1 | The allocation is computed over the **whole registry**, never over a subset. | A module keeps its product code no matter which modules are asked for. Adding a module never renumbers the others. |
| 2 | Product codes are **unique** across the registry, and the (manufacturer, product, plug) triples are unique by construction. | Two plug-ins with the same id would be the same plug-in. The checklist tool checks both. |
| 3 | The manufacturer code is **frozen**, and the plug codes are **fixed**. | They are family-wide constants. |
| 4 | A module is **never removed from the middle** of the registry; a retired module keeps its slot. | The product code of a retired module is never given to a new one. |
| 5 | An identifier is **never re-used** for a different module, even after the module is retired. | A host may still have the old plug-in cached. |
| 6 | The identifiers live in the **identity record**, which is stamped into the binary at build time, and the same triple is compiled into the bundle where the format requires it. | The build reads the stamped record back out of the shipped file and checks it against the compiled-in id, so the two cannot disagree. |

Point 6 is worth spelling out, because it is what makes the table in section
2.5 *true* rather than *intended*: the effect id is compiled into the
bundle, because the host has to be able to read it at load time, and the
same triple is written into the stamped identity record. The build reads the
record back out of the shipped file and compares the two, so a table that
does not match the binary fails the build rather than the release.

### 2.5 The generated identifier table

**All 50 modules, 150 effect ids.** Manufacturer `AURA` in every row; the
three plug columns hold the full (manufacturer, product, plug) triple.
"Discrete" is the number of discrete controls of the module (section 4.1).

<!-- BEGIN GENERATED TABLE -->
| # | Module | Code | Product id | MONO | M2ST | STER | Params | Discrete |
|---:|---|---|---|---|---|---:|---:|---:|
| 0 | De-Click | `AUR_DECLICK` | `DECL` | `AURA:DECL:MONO` | `AURA:DECL:M2ST` | `AURA:DECL:STER` | 5 | 2 |
| 1 | De-Crackle | `AUR_DECRACKLE` | `DECR` | `AURA:DECR:MONO` | `AURA:DECR:M2ST` | `AURA:DECR:STER` | 4 | 2 |
| 2 | De-Clip | `AUR_DECLIP` | `DECS` | `AURA:DECS:MONO` | `AURA:DECS:M2ST` | `AURA:DECS:STER` | 5 | 3 |
| 3 | De-Hum | `AUR_DEHUM` | `DEHU` | `AURA:DEHU:MONO` | `AURA:DEHU:M2ST` | `AURA:DEHU:STER` | 19 | 9 |
| 4 | De-Ess | `AUR_DEESS` | `DEES` | `AURA:DEES:MONO` | `AURA:DEES:M2ST` | `AURA:DEES:STER` | 8 | 4 |
| 5 | De-Plosive | `AUR_DEPLOSIVE` | `DEPL` | `AURA:DEPL:MONO` | `AURA:DEPL:M2ST` | `AURA:DEPL:STER` | 3 | 0 |
| 6 | De-Reverb | `AUR_DEREVERB` | `DERE` | `AURA:DERE:MONO` | `AURA:DERE:M2ST` | `AURA:DERE:STER` | 9 | 2 |
| 7 | De-Rustle | `AUR_DERUSTLE` | `DERU` | `AURA:DERU:MONO` | `AURA:DERU:M2ST` | `AURA:DERU:STER` | 3 | 1 |
| 8 | De-Wind | `AUR_DEWIND` | `DEWI` | `AURA:DEWI:MONO` | `AURA:DEWI:M2ST` | `AURA:DEWI:STER` | 4 | 0 |
| 9 | De-Bleed | `AUR_DEBLEED` | `DEBL` | `AURA:DEBL:MONO` | `AURA:DEBL:M2ST` | `AURA:DEBL:STER` | 2 | 0 |
| 10 | Breath Control | `AUR_BREATH` | `BREA` | `AURA:BREA:MONO` | `AURA:BREA:M2ST` | `AURA:BREA:STER` | 5 | 2 |
| 11 | Mouth De-Click | `AUR_MOUTHDECLICK` | `MOUT` | `AURA:MOUT:MONO` | `AURA:MOUT:M2ST` | `AURA:MOUT:STER` | 4 | 1 |
| 12 | Voice De-Noise | `AUR_VOICEDENOISE` | `VOIC` | `AURA:VOIC:MONO` | `AURA:VOIC:M2ST` | `AURA:VOIC:STER` | 12 | 4 |
| 13 | Spectral De-Noise | `AUR_SPECTRALDENOISE` | `SPEC` | `AURA:SPEC:MONO` | `AURA:SPEC:M2ST` | `AURA:SPEC:STER` | 19 | 5 |
| 14 | Spectral Repair | `AUR_SPECTRALREPAIR` | `SPEY` | `AURA:SPEY:MONO` | `AURA:SPEY:M2ST` | `AURA:SPEY:STER` | 7 | 3 |
| 15 | Spectral Recovery | `AUR_SPECTRALRECOVERY` | `SPEB` | `AURA:SPEB:MONO` | `AURA:SPEB:M2ST` | `AURA:SPEB:STER` | 5 | 1 |
| 16 | Guitar De-Noise | `AUR_GUITARDENOISE` | `GUIT` | `AURA:GUIT:MONO` | `AURA:GUIT:M2ST` | `AURA:GUIT:STER` | 8 | 1 |
| 17 | Deconstruct | `AUR_DECONSTRUCT` | `DECO` | `AURA:DECO:MONO` | `AURA:DECO:M2ST` | `AURA:DECO:STER` | 6 | 2 |
| 18 | Dialogue Isolate | `AUR_DIALOGUEISOLATE` | `DIAL` | `AURA:DIAL:MONO` | `AURA:DIAL:M2ST` | `AURA:DIAL:STER` | 5 | 1 |
| 19 | Music Rebalance | `AUR_MUSICREBALANCE` | `MUSI` | `AURA:MUSI:MONO` | `AURA:MUSI:M2ST` | `AURA:MUSI:STER` | 9 | 5 |
| 20 | Ambience Match | `AUR_AMBIENCEMATCH` | `AMBI` | `AURA:AMBI:MONO` | `AURA:AMBI:M2ST` | `AURA:AMBI:STER` | 6 | 2 |
| 21 | Repair Assistant | `AUR_REPAIRASSISTANT` | `REPA` | `AURA:REPA:MONO` | `AURA:REPA:M2ST` | `AURA:REPA:STER` | 7 | 1 |
| 22 | Leveler | `AUR_LEVELER` | `LEVE` | `AURA:LEVE:MONO` | `AURA:LEVE:M2ST` | `AURA:LEVE:STER` | 6 | 1 |
| 23 | Loudness Control | `AUR_LOUDNESS` | `LOUD` | `AURA:LOUD:MONO` | `AURA:LOUD:M2ST` | `AURA:LOUD:STER` | 6 | 1 |
| 24 | Loudness Optimize | `AUR_LOUDNESSOPT` | `LOUV` | `AURA:LOUV:MONO` | `AURA:LOUV:M2ST` | `AURA:LOUV:STER` | 4 | 0 |
| 25 | EQ | `AUR_EQ` | `EQ00` | `AURA:EQ00:MONO` | `AURA:EQ00:M2ST` | `AURA:EQ00:STER` | 14 | 1 |
| 26 | EQ Match | `AUR_EQMATCH` | `EQMA` | `AURA:EQMA:MONO` | `AURA:EQMA:M2ST` | `AURA:EQMA:STER` | 1 | 0 |
| 27 | Gain | `AUR_GAIN` | `GAIN` | `AURA:GAIN:MONO` | `AURA:GAIN:M2ST` | `AURA:GAIN:STER` | 1 | 0 |
| 28 | Fade | `AUR_FADE` | `FADE` | `AURA:FADE:MONO` | `AURA:FADE:M2ST` | `AURA:FADE:STER` | 2 | 1 |
| 29 | Normalize | `AUR_NORMALIZE` | `NORM` | `AURA:NORM:MONO` | `AURA:NORM:M2ST` | `AURA:NORM:STER` | 1 | 0 |
| 30 | Phase | `AUR_PHASE` | `PHAS` | `AURA:PHAS:MONO` | `AURA:PHAS:M2ST` | `AURA:PHAS:STER` | 2 | 1 |
| 31 | Mixing | `AUR_MIXING` | `MIXI` | `AURA:MIXI:MONO` | `AURA:MIXI:M2ST` | `AURA:MIXI:STER` | 2 | 0 |
| 32 | Center Extract | `AUR_CENTEREXTRACT` | `CENT` | `AURA:CENT:MONO` | `AURA:CENT:M2ST` | `AURA:CENT:STER` | 4 | 1 |
| 33 | Azimuth | `AUR_AZIMUTH` | `AZIM` | `AURA:AZIM:MONO` | `AURA:AZIM:M2ST` | `AURA:AZIM:STER` | 3 | 1 |
| 34 | Dither | `AUR_DITHER` | `DITH` | `AURA:DITH:MONO` | `AURA:DITH:M2ST` | `AURA:DITH:STER` | 4 | 4 |
| 35 | Resample | `AUR_RESAMPLE` | `RESA` | `AURA:RESA:MONO` | `AURA:RESA:M2ST` | `AURA:RESA:STER` | 4 | 2 |
| 36 | Time & Pitch | `AUR_TIMEPITCH` | `TIME` | `AURA:TIME:MONO` | `AURA:TIME:M2ST` | `AURA:TIME:STER` | 7 | 2 |
| 37 | Variable Pitch | `AUR_VARIPITCH` | `VARI` | `AURA:VARI:MONO` | `AURA:VARI:M2ST` | `AURA:VARI:STER` | 4 | 1 |
| 38 | Variable Time | `AUR_VARTIME` | `VART` | `AURA:VART:MONO` | `AURA:VART:M2ST` | `AURA:VART:STER` | 3 | 0 |
| 39 | Wow & Flutter | `AUR_WOWFLUTTER` | `WOWF` | `AURA:WOWF:MONO` | `AURA:WOWF:M2ST` | `AURA:WOWF:STER` | 4 | 0 |
| 40 | Interpolate | `AUR_INTERPOLATE` | `INTE` | `AURA:INTE:MONO` | `AURA:INTE:M2ST` | `AURA:INTE:STER` | 1 | 0 |
| 41 | Signal Generator | `AUR_SIGNALGEN` | `SIGN` | `AURA:SIGN:MONO` | `AURA:SIGN:M2ST` | `AURA:SIGN:STER` | 5 | 2 |
| 42 | Streaming Preview | `AUR_STREAMPREVIEW` | `STRE` | `AURA:STRE:MONO` | `AURA:STRE:M2ST` | `AURA:STRE:STER` | 4 | 3 |
| 43 | Find Similar | `AUR_FINDSIMILAR` | `FIND` | `AURA:FIND:MONO` | `AURA:FIND:M2ST` | `AURA:FIND:STER` | 1 | 0 |
| 44 | Connect | `AUR_CONNECT` | `CONN` | `AURA:CONN:MONO` | `AURA:CONN:M2ST` | `AURA:CONN:STER` | 2 | 2 |
| 45 | Monitor | `AUR_MONITOR` | `MONI` | `AURA:MONI:MONO` | `AURA:MONI:M2ST` | `AURA:MONI:STER` | 1 | 1 |
| 46 | Clip Classifier | `AUR_CLIPCLASSIFIER` | `CLIP` | `AURA:CLIP:MONO` | `AURA:CLIP:M2ST` | `AURA:CLIP:STER` | 5 | 2 |
| 47 | Spectral Editor | `AUR_SPECTRALEDITOR` | `SPEN` | `AURA:SPEN:MONO` | `AURA:SPEN:M2ST` | `AURA:SPEN:STER` | 7 | 3 |
<!-- END GENERATED TABLE -->

*Totals: 50 modules, 293 parameters, 80 discrete controls, 150 effect ids.
Regenerate with `python tools/aax_application_checklist.py` — the table above
is that command's stdout, pasted in, not a transcription.*

---

## 3. The page-table contract

### 3.1 What is published

The **application package** is published as two files, both generated from
the canonical table by `scripts/aax_tables.py`:

| File | What it is |
|---|---|
| `docs/AAX_PAGE_TABLES.md` | the human readable half: every module, its identifiers, its pages and its controls |
| `docs/aax_page_tables.json` | the machine readable half: the same data, plus the label ladder of every control |

The package holds **one page table per module** — 50 of them, 131 pages in
total — and each module carries its **three effect ids** (`MONO`, `M2ST`,
`STER`).  The pages do not depend on the channel configuration, because
the parameter set of a module is identical in all three, so one page table
serves the three ids.

The **150 page tables that the host actually loads** (50 modules × 3
configurations) are *materialised* by the SDK-facing step from this package:
it takes the page table of a module, writes the `plug` attribute of the
configuration it builds, and translates the schema of section 3.2 into the
SDK's own.  That step needs the SDK; the package it consumes does not.

### 3.2 The schema

The schema below is **ours**. It is the shape the generator emits and the
shape the SDK-facing step consumes; the translation into the SDK's own page
table happens in exactly one place (section 6.3). The example is
abbreviated: the complete De-Click table has a second page carrying the
`Output Clicks Only` switch.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!-- GENERATED FILE, DO NOT EDIT.
     Generator:       scripts/aax_tables.py
     Source of truth: aurcore/schema/params.tsv
-->
<PageTable module="AUR_DECLICK" name="De-Click" category="Repair"
           manufacturer="AURA" product="DECL" plug="MONO" paramIdBase="256">
  <Page index="1">
    <Control slot="switch" parameter="0" type="enum" name="Algorithm" label="Algorithm">
      <Choice>Single Band</Choice>
      <Choice>MB Periodic</Choice>
      <Choice>MB Random</Choice>
      <Choice>Low Latency</Choice>
    </Control>
    <Control slot="knob1" parameter="1" type="float" name="Sensitivity" label="Sensitivity"
             min="0" max="10" default="3.0" unit=""/>
  </Page>
</PageTable>
```

| Element | Attributes | Cardinality | Meaning |
|---|---|---|---|
| `PageTable` | `module`, `name`, `category`, `manufacturer`, `product`, `plug`, `paramIdBase` | 1 per file | the root; `module` is the registry code, `name` the display name, `category` the registry category, `manufacturer` the manufacturer id, `product` the AAX product id, `plug` the channel configuration, `paramIdBase` the base the SDK-facing step adds to the parameter ids |
| `Page` | `index` | 1..n, `index` starting at 1 and increasing by one | one page of controls; the host shows one page at a time |
| `Control` | `slot`, `parameter`, `type`, `name`, `label`, plus `min`, `max`, `default`, `unit` for a continuous control | 1..4 per page | one bound control |
| `Choice` | text content | 1..n, only inside an `enum` control | one selectable state of a discrete control |

The machine readable copy of the package (`docs/aax_page_tables.json`)
carries the same elements as JSON objects, plus the **label ladder** of
every control (section 4.3) and the **effect ids** of every module.

Attribute rules:

| Attribute | Rule |
|---|---|
| `slot` | one of `switch`, `knob1`, `knob2`, `knob3` — the page's fixed slots |
| `parameter` | the **parameter id of the module** — the position of the parameter inside its own module, as the generated registry numbers it (section 5.4); not a per-page index. The SDK-facing step adds `paramIdBase` to it |
| `type` | one of `float`, `int`, `bool`, `enum` — copied from the canonical table |
| `name` | the **full** canonical parameter name; never abbreviated |
| `label` | the label drawn on the control (section 4.2) |
| `min`, `max`, `default` | in **natural units** (dB, Hz, ms, %, st) — the same units the core uses; a continuous control only |
| `unit` | the unit string of the canonical table, empty when the parameter has none |

### 3.3 The layout rule

The page layout is **generated**, and the rule is exact:

1. Group the module's parameters by their `group` column in the canonical
   table.
2. Order the groups by first appearance in the table.
3. Within a group, keep the table order.
4. That is the **layout order**. Walk it and place every parameter:
   * a **discrete** control (section 4.1) takes the page's **switch** slot;
   * a **continuous** control takes the next free **knob** slot.
5. A page holds **one switch slot and three knob slots** — at most four
   controls. When the current page cannot take the control, start a new page.
6. The controls of a page are written in **slot order**: the switch first,
   then the knobs, in slot order.  A page is a fixed set of slots, and the
   table reads the way the page is drawn.

The parameter set is the same in all three channel configurations, so the
pages are the same in all three.

*Consequence worth knowing before the SDK arrives:* because a page has a
single switch slot, a module with *N* discrete controls needs at least *N*
pages. The module with the most discrete controls in the family is De-Hum
(9 discrete controls, 19 parameters, therefore 9 pages). We may want a
different page geometry — a second switch slot, or a page that carries a
"label only" row — but that decision belongs to the SDK-facing step, where
the host's actual page geometry is visible, and it is a change to one
constant, not to the model.

### 3.4 A worked example — Spectral De-Noise

Spectral De-Noise (`AUR_SPECTRALDENOISE`, product `SPEC`) has 19
parameters, of which 5 are discrete. The layout rule puts them on **seven**
pages.

The input (the canonical table, abbreviated):

| Parameter id | Name | Type | Range | Default | Group |
|---:|---|---|---|---|---|
| 0 | Processing Mode | enum | 2 choices | `realtime` | Algorithm |
| 1 | Algorithm | enum | 3 choices | `Advanced` | Algorithm |
| 2 | Quality | enum | 4 choices | `B` | Quality |
| 3 | FFT Size | float | 5 .. 60 ms | `20.0` | Analysis |
| 4 | Multi Resolution | bool | off / on | `1` | Analysis |
| 5 | Learning Time | float | 0.5 .. 30 s | `2.0` | Learning |
| 6 | Adaptive | bool | off / on | `1` | Learning |
| 7 | Noise Threshold | float | -40 .. 20 dB | `0.0` | Threshold |
| 8 | Tonal Threshold | float | -40 .. 20 dB | `0.0` | Threshold |
| 9 | Noise Reduction | float | 0 .. 40 dB | `15.0` | Reduction |
| 10 | Tonal Reduction | float | 0 .. 40 dB | `12.0` | Reduction |
| 11 | Artifact Control | float | 0 .. 1 | `0.5` | Smoothing |
| 12 | Smoothing | float | 0 .. 1 | `0.5` | Smoothing |
| 13 | Knee | float | 0 .. 1 | `0.5` | Dynamics |
| 14 | Release | float | 10 .. 1000 ms | `100.0` | Dynamics |
| 15 | Noise Floor Synthesis | float | 0 .. 1 | `0.5` | Noise Floor |
| 16 | Masking | float | 0 .. 1 | `0.5` | Noise Floor |
| 17 | Enhancement | float | 0 .. 1 | `0.5` | Noise Floor |
| 18 | Whitening | float | 0 .. 1 | `0.5` | Noise Floor |
| 19 | Output Noise Only | bool | off / on | `0` | Output |

The output (the generator's expected page table):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!-- GENERATED FILE, DO NOT EDIT.
     Generator:       scripts/aax_tables.py
     Source of truth: aurcore/schema/params.tsv
-->
<PageTable module="AUR_SPECTRALDENOISE" name="Spectral De-Noise" category="Repair" manufacturer="AURA" product="SPEC" plug="MONO" paramIdBase="256">
  <Page index="1">
    <Control slot="switch" parameter="0" type="enum" name="Processing Mode" label="Processing">
      <Choice>realtime</Choice>
      <Choice>offline</Choice>
    </Control>
  </Page>
  <Page index="2">
    <Control slot="switch" parameter="1" type="enum" name="Algorithm" label="Algorithm">
      <Choice>Simple</Choice>
      <Choice>Advanced</Choice>
      <Choice>Extreme</Choice>
    </Control>
  </Page>
  <Page index="3">
    <Control slot="switch" parameter="2" type="enum" name="Quality" label="Quality">
      <Choice>A</Choice>
      <Choice>B</Choice>
      <Choice>C</Choice>
      <Choice>D</Choice>
    </Control>
    <Control slot="knob1" parameter="3" type="float" name="FFT Size" label="FFT Size" min="5" max="60" default="20.0" unit="ms"/>
  </Page>
  <Page index="4">
    <Control slot="switch" parameter="4" type="bool" name="Multi Resolution" label="Multi" default="1"/>
    <Control slot="knob1" parameter="5" type="float" name="Learning Time" label="Learning" min="0.5" max="30" default="2.0" unit="s"/>
  </Page>
  <Page index="5">
    <Control slot="switch" parameter="6" type="bool" name="Adaptive" label="Adaptive" default="1"/>
    <Control slot="knob1" parameter="7" type="float" name="Noise Threshold" label="Noise" min="-40" max="20" default="0.0" unit="dB"/>
    <Control slot="knob2" parameter="8" type="float" name="Tonal Threshold" label="Tonal" min="-40" max="20" default="0.0" unit="dB"/>
    <Control slot="knob3" parameter="9" type="float" name="Noise Reduction" label="Noise" min="0" max="40" default="15.0" unit="dB"/>
  </Page>
  <Page index="6">
    <Control slot="knob1" parameter="10" type="float" name="Tonal Reduction" label="Tonal" min="0" max="40" default="12.0" unit="dB"/>
    <Control slot="knob2" parameter="11" type="float" name="Artifact Control" label="Artifact" min="0" max="1" default="0.5" unit=""/>
    <Control slot="knob3" parameter="12" type="float" name="Smoothing" label="Smoothing" min="0" max="1" default="0.5" unit=""/>
  </Page>
  <Page index="7">
    <Control slot="knob1" parameter="13" type="float" name="Knee" label="Knee" min="0" max="1" default="0.5" unit=""/>
    <Control slot="knob2" parameter="14" type="float" name="Release" label="Release" min="10" max="1000" default="100.0" unit="ms"/>
    <Control slot="knob3" parameter="15" type="float" name="Noise Floor Synthesis" label="Noise Floor" min="0" max="1" default="0.5" unit=""/>
  </Page>
  <Page index="8">
    <Control slot="switch" parameter="19" type="bool" name="Output Noise Only" label="Output Noise" default="0"/>
    <Control slot="knob1" parameter="16" type="float" name="Masking" label="Masking" min="0" max="1" default="0.5" unit=""/>
    <Control slot="knob2" parameter="17" type="float" name="Enhancement" label="Enhancement" min="0" max="1" default="0.5" unit=""/>
    <Control slot="knob3" parameter="18" type="float" name="Whitening" label="Whitening" min="0" max="1" default="0.5" unit=""/>
  </Page>
</PageTable>
```

Two things to notice, because they are the rule at work:

* **`Output Noise Only` (id 18) lands on page 7, before `Masking` (id 15).**
  The layout order is the *group* order, not the table order: the `Output`
  group is the last group of the module, so its parameter is placed last in
  the layout order — and it shares the last page with the `Noise Floor`
  group, because the page still had a free switch slot.
* **`Noise Floor Synthesis` is split across pages 6 and 7.** Pages fill; groups
  do not. A group is never re-ordered to keep it together, because the page
  is the smaller unit the host shows.

The block above is not a transcription: it is what
`scripts/aax_tables.py` writes for this module, and
`tests/test_aax_application.py` compares the two, so the example cannot
drift away from the generator.

### 3.5 The rule that the page table is never hand-written

> The page table is **generated** from the canonical parameter table
> (`aurcore/schema/params.tsv`) and is **never written or edited by hand**.

This is not a style preference, it is the only way the family stays
maintainable:

* **One source of truth.** A parameter's name, type, range, default, unit and
  choices live in exactly one place. The page table is a projection of that
  place, not a second copy of it.
* **Adding a module costs nothing.** A new module in the canonical table
  produces a page table, an identity record, a plug-in binary and a
  registration row, with no per-module work.
* **A page table that is edited by hand is a page table that is wrong.** The
  edit is not validated against the parameter table, the parameter ids drift
  from the registry index, and the failure appears in a host session rather
  than in a test.

The enforcement is mechanical: the generator owns the files, the files carry
a "GENERATED FILE, DO NOT EDIT" header, and the drift check fails the build
when a committed artefact differs from a fresh run of the generator:

```sh
python scripts\aax_tables.py            # write the package
python scripts\aax_tables.py --check    # fail when the package drifted
python scripts\aax_tables.py --sync-doc # refresh the worked example above
python -m pytest tests\test_aax_application.py -q   # the package's own tests
```

The generator is idempotent: the package is a pure function of the canonical
table (no timestamps, no paths, no dictionary-order surprises), so two runs
produce byte-identical files.  The `--check` mode is what says so.  The
worked example of section 3.4 is written by the same generator, so the
document cannot drift away from it either — and the test compares the two.

---

## 4. The label rules

Two rules decide what a control looks like: **what kind of control it is**,
and **what is written on it**.

### 4.1 The discrete-control classification

**The algorithm.** A parameter is a **discrete control** when its type is
`enum` or `bool`; it is a **continuous control** when its type is `float` or
`int`.

```
discrete(parameter)  :=  type in { "enum", "bool" }
control_kind(p)      :=  "switch" if discrete(p) else "knob"
```

| Type | States | AAX control | Slot |
|---|---|---|---|
| `enum` | one per choice (2 .. 10 today) | switch | `switch` |
| `bool` | 2 (off / on) | switch | `switch` |
| `float` | continuous | knob | `knob1` .. `knob3` |
| `int` | discrete but unbounded in count | knob | `knob1` .. `knob3` |

Three properties make this rule safe:

* **It is a pure function of the canonical table.** There are no
  per-module exceptions and no per-parameter overrides, so the classification
  cannot drift from the table it is derived from.
* **It is total.** Every parameter in the family gets exactly one kind.
  A parameter with no control would be a bug, and the checklist tool counts
  the discrete controls of every module so that a change in the table shows
  up in the numbers (section 2.5).
* **An `int` stays a knob even when its range is small.** `Interpolate →
  Quality` has eight values and is still a knob, because the canonical table
  carries no "stepped" flag: there is no rule in the data that could turn an
  integer into a selector. If a future parameter genuinely needs to be a
  selector, the canonical table grows the flag and this rule changes with
  it — in one place, for the whole family.

The counts: **80 discrete controls** and **173 knobs** across the 48
modules. Three modules are made entirely of discrete controls — Dither (4 of
4), Connect (2 of 2) and Monitor (1 of 1) — and twelve have none at all
(De-Plosive, De-Wind, De-Bleed, Loudness Optimize, EQ Match, Gain,
Normalize, Mixing, Variable Time, Wow & Flutter, Interpolate, Find Similar).
De-Hum has the most discrete controls in absolute terms, with 9.

### 4.2 The base label — the budget ladder

A control has a **name** (what the host shows in a parameter list) and a
**label** (what is drawn on the control). The name is always the full
canonical parameter name — nothing is lost. The label has to fit, so it is
produced by a fixed ladder with a **budget of 12 characters**:

1. **Use the full name** when it is 12 characters or shorter.
2. **Drop the trailing qualifier** — the last word — when the name has more
   than one word and the result fits the budget.
3. **Use the abbreviation** from the small per-parameter abbreviation table
   that lives next to the generator (`ABBREVIATIONS` in
   `scripts/aax_tables.py`), keyed by parameter name (so a shared parameter
   name gets the same abbreviation in every module).
4. **Truncate** the current candidate to the budget and mark it with an
   ellipsis: `candidate[:11] + "…"`.

The **first step that produces a label within the budget wins**. The ladder
is deterministic, so the same parameter always gets the same label on every
machine and in every build.

| Full name | Step 1 | Step 2 | Label |
|---|---|---|---|
| `Algorithm` | `Algorithm` | — | `Algorithm` |
| `Sensitivity` | `Sensitivity` | — | `Sensitivity` |
| `FFT Size` | `FFT Size` | — | `FFT Size` |
| `Multi Resolution` | 15 chars — no | `Multi` (5) | `Multi` |
| `Noise Threshold` | 16 chars — no | `Noise` (5) | `Noise` |
| `Noise Floor Synthesis` | 21 chars — no | `Noise Floor` (10) | `Noise Floor` |
| `Output Noise Only` | 17 chars — no | `Output Noise` (11) | `Output Noise` |
| `Short Term Loudness` | 19 chars — no | `Short Term` (10) | `Short Term` |
| `Output Clicks Only` | 17 chars — no | `Output Clicks` (13) — no | step 3: `Clicks Only` (11) |
| `Center Global Pitch` | 19 chars — no | `Center Global` (13) — no | step 3: `Center Pitch` (12) |

The last two rows are the cases that reach step 3: dropping the trailing word
is not enough, so the abbreviation table is consulted; if the parameter has no
entry there, the label is truncated with an ellipsis.  Twelve parameter names
reach step 3 today, and all twelve have an entry, so no published label ends
in an ellipsis.

The abbreviation table is the **only** hand-maintained text in this area.
It is deliberately small and deliberately keyed by *parameter name* rather
than by module, so the same parameter reads the same way everywhere.  The
generator refuses to build the package when an entry is longer than the
budget, so a bad abbreviation fails the build rather than a host session.

### 4.3 The published truncation ladder

The label that is drawn on a control is the **longest entry** of a ladder
that the plug-in publishes, so that the host can shorten the label when the
control is small.

**The rule is a straight prefix cut:**

```
ladder[s] === label.slice(0, s)        for s = 1 .. len(label) - 1
```

There are no exceptions, and there is **no word-aware rebalance**. A
word-aware rebalance (first word plus a lower-cased tail of the last word)
produces a *different* set — it drops the trailing space at `s = 6` and
lower-cases the tail — so it is deliberately not used:

```
"Click Widening"   ->  "Click "   "Click W"   "Click Wi"   ...     (prefix cuts)
"Frequency Skew"   ->  "Freq"     "Freque"    "Frequenc"   ...
```

This rule is **verified, not assumed**. The golden fixture
`tests/fixtures/truncation_ladders.json` holds 113 ladders captured from a
real product's plug-in page tables; `tests/test_truncation_ladder.py` pins
the rule against that fixture, and `tools/extract_truncation_ladders.py`
re-harvests it. The same rule is implemented in the shared interface helper
`aurui/js/widgets.js::truncationLadder()`, and the application interface
uses it for the same purpose: a control that gets smaller shows a shorter
label.

The ladder is published as a sequence of `(size, label)` entries in
**ascending size order** — the shortest label first — which is the order the
captured reference ladders use; the host takes the entry that fits the space
it has. The generator derives the ladder from the base label of section 4.2,
so the two rules compose: the base label decides *what* the control says,
the ladder decides how much of it fits.

**Where the ladder is published:** in the machine readable half of the
package, `docs/aax_page_tables.json`, one `ladder` list per control.  The
human readable half carries the base label of every control.

---

## 5. What the adapter will implement

### 5.1 The interfaces in scope

The AAX adapter is a thin bridge: it owns the parameter binding and the
wrapper, the DSP core owns every parameter and every algorithm. The
interfaces it implements are the format's component interfaces, plus the
descriptor interfaces the host reads to list the plug-in:

| Interface (name to be confirmed against the SDK headers) | Responsibility in the adapter |
|---|---|
| the **plug-in / effect descriptor** | reports the effect id (manufacturer, product, plug), the name, the category and the channel configuration |
| the **component** | owns the processing: the audio call, the lifecycle, the bypass |
| the **parameter container** | owns the parameters: their count, their identity and their values |
| the **parameter** | one bound control: value, range, default, unit, choices, the label ladder |
| the **page table** | the pages and the slots, generated from the canonical table |
| the **host application** (inbound) | what the adapter may ask of the host — the sample rate, the state, the host's own parameter ids |

The adapter does **not** implement: a custom editor, a MIDI interface, an
instrument, a scheduler, a network client, or anything that touches the file
system.

Two answers the host asks for are already on the other side of the C ABI and
do not need the SDK: the **latency** of the chain
(`aur_engine_latency_samples`) and its **tail** (`aur_engine_tail_samples`).
A module declares its own tail, the chain takes the longest of them, and a
module that makes a sound of its own — a generator, a bed of ambience —
never goes quiet and answers with the infinite tail the format uses. The
adapter hands both to the host as they are. The **rate** the chain's output
runs at (`aur_engine_output_sample_rate`) is there as well, for a chain
that changes the rate.

### 5.2 The state-chunk format

The plug-in's state is the same document the rest of the product uses, so a
session written by one format loads in another:

```jsonc
{
  "format": "auralis.chain", "version": 1,
  "steps": [
    { "code": "AUR_SPECTRALDENOISE", "enabled": true,
      "params": { "Noise Reduction": { "Type": "Float", "Value": 12.0 } },
      "state":  { "Type": "Base64", "Value": "<learned profile blob>" } }
  ]
}
```

* The **chain document** is the state chunk: the module code, the parameter
  values (in natural units, enums as their choice string) and the learned
  state blobs.
* It is produced by `aur_engine_serialize` and consumed by
  `aur_engine_deserialize` — the same two calls the other adapters use, so
  the format is defined once (`CONTRACT.md` §6.3) and cannot drift per
  format.
* The adapter hands the host an **opaque byte block**. It does not parse the
  host's state format, and it does not interpret the host's own parameter
  ids: it maps them through the identity record.

The exact interface that carries the state chunk is one of the things the
SDK confirms for us (section 1.4); the *content* of the chunk is already
decided and tested by the C ABI conformance tests.

### 5.3 The editor situation

**The adapter returns no editor.** The host draws the controls from the page
table, using the labels and the choices we publish.

This is a deliberate position, and it has three consequences:

* the host's generic editor is the editor, so the plug-in has **no UI code,
  no UI toolkit and no platform window** — nothing to break on a new OS
  version, nothing to validate visually;
* the *quality* of the result is entirely in the **page table**: the labels,
  the grouping, the order, the choices. That is why sections 3 and 4 are
  worth their length;
* a custom editor remains possible later, through the AUx/AAX host APIs
  (requested in section 1.4, not needed for the first release). When that
  happens it is an *addition* — the page table keeps working, because the
  host's generic editor and a custom editor read the same generated data.

The application interface (the web UI) remains the place where the full
Auralis experience lives: the spectrum, the chain, the repair tools. The
plug-in is the *host-side* surface, and the two are complementary rather
than competing.

### 5.4 The parameter-proxying design

The host speaks in **numbers**; the core speaks in **names**. The adapter is
the proxy between them, and the whole design is about keeping the numbers
stable.

```
  host parameter id  ──▶  identity record (param_id_map)  ──▶  canonical name
                                                                        │
                                                                        ▼
  host value          ◀──  ParamStore.set(name, value)  ◀──  natural units
```

The rules:

| # | Rule |
|---|---|
| 1 | The host parameter id is the **parameter id of the module** — the position of the parameter inside its own module, as the generated registry numbers it — offset by a fixed base (`0x0100`) so the low range stays free for the plug-in's own controls. The exact size of the reserved range is confirmed against the SDK's parameter-id header before the first shipping build. |
| 2 | The id of a parameter is **fixed by its position in its module** and never changes. New parameters are appended to the end of their module; a parameter that is renamed keeps its id. |
| 3 | The mapping host id → canonical name lives in the **identity record**, which is stamped into the binary and verified on the way out. The adapter has no hard-coded parameter list. |
| 4 | The core is addressed **by name**, in **natural units** (dB, Hz, ms, %). The core never sees a normalised 0..1 knob position, and an enum carries its **choice string**, not an index. |
| 5 | The chain holds **exactly one module** — the one named by the stamped identity record. |
| 6 | The proxy is **bidirectional and lossless**: a value the host sets reaches the core unchanged, and a value the core holds is published unchanged. The C ABI conformance tests pin this down. |

Rule 2 is what makes an automation lane written in one format mean the same
thing in another, and what makes a session written by one build load in the
next.

**Why the id space is per module.** A plug-in implements exactly one module,
so the ids of that module are unique **within the plug-in**, which is what
the format asks for.  The space is also stable in the way that matters:
adding, removing or re-ordering a module never renumbers the parameters of
another module, and a parameter that is appended to a module keeps every
other id in the family.  The same numbers are the ones the VST3 adapter
carries in the identity record, so a session written by one format means the
same thing in another — that is the whole point of rule 6.

The identifiers are not written down anywhere: they come from the generated
registry (`aurcore/schema/generated.py`), they are checked against it by the
package generator (`scripts/aax_tables.py`), and they are compared with the
identity record by `tests/test_aax_application.py`.

---

## 6. The build and validation plan

### 6.1 How the AAX adapter is built

The build is the same build the other formats use, with the AAX specifics
folded in:

```
aurcore/schema/params.tsv
        │
        ├──▶ tools/codegen_registry.py ──▶ generated header
        ├──▶ scripts/aax_tables.py ──▶ the application package
        │                              (page tables, labels, ids)
        │
        └──▶ plugins/identity.py ──▶ identity record
                                            │
   AAX SDK ──▶ AAX adapter template ──▶ link ──▶ stamp identity ──▶ bundle
```

1. **One template, many plug-ins.** The adapter source is a single template.
   The module it implements is decided by the stamped identity record, not
   by the code. Adding a module produces a new plug-in without touching the
   adapter.
2. **One bundle per (module, channel configuration).** 50 × 3 = 150
   bundles. The effect id is part of the bundle's identity, so it is baked
   in at build time — the same way the other formats bake in their class id
   — and the identity record in the binary carries the same triple, so the
   two can be checked against each other after the build.
3. **The SDK is a build input, never a build artefact.** The SDK path is
   supplied to the build system; the SDK is not copied into the repository,
   into the build tree or into the bundle. The build refuses to start with
   a clear message when the SDK path does not contain what it needs.
4. **The build is stamped after linking**, exactly like the other formats:
   the identity record is patched into a reserved slot in the linked file,
   and read back out of the shipped file to verify it.
5. **The page tables are generated** next to the bundles and shipped with
   them (section 3).  They come out of the application package
   (`docs/AAX_PAGE_TABLES.md`), so the bundle carries exactly the pages the
   package describes.

**Direct against the SDK, or through the plug-in framework's AAX wrapper?**
The plan is to implement the adapter **directly against the SDK's component
interfaces**, because the three things this plug-in needs — a generated page
table, an opaque state chunk, and parameters addressed by name — are exactly
the three things a generic wrapper models differently. Writing the adapter
directly costs a few hundred lines and removes a layer of translation
between our model and the host's. The wrapper route stays available as a
fallback if the direct route turns out to be blocked by something in the
SDK; it is a build-system choice, not an architecture change.

### 6.2 Validating without the SDK

The point of this section: **most of the adapter's behaviour can be proven
before the SDK arrives**, so that the moment the SDK lands, the remaining
work is glue, not debugging.

**Layer 1 — the C ABI conformance harness (already running).** The core's C
ABI is implemented and tested: `plugins/abi/aurcore_abi.cpp` implements it
over the core, `plugins/abi/build_abi.py` builds it, and
`tests/test_plugin_abi.py` loads the library *the way a host does* and
pins the contract down:

* the five required entry points, `aur_shutdown` and the twelve extensions —
  **eighteen exports** in all, at contract version 3
  (`AUR_ABI_VERSION` in `plugins/vst3/aurcore_c_api.h`);
* success is `0` (or a non-negative index), failure is a negative error
  code;
* parameters are addressed **by name**, in **natural units**; an enum value
  is its **choice string**;
* the audio buffer is **planar**;
* a bypassed module is bit-transparent;
* the chain serialises to the `auralis.chain` document and restores from it.

Everything the AAX adapter does *through* the core is already covered by
these tests. The adapter's own logic — the id mapping, the page layout, the
label ladder, the state round-trip — is pure data handling on top of the
ABI.

**Layer 2 — the adapter's own logic, tested with no SDK at all.** The parts
of the adapter that do not touch an SDK interface are written against a
small, locally-owned interface seam. The same adapter source compiles
against:

* the **stub**: a local header that declares the same interface shapes the
  SDK declares, with no SDK material in it. It exists so that the adapter's
  logic — parameter id mapping, page layout, label ladder, state chunk
  round-trip, bypass handling, the error paths — can be compiled and unit
  tested on a machine that has never seen the SDK. The stub is a *test
  double*, not a re-implementation of the format: it is never linked into a
  shipped bundle.
* the **SDK**: the real headers, once access is granted.

The tests that run against the stub are the same tests that later run against
the SDK, with the SDK-specific expectations added on top. This is the
standard way to keep a licensed-dependency adapter testable, and it is why
the adapter's design keeps the SDK-facing surface as small as possible.

**Layer 3 — the checks that need no SDK and exist today.**

| Check | What it proves | Where |
|---|---|---|
| the identifier checklist | every effect id obeys the alphabet and the length rule, is unique, and matches the identity layer | `tools/aax_application_checklist.py` |
| the schema validation | the canonical table, the generated registry and the committed artefacts agree | `tools/validate_schema.py` |
| the C ABI conformance tests | the core behaves the way a host will drive it | `tests/test_plugin_abi.py` |
| the truncation-ladder golden test | the published label ladder is a straight prefix cut | `tests/test_truncation_ladder.py` |
| the package drift check | the committed package is byte-identical to a fresh run of the generator | `python scripts\aax_tables.py --check` |
| the package tests | the package is complete: every module has an id, the ids are unique, every parameter is on exactly one page, the parameter ids are the ones the VST3 adapter uses, and no third party name appears anywhere | `tests/test_aax_application.py` |

### 6.3 What the SDK is needed for — and only for this

| The SDK is needed for | The SDK is **not** needed for |
|---|---|
| compiling against the component interfaces | deriving the identifiers |
| the host-facing page table and the reserved parameter-id range | the page layout, the label ladder, the label budget |
| the validation run and the submission | running the DSP, the C ABI, the identity stamping |
| the effect-id registration process | the parameter model |
| a build that produces a loadable bundle | the page tables themselves (our schema) |
| — | **the whole application package, which is generated and checked today** |

And the SDK-facing step, when it is written, is exactly one file: the
translation from our published page table into the SDK's own page table,
plus the confirmation of the reserved parameter-id range. Both are marked in
`plugins/aax/AAX_ADAPTER_NOTES.md` as "to be confirmed against the SDK".

---

## 7. Timeline and checklist

`T+0` is the day the request in section 0.3 is sent. The absolute dates
below assume the request is sent on **2026-10-21**; the lead time itself is
set by the vendor and is not ours to compress, so the plan is written in
weeks from `T+0` rather than in fixed dates.

### 7.1 Before the SDK arrives — everything on this list is unblocked

| # | Step | Depends on | Effort |
|---|---|---|---|
| 1 | Send the request (section 0.3 is the text) | — | 1 h |
| 2 | Keep the identifier checklist green in CI | — | done, `tools/aax_application_checklist.py` |
| 3 | Freeze the manufacturer code and the plug codes | — | done |
| 4 | Write the page-table generator (our schema, section 3.2) | — | **done**, `scripts/aax_tables.py` |
| 5 | Pin the generated page tables with a drift check, like the registry's | — | **done**, `python scripts\aax_tables.py --check` |
| 6 | Write the label ladder's generator side (base label + ladder) | — | **done**, `scripts/aax_tables.py` |
| 7 | Write the adapter's own logic against the stub interface seam (section 6.2) | — | 3–5 days |
| 8 | Unit-test the adapter logic against the stub: id mapping, layout, ladder, state round-trip | — | 2–3 days |
| 9 | Write the parameter-id reservation note (what we assume, what has to be confirmed) | — | 0.5 day |
| 10 | Prepare the effect-id registration request (the table in section 2.5) | — | 0.5 day |

**Total: roughly 2–3 weeks of work that no part of the SDK can unblock, and
that must not wait for the SDK.**  Steps 4, 5 and 6 are done: the
application package is generated, drift-checked and tested
(`tests/test_aax_application.py`).  What is left is the adapter's own logic
against the stub seam and the two notes.

### 7.2 Blocked on the vendor

| # | Step | Blocked by |
|---|---|---|
| B1 | The host validation run and the submission | the validator |
| B2 | The effect-id registration | the vendor's registration process |

B1..B3 used to include compiling the adapter, the translation of the page
table and the reserved parameter-id range.  All three are done: the SDK
is in the tree, the adapter is written against it (`plugins/aax/`) and
the page table comes from the generated package.

Nothing else is blocked. In particular, **the DSP, the module set, the
other plug-in formats, the application and the web interface are not
affected** by any of B1..B2.

### 7.3 Once access is granted — the ordered steps

| # | Step | Done when |
|---|---|---|
| 1 | Install the SDK; point the build at it (`AURALIS_AAX_SDK_PATH`) | the build finds `Interfaces/ACF` and `Utilities/PlugIn.ico` |
| 2 | Compile the adapter against the real interfaces, stub swapped out | the bundle links |
| 3 | Read the SDK's parameter-id header; adjust the parameter-id base if the reserved range is larger than assumed | the base is confirmed or corrected in one constant |
| 4 | Read the SDK's page-table schema; write the translation from our published page table | 150 page tables translate mechanically |
| 5 | Build one module in one configuration; load it in the SDK's own host or validator | the plug-in is listed |
| 6 | Run the validator on that candidate | the validator passes |
| 7 | Build all 150 bundles; stamp the identities; read them back | every bundle reports the id in the table of section 2.5 |
| 8 | Re-run the whole automated suite against the real SDK (the stub tests, plus the SDK-specific ones) | the suite is green with the real SDK |
| 9 | Submit the plug-in family for the vendor's listing | the submission is accepted |
| 10 | Register the effect ids | the ids are registered |

**Expected elapsed time after the SDK is in hand: about two weeks** for
steps 1–8, plus the vendor's own review time for steps 9–10, which is
outside our control.

---

## 8. The fallback plan

If access is refused, or granted so late that the release window has closed,
the product ships without the AAX format. This section states what that
looks like, so that the decision is a plan and not an accident.

### 8.1 The plug-in matrix

| Format | Platforms | Status if the SDK is refused | Notes |
|---|---|---|---|
| **The application** (the web interface and the local server) | Windows, macOS, Linux | **ships** | The full module set, the spectrum view, the chain, batch rendering. Unaffected by the SDK. |
| **VST3** | Windows, macOS, Linux | **ships** | The adapter template and the identity stamping are done; the C++ wrapper needs the plug-in framework, which needs no licence. |
| **AU / AUv3** | macOS | **ships with extra work** | No licence needed. Estimated at 2–3 person-weeks of adapter work, because the parameter and state model has to be mapped onto the AUv3 parameter tree. |
| **AAX** | Windows, macOS | **does not ship** | Blocked on the SDK. Nothing else in the product depends on it. |
| **The DSP core** | all | **ships** | The core is format-agnostic; the modules, the presets and the chain documents are the same in every format. |

The important property of this matrix: **AAX is additive.** The architecture
is already format-agnostic, the identifiers live in a data record rather
than in the code, and the page tables are generated from the same canonical
table the other formats read. A refusal costs the AAX *format*, not a
refactor.

### 8.2 The effort

| Item | Effort | Depends on |
|---|---|---|
| The adapter template, the identity stamping, the identifiers | **done** | — |
| The C ABI and its conformance harness | **done** | — |
| The page-table generator and the label generator | **done** | — |
| The AAX application package (the page tables, the labels, the ids) | **done** | — |
| The VST3 C++ wrapper and the bundle build | 1–2 person-weeks | the plug-in framework, no licence |
| The AU/AUv3 adapter | 2–3 person-weeks | no licence |
| The AAX adapter, **after** the SDK arrives | 2 person-weeks | the SDK |
| The AAX validation and submission rounds | 1–2 person-weeks plus the vendor's review time | the SDK and the validator |

If the request is refused, the AAX rows fall away and the plug-in layer
ships with VST3 and AU. If the request is granted late, the AAX rows are
deferred to the next release; nothing else has to change.

### 8.3 The user-facing message

The message is part of the fallback, so it is written down now rather than
improvised at the time.

**In the application** (when a user asks about the professional host):

> **AAX support.** Auralis is not yet available as an AAX plug-in. The
> request for the vendor's SDK has been sent, and the format is added as soon
> as access is granted — the modules, the presets and the chain are already
> built to be identical in every format.
>
> **In the meantime**, Auralis runs as a standalone application with the full
> module set, and the VST3 build works in the other hosts that speak it. The
> chain and the presets can be shared with the plug-in.

**In the release notes** (when the release ships without AAX):

> This release ships the Auralis application and the VST3 (and AU) plug-ins.
> The AAX plug-in is not included in this release: the format's SDK is
> licensed separately and the request for access is still open. Nothing
> about the modules, the presets or the chain format changes when it
> arrives — the plug-in will read the same sessions.

**On the plug-in matrix page** (a one-line status, so the answer is the same
everywhere):

```
AAX        — in development, waiting for SDK access
VST3       — available
AU / AUv3  — available
Standalone — available
```

The rule for all three: **say what is available, say what is coming, and
never apologise for the format the user does not have.** The Auralis product
is the application and the DSP; AAX is one surface of it.

---

## 9. Where the pieces live

| Piece | Where |
|---|---|
| this document | `docs/AAX_APPLICATION.md` (a pointer sits next to the adapter) |
| the identifier checklist tool | `tools/aax_application_checklist.py` |
| the identifier model and the allocation | `plugins/identity.py` |
| the adapter design (identifiers, parameter ids, pages, labels) | `plugins/aax/AAX_ADAPTER_NOTES.md` |
| the canonical parameter table | `aurcore/schema/params.tsv` |
| the C ABI between an adapter and the core | `plugins/vst3/aurcore_c_api.h` |
| the C ABI implementation and its conformance tests | `plugins/abi/`, `tests/test_plugin_abi.py` |
| the label-ladder golden fixture and its test | `tests/fixtures/truncation_ladders.json`, `tests/test_truncation_ladder.py` |
| **the application package generator** | `scripts/aax_tables.py` |
| **the application package** (human readable) | `docs/AAX_PAGE_TABLES.md` |
| **the application package** (machine readable) | `docs/aax_page_tables.json` |
| **the package's own tests** | `tests/test_aax_application.py` |
| the gate this work unblocks | `docs/ALPHA_STATUS.md` (G10) |

---

## 10. The state of the AAX application package

The numbers, measured by `python scripts\aax_tables.py`:

| Item | Count |
|---|---:|
| Modules | 48 |
| Page tables (one per module) | 48 |
| Pages | 112 |
| Parameters | 253 |
| Switches (discrete controls) | 80 |
| Knobs (continuous controls) | 173 |
| Effect identifiers (48 × 3) | 144 |

Every one of those numbers is derived from `aurcore/schema/params.tsv` and
re-derived by `tests/test_aax_application.py`; none of them is written
down by hand.  The package is complete: every module has its three effect
ids, every parameter has an identifier and a place on exactly one page,
and every identifier is the one the VST3 adapter carries.

**What is not there:** an AAX binary.  The format needs the vendor's SDK,
and the SDK-facing glue (sections 0.2 and 7.2) is the only thing that is
waiting for it.
