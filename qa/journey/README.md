# Auralis — the product journey

`journey.py` walks through the product the way a person does, in order,
against a **running server**.  It is the sibling of `qa/verify_app.py` (which
checks the individual endpoints) — this one checks that the *whole* path a
user takes still works: load a file, look at it, look at a part of it, build
a chain, bounce it, remember the settings, keep the session, take a change
back, compare two chains, deliver it, run it overnight.

Two rules hold for the whole walk-through:

* **every step asserts something.**  A step that only calls an endpoint and
  hopes for the best is not a step.  The journey counts its assertions and
  reports them, so a step with no assertion is visible in the summary.
* **the numbers are the same in every run.**  The measurements of a run are
  deterministic: the same build reports the same numbers twice.  The fields
  that cannot be deterministic (the port, the asset ids, the job ids, the
  delivered file names) are named in the ``volatile`` key of the summary.

## Running it

```powershell
cd <repo>
python qa\journey\journey.py                      # starts a server, runs, stops it
python qa\journey\journey.py --port 8741          # on a fixed port
python qa\journey\journey.py --base-url http://127.0.0.1:8741   # use a running server
python qa\journey\journey.py --json               # machine-readable summary on stdout
```

Every step prints one `[PASS]` / `[FAIL]` line with its measurements.
`--json` moves the step lines to stderr and prints the summary as JSON on
stdout.  The exit code is `0` when every step passed and `1` otherwise.

## The summary

```json
{
  "ok": true,
  "base_url": "http://127.0.0.1:5185",
  "passed": 37,
  "failed": 0,
  "total": 37,
  "assertions": 202,
  "steps": [
    {
      "name": "analysis: spectrogram",
      "ok": true,
      "detail": "512 bins x 278 frames, loudest bin 984 Hz, range 28..229",
      "assertions": 10,
      "measurements": {"bins": 512, "frames": 278, "bin_hz": 46.875}
    }
  ],
  "measurements": {"analysis: spectrogram": {"bins": 512, "frames": 278}},
  "volatile": {"base_url": "the port of the server is a free port, ..."}
}
```

* `ok`, `passed`, `failed`, `total` — the verdict of the run.
* `assertions` — how many assertions the journey made in total.
* `steps` — one record per step, with its verdict, its detail, the number of
  assertions it made and its measurements.
* `measurements` — the numbers of the run, keyed by the step name.  A CI run
  may compare them between two builds.
* `volatile` — the fields that differ between two runs, and why.

## The steps

| # | Step | What it proves |
|---|---|---|
| 1 | the workbench answers | the server is alive |
| 2 | the module catalogue | 48 modules, De-Hum and De-Click among them |
| 3 | upload the recording | a synthetic recording (tone + hum + clicks) is loaded |
| 4 | analysis: spectrogram | the matrix is there, has the right shape and shows the programme |
| 5 | analysis: the frequency axis | the loudest bin is reported at the frequency it really has |
| 6 | analysis: every row carries its own band | the matrix is not padded |
| 7 | analysis: waveform envelope | the min/max envelope is there and sane |
| 8 | analysis: a selection is measured | a selection is shorter than the whole recording |
| 9 | analysis: the selection leaves the click outside | the selection is really measured, not the whole file |
| 10 | analysis: the selection is reported | the measurement payload names the range it measured |
| 11 | chain: add De-Hum and De-Click | the chain grows |
| 12 | chain: reorder | the order changes |
| 13 | chain: set a parameter | a value is stored and read back |
| 14 | chain: bypass De-Hum | the power button works |
| 15 | chain: engage De-Hum again | the power button works both ways |
| 16 | render: the chain runs | the render answers and no module raised |
| 17 | render: a valid WAV of the right length | RIFF/WAVE, same frame count, finite samples |
| 18 | render: the hum is measurably reduced | the 50 Hz bin drops by 12 dB or more |
| 19 | render: the programme survives | the 1 kHz programme loses at most 1 dB |
| 20 | preset: save | the preset is written into the library |
| 21 | preset: list | the library lists it once, with its module and category |
| 22 | preset: the shipped library is there | the library holds the shipped presets as well |
| 23 | preset: load and round-trip the values | every value comes back unchanged |
| 24 | session: save the document | the document holds the chain and every parameter |
| 25 | session: the document lists in the library | the session shows up in the list of sessions |
| 26 | session: load the document | the chain, the bypass flags and the parameters come back |
| 27 | undo: the change is reverted | the undo history puts the chain back as it was |
| 28 | undo: the history is in the document | the history travels into the session document |
| 29 | undo: the chain comes back as it was | a change in the workbench can be taken back |
| 30 | compare: the two renders | the compare route renders the same audio twice |
| 31 | compare: the hum is gone in one of them | the two renders differ, and the programme survives |
| 32 | delivery: the catalogue | the delivery targets are served |
| 33 | batch: start the job | the batch job starts |
| 34 | batch: the job finished | every file was delivered |
| 35 | batch: the output exists | the delivered files are on disk with the right shape |
| 36 | batch: every delivered file has a loudness | every delivery carries its own loudness |
| 37 | batch: the loudness follows the input | the louder source is the louder delivery |

## The scratch files

The journey writes into `var/journey/`:

* `journey-loud.wav`, `journey-quiet.wav` — the two sources of the batch job
  (the same recording at two levels);
* `out/` — the delivered files.  The files of the journey are removed before
  the batch runs, because the runner never overwrites a file: without the
  cleaning the second run would deliver `journey-loud_1.wav` and the names
  of the delivered files would differ between two runs.

## In the test suite

`tests/test_journey.py` runs the same script against a server started in the
test process (uvicorn in a thread) and asserts the outcome.  It also runs the
journey a **second** time and compares the measurements, so a run that is no
longer deterministic fails the suite.

A known defect is marked `xfail` in the test module, and every such marker is
**strict**: a defect that is fixed turns the marker into a failure that has
to be noticed, and the marker is removed.  A soft marker would hide the fix.
