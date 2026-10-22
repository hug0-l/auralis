# Auralis Modules

> **Generated file — do not edit by hand.**
> Regenerate with `python tools/codegen_registry.py`.
> Source of truth: `aurcore/schema/params.tsv`.

51 modules, 295 parameters. Every module exposes its parameters through `ParamStore`; the names below are the canonical parameter keys used in presets and chains.

The **Id** column is the parameter id: the position of the parameter inside its own module, counted from zero.  The id space is per module, so the ids of a module never depend on any other module — adding or removing a module, or growing another module, leaves every existing id where it was.  The same numbers appear in `aurcore/schema/modules.json`, `aurcore/schema/generated.py` and `aurcore/schema/aur_modules.h`.

## Index

| # | Code | Module | Category | Edition | Element | Params |
|---:|---|---|---|---|---|---:|
| 0 | `AUR_DECLICK` | De-Click | Repair | standard | `DeClicker` | 6 |
| 1 | `AUR_DECRACKLE` | De-Crackle | Repair | standard | `DeClicker` | 4 |
| 2 | `AUR_DECLIP` | De-Clip | Repair | standard | `Declipper` | 5 |
| 3 | `AUR_DEHUM` | De-Hum | Repair | standard | `HumRemoval` | 20 |
| 4 | `AUR_DEESS` | De-Ess | Repair | standard | `DeEsser` | 8 |
| 5 | `AUR_DEPLOSIVE` | De-Plosive | Repair | standard | `DePlosive` | 3 |
| 6 | `AUR_DEREVERB` | De-Reverb | Repair | standard | `Dereverb` | 10 |
| 7 | `AUR_DERUSTLE` | De-Rustle | Repair | advanced | `DeRustle` | 3 |
| 8 | `AUR_DEWIND` | De-Wind | Repair | advanced | `DeWind` | 5 |
| 9 | `AUR_DEBLEED` | De-Bleed | Repair | standard | `DeBleed` | 2 |
| 10 | `AUR_BREATH` | Breath Control | Repair | standard | `DeBreath` | 6 |
| 11 | `AUR_MOUTHDECLICK` | Mouth De-Click | Repair | standard | `MouthDeclick` | 4 |
| 12 | `AUR_VOICEDENOISE` | Voice De-Noise | Repair | standard | `DenoiserZL` | 14 |
| 13 | `AUR_SPECTRALDENOISE` | Spectral De-Noise | Repair | standard | `Denoiser` | 20 |
| 14 | `AUR_SPECTRALREPAIR` | Spectral Repair | Repair | standard | `SpectralRepair` | 7 |
| 15 | `AUR_SPECTRALRECOVERY` | Spectral Recovery | Repair | advanced | `SpectralRecovery` | 5 |
| 16 | `AUR_GUITARDENOISE` | Guitar De-Noise | Repair | standard | `GuitarDenoiser` | 8 |
| 17 | `AUR_DECONSTRUCT` | Deconstruct | Repair | advanced | `Deconstruct` | 6 |
| 18 | `AUR_DIALOGUEISOLATE` | Dialogue Isolate | Repair | advanced | `DialogueExtract` | 5 |
| 19 | `AUR_MUSICREBALANCE` | Music Rebalance | Repair | standard | `MusicSourceSeparate` | 9 |
| 20 | `AUR_AMBIENCEMATCH` | Ambience Match | Repair | advanced | `MatchAmbience` | 6 |
| 21 | `AUR_REPAIRASSISTANT` | Repair Assistant | Repair | standard | `RepairAssistant` | 8 |
| 22 | `AUR_LEVELER` | Leveler | Utility | advanced | `Leveler` | 6 |
| 23 | `AUR_LOUDNESS` | Loudness Control | Utility | standard | `Loudness` | 6 |
| 24 | `AUR_LOUDNESSOPT` | Loudness Optimize | Utility | standard | `LoudnessOptimize` | 4 |
| 25 | `AUR_EQ` | EQ | Utility | standard | `ParametricEQ` | 14 |
| 26 | `AUR_EQMATCH` | EQ Match | Utility | advanced | `MatchEQ` | 1 |
| 27 | `AUR_GAIN` | Gain | Utility | standard | `GainStage` | 1 |
| 28 | `AUR_FADE` | Fade | Utility | standard | `GainStage` | 2 |
| 29 | `AUR_NORMALIZE` | Normalize | Utility | standard | `GainStage` | 1 |
| 30 | `AUR_PHASE` | Phase | Utility | standard | `ChannelOperations` | 2 |
| 31 | `AUR_MIXING` | Mixing | Utility | standard | `ChannelOperations` | 2 |
| 32 | `AUR_CENTEREXTRACT` | Center Extract | Repair | advanced | `ChannelOperations` | 4 |
| 33 | `AUR_AZIMUTH` | Azimuth | Repair | advanced | `ChannelOperations` | 3 |
| 34 | `AUR_DITHER` | Dither | Utility | standard | `Dither` | 4 |
| 35 | `AUR_RESAMPLE` | Resample | Utility | standard | `Resampler` | 4 |
| 36 | `AUR_TIMEPITCH` | Time & Pitch | Utility | standard | `TimeStretchPitchShift` | 7 |
| 37 | `AUR_VARIPITCH` | Variable Pitch | Utility | standard | `PitchContour` | 4 |
| 38 | `AUR_VARTIME` | Variable Time | Utility | standard | `VariableTime` | 3 |
| 39 | `AUR_WOWFLUTTER` | Wow & Flutter | Repair | advanced | `WowAndFlutter` | 4 |
| 40 | `AUR_INTERPOLATE` | Interpolate | Utility | standard | `Interpolate` | 1 |
| 41 | `AUR_SIGNALGEN` | Signal Generator | Utility | standard | `SignalGenerator` | 5 |
| 42 | `AUR_STREAMPREVIEW` | Streaming Preview | Utility | standard | `StreamingPreview` | 4 |
| 43 | `AUR_FINDSIMILAR` | Find Similar | Measurement | standard | `FindSimilar` | 1 |
| 44 | `AUR_CONNECT` | Connect | System | standard | `Connect` | 2 |
| 45 | `AUR_MONITOR` | Monitor | System | standard | `Monitor` | 1 |
| 46 | `AUR_CLIPCLASSIFIER` | Clip Classifier | Measurement | standard | `ClipClassifier` | 5 |
| 47 | `AUR_SPECTRALEDITOR` | Spectral Editor | Repair | standard | `SpectralEditor` | 7 |
| 48 | `AUR_BANDRESTORATION` | Band Restoration | Repair | standard | `BandRestoration` | 25 |
| 49 | `AUR_VOICEREVERB` | Voice Reverb | Repair | standard | `Dereverb` | 6 |
| 50 | `AUR_SPATIALSUPPRESSOR` | Spatial Suppressor | Repair | advanced | `ChannelOperations` | 2 |

## Repair

### De-Click (`AUR_DECLICK`)

Element `DeClicker` · edition `standard` · 6 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Algorithm | enum | `Single Band` | 0 – 3 | — | Single Band, MB Periodic, MB Random, Low Latency | Algorithm | yes | no | linear | 1 | Repair strategy: broadband, periodic buzz, multi-band or low latency. | — |
| 1 | Sensitivity | float | `3.0` | 0 – 10 | — | — | Sensitivity | yes | no | linear | 1 | How eagerly transients are detected. Higher values catch quieter events. | — |
| 2 | Frequency Skew | float | `0.0` | -1 – 1 | — | — | Frequency Skew | yes | no | linear | 1 | Bias detection towards high (positive) or low (negative) frequencies. | — |
| 3 | Click Widening | float | `0.0` | 0 – 1 | — | — | Click Widening | yes | no | linear | 1 | Widens the spectral band that is replaced around a detected event. | — |
| 4 | Output Clicks Only | bool | `false` | 0 – 1 | — | — | Output | yes | no | linear | 1 | Render only the detected click signal instead of the repaired audio. | — |
| 5 | Removed Mix | float | `0.0` | 0 – 100 | % | — | Mix | yes | no | linear | 1 | Mixes the removed clicks back into the output: 0 % leaves the processed signal as it is, 100 % plays the removed clicks alone. | — |

### De-Crackle (`AUR_DECRACKLE`)

