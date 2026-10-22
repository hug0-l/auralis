# AAX page tables — the generated application package

<!--
     GENERATED FILE, DO NOT EDIT.
     Generator:       scripts/aax_tables.py
     Source of truth: aurcore/schema/params.tsv
     Source sha256:   088833e9a2cd0673584478488601e9ad7363a40017d11f71782f0331c6ac6bb1
     Regenerate with: python scripts/aax_tables.py
     Verify with:     python scripts/aax_tables.py --check
-->

This is the **application package** of the AAX plug-in family: the data that describes the plug-in to the host — the effect identifiers, the parameter identifiers, the page layout, the labels, the ranges, the defaults and the category.

It is **generated** from the canonical parameter table (`aurcore/schema/params.tsv`) and it is **complete**: every module of the registry has an effect id, every parameter of every module has an identifier and a place on exactly one page.

It is **not** the format's page table. The format's page table is a resource document in the plug-in bundle, in the format's own schema; this package is the part that does not need the SDK, and the generator writes that document as well, into `plugins/aax/resources/`, which the build stages into the bundle. See `plugins/aax/README.md` §6.

## Totals

| Item | Count |
|---|---:|
| Modules | 51 |
| Pages | 132 |
| Parameters | 295 |
| Switches (discrete controls) | 97 |
| Knobs (continuous controls) | 198 |
| Effect identifiers | 153 |

## The rules the package encodes

| Rule | Value |
|---|---|
| Manufacturer id | `AURA` |
| Plug ids (channel configurations) | `MONO`, `M2ST`, `STER` |
| Parameter id base | `256` (added by the SDK-facing step) |
| Label budget | 12 characters |
| Page geometry | one `switch` slot and 3 knob slots |

A parameter's identifier is the one the generated registry publishes — the position of the parameter inside its own module.  It is the number the VST3 adapter carries in the identity record, so a session written by one format means the same thing in another.  A plug-in implements exactly one module, so the identifiers are unique within the plug-in, and adding or removing a module never renumbers another one.

## Modules

### 0. De-Click — `AUR_DECLICK`

| | |
|---|---|
| Category | Repair |
| Product id | `DECL` |
| Effect ids | `AURA:DECL:MONO` · `AURA:DECL:M2ST` · `AURA:DECL:STER` |
| Parameters | 6 (2 switches, 4 knobs) |
| Pages | 2 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 0 | enum | Algorithm | Algorithm | 4 choices | Single Band |  | Single Band, MB Periodic, MB Random, Low Latency |
| knob1 | 1 | float | Sensitivity | Sensitivity | 0 .. 10 | 3.0 |  |  |
| knob2 | 2 | float | Frequency Skew | Frequency | -1 .. 1 | 0.0 |  |  |
| knob3 | 3 | float | Click Widening | Click | 0 .. 1 | 0.0 |  |  |

**Page 2**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 4 | bool | Output Clicks Only | Clicks Only | off / on | off |  |  |
| knob1 | 5 | float | Removed Mix | Removed Mix | 0 .. 100 | 0.0 | % |  |

### 1. De-Crackle — `AUR_DECRACKLE`

| | |
|---|---|
| Category | Repair |
| Product id | `DECR` |
| Effect ids | `AURA:DECR:MONO` · `AURA:DECR:M2ST` · `AURA:DECR:STER` |
| Parameters | 4 (2 switches, 2 knobs) |
| Pages | 2 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 0 | enum | Quality | Quality | 3 choices | Medium |  | Low, Medium, High |
| knob1 | 1 | float | Reduction | Reduction | 0 .. 1 | 0.5 |  |  |
| knob2 | 2 | float | Amplitude Skew | Amplitude | -1 .. 1 | 0.0 |  |  |

**Page 2**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 3 | bool | Output Crackle Only | Crackle Only | off / on | off |  |  |

### 2. De-Clip — `AUR_DECLIP`

| | |
|---|---|
| Category | Repair |
| Product id | `DECS` |
| Effect ids | `AURA:DECS:MONO` · `AURA:DECS:M2ST` · `AURA:DECS:STER` |
| Parameters | 5 (3 switches, 2 knobs) |
| Pages | 3 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 1 | bool | Link Threshold | Link | off / on | on |  |  |
| knob1 | 0 | float | Threshold | Threshold | -6 .. 0 | -0.1 | dB |  |

**Page 2**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 2 | enum | Quality | Quality | 3 choices | Medium |  | Low, Medium, High |
| knob1 | 3 | float | Makeup Gain | Makeup Gain | -12 .. 24 | 0.0 | dB |  |

**Page 3**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 4 | bool | Post Limiter | Post Limiter | off / on | off |  |  |

### 3. De-Hum — `AUR_DEHUM`

| | |
|---|---|
| Category | Repair |
| Product id | `DEHU` |
| Effect ids | `AURA:DEHU:MONO` · `AURA:DEHU:M2ST` · `AURA:DEHU:STER` |
| Parameters | 20 (9 switches, 11 knobs) |
| Pages | 9 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 0 | enum | Mode | Mode | 2 choices | Static |  | Static, Dynamic |
| knob1 | 1 | float | Base Frequency | Base | 20 .. 500 | 50.0 | Hz |  |
| knob2 | 2 | float | Filter Q | Filter Q | 0.1 .. 30 | 1.0 |  |  |
| knob3 | 3 | int | Number of Harmonics | Number of | 0 .. 16 | 8 |  |  |

**Page 2**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 4 | enum | Link Harmonics | Link | 3 choices | All |  | All, Odd/Even, None |
| knob1 | 5 | float | Harmonic Slope | Harmonic | -12 .. 12 | 0.0 | dB |  |

**Page 3**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 6 | bool | Enable Learning | Enable | off / on | off |  |  |

**Page 4**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 7 | bool | Adaptive | Adaptive | off / on | on |  |  |

**Page 5**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 8 | bool | Enable Highpass | Enable | off / on | off |  |  |
| knob1 | 9 | float | Highpass Frequency | Highpass | 20 .. 500 | 20.0 | Hz |  |

**Page 6**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 10 | bool | Enable Lowpass | Enable | off / on | off |  |  |
| knob1 | 11 | float | Lowpass Frequency | Lowpass | 500 .. 22050 | 20000.0 | Hz |  |

**Page 7**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 12 | bool | Linear Phase Filters | Linear Phase | off / on | off |  |  |

**Page 8**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 13 | bool | Enable DC Offset | Enable DC | off / on | off |  |  |
| knob1 | 14 | float | Dynamic Sensitivity | Dynamic | 0 .. 1 | 0.5 |  |  |
| knob2 | 15 | int | Dynamic Bands | Dynamic | 1 .. 12 | 3 |  |  |
| knob3 | 16 | float | Dynamic Low Cutoff | Dynamic Low | 20 .. 500 | 20.0 | Hz |  |

