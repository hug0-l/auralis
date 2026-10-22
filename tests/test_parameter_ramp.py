"""
tests/test_parameter_ramp.py — the ramp of a parameter change.

A parameter change that arrives while the plug-in is playing is applied over
a short, declared time instead of as a step.  A step is a click: the output
jumps by the whole change at once, and the jump is heard as a tick in the
programme.

**The gate.**  The largest step a parameter change makes must not exceed
**0.010** (about -40 dBFS).  The number comes from the study of the
reference product: a parameter change there moves the output by **0.080**
(about -22 dBFS) in one step, and the signal that carries it moves by
0.027 on its own.  The bound is an eighth of that step, and it is measured
on a signal that is calibrated so that a step-like change would move the
output by exactly 0.080 — so the test fails on a plug-in that steps and
passes on one that ramps.

The tests cover the whole path: the engine, the ABI, and the property the
ramp rests on — that cutting a block into pieces does not change what a
module makes of it.
"""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurcore import registry  # noqa: E402
from aurcore.api import (  # noqa: E402
    CUT_SENSITIVE_MODULES,
    PARAM_RAMP_SECONDS,
    PARAM_RAMP_STEPS,
    create_engine,
)

SAMPLE_RATE = 48000
BLOCK = 256
CHANNELS = 2

#: The bound of the gate: the largest step a parameter change may make.
STEP_BOUND = 0.010

#: The step the reference product makes when a parameter changes: the number
#: the bound is an eighth of.
REFERENCE_STEP = 0.080

#: The tone the measurement uses.  Its own step -- the largest difference
#: between two neighbouring samples -- is 0.0017, so a step of the change
#: stands out of the signal.
TONE_HZ = 100.0

#: The amplitude that makes a step-like change move the output by exactly
#: the reference step: a gain of 0 dB to -8 dB takes 0.602 of the level
#: away, so the amplitude is the reference step over that.
TONE_AMPLITUDE = REFERENCE_STEP / (1.0 - 10.0 ** (-8.0 / 20.0))

#: The gain the change moves to, in dB.
GAIN_DOWN_DB = -8.0

#: The block the change arrives in.
CHANGE_AT = 8

LIBRARY = ROOT / "plugins" / "abi" / "build" / "aurcore_abi.dll"


def tone(count: int, offset: int, phase: float) -> np.ndarray:
    """A tone of the measurement, with the peak at `offset`."""
    t = (np.arange(count, dtype=np.float64) + offset) / SAMPLE_RATE
    sig = TONE_AMPLITUDE * np.sin(2.0 * np.pi * TONE_HZ * t + phase)
    return np.repeat(sig[:, None], CHANNELS, axis=1)


def phase_for_peak(offset: int) -> float:
    """The phase that puts the peak of the tone on the sample at `offset`."""
    return 0.5 * np.pi - 2.0 * np.pi * TONE_HZ * offset / SAMPLE_RATE


def run_change(ramp_seconds: float = 0.0, blocks: int = 24,
               change_at: int = CHANGE_AT) -> np.ndarray:
    """Run the gain module and change the gain in the block `change_at`."""
    engine = create_engine(SAMPLE_RATE, CHANNELS)
    engine.add_module("AUR_GAIN")
    engine.set_param(0, "Gain", 0.0)

    phase = phase_for_peak(change_at * BLOCK)
    out = []
    for i in range(blocks):
        if i == change_at:
            engine.set_param(0, "Gain", GAIN_DOWN_DB, ramp_seconds=ramp_seconds)
        out.append(engine.process(tone(BLOCK, i * BLOCK, phase)))

    return np.concatenate(out, axis=0)[:, 0]


def largest_step(signal: np.ndarray, start: int, count: int) -> float:
    """The largest difference between two neighbouring samples in a window."""
    diff = np.abs(np.diff(signal[start:start + count]))
    return float(np.max(diff)) if diff.size else 0.0


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------

