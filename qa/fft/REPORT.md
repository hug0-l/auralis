# The real FFT of the Auralis core — measurement and repair

Owner: the agent of `aurcore/dsp/fft.py`.
Files: `aurcore/dsp/fft.py`, `tests/test_fft.py`.
Harness: `qa/fft/harness.py`, `qa/fft/run_fft.py`.
Raw results: `qa/fft/results.json`.  Full tables: `qa/fft/tables.md` (after)
and `qa/fft/tables_before.md` (before the repair).

## 1. What was measured, and with which command

    python qa/fft/run_fft.py --json          # writes qa/fft/results.json and qa/fft/tables.md
    python qa/fft/run_fft.py --table         # prints the tables only
    python -m pytest tests/test_fft.py -q    # 239 assertions

The harness never modifies `aurcore`; it only calls it.

The machine is shared, so every timing is the **fastest of five rounds of 200
timed calls**, and the round trip numbers are deterministic (a fixed seed).

## 2. The sizes that were unchecked before

The suite had **one** size for the transform (64, against a textbook DFT) and
one size for the round trip (128).  The file supports every length from 1
upwards, so the size matrix that was missing is:

| family | sizes | checked before |
|---|---|---|
| every size | 1 .. 1024 (1024 sizes) | 1 size (64) |
| the large sizes | 1536, 2048, 3000, 4096, 8192, 16384, 32768, 65536 | none |
| the odd sizes | 3, 5, 7, 9, 11, 13, 15, 17, 21, 23, 31, 33, 63, 65, 101, 127, 129, 251, 257, 511, 513, 1001, 1023, 1025 | none |
| the prime and awkward sizes | 31, 127, 251, 383, 511, 1000, 1021, 3000, 4096 | none |
| the small blocks | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 | none |
| the short-time frame sizes | 2 .. 4096, 57 sizes, at 50 % and 75 % overlap | 5 combinations, all even |
| the transform axes | 0, 1, -1 | 1 (the last) |
| the radix-2 backend | 1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024 | 1 size (64) |

The one that mattered most: **512 of the 1024 sizes came back a sample
short and at the wrong level**, and every one of them was an odd size (plus
n = 1, which raised).

## 3. The round trip, at every size

Full table: `qa/fft/tables.md`, section 1.

| what | sizes | worst error | level |
|---|---|---|---|
| explicit length, 1 .. 1024 | 1024 | 1.221e-15 (n = 934) | **-298.3 dBFS** |
| explicit length, large | 8 | 4.996e-16 (n = 16384) | **-306.0 dBFS** |
| default length, 1 .. 1024 (after the fix) | 1024 | 1.221e-15 (n = 934) | **-298.3 dBFS** |
| short-time transform, 57 frame sizes x 2 overlaps | 114 | 4.441e-16 (n_fft = 251) | **-307.1 dBFS** |

The worst error is 1.221e-15, i.e. **5.5 times the machine epsilon**, and
it is the same for the odd sizes as for the even ones — once the length is
known, nothing about the parity of the size matters.

### Against a textbook DFT

Full table: `qa/fft/tables.md`, section 4.  The error is relative to the
largest bin, because the textbook DFT loses accuracy itself as n grows.

| n | absolute error | relative error | radix-2 absolute error |
|---|---|---|---|
| 2 | 1.882e-17 | 1.631e-17 | 1.882e-17 |
| 8 | 4.965e-16 | 1.995e-16 | 4.965e-16 |
| 16 | 7.376e-15 | 2.179e-15 | 7.376e-15 |
| 32 | 9.952e-15 | 2.814e-15 | 9.860e-15 |
| 64 | 2.093e-14 | 3.062e-15 | 2.093e-14 |
| 128 | 8.280e-14 | 9.277e-15 | 8.527e-14 |
| 256 | 2.832e-13 | 2.454e-14 | 2.726e-13 |
| 512 | 7.802e-13 | 3.839e-14 | 7.592e-13 |
| 1024 | 2.396e-12 | 8.276e-14 | 2.514e-12 |

The reference itself is the limit here: at n = 1024 the textbook DFT is
already 8.3e-14 away from the fast transform, which is 370 times the machine
epsilon.  The fast transform is the accurate one.

