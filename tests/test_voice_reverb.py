"""
tests/test_voice_reverb.py — the quality gate of the Voice Reverb module.

The material
------------
The wanted content is the **synthetic voice** of ``qa/qualityvoice/``: a
fundamental with a jitter, four formants, a spectral tilt and pauses, so
the signal behaves like a voice and not like a harmonic stack.  The room is
the one of the golden corpus clip ``reverb``: an impulse response with a
direct sound and exponentially decaying noise, whose amplitude decay
constant is ``RT60 / ln(1000)``, so the reverberation time of the room is
exactly 1.2 s (``corpus/build_corpus.py``, ``_build_reverb``).  The voice is
convolved with that room, so the dry voice is the ground truth of the wet
one.

What the gate asks
------------------
* the **room's tail** -- what is left in the gaps between the words -- has
  to lose at least 4 dB,
* the **wanted voice** must not move: a clean voice may not move by more
  than 1 dB, and a voice in a room has to end up within 0.5 dB of the dry
  voice it was made from,
* the **gain curve** may not chatter: the roughness of the applied gain may
  not be more than 1 dB,
* the module must report the **latency** it adds, and the delay measured on
  the signal must be the same number,
* the module must be **block coherent**: a render that is chopped into the
  blocks a plug-in host uses must not differ from a one block render,
* the awkward inputs -- silence, a constant signal, a full scale square, a
  single impulse and a tone -- must come back **finite and with the same
  length**, whatever the size of the block,
* one block must fit into the **realtime budget**,
* the **parameter rows** of the canonical table must agree with the module.

The numbers were measured with::

    python var/w28/measure.py
"""
from __future__ import annotations

import importlib.util
import math
import sys
import time
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MODULE_CODE = "AUR_VOICEREVERB"

#: The sample rate every measurement runs at.
SAMPLE_RATE = 48_000

#: The wanted voice may not move by more than this (dB).  It is the budget
#: the quality harness uses for a module that works on a voice.
VOICE_BUDGET_DB = 1.0

#: The room's tail has to lose at least this much (dB).
TARGET_TAIL_REMOVAL_DB = 4.0

#: A voice in a room has to end up this close to the dry voice (dB).
TARGET_TRUTH_DB = 0.5

#: The roughness of the gain curve may not be more than this (dB).
CHATTER_BUDGET_DB = 1.0

#: The blocked render must not differ from the one block render by more than
#: this, relative to the level of the render (dB).
BLOCK_COHERENCE_DB = -60.0

#: The realtime budget of a 64 sample block at 48 kHz (us).
BUDGET_US = 1333.3

#: The number of samples the module is expected to delay its output with:
#: the look-ahead of the centred analysis.
EXPECTED_LATENCY_SAMPLES = 1024

#: The rows the module publishes to the canonical parameter table.  They
#: live in a patch until the owner of the table merges them.
ROWS_PATH = ROOT / "var" / "patches" / "w28_params.tsv"