def test_a_parameter_change_makes_no_step_larger_than_the_bound():
    """The gate: a change must not step the output.

    The signal is calibrated so that a change applied as a step moves the
    output by the reference's 0.080.  With the ramp the same change moves it
    by a sixteenth of that, in steps that are under the bound.
    """
    boundary = CHANGE_AT * BLOCK

    stepped = run_change(ramp_seconds=0.0)
    ramped = run_change(ramp_seconds=PARAM_RAMP_SECONDS)

    steady = largest_step(stepped, 0, boundary - 1)
    step = largest_step(stepped, boundary - 1, 4 * BLOCK)
    ramp = largest_step(ramped, boundary - 1, 4 * BLOCK)

    # the calibration: without a ramp the change moves the output by the
    # reference's step, so the bound means something
    assert step >= REFERENCE_STEP * 0.95, (
        f"the signal is not calibrated: a step moves the output by {step:.4f}, "
        f"not by {REFERENCE_STEP:.4f}")

    assert ramp <= STEP_BOUND, (
        f"a parameter change made a step of {ramp:.4f}, the bound is {STEP_BOUND:.4f}")
    assert ramp < step / 8.0, (
        f"the ramp did not help: {ramp:.4f} against {step:.4f}")


def test_the_signal_of_the_measurement_moves_by_less_than_the_bound():
    """The signal itself must not be the step the gate measures."""
    boundary = CHANGE_AT * BLOCK
    signal = run_change(ramp_seconds=0.0)
    assert largest_step(signal, 0, boundary - 1) < STEP_BOUND


# ---------------------------------------------------------------------------
# the rule of the core
# ---------------------------------------------------------------------------
#
#  The ramp belongs to the core: the core owns it, and every caller of it
#  gets it.  A caller can still ask for a change at once, and a caller can
#  ask for a ramp of a length of its own, but the rule is the core's.
#
#  The rule has two halves: a change that arrives while the chain is
#  playing is ramped, and a change that arrives before the chain has
#  played anything -- a change of the setup -- is applied at once.


def run_after_playing(ramp_seconds: float | None) -> np.ndarray:
    """Change the gain while the chain is playing, the way it is asked for."""
    engine = create_engine(SAMPLE_RATE, CHANNELS)
    engine.add_module("AUR_GAIN")
    engine.set_param(0, "Gain", 0.0)

    phase = phase_for_peak(0)
    out = []

    for i in range(8):
        if i == 2:
            if ramp_seconds is None:
                # the core's own rule
                engine.set_param(0, "Gain", GAIN_DOWN_DB)
            else:
                engine.set_param(0, "Gain", GAIN_DOWN_DB, ramp_seconds=ramp_seconds)
        out.append(engine.process(tone(BLOCK, i * BLOCK, phase)))

    return np.concatenate(out, axis=0)[:, 0]


def test_the_two_ways_of_asking_for_a_change_agree():
    """The same value must sound the same however it is asked for.

    The core can be asked for a change in two ways: with a stated ramp and
    with the core's own rule.  Both have to produce the same audio, or a
    value would sound different depending on which way the caller asked
    for it -- and the callers are the application, the server and the
    plug-in.
    """
    stated = run_after_playing(PARAM_RAMP_SECONDS)
    rule = run_after_playing(None)

    difference = float(np.max(np.abs(stated - rule))) if stated.size else 0.0

    assert difference == 0.0, (
        f"the two ways of asking for the change differ by {difference:.9f}"
    )


def test_a_change_while_the_chain_plays_is_ramped():
    """The core's own rule: a change that arrives while the chain plays is
    applied over the ramp, not as a step."""
    boundary = 2 * BLOCK

    stepped = run_change(ramp_seconds=0.0, change_at=2)
    ramped = run_after_playing(None)

    step = largest_step(ramped, boundary - 1, 4 * BLOCK)
    assert step <= STEP_BOUND, (
        f"the change the core's own rule made a step of {step:.4f}, the bound is "
        f"{STEP_BOUND:.4f}"
    )

    # and it is a ramp, not a step: the stepped run steps much more
    stepped_step = largest_step(stepped, boundary - 1, 4 * BLOCK)
    assert step < stepped_step / 8.0, (
        f"the ramp did not help: {step:.4f} against {stepped_step:.4f}"
    )