Element `DeClicker` · edition `standard` · 4 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Quality | enum | `Medium` | 0 – 2 | — | Low, Medium, High | Algorithm | yes | no | linear | 1 | Detection aggressiveness and the bandwidth that is repaired. | — |
| 1 | Reduction | float | `0.5` | 0 – 1 | — | — | Reduction | yes | no | linear | 1 | How much of the detected crackle is removed. | Strength |
| 2 | Amplitude Skew | float | `0.0` | -1 – 1 | — | — | Amplitude Skew | yes | no | linear | 1 | Positive values favour quiet crackle, negative values favour loud crackle. | — |
| 3 | Output Crackle Only | bool | `false` | 0 – 1 | — | — | Output | yes | no | linear | 1 | Render only the detected crackle instead of the repaired audio. | — |

### De-Clip (`AUR_DECLIP`)

Element `Declipper` · edition `standard` · 5 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Threshold | float | `-0.1` | -6 – 0 | dB | — | Threshold | yes | no | linear | 1 | How far below the peak of the material a flat top may sit and still be counted as a clipped peak. | — |
| 1 | Link Threshold | bool | `true` | 0 – 1 | — | — | Threshold | yes | no | linear | 1 | Use one threshold for both polarities. When off the negative polarity is detected 1 dB earlier. | — |
| 2 | Quality | enum | `Medium` | 0 – 2 | — | Low, Medium, High | Algorithm | yes | no | linear | 1 | Interpolation order and bandwidth of the reconstructed peak. | — |
| 3 | Makeup Gain | float | `0.0` | -12 – 24 | dB | — | Gain | yes | no | linear | 1 | Gain applied after the peaks have been reconstructed. | — |
| 4 | Post Limiter | bool | `false` | 0 – 1 | — | — | Gain | yes | no | linear | 1 | Soft limiter after the makeup gain, to catch reconstruction overshoot. | — |

### De-Hum (`AUR_DEHUM`)

Element `HumRemoval` · edition `standard` · 20 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Mode | enum | `Static` | 0 – 1 | — | Static, Dynamic | Algorithm | yes | no | linear | 1 | Static notch bank, or an adaptive bank that follows a drifting hum. | — |
| 1 | Base Frequency | float | `50.0` | 20 – 500 | Hz | — | Static | yes | no | log | 1 | Fundamental frequency of the hum. | — |
| 2 | Filter Q | float | `1.0` | 0.1 – 30 | — | — | Static | yes | no | log | 1 | Width of the notch, relative to the fundamental. | — |
| 3 | Number of Harmonics | int | `8` | 0 – 16 | — | — | Static | yes | no | linear | 1 | How many harmonics above the fundamental are notched. | — |
| 4 | Link Harmonics | enum | `All` | 0 – 2 | — | All, Odd/Even, None | Static | yes | no | linear | 1 | Which harmonics take part: All, odd harmonics only, or the fundamental alone. | — |
| 5 | Harmonic Slope | float | `0.0` | -12 – 12 | dB | — | Static | yes | no | linear | 1 | Gain tilt applied to each successive harmonic. | — |
| 6 | Enable Learning | bool | `false` | 0 – 1 | — | — | Learning | yes | no | linear | 1 | Analyse an excerpt and use the hum frequency found in it. | — |
| 7 | Adaptive | bool | `true` | 0 – 1 | — | — | Learning | yes | no | linear | 1 | Keep re-estimating the hum frequency while the audio runs. | — |
| 8 | Enable Highpass | bool | `false` | 0 – 1 | — | — | Filters | yes | no | linear | 1 | Remove everything below the high-pass frequency. | — |
| 9 | Highpass Frequency | float | `20.0` | 20 – 500 | Hz | — | Filters | yes | no | log | 1 | Corner frequency of the optional high-pass. | — |
| 10 | Enable Lowpass | bool | `false` | 0 – 1 | — | — | Filters | yes | no | linear | 1 | Remove everything above the low-pass frequency. | — |
| 11 | Lowpass Frequency | float | `20000.0` | 500 – 22050 | Hz | — | Filters | yes | no | log | 1 | Corner frequency of the optional low-pass. | — |
| 12 | Linear Phase Filters | bool | `false` | 0 – 1 | — | — | Filters | yes | no | linear | 1 | Use a linear-phase FIR instead of the IIR biquad bank. | — |
| 13 | Enable DC Offset | bool | `false` | 0 – 1 | — | — | Filters | yes | no | linear | 1 | Remove a constant DC offset from the signal. | — |
| 14 | Dynamic Sensitivity | float | `0.5` | 0 – 1 | — | — | Dynamic | yes | no | linear | 1 | How aggressively the adaptive bank suppresses the hum. | — |
| 15 | Dynamic Bands | int | `3` | 1 – 12 | — | — | Dynamic | yes | no | linear | 1 | Number of adaptive filters (fundamental plus harmonics). | — |
| 16 | Dynamic Low Cutoff | float | `20.0` | 20 – 500 | Hz | — | Dynamic | yes | no | log | 1 | Lowest frequency the adaptive bank looks at. | — |
| 17 | Dynamic High Cutoff | float | `20000.0` | 500 – 22050 | Hz | — | Dynamic | yes | no | log | 1 | Highest frequency the adaptive bank looks at. | — |
| 18 | Output Hum Only | bool | `false` | 0 – 1 | — | — | Output | yes | no | linear | 1 | Render the estimated hum component on its own. | — |
| 19 | Removed Mix | float | `0.0` | 0 – 100 | % | — | Mix | yes | no | linear | 1 | Mixes the removed hum back into the output: 0 % leaves the processed signal as it is, 100 % plays the removed hum alone. | — |

### De-Ess (`AUR_DEESS`)

Element `DeEsser` · edition `standard` · 8 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Mode | enum | `Classic` | 0 – 1 | — | Classic, Spectral | Algorithm | yes | no | linear | 1 | Broadband band reduction, or per-bin spectral shaping. | — |
| 1 | Threshold | float | `-30.0` | -60 – 0 | dB | — | Threshold | yes | no | linear | 1 | Level above which the band counts as sibilant. | — |
| 2 | Absolute Mode | bool | `false` | 0 – 1 | — | — | Threshold | yes | no | linear | 1 | Measure the band against an absolute level instead of the whole signal. | — |
| 3 | Cutoff Frequency | float | `6000.0` | 1000 – 16000 | Hz | — | Threshold | yes | no | log | 1 | Lowest frequency of the sibilant band. | — |
| 4 | Spectral Flattening | float | `0.5` | 0 – 1 | — | — | Tone | yes | no | linear | 1 | Pull the sibilant band towards its own average. | — |
| 5 | Spectral Tilt | float | `0.0` | -1 – 1 | — | — | Tone | yes | no | linear | 1 | Weight the reduction towards the low or the high end of the band. | — |
| 6 | Speed | enum | `Fast` | 0 – 1 | — | Fast, Slow | Algorithm | yes | no | linear | 1 | Attack and release of the gain reduction. | — |
| 7 | Output Ess Only | bool | `false` | 0 – 1 | — | — | Output | yes | no | linear | 1 | Render the sibilance that was removed. | — |

### De-Plosive (`AUR_DEPLOSIVE`)

Element `DePlosive` · edition `standard` · 3 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Sensitivity | float | `3.0` | 0 – 10 | — | — | Sensitivity | yes | no | linear | 1 | How far above the reference level a burst has to sit to count. | — |
| 1 | Reduction | float | `2.5` | 0 – 10 | — | — | Reduction | yes | no | linear | 1 | How hard the detected burst is pulled down. | Strength |
| 2 | Frequency Limit | float | `300.0` | 20 – 500 | Hz | — | Band | yes | no | log | 1 | Upper edge of the band the plosive is looked for in. | — |

### De-Reverb (`AUR_DEREVERB`)

