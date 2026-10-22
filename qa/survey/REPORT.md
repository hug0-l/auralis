# Auralis — the module survey

**51 modules measured — 50 pass every rule, 1 is listed with a defect that was already known, 0 are listed with a defect the survey found, 0 fail.**  What every module of the catalogue does to a signal, and what it costs.

1 defect is listed in `qa/survey/exceptions.py`, each of them with a finding file in `var/patches/b12/` (§5 and §6).  A module that is listed may be as bad as the listed number and no worse; a module that is not listed has to satisfy every rule.

The measurement code is in `qa/survey/`, the machine readable result is `qa/survey/report.json`, and the gate is `tests/test_module_survey.py`.

## 0. How this report was produced

```
python qa/survey/survey.py --all
```

The runner measures every module of the registry, writes this report and writes `qa/survey/report.json`.  It takes no other option: `--all` is the whole survey, and there is no subset of it that would answer the questions of this report.  A single module can be measured with `python qa/survey/survey.py --module AUR_DECLICK`, which does not write the report.

Measured on Windows-11-10.0.26300-SP0 with Python 3.15.0 and NumPy 2.5.4 on 2026-10-22 01:02, the machine was 49.8 % busy while it ran.

> The cost column is the cost of the block **on the reference machine**: every block is timed next to a batch of 1024 point transforms of the machine that measures it, and the ratio of the two — which is the number of transform equivalents the block costs — does not move with the load of the machine.  The result is expressed in the microseconds of the reference machine (8.300 us per transform), the yardstick of the performance table (`qa/performance/budgets.py`), so the budget of 1333.3 us is the same requirement in both tables.  The number is the fastest of five passes of fifteen timed blocks after three warm up blocks, so it is the least disturbed observation of the run.

## 1. How the modules are measured

| item | value |
|---|---|
| sample rate | 48 000 Hz |
| channels | 2 |
| block size of the cost measurement | 64 samples |
| realtime budget of a 64 sample block | 1333.3 us |
| probes | 34 signals: 8 of them are clips of `corpus/audio/test`, the other 26 are synthetic signals built for the purpose |
| the wanted part | the programme material of the probe, in a frequency band that carries nothing but the programme |
| the unwanted part | the defect of the probe, in a band that carries nothing but the defect |
| the cost | the fastest of five passes of 15 timed blocks after 3 warm up blocks, the median of the pass, every block timed next to a batch of 1024 point transforms and expressed in the microseconds of the reference machine (8.300 us per transform) |
| the latency | the delay of the output against the input, from the peak of the cross correlation of a broadband noise burst, searched over the whole range the catalogue can declare |
| the block delta | the RMS difference between the one block render and the 64 sample render, aligned by the delay; a block coherent module is at or below -40 dB |
| the runaway | how much louder the 64 sample render is than the input; a module may not be more than 24 dB louder |
| the allocator check | the module is run twice, once with a clean heap and once with a poisoned heap; the two outputs have to be identical bit for bit |
| the determinism check | the module is run twice; the two outputs have to be identical bit for bit |

The budget of the wanted part is a property of the module, not of the measurement: it is written down in `qa/survey/specs.py` and the gate holds every module to it.  A module that breaks a rule is listed in `qa/survey/exceptions.py` with the number that was measured and the reason; the gate then holds it to the listed number instead of to the rule, so a repair cannot make the defect worse unnoticed.

## 2. The table

One row per module.  *keeps the wanted part* is the change of the programme: negative is damage.  *removes the unwanted part* is the change of the defect: negative is the job being done, positive means the module *added* something (Dither, Signal Generator, Spectral Recovery and EQ Match are supposed to do that).