def test_a_change_of_the_setup_is_applied_at_once():
    """A change that arrives before the chain has played is at once.

    A document that is loaded and a chain that is built are changes of the
    setup: the value has to be there when the first block is played, or
    the playback would fade in.
    """
    engine = create_engine(SAMPLE_RATE, CHANNELS)
    engine.add_module("AUR_GAIN")
    engine.set_param(0, "Gain", 0.0)

    # the core's own rule, with nothing played yet
    engine.set_param(0, "Gain", GAIN_DOWN_DB)

    block = np.ones((BLOCK, CHANNELS), dtype=np.float64)
    out = engine.process(block)
    expected = 10.0 ** (GAIN_DOWN_DB / 20.0)

    assert float(np.max(np.abs(out - expected))) < 1.0e-12, (
        "the change of the setup was not at once"
    )


# ---------------------------------------------------------------------------
# what the ramp does
# ---------------------------------------------------------------------------

def test_the_ramp_reaches_the_value():
    """The automation must still reach the value the host asked for."""
    engine = create_engine(SAMPLE_RATE, CHANNELS)
    engine.add_module("AUR_GAIN")
    engine.set_param(0, "Gain", 0.0)
    engine.set_param(0, "Gain", GAIN_DOWN_DB, ramp_seconds=PARAM_RAMP_SECONDS)

    # the parameter holds the value it is going to have right away
    assert engine.get_param(0, "Gain") == GAIN_DOWN_DB

    block = np.zeros((BLOCK, CHANNELS), dtype=np.float64)
    for _ in range(8):
        engine.process(block)

    assert engine.get_param(0, "Gain") == GAIN_DOWN_DB


def test_the_ramp_takes_the_declared_time():
    """The value reaches its target at the end of the declared ramp."""
    engine = create_engine(SAMPLE_RATE, CHANNELS)
    engine.add_module("AUR_GAIN")
    engine.set_param(0, "Gain", 0.0)

    phase = phase_for_peak(0)
    engine.set_param(0, "Gain", GAIN_DOWN_DB, ramp_seconds=PARAM_RAMP_SECONDS)

    seen = []
    for i in range(8):
        engine.process(tone(BLOCK, i * BLOCK, phase))
        seen.append(engine.get_param(0, "Gain"))

    # the value is the target all the way: the ramp moves the value the
    # module works with, not the value the parameter holds
    assert all(value == GAIN_DOWN_DB for value in seen)


def test_a_change_during_a_ramp_does_not_jump():
    """A change that arrives while a ramp runs re-targets the ramp.

    The value moves on from where it is: it does not jump to the value the
    first ramp was going to, and it does not jump to the new one either.
    """
    engine = create_engine(SAMPLE_RATE, CHANNELS)
    engine.add_module("AUR_GAIN")
    engine.set_param(0, "Gain", 0.0)

    phase = phase_for_peak(0)
    signal = tone(BLOCK, 0, phase)

    # the first change starts a ramp
    engine.set_param(0, "Gain", -8.0, ramp_seconds=PARAM_RAMP_SECONDS)
    engine.process(signal)

    # the second change arrives while the ramp runs: it goes to the other
    # way, and the ramp turns around from where it is
    engine.set_param(0, "Gain", 6.0, ramp_seconds=PARAM_RAMP_SECONDS)
    out = engine.process(signal)

    diff = np.abs(np.diff(out[:, 0]))
    # the signal's own step is the bound for a turn around
    steady = largest_step(run_change(ramp_seconds=0.0), 0, BLOCK - 1)
    assert float(np.max(diff)) <= max(steady, STEP_BOUND), (
        f"the change during the ramp made a step of {float(np.max(diff)):.4f}")

    # and the ramp ends at the new value
    for _ in range(8):
        engine.process(signal)
    assert engine.get_param(0, "Gain") == 6.0