**Page 9**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 18 | bool | Output Hum Only | Output Hum | off / on | off |  |  |
| knob1 | 17 | float | Dynamic High Cutoff | Dynamic High | 500 .. 22050 | 20000.0 | Hz |  |
| knob2 | 19 | float | Removed Mix | Removed Mix | 0 .. 100 | 0.0 | % |  |

### 4. De-Ess — `AUR_DEESS`

| | |
|---|---|
| Category | Repair |
| Product id | `DEES` |
| Effect ids | `AURA:DEES:MONO` · `AURA:DEES:M2ST` · `AURA:DEES:STER` |
| Parameters | 8 (4 switches, 4 knobs) |
| Pages | 4 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 0 | enum | Mode | Mode | 2 choices | Classic |  | Classic, Spectral |

**Page 2**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 6 | enum | Speed | Speed | 2 choices | Fast |  | Fast, Slow |
| knob1 | 1 | float | Threshold | Threshold | -60 .. 0 | -30.0 | dB |  |

**Page 3**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 2 | bool | Absolute Mode | Absolute | off / on | off |  |  |
| knob1 | 3 | float | Cutoff Frequency | Cutoff | 1000 .. 16000 | 6000.0 | Hz |  |
| knob2 | 4 | float | Spectral Flattening | Spectral | 0 .. 1 | 0.5 |  |  |
| knob3 | 5 | float | Spectral Tilt | Spectral | -1 .. 1 | 0.0 |  |  |

**Page 4**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 7 | bool | Output Ess Only | Output Ess | off / on | off |  |  |

### 5. De-Plosive — `AUR_DEPLOSIVE`

| | |
|---|---|
| Category | Repair |
| Product id | `DEPL` |
| Effect ids | `AURA:DEPL:MONO` · `AURA:DEPL:M2ST` · `AURA:DEPL:STER` |
| Parameters | 3 (0 switches, 3 knobs) |
| Pages | 1 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| knob1 | 0 | float | Sensitivity | Sensitivity | 0 .. 10 | 3.0 |  |  |
| knob2 | 1 | float | Reduction | Reduction | 0 .. 10 | 2.5 |  |  |
| knob3 | 2 | float | Frequency Limit | Frequency | 20 .. 500 | 300.0 | Hz |  |

### 6. De-Reverb — `AUR_DEREVERB`

| | |
|---|---|
| Category | Repair |
| Product id | `DERE` |
| Effect ids | `AURA:DERE:MONO` · `AURA:DERE:M2ST` · `AURA:DERE:STER` |
| Parameters | 10 (2 switches, 8 knobs) |
| Pages | 4 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| knob1 | 0 | float | Reduction | Reduction | 0 .. 1 | 0.5 |  |  |
| knob2 | 1 | float | Tail Length | Tail Length | 0.1 .. 10 | 1.0 | s |  |
| knob3 | 2 | float | Artifact Smoothing | Artifact | 0 .. 1 | 0.5 |  |  |

**Page 2**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| knob1 | 3 | float | Band Reduction Low | Band Reduct… | 0 .. 1 | 1.0 |  |  |
| knob2 | 4 | float | Band Reduction Low-Mid | Band Reduct… | 0 .. 1 | 1.0 |  |  |
| knob3 | 5 | float | Band Reduction High-Mid | Band Reduct… | 0 .. 1 | 1.0 |  |  |

**Page 3**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 7 | bool | Enhance Dry Signal | Enhance Dry | off / on | off |  |  |
| knob1 | 6 | float | Band Reduction High | Band Reduct… | 0 .. 1 | 1.0 |  |  |
| knob2 | 9 | float | Removed Mix | Removed Mix | 0 .. 100 | 0.0 | % |  |

**Page 4**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 8 | bool | Output Reverb Only | Reverb Only | off / on | off |  |  |

### 7. De-Rustle — `AUR_DERUSTLE`

| | |
|---|---|
| Category | Repair |
| Product id | `DERU` |
| Effect ids | `AURA:DERU:MONO` · `AURA:DERU:M2ST` · `AURA:DERU:STER` |
| Parameters | 3 (1 switches, 2 knobs) |
| Pages | 1 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 2 | enum | Separation Algorithm | Separation | 3 choices | Channel Independent |  | Channel Independent, Joint, Advanced Joint |
| knob1 | 0 | float | Reduction | Reduction | 0 .. 1 | 0.5 |  |  |
| knob2 | 1 | float | Ambience Preservation | Ambience | 0 .. 1 | 0.5 |  |  |

### 8. De-Wind — `AUR_DEWIND`

| | |
|---|---|
| Category | Repair |
| Product id | `DEWI` |
| Effect ids | `AURA:DEWI:MONO` · `AURA:DEWI:M2ST` · `AURA:DEWI:STER` |
| Parameters | 5 (0 switches, 5 knobs) |
| Pages | 2 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| knob1 | 0 | float | Reduction | Reduction | 0 .. 1 | 0.5 |  |  |
| knob2 | 1 | float | Crossover Frequency | Crossover | 50 .. 2000 | 500.0 | Hz |  |
| knob3 | 2 | float | Fundamental Recovery | Fundamental | 0 .. 1 | 0.5 |  |  |

**Page 2**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| knob1 | 3 | float | Artifact Smoothing | Artifact | 0 .. 1 | 0.5 |  |  |
| knob2 | 4 | float | Removed Mix | Removed Mix | 0 .. 100 | 0.0 | % |  |

### 9. De-Bleed — `AUR_DEBLEED`

| | |
|---|---|
| Category | Repair |
| Product id | `DEBL` |
| Effect ids | `AURA:DEBL:MONO` · `AURA:DEBL:M2ST` · `AURA:DEBL:STER` |
| Parameters | 2 (0 switches, 2 knobs) |
| Pages | 1 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| knob1 | 0 | float | Reduction | Reduction | 0 .. 1 | 0.5 |  |  |
| knob2 | 1 | float | Artifact Smoothing | Artifact | 0 .. 1 | 0.5 |  |  |

### 10. Breath Control — `AUR_BREATH`

| | |
|---|---|
| Category | Repair |
| Product id | `BREA` |
| Effect ids | `AURA:BREA:MONO` · `AURA:BREA:M2ST` · `AURA:BREA:STER` |
| Parameters | 6 (2 switches, 4 knobs) |
| Pages | 2 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 0 | enum | Mode | Mode | 2 choices | Gain Mode |  | Gain Mode, Target Mode |
| knob1 | 1 | float | Gain | Gain | -24 .. 0 | -9.0 | dB |  |
| knob2 | 2 | float | Target Level | Target Level | -60 .. 0 | -30.0 | dBFS |  |
| knob3 | 3 | float | Sensitivity | Sensitivity | 0 .. 1 | 0.5 |  |  |