## 4. The defects, and the repair

### Defect 1 — the default inverse length silently dropped a sample

`irfft(X)` with no length derived the length as `2 * (n_bins - 1)`.  That is
the right answer for an **even** length and one sample short for an **odd**
one, because a one-sided spectrum of `k` bins describes a signal of either
`2k-2` samples (the top bin is the Nyquist bin and is therefore real) or
`2k-1` samples (the top bin is an ordinary bin).

| | before | after |
|---|---|---|
| sizes whose default round trip is short | **512 of 1024** (every odd size) | **0 of 1024** (for a non-degenerate signal) |
| n = 1 | raised `ValueError: n must be >= 1` | returns the one sample, error 0.0 |
| n = 5, a noise signal | 4 samples back, error 1.917e-01 (**-14.3 dBFS**) | 5 samples back, error 1.457e-16 (-316.7 dBFS) |
| n = 101, a noise signal | 100 samples back, error 1.012e+00 (**+0.1 dBFS**) | 101 samples back, error 2.220e-16 (-313.1 dBFS) |
| n = 1023, a noise signal | 1022 samples back, error 1.203e+00 (**+1.6 dBFS**) | 1023 samples back, error 3.747e-16 (-308.5 dBFS) |
| n = 1025, a noise signal | 1024 samples back, error 1.202e+00 (**+1.6 dBFS**) | 1025 samples back, error 4.441e-16 (-307.1 dBFS) |
| n = 1024, a noise signal | 1024 samples, error 3.331e-16 (-309.5 dBFS) | unchanged |

The loss was not only a missing sample: the inverse transform divides by the
length, so a signal that was inverted with the wrong length came back at the
**wrong level** as well (the measured level error is +1.67 dB at n = 3 and
+1.94 dB at n = 5 for a DC signal), and the sample that was dropped was the
one that carried the mismatch.  For a full-scale signal the round trip was
therefore wrong by **up to 1.6 dBFS** — an audible error, not a rounding
artefact.

**The repair.**  `irfft` now infers the length from the spectrum, choosing the
reading that loses no information: the odd length `2k-1` when the top bin is
complex, the even length `2k-2` when it is real.  The rule is safe because
the Nyquist bin of an even-length spectrum is *always* exactly real (measured
over 897 random even-length signals: 0 exceptions) while the top bin of an
odd-length spectrum is complex for every non-degenerate signal (0 exact-real
cases in 897 random odd-length signals).  A new `fft_round_trip(x)` carries
the length from the forward transform to the inverse one, so the pair is
exact at every size by construction.

**The note — the residual ambiguity.**  The ambiguity is irreducible: a
one-sided spectrum of `k` bins cannot say whether the signal had `2k-2` or
`2k-1` samples when the top bin is exactly real.  The residual case is an
odd-length signal whose top bin is *degenerate* — a DC signal, silence, or
an impulse — and it is **not** repaired:

| n | signal | top bin real | samples back | error | level error |
|---|---|---|---|---|---|
| 3 | dc | yes | 2 (expected 3) | 5.000e-01 (-6.0 dBFS) | +3.5218 dB |
| 5 | dc | yes | 4 (expected 5) | 2.500e-01 (-12.0 dBFS) | +1.9382 dB |
| 5 | noise | no | 5 | 1.457e-16 | 0.0000 dB |
| 7 | dc | yes | 6 (expected 7) | 1.667e-01 (-15.6 dBFS) | +1.3389 dB |
| 101 | dc | yes | 100 (expected 101) | 1.000e-02 (-40.0 dBFS) | +0.0864 dB |
| 101 | noise | no | 101 | 2.220e-16 | 0.0000 dB |
| 1025 | dc | no | 1025 | 0.000e+00 | 0.0000 dB |
| 1025 | noise | no | 1025 | 4.441e-16 | 0.0000 dB |