Element `Dereverb` · edition `standard` · 10 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Reduction | float | `0.5` | 0 – 1 | — | — | Reduction | yes | no | linear | 1 | How much of the reverberant field is removed. | — |
| 1 | Tail Length | float | `1.0` | 0.1 – 10 | s | — | Tail | yes | no | linear | 1 | Decay time used to separate the sustained field from the direct one. | — |
| 2 | Artifact Smoothing | float | `0.5` | 0 – 1 | — | — | Smoothing | yes | no | linear | 1 | Smooths the gain curve to avoid audible artefacts. | — |
| 3 | Band Reduction Low | float | `1.0` | 0 – 1 | — | — | Bands | yes | no | linear | 1 | How much of the band below 250 Hz is removed. | Band Strength Low |
| 4 | Band Reduction Low-Mid | float | `1.0` | 0 – 1 | — | — | Bands | yes | no | linear | 1 | How much of the band from 250 Hz to 2 kHz is removed. | Band Strength Low-Mid |
| 5 | Band Reduction High-Mid | float | `1.0` | 0 – 1 | — | — | Bands | yes | no | linear | 1 | How much of the band from 2 kHz to 8 kHz is removed. | Band Strength High-Mid |
| 6 | Band Reduction High | float | `1.0` | 0 – 1 | — | — | Bands | yes | no | linear | 1 | How much of the band above 8 kHz is removed. | Band Strength High |
| 7 | Enhance Dry Signal | bool | `false` | 0 – 1 | — | — | Mix | yes | no | linear | 1 | Compensates the direct signal for the energy that was removed. | — |
| 8 | Output Reverb Only | bool | `false` | 0 – 1 | — | — | Output | yes | no | linear | 1 | Renders the removed reverberant component instead of the cleaned signal. | — |
| 9 | Removed Mix | float | `0.0` | 0 – 100 | % | — | Mix | yes | no | linear | 1 | Mixes the removed reverberant field back into the output: 0 % leaves the processed signal as it is, 100 % plays the removed reverberant field alone. | — |

### De-Rustle (`AUR_DERUSTLE`)

Element `DeRustle` · edition `advanced` · 3 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Reduction | float | `0.5` | 0 – 1 | — | — | Reduction | yes | no | linear | 1 | How much of the estimated floor is subtracted. | Reduction Strength |
| 1 | Ambience Preservation | float | `0.5` | 0 – 1 | — | — | Reduction | yes | no | linear | 1 | How much of the ambience is left in the signal. | — |
| 2 | Separation Algorithm | enum | `Channel Independent` | 0 – 2 | — | Channel Independent, Joint, Advanced Joint | Algorithm | yes | no | linear | 1 | Whether the channels are cleaned on their own or together. | — |

### De-Wind (`AUR_DEWIND`)

Element `DeWind` · edition `advanced` · 5 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Reduction | float | `0.5` | 0 – 1 | — | — | Reduction | yes | no | linear | 1 | How much of the detected rumble is taken away. | — |
| 1 | Crossover Frequency | float | `500.0` | 50 – 2000 | Hz | — | Band | yes | no | log | 1 | Frequency that separates the rumble band from the signal. | — |
| 2 | Fundamental Recovery | float | `0.5` | 0 – 1 | — | — | Recovery | yes | no | linear | 1 | How much of the wanted fundamental is put back. | — |
| 3 | Artifact Smoothing | float | `0.5` | 0 – 1 | — | — | Smoothing | yes | no | linear | 1 | How slowly the gain is allowed to move. | — |
| 4 | Removed Mix | float | `0.0` | 0 – 100 | % | — | Mix | yes | no | linear | 1 | Mixes the removed rumble back into the output: 0 % leaves the processed signal as it is, 100 % plays the removed rumble alone. | — |

### De-Bleed (`AUR_DEBLEED`)

Element `DeBleed` · edition `standard` · 2 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Reduction | float | `0.5` | 0 – 1 | — | — | Reduction | yes | no | linear | 1 | How much of the target the estimated bleed is taken out of. At 0 nothing is touched, at 1 the bleed is taken out of every bin the reference explains. | Strength |
| 1 | Artifact Smoothing | float | `0.5` | 0 – 1 | — | — | Smoothing | yes | no | linear | 1 | How much of the original target is protected from artefacts. A high value widens the soft edge of the decision and slows the mask down. | — |

### Breath Control (`AUR_BREATH`)

Element `DeBreath` · edition `standard` · 6 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Mode | enum | `Gain Mode` | 0 – 1 | — | Gain Mode, Target Mode | Algorithm | yes | no | linear | 1 | Apply a fixed gain, or pull the breath down to a target level. | — |
| 1 | Gain | float | `-9.0` | -24 – 0 | dB | — | Gain | yes | no | linear | 1 | Gain applied to a detected breath in Gain Mode. | — |
| 2 | Target Level | float | `-30.0` | -60 – 0 | dBFS | — | Gain | yes | no | linear | 1 | Level a detected breath is pulled down to in Target Mode. | — |
| 3 | Sensitivity | float | `0.5` | 0 – 1 | — | — | Sensitivity | yes | no | linear | 1 | How easily a breath is recognised. | — |
| 4 | Output Breaths Only | bool | `false` | 0 – 1 | — | — | Output | yes | no | linear | 1 | Render the breathing that was removed. | — |
| 5 | Removed Mix | float | `0.0` | 0 – 100 | % | — | Mix | yes | no | linear | 1 | Mixes the removed breathing back into the output: 0 % leaves the processed signal as it is, 100 % plays the removed breathing alone. | — |

### Mouth De-Click (`AUR_MOUTHDECLICK`)

Element `MouthDeclick` · edition `standard` · 4 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Sensitivity | float | `3.0` | 0 – 10 | — | — | Sensitivity | yes | no | linear | 1 | How eagerly mouth clicks are detected. | — |
| 1 | Frequency Skew | float | `0.0` | -1 – 1 | — | — | Frequency Skew | yes | no | linear | 1 | Bias detection towards high (positive) or low (negative) frequencies. | — |
| 2 | Click Widening | float | `0.0` | 0 – 1 | — | — | Click Widening | yes | no | linear | 1 | Widens the spectral band that is replaced around a detected click. | — |
| 3 | Output Clicks Only | bool | `false` | 0 – 1 | — | — | Output | yes | no | linear | 1 | Render only the detected click signal instead of the repaired audio. | — |

### Voice De-Noise (`AUR_VOICEDENOISE`)

Element `DenoiserZL` · edition `standard` · 14 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Processing Mode | enum | `realtime` | 0 – 1 | — | realtime, offline | Algorithm | yes | no | linear | 1 | Realtime keeps the module inside the budget of a block; offline uses the best configuration the module has and declares the look-ahead it costs. | — |
| 1 | Optimize For | enum | `Dialogue` | 0 – 1 | — | Dialogue, Music | Algorithm | yes | no | linear | 1 | Dialogue favours speech intelligibility, Music keeps the spectrum natural. | — |
| 2 | Filter Type | enum | `Surgical` | 0 – 1 | — | Surgical, Gentle | Algorithm | yes | no | linear | 1 | Surgical removes more, Gentle keeps more of the original texture. | — |
| 3 | Adaptive Mode | bool | `true` | 0 – 1 | — | — | Adaptive | yes | no | linear | 1 | Keep tracking the noise reference while processing. | — |
| 4 | Threshold | float | `-15.0` | -90 – 0 | dB | — | Threshold | yes | no | linear | 1 | Global threshold offset applied to every band group. | — |
| 5 | Threshold 1 | float | `-40.0` | -90 – 0 | dB | — | Bands | yes | no | linear | 1 | Threshold of the highest band group. | — |
| 6 | Threshold 2 | float | `-50.0` | -90 – 0 | dB | — | Bands | yes | no | linear | 1 | Threshold of the second band group. | — |
| 7 | Threshold 3 | float | `-60.0` | -90 – 0 | dB | — | Bands | yes | no | linear | 1 | Threshold of the third band group. | — |
| 8 | Threshold 4 | float | `-70.0` | -90 – 0 | dB | — | Bands | yes | no | linear | 1 | Threshold of the fourth band group. | — |
| 9 | Threshold 5 | float | `-80.0` | -90 – 0 | dB | — | Bands | yes | no | linear | 1 | Threshold of the fifth band group. | — |
| 10 | Threshold 6 | float | `-90.0` | -90 – 0 | dB | — | Bands | yes | no | linear | 1 | Threshold of the lowest band group. | — |
| 11 | Reduction | float | `18.0` | 0 – 30 | dB | — | Reduction | yes | no | linear | 1 | Maximum gain reduction per band. | — |
| 12 | Learn | bool | `false` | 0 – 1 | — | — | Learning | yes | no | linear | 1 | Capture the noise profile of the incoming audio. | — |
| 13 | Removed Mix | float | `0.0` | 0 – 100 | % | — | Mix | yes | no | linear | 1 | Mixes the removed noise back into the output: 0 % leaves the processed signal as it is, 100 % plays the removed noise alone. | — |