def test_a_plain_change_is_at_once():
    """A change that is not a user change -- a transfer -- is applied at once.

    The plug-in formats transfer the parameters over the connection point,
    and a transfer is not a change the user made: it is the state of the
    plug-in being put back where it belongs.  It must not be ramped, and
    the caller says so: a transfer asks for the change at once.

    The core's own rule ramps a change that arrives while the chain is
    playing, so a transfer has to say what it wants.  The plug-in's
    transfer is the plain set every block, and it does not change the
    value, so it starts no ramp at all.
    """
    engine = create_engine(SAMPLE_RATE, CHANNELS)
    engine.add_module("AUR_GAIN")
    engine.set_param(0, "Gain", 0.0)

    # the chain has played, so the core's own rule would ramp the change
    engine.process(np.zeros((BLOCK, CHANNELS), dtype=np.float64))

    # a transfer asks for the change at once
    engine.set_param(0, "Gain", GAIN_DOWN_DB, ramp_seconds=0.0)
    assert engine.get_param(0, "Gain") == GAIN_DOWN_DB

    # and the value the module works with is the new one at once
    block = np.ones((BLOCK, CHANNELS), dtype=np.float64)
    out = engine.process(block)
    expected = 10.0 ** (GAIN_DOWN_DB / 20.0)
    assert float(np.max(np.abs(out - expected))) < 1.0e-12


def test_a_stepped_parameter_changes_at_once():
    """A parameter that is stepped has no value between two of its values."""
    engine = create_engine(SAMPLE_RATE, CHANNELS)
    engine.add_module("AUR_DEESS")
    engine.set_param(0, "Mode", "Spectral", ramp_seconds=PARAM_RAMP_SECONDS)
    assert engine.get_param(0, "Mode") == "Spectral"


# ---------------------------------------------------------------------------
# the property the ramp rests on
# ---------------------------------------------------------------------------

def test_cutting_a_block_does_not_put_a_step_into_the_output():
    """The ramp cuts the block into pieces, and the pieces must add up.

    A module that does not care how the block was cut makes the same samples
    of it: the ramp may then cut the block as finely as it likes without
    changing the sound of the module.  A module that does the analysis of
    the whole block at once cannot be cut, and the core knows which modules
    those are -- the list is measured, and this test is the measurement.
    """
    rng = np.random.default_rng(20240521)
    block = rng.standard_normal((BLOCK, CHANNELS)) * 0.1
    piece = max(1, int(SAMPLE_RATE * PARAM_RAMP_SECONDS) // PARAM_RAMP_STEPS)

    for info in registry.all():
        whole = create_engine(SAMPLE_RATE, CHANNELS)
        whole.add_module(info.code)
        for _ in range(4):
            whole.process(block.copy())
        expected = whole.process(block.copy())

        cut = create_engine(SAMPLE_RATE, CHANNELS)
        cut.add_module(info.code)
        for _ in range(4):
            cut.process(block.copy())
        pieces = []
        position = 0
        while position < BLOCK:
            length = min(piece, BLOCK - position)
            pieces.append(cut.process(block[position:position + length].copy()))
            position += length
        made = np.concatenate(pieces, axis=0)

        assert made.shape == expected.shape, f"{info.code} changed the length"

        whole_step = float(np.max(np.abs(np.diff(expected[:, 0]))))
        cut_step = float(np.max(np.abs(np.diff(made[:, 0]))))

        if info.code in CUT_SENSITIVE_MODULES:
            # the module is known to mind: the core does not cut its block
            continue

        assert cut_step <= whole_step * 1.5 + 1.0e-9, (
            f"{info.code} minds the cut: the step of the whole block is "
            f"{whole_step:.4f}, the step of the cut block is {cut_step:.4f}")


# ---------------------------------------------------------------------------
# the ABI
# ---------------------------------------------------------------------------

def _load_library():
    if not LIBRARY.exists():
        pytest.skip("the core ABI library is not built")
    lib = ctypes.CDLL(str(LIBRARY))
    lib.aur_engine_create.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    lib.aur_engine_create.restype = ctypes.c_void_p
    lib.aur_engine_add_module.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.aur_engine_add_module.restype = ctypes.c_int
    lib.aur_engine_set_param.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                        ctypes.c_char_p, ctypes.c_double]
    lib.aur_engine_set_param.restype = ctypes.c_int
    lib.aur_engine_set_param_ramped.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                                ctypes.c_char_p, ctypes.c_double,
                                                ctypes.c_double]
    lib.aur_engine_set_param_ramped.restype = ctypes.c_int
    lib.aur_engine_get_param.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                         ctypes.c_char_p, ctypes.c_char_p,
                                         ctypes.c_size_t]
    lib.aur_engine_get_param.restype = ctypes.c_int
    lib.aur_engine_process.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.POINTER(ctypes.c_float)),
        ctypes.POINTER(ctypes.POINTER(ctypes.c_float)),
        ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    lib.aur_engine_process.restype = ctypes.c_int
    lib.aur_engine_destroy.argtypes = [ctypes.c_void_p]
    return lib