**Page 2**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 4 | bool | Output Breaths Only | Breaths Only | off / on | off |  |  |
| knob1 | 5 | float | Removed Mix | Removed Mix | 0 .. 100 | 0.0 | % |  |

### 11. Mouth De-Click — `AUR_MOUTHDECLICK`

| | |
|---|---|
| Category | Repair |
| Product id | `MOUT` |
| Effect ids | `AURA:MOUT:MONO` · `AURA:MOUT:M2ST` · `AURA:MOUT:STER` |
| Parameters | 4 (1 switches, 3 knobs) |
| Pages | 1 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 3 | bool | Output Clicks Only | Clicks Only | off / on | off |  |  |
| knob1 | 0 | float | Sensitivity | Sensitivity | 0 .. 10 | 3.0 |  |  |
| knob2 | 1 | float | Frequency Skew | Frequency | -1 .. 1 | 0.0 |  |  |
| knob3 | 2 | float | Click Widening | Click | 0 .. 1 | 0.0 |  |  |

### 12. Voice De-Noise — `AUR_VOICEDENOISE`

| | |
|---|---|
| Category | Repair |
| Product id | `VOIC` |
| Effect ids | `AURA:VOIC:MONO` · `AURA:VOIC:M2ST` · `AURA:VOIC:STER` |
| Parameters | 14 (5 switches, 9 knobs) |
| Pages | 6 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 0 | enum | Processing Mode | Processing | 2 choices | realtime |  | realtime, offline |

**Page 2**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 1 | enum | Optimize For | Optimize For | 2 choices | Dialogue |  | Dialogue, Music |

**Page 3**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 2 | enum | Filter Type | Filter Type | 2 choices | Surgical |  | Surgical, Gentle |

**Page 4**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 3 | bool | Adaptive Mode | Adaptive | off / on | on |  |  |
| knob1 | 4 | float | Threshold | Threshold | -90 .. 0 | -15.0 | dB |  |
| knob2 | 5 | float | Threshold 1 | Threshold 1 | -90 .. 0 | -40.0 | dB |  |
| knob3 | 6 | float | Threshold 2 | Threshold 2 | -90 .. 0 | -50.0 | dB |  |

**Page 5**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| knob1 | 7 | float | Threshold 3 | Threshold 3 | -90 .. 0 | -60.0 | dB |  |
| knob2 | 8 | float | Threshold 4 | Threshold 4 | -90 .. 0 | -70.0 | dB |  |
| knob3 | 9 | float | Threshold 5 | Threshold 5 | -90 .. 0 | -80.0 | dB |  |

**Page 6**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 12 | bool | Learn | Learn | off / on | off |  |  |
| knob1 | 10 | float | Threshold 6 | Threshold 6 | -90 .. 0 | -90.0 | dB |  |
| knob2 | 11 | float | Reduction | Reduction | 0 .. 30 | 18.0 | dB |  |
| knob3 | 13 | float | Removed Mix | Removed Mix | 0 .. 100 | 0.0 | % |  |

### 13. Spectral De-Noise — `AUR_SPECTRALDENOISE`

| | |
|---|---|
| Category | Repair |
| Product id | `SPEC` |
| Effect ids | `AURA:SPEC:MONO` · `AURA:SPEC:M2ST` · `AURA:SPEC:STER` |
| Parameters | 20 (6 switches, 14 knobs) |
| Pages | 8 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 0 | enum | Processing Mode | Processing | 2 choices | realtime |  | realtime, offline |

**Page 2**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 1 | enum | Algorithm | Algorithm | 3 choices | Advanced |  | Simple, Advanced, Extreme |

**Page 3**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 2 | enum | Quality | Quality | 4 choices | B |  | A, B, C, D |
| knob1 | 3 | float | FFT Size | FFT Size | 5 .. 60 | 20.0 | ms |  |

**Page 4**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 4 | bool | Multi Resolution | Multi | off / on | on |  |  |
| knob1 | 5 | float | Learning Time | Learning | 0.5 .. 30 | 2.0 | s |  |

**Page 5**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 6 | bool | Adaptive | Adaptive | off / on | on |  |  |
| knob1 | 7 | float | Noise Threshold | Noise | -40 .. 20 | 0.0 | dB |  |
| knob2 | 8 | float | Tonal Threshold | Tonal | -40 .. 20 | 0.0 | dB |  |
| knob3 | 9 | float | Noise Reduction | Noise | 0 .. 40 | 15.0 | dB |  |

**Page 6**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| knob1 | 10 | float | Tonal Reduction | Tonal | 0 .. 40 | 12.0 | dB |  |
| knob2 | 11 | float | Artifact Control | Artifact | 0 .. 1 | 0.5 |  |  |
| knob3 | 12 | float | Smoothing | Smoothing | 0 .. 1 | 0.5 |  |  |

**Page 7**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| knob1 | 13 | float | Knee | Knee | 0 .. 1 | 0.5 |  |  |
| knob2 | 14 | float | Release | Release | 10 .. 1000 | 100.0 | ms |  |
| knob3 | 15 | float | Noise Floor Synthesis | Noise Floor | 0 .. 1 | 0.5 |  |  |

**Page 8**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 19 | bool | Output Noise Only | Output Noise | off / on | off |  |  |
| knob1 | 16 | float | Masking | Masking | 0 .. 1 | 0.5 |  |  |
| knob2 | 17 | float | Enhancement | Enhancement | 0 .. 1 | 0.5 |  |  |
| knob3 | 18 | float | Whitening | Whitening | 0 .. 1 | 0.5 |  |  |

### 14. Spectral Repair — `AUR_SPECTRALREPAIR`

| | |
|---|---|
| Category | Repair |
| Product id | `SPEY` |
| Effect ids | `AURA:SPEY:MONO` · `AURA:SPEY:M2ST` · `AURA:SPEY:STER` |
| Parameters | 7 (3 switches, 4 knobs) |
| Pages | 3 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 0 | enum | Mode | Mode | 5 choices | Attenuate |  | Attenuate, Replace, Gain, Pattern, Partials |
| knob1 | 1 | float | Reduction | Reduction | 0 .. 1 | 0.5 |  |  |
| knob2 | 2 | float | Region Length | Region | 0.02 .. 10 | 0.25 | s |  |

**Page 2**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 3 | enum | Direction | Direction | 3 choices | 2D |  | Horizontal, Vertical, 2D |

**Page 3**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 4 | bool | Multi Resolution | Multi | off / on | on |  |  |
| knob1 | 5 | float | Before Weighting | Before | 0 .. 1 | 0.5 |  |  |
| knob2 | 6 | float | After Weighting | After | 0 .. 1 | 0.5 |  |  |