### Spectral De-Noise (`AUR_SPECTRALDENOISE`)

Element `Denoiser` · edition `standard` · 20 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Processing Mode | enum | `realtime` | 0 – 1 | — | realtime, offline | Algorithm | yes | no | linear | 1 | Realtime keeps the module inside the budget of a block; offline uses the best configuration the module has and declares the look-ahead it costs. | — |
| 1 | Algorithm | enum | `Advanced` | 0 – 2 | — | Simple, Advanced, Extreme | Algorithm | yes | no | linear | 1 | Simple: single resolution subtraction. Advanced: OM-LSA refinement. Extreme: multi resolution plus stronger artifact control. | — |
| 2 | Quality | enum | `B` | 0 – 3 | — | A, B, C, D | Quality | yes | no | linear | 1 | A is the fastest and least accurate, D the slowest and most accurate. | — |
| 3 | FFT Size | float | `20.0` | 5 – 60 | ms | — | Analysis | yes | no | linear | 1 | Length of the analysis window. | — |
| 4 | Multi Resolution | bool | `true` | 0 – 1 | — | — | Analysis | yes | no | linear | 1 | Analyse several window sizes and combine the gain curves. | — |
| 5 | Learning Time | float | `2.0` | 0.5 – 30 | s | — | Learning | yes | no | linear | 1 | How long the noise profile is captured. | — |
| 6 | Adaptive | bool | `true` | 0 – 1 | — | — | Learning | yes | no | linear | 1 | Keep tracking the noise profile while processing. | — |
| 7 | Noise Threshold | float | `0.0` | -40 – 20 | dB | — | Threshold | yes | no | linear | 1 | Estimated signal to noise ratio below which a bin counts as noise. | — |
| 8 | Tonal Threshold | float | `0.0` | -40 – 20 | dB | — | Threshold | yes | no | linear | 1 | Same, measured against the broadband floor for tonal components. | — |
| 9 | Noise Reduction | float | `15.0` | 0 – 40 | dB | — | Reduction | yes | no | linear | 1 | Maximum broadband attenuation. | — |
| 10 | Tonal Reduction | float | `12.0` | 0 – 40 | dB | — | Reduction | yes | no | linear | 1 | Maximum attenuation applied to tonal components. | — |
| 11 | Artifact Control | float | `0.5` | 0 – 1 | — | — | Smoothing | yes | no | linear | 1 | Temporal smoothing of the gain curve (musical noise control). | — |
| 12 | Smoothing | float | `0.5` | 0 – 1 | — | — | Smoothing | yes | no | linear | 1 | Frequency smoothing width of the gain curve. | — |
| 13 | Knee | float | `0.5` | 0 – 1 | — | — | Dynamics | yes | no | linear | 1 | Soft knee width of the reduction law. | — |
| 14 | Release | float | `100.0` | 10 – 1000 | ms | — | Dynamics | yes | no | linear | 1 | Gain release time once wanted content returns. | — |
| 15 | Noise Floor Synthesis | float | `0.5` | 0 – 1 | — | — | Noise Floor | yes | no | linear | 1 | Regenerate the removed noise floor instead of leaving a hole. | — |
| 16 | Masking | float | `0.5` | 0 – 1 | — | — | Noise Floor | yes | no | linear | 1 | Psychoacoustic masking model limits how far a bin may be attenuated. | — |
| 17 | Enhancement | float | `0.5` | 0 – 1 | — | — | Noise Floor | yes | no | linear | 1 | Re-add low level but wanted content. | — |
| 18 | Whitening | float | `0.5` | 0 – 1 | — | — | Noise Floor | yes | no | linear | 1 | Flatten the long term spectrum towards a target curve. | — |
| 19 | Output Noise Only | bool | `false` | 0 – 1 | — | — | Output | yes | no | linear | 1 | Render only the noise that was removed. | — |

### Spectral Repair (`AUR_SPECTRALREPAIR`)

Element `SpectralRepair` · edition `standard` · 7 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Mode | enum | `Attenuate` | 0 – 4 | — | Attenuate, Replace, Gain, Pattern, Partials | Algorithm | yes | no | linear | 1 | What happens inside the selected region. | — |
| 1 | Reduction | float | `0.5` | 0 – 1 | — | — | Attenuate | yes | no | linear | 1 | How strongly the edit is applied. | Strength |
| 2 | Region Length | float | `0.25` | 0.02 – 10 | s | — | Attenuate | yes | no | linear | 1 | Length of the edited region in seconds. | — |
| 3 | Direction | enum | `2D` | 0 – 2 | — | Horizontal, Vertical, 2D | Attenuate | yes | no | linear | 1 | Horizontal smears in time, Vertical in frequency, 2D in both. | — |
| 4 | Multi Resolution | bool | `true` | 0 – 1 | — | — | Attenuate | yes | no | linear | 1 | Analyse the region with more than one window size. | — |
| 5 | Before Weighting | float | `0.5` | 0 – 1 | — | — | Attenuate | yes | no | linear | 1 | Cross fade weight at the start of the region. | — |
| 6 | After Weighting | float | `0.5` | 0 – 1 | — | — | Attenuate | yes | no | linear | 1 | Cross fade weight at the end of the region. | — |

### Spectral Recovery (`AUR_SPECTRALRECOVERY`)

Element `SpectralRecovery` · edition `advanced` · 5 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Spectral Patching | bool | `true` | 0 – 1 | — | — | Recovery | yes | no | linear | 1 | Regenerate the band above the detected cutoff. | — |
| 1 | Low Gain | float | `-20.0` | -60 – 12 | dB | — | Recovery | yes | no | taper | 0.264 | Level below the low cutoff. | — |
| 2 | Low Cutoff | float | `200.0` | 20 – 2000 | Hz | — | Recovery | yes | no | log | 1 | Frequency where the low shelf starts. | — |
| 3 | High Cutoff | float | `4000.0` | 2000 – 16000 | Hz | — | Recovery | yes | no | log | 1 | Frequency where the high shelf starts. | — |
| 4 | High Gain | float | `0.0` | -12 – 2.4 | dB | — | Recovery | yes | no | taper | 0.264 | Level above the high cutoff. | — |

### Guitar De-Noise (`AUR_GUITARDENOISE`)

Element `GuitarDenoiser` · edition `standard` · 8 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Amp Sensitivity | float | `0.5` | 0 – 1 | — | — | Amp | yes | no | linear | 1 | How hard the amp buzz and rumble are notched out. | — |
| 1 | Amp Resolution | int | `128` | 16 – 512 | — | — | Amp | yes | no | linear | 1 | Length (in samples) of the analysis the amp detector reads the rumble level with. | — |
| 2 | Squeak Sensitivity | float | `0.5` | 0 – 1 | — | — | Squeak | yes | no | linear | 1 | How sensitive the string squeak detector is. | — |
| 3 | Squeak Reduction | float | `0.5` | 0 – 1 | — | — | Squeak | yes | no | linear | 1 | How much of a detected squeak is removed. | — |
| 4 | Squeak Duration | enum | `Short` | 0 – 1 | — | Short, Long | Squeak | yes | no | linear | 1 | How long a squeak has to last to count. | — |
| 5 | Pick Sensitivity | float | `0.5` | 0 – 1 | — | — | Pick | yes | no | linear | 1 | How sensitive the pick attack detector is. | — |
| 6 | Pick Reduction | float | `0.5` | 0 – 1 | — | — | Pick | yes | no | linear | 1 | How much of the pick attack is removed. | — |
| 7 | Pick Attack | float | `0.5` | 0 – 1 | — | — | Pick | yes | no | linear | 1 | How tightly the attack window follows the onset. | — |

### Deconstruct (`AUR_DECONSTRUCT`)