| # | module | name | policy | keeps the wanted part (dB) | removes the unwanted part (dB) | cost us | budget % | latency (samples) | block delta (dB) | runaway (dB) | finite | same length | alloc | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `AUR_AMBIENCEMATCH` | Ambience Match | restoration | 0.01 | -10.76 | 456 | 34% | 0 | -21.8 | 0 | yes | yes | yes | PASS |
| 2 | `AUR_AZIMUTH` | Azimuth | transform | 0.00 | — | 71 | 5% | 0 | -172.8 | 0 | yes | yes | yes | PASS |
| 3 | `AUR_BANDRESTORATION` | Band Restoration | restoration | -0.00 | -9.58 | 128 | 10% | 9 | -168.0 | -0 | yes | yes | yes | PASS |
| 4 | `AUR_BREATH` | Breath Control | restoration | -0.00 | -7.62 | 39 | 3% | 1024 | -299.3 | -7 | yes | yes | yes | PASS |
| 5 | `AUR_CENTEREXTRACT` | Center Extract | restoration | -0.34 | -15.62 | 14 | 1% | 1024 | -170.6 | -2 | yes | yes | yes | PASS |
| 6 | `AUR_DEBLEED` | De-Bleed | restoration | 0.00 | -54.43 | 373 | 28% | 0 | -10.8 | -1 | yes | yes | yes | PASS |
| 7 | `AUR_DECLICK` | De-Click | restoration | -0.01 | -38.00 | 23 | 2% | 1664 | -136.8 | -40 | yes | yes | yes | PASS |
| 8 | `AUR_DECLIP` | De-Clip | restoration | -1.88 | -25.27 | 222 | 17% | 0 | -10.2 | 0 | yes | yes | yes | PASS |
| 9 | `AUR_DECRACKLE` | De-Crackle | restoration | -0.00 | -4.89 | 11 | 1% | 1664 | -150.6 | -5 | yes | yes | yes | PASS |
| 10 | `AUR_DEESS` | De-Ess | restoration | -0.00 | -14.79 | 43 | 3% | 1024 | -161.9 | -7 | yes | yes | yes | PASS |
| 11 | `AUR_DEHUM` | De-Hum | restoration | -0.04 | -36.03 | 113 | 8% | 0 | -20.8 | 0 | yes | yes | yes | PASS |
| 12 | `AUR_DEPLOSIVE` | De-Plosive | restoration | -0.00 | -22.85 | 174 | 13% | 512 | -153.4 | -10 | yes | yes | yes | PASS |
| 13 | `AUR_DEREVERB` | De-Reverb | restoration | 0.00 | -6.02 | 36 | 3% | 2048 | -148.5 | 0 | yes | yes | yes | PASS |
| 14 | `AUR_DERUSTLE` | De-Rustle | restoration | -0.00 | -3.70 | 42 | 3% | 2048 | -170.6 | -0 | yes | yes | yes | PASS |
| 15 | `AUR_DEWIND` | De-Wind | restoration | -2.47 | -3.72 | 54 | 4% | 1024 | -167.2 | -1 | yes | yes | yes | PASS |
| 16 | `AUR_DECONSTRUCT` | Deconstruct | transform | -3.02 | -8.23 | 53 | 4% | 2048 | -156.6 | -9 | yes | yes | yes | PASS |
| 17 | `AUR_DIALOGUEISOLATE` | Dialogue Isolate | transform | -0.37 | -12.04 | 39 | 3% | 4096 | -161.2 | -7 | yes | yes | yes | PASS |
| 18 | `AUR_GUITARDENOISE` | Guitar De-Noise | restoration | -0.18 | -11.77 | 311 | 23% | 0 | -10.2 | 1 | yes | yes | yes | PASS |
| 19 | `AUR_MOUTHDECLICK` | Mouth De-Click | restoration | -0.01 | -40.00 | 12 | 1% | 1664 | -136.8 | -40 | yes | yes | yes | PASS |
| 20 | `AUR_MUSICREBALANCE` | Music Rebalance | transform | 0.00 | -12.00 | 764 | 57% | 0 | -5.3 | -0 | yes | yes | yes | PASS |
| 21 | `AUR_REPAIRASSISTANT` | Repair Assistant | restoration | -0.43 | -9.19 | 416 | 31% | 0 | -8.3 | -1 | yes | yes | yes | PASS |
| 22 | `AUR_SPATIALSUPPRESSOR` | Spatial Suppressor | restoration | -0.01 | -94.14 | 198 | 15% | 512 | -161.9 | -2 | yes | yes | yes | PASS |
| 23 | `AUR_SPECTRALDENOISE` | Spectral De-Noise | restoration | -3.72 | -41.60 | 83 | 6% | 1440 | -54.3 | 0 | yes | yes | yes | EXCEPTION |
| 24 | `AUR_SPECTRALEDITOR` | Spectral Editor | transform | -0.00 | — | 5 | 0% | 0 | -147.4 | 0 | yes | yes | yes | PASS |
| 25 | `AUR_SPECTRALRECOVERY` | Spectral Recovery | transform | -0.00 | 0.00 | 403 | 30% | 0 | -23.9 | 0 | yes | yes | yes | PASS |
| 26 | `AUR_SPECTRALREPAIR` | Spectral Repair | transform | -0.00 | — | 231 | 17% | 0 | -173.8 | 0 | yes | yes | yes | PASS |
| 27 | `AUR_VOICEDENOISE` | Voice De-Noise | restoration | -0.01 | -17.99 | 21 | 2% | 2047 | -314.0 | -0 | yes | yes | yes | PASS |
| 28 | `AUR_VOICEREVERB` | Voice Reverb | restoration | -1.67 | -11.16 | 21 | 2% | 1024 | -329.2 | 0 | yes | yes | yes | PASS |
| 29 | `AUR_WOWFLUTTER` | Wow & Flutter | restoration | 0.00 | -6.51 | 250 | 19% | 974 | -239.2 | -0 | yes | yes | yes | PASS |
| 30 | `AUR_DITHER` | Dither | transform | 0.00 | 72.90 | 27 | 2% | 0 | -86.6 | 0 | yes | yes | yes | PASS |
| 31 | `AUR_EQ` | EQ | transform | 0.12 | 3.00 | 43 | 3% | 0 | -174.8 | 2 | yes | yes | yes | PASS |
| 32 | `AUR_EQMATCH` | EQ Match | transform | 0.91 | 11.42 | 18 | 1% | 0 | -174.0 | 6 | yes | yes | yes | PASS |
| 33 | `AUR_FADE` | Fade | transform | 0.00 | -53.70 | 16 | 1% | 0 | -170.1 | 0 | yes | yes | yes | PASS |
| 34 | `AUR_GAIN` | Gain | transform | -6.00 | — | 5 | 0% | 0 | -165.0 | -6 | yes | yes | yes | PASS |
| 35 | `AUR_INTERPOLATE` | Interpolate | restoration | 0.00 | 11.44 | 639 | 48% | 0 | -22.0 | 0 | yes | yes | yes | PASS |
| 36 | `AUR_LEVELER` | Leveler | transform | -17.07 | — | 103 | 8% | 4848 | -153.0 | -17 | yes | yes | yes | PASS |
| 37 | `AUR_LOUDNESS` | Loudness Control | transform | -17.07 | — | 100 | 7% | 4848 | -153.0 | -17 | yes | yes | yes | PASS |
| 38 | `AUR_LOUDNESSOPT` | Loudness Optimize | transform | -4.50 | — | 29 | 2% | 0 | -64.9 | -4 | yes | yes | yes | PASS |
| 39 | `AUR_MIXING` | Mixing | transform | 0.00 | — | 14 | 1% | 0 | -172.8 | 0 | yes | yes | yes | PASS |
| 40 | `AUR_NORMALIZE` | Normalize | transform | 5.00 | — | 7 | 1% | 0 | -176.0 | 5 | yes | yes | yes | PASS |
| 41 | `AUR_PHASE` | Phase | transform | 0.00 | — | 4 | 0% | 0 | -172.8 | 0 | yes | yes | yes | PASS |
| 42 | `AUR_RESAMPLE` | Resample | transform | -0.01 | — | 59 | 4% | 48 | -171.0 | 0 | yes | yes | yes | PASS |
| 43 | `AUR_SIGNALGEN` | Signal Generator | transform | 0.00 | 160.75 | 15 | 1% | 0 | -9.3 | 2 | yes | yes | yes | PASS |
| 44 | `AUR_STREAMPREVIEW` | Streaming Preview | transform | -10.07 | — | 9 | 1% | 4800 | -160.0 | -10 | yes | yes | yes | PASS |
| 45 | `AUR_TIMEPITCH` | Time & Pitch | transform | 0.00 | — | 6 | 0% | 0 | -171.0 | 0 | yes | yes | yes | PASS |
| 46 | `AUR_VARIPITCH` | Variable Pitch | transform | 0.00 | — | 4 | 0% | 0 | -171.0 | 0 | yes | yes | yes | PASS |
| 47 | `AUR_VARTIME` | Variable Time | transform | 0.00 | — | 3 | 0% | 0 | -171.0 | 0 | yes | yes | yes | PASS |
| 48 | `AUR_CLIPCLASSIFIER` | Clip Classifier | passthrough | 0.00 | — | 14 | 1% | 0 | -160.0 | 0 | yes | yes | yes | PASS |
| 49 | `AUR_FINDSIMILAR` | Find Similar | passthrough | 0.00 | — | 3 | 0% | 0 | -160.0 | 0 | yes | yes | yes | PASS |
| 50 | `AUR_CONNECT` | Connect | passthrough | 0.00 | — | 5 | 0% | 0 | -171.0 | 0 | yes | yes | yes | PASS |
| 51 | `AUR_MONITOR` | Monitor | passthrough | 0.00 | — | 105 | 8% | 0 | -171.0 | 0 | yes | yes | yes | PASS |