def test_the_abi_ramps_a_parameter_change():
    """The plug-in's own path: the ABI asks for a ramp and the core ramps."""
    lib = _load_library()

    engine = lib.aur_engine_create(SAMPLE_RATE, CHANNELS)
    assert engine, "the engine could not be created"
    try:
        assert lib.aur_engine_add_module(engine, b"AUR_GAIN") == 0

        boundary = CHANGE_AT * BLOCK
        phase = phase_for_peak(boundary)

        out = []
        for i in range(24):
            if i == CHANGE_AT:
                rc = lib.aur_engine_set_param_ramped(engine, 0, b"Gain",
                                                      GAIN_DOWN_DB,
                                                      PARAM_RAMP_SECONDS * 1000.0)
                assert rc == 0, f"the ramped set failed with {rc}"
            block = tone(BLOCK, i * BLOCK, phase).astype(np.float32)
            left = np.ascontiguousarray(block[:, 0])
            right = np.ascontiguousarray(block[:, 1])
            inputs = (ctypes.POINTER(ctypes.c_float) * 2)(
                left.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                right.ctypes.data_as(ctypes.POINTER(ctypes.c_float)))
            out_left = np.zeros(BLOCK, dtype=np.float32)
            out_right = np.zeros(BLOCK, dtype=np.float32)
            outputs = (ctypes.POINTER(ctypes.c_float) * 2)(
                out_left.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                out_right.ctypes.data_as(ctypes.POINTER(ctypes.c_float)))
            rc = lib.aur_engine_process(engine, inputs, outputs, BLOCK, BLOCK,
                                        SAMPLE_RATE, None)
            assert rc == 0, f"the block failed with {rc}"
            out.append(out_left.astype(np.float64).copy())

        signal = np.concatenate(out)
        step = largest_step(signal, boundary - 1, 4 * BLOCK)
        assert step <= STEP_BOUND, (
            f"the plug-in path made a step of {step:.4f}, the bound is {STEP_BOUND:.4f}")

        buffer = ctypes.create_string_buffer(64)
        assert lib.aur_engine_get_param(engine, 0, b"Gain", buffer, 64) == 0
        assert buffer.value.decode() == str(GAIN_DOWN_DB)
    finally:
        lib.aur_engine_destroy(engine)


def test_the_ramp_is_declared_in_the_abi():
    """The contract says how long a ramp is, so an adapter can say it too."""
    header = (ROOT / "plugins" / "vst3" / "aurcore_c_api.h").read_text(encoding="utf-8")
    assert "AUR_PARAM_RAMP_MS" in header
    assert "aur_engine_set_param_ramped" in header
    assert int(PARAM_RAMP_SECONDS * 1000) == 20
    assert PARAM_RAMP_STEPS == 16