Element `Deconstruct` · edition `advanced` · 6 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Tonal Gain | float | `0.0` | -12 – 2.4 | dB | — | Tonal | yes | no | taper | 0.264 | Level of the harmonic component. | — |
| 1 | Noisy Gain | float | `0.0` | -12 – 2.4 | dB | — | Noisy | yes | no | taper | 0.264 | Level of the stationary broadband component. | — |
| 2 | Separate Transients | bool | `true` | 0 – 1 | — | — | Transients | yes | no | linear | 1 | Keep the percussive part as its own component. | — |
| 3 | Transient Gain | float | `0.0` | -12 – 2.4 | dB | — | Transients | yes | no | taper | 0.264 | Level of the percussive component. | — |
| 4 | Balance | enum | `Noisy` | 0 – 1 | — | Noisy, Tonal | Balance | yes | no | linear | 1 | Tilt the balance between the noisy and the tonal part. | — |
| 5 | Artifact Smoothing | float | `0.5` | 0 – 1 | — | — | Smoothing | yes | no | linear | 1 | Smooths the component masks so the split does not chatter. | — |

### Dialogue Isolate (`AUR_DIALOGUEISOLATE`)

Element `DialogueExtract` · edition `advanced` · 5 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Voice Gain | float | `0.0` | -24 – 4.8 | dB | — | Mix | yes | no | taper | 0.264 | Level of the isolated voice stem. | — |
| 1 | Reverb Gain | float | `0.0` | -24 – 4.8 | dB | — | Mix | yes | no | taper | 0.264 | Level of the isolated room-reverb stem. | — |
| 2 | Noise Gain | float | `0.0` | -24 – 4.8 | dB | — | Mix | yes | no | taper | 0.264 | Level of the isolated noise stem. | — |
| 3 | Sensitivity | float | `0.5` | 0 – 1 | — | — | Sensitivity | yes | no | linear | 1 | Softness of the separation boundaries and the voice-detection threshold. | — |
| 4 | Quality | enum | `Good` | 0 – 1 | — | Good, Best | Quality | yes | no | linear | 1 | Good: fast transform. Best: finer transform and a longer analysis window. | — |

### Music Rebalance (`AUR_MUSICREBALANCE`)

Element `MusicSourceSeparate` · edition `standard` · 9 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Vocal Gain | float | `0.0` | -24 – 4.8 | dB | — | Stems | yes | no | taper | 0.264 | Level of the vocal stem. | — |
| 1 | Bass Gain | float | `0.0` | -24 – 4.8 | dB | — | Stems | yes | no | taper | 0.264 | Level of the bass stem (everything below 250 Hz). | — |
| 2 | Drums Gain | float | `0.0` | -24 – 4.8 | dB | — | Stems | yes | no | taper | 0.264 | Level of the drum stem (transient broadband content). | — |
| 3 | Other Gain | float | `0.0` | -24 – 4.8 | dB | — | Stems | yes | no | taper | 0.264 | Level of the remaining instruments. | — |
| 4 | Vocal Mute | bool | `false` | 0 – 1 | — | — | Stems | yes | no | linear | 1 | Silence the vocal stem. | — |
| 5 | Bass Mute | bool | `false` | 0 – 1 | — | — | Stems | yes | no | linear | 1 | Silence the bass stem. | — |
| 6 | Drums Mute | bool | `false` | 0 – 1 | — | — | Stems | yes | no | linear | 1 | Silence the drum stem. | — |
| 7 | Other Mute | bool | `false` | 0 – 1 | — | — | Stems | yes | no | linear | 1 | Silence the remaining instruments. | — |
| 8 | Quality | enum | `Good` | 0 – 2 | — | Good, Better, Best | Quality | yes | no | linear | 1 | Mask resolution. Best uses a 4096-point transform. | — |

### Ambience Match (`AUR_AMBIENCEMATCH`)

Element `MatchAmbience` · edition `advanced` · 6 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Ambience Type | enum | `Complex` | 0 – 1 | — | Complex, Static | Algorithm | yes | no | linear | 1 | Complex: a modulated, living room tone. Static: an unmodulated bed. | — |
| 1 | Ambience Threshold | float | `-60.0` | -100 – 0 | dB | — | Threshold | yes | no | linear | 1 | How far below the active level the source is treated as ambience. | — |
| 2 | Movement | float | `0.5` | 0 – 1 | — | — | Movement | yes | no | linear | 1 | How strongly the ambience is modulated. | — |
| 3 | Randomness | float | `0.5` | 0 – 1 | — | — | Movement | yes | no | linear | 1 | How chaotic the modulation is. | — |
| 4 | Gain | float | `0.0` | -24 – 4.8 | dB | — | Gain | yes | no | taper | 0.264 | Level of the matched ambience relative to the reference profile. | — |
| 5 | Output Ambience Only | bool | `false` | 0 – 1 | — | — | Output | yes | no | linear | 1 | Render the synthesised ambience without the source. | — |

### Repair Assistant (`AUR_REPAIRASSISTANT`)

Element `RepairAssistant` · edition `standard` · 8 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Processing Mode | enum | `realtime` | 0 – 1 | — | realtime, offline | Algorithm | yes | no | linear | 1 | Realtime keeps the module inside the budget of a block; offline uses the best configuration the module has and declares the look-ahead it costs. | — |
| 1 | Mode | enum | `Voice` | 0 – 1 | — | Voice, Music | Algorithm | yes | no | linear | 1 | Voice tunes the detectors for speech, Music for a full mix. | — |
| 2 | Noise Reduction | float | `0.0` | 0 – 1 | — | — | Clean Up | yes | no | linear | 1 | How much of the detected noise floor to remove. | — |
| 3 | Reverb Reduction | float | `0.0` | 0 – 1 | — | — | Clean Up | yes | no | linear | 1 | How much of the detected room reverb to remove. | — |
| 4 | Hum Reduction | float | `0.0` | 0 – 1 | — | — | Clean Up | yes | no | linear | 1 | How much of the detected mains hum to remove. | — |
| 5 | Click Reduction | float | `0.0` | 0 – 1 | — | — | Clean Up | yes | no | linear | 1 | How much of the detected clicks to remove. | — |
| 6 | De-Ess Reduction | float | `0.0` | 0 – 1 | — | — | Tone | yes | no | linear | 1 | How much of the detected sibilance to tame. | — |
| 7 | De-Clip Reduction | float | `0.0` | 0 – 1 | — | — | Tone | yes | no | linear | 1 | How much of the detected clipping to repair. | — |

### Center Extract (`AUR_CENTEREXTRACT`)

Element `ChannelOperations` · edition `advanced` · 4 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Algorithm | enum | `True Phase` | 0 – 1 | — | True Phase, Pseudo Pan | Algorithm | yes | no | linear | 1 | How the centred part of the image is estimated. | — |
| 1 | Reduction | float | `1.0` | 0 – 1 | — | — | Reduction | yes | no | linear | 1 | How much of the non centred content is removed. | Reduction Strength |
| 2 | Artifact Smoothing | float | `0.5` | 0 – 1 | — | — | Smoothing | yes | no | linear | 1 | Smoothing of the per bin masks; higher values reduce musical noise. | — |
| 3 | Dry Mix | float | `0.0` | 0 – 100 | % | — | Mix | yes | no | linear | 1 | Percentage of the untouched signal blended into the result. | — |

### Azimuth (`AUR_AZIMUTH`)

Element `ChannelOperations` · edition `advanced` · 3 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Gain | float | `0.0` | -12 – 12 | dB | — | Gain | yes | no | linear | 1 | Gain trim of the right channel against the left one. | Level |
| 1 | Delay | float | `0.0` | -10 – 10 | ms | — | Delay | yes | no | linear | 1 | Relative shift between the channels; positive delays the right channel. | — |
| 2 | Adaptive Matching | bool | `false` | 0 – 1 | — | — | Matching | yes | no | linear | 1 | Measure and correct the level and time offset between the channels. | — |

### Wow & Flutter (`AUR_WOWFLUTTER`)

Element `WowAndFlutter` · edition `advanced` · 4 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Wow | float | `1.0` | 0 – 5 | Hz | — | Wow | yes | no | linear | 1 | Rate of the slow pitch drift that is corrected. | — |
| 1 | Flutter | float | `20.0` | 8 – 40 | Hz | — | Flutter | yes | no | log | 1 | Rate of the fast pitch flutter that is corrected. | — |
| 2 | Sensitivity | float | `0.5` | 0 – 1 | — | — | Sensitivity | yes | no | linear | 1 | How much of the measured modulation is removed. | — |
| 3 | Center Global Pitch | float | `440.0` | 380 – 500 | Hz | — | Pitch | yes | no | log | 1 | Frequency the pitch tracker follows. | — |

