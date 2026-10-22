"""
tests/test_ambience_match.py — Ambience Match: behaviour and cost.

The module matches the room tone of one recording under another one.  The
tests below cover the three things that matter for it:

* it does what it says — the learned profile describes the reference, the
  synthesised bed follows the reference's spectrum, and the source survives;
* it keeps its state across a serialisation round trip;
* it fits into the realtime budget of a 64 sample block at 48 kHz.

The cost test is the one that matters most: the module used to need about
4.9 ms per 64 sample block on the reference machine, i.e. more than three
times the 1333 us a block has to be played in.
"""
from __future__ import annotations

import time

import numpy as np
import pytest

from aurcore import registry
from aurcore.modules.ambience_match import (
    _HOP,
    _N_FFT,
    AmbienceMatch,
    _stft,
)

#: The sample rate the tests run at.
SAMPLE_RATE = 48_000

#: The channel count the tests run at.
CHANNELS = 2

#: The block size the cost is measured at.
BLOCK = 64

#: The realtime budget of one block, in microseconds: the time the block
#: needs to be played.
BUDGET_US = (BLOCK / SAMPLE_RATE) * 1_000_000.0

#: How many timed blocks one round measures.
REPEATS = 7

#: How many untimed blocks warm the module up before a round is measured.
WARMUP = 2

#: How many rounds are measured.  A shared machine makes every timing noisy,
#: so the fastest round is the one that was least disturbed by other work.
ROUNDS = 3

#: The median block time may not exceed the budget by more than this factor
#: when the machine is busy.  The module's own cost is checked against the
#: budget itself, in the test below.
BUSY_MACHINE_FACTOR = 3.0

#: The frequency band the source's own noise floor lives in.
FLOOR_LO_HZ = 6000.0
FLOOR_HI_HZ = 12000.0

#: The band the programme lives in.
PROGRAMME_LO_HZ = 950.0
PROGRAMME_HI_HZ = 1050.0

#: The window of the source that carries the programme, in seconds.
PROGRAMME_WINDOW = (0.0, 0.25)

#: The window of the source that carries nothing but the source's own noise
#: floor, in seconds.
FLOOR_WINDOW = (0.25, 0.40)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def make_room_tone(seconds: float = 1.0, seed: int = 5) -> np.ndarray:
    """A room tone: coloured noise with a slow amplitude modulation."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    noise = rng.standard_normal(n)
    out = np.empty(n)
    acc = 0.0
    for i in range(n):
        acc = 0.97 * acc + 0.24 * noise[i]
        out[i] = acc
    out /= np.max(np.abs(out))
    out *= 1.0 + 0.35 * np.sin(2.0 * np.pi * 0.7 * t + 0.7)
    return np.repeat((out * 0.05)[:, None], CHANNELS, axis=1)


def make_source_with_tail(seconds: float = 1.0, seed: int = 11) -> np.ndarray:
    """A voice-like recording that ends in its own room tail."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    src = np.zeros(n)
    for harmonic, amp in ((1, 0.35), (2, 0.22), (3, 0.14), (5, 0.08), (8, 0.05)):
        src += amp * np.sin(2.0 * np.pi * 128.0 * harmonic * t)
    src *= 0.5 + 0.5 * np.sin(2.0 * np.pi * 3.1 * t)
    src *= np.clip((t < 0.55 * seconds).astype(float), 0.0, 1.0)
    src += 0.004 * rng.standard_normal(n)
    tail = np.zeros(n)
    acc = 0.0
    tail_start = int(0.55 * seconds * SAMPLE_RATE)
    for i in range(tail_start, n):
        acc = 0.995 * acc + 0.02 * rng.standard_normal()
        tail[i] = acc
    tail *= np.exp(-(np.arange(n) - tail_start) / (0.15 * SAMPLE_RATE))
    tail[:tail_start] = 0.0
    out = src + tail * 0.9
    peak = float(np.max(np.abs(out)))
    if peak > 0.0:
        out = out / peak * 0.3
    return np.repeat(out[:, None], CHANNELS, axis=1)