## 3. The latency of every module

The delay a module puts into the signal is measured on a broadband noise burst: the cross correlation of noise has one peak, so the peak is the delay, and the search does not have to believe the module.  The range that is searched is not a constant: it is the largest latency any module of the catalogue declares (4848 samples for `AUR_LEVELER`) plus a margin of 2048 samples, so the search covers **every delay the product can declare**: 6896 samples, 143.7 ms at 48 kHz.  The margin is one window of the analysis of the core (2048 samples), so a module whose declaration is off by a whole analysis window is measured inside the search and not missed.

The rule of the gate: the delay a module declares and the delay that was measured may not differ by more than the tolerance of the module.  A module that declares a latency must delay the signal by exactly that latency — the tolerance is 2 samples (41.7 us at 48 kHz), because the delay line of a module has a fixed length and the measurement resolves it to the sample.  A module that declares no latency may not delay the signal by more than a block (64 samples, 1.33 ms at 48 kHz), because the cross correlation of a filtered signal peaks at the group delay of the filter and not at a latency.  A module whose delay is not a constant — a module that warps the time axis — carries the tolerance it needs in `qa/survey/specs.py`.

| module | the delay it declares | the delay that was measured | the difference | the tolerance | the verdict |
|---|---|---|---|---|---|
| `AUR_AMBIENCEMATCH` | 0 | 0 | +0 | 64 | PASS |
| `AUR_AZIMUTH` | 0 | 0 | +0 | 64 | PASS |
| `AUR_BANDRESTORATION` | 0 | 9 | +9 | 64 | PASS |
| `AUR_BREATH` | 1024 | 1024 | +0 | 2 | PASS |
| `AUR_CENTEREXTRACT` | 1024 | 1024 | +0 | 2 | PASS |
| `AUR_DEBLEED` | 0 | 0 | +0 | 64 | PASS |
| `AUR_DECLICK` | 1664 | 1664 | +0 | 2 | PASS |
| `AUR_DECLIP` | 0 | 0 | +0 | 64 | PASS |
| `AUR_DECRACKLE` | 1664 | 1664 | +0 | 2 | PASS |
| `AUR_DEESS` | 1024 | 1024 | +0 | 2 | PASS |
| `AUR_DEHUM` | 0 | 0 | +0 | 64 | PASS |
| `AUR_DEPLOSIVE` | 512 | 512 | +0 | 2 | PASS |
| `AUR_DEREVERB` | 2048 | 2048 | +0 | 2 | PASS |
| `AUR_DERUSTLE` | 2048 | 2048 | +0 | 2 | PASS |
| `AUR_DEWIND` | 1024 | 1024 | +0 | 2 | PASS |
| `AUR_DECONSTRUCT` | 2048 | 2048 | +0 | 2 | PASS |
| `AUR_DIALOGUEISOLATE` | 4096 | 4096 | +0 | 2 | PASS |
| `AUR_GUITARDENOISE` | 0 | 0 | +0 | 64 | PASS |
| `AUR_MOUTHDECLICK` | 1664 | 1664 | +0 | 2 | PASS |
| `AUR_MUSICREBALANCE` | 0 | 0 | +0 | 64 | PASS |
| `AUR_REPAIRASSISTANT` | 0 | 0 | +0 | 64 | PASS |
| `AUR_SPATIALSUPPRESSOR` | 512 | 512 | +0 | 2 | PASS |
| `AUR_SPECTRALDENOISE` | 1440 | 1440 | +0 | 2 | EXCEPTION |
| `AUR_SPECTRALEDITOR` | 0 | 0 | +0 | 64 | PASS |
| `AUR_SPECTRALRECOVERY` | 0 | 0 | +0 | 64 | PASS |
| `AUR_SPECTRALREPAIR` | 0 | 0 | +0 | 64 | PASS |
| `AUR_VOICEDENOISE` | 2047 | 2047 | +0 | 2 | PASS |
| `AUR_VOICEREVERB` | 1024 | 1024 | +0 | 2 | PASS |
| `AUR_WOWFLUTTER` | 1024 | 974 | -50 | 64 | PASS |
| `AUR_DITHER` | 0 | 0 | +0 | 64 | PASS |
| `AUR_EQ` | 0 | 0 | +0 | 64 | PASS |
| `AUR_EQMATCH` | 0 | 0 | +0 | 64 | PASS |
| `AUR_FADE` | 0 | 0 | +0 | 64 | PASS |
| `AUR_GAIN` | 0 | 0 | +0 | 64 | PASS |
| `AUR_INTERPOLATE` | 0 | 0 | +0 | 64 | PASS |
| `AUR_LEVELER` | 4848 | 4848 | +0 | 2 | PASS |
| `AUR_LOUDNESS` | 4848 | 4848 | +0 | 2 | PASS |
| `AUR_LOUDNESSOPT` | 0 | 0 | +0 | 64 | PASS |
| `AUR_MIXING` | 0 | 0 | +0 | 64 | PASS |
| `AUR_NORMALIZE` | 0 | 0 | +0 | 64 | PASS |
| `AUR_PHASE` | 0 | 0 | +0 | 64 | PASS |
| `AUR_RESAMPLE` | 48 | 48 | +0 | 2 | PASS |
| `AUR_SIGNALGEN` | 0 | 0 | +0 | 64 | PASS |
| `AUR_STREAMPREVIEW` | 4800 | 4800 | +0 | 2 | PASS |
| `AUR_TIMEPITCH` | 0 | 0 | +0 | 64 | PASS |
| `AUR_VARIPITCH` | 0 | 0 | +0 | 64 | PASS |
| `AUR_VARTIME` | 0 | 0 | +0 | 64 | PASS |
| `AUR_CLIPCLASSIFIER` | 0 | 0 | +0 | 64 | PASS |
| `AUR_FINDSIMILAR` | 0 | 0 | +0 | 64 | PASS |
| `AUR_CONNECT` | 0 | 0 | +0 | 64 | PASS |
| `AUR_MONITOR` | 0 | 0 | +0 | 64 | PASS |

