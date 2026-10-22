# Auralis — the edge contract

> **What this document is.**  The rules a module has to hold at the limits of
> the signal: silence, a full scale tone, an impulse at the first frame and
> in the middle, a hard clipped signal, a full scale offset, a denormal, a
> square at the ceiling, and inputs of one, two, seven, sixty four and five
> hundred and eleven frames.  What was measured, what the two references do,
> where the family stands today, and which of the rules are gates of the tree
> and which are not.
>
> **The numbers** are in
> [`../qa/benchmark/edge_cases.py`](../qa/benchmark/edge_cases.py).  The
> instrument of our side is
> [`../qa/measure_edge_contract.py`](../qa/measure_edge_contract.py), and the
> gate that holds the family is
> [`../qa/check_module_edges.py`](../qa/check_module_edges.py).
>
> **Every number in this document is checkable**, and the command that
> reproduces it is in §2.
>
> **No third party name appears in this document.**  The two references are
> described by their shape, never by their names.  The gate is
> [`../tools/hygiene_check.py`](../tools/hygiene_check.py).
>
> The binding rules live in [`../CONTRACT.md`](../CONTRACT.md).  Where this
> document and the contract disagree, the contract wins.

---

## 1. The rules

Every module is measured at the limits of what a host can hand it.  Seven
things are asked of a module over those signals:

| # | the rule | what it asks | a gate? |
|---|---|---|---|
| 1 | **the length** | the output has exactly as many frames as the input: one frame in, one frame out | **yes** |
| 2 | **the finiteness** | not one output sample is NaN or Inf, for any edge signal | **yes** |
| 3 | **the silence** | a silent input comes back as zeros, unless the module says that it makes a sound of its own | **yes** |
| 4 | **the ring out** | the module stops making a sound no later than the latency and the tail it declares | **yes** |
| 5 | **the ceiling** | the **output of the product** — the chain's output and the plug-in's output — does not come out above the limit the chain declares: the digital ceiling, multiplied by the gain the chain's own level controls declare.  A module's own answer may go over: the rule is at the output, not inside the module | **yes** |
| 6 | **the block size** | the answer does not depend on the block size the host happens to use | **partly** |
| 7 | **the length of the material** | the level of the output does not depend on how long the material is | **yes** |

A module that carries a latency of L answers a signal shorter than L with
nothing at all.  That is correct and is not a fault: the signal is still
there, L frames later.