### 15. Spectral Recovery — `AUR_SPECTRALRECOVERY`

| | |
|---|---|
| Category | Repair |
| Product id | `SPEB` |
| Effect ids | `AURA:SPEB:MONO` · `AURA:SPEB:M2ST` · `AURA:SPEB:STER` |
| Parameters | 5 (1 switches, 4 knobs) |
| Pages | 2 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 0 | bool | Spectral Patching | Spectral | off / on | on |  |  |
| knob1 | 1 | float | Low Gain | Low Gain | -60 .. 12 | -20.0 | dB |  |
| knob2 | 2 | float | Low Cutoff | Low Cutoff | 20 .. 2000 | 200.0 | Hz |  |
| knob3 | 3 | float | High Cutoff | High Cutoff | 2000 .. 16000 | 4000.0 | Hz |  |

**Page 2**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| knob1 | 4 | float | High Gain | High Gain | -12 .. 2.4 | 0.0 | dB |  |

### 16. Guitar De-Noise — `AUR_GUITARDENOISE`

| | |
|---|---|
| Category | Repair |
| Product id | `GUIT` |
| Effect ids | `AURA:GUIT:MONO` · `AURA:GUIT:M2ST` · `AURA:GUIT:STER` |
| Parameters | 8 (1 switches, 7 knobs) |
| Pages | 3 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| knob1 | 0 | float | Amp Sensitivity | Amp | 0 .. 1 | 0.5 |  |  |
| knob2 | 1 | int | Amp Resolution | Amp | 16 .. 512 | 128 |  |  |
| knob3 | 2 | float | Squeak Sensitivity | Squeak | 0 .. 1 | 0.5 |  |  |

**Page 2**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 4 | enum | Squeak Duration | Squeak | 2 choices | Short |  | Short, Long |
| knob1 | 3 | float | Squeak Reduction | Squeak | 0 .. 1 | 0.5 |  |  |
| knob2 | 5 | float | Pick Sensitivity | Pick | 0 .. 1 | 0.5 |  |  |
| knob3 | 6 | float | Pick Reduction | Pick | 0 .. 1 | 0.5 |  |  |

**Page 3**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| knob1 | 7 | float | Pick Attack | Pick Attack | 0 .. 1 | 0.5 |  |  |

### 17. Deconstruct — `AUR_DECONSTRUCT`

| | |
|---|---|
| Category | Repair |
| Product id | `DECO` |
| Effect ids | `AURA:DECO:MONO` · `AURA:DECO:M2ST` · `AURA:DECO:STER` |
| Parameters | 6 (2 switches, 4 knobs) |
| Pages | 2 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 2 | bool | Separate Transients | Separate | off / on | on |  |  |
| knob1 | 0 | float | Tonal Gain | Tonal Gain | -12 .. 2.4 | 0.0 | dB |  |
| knob2 | 1 | float | Noisy Gain | Noisy Gain | -12 .. 2.4 | 0.0 | dB |  |
| knob3 | 3 | float | Transient Gain | Transient | -12 .. 2.4 | 0.0 | dB |  |

**Page 2**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 4 | enum | Balance | Balance | 2 choices | Noisy |  | Noisy, Tonal |
| knob1 | 5 | float | Artifact Smoothing | Artifact | 0 .. 1 | 0.5 |  |  |

### 18. Dialogue Isolate — `AUR_DIALOGUEISOLATE`

| | |
|---|---|
| Category | Repair |
| Product id | `DIAL` |
| Effect ids | `AURA:DIAL:MONO` · `AURA:DIAL:M2ST` · `AURA:DIAL:STER` |
| Parameters | 5 (1 switches, 4 knobs) |
| Pages | 2 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| knob1 | 0 | float | Voice Gain | Voice Gain | -24 .. 4.8 | 0.0 | dB |  |
| knob2 | 1 | float | Reverb Gain | Reverb Gain | -24 .. 4.8 | 0.0 | dB |  |
| knob3 | 2 | float | Noise Gain | Noise Gain | -24 .. 4.8 | 0.0 | dB |  |

**Page 2**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 4 | enum | Quality | Quality | 2 choices | Good |  | Good, Best |
| knob1 | 3 | float | Sensitivity | Sensitivity | 0 .. 1 | 0.5 |  |  |

### 19. Music Rebalance — `AUR_MUSICREBALANCE`

| | |
|---|---|
| Category | Repair |
| Product id | `MUSI` |
| Effect ids | `AURA:MUSI:MONO` · `AURA:MUSI:M2ST` · `AURA:MUSI:STER` |
| Parameters | 9 (5 switches, 4 knobs) |
| Pages | 6 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| knob1 | 0 | float | Vocal Gain | Vocal Gain | -24 .. 4.8 | 0.0 | dB |  |
| knob2 | 1 | float | Bass Gain | Bass Gain | -24 .. 4.8 | 0.0 | dB |  |
| knob3 | 2 | float | Drums Gain | Drums Gain | -24 .. 4.8 | 0.0 | dB |  |

**Page 2**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 4 | bool | Vocal Mute | Vocal Mute | off / on | off |  |  |
| knob1 | 3 | float | Other Gain | Other Gain | -24 .. 4.8 | 0.0 | dB |  |

**Page 3**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 5 | bool | Bass Mute | Bass Mute | off / on | off |  |  |

**Page 4**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 6 | bool | Drums Mute | Drums Mute | off / on | off |  |  |

**Page 5**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 7 | bool | Other Mute | Other Mute | off / on | off |  |  |

**Page 6**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 8 | enum | Quality | Quality | 3 choices | Good |  | Good, Better, Best |

### 20. Ambience Match — `AUR_AMBIENCEMATCH`

| | |
|---|---|
| Category | Repair |
| Product id | `AMBI` |
| Effect ids | `AURA:AMBI:MONO` · `AURA:AMBI:M2ST` · `AURA:AMBI:STER` |
| Parameters | 6 (2 switches, 4 knobs) |
| Pages | 2 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 0 | enum | Ambience Type | Ambience | 2 choices | Complex |  | Complex, Static |
| knob1 | 1 | float | Ambience Threshold | Ambience | -100 .. 0 | -60.0 | dB |  |
| knob2 | 2 | float | Movement | Movement | 0 .. 1 | 0.5 |  |  |
| knob3 | 3 | float | Randomness | Randomness | 0 .. 1 | 0.5 |  |  |

**Page 2**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 5 | bool | Output Ambience Only | Amb Only | off / on | off |  |  |
| knob1 | 4 | float | Gain | Gain | -24 .. 4.8 | 0.0 | dB |  |