For those, pass `n` explicitly or use `fft_round_trip`.  The full list is in
`qa/fft/tables.md`, section 2b.  The rule was chosen over the alternative
("always the even length", the old behaviour) because it is the reading that
never throws information away, and over the other alternative ("always the odd
length") because that would break every even-length signal, which is the
overwhelmingly common case.

### Defect 2 — the radix-2 backend ignored the transform axis

`rfft(x, n=16, axis=0, backend="radix2")` transformed the **last** axis
whatever the caller asked for, and returned the wrong shape as well.  It
answered with the right numbers for the wrong axis, which is the worst kind
of wrong answer.

| n | axis | before | after |
|---|---|---|---|
| 8 | 0 | shape (6, 5), expected (5, 8) | shape (5, 8), error 8.882e-16 |
| 16 | 0 | shape (6, 9), expected (9, 16) | shape (9, 16), error 9.155e-16 |
| 32 | 0 | shape (6, 17), expected (17, 32) | shape (17, 32), error 2.001e-15 |
| 64 | 0 | shape (6, 33), expected (33, 64) | shape (33, 64), error 4.552e-15 |

**The repair.**  The axis that is asked for is moved to the end with
`np.moveaxis`, transformed there, and moved back.

### Defect 3 — the helpers did not keep the promise in their own docstring

`magnitude` said "the result is always finite" and `magnitude_db` said the
magnitudes are reported as the floor.  Neither was true for a spectrum that
holds an infinity or a NaN.

| input | before | after |
|---|---|---|
| `magnitude_db` of an infinity | `inf` dB | -120.0 dB (the floor) |
| `magnitude_db` of a NaN | `nan` dB | -120.0 dB (the floor) |
| `phase` of a NaN | `nan` rad | 0.000 rad |
| `to_complex` of a NaN magnitude | `nan` | 0.0 |

**The repair.**  `magnitude`, `magnitude_db`, `phase` and `to_complex` are now
*total*: they always return a finite value.  The finiteness test is one
reduction (`max`, which propagates a NaN) and one scalar comparison, so the
common path costs **+1.25 us** on `phase` and **+2.40 us** on `to_complex`
(a 1024-sample frame: +3.75 us on `STFT.analyze`, +2.70 us on
`STFT.synthesize`, i.e. +13 % and +7 % of one frame).  The transform itself
is untouched: a NaN in the input still poisons the spectrum, because that is
what a linear transform does, and hiding it there would be a lie.

### Defect 4 — the zero-padding assumption was nowhere in the user's sight

The module documentation listed three conventions and none of them said that
a transform treats the block as if the signal continued as zeros outside it.
Only the parameter list of `rfft` mentioned that a longer transform
zero-pads, which is about the *transform length*, not about the *signal*.

**The repair.**  The module documentation now has a "Block edges and padding"
section with the measured leakage table, and `rfft` points at it.

## 5. The phase at the edges of a block

Full table: `qa/fft/tables.md`, section 9.

| block length | parity | bins | DC imaginary part | top bin | top phase | magnitude/phase rebuild error |
|---|---|---|---|---|---|---|
| 1024 | even | 513 | 0.000e+00 | -2.738100 + 0.000000j (the Nyquist bin, real) | 3.141593 | 5.024e-15 (-286.0 dBFS) |
| 1025 | odd | 513 | 0.000e+00 | -3.686169 - 2.597348j (an ordinary bin) | -2.527773 | 5.024e-15 (-286.0 dBFS) |

Three facts fall out of it:

* **The DC bin is always real** (its imaginary part is exactly 0.0) and its
  phase is 0.
* **The Nyquist bin of an even block is real** and its phase is 0 or ±π;
  **the top bin of an odd block is an ordinary complex bin** and carries a
  real phase.  The inverse transform keeps the imaginary part of that bin
  (verified: the round trip at n = 1025 is exact to 4.441e-16), so the phase
  at the top of an odd block is *not* thrown away.
* **Storing the spectrum as magnitude and phase costs 29 dB** against
  keeping the complex spectrum: 5.024e-15 (-286.0 dBFS) against 1.7e-16
  (-315.6 dBFS).  That is the price of the polar form, and it is the
  representation the spectral editor works with.

## 6. Does the transform assume the block is zero padded?  Yes.

Full table: `qa/fft/tables.md`, section 10.  A 1024-sample block with a tone
at bin 64:

| block content | worst bin other than the tone |
|---|---|
| periodic (the tone fits the block) | **-289.6 dB** below the tone |
| 1 sample gated at each end | **-48.5 dB** below the tone |
| 8 samples gated at each end | -35.7 dB below the tone |
| 64 samples gated at each end | -17.1 dB below the tone |
| 256 samples gated at each end | -3.9 dB below the tone |
| a ramp (a step of 1.0 at the last sample) | **0.0 dB** below the tone |

A block that is periodic over its own length is transformed exactly.  A block
with a discontinuity at its edge is not: the step is a broadband click, and at
the end of a ramp it is as loud as the tone itself.  This is now stated in
the module documentation of `aurcore/dsp/fft.py`.

## 7. The cost of one transform

Full table: `qa/fft/tables.md`, section 8.  The budget is the realtime
budget of a block of that size at 48 kHz (64 samples = 1333.3 us for
*everything*).

The numbers below are the **quietest** observation of a 64-sample transform
on this shared machine; the same measurement on a loaded machine gives
8.30 us instead of 3.60 us, so the share of the budget moves between
**0.27 % and 0.62 %**.  The ratio between the backends does not move.

| n | forward | inverse | radix-2 | budget | share of the budget |
|---|---|---|---|---|---|
| 64 | **3.60 us** | 4.00 us | 96.6 us | 1333.3 us | **0.27 %** |
| 128 | 4.00 us | 4.30 us | 200.4 us | 2666.7 us | 0.15 % |
| 256 | 4.30 us | 4.70 us | 416.9 us | 5333.3 us | 0.08 % |
| 512 | 5.50 us | 5.80 us | 894.2 us | 10666.7 us | 0.05 % |
| 1024 | 7.10 us | 7.50 us | 1902.8 us | 21333.3 us | 0.03 % |
| 2048 | 11.20 us | 12.00 us | — | — | — |
| 4096 | 18.90 us | 19.60 us | — | — | — |
| 8192 | 38.50 us | 37.70 us | — | — | — |
| 16384 | 78.10 us | 75.80 us | — | — | — |

The transform is **not** the reason a module misses the block budget: one
64-point transform is 3.60 us, i.e. 0.27 % of the budget for the whole
block.  The hand written radix-2 backend is 27 times slower and would eat
7 % of the budget on its own at 64 points, and 0.89 ms at 512 points — it
is a reference implementation, not a fast path.

## 8. The numerical edges

Full table: `qa/fft/tables.md`, section 11.  Input: 64 samples.

| case | the spectrum | the rebuild | the magnitude of DC | the phase of DC |
|---|---|---|---|---|
| silence | exactly zero | exactly zero | -120.0 dB (the floor) | 0.000 rad |
| denormal (1e-320) | finite, the denormal survives | finite | -120.0 dB (the floor) | 0.000 rad |
| huge (1e300) | finite (6.4e301 after the sum) | finite | 6036.1 dB | 0.000 rad |
| overflow (1e307) | **infinite** (the sum of 64 values leaves the float range) | infinite | -120.0 dB (the floor) | 0.000 rad |
| infinity | **not finite** (one infinity poisons every bin) | not finite | -120.0 dB (the floor) | 0.000 rad |
| NaN | **not finite** (one NaN poisons every bin) | not finite | -120.0 dB (the floor) | 0.000 rad |

What this means:

* **Silence** is clean: the spectrum is exactly zero, the magnitude sits on
  the floor, the phase is 0, and the rebuild is exactly zero.
* **A denormal** survives (it is not flushed to zero) and it costs no
  measurable time: 7.10 us against 7.00 us for a normal signal at n = 1024
  (ratio 1.01 x), 19.10 us against 19.40 us at n = 4096 (0.98 x).
* **A very large value** is fine up to about 2.8e306 for a 64-sample block
  (n x value must stay under the largest double).  1e300 is fine, 1e307
  overflows.
* **An infinity and a NaN** poison the whole spectrum, because the transform
  is a sum: one bad sample in a 64-sample block contaminates every bin.  The
  helpers do not hide it (the magnitude and the phase stay finite), and the
  transform does not hide it either.  A NaN in the input therefore comes out
  as a NaN in the output, which is the right behaviour: it is a fault in the
  signal, not in the transform.
* A NumPy runtime warning ("invalid value encountered in rfft_n_even") is
  raised for the infinity and the NaN case.  The test suite turns runtime
  warnings into errors, so the two tests that need them suppress it
  explicitly.

## 9. A short input, and an input that is not a multiple of the frame

Full table: `qa/fft/tables.md`, section 7.  A 1024-sample frame, hop 512
(50 %) and 256 (75 %).

* **Shorter than one frame** (1, 63, 64, 100, 511, 1000, 1023 samples): with
  `center=True` (the default) the output has exactly the input length and
  the error is 0.0 to 1.67e-16 (-307.1 dBFS).  The frame is zero-padded, and
  the zero padding is the part of the frame that carries no information, so
  nothing is lost.
* **Not a multiple of the frame** (1000, 1023, 1025, 2047, 3000, 4096
  samples): the output has exactly the input length and the error is
  1.67e-16 to 2.22e-16 (-311.1 dBFS).  The tail is not dropped and not
  repeated.
* **With `center=False`** the first and the last samples are *not*
  reconstructed: the error at the first sample is 1.114e-01 and at the last
  8.895e-13, while the error over the interior is 1.636e-12 (-235.8 dBFS).
  The reason is the window: the Hann window is exactly zero at its edges, so
  the first and the last samples carry no information at all.  This is
  already stated in `aurcore/dsp/stft.py` ("the very first sample carries no
  information at all and cannot be reconstructed"), and it is a property of
  the short-time transform, not of the transform wrapper.

## 10. The tests

`tests/test_fft.py` is new (the file did not exist).  239 assertions:

* the round trip at **every** size from 1 to 1024, and at the large sizes;
* the round trip through the **default** inverse length, at every size;
* `fft_round_trip` at every size;
* the transform against a textbook DFT at 24 sizes, relative tolerance
  1e-15 x n;
* the radix-2 backend against the textbook DFT at 11 sizes, and against the
  compiled backend at 5 sizes x 3 axes;
* the short-time transform at 34 frame sizes x 2 overlaps;
* a short or ragged input at 13 lengths;
* the phase at the edges of a block (the Nyquist bin is real, the top bin of
  an odd block is complex, the magnitude/phase round trip stays below
  -280 dBFS);
* the zero-padding assumption (a periodic block leaks below -280 dB, a
  gated block leaks no further than -40 dB);
* the six numerical edges and the totality of the helpers;
* the padding convention, the argument checks and `fft_size`.

The tolerance for the round trip is -260 dBFS (1e-13), which is 38 dB below
the worst measured case (-298.3 dBFS).

## 11. What belongs to somebody else

Four things were found in files this agent does not own.  **The first two
were fixed by their owners while this measurement was running**, and the tree
is clean again; they are listed here because they were real.

1. **`aurcore/modules/voice_de_noise.py`, lines 555 and 564**: the frames are
   built as `(n_frames, n_channels, n_fft)` but the window was applied as
   `self._engine.win[:, None]`, which is `(n_fft, 1)`.  The two do not
   broadcast, and the module raised

       ValueError: operands could not be broadcast together
       with shapes (184,2,2048) (2048,1)

   at `np.fft.rfft(frames * self._engine.win[:, None], axis=1)`.  The window
   has to be applied over the **last** axis, i.e. `self._engine.win` (shape
   `(n_fft,)`) or `self._engine.win[None, None, :]`.  The failure is raised
   before any call into `aurcore.dsp.fft` (the module calls
   `numpy.fft.rfft` directly), so it was not caused by this work.  It made
   `tests/test_modules_specific.py` and `qa/sweep_modules.py` fail for
   `AUR_VOICEDENOISE` for a while; both are green again now.

2. **`tools/hygiene_check.py` reported findings in other agents' files**
   during the work: a `C:\\Windows\\x.wav` example in the docstring of
   `aurserver/assets.py`, the same string in `tests/test_assets.py`, the
   `C:\Program Files\Common Files\VST3` path twice in `packaging/README.md`,
   and the `C:\ProgramData\chocolatey\bin` path in the two workflow files
   under `.github/workflows/`.  All of them are `absolute-path` findings in
   files this agent does not own, and all of them have been fixed by their
   owners: the gate reports **0 findings in 372 files** as of the run of
   2026-10-21, 16:05.

   This agent's own files were clean at every point of the work:
   `tools/hygiene_check.py --root qa/fft` reports 0 findings and
   `--root aurcore/dsp` reports 0 findings.

3. **`tests/test_quality_spectral.py` and `qa/qualityvoice/harness.py`** were
   half-edited while the tests ran (undefined names `MEASURED_AT_15`, `AT_12`
   and `CASES`, and a `build_cases()` that returned strings).  Those
   failures are in another agent's work in progress and are unrelated to the
   transform; they are not reproduced by `tests/test_fft.py`.

4. **`tests/test_modules_specific.py::test_voice_denoise_keeps_the_voice`**
   failed with -7.46 dB of voice attenuation while the Voice De-Noise
   module was being rewritten.  That was open defect #2 of the brief; the
   module is rewritten now and the survey measures its voice band at
   0.02 dB (`qa/survey/REPORT.md`).  It is **not** caused by this work, and
   that was measured rather than argued: the module calls `irfft(...,
   n=_FFT_SIZE, axis=2)` with an explicit length and `to_complex(mag,
   np.angle(spec))` with finite arguments, so neither changed helper can
   alter its output.  Replacing the two helpers with their *old*
   implementations and running the module again gives

       max |new - old| over the whole block: 0.000e+00

   i.e. the output is bit-identical before and after the change.

## 12. What could not be finished

* **The length ambiguity of a one-sided spectrum is irreducible.**  A
  spectrum of `k` bins describes a signal of `2k-2` or `2k-1` samples and
  nothing in the spectrum can say which, when the top bin is exactly real.
  The repair removes the silent failure for every non-degenerate signal
  (a DC signal of an odd length is still short by one sample and off by
  +1.94 dB at n = 5, and that case is pinned by a test); the way out for it
  is `fft_round_trip` or an explicit `n`.
* **The transform still propagates a NaN and an infinity.**  That is the
  correct behaviour of a sum, and the fix was deliberately not made there.
  A caller that wants a sanitised signal has to sanitise it before the
  transform.
* **The `radix2` backend was not made faster.**  It is 27 times slower than
  the compiled transform at 64 points, which the documentation now says.
  It exists for cross-checking.
* **`aurcore/dsp/__init__.py` was not touched**, so `fft_round_trip` is
  reachable as `aurcore.dsp.fft.fft_round_trip` but not as
  `aurcore.dsp.fft_round_trip`.  That file was not given to this agent.
* **The VST3 bundles carry a copy of `aurcore/dsp/fft.py`** and are now one
  revision behind the source.  They are build artefacts; the next
  `scripts/build_vst3.py` run refreshes them.  The bundles under `dist/` and
  `build/` were deliberately not edited by hand.
* **The timing numbers are machine dependent.**  They were taken on a shared
  box; the ratios (the transform against the budget, the radix-2 backend
  against the compiled one) are the durable part, the microseconds are not.
  The same measurement on a loaded machine gives 8.30 us for a 64 point
  transform where the table above says 3.60 us.

## 13. The verification run

    python -m pytest tests\test_fft.py -q          239 passed, exit 0
    python -m pytest tests\test_dsp.py \
        tests\test_dsp_layer.py \
        tests\test_analysis.py \
        tests\test_modules_generic.py -q            exit 0
    python qa\sweep_modules.py                      48 modules, 240 runs, no problems, exit 0
    python tools\hygiene_check.py                   0 findings in 372 files, exit 0
    python tools\hygiene_check.py --root qa\fft     0 findings
    python tools\hygiene_check.py --root aurcore\dsp   0 findings

The run of 2026-10-21, 16:05, is the one the numbers of this report carry.
`tests/test_modules_specific.py::test_voice_denoise_keeps_the_voice` failed
with -7.46 dB of voice attenuation while the Voice De-Noise module was being
rewritten; the module is rewritten now (section 11 item 4).
