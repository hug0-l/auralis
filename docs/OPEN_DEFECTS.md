# Auralis — the open defects of the measurement bridge

This is the list of the defects that the last measurement set of the study
shows in **our own modules**.  It is the companion of
[`qa/benchmark/reference_bridge.py`](../qa/benchmark/reference_bridge.py), the
file that carries the numbers, and it is the list that
`tests/test_benchmark_bridge.py` holds against those numbers: every defect
below is a rule of the product that the numbers break, and the gate of the
product that holds the rule is named in the row.

`docs/ALPHA_STATUS.md` carries the state of the build and the defects that
were found and fixed while it was built.  This list is the other half: the
defects that are **still open** after the last measurement of the study, with
the number that shows them.

The tree was being worked on while this list was written, so every row says
when it was measured.  The numbers are the ones of the study's table; the
gate state is the state of the gate at the moment named in the row.

## The three open defects

| # | module | case | the rule it breaks | the number | the gate | the state |
|---|---|---|---|---|---|---|
| 1 | `AUR_SPECTRALDENOISE` | `voice_hiss` | the wanted partial may move by at most **1.00 dB** and the noise has to lose **12.00 dB** (`qa/quality/quality_rules.py`) | the partial moves **9.16 dB**; the noise goes down 42.55 dB | `tests/test_quality_spectral.py` | **an expected failure, not a pass**: the rule sits in the table of the unmet rules of the gate under `aur_spectraldenoise_voice_hiss` and carries a strict `xfail` marker, so the marker turns into a failure the day the module reaches the target |
| 2 | `AUR_SPECTRALDENOISE` | `music_mixture` | the same rule on the music case: a wanted partial may move by at most **1.00 dB** | the 660 Hz note moves **1.68 dB**; the bed goes down 12.83 dB | `tests/test_quality_spectral.py` | **an expected failure, not a pass**: the rule sits under `aur_spectraldenoise_music_mixture` with a strict `xfail` marker |
| 3 | `AUR_DECLICK` | `transient` | an impulsive artefact has to lose **20.00 dB**, and the programme around it may move by at most **1.00 dB** | the artefact is left at **-2.20 dB** (it was not taken out of the signal); the programme around it moves 0.61 dB, which is inside its budget | `tests/test_quality_spectral.py` | **an expected failure, not a pass**: the rule sits under `aur_declick_transient` with a strict `xfail` marker |

All three were re-measured on the tree of today (23:05) with the instrument
of the study and came out the same, to the last digit, and the gate still
marks them as expected failures.

## The defect the tree closed while this list was being written

| # | module | case | the rule it broke | the number of the study | the gate | the state |
|---|---|---|---|---|---|---|
| 4 | `AUR_DECLIP` | `clipped` | the wanted partial may move by at most **2.50 dB**, and the restoration has to come **6.00 dB** closer to the truth | the partial moved **3.25 dB**, and the module came **5.32 dB** closer to the truth | `tests/test_quality_spectral.py` | **not marked at all**: the gate asserted the rule, so the gate **failed** on it (22:30, one failure, `aur_declip_clipped`).  The tree closed it while this list was being written (the ground truth of the clipped case was settled: its truth peaked at 1.000000 and had peaked at 1.242148, a level no file can hold; the rule of the case says so in `qa/quality/quality_rules.py`).  The same instrument now measures **14.95 dB** closer to the truth and **1.37 dB** of damage at 1 kHz, and the gate passes again (23:05) |

The number of the study stays in the constants file: it is what the study
measured, and a measurement is not changed by a later one.  The gate, not the
constants, says whether the defect is still open.

## The one that is not a defect

The bridge also reports a failure of `AUR_DIALOGUEISOLATE` on the voice case
`voice_hiss`: the hiss band does not come down at all (**0.00 dB**), and the
rule of the module asks for **6.00 dB**.  That number is a measurement of the
module at **its own default settings**, and the rule of the module asks for
the noise gain at **-12 dB**.  Measured at the setting of the rule the same
instrument gives **8.22 dB** of hiss removed, the voice band moves by
0.01 dB and the gain chatters by 0.38 dB, and the gate holds the rule.  There
is no defect here: the number belongs to the setting, and the constants of
the bridge carry both settings side by side.

## How the numbers were taken

Every number above was measured with the product's **own** code and judged by
the product's **own** rules, on both sides of the comparison:

* the ground truth cases (`qa/quality/ground_truth.py`) are measured with
  `qa/quality/measurement.py` and judged by `qa/quality/quality_rules.py`;
* the voice cases (`qa/qualityvoice/harness.py`) are measured with
  `qa/qualityvoice/harness.py` and judged by `qa/qualityvoice/rules.py`;
* the clips of the corpus (`corpus/manifest.json`) are measured with
  `corpus/measure.py`.

Our own module is rendered by the product's own engine and the reference is
rendered through the study's own host, and the measurement of the two sides
uses the same code, so the two cannot drift apart.  The self check of the
bridge proves it: for every rule of the product the measurement of the bridge
and the measurement of `qa/quality/measurement.py` agree **to the last bit**.

The numbers of this list were measured on the tree of the study's day and
measured again on the tree of today with the same instrument: every figure
came out the same, to the last digit.  The constants that carry them are in
`qa/benchmark/reference_bridge.py`.

## What a marker means

A rule that is not met yet is marked `xfail(strict=True)` in the gate that
holds it.  A strict marker is a **promise to look at the defect**: the day
the module reaches the number, the marker turns into a failure and the
marker has to be removed.  A green suite therefore always means "the rule is
not met yet", never "nobody looked".

Row 4 of the table above is the case where there was no marker: the rule was
asserted, the gate failed, and the failure was the report.  It is the one
defect of the four that was not a promise but a failure, and the tree closed
it while this list was being written.