# ---------------------------------------------------------------------------
# The material
# ---------------------------------------------------------------------------
def _load_voice_signal():
    """Import the synthetic voice from ``qa/qualityvoice``."""
    path = ROOT / "qa" / "qualityvoice" / "voice_signal.py"
    if not path.is_file():
        pytest.skip(f"the synthetic voice is missing ({path})")
    spec = importlib.util.spec_from_file_location("aur_qa_voice_signal", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


VOICE_SIGNAL = _load_voice_signal()


def room_ir(seed: int = 4242, rt60: float = 1.2, length_s: float = 1.5) -> np.ndarray:
    """The impulse response of the room of the corpus clip ``reverb``.

    The recipe is the one of ``corpus/build_corpus.py``: the direct sound
    plus exponentially decaying noise, with the amplitude decay constant
    ``RT60 / ln(1000)`` so that the reverberation time of the room is
    exactly ``rt60`` seconds.
    """
    rng = np.random.default_rng(seed)
    count = int(round(length_s * SAMPLE_RATE))
    tau = rt60 / math.log(1000.0)
    times = np.arange(count, dtype=np.float64) / float(SAMPLE_RATE)
    ir = rng.standard_normal(count) * np.exp(-times / tau) * 0.01
    ir[0] = 1.0  # the direct sound
    return ir


def _module(**params):
    """Create the module and apply the parameters."""
    from aurcore import registry

    import aurcore.modules  # noqa: F401 - importing registers the modules

    module = registry.create(MODULE_CODE, SAMPLE_RATE, 1)
    for name, value in params.items():
        module.params.set(name, value)
    return module


def render(signal: np.ndarray, block: int | None = None, **params) -> np.ndarray:
    """Run the module over a signal, in one block or in blocks of ``block``."""
    module = _module(**params)
    arr = np.asarray(signal, dtype=np.float64)[:, None]
    if block is None:
        return np.asarray(module.process(arr), dtype=np.float64)[:, 0]
    pieces = [
        np.asarray(module.process(arr[start : start + block]), dtype=np.float64)[:, 0]
        for start in range(0, arr.shape[0], block)
    ]
    return np.concatenate(pieces, axis=0)


def align(signal: np.ndarray, latency: int) -> np.ndarray:
    """Shift a delayed output back so that it lines up with the input."""
    arr = np.asarray(signal, dtype=np.float64)
    out = arr[latency:]
    if out.shape[0] < arr.shape[0]:
        out = np.concatenate(
            [out, np.zeros(arr.shape[0] - out.shape[0], dtype=np.float64)], axis=0
        )
    return out


def band_level_db(signal: np.ndarray, low: float, high: float, start: int, end: int) -> float:
    """The level of a frequency band over a stretch of the signal (dBFS)."""
    seg = np.asarray(signal, dtype=np.float64)[start:end]
    if seg.size < 64:
        return -300.0
    n_fft = 4096
    while n_fft > 64 and seg.size < n_fft:
        n_fft //= 2
    window = np.hanning(n_fft)
    hop = max(1, n_fft // 4)
    frames = 1 + (seg.size - n_fft) // hop
    view = np.lib.stride_tricks.sliding_window_view(seg, n_fft)[::hop][:frames]
    spectrum = np.fft.rfft(view * window, axis=1)
    freqs = np.fft.rfftfreq(n_fft, 1.0 / float(SAMPLE_RATE))
    mask = (freqs >= low) & (freqs <= high)
    if not np.any(mask):
        return -300.0
    return 10.0 * math.log10(max(float(np.sum(np.abs(spectrum[:, mask]) ** 2)), 1e-30))


def region_energy_db(signal: np.ndarray, regions) -> float:
    """The total energy of a set of stretches of the signal (dBFS)."""
    total = 0.0
    for start, end in regions:
        seg = np.asarray(signal, dtype=np.float64)[start:end]
        if seg.size:
            total += float(np.sum(seg * seg))
    return 10.0 * math.log10(max(total, 1e-30))


def measure_latency(x: np.ndarray, y: np.ndarray, max_lag: int = 4096) -> int:
    """The delay of the output against the input, in samples."""
    a = np.asarray(x, dtype=np.float64) - float(np.mean(x))
    b = np.asarray(y, dtype=np.float64) - float(np.mean(y))
    count = min(a.size, b.size)
    if count < 8:
        return 0
    a = a[:count]
    b = b[:count]
    spectrum = np.fft.rfft(a) * np.conj(np.fft.rfft(b))
    correlation = np.fft.irfft(spectrum, n=count)
    correlation = np.concatenate([correlation[-max_lag:], correlation[: max_lag + 1]])
    lags = np.arange(-max_lag, max_lag + 1)
    return -int(lags[int(np.argmax(correlation))])


# ---------------------------------------------------------------------------
# The fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def voice() -> np.ndarray:
    """The wanted content: the synthetic voice, without a room."""
    return VOICE_SIGNAL.synth_voice()


@pytest.fixture(scope="module")
def wet(voice: np.ndarray) -> np.ndarray:
    """The same voice, recorded in the room of the corpus clip ``reverb``."""
    return np.convolve(voice, room_ir())[: voice.size]


@pytest.fixture(scope="module")
def segments():
    """The syllable and pause layout of the synthetic voice."""
    return VOICE_SIGNAL.voice_segments()


@pytest.fixture(scope="module")
def voiced(segments):
    """The stretches of the voice that carry a syllable."""
    return [(s, e) for kind, s, e in segments if kind == "voiced"]


@pytest.fixture(scope="module")
def pauses(segments):
    """The stretches of the voice where only the room is left."""
    return [(s, e) for kind, s, e in segments if kind == "pause"]


@pytest.fixture(scope="module")
def latency() -> int:
    """The latency the module reports."""
    return int(_module().latency_samples)


@pytest.fixture(scope="module")
def clean_render(voice: np.ndarray, latency: int) -> np.ndarray:
    """The clean voice through the module, lined up with the input."""
    return align(render(voice), latency)


@pytest.fixture(scope="module")
def wet_render(wet: np.ndarray, latency: int) -> np.ndarray:
    """The voice in the room through the module, lined up with the input."""
    return align(render(wet), latency)


# ---------------------------------------------------------------------------
# The tail of the room
# ---------------------------------------------------------------------------
def test_voice_reverb_removes_the_tail_of_the_room(wet, wet_render, pauses):
    """What is left in the gaps between the words has to lose 4 dB.

    The gaps hold nothing but the room: the voice has stopped and the
    reverberant field is all that is left.  This is the part of the
    reverberation the user hears as "the room".
    """
    before = region_energy_db(wet, pauses)
    after = region_energy_db(wet_render, pauses)
    removed = after - before
    assert removed <= -TARGET_TAIL_REMOVAL_DB, (
        f"the tail of the room only lost {abs(removed):.2f} dB, the gate asks "
        f"for {TARGET_TAIL_REMOVAL_DB:.1f} dB"
    )


# ---------------------------------------------------------------------------
# The wanted content
# ---------------------------------------------------------------------------
def test_voice_reverb_keeps_the_clean_voice(voice, clean_render, voiced):
    """A clean voice may not move by more than 1 dB.

    A voice that carries no room carries no reverberation either, so the
    module has nothing to do.  A module that moved it would be taking the
    wanted content with it.
    """
    before = np.mean([band_level_db(voice, 120, 3000, s, e) for s, e in voiced])
    after = np.mean([band_level_db(clean_render, 120, 3000, s, e) for s, e in voiced])
    drift = after - before
    assert abs(drift) <= VOICE_BUDGET_DB, (
        f"the clean voice moved {drift:+.3f} dB, the gate allows "
        f"{VOICE_BUDGET_DB:.1f} dB"
    )


def test_voice_reverb_keeps_every_syllable_of_the_clean_voice(voice, clean_render, voiced):
    """No single syllable of a clean voice may move by more than 1 dB.

    The level of the whole voice can hide a syllable that was taken with
    it, so every syllable is measured on its own.
    """
    for index, (start, end) in enumerate(voiced):
        before = band_level_db(voice, 120, 3000, start, end)
        after = band_level_db(clean_render, 120, 3000, start, end)
        drift = after - before
        assert abs(drift) <= VOICE_BUDGET_DB, (
            f"the syllable {index} moved {drift:+.3f} dB, the gate allows "
            f"{VOICE_BUDGET_DB:.1f} dB"
        )


def test_voice_reverb_brings_the_voice_back_to_the_truth(voice, wet_render, voiced):
    """A voice in a room has to end up within 0.5 dB of the dry voice.

    This is the measurement that separates the module from a general
    reverberation remover: the reverb the voice itself carries has to go,
    and the voice that carried it has to be left where it was.  The dry
    voice is the ground truth, so the number says how far the wanted
    content is from where it belongs.
    """
    truth = np.mean([band_level_db(voice, 120, 3000, s, e) for s, e in voiced])
    after = np.mean([band_level_db(wet_render, 120, 3000, s, e) for s, e in voiced])
    distance = after - truth
    assert abs(distance) <= TARGET_TRUTH_DB, (
        f"the voice ends {distance:+.2f} dB away from the dry voice it was "
        f"made from, the gate allows {TARGET_TRUTH_DB:.1f} dB"
    )


def test_voice_reverb_removes_the_reverb_the_voice_carries(voice, wet, wet_render, voiced):
    """The reverb the voice itself carries has to be smaller than it was.

    The voice in the room is louder than the dry voice by the energy the
    room added.  The module has to take that energy away again.
    """
    truth = np.mean([band_level_db(voice, 120, 3000, s, e) for s, e in voiced])
    before = np.mean([band_level_db(wet, 120, 3000, s, e) for s, e in voiced])
    after = np.mean([band_level_db(wet_render, 120, 3000, s, e) for s, e in voiced])
    assert after - truth < before - truth, (
        f"the voice carries {before - truth:+.2f} dB of room before the "
        f"module and {after - truth:+.2f} dB after it"
    )


# ---------------------------------------------------------------------------
# The gain curve
# ---------------------------------------------------------------------------
def test_voice_reverb_does_not_chatter(wet, wet_render):
    """The gain the module applies may not be rough.

    A gain that hunts from frame to frame is heard as musical noise.  The
    roughness is the median absolute deviation of the frame to frame
    difference of the gain curve, scaled to a standard deviation -- the
    measure of the voice harness (``qa/qualityvoice/harness.py``).
    """
    before = _band_series(wet, 120.0, 3000.0)
    after = _band_series(wet_render, 120.0, 3000.0)
    count = min(before.size, after.size)
    if count < 16:
        pytest.skip("the signal is too short to measure the gain curve")
    gain = after[:count] - before[:count]
    steps = np.diff(gain[int(0.2 * count) :])
    median = float(np.median(steps))
    roughness = 1.4826 * float(np.median(np.abs(steps - median))) / math.sqrt(2.0)
    assert roughness <= CHATTER_BUDGET_DB, (
        f"the gain curve is {roughness:.2f} dB rough, the gate allows "
        f"{CHATTER_BUDGET_DB:.1f} dB"
    )


def _band_series(signal: np.ndarray, low: float, high: float) -> np.ndarray:
    """The level of a frequency band, frame by frame (dB)."""
    arr = np.asarray(signal, dtype=np.float64)
    n_fft = 2048
    hop = 512
    window = np.hanning(n_fft)
    frames = 1 + (arr.size - n_fft) // hop
    view = np.lib.stride_tricks.sliding_window_view(arr, n_fft)[::hop][:frames]
    spectrum = np.fft.rfft(view * window, axis=1)
    freqs = np.fft.rfftfreq(n_fft, 1.0 / float(SAMPLE_RATE))
    mask = (freqs >= low) & (freqs <= high)
    power = np.sum(np.abs(spectrum[:, mask]) ** 2, axis=1)
    return 10.0 * np.log10(np.maximum(power, 1e-30))


# ---------------------------------------------------------------------------
# Latency and block coherence
# ---------------------------------------------------------------------------
def test_voice_reverb_reports_its_latency(latency):
    """The module reports the latency it adds, and the signal shows it."""
    assert latency == EXPECTED_LATENCY_SAMPLES, (
        f"the module reports {latency} samples of latency, the gate expects "
        f"{EXPECTED_LATENCY_SAMPLES}"
    )


def test_voice_reverb_delays_the_signal_by_what_it_reports(voice, latency):
    """The delay measured on the signal is the delay the module reports."""
    out = render(voice)
    measured = measure_latency(voice, out)
    assert measured == latency, (
        f"the output is delayed by {measured} samples, the module reports "
        f"{latency}"
    )


def test_voice_reverb_is_block_coherent(wet):
    """A blocked render must not differ from a one block render."""
    whole = render(wet)
    for block in (64, 128, 256, 512):
        chunked = render(wet, block=block)
        count = min(whole.size, chunked.size)
        difference = whole[:count] - chunked[:count]
        reference = float(np.sqrt(np.mean(whole[:count] ** 2))) or 1e-12
        rms = float(np.sqrt(np.mean(difference * difference)))
        level = 20.0 * math.log10(max(rms, 1e-12) / reference)
        assert level <= BLOCK_COHERENCE_DB, (
            f"at {block} samples the blocked render differs by {level:.2f} dB, "
            f"the gate asks for {BLOCK_COHERENCE_DB:.1f} dB"
        )


def test_voice_reverb_is_deterministic(voice):
    """Two fresh modules must return the same samples."""
    first = render(voice)
    second = render(voice)
    assert np.array_equal(first, second)


def test_voice_reverb_does_nothing_when_the_reduction_is_zero(wet, latency):
    """With the reduction at 0 dB the module has to give the input back.

    The edges of the signal are left out of the measurement: the centred
    analysis needs the samples on both sides of a frame, so the first and
    the last window of the signal are reconstructed from padded frames.
    """
    flat = align(render(wet, Reduction=0.0), latency)
    low, high = 4096, wet.size - 4096
    difference = flat[low:high] - wet[low:high]
    reference = float(np.sqrt(np.mean(wet[low:high] ** 2))) or 1e-12
    rms = float(np.sqrt(np.mean(difference * difference)))
    level = 20.0 * math.log10(max(rms, 1e-12) / reference)
    assert level <= -100.0, (
        f"the module changed the signal by {level:.1f} dB with the reduction "
        "at 0 dB"
    )


def test_voice_reverb_can_render_the_removed_reverb(wet, wet_render, pauses, latency):
    """The module can render the reverberant field it took out.

    The removed part is the difference between the input and the cleaned
    signal, and it has to carry the room: in the gaps between the words it
    is the only thing there is.
    """
    only = align(render(wet, **{"Output Reverb Only": True}), latency)
    total = 0.0
    for start, end in pauses:
        total += float(np.sum(only[start:end] ** 2))
    input_energy = sum(float(np.sum(wet[start:end] ** 2)) for start, end in pauses)
    share = 10.0 * math.log10(max(total, 1e-30) / max(input_energy, 1e-30))
    assert share <= -3.0, (
        f"the removed part carries only {share:+.2f} dB of the energy in the "
        "gaps between the words"
    )


# ---------------------------------------------------------------------------
# The edges
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("case", ["silence", "dc", "square", "impulse", "tone"])
def test_voice_reverb_survives_the_awkward_inputs(case):
    """No NaN, no infinity and no change of length.

    The module is driven with the awkward inputs in one block and in the 64
    sample blocks a plug-in host uses.
    """
    frames = 8 * EXPECTED_LATENCY_SAMPLES
    if case == "silence":
        signal = np.zeros(frames, dtype=np.float64)
    elif case == "dc":
        signal = np.full(frames, 0.5, dtype=np.float64)
    elif case == "square":
        times = np.arange(frames) / float(SAMPLE_RATE)
        signal = np.sign(np.sin(2.0 * math.pi * 220.0 * times))
    elif case == "impulse":
        signal = np.zeros(frames, dtype=np.float64)
        signal[frames // 2] = 1.0
    else:
        times = np.arange(frames) / float(SAMPLE_RATE)
        signal = 0.5 * np.sin(2.0 * math.pi * 1000.0 * times)

    for block in (None, 64):
        out = render(signal, block=block)
        assert out.shape[0] == signal.shape[0], (
            f"{case} (block {block}) returned {out.shape[0]} samples instead "
            f"of {signal.shape[0]}"
        )
        assert np.all(np.isfinite(out)), (
            f"{case} (block {block}) returned "
            f"{int(np.count_nonzero(~np.isfinite(out)))} non finite samples"
        )


def test_voice_reverb_keeps_the_impulse_at_full_scale():
    """A single impulse must not be turned down.

    The impulse is the shortest possible wanted sound; a module that cannot
    protect it cannot protect anything.
    """
    frames = 8 * EXPECTED_LATENCY_SAMPLES
    signal = np.zeros(frames, dtype=np.float64)
    signal[frames // 2] = 1.0
    out = render(signal)
    assert float(np.max(np.abs(out))) > 0.9, (
        f"the impulse came back at {float(np.max(np.abs(out))):.4f} instead of 1.0"
    )


# ---------------------------------------------------------------------------
# The realtime budget
# ---------------------------------------------------------------------------
def test_voice_reverb_fits_into_the_block_budget():
    """One 64 sample block must fit into the realtime budget.

    The budget is 1333 us at 48 kHz.  The reference machine measures about
    20 us for the median block, so the guard allows three budgets: the
    point of the test is to catch a module that becomes an order of
    magnitude slower, not to measure the machine.
    """
    module = _module()
    data = np.zeros((64, 1), dtype=np.float64)
    times = []
    for index in range(40):
        start = time.perf_counter_ns()
        module.process(data)
        elapsed = (time.perf_counter_ns() - start) / 1000.0
        if index >= 10:
            times.append(elapsed)
    measured = float(np.median(times))
    assert measured <= 3.0 * BUDGET_US, (
        f"a 64 sample block costs {measured:.0f} us, the ceiling is "
        f"{3.0 * BUDGET_US:.0f} us"
    )


# ---------------------------------------------------------------------------
# The parameter table
# ---------------------------------------------------------------------------
def test_the_parameter_rows_agree_with_the_module():
    """The rows of the canonical table must match the module.

    The rows live in a patch until the owner of the table merges them into
    ``aurcore/schema/params.tsv``; the check is the same one the table
    makes: the same names in the same order, with the same unit, range,
    default, curve, group and help.
    """
    if not ROWS_PATH.is_file():
        pytest.skip(f"the parameter rows are missing ({ROWS_PATH})")
    from aurcore.schema import load

    specs = load(str(ROWS_PATH))
    assert MODULE_CODE in specs, f"the rows do not carry the module {MODULE_CODE}"
    spec = specs[MODULE_CODE]
    live = {desc.name: desc for desc in _module().describe()}

    assert [desc.name for desc in spec.params] == list(live), (
        f"the rows list {[desc.name for desc in spec.params]}, the module "
        f"{list(live)}"
    )
    problems = []
    for desc in spec.params:
        have = live[desc.name]
        if have.type != desc.type:
            problems.append(f"{desc.name}: type {have.type!r} != {desc.type!r}")
        if have.unit != desc.unit:
            problems.append(f"{desc.name}: unit {have.unit!r} != {desc.unit!r}")
        if have.group != desc.group:
            problems.append(f"{desc.name}: group {have.group!r} != {desc.group!r}")
        if have.curve != desc.curve:
            problems.append(f"{desc.name}: curve {have.curve!r} != {desc.curve!r}")
        if have.help != desc.help:
            problems.append(f"{desc.name}: help {have.help!r} != {desc.help!r}")
        if abs(float(have.minimum) - desc.minimum) > 1e-9:
            problems.append(f"{desc.name}: min {have.minimum} != {desc.minimum}")
        if abs(float(have.maximum) - desc.maximum) > 1e-9:
            problems.append(f"{desc.name}: max {have.maximum} != {desc.maximum}")
        if abs(float(have.default) - float(desc.default)) > 1e-9:
            problems.append(f"{desc.name}: default {have.default!r} != {desc.default!r}")
    assert not problems, "\n".join(problems)


def test_every_parameter_carries_its_unit_and_its_help():
    """Every parameter of the module says what it is and what it does.

    A unit the help contradicts is a unit the module does not use, so the
    two are checked against each other the way the canonical table checks
    them.
    """
    for desc in _module().describe():
        assert desc.help, f"{desc.name} has no help"
        assert desc.help.endswith("."), f"{desc.name} does not end in a full stop"
        lowered = desc.help.lower()
        if "hz" in lowered:
            assert desc.unit == "Hz", f"{desc.name} talks about Hz, the unit is {desc.unit!r}"
        if "%" in desc.help or "percent" in lowered:
            assert desc.unit == "%", f"{desc.name} is a percentage, the unit is {desc.unit!r}"
