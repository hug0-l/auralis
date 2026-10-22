# The loudness meter against the published standards

`aurcore/dsp/loudness.py` is what the batch runner reports for every
delivered file, so a change in it moves every number the project delivers.
This report is the measurement of that meter against the documents it names:
ITU-R BS.1770-4 (Annex 1 and Annex 2), EBU Tech 3341 (the 'EBU mode'
compliance tests) and EBU Tech 3342 (the loudness range measure).

The numbers below come from the harness in this folder:

    python qa/loudness/harness.py

It prints the same tables, with the same tolerances, and it exits with 1
when a case falls outside its published tolerance.  The current state is
**45 of 45 cases inside the published tolerances**.

The tables below are the run of **2026-10-21, 16:05**, on Windows 11 with
Python 3.15.0 and NumPy 2.5.4.  The harness is deterministic: every case is
built from a seeded generator, so the numbers are the same on every run.

## The headline

| measurement | published | measured | error |
|---|---|---|---|
| integrated loudness, stereo 1 kHz at the reference level | -23.0 LUFS ±0.1 | **-22.9933 LUFS** | **+0.0067 LU** |
| momentary loudness, same signal | -23.0 LUFS ±0.1 | **-22.9933 LUFS** | **+0.0067 LU** |
| short-term loudness, same signal | -23.0 LUFS ±0.1 | **-22.9933 LUFS** | **+0.0067 LU** |
| true peak of a signal that is full scale in the samples | 0.0 dBTP | **0.0000 dBTP** | **0.0000 dB** |
| loudness range of a programme with one level | 0.0 LU | **0.0000 LU** | **0.0000 LU** |

The reference level is read 0.0067 LU high, which is the K-weighting gain
of the filter at 1 kHz against the -0.691 offset of BS.1770-4.  The
recommendation itself says that the offset "cancels out the K-weighting
gain for 997 Hz", so a small positive error at 1 kHz is the expected
behaviour, not a defect.

## The measurement table

Tolerances are the published ones: 0.1 LU for the loudness of EBU mode,
+0.2 / -0.4 dBTP for the true peak, 1 LU for the loudness range.

| case | source | measured | expected | error | tolerance |
|---|---|---|---|---|---|
| K-weighting coefficients at 48 kHz | BS.1770-4 Tables 1 and 2 | 0.00000000 | 0 | 0.00000000 | 1e-12 |
| K-weighting response at 44100 Hz | BS.1770-4 Table 1 | 0.0031 dB | 0 dB | +0.0031 dB | 0.05 dB |
| K-weighting response at 96000 Hz | BS.1770-4 Table 1 | 0.0126 dB | 0 dB | +0.0126 dB | 0.05 dB |
| integrated loudness, stereo 1 kHz at -23.0 dBFS peak | EBU 3341 test 1 | -22.9933 | -23.0000 | +0.0067 | 0.10 |
| momentary loudness, stereo 1 kHz at -23.0 dBFS peak | EBU 3341 test 1 | -22.9933 | -23.0000 | +0.0067 | 0.10 |
| short-term loudness, stereo 1 kHz at -23.0 dBFS peak | EBU 3341 test 1 | -22.9933 | -23.0000 | +0.0067 | 0.10 |
| integrated loudness, stereo 1 kHz at -33.0 dBFS peak | EBU 3341 test 2 | -32.9933 | -33.0000 | +0.0067 | 0.10 |
| momentary loudness, stereo 1 kHz at -33.0 dBFS peak | EBU 3341 test 2 | -32.9933 | -33.0000 | +0.0067 | 0.10 |
| short-term loudness, stereo 1 kHz at -33.0 dBFS peak | EBU 3341 test 2 | -32.9933 | -33.0000 | +0.0067 | 0.10 |
| integrated loudness, -36 / -23 / -36 dBFS | EBU 3341 test 3 | -23.0139 | -23.0000 | -0.0139 | 0.10 |
| integrated loudness, -72 / -36 / -23 / -36 / -72 dBFS | EBU 3341 test 4 | -23.0139 | -23.0000 | -0.0139 | 0.10 |
| integrated loudness, -26 / -20 / -26 dBFS | EBU 3341 test 5 | -22.9787 | -23.0000 | +0.0213 | 0.10 |
| integrated loudness, 5.0 channel programme | EBU 3341 test 6 | -23.0163 | -23.0000 | -0.0163 | 0.10 |
| short-term loudness, 1.34 s / 1.66 s segments | EBU 3341 test 9 | -22.9863 | -23.0000 | +0.0137 | 0.10 |
| momentary loudness, 0.18 s / 0.22 s segments | EBU 3341 test 12 | -22.9604 | -23.0000 | +0.0396 | 0.10 |
| true peak, fs/4, amplitude 0.50, phase 0 | EBU 3341 test 15 | -6.0206 | -6.0000 | -0.0206 | 0.40 |
| true peak, fs/4, amplitude 0.50, phase 45 | EBU 3341 test 16 | -6.0203 | -6.0000 | -0.0203 | 0.40 |
| true peak, fs/6, amplitude 0.50, phase 60 | EBU 3341 test 17 | -6.0208 | -6.0000 | -0.0208 | 0.40 |
| true peak, fs/8, amplitude 0.50, phase 67.5 | EBU 3341 test 18 | -6.0204 | -6.0000 | -0.0204 | 0.40 |
| true peak, fs/4, amplitude 1.41, phase 45 | EBU 3341 test 19 | +2.9847 | +3.0000 | -0.0153 | 0.40 |
| true peak, full scale alternating samples | band-limited reconstruction | 0.0000 | 0.0000 | 0.0000 | 0.10 |
| loudness range, -20 dBFS then -30 dBFS | EBU 3342 test 1 | 10.0000 | 10.0000 | 0.0000 | 1.00 |
| loudness range, -20 dBFS then -15 dBFS | EBU 3342 test 2 | 5.0000 | 5.0000 | 0.0000 | 1.00 |
| loudness range, -40 dBFS then -20 dBFS | EBU 3342 test 3 | 20.0000 | 20.0000 | 0.0000 | 1.00 |
| loudness range, -50 / -35 / -20 / -35 / -50 dBFS | EBU 3342 test 4 | 15.0000 | 15.0000 | 0.0000 | 1.00 |
| integrated loudness with a pause of silence | BS.1770-4 Annex 1 | -23.0589 | -23.0000 | -0.0589 | 0.10 |
| integrated loudness with a pause of -70 dBFS | BS.1770-4 Annex 1 | -23.0589 | -23.0000 | -0.0589 | 0.10 |
| integrated loudness with a pause of -36 dBFS | BS.1770-4 Annex 1 | -23.0556 | -23.0000 | -0.0556 | 0.10 |
| integrated loudness of silence | a silent file has no loudness | -inf | -inf | - | - |
| integrated loudness of one impulse | filter energy in the frequency domain | -39.6950 | -39.6950 | 0.0000 | 0.10 |
| integrated loudness of a constant | the high pass removes the direct current | -32.2975 | below -43.00 | - | - |
| integrated loudness of a 200 ms signal | shorter than the 400 ms block | -26.0042 | -26.0103 | +0.0061 | 0.20 |