### 21. Repair Assistant — `AUR_REPAIRASSISTANT`

| | |
|---|---|
| Category | Repair |
| Product id | `REPA` |
| Effect ids | `AURA:REPA:MONO` · `AURA:REPA:M2ST` · `AURA:REPA:STER` |
| Parameters | 8 (2 switches, 6 knobs) |
| Pages | 3 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 0 | enum | Processing Mode | Processing | 2 choices | realtime |  | realtime, offline |

**Page 2**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 1 | enum | Mode | Mode | 2 choices | Voice |  | Voice, Music |
| knob1 | 2 | float | Noise Reduction | Noise | 0 .. 1 | 0.0 |  |  |
| knob2 | 3 | float | Reverb Reduction | Reverb | 0 .. 1 | 0.0 |  |  |
| knob3 | 4 | float | Hum Reduction | Hum | 0 .. 1 | 0.0 |  |  |

**Page 3**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| knob1 | 5 | float | Click Reduction | Click | 0 .. 1 | 0.0 |  |  |
| knob2 | 6 | float | De-Ess Reduction | De-Ess | 0 .. 1 | 0.0 |  |  |
| knob3 | 7 | float | De-Clip Reduction | De-Clip | 0 .. 1 | 0.0 |  |  |

### 22. Leveler — `AUR_LEVELER`

| | |
|---|---|
| Category | Utility |
| Product id | `LEVE` |
| Effect ids | `AURA:LEVE:MONO` · `AURA:LEVE:M2ST` · `AURA:LEVE:STER` |
| Parameters | 6 (1 switches, 5 knobs) |
| Pages | 2 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 0 | enum | Optimize For | Optimize For | 2 choices | Dialogue |  | Dialogue, Music |
| knob1 | 1 | float | Target Level | Target Level | -40 .. 0 | -23.0 | LUFS |  |
| knob2 | 2 | float | Responsiveness | Response | 0 .. 1 | 0.5 |  |  |
| knob3 | 3 | float | Preserve Dynamics | Preserve | 0 .. 1 | 0.5 |  |  |

**Page 2**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| knob1 | 4 | float | Ess Reduction | Ess | 0 .. 12 | 0.0 | dB |  |
| knob2 | 5 | float | Limiter | Limiter | -12 .. 0 | -1.0 | dBTP |  |

### 23. Loudness Control — `AUR_LOUDNESS`

| | |
|---|---|
| Category | Utility |
| Product id | `LOUD` |
| Effect ids | `AURA:LOUD:MONO` · `AURA:LOUD:M2ST` · `AURA:LOUD:STER` |
| Parameters | 6 (1 switches, 5 knobs) |
| Pages | 2 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| knob1 | 0 | float | Target Loudness | Target | -36 .. 0 | -23.0 | LUFS |  |
| knob2 | 1 | float | Tolerance | Tolerance | 0.5 .. 2 | 0.5 | LU |  |
| knob3 | 2 | float | Short Term Loudness | Short Term | -36 .. 0 | -23.0 | LUFS |  |

**Page 2**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 5 | enum | Standard | Standard | 10 choices | EBU R128 |  | EBU R128, EBU R128 s1, BS.1770-2, BS.1770-4, ATSC A/85, ARIB TR-B32, AES TD1006.1.17-10, OP-59, Portaria 354, Custom |
| knob1 | 3 | float | Momentary Loudness | Momentary | -36 .. 0 | -23.0 | LUFS |  |
| knob2 | 4 | float | True Peak | True Peak | -9 .. 0 | -1.0 | dBTP |  |

### 24. Loudness Optimize — `AUR_LOUDNESSOPT`

| | |
|---|---|
| Category | Utility |
| Product id | `LOUV` |
| Effect ids | `AURA:LOUV:MONO` · `AURA:LOUV:M2ST` · `AURA:LOUV:STER` |
| Parameters | 4 (0 switches, 4 knobs) |
| Pages | 2 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| knob1 | 0 | float | Boost | Boost | 0 .. 12 | 0.0 | dB |  |
| knob2 | 1 | float | Threshold | Threshold | -30 .. 0 | -12.0 | dBFS |  |
| knob3 | 2 | float | Ratio | Ratio | 1 .. 20 | 4.0 |  |  |

**Page 2**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| knob1 | 3 | float | Speed | Speed | 10 .. 1000 | 120.0 | ms |  |

### 25. EQ — `AUR_EQ`

| | |
|---|---|
| Category | Utility |
| Product id | `EQ00` |
| Effect ids | `AURA:EQ00:MONO` · `AURA:EQ00:M2ST` · `AURA:EQ00:STER` |
| Parameters | 14 (1 switches, 13 knobs) |
| Pages | 5 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 0 | enum | Type | Type | 2 choices | Analog IIR |  | Analog IIR, Digital Linear Phase FIR |
| knob1 | 1 | float | Band 1 Frequency | Band 1 | 20 .. 20000 | 100.0 | Hz |  |
| knob2 | 2 | float | Band 1 Gain | Band 1 Gain | -18 .. 3.6 | 0.0 | dB |  |
| knob3 | 3 | float | Band 1 Q | Band 1 Q | 0.1 .. 20 | 1.0 |  |  |

**Page 2**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| knob1 | 4 | float | Band 2 Frequency | Band 2 | 20 .. 20000 | 1000.0 | Hz |  |
| knob2 | 5 | float | Band 2 Gain | Band 2 Gain | -18 .. 3.6 | 0.0 | dB |  |
| knob3 | 6 | float | Band 2 Q | Band 2 Q | 0.1 .. 20 | 1.0 |  |  |

**Page 3**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| knob1 | 7 | float | Band 3 Frequency | Band 3 | 20 .. 20000 | 5000.0 | Hz |  |
| knob2 | 8 | float | Band 3 Gain | Band 3 Gain | -18 .. 3.6 | 0.0 | dB |  |
| knob3 | 9 | float | Band 3 Q | Band 3 Q | 0.1 .. 20 | 1.0 |  |  |

**Page 4**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| knob1 | 10 | float | Band 4 Frequency | Band 4 | 20 .. 20000 | 10000.0 | Hz |  |
| knob2 | 11 | float | Band 4 Gain | Band 4 Gain | -18 .. 3.6 | 0.0 | dB |  |
| knob3 | 12 | float | Band 4 Q | Band 4 Q | 0.1 .. 20 | 1.0 |  |  |

**Page 5**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| knob1 | 13 | float | Output Gain | Output Gain | -18 .. 3.6 | 0.0 | dB |  |

### 26. EQ Match — `AUR_EQMATCH`

