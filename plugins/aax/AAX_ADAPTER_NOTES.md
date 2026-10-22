# AAX adapter notes

**Status: implemented, and measured against the format's own validator.**

The format's SDK is a separate download, so the build is told where it is
(`python scripts\build_aax.py --sdk PATH`, or the `AURALIS_AAX_SDK_PATH`
environment variable); the copy in the tree is the fallback.  What this
repository *does* own is everything that does **not** depend on the SDK: the
identifiers, the parameter table, the page layout and the rules that produce
them.  Those parts are generated and verified here, and the SDK-facing glue
is built and measured against the SDK itself.

Everything in this document is derived from the canonical parameter table
(`aurcore/schema/params.tsv`). Nothing about a module is written by hand.

---

## 1. The identifier model

An AAX plug-in is identified by three four-character codes:

| Component      | Meaning                          | Value in Auralis                     |
|----------------|----------------------------------|--------------------------------------|
| Manufacturer   | who made the plug-in             | `AURA`                               |
| Product        | which product within the maker   | the module's four-character code     |
| Plug           | which channel variant of it      | `MONO`, `M2ST` or `STER`             |

Together they form the **EffectID** of the plug-in.

### 1.1 Manufacturer

`AURA`, four characters, shared with the VST3 manufacturer code. One product
family, one manufacturer id, across every format.

### 1.2 Product

The product id is the module's **product code** — the same four characters
that the VST3 format uses as its plugin code, and therefore the same four
characters that the VST3 class id is derived from.

That is deliberate. A module has one identity across formats:

```
AUR_DECLICK  ->  DECL  ->  VST3 class id 0101abab-abcd-ef01-4155-52414445434c
                        ->  AAX effect id AURA:DECL:MONO
```