The pause costs 0.06 LU: the four gating blocks that straddle the edge of
the pause carry part of the pause and part of the programme, and they are
inside the relative gate.  That is the behaviour of every meter that follows
the recommendation.

## What was wrong, and what it is now

Three defects were found.  The "before" column is measured by the harness
itself, which still carries the old rules, so the table can be reproduced
with one command.

| # | defect | where | before | after | change |
|---|---|---|---|---|---|
| 1 | the centre channel carried the weight 1.41 instead of the 1.0 of BS.1770-4 Table 3, and the fourth channel of a 4.0 layout was treated as an LFE channel | `channel_weights` | 5.0 channel programme reads **-22.3579 LUFS** | **-23.0163 LUFS** | **-0.66 LU** |
| 2 | the relative gate of the loudness range used the arithmetic mean of the loudness values instead of the energy mean of the reference implementation | `loudness_range` | the EBU Tech 3342 test 4 signal reads an LRA of **30.0 LU** | **15.0 LU** | **-15.0 LU** |
| 3 | the true peak was read over the whole buffer, so the interpolation at the two ends of the file could raise the reading by 1.5 dB | `true_peak_linear` | a full scale signal reads **+1.4756 dBTP** | **0.0000 dBTP** | **-1.48 dB** |

### 1. The channel weights

BS.1770-4 Table 3 gives the weight 1.0 for the centre channel and 1.41 for
the two surrounds; the LFE channel is left out of the measurement.  The
meter gave 1.41 to the centre and 0 to the fourth channel of a 4.0 layout.

The published test for this is EBU Tech 3341 test 6: a 5.0 channel
programme with -28 dBFS in left and right, -24 dBFS in the centre and
-30 dBFS in the surrounds must read -23.0 LUFS.  The old weights read
-22.3579 LUFS, which is 0.66 LU outside the tolerance of 0.1 LU; the
weights of Table 3 read -23.0163 LUFS.

A stereo file was never affected: the weights of two channels are 1.0 and
1.0 in both versions.

### 2. The loudness range

EBU Tech 3342 defines the relative gate of the loudness range as 20 LU
below the absolute-gated loudness level, and its reference implementation
computes that level as the mean of the un-logarithmed values.  The meter
used the arithmetic mean of the loudness values, which is a different
number, and it used the 5th percentile instead of the 10th.

The published test for this is EBU Tech 3342 test 4: five tone segments at
-50, -35, -20, -35 and -50 dBFS must give a loudness range of 15 LU ±1.
With the arithmetic mean the -50 dBFS segments stayed inside the gate and
the range came out as 30 LU.  With the energy mean they are gated out and
the range is 15.0 LU.