| | |
|---|---|
| Category | Utility |
| Product id | `EQMA` |
| Effect ids | `AURA:EQMA:MONO` · `AURA:EQMA:M2ST` · `AURA:EQMA:STER` |
| Parameters | 1 (0 switches, 1 knobs) |
| Pages | 1 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| knob1 | 0 | float | Amount | Amount | 0 .. 1 | 0.25 |  |  |

### 27. Gain — `AUR_GAIN`

| | |
|---|---|
| Category | Utility |
| Product id | `GAIN` |
| Effect ids | `AURA:GAIN:MONO` · `AURA:GAIN:M2ST` · `AURA:GAIN:STER` |
| Parameters | 1 (0 switches, 1 knobs) |
| Pages | 1 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| knob1 | 0 | float | Gain | Gain | -60 .. 12 | 0.0 | dB |  |

### 28. Fade — `AUR_FADE`

| | |
|---|---|
| Category | Utility |
| Product id | `FADE` |
| Effect ids | `AURA:FADE:MONO` · `AURA:FADE:M2ST` · `AURA:FADE:STER` |
| Parameters | 2 (1 switches, 1 knobs) |
| Pages | 1 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 0 | enum | Fade Type | Fade Type | 4 choices | Log |  | Log, Linear, Cosine, Equal Power |
| knob1 | 1 | float | Fade Length | Fade Length | 0.01 .. 30 | 1.0 | s |  |

### 29. Normalize — `AUR_NORMALIZE`

| | |
|---|---|
| Category | Utility |
| Product id | `NORM` |
| Effect ids | `AURA:NORM:MONO` · `AURA:NORM:M2ST` · `AURA:NORM:STER` |
| Parameters | 1 (0 switches, 1 knobs) |
| Pages | 1 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| knob1 | 0 | float | Target Peak Level | Target Peak | -24 .. 0 | -1.0 | dBFS |  |

### 30. Phase — `AUR_PHASE`

| | |
|---|---|
| Category | Utility |
| Product id | `PHAS` |
| Effect ids | `AURA:PHAS:MONO` · `AURA:PHAS:M2ST` · `AURA:PHAS:STER` |
| Parameters | 2 (1 switches, 1 knobs) |
| Pages | 1 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 1 | bool | Adaptive Rotation | Adaptive | off / on | off |  |  |
| knob1 | 0 | float | Rotation | Rotation | -180 .. 180 | 0.0 | deg |  |

### 31. Mixing — `AUR_MIXING`

| | |
|---|---|
| Category | Utility |
| Product id | `MIXI` |
| Effect ids | `AURA:MIXI:MONO` · `AURA:MIXI:M2ST` · `AURA:MIXI:STER` |
| Parameters | 2 (0 switches, 2 knobs) |
| Pages | 1 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| knob1 | 0 | float | Left Output Mix | Left Output | 0 .. 200 | 100.0 | % |  |
| knob2 | 1 | float | Right Output Mix | Right Output | 0 .. 200 | 100.0 | % |  |

### 32. Center Extract — `AUR_CENTEREXTRACT`

| | |
|---|---|
| Category | Repair |
| Product id | `CENT` |
| Effect ids | `AURA:CENT:MONO` · `AURA:CENT:M2ST` · `AURA:CENT:STER` |
| Parameters | 4 (1 switches, 3 knobs) |
| Pages | 1 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 0 | enum | Algorithm | Algorithm | 2 choices | True Phase |  | True Phase, Pseudo Pan |
| knob1 | 1 | float | Reduction | Reduction | 0 .. 1 | 1.0 |  |  |
| knob2 | 2 | float | Artifact Smoothing | Artifact | 0 .. 1 | 0.5 |  |  |
| knob3 | 3 | float | Dry Mix | Dry Mix | 0 .. 100 | 0.0 | % |  |

### 33. Azimuth — `AUR_AZIMUTH`

| | |
|---|---|
| Category | Repair |
| Product id | `AZIM` |
| Effect ids | `AURA:AZIM:MONO` · `AURA:AZIM:M2ST` · `AURA:AZIM:STER` |
| Parameters | 3 (1 switches, 2 knobs) |
| Pages | 1 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 2 | bool | Adaptive Matching | Adaptive | off / on | off |  |  |
| knob1 | 0 | float | Gain | Gain | -12 .. 12 | 0.0 | dB |  |
| knob2 | 1 | float | Delay | Delay | -10 .. 10 | 0.0 | ms |  |

### 34. Dither — `AUR_DITHER`

| | |
|---|---|
| Category | Utility |
| Product id | `DITH` |
| Effect ids | `AURA:DITH:MONO` · `AURA:DITH:M2ST` · `AURA:DITH:STER` |
| Parameters | 4 (4 switches, 0 knobs) |
| Pages | 4 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 0 | enum | Bit Depth | Bit Depth | 5 choices | 16 |  | 24, 20, 16, 12, 8 |

**Page 2**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 1 | enum | Noise Shaping | Noise | 7 choices | None |  | None, Shape 1, Shape 2, Shape 3, Shape 4, Shape 5, Ultra |

**Page 3**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 2 | enum | Dither Amount | Dither | 4 choices | Medium |  | None, Low, Medium, High |

**Page 4**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 3 | enum | Auto Blanking | Auto | 3 choices | If Quantized |  | If Quantized, On Silence, Off |

### 35. Resample — `AUR_RESAMPLE`

| | |
|---|---|
| Category | Utility |
| Product id | `RESA` |
| Effect ids | `AURA:RESA:MONO` · `AURA:RESA:M2ST` · `AURA:RESA:STER` |
| Parameters | 4 (2 switches, 2 knobs) |
| Pages | 2 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 0 | enum | Sample Rate | Sample Rate | 8 choices | 48000 |  | 22050, 32000, 44100, 48000, 88200, 96000, 176400, 192000 |

**Page 2**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 3 | bool | Post Limiter | Post Limiter | off / on | off |  |  |
| knob1 | 1 | float | Filter Steepness | Filter | 0 .. 1 | 0.5 |  |  |
| knob2 | 2 | float | Pre Ringing | Pre Ringing | 0 .. 1 | 0.0 |  |  |

### 36. Time & Pitch — `AUR_TIMEPITCH`

| | |
|---|---|
| Category | Utility |
| Product id | `TIME` |
| Effect ids | `AURA:TIME:MONO` · `AURA:TIME:M2ST` · `AURA:TIME:STER` |
| Parameters | 7 (2 switches, 5 knobs) |
| Pages | 2 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 0 | enum | Algorithm | Algorithm | 3 choices | Radius |  | Radius, Radius RT, Solo |
| knob1 | 1 | float | Stretch Ratio | Stretch | 0.125 .. 8 | 1.0 |  |  |
| knob2 | 2 | float | Pitch Shift | Pitch Shift | -24 .. 24 | 0.0 | st |  |

