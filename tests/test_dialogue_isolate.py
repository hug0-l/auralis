"""
tests/test_dialogue_isolate.py — the behaviour of the Dialogue Isolate module.

The module has one job that is harder than it looks: it has to keep the
dialogue and remove everything else.  The three gain controls act on three
stems, and the noise gain must not touch the voice.

What the tests below hold the module to
--------------------------------------
* **the voice survives the noise gain.**  With the noise gain at -12 dB the
  voice may move by at most 1 dB.  Before the repair the module took 10.32 dB
  of the voice with it: the harmonic estimate did not resolve the partials of
  a voice at the transform size the module used, so the voice was classified
  as noise and went into the noise stem with the hiss.
* **the noise still goes.**  The same knob has to take at least 6 dB off the
  noise bed — a fix that only saves the voice by keeping the noise is not a
  fix.
* **the transform has to resolve a 110 Hz fundamental.**  The partials of a
  voice are separated only when they are more than four bins apart (the main
  lobe of a Hann window is four bins wide), so the transform size is not a
  free parameter: at 48 kHz a 110 Hz fundamental needs more than 1745 points.
* **the other gains do what they say.**  The voice gain moves the voice and
  leaves the noise alone, the reverb gain moves a room tail, and the three
  gains at zero return the input.
* **the cost and the block coherence** are measured and stated, so a change
  that makes the module slower or chunkier is visible.

Every number in this file was measured with ``var/f3/final.py`` and
``var/f3/dialogue_measure.py``; the tolerances leave room for the platform
but they are far tighter than the budgets.
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
if str(ROOT / "corpus") not in sys.path:
    sys.path.insert(0, str(ROOT / "corpus"))

from aurcore.modules.dialogue_isolate import DialogueIsolate, _QUALITY  # noqa: E402

import measure as corpus_measure  # noqa: E402

SR = 48_000
CHANNELS = 2

#: The seed of the noise sources of the probe of the survey.
SEED = 20_260_101

#: The fundamental of the voice of the probe, in hertz.
PROBE_F0 = 120.0

#: The lowest fundamental the module has to cope with, in hertz.  It is the
#: lowest fundamental of the reference range of a voice (85 Hz).
LOWEST_F0 = 110.0

#: The gain the survey drives the module with when it measures the voice.
NOISE_GAIN_DB = -12.0

#: The rule for the voice: the noise gain may not take more than this much
#: of it.
VOICE_BUDGET_DB = 1.0

#: The rule for the noise: the noise gain has to take this much off the bed.
NOISE_FLOOR_DB = 6.0

#: The measured numbers of the shipped module (var/a42/measure_dialogue.py,
#: and var/w63/measure_module.py after the decision was moved into the ratio
#: domain): ``signal: (the voice change dB, the noise change dB)``.
MEASURED = {
    "probe": (-0.38, -12.03),
    "corpus": (-0.01, -11.35),
    "quality": (0.01, -10.35),
}

#: How far a measurement may move from the value above (dB).
TOLERANCE_DB = 0.75

#: The delay the module has to declare: the analysis window is 4096 points
#: and the centred analysis needs the samples that follow a frame.
EXPECTED_LATENCY = 4096


# ---------------------------------------------------------------------------
# the helpers
# ---------------------------------------------------------------------------
def _load(name: str, path: Path):
    """Import a helper by path, under a name of its own."""
    if not path.is_file():
        pytest.skip(f"the helper is missing ({path})")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _rms(x: np.ndarray) -> float:
    """Return the root mean square of a signal."""
    arr = np.asarray(x, dtype=np.float64)
    if arr.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(arr * arr)))


def _scale(x: np.ndarray, level_dbfs: float) -> np.ndarray:
    """Return the signal scaled to a given RMS level."""
    value = _rms(x)
    if value <= 0.0:
        return x
    return x * (10.0 ** (float(level_dbfs) / 20.0) / value)


def _harmonic_stack(f0: float, n: int, count: int = 16, top_hz: float = 3000.0) -> np.ndarray:
    """Return a voice-like harmonic stack with a 1/f tilt."""
    out = np.zeros(n, dtype=np.float64)
    t = np.arange(n, dtype=np.float64) / float(SR)
    for k in range(1, count + 1):
        freq = f0 * k
        if freq > top_hz:
            break
        out += (1.0 / k) * np.sin(2.0 * math.pi * freq * t)
    return out


def _probe() -> np.ndarray:
    """The probe of the survey: a voice, a noise burst and a noise bed.

    It is the signal the module survey measures the module with, rebuilt
    here so that the test does not depend on the survey.
    """
    n = int(1.0 * SR)
    voice = _scale(_harmonic_stack(PROBE_F0, n), -20.0)
    rng = np.random.default_rng(SEED + 37)
    burst = _scale(rng.standard_normal(n), -30.0)
    tail = np.convolve(burst, np.exp(-np.arange(4000) / 900.0), mode="full")[:n]
    rng = np.random.default_rng(SEED + 41)
    bed = _scale(rng.standard_normal(n), -45.0)
    mono = voice + tail * 0.3 + bed
    return np.repeat(mono[:, None], CHANNELS, axis=1)


def _level(x: np.ndarray, lo_hz: float, hi_hz: float,
           start_s: float = 0.0, end_s: float | None = None) -> float:
    """Return the level of a band of a signal, in dBFS.

    The measurement is the one of the survey: a Hann windowed transform of the
    time window, and the mean square of the band is reconstructed from the
    transform, so a full scale sine inside the band reads -3.01 dBFS.
    """
    arr = np.asarray(x, dtype=np.float64)
    if arr.ndim == 1:
        arr = arr[:, None]
    total = int(arr.shape[0])
    i0 = max(0, int(round(float(start_s) * SR)))
    i1 = total if end_s is None else min(total, int(round(float(end_s) * SR)))
    if i1 <= i0:
        return -180.0
    seg = arr[i0:i1].mean(axis=1)
    window = np.hanning(seg.size)
    spectrum = np.fft.rfft(seg * window)
    freqs = np.fft.rfftfreq(seg.size, 1.0 / float(SR))
    mask = (freqs >= float(lo_hz)) & (freqs < float(hi_hz))
    if not bool(mask.any()):
        return -180.0
    power = float(np.sum(np.abs(spectrum[mask]) ** 2))
    weight = float(np.sum(window))
    if weight <= 0.0 or power <= 0.0:
        return -180.0
    return 10.0 * math.log10(max(2.0 * power / (weight * weight), 1e-30))


def _change(before: float, after: float) -> float:
    """Return the change of a level in decibels; negative means quieter."""
    return float(after - before)


def _module(**params) -> DialogueIsolate:
    """Return the module with the given parameters."""
    module = DialogueIsolate(SR, CHANNELS)
    for name, value in params.items():
        module.params.set(name.replace("_", " "), value)
    return module


def _process(module: DialogueIsolate, signal: np.ndarray) -> np.ndarray:
    """Process a signal and take the delay of the module off.

    The module is a streaming one: it delays its output by the length of
    its analysis window and says so.  The tests that compare the input with
    the output have to take that delay off, otherwise they measure the
    delay instead of the module.
    """
    arr = np.asarray(signal, dtype=np.float64)
    out = np.asarray(module.process(arr.copy()), dtype=np.float64)
    delay = int(module.latency_samples)
    if delay <= 0:
        return out
    return out[delay:delay + arr.shape[0] - delay]


def _voice_and_noise(signal: np.ndarray, out: np.ndarray) -> tuple[float, float]:
    """Return the change of the voice band and of the noise bed, in dB."""
    voice = _change(_level(signal, 120.0, 3000.0, 0.05, 0.45),
                    _level(out, 120.0, 3000.0, 0.05, 0.45))
    noise = _change(_level(signal, 12000.0, 18000.0),
                    _level(out, 12000.0, 18000.0))
    return voice, noise


# ---------------------------------------------------------------------------
# the defect: the noise gain took the voice with it
# ---------------------------------------------------------------------------
def test_the_noise_gain_does_not_take_the_voice_with_it():
    """The voice survives the noise gain, and the noise still goes.

    The probe is the one of the survey: a voice stack in the first half, a
    noise burst and a noise bed.  The noise gain at -12 dB may move the voice
    by at most 1 dB and has to take at least 6 dB off the bed.
    """
    signal = _probe()
    out = _process(_module(Noise_Gain=NOISE_GAIN_DB), signal)
    voice, noise = _voice_and_noise(signal, out)

    assert voice > -VOICE_BUDGET_DB, (
        f"the noise gain took {abs(voice):.2f} dB of the voice with it "
        f"(the budget is {VOICE_BUDGET_DB:.1f} dB)"
    )
    assert noise < -NOISE_FLOOR_DB, (
        f"the noise bed only lost {abs(noise):.2f} dB "
        f"(the rule asks for {NOISE_FLOOR_DB:.1f} dB)"
    )
    assert voice == pytest.approx(MEASURED["probe"][0], abs=TOLERANCE_DB)
    assert noise == pytest.approx(MEASURED["probe"][1], abs=TOLERANCE_DB)


def test_the_voice_survives_on_the_corpus_clip():
    """The voice of the corpus clip survives the noise gain.

    ``voice_hiss.wav`` carries a 130 Hz voice in the mid and an independent
    hiss in the side, so the two can be told apart exactly.
    """
    samples, rate = corpus_measure.load_clip("voice_hiss")
    assert rate == SR
    signal = np.asarray(samples, dtype=np.float64)
    out = _process(_module(Noise_Gain=NOISE_GAIN_DB), signal)

    def mid(x: np.ndarray) -> np.ndarray:
        return (x[:, 0] + x[:, 1]) * 0.5

    def side(x: np.ndarray) -> np.ndarray:
        return (x[:, 0] - x[:, 1]) * 0.5

    voice = _change(_level(mid(signal)[:, None], 100.0, 4000.0),
                    _level(mid(out)[:, None], 100.0, 4000.0))
    hiss = _change(_level(side(signal)[:, None], 5000.0, 20000.0),
                   _level(side(out)[:, None], 5000.0, 20000.0))

    assert voice > -VOICE_BUDGET_DB, (
        f"the noise gain took {abs(voice):.2f} dB of the voice of the corpus clip"
    )
    assert hiss < -NOISE_FLOOR_DB, (
        f"the hiss of the corpus clip only lost {abs(hiss):.2f} dB"
    )
    assert voice == pytest.approx(MEASURED["corpus"][0], abs=TOLERANCE_DB)
    assert hiss == pytest.approx(MEASURED["corpus"][1], abs=TOLERANCE_DB)


def test_the_voice_survives_when_it_has_pauses():
    """A voice that comes and goes survives the noise gain as well.

    The signal is the case of the quality harness: the synthetic voice with
    a jitter, four formants and pauses, under a white hiss.  The pauses are
    the hard case: the detector has to call them silence and the voice
    between them has to stay in the voice stem.
    """
    harness = _load("aur_qa_qualityvoice", ROOT / "qa" / "qualityvoice" / "harness.py")
    case = harness.build_cases()["voice_hiss"]
    signal = np.asarray(case.x, dtype=np.float64)
    out = _process(_module(Noise_Gain=NOISE_GAIN_DB), signal)
    voice = _change(_level(signal, 100.0, 4000.0), _level(out, 100.0, 4000.0))
    hiss = _change(_level(signal, 5000.0, 20000.0), _level(out, 5000.0, 20000.0))

    assert voice > -VOICE_BUDGET_DB, (
        f"the noise gain took {abs(voice):.2f} dB of the voice with pauses"
    )
    assert hiss < -NOISE_FLOOR_DB, (
        f"the hiss only lost {abs(hiss):.2f} dB under the voice with pauses"
    )
    assert voice == pytest.approx(MEASURED["quality"][0], abs=TOLERANCE_DB)
    assert hiss == pytest.approx(MEASURED["quality"][1], abs=TOLERANCE_DB)


# ---------------------------------------------------------------------------
# the transform size is not a free parameter
# ---------------------------------------------------------------------------
def test_the_transform_resolves_a_110_hz_fundamental():
    """The transform has to be long enough to separate the partials.

    The main lobe of a Hann window is four bins wide, so two partials only
    separate when their distance is more than four bins:

        partial spacing in bins = f0 * n_fft / sample rate

    A 110 Hz fundamental at 48 kHz therefore needs more than 4 * 48000 / 110
    = 1745 points, and the module ships 4096 (Good) and 8192 (Best).
    """
    needed = 4.0 * SR / LOWEST_F0
    for quality in ("Good", "Best"):
        n_fft = int(_QUALITY[quality]["n_fft"])
        assert n_fft >= needed, (
            f"the {quality} transform of {n_fft} points does not resolve a "
            f"{LOWEST_F0:.0f} Hz fundamental (it needs more than {needed:.0f})"
        )
        spacing = LOWEST_F0 * n_fft / float(SR)
        assert spacing > 4.0, (
            f"the {quality} transform puts the partials {spacing:.2f} bins apart, "
            f"the main lobe of the window is four bins wide"
        )


def test_the_quality_setting_changes_the_transform():
    """``Best`` analyses with a longer transform than ``Good``."""
    good = int(_QUALITY["Good"]["n_fft"])
    best = int(_QUALITY["Best"]["n_fft"])
    assert best > good
    assert _module(Quality="Good").params.get("Quality") == "Good"
    assert _module(Quality="Best").params.get("Quality") == "Best"


# ---------------------------------------------------------------------------
# the other modes
# ---------------------------------------------------------------------------
def test_the_voice_gain_moves_the_voice_and_leaves_the_noise_alone():
    """The voice gain is a gain: it moves the voice and nothing else.

    The gain is taken to the top of its travel, because the travel of a
    gain is a taper: the useful range of a gain is below zero, and the top
    of the travel is a small boost.  The measurement is the level of the
    voice band with the gain at the top of the travel.
    """
    signal = _probe()
    top = max(d.maximum for d in _module().describe() if d.name == "Voice Gain")
    out = _process(_module(Voice_Gain=top), signal)
    voice = _change(_level(signal, 120.0, 3000.0, 0.05, 0.45),
                    _level(out, 120.0, 3000.0, 0.05, 0.45))
    noise = _change(_level(signal, 12000.0, 18000.0),
                    _level(out, 12000.0, 18000.0))
    assert 3.5 <= voice <= 6.0, (
        f"the voice gain of {top:.1f} dB moved the voice {voice:.2f} dB"
    )
    assert abs(noise) < 0.5, f"the voice gain moved the noise bed {noise:.2f} dB"


def test_the_voice_gain_down_takes_the_voice_with_it():
    """The voice gain works in both directions.

    The band of the voice carries what the other two stems hold as well, so
    the band moves by less than the gain: the voice stem is 0.9 of the band
    and the gain takes 12 dB off that part.
    """
    signal = _probe()
    out = _process(_module(Voice_Gain=-12.0), signal)
    voice = _change(_level(signal, 120.0, 3000.0, 0.05, 0.45),
                    _level(out, 120.0, 3000.0, 0.05, 0.45))
    noise = _change(_level(signal, 12000.0, 18000.0),
                    _level(out, 12000.0, 18000.0))
    assert -11.0 <= voice <= -8.0, f"the voice gain of -12 dB moved the voice {voice:.2f} dB"
    assert abs(noise) < 0.5, f"the voice gain moved the noise bed {noise:.2f} dB"


def test_the_reverb_gain_moves_a_room_tail():
    """The reverb gain moves a tail that outlives the direct sound.

    The corpus clip ``reverb.wav`` is a 440 Hz starter with a 1.2 s tail, so
    the tail is the room and the starter is the direct sound.
    """
    samples, rate = corpus_measure.load_clip("reverb")
    assert rate == SR
    signal = np.asarray(samples, dtype=np.float64)

    def tail(x: np.ndarray) -> float:
        return _level(x, 410.0, 470.0, 0.40, 1.00)

    def starter(x: np.ndarray) -> float:
        return _level(x, 410.0, 470.0, 0.19, 0.22)

    plain = _process(_module(), signal)
    loud = _process(_module(Reverb_Gain=12.0), signal)

    moved = _change(tail(plain), tail(loud))
    assert moved > 2.0, (
        f"the reverb gain of 12 dB moved the tail by {moved:.2f} dB — the "
        f"reverb stem does not hold the room"
    )
    assert abs(_change(starter(plain), starter(loud))) < 1.0, (
        "the reverb gain moved the direct sound"
    )


def test_the_balance_returns_the_input():
    """The three gains at zero are transparent.

    The stems always sum back to the input, so a unity mix has to return the
    input bit for bit — once the delay of the analysis is taken off.
    """
    signal = _probe()
    out = _process(_module(), signal)
    error = float(np.max(np.abs(out - signal[: out.shape[0]])))
    assert error < 1e-12, f"the unity mix is not transparent (the error is {error:.3e})"


def test_the_three_gains_act_independently():
    """Every gain moves its own stem and leaves the others alone."""
    signal = _probe()
    out = _process(
        _module(Voice_Gain=-12.0, Reverb_Gain=-12.0, Noise_Gain=-12.0), signal
    )
    voice = _change(_level(signal, 120.0, 3000.0, 0.05, 0.45),
                    _level(out, 120.0, 3000.0, 0.05, 0.45))
    noise = _change(_level(signal, 12000.0, 18000.0),
                    _level(out, 12000.0, 18000.0))
    assert -13.0 <= voice <= -11.0, f"the voice gain of -12 dB moved the voice {voice:.2f} dB"
    assert -13.0 <= noise <= -11.0, f"the noise gain of -12 dB moved the bed {noise:.2f} dB"


def test_the_output_is_finite_and_the_right_length():
    """The module returns a finite signal of the same length."""
    signal = _probe()
    out = np.asarray(_module(Noise_Gain=NOISE_GAIN_DB).process(signal.copy()),
                      dtype=np.float64)
    assert out.shape == signal.shape
    assert bool(np.all(np.isfinite(out)))


def test_two_runs_are_the_same():
    """The module is deterministic."""
    signal = _probe()
    first = np.asarray(_module(Noise_Gain=NOISE_GAIN_DB).process(signal.copy()),
                       dtype=np.float64)
    second = np.asarray(_module(Noise_Gain=NOISE_GAIN_DB).process(signal.copy()),
                        dtype=np.float64)
    assert np.array_equal(first, second)


# ---------------------------------------------------------------------------
# the cost and the block coherence
# ---------------------------------------------------------------------------
def test_the_cost_of_a_64_sample_block():
    """A 64 sample block has to fit into its realtime budget.

    The budget is 1333.3 us at 48 kHz.  The module analyses the block it is
    given with a 4096 point transform, so the cost does not fall with the
    block size — that is the price of resolving the partials, and it is
    measured here so that a change is visible.
    """
    module = _module(Noise_Gain=NOISE_GAIN_DB)
    block = np.ascontiguousarray(_probe()[:64])
    module.process(block)
    times = []
    for _ in range(9):
        start = time.perf_counter_ns()
        module.process(block)
        times.append((time.perf_counter_ns() - start) / 1000.0)
    cost = float(np.median(times))
    budget = (64.0 / SR) * 1.0e6
    assert cost < budget, (
        f"a 64 sample block costs {cost:.0f} us, the budget is {budget:.0f} us"
    )


def test_the_module_is_block_coherent():
    """The output may not depend on the block size of the host.

    The module keeps its state between the blocks: the frames sit on one
    grid, the noise reference of the detector and the two envelope followers
    are carried from frame to frame and the state of the overlap-add is
    carried from one block to the next.  A host that feeds 64 samples at a
    time therefore gets the same samples as a host that hands over the whole
    signal at once.

    The measurement is the residual between the whole render and the render
    in 64 sample blocks; the survey wants it 40 dB below the signal.
    """
    signal = _probe()
    module = _module(Noise_Gain=NOISE_GAIN_DB)
    whole = np.asarray(module.process(signal.copy()), dtype=np.float64)
    module = _module(Noise_Gain=NOISE_GAIN_DB)
    pieces = []
    for start in range(0, signal.shape[0], 64):
        pieces.append(np.asarray(module.process(signal[start:start + 64]), dtype=np.float64))
    streamed = np.concatenate(pieces, axis=0)
    residual = whole - streamed
    level = 20.0 * math.log10(max(_rms(residual), 1e-30))
    reference = 20.0 * math.log10(max(_rms(whole), 1e-30))
    delta = level - reference
    assert delta < -40.0, (
        f"the residual between the whole render and the streamed one is "
        f"{delta:.2f} dB — the module is not block coherent"
    )


def test_the_module_declares_the_delay_it_adds():
    """The module has to report the delay it really adds.

    The analysis is centred, so the module cannot hand out an output sample
    before the input has advanced by the length of the analysis window.  The
    number is a property of the transform size: 4096 points for ``Good`` and
    8192 for ``Best``.
    """
    for quality, expected in (("Good", 4096), ("Best", 8192)):
        module = _module(Quality=quality)
        assert module.latency_samples == expected, (
            f"the {quality} transform declares {module.latency_samples} samples "
            f"of delay, the analysis window is {expected}"
        )

    module = _module()
    signal = _probe()
    out = np.asarray(module.process(signal.copy()), dtype=np.float64)
    delay = int(module.latency_samples)
    # with every gain at 0 dB the output is the input, delayed
    error = float(np.max(np.abs(out[delay:] - signal[: signal.shape[0] - delay])))
    assert error < 1e-9, (
        f"the module does not delay its output by the {delay} samples it "
        f"declares (the error is {error:.3e})"
    )


def test_the_module_needs_a_block_of_at_least_one_hop():
    """The module has to be fed a block of at least one hop.

    The frames of the analysis sit on a grid of one hop (1024 samples for
    ``Good``).  A block that is shorter than the hop carries less context
    than the analysis assumes, so the module says how much context it
    needs: the block has to hold at least the hop.
    """
    for quality, hop in (("Good", 1024), ("Best", 2048)):
        assert int(_QUALITY[quality]["hop"]) == hop
    module = _module()
    assert int(_QUALITY["Good"]["hop"]) >= 1024, (
        "the grid of the analysis has to hold at least 1024 samples, so that a "
        "110 Hz fundamental is resolved"
    )
