# Auralis — the benchmark

`qa/benchmark/` measures **our** modules against the numbers that a study of two
commercial restoration products recorded.  It is the instrument that keeps the gap
closed: a module that falls behind a recorded number is reported, with the size of
the gap.

The design note that says what was measured, what the numbers are and what is still
missing is [`../../docs/BENCHMARK_NOTES.md`](../../docs/BENCHMARK_NOTES.md).

## What is in here

| File | What it is |
|---|---|
| `cases.py` | the cases of the benchmark: the corpus clips and the voice signals, each one with the module of ours that owns it and the reason why |
| `reference.py` | the numbers the study recorded, as plain constants, with the case, the unit, the way they were measured and the series they came from |
| `reference_voice.py` | the numbers of the voice head to head: our voice de-noise against a commercial voice cleaner, with the case, the unit, the way they were measured, the series they came from and the tree they were measured on |
| `reference_bridge.py` | the numbers of the measurement bridge: our own modules and the reference, measured on the same cases with the product's own instrument and judged by the product's own rules, with the case, the unit, the way they were measured and the series they came from |
| `harness.py` | the measurement itself: it renders a case through a module and measures it |
| `run_benchmark.py` | the runner: it measures our modules, prints the table and says which modules are behind and by how much |

## The rule

**A reference is a target, not a contract.**  The numbers were measured on
somebody else's build, on somebody else's machine, with somebody else's settings.
A gate that failed on them would be a gate held by a machine we do not control, so
the runner *reports* a module that is behind a reference and only fails when the
instrument itself is broken.

When more than one series carries a number for a case and a metric, the strongest
one is the reference: the benchmark is held to the best of them, because that is the
one worth closing the gap to.

## The series

The two references are described by their shape and never by their names:

* `stem_separation` — the reference that splits a recording into three stems and
  gives each of them a gain and three switches.
* `band_restoration` — the reference that picks an algorithm and gives it four
  bands.

## The metrics

| Metric | Unit | Better |
|---|---|---|
| `removed_db` | dB | more is better — how much of the artefact is taken away |
| `kept_db` | dB | less is better — how far the wanted content moves |
| `gain_var_db2` | dB² | less is better — how much the gain curve breathes |
| `chatter_db` | dB | less is better — how much the gain chatters on a voice |
| `latency_samples` | samples | less is better — how far the output lags the input |
| `cost_transform_eq` | transforms | less is better — how many 1024-point transforms one 64-sample block costs |

## Running it

```powershell
cd <repo>
python qa\benchmark\run_benchmark.py                      # the table
python qa\benchmark\run_benchmark.py --case hiss          # one case
python qa\benchmark\run_benchmark.py --json out.json      # machine-readable result
```

The runner prints one row per case, metric and module: our number, the reference,
the gap and the verdict.  It exits `0` when the instrument worked and `1` when a
case could not be measured at all.