The codes are assigned by `plugins/identity.py` over the **whole** registry,
so a module keeps its code no matter which subset of modules is asked for.
When two modules want the same natural code (the first four characters of
the module's code suffix) the first one in registry order keeps it and the
others get a code derived from a hash of their own module code. A re-assigned
code never takes the natural code of another module.

### 1.3 Plug — the channel variants

AAX effect ids are per channel configuration. A mono plug-in and a stereo
plug-in are different plug-ins to the host, so they need different ids.
Each module therefore has **three** effect ids:

| Plug id | Configuration | Input  | Output |
|---------|---------------|--------|--------|
| `MONO`  | mono          | 1      | 1      |
| `M2ST`  | mono to stereo| 1      | 2      |
| `STER`  | stereo        | 2      | 2      |

The parameter set is identical in all three; only the routing differs. The
adapter builds the same chain in every case and lets the wrapper's channel
configuration decide the rest.

### 1.4 The record

The three triples live in the identity record, in `aax_effect_ids`:

```json
"aax_effect_ids": [
  ["AURA", "DECL", "MONO"],
  ["AURA", "DECL", "M2ST"],
  ["AURA", "DECL", "STER"]
]
```

Every component is exactly four characters, which the JSON Schema enforces
(`plugins/identity.schema.json`).

---

## 2. Parameter ids

AAX addresses parameters by a 32-bit id that is unique **within the plug-in**.
Our ids are the ids of the generated index - **the position of the parameter
inside its own module**, counted from zero - shifted by a fixed base so that
the low range stays free for the plug-in's own controls:

```
aax_parameter_id = AURALIS_AAX_PARAM_ID_BASE + parameter_id
```

with `AURALIS_AAX_PARAM_ID_BASE = 0x0100` (256). The format reserves **no
numeric range**: its own control identifiers are strings (`"PreviewID"`,
`"MasterBypassID"`), and the rule the SDK applies when it filters a parameter
out of the saved state is a text prefix, not a number. A numeric base
therefore cannot collide with them.

**The id space is per module, not registry-wide.**  The numbers are `0 … n−1`
*inside* each module: the ids of De-Click are 0 … 4 and the ids of De-Hum are
0 … 18, so the same number names different parameters in different modules.
That is deliberate and it is safe, because a plug-in implements exactly one
module: the ids are unique within the plug-in, which is what the format asks
for, and adding, removing or growing a module never renumbers another one.

The rule is enforced by the generator, not by hand: `scripts/aax_tables.py`
numbers the parameters from the canonical table (`assign_parameter_ids`) and
then compares its own numbering with the one the plug-in layer publishes
(`plugins/identity.py`, `param_index()`); a disagreement fails the build
(`cross_check`).  The numbers in this package are therefore the numbers the
VST3 adapter carries in the identity record, so a session written by one
format means the same thing in another.

The important property is not the value but the **rule**: the id of a
parameter is fixed by its position in its own module and never changes. See
`plugins/README.md`, "Parameter id stability".

---

## 3. Pages

An AAX plug-in presents its parameters as **pages**. A page is a fixed set of
control slots — one switch slot and three knob slots — and each slot
is bound to exactly one parameter id. The host shows one page at a time and
lets the user page through them.

The page layout is **generated** from the parameter table. It is never
written by hand, and it is regenerated whenever the parameter table changes.

### 3.1 The layout rule

For each module:

1. Parameters are grouped by their `group` column in the parameter table.
2. Groups are laid out in the order they first appear.
3. Inside a group, parameters keep their table order.
4. A parameter fills the next free slot:
   * a **switch** (an enum, or a boolean) fills the page's switch slot;
   * a **knob** (a float or an int) fills the next free knob slot.
5. When a page is full, the next parameter starts a new page.

An enum is a switch, whatever the number of its choices, and it occupies the
page's single switch slot like any other discrete control.  The choices are
the text the control draws, so a long enum does not need a page of its own;
what a long enum costs is width, not a page.  The page count of a module is
therefore driven by the number of its discrete controls and by the number of
its continuous ones, and the module with the most pages in the family is
De-Hum (19 parameters, 9 of them discrete, 9 pages - `docs/AAX_PAGE_TABLES.md`).

### 3.2 A worked example — De-Click

De-Click (`AUR_DECLICK`, product code `DECL`) has five parameters in the
canonical table.  The ids are the ones of the module itself, `0 … 4`:

| Parameter id (inside the module) | Name                | Type  | Range        | Default       | Group            |
|-------------|---------------------|-------|--------------|---------------|------------------|
| 0           | Algorithm           | enum  | 4 choices    | `Single Band` | Algorithm        |
| 1           | Sensitivity         | float | 0 .. 10      | 3.0           | Sensitivity      |
| 2           | Frequency Skew      | float | -1 .. 1      | 0.0           | Frequency Skew   |
| 3           | Click Widening      | float | 0 .. 1       | 0.0           | Click Widening   |
| 4           | Output Clicks Only  | bool  | off / on     | off           | Output           |

Laid out with one switch slot and three knob slots per page:

```
Page 1
  switch : Algorithm            (id 0)   choices: Single Band | MB Periodic | MB Random | Low Latency
  knob 1 : Sensitivity          (id 1)   0 .. 10
  knob 2 : Frequency Skew       (id 2)   -1 .. 1
  knob 3 : Click Widening       (id 3)   0 .. 1

Page 2
  switch : Output Clicks Only   (id 4)
```

The table above is the input; the page layout above is the output. Both come
from the same file.

### 3.3 The generated page table

The generator emits one page table per module, in a machine-readable form
that the SDK-facing step turns into the format's page-table document. **The
format's shape is now known:** the document is a resource in the plug-in
bundle (`PageTables` → `PageTableLayouts` → `PTLayout` → `PageTable` →
`Page` → `Cell`), keyed by the plugin triad, with the parameter bound by
`knobID` / `selectButtonID` / `inOutButtonID`, and the control-name
variations in `ControlNamesVariations`. The plug-in declares that resource
to the host. **The document above is still ours, not the format's** — the
format's own document is the one of §3.4, and the format's validator loads
it and passes the page-table checks.

This is what `scripts/aax_tables.py` writes for De-Click, byte for byte:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!-- GENERATED FILE, DO NOT EDIT.
     Generator:       scripts/aax_tables.py
     Source of truth: aurcore/schema/params.tsv
-->
<PageTable module="AUR_DECLICK" name="De-Click" category="Repair" manufacturer="AURA" product="DECL" plug="MONO" paramIdBase="256">
  <Page index="1">
    <Control slot="switch" parameter="0" type="enum" name="Algorithm" label="Algorithm">
      <Choice>Single Band</Choice>
      <Choice>MB Periodic</Choice>
      <Choice>MB Random</Choice>
      <Choice>Low Latency</Choice>
    </Control>
    <Control slot="knob1" parameter="1" type="float" name="Sensitivity" label="Sensitivity" min="0" max="10" default="3.0" unit=""/>
    <Control slot="knob2" parameter="2" type="float" name="Frequency Skew" label="Frequency" min="-1" max="1" default="0.0" unit=""/>
    <Control slot="knob3" parameter="3" type="float" name="Click Widening" label="Click" min="0" max="1" default="0.0" unit=""/>
  </Page>
  <Page index="2">
    <Control slot="switch" parameter="4" type="bool" name="Output Clicks Only" label="Clicks Only" default="0"/>
  </Page>
</PageTable>
```

The `parameter` attribute is the **id of the parameter inside its module**,
the number of section 2 - `0 … n−1` inside the module, not a registry-wide
number and not a per-page index.  The SDK-facing step adds `paramIdBase`
(256) to it when it writes the declarations.

One page table serves the three channel configurations, because the
parameter set of a module is identical in all three: the `plug` attribute
is the only thing that differs between `MONO`, `M2ST` and `STER`, and the
machine-readable half of the package (`docs/aax_page_tables.json`) carries
the three effect ids of the module next to its one page table.  The 144 page
tables the host loads are materialised from these 48 by the SDK-facing step.

### 3.4 The page table resource — the format's own document

The document above is **ours**.  The format has a document of its own, and
the host reads that one: the page table is a **resource of the plug-in
bundle**, and the plug-in declares it to the host.

The format's interface says so (`AAX_Enums.h`):

    AAX_eResourceType_PageTable = 1,   // the file name of the page table xml file
    AAX_eResourceType_PageTableDir = 2, // the directory the file(s) are in;
                                        // it defaults to *.aaxplugin/Contents/Resources

So the plug-in declares the **name of the file** and the host looks for it
in `Contents/Resources` of the bundle:

```cpp
descriptor->AddResourceInfo (AAX_eResourceType_PageTable, "AuralisPages.xml");
```

The generator writes that document as well, in the format's own schema, and
the build copies it into the bundle:

    aurcore/schema/params.tsv
        -> scripts/aax_tables.py
        -> plugins/aax/resources/AUR_DECLICK.xml   (the format's document)
        -> scripts/build_aax.py
        -> build/aax/bundles/AUR_DECLICK.aaxplugin/Contents/Resources/AuralisPages.xml

The document is one `PageTables` element:

| Element | What it holds |
|---|---|
| `PageTables` | the document; the `vers` attribute is the version of the schema |
| `PageTableLayouts` | the layouts |
| `Plugin` | one per channel configuration: `manID`, `prodID`, `plugID` — the triad the host looks the plug-in up by — and the name of its layout |
| `PTLayout` | one per layout; it holds the page tables of the layout |
| `PageTable` | one per page table type; `type` is the kind of surface and `pgsz` the number of controls on a page |
| `Page` / `Cell` | the controls of a page |
| `ID` | the parameter id a control is bound by |
| `ControlNamesVariations` | the short names of the controls, per size |

The control is bound by the **parameter id** — the very string the adapter
binds it by in code (`256` for the first parameter of a module) — so the
document and the plug-in cannot disagree about which control is which.  The
three channel configurations of a module are three `Plugin` elements in the
same document, because the parameter set of a module is the same in all
three.

The **bypass switch** is the plug-in's own control — it carries the id every
format of the family uses — and it is listed in the document as well, alone
on the first page, the way the format's own examples write it.  The host
warns about an automatable parameter that is not in the page table, and the
warning is gone since the switch was listed.

### 3.5 The bundle

The format loads a plug-in from a **bundle directory**, not from a file:

    build/aax/bundles/AUR_DECLICK.aaxplugin\
        Contents\
            x64\AUR_DECLICK.dll          the plug-in
            Resources\AuralisPages.xml   the page table document

The plug-in inside `Contents/x64` has to carry the name of the bundle
directory.  A bundle whose two names differ is one the host cannot load: it
fails with an access violation instead of with a message.  The build makes
the two names agree, and `python scripts\build_aax.py check` reports a
bundle whose names differ.

**What the tree keeps, and why.**  The document of §3.3 is kept: it is the
worked example the application document quotes, and `scripts/aax_tables.py
--sync-doc` keeps that quotation in step with the generator.  It is not
shipped.  What ships is the document of §3.4, and the drift gate checks
both: `python scripts\aax_tables.py --check` compares every generated
document with a fresh run of the generator, and `python scripts\build_aax.py
check` compares the document **inside every built bundle** with a fresh run
of the generator, so a bundle cannot carry a hand-edited copy.

---

## 4. Labels

AAX control labels are short. A control has a *label* (what is drawn on the
control) and a *name* (what the host shows in a list). The name can be the
full parameter name; the label has to fit.

The generator therefore produces two strings per parameter and applies a
fixed **truncation ladder** to the label:

1. **Use the full name** when it fits the label budget.
2. **Drop a trailing qualifier**: the last word, if the name has more than
   one word and the result fits.
3. **Use an abbreviation** from a small per-parameter table.
4. **Truncate** to the budget and mark it with an ellipsis.

The budget is a single generator constant (`LABEL_BUDGET = 12` in
`scripts/aax_tables.py`). The ladder, applied to the real De-Click names
with a twelve-character budget — **the first step that produces a label
within the budget wins**, and the label in the last column is the one the
package publishes:

| Full name            | Step 1 (the full name) | Step 2 (no trailing word) | Step 3 (the abbreviation) | The published label |
|----------------------|------------------------|----------------------------|----------------------------|---------------------|
| `Algorithm`          | `Algorithm` (9) — fits | —                          | —                          | `Algorithm`         |
| `Sensitivity`        | `Sensitivity` (11) — fits | —                       | —                          | `Sensitivity`       |
| `Frequency Skew`     | 14 — no                | `Frequency` (9) — fits     | —                          | `Frequency`         |
| `Click Widening`     | 14 — no                | `Click` (5) — fits         | —                          | `Click`             |
| `Output Clicks Only` | 17 — no                | `Output Clicks` (13) — no  | `Clicks Only` (11) — fits  | `Clicks Only`       |

Step 4 (truncate to the budget and mark it with an ellipsis) is never
reached today: **twelve** parameter names get that far, and all twelve have
an entry in the abbreviation table, so **no published label ends in an
ellipsis** (measured over the 253 controls of the package,
`docs/AAX_PAGE_TABLES.md`).

Two rules make this safe:

* the ladder is **deterministic**, so the same parameter always gets the
  same label, on every machine and in every build;
* the **full name is never lost** — it is what the host shows in a list, so
  a truncated label never hides what a parameter is.

The abbreviation table is the only hand-maintained text in this area. It
lives next to the generator, not next to the modules, and it is keyed by
parameter name, so a module that uses a shared parameter name inherits the
same abbreviation everywhere.

---

## 5. What the adapter does with the core

The AAX adapter talks to the DSP core through the same C ABI as the VST3
adapter (`plugins/vst3/aurcore_c_api.h`), and with the same rules:

* the core is addressed by parameter **name**;
* values are in **natural units** (dB, Hz, ms, %), never normalised;
* an enum parameter carries its **choice string**, not an index.

Two more answers the adapter will need come from the same boundary, and
both are already there: the **latency** of the chain
(`aur_engine_latency_samples`) and its **tail** (`aur_engine_tail_samples`).
A module declares its own tail, the chain takes the longest of them, and a
module that makes a sound of its own — a generator, a bed of ambience —
never goes quiet and answers with the infinite tail the format uses.  The
adapter hands both to the host as they are.  The **rate** the chain's
output runs at (`aur_engine_output_sample_rate`) is there as well: a chain
that changes the rate — the resampler — runs at another rate than the
host's, and the core converts the output back.

The chain holds exactly one module — the one named by the stamped identity
record — so a session restores identically in every format.

---

## 6. What is still to be done

| Step | Depends on | Notes |
|------|------------|-------|
| Page table generator | — | **done**: `scripts/aax_tables.py` reads `params.tsv` and emits the tables above and the format's own page table document (§3.4). `python scripts\aax_tables.py --check` is the drift gate. |
| SDK glue | the format's SDK | **done**: the adapter is written against the SDK's interfaces, compiles against it, and registers the three channel variants of a module. |
| Effect id registration | the format's vendor | The four-character codes have to be registered with the format's vendor before shipping. |
| Reserved id range | the format's SDK | **settled**: the format reserves no numeric range. Its own control identifiers are strings (`"PreviewID"`, `"MasterBypassID"`), and the SDK's reserved-prefix rule is a text prefix, so the base `0x0100` cannot collide with them. |
| Page table schema | the format's SDK | **settled**: the page table is a resource document in the plug-in bundle — `PageTables` / `PageTableLayouts` / `PTLayout` / `PageTable` / `Page` / `Cell`, keyed by the plugin triad — and the plug-in declares it to the host (§3.4). **Done**: the generator writes it and the build copies it into the bundle. |

The identifiers, the parameter ids and the page layout do **not** depend on
any of that, which is why they are generated and checked in this repository.

## 7. What the package measures today

Measured with `python scripts\aax_tables.py --check` and
`python -m pytest tests\test_aax_application.py -q`:

| Item | Count |
|---|---:|
| Modules | 50 |
| Page tables (one per module) | 50 |
| Page table documents in the format's schema (one per module) | 50 |
| Pages | 131 |
| Parameters | 293 |
| Switches (discrete controls) | 97 |
| Knobs (continuous controls) | 196 |
| Effect identifiers (50 × 3) | 150 |
| Published labels that end in an ellipsis | 5 |
| Parameter names that reach step 3 of the label ladder | 5 |

The drift check is green: the committed package
(`docs/AAX_PAGE_TABLES.md`, `docs/aax_page_tables.json`) and the committed
page table documents (`plugins/aax/resources/`) are byte-identical to a fresh
run of the generator, so the tables above are the tables the generator writes
and not a transcription of them.  The document **inside every built bundle**
is checked the same way, by `python scripts\build_aax.py check`.