### Spectral Editor (`AUR_SPECTRALEDITOR`)

Element `SpectralEditor` · edition `standard` · 7 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Mode | enum | `Attenuate` | 0 – 4 | — | Attenuate, Replace, Gain, Pattern, Partials | Algorithm | yes | no | linear | 1 | What happens inside the selected region. | — |
| 1 | Reduction | float | `0.5` | 0 – 1 | — | — | Edit | yes | no | linear | 1 | How strongly the edit is applied. | Strength |
| 2 | Region Length | float | `0.25` | 0.02 – 10 | s | — | Edit | yes | no | linear | 1 | Length of the edited region in seconds. | — |
| 3 | Direction | enum | `2D` | 0 – 2 | — | Horizontal, Vertical, 2D | Edit | yes | no | linear | 1 | Axis the cross fade runs along: in time, in frequency, or both. | — |
| 4 | Multi Resolution | bool | `true` | 0 – 1 | — | — | Edit | yes | no | linear | 1 | Analyse the region with more than one window size. | — |
| 5 | Before Weighting | float | `0.5` | 0 – 1 | — | — | Edit | yes | no | linear | 1 | Cross fade weight at the start of the region: 0 is a hard edge, 1 a 20 ms fade. | — |
| 6 | After Weighting | float | `0.5` | 0 – 1 | — | — | Edit | yes | no | linear | 1 | Cross fade weight at the end of the region: 0 is a hard edge, 1 a 20 ms fade. | — |

### Band Restoration (`AUR_BANDRESTORATION`)

Element `BandRestoration` · edition `standard` · 25 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Bands | int | `4` | 1 – 4 | — | — | Crossover | yes | no | linear | 1 | How many bands the signal is split into. | — |
| 1 | Low Crossover | float | `200.0` | 20 – 2000 | Hz | — | Crossover | yes | no | log | 1 | The edge between the first band and the second one. | — |
| 2 | Mid Crossover | float | `2000.0` | 200 – 10000 | Hz | — | Crossover | yes | no | log | 1 | The edge between the second band and the third one. | — |
| 3 | High Crossover | float | `10000.0` | 1000 – 20000 | Hz | — | Crossover | yes | no | log | 1 | The edge between the third band and the fourth one. | — |
| 4 | Phase Linearity | enum | `Efficient` | 0 – 1 | — | Efficient, Linear Phase | Crossover | yes | no | linear | 1 | Efficient is an IIR crossover that adds no delay, Linear Phase is a FIR crossover whose bands carry no phase error and which delays the signal. | — |
| 5 | Restoration Depth | float | `20.0` | 0 – 40 | dB | — | Restoration | yes | no | linear | 1 | The deepest cut the restoration may make in a band. | — |
| 6 | Noise Threshold | float | `6.0` | 0 – 24 | dB | — | Restoration | yes | no | linear | 1 | How far above the learned noise level a band has to rise before the restoration leaves it alone. | — |
| 7 | High Frequency Transparency | float | `100.0` | 0 – 100 | % | — | Restoration | yes | no | linear | 1 | How much of the restoration the top band is spared; at 100 % the top band is left alone. | — |
| 8 | Broadband Mix | float | `0.0` | 0 – 100 | % | — | Mix | yes | no | linear | 1 | How much of the untouched signal is mixed back against the band path. | — |
| 9 | Band 1 Mix | float | `0.0` | -60 – 12 | dB | — | Band 1 | yes | no | taper | 0.264 | The level of band 1 in the output. | — |
| 10 | Band 1 Solo | bool | `false` | 0 – 1 | — | — | Band 1 | yes | no | linear | 1 | Audition band 1 on its own. | — |
| 11 | Band 1 Mute | bool | `false` | 0 – 1 | — | — | Band 1 | yes | no | linear | 1 | Take band 1 out of the recording. | — |
| 12 | Band 1 Bypass | bool | `false` | 0 – 1 | — | — | Band 1 | yes | no | linear | 1 | Leave band 1 as the crossover made it, without the restoration. | — |
| 13 | Band 2 Mix | float | `0.0` | -60 – 12 | dB | — | Band 2 | yes | no | taper | 0.264 | The level of band 2 in the output. | — |
| 14 | Band 2 Solo | bool | `false` | 0 – 1 | — | — | Band 2 | yes | no | linear | 1 | Audition band 2 on its own. | — |
| 15 | Band 2 Mute | bool | `false` | 0 – 1 | — | — | Band 2 | yes | no | linear | 1 | Take band 2 out of the recording. | — |
| 16 | Band 2 Bypass | bool | `false` | 0 – 1 | — | — | Band 2 | yes | no | linear | 1 | Leave band 2 as the crossover made it, without the restoration. | — |
| 17 | Band 3 Mix | float | `0.0` | -60 – 12 | dB | — | Band 3 | yes | no | taper | 0.264 | The level of band 3 in the output. | — |
| 18 | Band 3 Solo | bool | `false` | 0 – 1 | — | — | Band 3 | yes | no | linear | 1 | Audition band 3 on its own. | — |
| 19 | Band 3 Mute | bool | `false` | 0 – 1 | — | — | Band 3 | yes | no | linear | 1 | Take band 3 out of the recording. | — |
| 20 | Band 3 Bypass | bool | `false` | 0 – 1 | — | — | Band 3 | yes | no | linear | 1 | Leave band 3 as the crossover made it, without the restoration. | — |
| 21 | Band 4 Mix | float | `0.0` | -60 – 12 | dB | — | Band 4 | yes | no | taper | 0.264 | The level of band 4 in the output. | — |
| 22 | Band 4 Solo | bool | `false` | 0 – 1 | — | — | Band 4 | yes | no | linear | 1 | Audition band 4 on its own. | — |
| 23 | Band 4 Mute | bool | `false` | 0 – 1 | — | — | Band 4 | yes | no | linear | 1 | Take band 4 out of the recording. | — |
| 24 | Band 4 Bypass | bool | `false` | 0 – 1 | — | — | Band 4 | yes | no | linear | 1 | Leave band 4 as the crossover made it, without the restoration. | — |

### Voice Reverb (`AUR_VOICEREVERB`)

Element `Dereverb` · edition `standard` · 6 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Reduction | float | `12.0` | 0 – 24 | dB | — | Reduction | yes | no | linear | 1 | How much of the reverberant field is removed. | — |
| 1 | Tail Length | float | `1.0` | 0.1 – 5 | s | — | Tail | yes | no | linear | 1 | Decay time used to separate the sustained field from the direct one. | — |
| 2 | Voice Band Low | float | `80.0` | 40 – 400 | Hz | — | Bands | yes | no | log | 1 | The lowest frequency in Hz the module treats as part of the voice. | — |
| 3 | Voice Band High | float | `12000.0` | 3000 – 20000 | Hz | — | Bands | yes | no | log | 1 | The highest frequency in Hz the module treats as part of the voice. | — |
| 4 | Sensitivity | float | `0.5` | 0 – 1 | — | — | Sensitivity | yes | no | linear | 1 | How deeply the level has to fall before the module looks for a room. | — |
| 5 | Output Reverb Only | bool | `false` | 0 – 1 | — | — | Output | yes | no | linear | 1 | Renders the removed reverberant component instead of the cleaned signal. | — |

### Spatial Suppressor (`AUR_SPATIALSUPPRESSOR`)

Element `ChannelOperations` · edition `advanced` · 2 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Reduction | float | `1.0` | 0 – 1 | — | — | Reduction | yes | no | linear | 1 | How much of the content the channels do not share is removed. At 0 nothing is touched, at 1 all of it is removed. | Reduction Strength |
| 1 | Artifact Smoothing | float | `0.5` | 0 – 1 | — | — | Smoothing | yes | no | linear | 1 | The width of the soft edge around the decision. A high value widens the edge and trades the removal for a smoother gain curve. | — |

## Utility

### Leveler (`AUR_LEVELER`)