**Page 2**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 6 | bool | Shift Formants | Shift | off / on | off |  |  |
| knob1 | 3 | float | Adaptive Window | Adaptive | 10 .. 120 | 37.0 | ms |  |
| knob2 | 4 | float | Transient Sensitivity | Transient | 0 .. 2 | 1.0 |  |  |
| knob3 | 5 | float | Phase Coherence | Phase | 0 .. 1 | 0.5 |  |  |

### 37. Variable Pitch — `AUR_VARIPITCH`

| | |
|---|---|
| Category | Utility |
| Product id | `VARI` |
| Effect ids | `AURA:VARI:MONO` · `AURA:VARI:M2ST` · `AURA:VARI:STER` |
| Parameters | 4 (1 switches, 3 knobs) |
| Pages | 1 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 2 | bool | Preserve Time | Preserve | off / on | on |  |  |
| knob1 | 0 | float | Contour | Contour | -24 .. 24 | 0.0 | st |  |
| knob2 | 1 | float | Smoothing | Smoothing | 0 .. 1 | 0.5 |  |  |
| knob3 | 3 | float | Phase Coherence | Phase | 0 .. 1 | 0.5 |  |  |

### 38. Variable Time — `AUR_VARTIME`

| | |
|---|---|
| Category | Utility |
| Product id | `VART` |
| Effect ids | `AURA:VART:MONO` · `AURA:VART:M2ST` · `AURA:VART:STER` |
| Parameters | 3 (0 switches, 3 knobs) |
| Pages | 1 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| knob1 | 0 | float | Ratio | Ratio | 0.125 .. 8 | 1.0 |  |  |
| knob2 | 1 | float | Smoothing | Smoothing | 0 .. 1 | 0.5 |  |  |
| knob3 | 2 | float | Phase Coherence | Phase | 0 .. 1 | 0.5 |  |  |

### 39. Wow & Flutter — `AUR_WOWFLUTTER`

| | |
|---|---|
| Category | Repair |
| Product id | `WOWF` |
| Effect ids | `AURA:WOWF:MONO` · `AURA:WOWF:M2ST` · `AURA:WOWF:STER` |
| Parameters | 4 (0 switches, 4 knobs) |
| Pages | 2 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| knob1 | 0 | float | Wow | Wow | 0 .. 5 | 1.0 | Hz |  |
| knob2 | 1 | float | Flutter | Flutter | 8 .. 40 | 20.0 | Hz |  |
| knob3 | 2 | float | Sensitivity | Sensitivity | 0 .. 1 | 0.5 |  |  |

**Page 2**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| knob1 | 3 | float | Center Global Pitch | Center Pitch | 380 .. 500 | 440.0 | Hz |  |

### 40. Interpolate — `AUR_INTERPOLATE`

| | |
|---|---|
| Category | Utility |
| Product id | `INTE` |
| Effect ids | `AURA:INTE:MONO` · `AURA:INTE:M2ST` · `AURA:INTE:STER` |
| Parameters | 1 (0 switches, 1 knobs) |
| Pages | 1 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| knob1 | 0 | int | Quality | Quality | 1 .. 8 | 3 |  |  |

### 41. Signal Generator — `AUR_SIGNALGEN`

| | |
|---|---|
| Category | Utility |
| Product id | `SIGN` |
| Effect ids | `AURA:SIGN:MONO` · `AURA:SIGN:M2ST` · `AURA:SIGN:STER` |
| Parameters | 5 (2 switches, 3 knobs) |
| Pages | 2 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 0 | enum | Tone Shape | Tone Shape | 5 choices | Sine |  | Sine, Triangle, Saw, Square, Pulses |
| knob1 | 1 | float | Frequency | Frequency | 20 .. 20000 | 440.0 | Hz |  |
| knob2 | 2 | float | Amplitude | Amplitude | -96 .. 0 | -18.0 | dBFS |  |

**Page 2**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 3 | enum | Noise Type | Noise Type | 6 choices | White Gaussian |  | White Gaussian, White Triangular, White Uniform, White Binary, Pink, Brown |
| knob1 | 4 | float | Level | Level | -96 .. 0 | -60.0 | dBFS |  |

### 42. Streaming Preview — `AUR_STREAMPREVIEW`

| | |
|---|---|
| Category | Utility |
| Product id | `STRE` |
| Effect ids | `AURA:STRE:MONO` · `AURA:STRE:M2ST` · `AURA:STRE:STER` |
| Parameters | 4 (3 switches, 1 knobs) |
| Pages | 3 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 1 | bool | Apply Positive Gain | Pos Gain | off / on | on |  |  |
| knob1 | 0 | float | Playback Loudness | Playback | -36 .. 0 | -16.0 | LUFS |  |

**Page 2**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 2 | enum | Codec | Codec | 5 choices | Lossless |  | Lossless, OGG, MP3, AAC, OPUS |

**Page 3**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 3 | enum | Quality | Quality | 3 choices | Medium |  | Low, Medium, High |

### 43. Find Similar — `AUR_FINDSIMILAR`

| | |
|---|---|
| Category | Measurement |
| Product id | `FIND` |
| Effect ids | `AURA:FIND:MONO` · `AURA:FIND:M2ST` · `AURA:FIND:STER` |
| Parameters | 1 (0 switches, 1 knobs) |
| Pages | 1 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| knob1 | 0 | float | Similarity | Similarity | 0 .. 1 | 0.8 |  |  |

### 44. Connect — `AUR_CONNECT`

| | |
|---|---|
| Category | System |
| Product id | `CONN` |
| Effect ids | `AURA:CONN:MONO` · `AURA:CONN:M2ST` · `AURA:CONN:STER` |
| Parameters | 2 (2 switches, 0 knobs) |
| Pages | 2 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 0 | bool | Auto Send | Auto Send | off / on | off |  |  |

**Page 2**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 1 | bool | Keep Source | Keep Source | off / on | on |  |  |

### 45. Monitor — `AUR_MONITOR`

| | |
|---|---|
| Category | System |
| Product id | `MONI` |
| Effect ids | `AURA:MONI:MONO` · `AURA:MONI:M2ST` · `AURA:MONI:STER` |
| Parameters | 1 (1 switches, 0 knobs) |
| Pages | 1 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 0 | bool | Monitor Loudness | Monitor | off / on | on |  |  |

### 46. Clip Classifier — `AUR_CLIPCLASSIFIER`

