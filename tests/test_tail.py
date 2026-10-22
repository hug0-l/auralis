"""
The tail of a module, and the tail of a chain.

A tail says how long a module keeps ringing after the input stopped.  It
is the answer a plug-in has to give its host, and the host measures it:
the independent host in ``qa/vst3host`` feeds a tone, stops it, and
watches how long the output keeps going.  A plug-in that says nothing
about its tail is a plug-in whose host cuts the sound off.

These tests check four things:

  * the concept itself -- a module declares a tail, the engine reports the
    longest tail of the chain, and a module that never goes quiet says so;
  * the declarations of **every** module of the catalogue -- measured
    against a measurement made the way the host makes it;
  * the delay the modules declare -- the declared delay is the real one;
  * the block size -- the declaration of a module is the same whatever the
    host uses to chop the stream up.

The rule
--------
**The tail a module declares must be the tail it has.**  Concretely:

  * the ring-out the host measures must not outlast the quiet time the
    module declares (``latency_samples + tail_samples``) by more than
    :data:`TAIL_TOLERANCE`;
  * a module may declare *more* than it needs: the de-hum and the guitar
    de-noise do, because the ring-out of those two depends on the material
    they are built for (the hum of the mains, the ceiling of the signal)
    and the declaration is the worst case over that material.  A module may
    never declare *less*: that is the case where the host cuts the sound
    off;
  * a module that never goes quiet says so, and a module that says so
    never does go quiet;
  * the declared latency is the delay the module really adds, and neither
    the latency nor the tail moves when the host changes the size of the
    block it chops the stream up with.

The tolerance
-------------
:data:`TAIL_TOLERANCE` is 64 samples, 1.33 ms at 48 kHz.

The reference product's own rule of thumb is stricter: its ring-out is
covered to the sample, with nothing to spare.  Ours cannot be that strict,
because the residue of an analysis window is not the same number from one
state of a module to the next: the de-construct was measured ringing for 5
samples beyond its look-ahead in one state and for 547 in another, a spread
of 542 samples.

What the family itself says is the tighter of the two numbers.  Measured
the way the host measures it, the modules of this family that are correct
leave a residue of 0 samples (de-construct), 17 (de-ess), 24 (wow and
flutter), 47 (resample), 49 (band restoration) and 54 (de-reverb).  64
samples covers every one of them, it is the same allowance the host gives
for the delay (:data:`DELAY_TOLERANCE`, 64 samples, "below that the
misalignment is inaudible"), and it is an eighth of the 2048-sample
analysis window the residue comes from.

The measurement
---------------
The signal has the same length at every block size -- 8192 samples of tone
and 8192 samples of silence -- so that the ring-out of a module can be
compared across the block sizes without the length of the material
confounding it.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
import pytest

from aurcore import registry
from aurcore.api import create_engine, module_tail
from aurcore.params import TAIL_INFINITE

# --------------------------------------------------------------------------
# the measurement, the way the host makes it
# --------------------------------------------------------------------------

#: The block size the host uses.
BLOCK = 256

#: The block sizes the declaration is checked at.  64 is the smallest block
#: a host may use and 4096 the largest one the product is measured at; the
#: two in between are the ones the engine is used with.
BLOCKS = (64, 256, 1024, 4096)

#: The rate the host runs at.
SAMPLE_RATE = 48000

#: The tone the host feeds in.
TONE_HZ = 1000.0
TONE_AMPLITUDE = 0.5

#: The level below which a sample counts as silence: -60 dBFS.  The host
#: uses the same one, so the two measurements mean the same thing.
SILENCE = 1.0e-3

#: How many samples of tone the host feeds in, and how many samples of
#: silence it watches afterwards.  The numbers are samples and not blocks:
#: the ring-out of a module is compared across the block sizes, and the
#: material has to be the same material every time.
TONE_SAMPLES = 8192
SILENCE_SAMPLES = 8192

#: How much the declared tail may differ from the measured tail, in
#: samples.  See the module docstring for the number and the reason.
TAIL_TOLERANCE = 64

#: How much delay a module may have without declaring it, in samples.
#: 64 samples are 1.33 ms at 48 kHz: below that the misalignment is
#: inaudible.  The host uses the same allowance.
DELAY_TOLERANCE = 64

#: The parameters the host has moved when it measures, and the modules
#: whose ring-out depends on them.  The host drives parameter 0 of every
#: plug-in before it measures the delay and the tail, so a measurement
#: that does not do the same measures a different module.
HOST_PARAMETERS = {
    "AUR_DECONSTRUCT": {"Tonal Gain": -6.0},
    "AUR_VOICEDENOISE": {"Optimize For": "Music"},
    "AUR_GUITARDENOISE": {"Amp Sensitivity": 0.25},
}

#: The modules whose output is not a delayed copy of their input, so the
#: lag the correlation finds is not a delay.  Their delay is checked
#: through the ring-out instead, and the correlation is checked to be low
#: here, so that the list cannot grow silently.
NOT_A_DELAY = {
    "AUR_DEBLEED": "the module takes the bleed out, so the output is not the input",
    "AUR_WOWFLUTTER": (
        "the module changes the speed of the signal, so the lag the host "
        "finds is the time scale and not a delay"
    ),
}

#: The correlation below which the lag of the measurement is not a delay.
NO_CORRELATION = 0.6

#: The modules that need a profile learned before they are measured.  The
#: ambience matcher makes a bed of its own, and the bed only follows the
#: reference profile once one has been learned: without a profile the bed
#: fades away with the source, and the module does not ring forever.
LEARNED_PROFILE = ("AUR_AMBIENCEMATCH",)


def _tone(count: int) -> np.ndarray:
    t = np.arange(count, dtype=np.float64) / SAMPLE_RATE
    return TONE_AMPLITUDE * np.sin(2.0 * np.pi * TONE_HZ * t)


def _sweep(count: int) -> np.ndarray:
    """A linear sweep from 200 Hz to 18 kHz, the way the host builds it."""
    t = np.arange(count, dtype=np.float64) / SAMPLE_RATE
    slope = (18000.0 - 200.0) / max(t[-1], 1e-9)
    phase = 2.0 * np.pi * (200.0 * t + 0.5 * slope * t * t)
    return TONE_AMPLITUDE * np.sin(phase)


def _feed(engine, signal: np.ndarray, block: int) -> np.ndarray:
    """Run a signal through an engine in blocks, and collect the output."""
    out: list[np.ndarray] = []
    for start in range(0, signal.shape[0], block):
        piece = signal[start : start + block]
        if piece.shape[0] < block:
            piece = np.pad(piece, (0, block - piece.shape[0]))
        buf = np.stack([piece] * 2, axis=1)
        out.append(np.asarray(engine.process(buf))[:, 0])
    return np.concatenate(out) if out else np.zeros(0, dtype=np.float64)


def _engine(code: str, params: dict | None = None, block: int = BLOCK):
    engine = create_engine(SAMPLE_RATE, 2)
    engine.add_module(code)
    for name, value in (params or {}).items():
        engine.set_param(0, name, value)
    if code in LEARNED_PROFILE:
        # the bed of the ambience matcher only lasts as long as the
        # reference profile it follows
        reference = np.random.default_rng(11).standard_normal(8192) * 0.01
        engine.learn(0, np.stack([reference] * 2, axis=1))
    return engine


def measure_ring_out(code: str, params: dict | None = None, block: int = BLOCK) -> int:
    """How long the output of a module keeps going after the input stopped.

    The figure counts from the end of the input and includes the latency
    the module reports, so it is the number the host measures.  The tail
    a module declares counts from the end of its latency, so the two are
    compared after the latency is taken off.

    A module that never goes quiet returns the whole length of the silence
    it was watched for: the host cannot tell how long such a module rings,
    and the answer "at least this long" is the honest one.
    """
    engine = _engine(code, params, block)
    signal = np.concatenate([_tone(TONE_SAMPLES), np.zeros(SILENCE_SAMPLES)])
    stream = _feed(engine, signal, block)

    loud = np.nonzero(np.abs(stream[TONE_SAMPLES:]) >= SILENCE)[0]
    if loud.size == 0:
        return 0
    return int(loud[-1]) + 1


def _best_lag(reference: np.ndarray, answer: np.ndarray, max_lag: int) -> tuple[int, float]:
    """The lag at which the answer looks most like the reference.

    The correlation is taken through a transform, so the whole range of
    lags is looked at at once: the loop over the lags costs more than the
    rest of the measurement put together.
    """
    size = 1
    while size < reference.shape[0] + answer.shape[0]:
        size <<= 1
    ref = np.fft.rfft(reference, size)
    ans = np.fft.rfft(answer, size)
    corr = np.fft.irfft(ref * np.conj(ans), size)
    # ``corr[k]`` is the sum of ``reference[i] * answer[i - k]``, so the
    # lag we are after is ``corr[(size - lag) % size]``.
    window = np.concatenate((corr[:1], corr[size - max_lag :][::-1]))
    index = int(np.argmax(window))
    return index, float(window[index])


def measure_delay(code: str, params: dict | None = None, block: int = BLOCK) -> tuple[int, float]:
    """The delay a module really adds, and how well the output matches.

    The delay is the lag at which the output of the module looks most
    like the input; the correlation says whether that lag means anything.
    """
    engine = _engine(code, params, block)
    total = 2 * TONE_SAMPLES
    sweep = _sweep(total)
    swept = _feed(engine, sweep, block)

    half = total // 2
    reference = sweep[half:]
    answer = swept[half : half + reference.shape[0]]

    best_lag, best = _best_lag(reference, answer, reference.shape[0] - 1)

    # The correlation is taken over the samples the two have in common: a
    # module that delays its output by more than half of the sweep has
    # only that much overlap, and the parts that do not overlap carry no
    # energy and would drag the figure down for no reason.
    count = min(reference.shape[0], answer.shape[0] - best_lag)
    overlap_in = reference[:count]
    overlap_out = answer[best_lag : best_lag + count]
    energy_in = float(np.dot(overlap_in, overlap_in))
    energy_out = float(np.dot(overlap_out, overlap_out))
    correlation = best / (np.sqrt(energy_in * energy_out) + 1e-30)
    return best_lag, correlation


def measure_declaration(code: str, params: dict | None = None, block: int = BLOCK) -> tuple[int,
                                                                                            int]:
    """The latency and the tail a module declares, in the samples of its rate.

    A module that computes its figure from the filter it has built needs a
    block of audio before the figure is there, so one block is fed first.
    """
    engine = _engine(code, params, block)
    engine.process(np.zeros((block, 2), dtype=np.float64))
    return int(engine.latency_samples), int(module_tail(engine.chain[0].module))


# --------------------------------------------------------------------------
# the survey of the whole family
# --------------------------------------------------------------------------

#: Every module of the catalogue.  The gate runs over all of them and not
#: over a hand-picked few: a rule that is only checked on three modules is
#: a rule that three modules pass.
CODES = [info.code for info in registry.all()]


@dataclass
class Measurement:
    """What was measured about one module."""

    code: str
    latency: dict[int, int] = field(default_factory=dict)
    tail: dict[int, int] = field(default_factory=dict)
    ring: dict[int, int] = field(default_factory=dict)
    delay: int = 0
    correlation: float = 0.0

    @property
    def declared_latency(self) -> int:
        return self.latency[BLOCK]

    @property
    def declared_tail(self) -> int:
        return self.tail[BLOCK]

    @property
    def ring_out(self) -> int:
        return self.ring[BLOCK]

    @property
    def quiet_time(self) -> int:
        """The time the module promises to be quiet, in samples."""
        return self.declared_latency + self.declared_tail

    @property
    def never_quiet(self) -> bool:
        """Whether the module says that it never goes quiet."""
        return self.declared_tail == TAIL_INFINITE

    def keeps_going(self, block: int = BLOCK) -> bool:
        """Whether the module is still making sound at the end of the window.

        A module that never goes quiet fills the whole window the host
        watches; the last sample of the window may fall below the
        threshold, so the allowance is one block.
        """
        return self.ring[block] >= SILENCE_SAMPLES - block


@dataclass
class Survey:
    """The measurement of every module of the catalogue."""

    modules: dict[str, Measurement]
    seconds: float

    def __getitem__(self, code: str) -> Measurement:
        return self.modules[code]


@pytest.fixture(scope="module")
def survey() -> Survey:
    """Measure every module of the catalogue, once.

    The measurement is the expensive part of the gate, so it is taken once
    and every test reads the result.  The time it took is kept, so that the
    gate can hold itself to being a gate.
    """
    start = time.perf_counter()
    modules: dict[str, Measurement] = {}
    for code in CODES:
        params = HOST_PARAMETERS.get(code)
        measurement = Measurement(code=code)
        for block in BLOCKS:
            latency, tail = measure_declaration(code, params, block)
            measurement.latency[block] = latency
            measurement.tail[block] = tail
            measurement.ring[block] = measure_ring_out(code, params, block)
        measurement.delay, measurement.correlation = measure_delay(code, params)
        modules[code] = measurement
    return Survey(modules=modules, seconds=time.perf_counter() - start)


# --------------------------------------------------------------------------
# the concept
# --------------------------------------------------------------------------

def test_a_module_that_says_nothing_has_no_tail():
    engine = create_engine(SAMPLE_RATE, 2)
    engine.add_module("AUR_GAIN")
    assert engine.tail_samples == 0


def test_the_engine_reports_the_tail_of_the_chain():
    engine = create_engine(SAMPLE_RATE, 2)
    engine.add_module("AUR_DECONSTRUCT")
    assert engine.tail_samples == 1214


def test_the_tail_of_a_chain_is_the_longest_one_and_not_the_sum():
    """Two modules, two tails: the chain waits for the longer of them.

    The modules of a chain are clocked together, so their ring-outs
    overlap, and a host that chains two plug-ins waits for the longer of
    the two tails.  A sum would hold a finished session open for as long
    as the chain is long.
    """
    engine = create_engine(SAMPLE_RATE, 2)
    engine.add_module("AUR_DECONSTRUCT")        # 1214 samples
    engine.add_module("AUR_VOICEDENOISE")        # 1536 samples
    engine.add_module("AUR_GUITARDENOISE")      # 1536 samples

    tails = [module_tail(step.module) for step in engine.chain]
    assert sorted(tails) == [1214, 1536, 1536]

    assert engine.tail_samples == 1536, (
        "the tail of a chain is the longest tail of its modules, not the sum"
    )
    assert engine.tail_samples != sum(tails)


def test_a_module_that_never_goes_quiet_makes_the_chain_never_go_quiet():
    engine = create_engine(SAMPLE_RATE, 2)
    engine.add_module("AUR_DECONSTRUCT")
    engine.add_module("AUR_SIGNALGEN")
    assert engine.tail_samples == TAIL_INFINITE


def test_a_bypassed_module_does_not_ring():
    engine = create_engine(SAMPLE_RATE, 2)
    engine.add_module("AUR_SIGNALGEN")
    engine.add_module("AUR_DECONSTRUCT")
    engine.set_bypass(0, True)
    assert engine.tail_samples == 1214


def test_every_module_of_the_registry_declares_a_tail():
    """Every module answers the question, even when the answer is zero."""
    for info in registry.all():
        engine = create_engine(SAMPLE_RATE, 2)
        engine.add_module(info.code)
        tail = engine.tail_samples
        assert tail >= 0, f"{info.code}: the tail is negative"
        assert tail == 0 or tail == TAIL_INFINITE or tail < TAIL_INFINITE


# --------------------------------------------------------------------------
# the gate over the whole family
# --------------------------------------------------------------------------

def test_the_gate_covers_every_module_of_the_catalogue(survey: Survey):
    """The gate is a gate: it looks at every module the catalogue has.

    The measurement is taken over the registry, so a module that is added
    to the catalogue is measured the day it arrives.
    """
    assert len(CODES) == len(registry.all())
    assert set(CODES) == {info.code for info in registry.all()}
    assert len(survey.modules) == len(CODES)


@pytest.mark.parametrize("code", CODES)
def test_the_declared_tail_covers_the_measured_ring_out(code: str, survey: Survey):
    """The tail a module declares must be the tail it has.

    The ring-out is measured the way the host measures it: a tone, then
    silence, then the number of samples the output keeps coming.  The
    quiet time the module promises is its latency plus its tail, and the
    ring-out must not outlast it.
    """
    measured = survey[code]
    if measured.never_quiet:
        # the module says it never goes quiet: it has to keep going
        assert measured.keeps_going(), (
            f"{code}: the module declares an infinite tail, but it goes "
            f"quiet after {measured.ring_out} samples"
        )
        return

    excess = measured.ring_out - measured.quiet_time
    assert excess <= TAIL_TOLERANCE, (
        f"{code}: the module declares a latency of {measured.declared_latency} "
        f"samples and a tail of {measured.declared_tail} samples, so it is "
        f"quiet {measured.quiet_time} samples after the input stopped, but "
        f"the host measures {measured.ring_out} samples of ring-out: "
        f"{excess} samples too many"
    )


@pytest.mark.parametrize("code", CODES)
def test_a_module_that_never_goes_quiet_says_so(code: str, survey: Survey):
    """The two modules that make a sound of their own say so.

    A module that rings forever and says nothing leaves the host waiting
    for a number that never comes; a module that says it rings forever and
    then goes quiet is lying to the host in the other direction.
    """
    measured = survey[code]
    if measured.keeps_going():
        assert measured.never_quiet or measured.quiet_time >= SILENCE_SAMPLES, (
            f"{code}: the module keeps ringing for the whole silence the host "
            f"watches ({measured.ring_out} samples) and declares a tail of "
            f"{measured.declared_tail} samples"
        )
    elif measured.never_quiet:
        assert False, (
            f"{code}: the module declares an infinite tail, but the host "
            f"measures {measured.ring_out} samples of ring-out and the "
            f"module does go quiet"
        )


@pytest.mark.parametrize("code", CODES)
def test_the_declared_latency_is_the_real_delay(code: str, survey: Survey):
    """The delay a module declares is the delay it really adds.

    The measurement compares the second half of a sweep with the second
    half of the output and looks for the lag at which the two agree.  For
    the few modules whose output is not a delayed copy of their input the
    lag means something else, and those are listed with the reason; their
    delay is covered by the ring-out instead.
    """
    measured = survey[code]
    if code in NOT_A_DELAY:
        assert measured.correlation < NO_CORRELATION, (
            f"{code}: the output still looks like the input "
            f"(the correlation is {measured.correlation:.3f}), so the "
            f"measurement is a delay after all and the module has to be "
            f"measured like the others"
        )
        # the delay is the ring-out of a module that declares no tail
        assert measured.ring_out - measured.declared_latency <= TAIL_TOLERANCE, (
            f"{code}: the module declares a latency of "
            f"{measured.declared_latency} samples and no tail, but the host "
            f"measures {measured.ring_out} samples of ring-out"
        )
        return

    assert measured.correlation >= NO_CORRELATION, (
        f"{code}: the output does not look like the input (the correlation "
        f"is {measured.correlation:.3f}), so the delay could not be measured"
    )
    difference = abs(measured.delay - measured.declared_latency)
    assert difference <= DELAY_TOLERANCE, (
        f"{code}: the module delays the output by {measured.delay} samples "
        f"and reports a latency of {measured.declared_latency} samples"
    )


@pytest.mark.parametrize("code", CODES)
def test_the_declaration_does_not_move_with_the_block_size(code: str, survey: Survey):
    """The latency and the tail of a module are its own, not the host's.

    A number that moves when the host changes the size of the block it
    chops the stream up with is a number the host cannot trust: it has to
    ask again and re-compensate every time it changes.  The voice de-noise
    is the shape of a correct module: 2047 samples at 64, 256, 1024 and
    4096.
    """
    measured = survey[code]
    for block in BLOCKS:
        assert measured.latency[block] == measured.declared_latency, (
            f"{code}: the module declares a latency of "
            f"{measured.declared_latency} samples at {BLOCK} samples per "
            f"block and {measured.latency[block]} samples at {block}"
        )
        assert measured.tail[block] == measured.declared_tail, (
            f"{code}: the module declares a tail of {measured.declared_tail} "
            f"samples at {BLOCK} samples per block and "
            f"{measured.tail[block]} samples at {block}"
        )


@pytest.mark.parametrize("code", CODES)
def test_the_ring_out_does_not_move_with_the_block_size(code: str, survey: Survey):
    """The ring-out of a module does not depend on the way it is fed.

    The material is the same at every block size -- the same 8192 samples
    of tone and the same 8192 samples of silence -- so the only thing
    that changes is where the host cuts the stream up.  A module that
    rings longer at one block size than at another is a module whose
    behaviour depends on the host, and the number it declares cannot be
    right at every block size.
    """
    measured = survey[code]
    if measured.never_quiet:
        # the module never goes quiet: every block size has to show that
        for block in BLOCKS:
            assert measured.keeps_going(block), (
                f"{code}: the module declares an infinite tail but goes quiet "
                f"after {measured.ring[block]} samples at {block} samples per "
                f"block"
            )
        return

    rings = [measured.ring[block] for block in BLOCKS]
    spread = max(rings) - min(rings)
    assert spread <= TAIL_TOLERANCE, (
        f"{code}: the ring-out of the module depends on the block size: "
        + ", ".join(f"{measured.ring[block]} at {block}" for block in BLOCKS)
        + f" samples, a spread of {spread} samples"
    )


def test_the_gate_is_a_gate_and_not_a_test_nobody_runs(survey: Survey):
    """The gate has to be quick enough to be run.

    A gate that takes longer than a minute is a gate that is run once a
    quarter, and a gate that is run once a quarter is not a gate.
    """
    assert survey.seconds < 60.0, (
        f"the gate measured the whole family in {survey.seconds:.1f} seconds, "
        "which is longer than the minute a gate may take"
    )


# --------------------------------------------------------------------------
# the voice de-noise: the number it declares and the number it has
# --------------------------------------------------------------------------

def test_the_voice_de_noise_declares_the_tail_it_has():
    """The voice de-noise is measured against its own declaration.

    The ring-out of the module is the end of the wanted content, spread
    over the analysis window, and the declaration has to cover it.  The
    numbers are the ones measured with the instrument of this file on
    2026-10-21: the module declares 1536 samples of tail on top of the
    2047 samples of look-ahead, and the host measures 3279 samples of
    ring-out for the tone of the measurement -- 1232 samples beyond the
    look-ahead, and 304 samples inside the declaration.

    The offline configuration analyses a window of twice the length, so
    the tail it has is twice the size: it declares 3072 samples.
    """
    params = HOST_PARAMETERS["AUR_VOICEDENOISE"]

    latency, tail = measure_declaration("AUR_VOICEDENOISE", params)
    assert latency == 2047
    assert tail == 1536

    measured = measure_ring_out("AUR_VOICEDENOISE", params)
    assert abs(measured - 3279) <= 16, (
        f"AUR_VOICEDENOISE: the host measures {measured} samples of ring-out; "
        "3279 samples were measured on 2026-10-21"
    )
    assert measured <= latency + tail, (
        f"AUR_VOICEDENOISE: the module declares a quiet time of "
        f"{latency + tail} samples and the host measures {measured} samples "
        "of ring-out"
    )

    offline = create_engine(SAMPLE_RATE, 2)
    offline.processing_mode = "offline"
    offline.add_module("AUR_VOICEDENOISE")
    # the chain gives the mode to its modules when it runs, so the figure is
    # read after a block has gone through.
    offline.process(np.zeros((BLOCK, 2), dtype=np.float64))
    assert module_tail(offline.chain[0].module) == 3072


# --------------------------------------------------------------------------
# the chain
# --------------------------------------------------------------------------

def test_the_latency_of_a_chain_is_the_sum_of_its_stages():
    """A chain of stages is as late as the sum of the stages."""
    engine = create_engine(SAMPLE_RATE, 2)
    engine.add_module("AUR_DECONSTRUCT")        # 2048 samples
    engine.add_module("AUR_VOICEDENOISE")        # 2047 samples
    assert engine.latency_samples == 2048 + 2047


def test_the_latency_of_a_chain_brings_the_stages_into_the_engines_rate():
    """A stage that works at another rate is brought into the engine's rate.

    The engine runs at 48 kHz and a stage behind the resampler runs at
    88.2 kHz, so the delay of that stage is counted in the samples of
    88.2 kHz and has to be told to the host in the samples of 48 kHz.
    """
    engine = create_engine(SAMPLE_RATE, 2)
    engine.add_module("AUR_RESAMPLE")
    engine.set_param(0, "Sample Rate", "88200")
    engine.add_module("AUR_DECONSTRUCT")

    assert engine.output_sample_rate == 88200

    # the same chain at 88.2 kHz needs no conversion back, so its latency
    # is the plain sum of the stages: 48 samples of the resampler and 2048
    # samples of the de-construct, both in the samples of 88.2 kHz.
    native = create_engine(88200, 2)
    native.add_module("AUR_RESAMPLE")
    native.set_param(0, "Sample Rate", "88200")
    native.add_module("AUR_DECONSTRUCT")
    assert native.latency_samples == 48 + 2048

    # the host, which works at 48 kHz, has to be told the same number in
    # the samples of 48 kHz, plus the look ahead of the conversion back.
    brought = int(round(native.latency_samples * (48000.0 / 88200.0)))
    assert brought <= engine.latency_samples <= brought + DELAY_TOLERANCE, (
        f"the chain at 48 kHz reports a latency of {engine.latency_samples} "
        f"samples; the stages add up to {brought} samples in the samples of "
        "48 kHz, and the conversion back may add its own look ahead"
    )


def test_the_tail_of_a_chain_is_reported_in_the_engines_rate():
    """A tail that is counted at another rate is brought into the engine's.

    The chain waits for the longer of its tails, and the host, which works
    at 48 kHz, has to be told the number in the samples of 48 kHz.
    """
    engine = create_engine(SAMPLE_RATE, 2)
    engine.add_module("AUR_RESAMPLE")
    engine.set_param(0, "Sample Rate", "88200")
    engine.add_module("AUR_DECONSTRUCT")

    tails = [module_tail(step.module) for step in engine.chain]
    expected = int(round(max(tails) * (48000.0 / 88200.0)))
    assert engine.tail_samples == expected


def test_an_infinite_tail_is_reported_as_infinite_and_not_as_a_number():
    """A stage that never stops makes the tail of the chain infinite."""
    engine = create_engine(SAMPLE_RATE, 2)
    engine.add_module("AUR_SIGNALGEN")
    engine.add_module("AUR_DECONSTRUCT")
    assert engine.tail_samples == TAIL_INFINITE
    assert engine.tail_samples != TAIL_INFINITE - 1


def test_a_bypassed_stage_does_not_add_to_the_latency_of_the_chain():
    """A stage that is not running adds nothing to the chain."""
    engine = create_engine(SAMPLE_RATE, 2)
    engine.add_module("AUR_DECONSTRUCT")
    engine.add_module("AUR_VOICEDENOISE")
    engine.set_bypass(1, True)
    assert engine.latency_samples == 2048
    assert engine.tail_samples == 1214