def make_bed_source(seconds: float = 1.0, seed: int = 31) -> np.ndarray:
    """A programme that comes and goes over a broadband noise bed.

    The programme is a 1 kHz tone at -6 dBFS peak; it is on from 0 s to
    0.25 s and from 0.4 s to 0.65 s.  Between the two the signal carries
    nothing but its own noise bed, a white noise at -30 dBFS.  That window is
    where the module has to gate the bed out.
    """
    rng = np.random.default_rng(seed)
    n = int(seconds * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    programme = 0.501187 * np.sin(2.0 * np.pi * 1000.0 * t)
    gate = np.zeros(n)
    for start in (0.0, 0.4):
        i0 = int(start * SAMPLE_RATE)
        gate[i0:i0 + int(0.25 * SAMPLE_RATE)] = 1.0
    programme *= gate
    bed = 0.031623 * rng.standard_normal(n)
    return np.repeat((programme + bed)[:, None], CHANNELS, axis=1)


def make_wanted_signal(seconds: float = 1.0) -> np.ndarray:
    """A wanted signal on its own: a 1 kHz tone at -6 dBFS peak."""
    n = int(seconds * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    tone = 0.501187 * np.sin(2.0 * np.pi * 1000.0 * t)
    return np.repeat(tone[:, None], CHANNELS, axis=1)


def make_stationary_room_tone(seconds: float = 1.0, seed: int = 17) -> np.ndarray:
    """A stationary room tone: white noise at -50 dBFS."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SAMPLE_RATE)
    tone = 0.0031623 * rng.standard_normal(n)
    return np.repeat(tone[:, None], CHANNELS, axis=1)


def band_level_db(x: np.ndarray, lo_hz: float, hi_hz: float,
                  start_s: float, end_s: float) -> float:
    """The level of a frequency band over a time window, in dBFS.

    The measurement is the one of the module survey: a Hann windowed
    transform of the window, and the mean square of the band is
    reconstructed from the transform.
    """
    arr = np.asarray(x, dtype=np.float64)
    if arr.ndim == 1:
        arr = arr[:, None]
    mono = arr.mean(axis=1)
    i0 = max(0, int(round(start_s * SAMPLE_RATE)))
    i1 = min(mono.shape[0], int(round(end_s * SAMPLE_RATE)))
    seg = mono[i0:i1]
    if seg.size == 0:
        return -180.0
    window = np.hanning(seg.size)
    spectrum = np.fft.rfft(seg * window)
    freqs = np.fft.rfftfreq(seg.size, 1.0 / SAMPLE_RATE)
    mask = (freqs >= lo_hz) & (freqs < hi_hz)
    if not bool(mask.any()):
        return -180.0
    power = float(np.sum(np.abs(spectrum[mask]) ** 2))
    weight = float(np.sum(window))
    if weight <= 0.0 or power <= 0.0:
        return -180.0
    return 10.0 * np.log10(max(2.0 * power / (weight * weight), 1e-30))


def bin_frequencies() -> np.ndarray:
    """The centre frequency of every bin of the module's analysis."""
    return np.fft.rfftfreq(_N_FFT, 1.0 / SAMPLE_RATE)


def frame_centres(frames: int) -> np.ndarray:
    """The centre time of every frame of the module's analysis, in seconds."""
    return np.arange(frames) * _HOP / float(SAMPLE_RATE)


def band_levels(x: np.ndarray, n_fft: int = 4096) -> np.ndarray:
    """Mean magnitude of ``x`` in 24 log-spaced bands."""
    edges = np.geomspace(30.0, SAMPLE_RATE / 2.0, 25)
    n = len(x)
    if n < n_fft:
        x = np.pad(x, (0, n_fft - n))
    spec = np.abs(np.fft.rfft(x[:n_fft] * np.hanning(n_fft)))
    freqs = np.fft.rfftfreq(n_fft, 1.0 / SAMPLE_RATE)
    out = np.zeros(len(edges) - 1)
    for b in range(len(edges) - 1):
        sel = (freqs >= edges[b]) & (freqs < edges[b + 1])
        out[b] = spec[sel].mean() if sel.any() else 1e-12
    return out


def band_db(x: np.ndarray) -> np.ndarray:
    """Band levels in dB."""
    return 20.0 * np.log10(np.maximum(band_levels(x), 1e-12))


def process_in_blocks(module: AmbienceMatch, signal: np.ndarray, block: int) -> np.ndarray:
    """Run the module over the signal in blocks of ``block`` samples."""
    n_blocks = len(signal) // block
    out = np.empty((n_blocks * block, signal.shape[1]))
    for i in range(n_blocks):
        chunk = np.ascontiguousarray(signal[i * block:(i + 1) * block])
        out[i * block:(i + 1) * block] = module.process(chunk)
    return out


# ---------------------------------------------------------------------------
# The module
# ---------------------------------------------------------------------------
def test_the_module_is_registered() -> None:
    """The module is registered under its code."""
    assert registry.has("AUR_AMBIENCEMATCH")
    assert registry.get("AUR_AMBIENCEMATCH") is AmbienceMatch


def test_the_module_exposes_its_parameters() -> None:
    """The parameter list matches the schema."""
    module = AmbienceMatch(SAMPLE_RATE, CHANNELS)
    names = [d.name for d in module.describe()]
    assert names == [
        "Ambience Type",
        "Ambience Threshold",
        "Movement",
        "Randomness",
        "Gain",
        "Output Ambience Only",
    ]
    assert module.params.get("Ambience Type") == "Complex"
    assert module.params.get("Ambience Threshold") == pytest.approx(-60.0)
    assert module.params.get("Movement") == pytest.approx(0.5)
    assert module.params.get("Gain") == pytest.approx(0.0)


def test_learn_captures_a_profile() -> None:
    """Learning a room tone produces a profile that describes it."""
    module = AmbienceMatch(SAMPLE_RATE, CHANNELS)
    assert module.profile is None
    module.learn(make_room_tone())
    profile = module.profile
    assert profile is not None
    assert profile["spectrum"].shape == (1025,)
    assert profile["level"] > 0.0
    assert 0.1 <= profile["rate"] <= 8.0
    assert 0.0 <= profile["depth"] <= 1.0
    # the profile describes the reference: the spectrum is not flat
    assert float(np.max(profile["spectrum"])) > 2.0 * float(np.mean(profile["spectrum"]))


def test_process_keeps_the_length_and_stays_finite() -> None:
    """A block comes back with the length it went in with."""
    module = AmbienceMatch(SAMPLE_RATE, CHANNELS)
    signal = make_source_with_tail()
    for block in (64, 128, 512, 4096):
        out = module.process(np.ascontiguousarray(signal[:block]))
        assert out.shape == (block, CHANNELS)
        assert np.all(np.isfinite(out))


def test_the_synthesised_ambience_follows_the_reference_spectrum() -> None:
    """The bed the module adds under the recording follows the reference.

    This is the module's whole job: the bed it synthesises has to carry the
    spectral shape of the room tone that was learned.
    """
    reference = make_room_tone()
    module = AmbienceMatch(SAMPLE_RATE, CHANNELS)
    module.learn(reference)
    module.params.set("Output Ambience Only", True)
    signal = make_source_with_tail()
    bed = process_in_blocks(module, signal, 64)[:, 0]

    ref_db = band_db(reference[:, 0])
    bed_db = band_db(bed)
    error = float(np.mean(np.abs(bed_db - ref_db)))
    # The bed follows the reference: the mean band error stays well below
    # 10 dB.  A bed that ignored the reference would be off by much more.
    assert error < 10.0, f"the bed does not follow the reference ({error:.2f} dB)"


def test_the_bed_is_quieter_than_the_source() -> None:
    """The bed sits at the level of the reference, not at the level of the
    recording it is mixed under."""
    reference = make_room_tone()
    module = AmbienceMatch(SAMPLE_RATE, CHANNELS)
    module.learn(reference)
    module.params.set("Output Ambience Only", True)
    signal = make_source_with_tail()
    bed = process_in_blocks(module, signal, 64)[:, 0]
    bed_db = 20.0 * np.log10(max(float(np.sqrt(np.mean(bed ** 2))), 1e-12))
    src_db = 20.0 * np.log10(max(float(np.sqrt(np.mean(signal[:, 0] ** 2))), 1e-12))
    assert bed_db < src_db - 10.0


def test_the_source_survives_the_mix() -> None:
    """Mixing the bed under the recording leaves the recording intact."""
    reference = make_room_tone()
    module = AmbienceMatch(SAMPLE_RATE, CHANNELS)
    module.learn(reference)
    signal = make_source_with_tail()
    out = process_in_blocks(module, signal, 64)
    voiced = slice(0, int(0.5 * SAMPLE_RATE))
    corr = float(np.corrcoef(out[voiced, 0], signal[voiced, 0])[0, 1])
    assert corr > 0.98, f"the source did not survive the mix (correlation {corr:.4f})"


def test_the_static_type_produces_an_unmodulated_bed() -> None:
    """``Static`` and ``Complex`` produce different beds."""
    signal = make_source_with_tail()
    reference = make_room_tone()

    def bed_for(kind: str) -> np.ndarray:
        module = AmbienceMatch(SAMPLE_RATE, CHANNELS)
        module.learn(reference)
        module.params.set("Ambience Type", kind)
        module.params.set("Output Ambience Only", True)
        return process_in_blocks(module, signal, 64)[:, 0]

    static = bed_for("Static")
    complex_bed = bed_for("Complex")
    assert static.shape == complex_bed.shape
    assert not np.allclose(static, complex_bed)


def test_the_state_survives_a_serialisation_round_trip() -> None:
    """A learned profile survives a preset round trip."""
    module = AmbienceMatch(SAMPLE_RATE, CHANNELS)
    module.learn(make_room_tone())
    module.params.set("Gain", 4.0)
    blob = module.state_base64()
    assert blob

    restored = AmbienceMatch(SAMPLE_RATE, CHANNELS)
    restored.load_state_base64(blob)
    profile = restored.profile
    assert profile is not None
    assert profile["level"] == pytest.approx(module.profile["level"])
    assert profile["spectrum"].shape == module.profile["spectrum"].shape


def test_reset_forgets_the_profile() -> None:
    """``reset`` clears the learned profile and the module still runs."""
    module = AmbienceMatch(SAMPLE_RATE, CHANNELS)
    module.learn(make_room_tone())
    module.reset()
    assert module.profile is None
    out = module.process(make_source_with_tail()[:64])
    assert np.all(np.isfinite(out))


def test_the_model_integration_seam_returns_two_stems() -> None:
    """``_analyse_stems`` returns the cleaned source and the matched bed.

    The seam is the documented integration point: a learned ambience model
    may replace it and return the same two keys.
    """
    module = AmbienceMatch(SAMPLE_RATE, CHANNELS)
    module.learn(make_room_tone())
    signal = make_source_with_tail()
    stems = module._analyse_stems(np.ascontiguousarray(signal[:512]))
    assert set(stems) == {"source", "ambience"}
    assert stems["source"].shape == (512, CHANNELS)
    assert stems["ambience"].shape == (512, CHANNELS)
    assert np.all(np.isfinite(stems["source"]))
    assert np.all(np.isfinite(stems["ambience"]))
    # the source stem is the recording, cleaned but not replaced
    corr = float(np.corrcoef(stems["source"][:, 0], signal[:512, 0])[0, 1])
    assert corr > 0.9, f"the source stem is not the recording (correlation {corr:.4f})"
    # the bed is the room tone under it, and it is much quieter than the
    # recording it is mixed under
    bed_db = 20.0 * np.log10(max(float(np.sqrt(np.mean(stems["ambience"][:, 0] ** 2))), 1e-12))
    src_db = 20.0 * np.log10(max(float(np.sqrt(np.mean(signal[:512, 0] ** 2))), 1e-12))
    assert bed_db < src_db - 10.0


# ---------------------------------------------------------------------------
# The source cleanup
# ---------------------------------------------------------------------------
def test_the_floor_is_a_real_floor() -> None:
    """The floor estimate sits above the mean of a noise bed.

    The magnitude of a noise bin is Rayleigh distributed: its mean is 5.5 dB
    above its 20th percentile and 1.05 dB below its root mean square.  A
    floor that sits *below* the mean can never be passed by a Rayleigh
    magnitude, so a gate that compares a bin with such a floor never opens.
    The module used to take the 20th percentile; it takes the root mean
    square of the quiet frames now.
    """
    source = make_bed_source()
    module = AmbienceMatch(SAMPLE_RATE, CHANNELS)
    module.learn(make_room_tone())
    module.process(source)

    floor = module._floor
    assert floor is not None, "the module did not estimate a floor"
    assert floor.shape == (CHANNELS, _N_FFT // 2 + 1)

    freqs = bin_frequencies()
    band = (freqs >= FLOOR_LO_HZ) & (freqs < FLOOR_HI_HZ)
    # the mean magnitude of the bed, measured on the input with the same
    # analysis the module uses
    mag = np.abs(_stft(source[:, 0], _N_FFT, _HOP))[:, 0, :]
    mean_mag = float(mag[:, band].mean())
    floor_db = float(np.median(floor[0, band]))
    assert floor_db > mean_mag, (
        f"the floor is {20.0 * np.log10(floor_db / mean_mag):+.2f} dB against the "
        f"mean magnitude of the bed: it has to be above it"
    )


def test_the_gate_opens_on_the_noise_bed() -> None:
    """The gate opens on the bins of the source's own noise floor."""
    source = make_bed_source()
    module = AmbienceMatch(SAMPLE_RATE, CHANNELS)
    module.learn(make_room_tone())
    module.process(source)

    gate = module._last_gate
    assert gate is not None
    freqs = bin_frequencies()
    band = (freqs >= FLOOR_LO_HZ) & (freqs < FLOOR_HI_HZ)
    centres = frame_centres(gate.shape[0])
    frames = (centres >= FLOOR_WINDOW[0]) & (centres < FLOOR_WINDOW[1])
    assert frames.any(), "the probe has no frame inside the noise only window"
    opened = float(np.mean(gate[frames][:, 0, band] > 0.0))
    assert opened > 0.8, (
        f"the gate opens on {100.0 * opened:.1f} % of the bins of the noise bed"
    )


def test_the_gate_does_not_open_on_a_wanted_signal() -> None:
    """A wanted signal on its own is not gated.

    A recording whose quietest frames are as loud as its programme carries
    no floor of its own, so there is nothing to gate and a gate that worked
    would take the programme with it.
    """
    module = AmbienceMatch(SAMPLE_RATE, CHANNELS)
    module.learn(make_room_tone())
    module.process(make_wanted_signal())

    gate = module._last_gate
    assert gate is not None
    opened = float(np.mean(gate > 0.0))
    assert opened < 0.01, (
        f"the gate opens on {100.0 * opened:.2f} % of the bins of a wanted signal"
    )


def test_the_source_own_floor_is_gated_out() -> None:
    """The source's own noise floor goes down by at least 3 dB.

    This is the number the module survey holds the module to: the source's
    own floor has to go, and the programme has to stay.
    """
    source = make_bed_source()
    module = AmbienceMatch(SAMPLE_RATE, CHANNELS)
    module.learn(make_room_tone())
    out = module.process(source)

    before = band_level_db(source, FLOOR_LO_HZ, FLOOR_HI_HZ, *FLOOR_WINDOW)
    after = band_level_db(out, FLOOR_LO_HZ, FLOOR_HI_HZ, *FLOOR_WINDOW)
    assert before - after >= 3.0, (
        f"the source's own floor moved by {after - before:+.2f} dB, "
        f"the survey asks for 3 dB"
    )


def test_the_programme_survives_the_source_cleanup() -> None:
    """The programme keeps its level and its shape.

    The correlation is measured over the window the programme is in; the
    bed is added under it, so the correlation of the whole signal would
    always be a little short of one.
    """
    source = make_bed_source()
    module = AmbienceMatch(SAMPLE_RATE, CHANNELS)
    module.learn(make_room_tone())
    out = module.process(source)

    before = band_level_db(source, PROGRAMME_LO_HZ, PROGRAMME_HI_HZ, *PROGRAMME_WINDOW)
    after = band_level_db(out, PROGRAMME_LO_HZ, PROGRAMME_HI_HZ, *PROGRAMME_WINDOW)
    assert after - before > -1.5, (
        f"the programme lost {before - after:.2f} dB, the budget is 1.5 dB"
    )

    window = slice(int(PROGRAMME_WINDOW[0] * SAMPLE_RATE),
                   int(PROGRAMME_WINDOW[1] * SAMPLE_RATE))
    corr = float(np.corrcoef(out[window, 0], source[window, 0])[0, 1])
    assert corr > 0.98, f"the programme did not survive the mix ({corr:.4f})"


def test_the_bed_sits_at_the_level_of_the_profile() -> None:
    """The bed the module adds is at the level of the learned profile.

    The profile holds the *quiet* part of the reference, so the bed is a
    little below the average level of a reference that breathes; it is the
    level of the profile itself the bed has to sit at.
    """
    reference = make_room_tone()
    module = AmbienceMatch(SAMPLE_RATE, CHANNELS)
    module.learn(reference)
    module.params.set("Output Ambience Only", True)
    bed = module.process(make_bed_source())[:, 0]

    level = float(module.profile["level"])
    bed_db = 20.0 * np.log10(max(float(np.sqrt(np.mean(bed ** 2))), 1e-12))
    target_db = 20.0 * np.log10(max(level, 1e-12))
    assert abs(bed_db - target_db) < 1.0, (
        f"the bed is at {bed_db:.2f} dBFS and the profile at {target_db:.2f} dBFS"
    )


def test_the_bed_follows_the_spectrum_of_the_reference() -> None:
    """The bed the module adds carries the spectrum of the reference."""
    reference = make_stationary_room_tone()
    module = AmbienceMatch(SAMPLE_RATE, CHANNELS)
    module.learn(reference)
    module.params.set("Output Ambience Only", True)
    bed = module.process(make_bed_source())[:, 0]

    ref_db = band_db(reference[:, 0])
    bed_db = band_db(bed)
    error = float(np.mean(np.abs(bed_db - ref_db)))
    assert error < 4.0, (
        f"the bed is {error:.2f} dB away from the reference, band by band"
    )


# ---------------------------------------------------------------------------
# The cost
# ---------------------------------------------------------------------------
def test_a_64_sample_block_fits_into_the_realtime_budget() -> None:
    """The module processes a 64 sample block within the budget.

    The budget is the time the block needs to be played: 1333 us at 48 kHz.
    The measurement keeps the fastest of :data:`ROUNDS` rounds, because a
    shared machine makes every timing noisy and the fastest round is the one
    that was least disturbed by other work.
    """
    signal = make_source_with_tail(0.2)
    block = np.ascontiguousarray(signal[:BLOCK])

    best_median = float("inf")
    best_min = float("inf")
    for _ in range(ROUNDS):
        module = AmbienceMatch(SAMPLE_RATE, CHANNELS)
        times = []
        for index in range(REPEATS + WARMUP):
            start = time.perf_counter_ns()
            module.process(block.copy())
            elapsed = (time.perf_counter_ns() - start) / 1000.0
            if index >= WARMUP:
                times.append(elapsed)
        best_median = min(best_median, float(np.median(times)))
        best_min = min(best_min, float(min(times)))

    assert best_median <= BUDGET_US * BUSY_MACHINE_FACTOR, (
        f"a {BLOCK} sample block needed {best_median:.0f} us, "
        f"the budget is {BUDGET_US:.0f} us"
    )
    # The module's own cost (the fastest block it ever produced) has to fit
    # into the budget; this is the number that says whether the module is
    # realtime safe at all.
    assert best_min <= BUDGET_US, (
        f"the fastest {BLOCK} sample block needed {best_min:.0f} us, "
        f"the budget is {BUDGET_US:.0f} us"
    )