## 4. The summary

| | count |
|---|---|
| modules measured | 51 |
| pass every rule | 50 |
| listed with a defect that was already known | 1 |
| listed with a defect the survey found | 0 |
| fail a rule | 0 |
| over the 64 sample budget | 0 |
| finite output | 51 |
| output as long as the input | 51 |
| output independent of the allocator | 51 |
| two runs of a module are the same | 51 |
| the declared latency is the measured latency | 51 |
| block coherent | 41 |

### The modules over the 64 sample budget

A module that is over the budget is held at a **debt ceiling** — the measured cost plus 50 % — until its owner brings it down.  A module that is inside the budget is held to the budget itself.

| module | cost us | budget % | the ceiling | the ceiling in us |
|---|---|---|---|---|
| — | — | — | no module was measured over the budget | — |

### The biggest misses of a rule

The biggest misses of a rule, in the order of the size of the miss.  A module that is listed with a defect is measured against the rule, not against the listed number.  The cost is expressed in the decibels of the budget the module misses.

| module | rule | how far it misses |
|---|---|---|
| `AUR_SPECTRALDENOISE` | 480 Hz partial: the wanted part | 2.72 dB — -3.72 dB of the wanted part are lost, the budget is -1.00 dB |

## 5. The open defects

Every entry of `qa/survey/exceptions.py`: the module may be as bad as the listed number, and no worse.  When the module is repaired the entry is deleted and the gate holds it to the rule.  Every entry has a finding file that carries the numbers.