Element `Leveler` · edition `advanced` · 6 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Optimize For | enum | `Dialogue` | 0 – 1 | — | Dialogue, Music | Algorithm | yes | no | linear | 1 | Dialogue rides the short-term loudness, music the momentary one. | — |
| 1 | Target Level | float | `-23.0` | -40 – 0 | LUFS | — | Target | yes | no | linear | 1 | Loudness the leveler steers towards. | — |
| 2 | Responsiveness | float | `0.5` | 0 – 1 | — | — | Response | yes | no | linear | 1 | How quickly the leveler follows the target. | — |
| 3 | Preserve Dynamics | float | `0.5` | 0 – 1 | — | — | Dynamics | yes | no | linear | 1 | Keeps the original level differences; less gain riding. | — |
| 4 | Ess Reduction | float | `0.0` | 0 – 12 | dB | — | Ess | yes | no | linear | 1 | Amount of sibilance reduction. | — |
| 5 | Limiter | float | `-1.0` | -12 – 0 | dBTP | — | Limiter | yes | no | linear | 1 | True-peak ceiling of the output. | — |

### Loudness Control (`AUR_LOUDNESS`)

Element `Loudness` · edition `standard` · 6 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Target Loudness | float | `-23.0` | -36 – 0 | LUFS | — | Target | yes | no | linear | 1 | Integrated loudness the programme is brought to. | — |
| 1 | Tolerance | float | `0.5` | 0.5 – 2 | LU | — | Target | yes | no | linear | 1 | Deviation that is accepted before the gain is moved. | — |
| 2 | Short Term Loudness | float | `-23.0` | -36 – 0 | LUFS | — | Target | yes | no | linear | 1 | Target for the three second short-term loudness. | — |
| 3 | Momentary Loudness | float | `-23.0` | -36 – 0 | LUFS | — | Target | yes | no | linear | 1 | Target for the 400 ms momentary loudness. | — |
| 4 | True Peak | float | `-1.0` | -9 – 0 | dBTP | — | Target | yes | no | linear | 1 | True-peak ceiling of the output. | — |
| 5 | Standard | enum | `EBU R128` | 0 – 9 | — | EBU R128, EBU R128 s1, BS.1770-2, BS.1770-4, ATSC A/85, ARIB TR-B32, AES TD1006.1.17-10, OP-59, Portaria 354, Custom | Target | yes | no | linear | 1 | Loudness standard; picking one overwrites the target fields. | — |

### Loudness Optimize (`AUR_LOUDNESSOPT`)

Element `LoudnessOptimize` · edition `standard` · 4 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Boost | float | `0.0` | 0 – 12 | dB | — | Boost | yes | no | linear | 1 | Maximum lift applied to quiet passages. | — |
| 1 | Threshold | float | `-12.0` | -30 – 0 | dBFS | — | Compressor | yes | no | linear | 1 | Level the quiet passages are lifted towards. | — |
| 2 | Ratio | float | `4.0` | 1 – 20 | — | — | Compressor | yes | no | linear | 1 | Compression ratio above the threshold. | — |
| 3 | Speed | float | `120.0` | 10 – 1000 | ms | — | Compressor | yes | no | linear | 1 | Time the level follower takes to settle. | — |

### EQ (`AUR_EQ`)

Element `ParametricEQ` · edition `standard` · 14 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Type | enum | `Analog IIR` | 0 – 1 | — | Analog IIR, Digital Linear Phase FIR | Algorithm | yes | no | linear | 1 | Filter topology used for the four bands. | — |
| 1 | Band 1 Frequency | float | `100.0` | 20 – 20000 | Hz | — | Band 1 | yes | no | log | 1 | Centre frequency of the band. | — |
| 2 | Band 1 Gain | float | `0.0` | -18 – 3.6 | dB | — | Band 1 | yes | no | taper | 0.264 | Gain of the band in decibels. | — |
| 3 | Band 1 Q | float | `1.0` | 0.1 – 20 | — | — | Band 1 | yes | no | log | 1 | Bandwidth of the band. | — |
| 4 | Band 2 Frequency | float | `1000.0` | 20 – 20000 | Hz | — | Band 2 | yes | no | log | 1 | Centre frequency of the band. | — |
| 5 | Band 2 Gain | float | `0.0` | -18 – 3.6 | dB | — | Band 2 | yes | no | taper | 0.264 | Gain of the band in decibels. | — |
| 6 | Band 2 Q | float | `1.0` | 0.1 – 20 | — | — | Band 2 | yes | no | log | 1 | Bandwidth of the band. | — |
| 7 | Band 3 Frequency | float | `5000.0` | 20 – 20000 | Hz | — | Band 3 | yes | no | log | 1 | Centre frequency of the band. | — |
| 8 | Band 3 Gain | float | `0.0` | -18 – 3.6 | dB | — | Band 3 | yes | no | taper | 0.264 | Gain of the band in decibels. | — |
| 9 | Band 3 Q | float | `1.0` | 0.1 – 20 | — | — | Band 3 | yes | no | log | 1 | Bandwidth of the band. | — |
| 10 | Band 4 Frequency | float | `10000.0` | 20 – 20000 | Hz | — | Band 4 | yes | no | log | 1 | Centre frequency of the band. | — |
| 11 | Band 4 Gain | float | `0.0` | -18 – 3.6 | dB | — | Band 4 | yes | no | taper | 0.264 | Gain of the band in decibels. | — |
| 12 | Band 4 Q | float | `1.0` | 0.1 – 20 | — | — | Band 4 | yes | no | log | 1 | Bandwidth of the band. | — |
| 13 | Output Gain | float | `0.0` | -18 – 3.6 | dB | — | Gain | yes | no | taper | 0.264 | Gain applied after the filter chain. | — |

### EQ Match (`AUR_EQMATCH`)

Element `MatchEQ` · edition `advanced` · 1 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Amount | float | `0.25` | 0 – 1 | — | — | Match | yes | no | linear | 1 | How much of the difference is applied. | — |

### Gain (`AUR_GAIN`)

Element `GainStage` · edition `standard` · 1 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Gain | float | `0.0` | -60 – 12 | dB | — | Gain | yes | no | taper | 0.264 | Output gain applied to the signal. | — |

### Fade (`AUR_FADE`)

Element `GainStage` · edition `standard` · 2 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Fade Type | enum | `Log` | 0 – 3 | — | Log, Linear, Cosine, Equal Power | Algorithm | yes | no | linear | 1 | Shape of the fade curve. | — |
| 1 | Fade Length | float | `1.0` | 0.01 – 30 | s | — | Fade | yes | no | linear | 1 | Duration of the fade-in and the fade-out. | — |

### Normalize (`AUR_NORMALIZE`)

Element `GainStage` · edition `standard` · 1 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Target Peak Level | float | `-1.0` | -24 – 0 | dBFS | — | Gain | yes | no | linear | 1 | Peak level the signal is normalised to. | — |

### Phase (`AUR_PHASE`)

Element `ChannelOperations` · edition `standard` · 2 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Rotation | float | `0.0` | -180 – 180 | deg | — | Phase | yes | no | linear | 1 | Phase rotation of the right channel relative to the left. | — |
| 1 | Adaptive Rotation | bool | `false` | 0 – 1 | — | — | Phase | yes | no | linear | 1 | Aligns the channels automatically by cross correlation. | — |

### Mixing (`AUR_MIXING`)

Element `ChannelOperations` · edition `standard` · 2 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Left Output Mix | float | `100.0` | 0 – 200 | % | — | Mix | yes | no | linear | 1 | Level of the left output bus (100 % = unity, 200 % = +6 dB). | — |
| 1 | Right Output Mix | float | `100.0` | 0 – 200 | % | — | Mix | yes | no | linear | 1 | Level of the right output bus (100 % = unity, 200 % = +6 dB). | — |

### Dither (`AUR_DITHER`)

Element `Dither` · edition `standard` · 4 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Bit Depth | enum | `16` | 0 – 4 | — | 24, 20, 16, 12, 8 | Output | yes | no | linear | 1 | Output word length. | — |
| 1 | Noise Shaping | enum | `None` | 0 – 6 | — | None, Shape 1, Shape 2, Shape 3, Shape 4, Shape 5, Ultra | Output | yes | no | linear | 1 | Error feedback filter applied to the quantisation error. | — |
| 2 | Dither Amount | enum | `Medium` | 0 – 3 | — | None, Low, Medium, High | Output | yes | no | linear | 1 | Amount of dither added to the signal. | — |
| 3 | Auto Blanking | enum | `If Quantized` | 0 – 2 | — | If Quantized, On Silence, Off | Output | yes | no | linear | 1 | When the dither is suppressed. | — |