The 10th percentile instead of the 5th is the other half of the same
defect.  It does not show in the four published tests, because none of
them has fewer than 20 per cent of its values in the lowest passage; it
shows in a programme with a short quiet passage, where the old meter
reported a range that is too wide.

### 3. The true peak at the ends of the file

The reconstruction of a sample needs eight samples of the source on either
side of it.  At the two ends of the buffer those samples do not exist, the
filter sees silence instead, and the reconstruction rings: a signal that is
full scale in the samples read **+1.4756 dBTP** instead of 0 dBTP.

The artefact is at the file boundary and says nothing about the signal, so
the peak is now taken over the interior of the buffer, where the
reconstruction is valid.  The same over-read was found with the filter
that BS.1770-4 Annex 2 publishes (it reads +1.5248 dBTP on the same
signal), so it is a property of the method and not of this filter.

## The true peak filter

BS.1770-4 Annex 2 publishes one set of coefficients for a 48-tap, 4-phase
interpolating filter and allows "a method that gives similar or superior
results".  The meter uses a 65-tap Kaiser-windowed sinc.  Both were
measured on the five published true-peak signals:

| test | expected | this meter | the Annex 2 filter |
|---|---|---|---|
| fs/4, amplitude 0.50, phase 0 | -6.0 | **-6.0206** | -6.2179 |
| fs/4, amplitude 0.50, phase 45 | -6.0 | **-6.0203** | -5.9759 |
| fs/6, amplitude 0.50, phase 60 | -6.0 | **-6.0208** | -6.0690 |
| fs/8, amplitude 0.50, phase 67.5 | -6.0 | **-6.0204** | -6.0279 |
| fs/4, amplitude 1.41, phase 45 | +3.0 | **+2.9847** | +3.0291 |

The filter of the meter is the more accurate of the two, and the passband
gain of the Annex 2 filter is 0.081 dB at 1 kHz against 0.0004 dB for the
filter of the meter.

The four times oversampling of the recommendation accepts an under-read of
up to 0.688 dB at the Nyquist frequency (Appendix 1 to Annex 2).  The
meter reads a 16 kHz tone 0.30 dB low and a 20 kHz tone 0.47 dB low, which
is inside that limit.

## The edges

| input | reading | note |
|---|---|---|
| silence | -inf | a silent file has no loudness; the momentary and short-term windows read the -300.69 dB floor |
| one impulse of amplitude 1.0 | -39.6950 LUFS | the energy of the impulse spread over the 400 ms block; the same number comes out of the frequency domain |
| a constant of 0.5 | -32.2975 LUFS | the high pass of the weighting removes the direct current; only the start-up transient of the filter is left, 29.3 dB below a full scale tone |
| 100 ms item at -23 dBFS | -29.0151 LUFS | the block is filled up with silence, which costs 6.02 dB |
| 200 ms item at -23 dBFS | -26.0042 LUFS | the block is filled up with silence, which costs 3.01 dB |
| 300 ms item at -23 dBFS | -24.2431 LUFS | the block is filled up with silence, which costs 1.25 dB |
| 400 ms item at -23 dBFS | -22.9936 LUFS | one full block, the reading is the level of the item |

The short items are the one behaviour that a delivery target has to know
about: see `var/patches/c6_targets.md`.

## The delivery targets

The catalogue is owned by another agent, so the findings are written down
in `var/patches/c6_targets.md` and are not repeated here.  In short: the
meter reaches every target of the catalogue to within 0.0001 LU when the
gain is applied, and the two targets that ask for more gain than the
true-peak ceiling allows (-16 LUFS and -14 LUFS against a -1 dBTP ceiling)
cannot be reached by a programme with 3 dB of crest factor above its
loudness.

## What could not be verified

* **The authentic programme material.**  EBU Tech 3341 test 7 and 8 and
  EBU Tech 3342 test 5 and 6 are programme segments, not synthetic
  signals, and the files of the EBU test set are not in this repository.
  They were not measured.
* **The single period signals of EBU Tech 3341 test 20 to 23.**  The
  description of the test does not name the anti-aliasing filter of the
  synthesis, so the exact signal could not be reproduced.  The property
  those tests check, that a peak between two samples is found, is covered
  by the fs/4 test signals and by the full scale alternating signal.
* **The compliance material of Report ITU-R BS.2217.**  The ITU test
  signals and their tolerances were not obtained, so the meter was not
  checked against them.  The EBU mode tolerances of EBU Tech 3341 and the
  published values of BS.1770-4 and BS.1770-4 Annex 2 were checked.
* **The loudness of a real delivery.**  The measurements above are made on
  synthetic signals and on `corpus/audio/test/music_mix.wav`.  A delivered
  master that was written at 16 bit and then read back carries the
  quantisation of the write, which was not part of the measurement.