| | |
|---|---|
| Category | Measurement |
| Product id | `CLIP` |
| Effect ids | `AURA:CLIP:MONO` · `AURA:CLIP:M2ST` · `AURA:CLIP:STER` |
| Parameters | 5 (2 switches, 3 knobs) |
| Pages | 2 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 0 | enum | Detected Class | Detected | 10 choices | Piano |  | Piano, Bass, Drums, Guitar, Vocals, Synth, Strings, Woodwind, Brass, Organ |
| knob1 | 1 | float | Confidence | Confidence | 0 .. 1 | 0.0 |  |  |
| knob2 | 2 | float | Similarity | Similarity | 0 .. 1 | 0.0 |  |  |

**Page 2**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 3 | bool | Highlight Match | Highlight | off / on | off |  |  |
| knob1 | 4 | float | Analysis Length | Analysis | 0.5 .. 30 | 4.0 | s |  |

### 47. Spectral Editor — `AUR_SPECTRALEDITOR`

| | |
|---|---|
| Category | Repair |
| Product id | `SPEN` |
| Effect ids | `AURA:SPEN:MONO` · `AURA:SPEN:M2ST` · `AURA:SPEN:STER` |
| Parameters | 7 (3 switches, 4 knobs) |
| Pages | 3 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 0 | enum | Mode | Mode | 5 choices | Attenuate |  | Attenuate, Replace, Gain, Pattern, Partials |
| knob1 | 1 | float | Reduction | Reduction | 0 .. 1 | 0.5 |  |  |
| knob2 | 2 | float | Region Length | Region | 0.02 .. 10 | 0.25 | s |  |

**Page 2**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 3 | enum | Direction | Direction | 3 choices | 2D |  | Horizontal, Vertical, 2D |

**Page 3**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 4 | bool | Multi Resolution | Multi | off / on | on |  |  |
| knob1 | 5 | float | Before Weighting | Before | 0 .. 1 | 0.5 |  |  |
| knob2 | 6 | float | After Weighting | After | 0 .. 1 | 0.5 |  |  |

### 48. Band Restoration — `AUR_BANDRESTORATION`

| | |
|---|---|
| Category | Repair |
| Product id | `BAND` |
| Effect ids | `AURA:BAND:MONO` · `AURA:BAND:M2ST` · `AURA:BAND:STER` |
| Parameters | 25 (13 switches, 12 knobs) |
| Pages | 14 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| knob1 | 0 | int | Bands | Bands | 1 .. 4 | 4 |  |  |
| knob2 | 1 | float | Low Crossover | Low | 20 .. 2000 | 200.0 | Hz |  |
| knob3 | 2 | float | Mid Crossover | Mid | 200 .. 10000 | 2000.0 | Hz |  |

**Page 2**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 4 | enum | Phase Linearity | Phase | 2 choices | Efficient |  | Efficient, Linear Phase |
| knob1 | 3 | float | High Crossover | High | 1000 .. 20000 | 10000.0 | Hz |  |
| knob2 | 5 | float | Restoration Depth | Restoration | 0 .. 40 | 20.0 | dB |  |
| knob3 | 6 | float | Noise Threshold | Noise | 0 .. 24 | 6.0 | dB |  |

**Page 3**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 10 | bool | Band 1 Solo | Band 1 Solo | off / on | off |  |  |
| knob1 | 7 | float | High Frequency Transparency | High Freque… | 0 .. 100 | 100.0 | % |  |
| knob2 | 8 | float | Broadband Mix | Broadband | 0 .. 100 | 0.0 | % |  |
| knob3 | 9 | float | Band 1 Mix | Band 1 Mix | -60 .. 12 | 0.0 | dB |  |

**Page 4**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 11 | bool | Band 1 Mute | Band 1 Mute | off / on | off |  |  |

**Page 5**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 12 | bool | Band 1 Bypass | Band 1 | off / on | off |  |  |
| knob1 | 13 | float | Band 2 Mix | Band 2 Mix | -60 .. 12 | 0.0 | dB |  |

**Page 6**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 14 | bool | Band 2 Solo | Band 2 Solo | off / on | off |  |  |

**Page 7**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 15 | bool | Band 2 Mute | Band 2 Mute | off / on | off |  |  |

**Page 8**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 16 | bool | Band 2 Bypass | Band 2 | off / on | off |  |  |
| knob1 | 17 | float | Band 3 Mix | Band 3 Mix | -60 .. 12 | 0.0 | dB |  |

**Page 9**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 18 | bool | Band 3 Solo | Band 3 Solo | off / on | off |  |  |

**Page 10**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 19 | bool | Band 3 Mute | Band 3 Mute | off / on | off |  |  |

**Page 11**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 20 | bool | Band 3 Bypass | Band 3 | off / on | off |  |  |
| knob1 | 21 | float | Band 4 Mix | Band 4 Mix | -60 .. 12 | 0.0 | dB |  |

**Page 12**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 22 | bool | Band 4 Solo | Band 4 Solo | off / on | off |  |  |

**Page 13**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 23 | bool | Band 4 Mute | Band 4 Mute | off / on | off |  |  |

**Page 14**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 24 | bool | Band 4 Bypass | Band 4 | off / on | off |  |  |

### 49. Voice Reverb — `AUR_VOICEREVERB`

| | |
|---|---|
| Category | Repair |
| Product id | `VOIZ` |
| Effect ids | `AURA:VOIZ:MONO` · `AURA:VOIZ:M2ST` · `AURA:VOIZ:STER` |
| Parameters | 6 (1 switches, 5 knobs) |
| Pages | 2 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| knob1 | 0 | float | Reduction | Reduction | 0 .. 24 | 12.0 | dB |  |
| knob2 | 1 | float | Tail Length | Tail Length | 0.1 .. 5 | 1.0 | s |  |
| knob3 | 2 | float | Voice Band Low | Voice Band | 40 .. 400 | 80.0 | Hz |  |

**Page 2**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| switch | 5 | bool | Output Reverb Only | Reverb Only | off / on | off |  |  |
| knob1 | 3 | float | Voice Band High | Voice Band | 3000 .. 20000 | 12000.0 | Hz |  |
| knob2 | 4 | float | Sensitivity | Sensitivity | 0 .. 1 | 0.5 |  |  |

### 50. Spatial Suppressor — `AUR_SPATIALSUPPRESSOR`

| | |
|---|---|
| Category | Repair |
| Product id | `SPAT` |
| Effect ids | `AURA:SPAT:MONO` · `AURA:SPAT:M2ST` · `AURA:SPAT:STER` |
| Parameters | 2 (0 switches, 2 knobs) |
| Pages | 1 |

**Page 1**

| Slot | Id | Type | Name | Label | Range | Default | Unit | Choices |
|---|---|---|---|---|---|---|---|---|
| knob1 | 0 | float | Reduction | Reduction | 0.0 .. 1.0 | 1.0 |  |  |
| knob2 | 1 | float | Artifact Smoothing | Artifact | 0.0 .. 1.0 | 0.5 |  |  |