| module | what | measured | allowed | the finding file | why |
|---|---|---|---|---|---|
| `AUR_SPECTRALDENOISE` | keep | -3.86 | -3.86 | `var/patches/b12/spectral_de_noise.md` | known, in the team brief as defect 1: a wanted partial is crushed by the noise law.  The patch of the study (qa/quality/spectral_de_noise.patch) was applied to an earlier version of the module and is history now: it does not apply to the current file, and the defect is still open — the 480 Hz partial lost 6.46 dB before the module was rewritten and loses 3.86 dB now.  The module was being rewritten while the survey ran, so the number has to be taken again when the rewrite lands.  The cause is in qa/quality/STUDY.md |

## 6. The findings

One file per module that broke a rule, with the numbers, so that the owner of the module can pick it up.  The modules are not touched by the survey.  A finding whose module has been repaired since is marked *closed* and says what the survey measures now.

| finding | the module | the state |
|---|---|---|
| `var/patches/b12/ambience_match.md` | `AUR_AMBIENCEMATCH` | closed |
| `var/patches/b12/deconstruct.md` | `AUR_DECONSTRUCT` | closed |
| `var/patches/b12/dialogue_isolate.md` | `AUR_DIALOGUEISOLATE` | closed |
| `var/patches/b12/eq_match.md` | `AUR_EQMATCH` | closed |
| `var/patches/b12/guitar_de_noise.md` | `AUR_GUITARDENOISE` | closed |
| `var/patches/b12/music_rebalance.md` | `AUR_MUSICREBALANCE` | open |
| `var/patches/b12/spectral_de_noise.md` | `AUR_SPECTRALDENOISE` | open |

## 7. The files

| file | what it is |
|---|---|
| `qa/survey/harness.py` | the measurement library: region levels, the cost, the latency, the block coherence, the allocator check |
| `qa/survey/probes.py` | the 31 probe signals |
| `qa/survey/specs.py` | the specification of every module: the probe, the regions, the budgets |
| `qa/survey/exceptions.py` | the known open defects |
| `qa/survey/survey.py` | the runner; it writes this report and `report.json` |
| `qa/survey/report.json` | the machine readable result |
| `tests/test_module_survey.py` | the gate |