### Resample (`AUR_RESAMPLE`)

Element `Resampler` · edition `standard` · 4 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Sample Rate | enum | `48000` | 0 – 7 | — | 22050, 32000, 44100, 48000, 88200, 96000, 176400, 192000 | Output | yes | no | linear | 1 | Sample rate the signal is converted to. | — |
| 1 | Filter Steepness | float | `0.5` | 0 – 1 | — | — | Filter | yes | no | linear | 1 | How sharp the anti-aliasing filter is. | — |
| 2 | Pre Ringing | float | `0.0` | 0 – 1 | — | — | Filter | yes | no | linear | 1 | Zero is a minimum phase kernel, one a linear phase kernel. | — |
| 3 | Post Limiter | bool | `false` | 0 – 1 | — | — | Output | yes | no | linear | 1 | Catches peaks that appear after the conversion. | — |

### Time & Pitch (`AUR_TIMEPITCH`)

Element `TimeStretchPitchShift` · edition `standard` · 7 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Algorithm | enum | `Radius` | 0 – 2 | — | Radius, Radius RT, Solo | Algorithm | yes | no | linear | 1 | Variant of the phase vocoder. | — |
| 1 | Stretch Ratio | float | `1.0` | 0.125 – 8 | — | — | Time | yes | no | linear | 1 | Duration multiplier; two means twice as long. | — |
| 2 | Pitch Shift | float | `0.0` | -24 – 24 | st | — | Pitch | yes | no | linear | 1 | Pitch change in semitones. | — |
| 3 | Adaptive Window | float | `37.0` | 10 – 120 | ms | — | Quality | yes | no | linear | 1 | Length of the analysis window. | — |
| 4 | Transient Sensitivity | float | `1.0` | 0 – 2 | — | — | Quality | yes | no | linear | 1 | How strongly attacks are detected and kept sharp. | — |
| 5 | Phase Coherence | float | `0.5` | 0 – 1 | — | — | Quality | yes | no | linear | 1 | Amount of identity phase locking. | — |
| 6 | Shift Formants | bool | `false` | 0 – 1 | — | — | Pitch | yes | no | linear | 1 | Keeps the vocal character when the pitch is shifted. | — |

### Variable Pitch (`AUR_VARIPITCH`)

Element `PitchContour` · edition `standard` · 4 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Contour | float | `0.0` | -24 – 24 | st | — | Contour | yes | no | linear | 1 | Pitch of the contour in semitones. | — |
| 1 | Smoothing | float | `0.5` | 0 – 1 | — | — | Contour | yes | no | linear | 1 | How much the phase is locked while the pitch moves. | — |
| 2 | Preserve Time | bool | `true` | 0 – 1 | — | — | Pitch | yes | no | linear | 1 | Keeps the duration while the pitch moves. | — |
| 3 | Phase Coherence | float | `0.5` | 0 – 1 | — | — | Quality | yes | no | linear | 1 | Amount of identity phase locking. | — |

### Variable Time (`AUR_VARTIME`)

Element `VariableTime` · edition `standard` · 3 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Ratio | float | `1.0` | 0.125 – 8 | — | — | Time | yes | no | linear | 1 | Duration multiplier; two means twice as long. | — |
| 1 | Smoothing | float | `0.5` | 0 – 1 | — | — | Time | yes | no | linear | 1 | How strongly the phase of neighbouring peaks is locked. | — |
| 2 | Phase Coherence | float | `0.5` | 0 – 1 | — | — | Quality | yes | no | linear | 1 | Amount of identity phase locking. | — |

### Interpolate (`AUR_INTERPOLATE`)

Element `Interpolate` · edition `standard` · 1 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Quality | int | `3` | 1 – 8 | — | — | Quality | yes | no | linear | 1 | Order of the interpolation used to replace the damaged samples. | — |

### Signal Generator (`AUR_SIGNALGEN`)

Element `SignalGenerator` · edition `standard` · 5 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Tone Shape | enum | `Sine` | 0 – 4 | — | Sine, Triangle, Saw, Square, Pulses | Generator | yes | no | linear | 1 | Shape of the generated tone. | — |
| 1 | Frequency | float | `440.0` | 20 – 20000 | Hz | — | Generator | yes | no | log | 1 | Frequency of the generated tone. | — |
| 2 | Amplitude | float | `-18.0` | -96 – 0 | dBFS | — | Generator | yes | no | linear | 1 | Peak level of the generated tone. | — |
| 3 | Noise Type | enum | `White Gaussian` | 0 – 5 | — | White Gaussian, White Triangular, White Uniform, White Binary, Pink, Brown | Generator | yes | no | linear | 1 | Flavour of the generated noise. | — |
| 4 | Level | float | `-60.0` | -96 – 0 | dBFS | — | Generator | yes | no | linear | 1 | Level of the generated noise. | — |

### Streaming Preview (`AUR_STREAMPREVIEW`)

Element `StreamingPreview` · edition `standard` · 4 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Playback Loudness | float | `-16.0` | -36 – 0 | LUFS | — | Preview | yes | no | linear | 1 | Loudness the preview is played back at. | — |
| 1 | Apply Positive Gain | bool | `true` | 0 – 1 | — | — | Preview | yes | no | linear | 1 | Allows the preview to be louder than the incoming signal. | — |
| 2 | Codec | enum | `Lossless` | 0 – 4 | — | Lossless, OGG, MP3, AAC, OPUS | Preview | yes | no | linear | 1 | Delivery format the preview imitates. | — |
| 3 | Quality | enum | `Medium` | 0 – 2 | — | Low, Medium, High | Preview | yes | no | linear | 1 | Quality of the delivery format. | — |

## Measurement

### Find Similar (`AUR_FINDSIMILAR`)

Element `FindSimilar` · edition `standard` · 1 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Similarity | float | `0.8` | 0 – 1 | — | — | Search | yes | no | linear | 1 | Minimum score a region has to reach to be reported as a match. | — |

### Clip Classifier (`AUR_CLIPCLASSIFIER`)

Element `ClipClassifier` · edition `standard` · 5 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Detected Class | enum | `Piano` | 0 – 9 | — | Piano, Bass, Drums, Guitar, Vocals, Synth, Strings, Woodwind, Brass, Organ | Result | yes | no | linear | 1 | The source family the classifier believes dominates the clip. | — |
| 1 | Confidence | float | `0.0` | 0 – 1 | — | — | Result | yes | no | linear | 1 | How strongly the clip matches the detected class. | — |
| 2 | Similarity | float | `0.0` | 0 – 1 | — | — | Result | yes | no | linear | 1 | Normalised distance to the best-matching reference. | Match Strength |
| 3 | Highlight Match | bool | `false` | 0 – 1 | — | — | Display | yes | no | linear | 1 | Flag the detected class for the interface to highlight. | — |
| 4 | Analysis Length | float | `4.0` | 0.5 – 30 | s | — | Analysis | yes | no | linear | 1 | How much audio from the start of the clip is analysed. | — |

## System

### Connect (`AUR_CONNECT`)

Element `Connect` · edition `standard` · 2 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Auto Send | bool | `false` | 0 – 1 | — | — | Transfer | yes | no | linear | 1 | Send every processed block to the host application automatically. | — |
| 1 | Keep Source | bool | `true` | 0 – 1 | — | — | Transfer | yes | no | linear | 1 | Keep the source material in the timeline next to the edit. | — |

### Monitor (`AUR_MONITOR`)

Element `Monitor` · edition `standard` · 1 parameters.

| Id | Parameter | Type | Default | Range | Unit | Choices | Group | Automatable | Stepped | Curve | Exponent | Help | Aliases |
|---:|---|---|---|---|---|---|---|---|---|---|---|:---|:---|
| 0 | Monitor Loudness | bool | `true` | 0 – 1 | — | — | Monitor | yes | no | linear | 1 | Run the BS.1770-4 loudness analysis. | — |