The ceiling is a rule of the **output**, and it is applied by **clipping**.
The limit is the digital ceiling, multiplied by the gain the chain's own
level controls declare: a chain of repair modules is held at 1.0, and a chain
that carries the user's own gain is held at the level that gain asks for (a
user who asks the gain module for +6 dB gets a limit of 1.995: the +6 dB is
carried, and the samples that go over the limit are clipped at it — they are
not scaled back).  A clip is a per sample operation, so it does not move the
level of anything that was inside the limit and it does not depend on the
size of the block the host chopped the stream into.  The rule lives in
[`../aurcore/ceiling.py`](../aurcore/ceiling.py), and it is applied in
[`../aurcore/api.py`](../aurcore/api.py) (the chain's output) and in
[`../plugins/vst3native/AuralisVST3.cpp`](../plugins/vst3native/AuralisVST3.cpp)
(the plug-in's output).

## 2. How the numbers were measured

Three instruments, one per side of the table.

**Our side** is measured by
[`../qa/measure_edge_contract.py`](../qa/measure_edge_contract.py): every
module of the registry is driven over the thirteen edge signals of the study
in one call to `process()`, at 48 kHz, in stereo.

```powershell
python qa\measure_edge_contract.py
```

**Our side is also held by the gate of the tree**,
[`../qa/check_module_edges.py`](../qa/check_module_edges.py), which drives
every module over its own six edge signals in 256 sample blocks, with a warm
up of 4096 samples of silence, and measures the ring-out the way the
independent host of `qa/vst3host` measures it: a tone goes in, silence
follows, and the ring-out is the number of samples the output stays above
**-60 dBFS** after the input stopped.  Three excitations are used — the tone
of the host, the full scale sine and the signal at the clipping ceiling — and
the longest of the three is the number, because a module has to hold its
contract for every input and not only for the one the host happens to feed.

```powershell
python qa\check_module_edges.py
```

**The references** were measured by the study, through the study's own host,
at 48 kHz in stereo, and the render was measured with the same code, so the
two sides of the table were measured with one instrument.  The study's ring
out is counted at the **-180 dBFS** floor, which is not the floor the tree
uses, and the two floors are two measurements: the tree's gate and the study's
instrument say different numbers about the same module, and both are right.

The gate holds two more rules: **the silence** (the peak of the output on a silent input may not be above 1e-6, which is -120 dBFS) and **the length of the material** (the level of the output may not move by more than 0.5 dB between a short material and a long one).  The numbers of the two rules are in [`../qa/edge_rules.py`](../qa/edge_rules.py).

The tree was being worked on while this note was written: the voice de-noise
was rewritten at 23:19 and the spatial suppressor was written in the same
hour.  Every number of ours below is the number of the tree of **2026-10-21,
23:30**, and the gate is what says whether it still holds.  The spatial
suppressor is the one module the survey does not know yet — it has no
specification in `qa/survey/specs.py` and no row in the report — so
`tests/test_module_survey.py` fails on it, and the survey does not measure
its block delta.

The rule of the ceiling moved on **2026-10-22**: it is at the output of the
product and not inside a module any more, and it clips instead of scaling.
The numbers of the ceiling in §4 were measured after the move, on the tree of
**2026-10-22, 02:30**; the other numbers are the ones of the 21st.

## 3. The two references

Neither reference is named.  One is a **plug-in module** and the other is
**installed from an installer**; the study measured both against the same
edge set.

| case | the plug-in module: peak out | samples over 1.0 | the installed one: peak out | frames out | non finite |
|---|---|---|---|---|---|
| silence | 0.000000 | 0 | exact zeros | = in | 0 |
| a tone at the ceiling | 0.999642 | 0 | = in | = in | 0 |
| an impulse | 0.999925 | 0 | = in | = in | 0 |
| a square at the ceiling | **1.000729** | **47996** | = in | = in | 0 |
| an offset at the ceiling | **1.000729** | **47996** | = in | = in | 0 |
| one sample | 0 (the answer comes 1726 samples later) | 0 | = in | = in | 0 |

Three things come out of the table.

**The plug-in module holds whole blocks.**  It is fed one sample and it hands
back nothing: the answer comes **1726 samples** later, at the block of 512
frames the study used.  Its latency is exactly the delay it has at every
block size, and it moves with the block size because the plug-in holds whole
blocks of its own: 1582 samples at 16 frames, 1470 at 128 and 256, 1726 at
512, 3262 at 2048 and 5310 at 4096.  The installed reference has a latency
of **3993 samples** at 48 kHz and does not move with the block size at all.

**The gain of the plug-in module is linear**: **+0.00627 dB** below 1 kHz and
**-0.00555 dB** at 1 kHz, the same at every level from 0.01 to 1.0.  That is
why a signal at the ceiling comes out **above** the ceiling: the stems the
plug-in sums back together do not add up to exactly one.  It is a design
choice and not a numerical accident, and it is the bar: **a module of ours
that lets the ceiling through untouched is at the reference, and one that lets
it through louder is not.**

**Both declare a tail of zero.**  The plug-in module rings for **1518
frames** above the -180 dBFS floor, so it under-declares a little; the
installed one rings for **zero frames** at the usual thresholds and 243
frames below -30 dB.  The rule of the tree counts the latency in — the limit
of a module is its latency plus its tail — so the plug-in module holds it
comfortably: 1518 frames of ring-out against a limit of 1726.

## 4. Where we stand

The family has **51 modules** in the registry today.

| rule | ours | the plug-in module | the installed one |
|---|---|---|---|
| the length | **1 frame for 1 frame**, every one of the 51 modules | the answer comes 1726 samples later | 1 frame for 1 frame |
| the finiteness | **0 samples** that are not finite, over the 13 edge signals of the study, for every module | 0 | 0 |
| the silence | **0.0** for 48 of the 51 modules that declare a finite tail; the spectral de-noise is at **2.74e-14** (-271 dBFS); the generator is at **0.128** and says so with an infinite tail | 0.0 | 0.0 |
| the ring out | the longest of the family is **4848 frames** (the leveler and the loudness control, which ring for exactly the latency they declare); the voice de-noise rings for **3375** against a limit of **3583** | 1518 frames above the -180 dBFS floor, a limit of 1726 | 0 frames |
| the ceiling | a module's own answer comes out above the ceiling for **21 of the 51** modules (the band restoration at 2.233088, the de-click at 1.886750, the wow and flutter at 1.279561, the resampler at 1.270637, the generator at 1.128866 with 47980 samples over): the rule is at the **output** of the product and not inside a module.  The **chain's** output is **1.000000** and **not one sample** outside [-1, 1] for **all 51** of them — the chain clips at the limit it declares | 1.000729 with 47996 samples over | 1.0, nothing over |
| the block size | **40 of the 50** surveyed modules are block coherent (the -40 dB budget of the survey); the 51st module is not in the survey | the latency moves with the block size | the latency does not move |
| the length of the material | **0.00 dBFS at every length** for every module that is ready.  The guitar de-noise and the voice de-noise declare that they need 8192 frames of material before their answer is final, and the fade declares that it shapes the material over its own length | not measured | not measured |

Two modules of ours make a sound of their own, and both say so: the
**generator** and the **ambience match** declare an **infinite tail**, because
the generator makes a tone and the ambience match lays a bed of its own under
the recording.  The rule of the tree says that a module which never goes
quiet has to say so, and the gate checks it.  With no learned profile the
ambience match is silent, and that is why the table above puts it at zero.

The ring out of the family, module by module, is in the table of the gate
(`python qa\check_module_edges.py`): **26 of the 51** modules that declare a
finite tail go quiet the moment the input stops, **45** ring no longer than
they declare, and **4** ring a little longer — the deconstruct by 24
samples, the de-ess by 42, the resample by 47 and the wow and flutter by 63.
All four are inside the **64 sample** allowance of the gate (1.33 ms at
48 kHz, the same allowance the product gives a latency).

The two level followers are the best of the family for the block size: the
leveler and the loudness control are at **-153.0 dB**, against a budget of
-40 dB.  The modules that are outside the budget are the music rebalance
(-5.3 dB), the ambience match (-6.8), the repair assistant (-8.3), the
generator (-9.3), the de-clip and the guitar de-noise (-10.2), the de-bleed
(-10.8), the de-hum (-20.8), the interpolate (-22.0) and the spectral
recovery (-23.9).

## 5. The rules that are gates, and the rules that are not

| rule | the gate | where it runs |
|---|---|---|
| the length | `qa/check_module_edges.py` (the "length" rule), `tests/test_module_edges.py` | the `edges` entry of `scripts/run_gates.py` |
| the finiteness | `qa/check_module_edges.py` (the "finite" rule) and `qa/check_module_finite.py` | the `edges` and the `finite` entries of `scripts/run_gates.py` |
| the ring out | the "tail" rule of `qa/check_module_edges.py`, with the 64 sample allowance, and `tests/test_tail.py` | the `edges` entry of `scripts/run_gates.py` |
| the ceiling | the rule lives in `aurcore/ceiling.py` and is applied at the **output** of the product: the chain's output (`aurcore/api.py`) and the plug-in's output (`plugins/vst3native/AuralisVST3.cpp`).  It is applied by clipping, not by scaling.  The gate is `tests/test_module_robustness.py` (the chain, the written file, the level controls, the generators) and `tests/test_ceiling.py` (the clip, the limit, the block independence) | the `pytest` entry of `scripts/run_gates.py`; `qa/measure_ceiling.py` is the instrument, not a gate |
| the block size | `tests/block_coherence.py` holds **5** modules (the leveler, the loudness control, the streaming preview, the centre extract and the fade) to the -40 dB budget.  The survey measures the number for every module, but its gate does not hold it | the `pytest` entry of `scripts/run_gates.py` |
| **the silence** | the rule is in [`../qa/edge_rules.py`](../qa/edge_rules.py) (1.0e-6, which is -120 dBFS) and the gate is `qa/check_module_edges.py` (the "silence" rule) and `tests/test_module_edges.py` | the `edges` entry of `scripts/run_gates.py` |
| **the length of the material** | the rule is in [`../qa/edge_rules.py`](../qa/edge_rules.py) (0.5 dB) and the gate is `qa/check_module_edges.py` (the "material" rule) and `tests/test_module_edges.py` | the `edges` entry of `scripts/run_gates.py` |

## 6. What the study said, and what is true now

The study measured the family on an older tree.  The tree has moved since —
it has 51 modules now, the ceiling rule is in `aurcore/ceiling.py` and the
edge gate is in `qa/check_module_edges.py` — so the claims of the study are
corrected here, one by one.

| the study said | the tree says today |
|---|---|
| 51 modules | **51 modules**: the spatial suppressor was added |
| the ring out of **13 modules** is longer than they declare (the rustle 3326 against 2048, the de-reverb 3187 against 2048, the voice de-noise 3554 against 2594, …) | **4 modules** ring longer than they declare, by 24, 42, 47 and 63 samples, and the allowance of the gate is 64 samples.  The declarations of the family were raised after the study, so the thirteen are gone |
| **23 of the 50** modules are outside the block coherence budget, and the two level followers are the worst by a long way (the leveler at +15.7 dB and the loudness control at +14.8 dB) | **10 of the 50** surveyed modules are outside the budget, and the two level followers are the **best** of the family at -153.0 dB each.  The estimators were rebuilt on a grid that does not move with the block |
| **9 of the 50** modules let a full scale signal come out above the ceiling (the ambience match at +5.8 dBFS, the deconstruct at +2.4, the band restoration at +1.2, …) | the rule is at the **output** of the product now, so a module's own answer may go over the ceiling: **21 of the 51** do (the band restoration at 2.233088, the de-click at 1.886750, the wow and flutter at 1.279561, the resampler at 1.270637).  The **chain's** output is clipped at the limit the chain declares and is inside [-1, 1] for **all 51**, and a file the product writes never exceeds the digital ceiling |
| **2 of the 50** modules let the level depend on the length of the material (the ambience match at +0.8 dBFS after 512 frames, +5.8 after 4096 and +12.8 after 49152; the leveler collapsing by 22 dB after a third of a second) | the **ambience match is at 0.00 dBFS** at every length, and the **leveler is at a constant -23.01 dBFS** from 8192 frames on (below that it answers nothing, which is its latency).  Two modules still let the level depend on the length: the **guitar de-noise** (-4.51 dBFS at 512 frames, 0.00 from 8192 on) and the **voice de-noise** (-2.84 dBFS at 4096 frames, 0.00 from 8192 on).  The fade does it on purpose: it shapes the material over its length |
| the first four rules hold for every module of ours | they do, and so do the two that came last: the edge gate passes over all 51 modules and holds five rules.  The two modules that make a sound of nothing — the generator, and the ambience match when it has a learned profile — say so with an infinite tail, and the gate reads that declaration from the module |
| the measurement is in `qa/benchmark/edge_cases.py` | the **constants** are there; the **instrument** is `qa/measure_edge_contract.py` and the **gate** is `qa/check_module_edges.py` |
| the reference answers one frame for one frame | the reference that is a plug-in module answers a one frame input **1726 samples later**, because it holds whole blocks; over the stream the length is the same.  The reference that is installed from an installer answers one frame for one frame, like every module of ours |

## 7. What to do first

1. **The four modules that ring a little longer than they declare.**  They
   are inside the allowance today, so nothing fails, but a declaration is
   supposed to be the tail the module has.
2. **The two estimators that let the level depend on the block size.**  A
   level estimator that depends on the block size of the host is an
   estimator that is not ready when the material starts.

The two rules that were not gates -- the silence and the length of the
material -- are gates now.  Their numbers are in
[`../qa/edge_rules.py`](../qa/edge_rules.py) and the gate that holds them is
[`../qa/check_module_edges.py`](../qa/check_module_edges.py).

## 8. The files

| file | what it is |
|---|---|
| [`../qa/benchmark/edge_cases.py`](../qa/benchmark/edge_cases.py) | the numbers of the edge contract, with the case, the unit, the way they were measured and the series they came from |
| [`../qa/measure_edge_contract.py`](../qa/measure_edge_contract.py) | the instrument of our side: every module over the edge signals |
| [`../qa/check_module_edges.py`](../qa/check_module_edges.py) | the gate against the edges of every module: finite, length, tail, silence and the length of the material |
| [`../qa/edge_rules.py`](../qa/edge_rules.py) | the numbers of the two rules that came last: the silence and the length of the material |
| [`../aurcore/ceiling.py`](../aurcore/ceiling.py) | the rule of the digital ceiling, the limit of a chain and the clip that applies it |
| [`../aurcore/api.py`](../aurcore/api.py) | the engine: the chain's output, where the rule is applied |
| [`../plugins/vst3native/AuralisVST3.cpp`](../plugins/vst3native/AuralisVST3.cpp) | the adapter: the plug-in's output, where the promise is made |
| [`../qa/measure_ceiling.py`](../qa/measure_ceiling.py) | the instrument of the ceiling: every module over seven edge cases |
| [`../tests/test_module_edges.py`](../tests/test_module_edges.py) | the test of the edge gate |
| [`../tests/test_module_robustness.py`](../tests/test_module_robustness.py) | the test of the ceiling: the chain, the written file, the level controls |
| [`../tests/test_ceiling.py`](../tests/test_ceiling.py) | the test of the rule itself: the clip, the limit, the block independence |
| [`../tests/block_coherence.py`](../tests/block_coherence.py) | the gate of the block coherence, for the five modules that follow the level |
| [`../qa/survey/`](../qa/survey/) | the survey of every module: the block delta, the latency, the cost |
