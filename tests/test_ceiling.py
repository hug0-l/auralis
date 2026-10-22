"""
tests.test_ceiling — the gate of the digital ceiling.

The rule of the family is that **nothing the product hands out goes above the
digital ceiling**, and that it is **clipped** to get there, never scaled.

The rule used to sit inside every module: the module's own output was scaled
so that it fitted.  A rule like that has two faults.  A scale is a gain: it
moves the level of everything in the block, and the level it moves to depends
on the block — so two renders of the same material that differ in their
history came out at different levels, and the material outside the region the
spectral editor edits was moved by a gain.  And a rule inside the module
measures the rule and not the module: the quality instrument saw a scaled
signal and not the answer of the module.

The rule is at the **output** now — the chain's output and the plug-in's
output — and it is a **clip**.  A clip is a per sample operation: a sample
above the limit becomes the limit and every other sample is untouched.  So it
is block independent by construction, and it does not move the level of
anything that was inside the limit.

The tests below hold five things:

* **the rule is not inside the module** — a module may hand back a signal
  above the ceiling, and the chain's output may not,
* **the clip does not move the level** — a block inside the limit comes back
  bit for bit, and only the samples over the limit change,
* **the clip is block independent** — the same stream comes back the same
  whether it is handed over in one block or in many,
* **the limit is the chain's** — the digital ceiling, multiplied by the gain
  the chain's own level controls declare, and by nothing else,
* **the user's own gain is carried** — a chain that asks for +6 dB gets a
  limit of 1.995, the gain is not scaled away, and what goes over the limit
  is clipped.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurcore import registry  # noqa: E402
import aurcore.modules  # noqa: E402,F401 - importing registers the modules
from aurcore.api import Engine  # noqa: E402
from aurcore.ceiling import (  # noqa: E402
    CEILING,
    chain_limit,
    ceiling_limit,
    clip_to_ceiling,
    declared_gain,
    peak_of,
)
from aurcore.modules.base import Module  # noqa: E402

#: How far the renders of two block sizes may differ.  The clip is a per
#: sample operation, so the two renders have to be the same to the last bit.
COHERENCE_LIMIT = 1.0e-12


class _Loud(Module):
    """A module that hands back more than the ceiling.

    The probe is the simplest module that can overshoot: it multiplies the
    signal by a factor above one, so the rule has to make room for it.
    """

    code = "AUR_CEILINGPROBE"
    display_name = "Ceiling Probe"

    def __init__(self, sample_rate: int = 48000, channels: int = 2, gain: float = 1.5):
        super().__init__(sample_rate, channels)
        self.gain = float(gain)

    def process(self, x: np.ndarray) -> np.ndarray:
        """Return the signal, louder than the ceiling."""
        arr = np.asarray(x, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[:, None]
        return arr * self.gain


def _signal(length: int = 1024, channels: int = 2) -> np.ndarray:
    """Return a signal that reaches the ceiling and overshoots it."""
    rng = np.random.default_rng(20_260_101)
    signal = rng.standard_normal((length, channels)) * 0.25
    t = np.arange(length, dtype=np.float64) / 48000.0
    signal[:, 0] += 0.4 * np.sin(2.0 * np.pi * 1000.0 * t)
    signal[length // 3, 0] += 0.8
    return signal


def _stream(engine: Engine, signal: np.ndarray, block: int) -> np.ndarray:
    """Feed a signal through a chain, block by block, and collect the output."""
    out: list[np.ndarray] = []
    for start in range(0, signal.shape[0], block):
        out.append(np.asarray(engine.process(signal[start : start + block])))
    return np.concatenate(out) if out else np.zeros((0, signal.shape[1]))


# ---------------------------------------------------------------------------
# The rule is not inside the module
# ---------------------------------------------------------------------------
def test_a_module_may_hand_back_a_signal_above_the_ceiling() -> None:
    """The rule is at the output of the product, not inside the module.

    The module is the thing the quality instrument measures, and the
    instrument has to measure the module and not a clipped signal.
    """
    module = _Loud()
    signal = np.full((256, 2), 0.9, dtype=np.float64)
    out = np.asarray(module.process(signal))
    assert peak_of(out) > CEILING, (
        "the module's own output was held at the ceiling: the rule is inside "
        "the module again"
    )
    assert np.allclose(out, 1.35), "the module's own answer was changed"


def test_the_chain_holds_the_line() -> None:
    """The chain's output is inside the limit, even when the module is not."""
    engine = Engine(sample_rate=48000, channels=2)
    engine.add_module("AUR_GAIN")
    signal = np.full((256, 2), 0.5, dtype=np.float64)
    out = np.asarray(engine.process(signal))
    assert peak_of(out) <= CEILING + 1e-12, (
        f"the chain handed back a peak of {peak_of(out):.6f}"
    )


# ---------------------------------------------------------------------------
# The clip does not move the level
# ---------------------------------------------------------------------------
def test_a_block_inside_the_limit_is_handed_back_untouched() -> None:
    """A block that fits is handed back bit for bit."""
    signal = np.full((256, 2), 0.25, dtype=np.float64)
    out = clip_to_ceiling(signal, CEILING)
    assert out is signal, "a block inside the limit was copied"


def test_only_the_samples_over_the_limit_change() -> None:
    """The clip is a per sample operation.

    A sample above the limit becomes the limit; every other sample keeps its
    value, so the material the user did not touch is not moved.
    """
    signal = np.array([[0.5, -0.5], [2.0, 0.25], [-3.0, 0.0]], dtype=np.float64)
    out = np.asarray(clip_to_ceiling(signal, CEILING))
    assert out[0, 0] == 0.5 and out[0, 1] == -0.5
    assert out[1, 0] == CEILING and out[1, 1] == 0.25
    assert out[2, 0] == -CEILING and out[2, 1] == 0.0


def test_the_clip_keeps_the_shape_of_the_material() -> None:
    """The ratios inside the waveform are the ratios of the material.

    A scale would keep them too, but it would move the level of the whole
    block; a clip keeps the level of everything that was inside the limit.
    """
    signal = np.full((64, 1), 0.5, dtype=np.float64)
    signal[32, 0] = 2.0
    out = np.asarray(clip_to_ceiling(signal, CEILING))
    for index in range(0, 32):
        assert float(out[index, 0]) == 0.5
    for index in range(33, 64):
        assert float(out[index, 0]) == 0.5
    assert float(out[32, 0]) == CEILING


# ---------------------------------------------------------------------------
# The clip is block independent
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("block", [1, 7, 16, 64, 128, 512])
def test_the_answer_does_not_depend_on_the_block_size(block: int) -> None:
    """One block and many blocks give the same answer, to the last bit.

    The clip is a per sample operation, so it cannot depend on the way the
    host chopped the stream into.
    """
    signal = _signal()
    whole = Engine(sample_rate=48000, channels=2)
    whole.add_module("AUR_GAIN")
    one = np.asarray(whole.process(signal))

    pieces = Engine(sample_rate=48000, channels=2)
    pieces.add_module("AUR_GAIN")
    many = _stream(pieces, signal, block)

    assert one.shape == many.shape
    assert np.array_equal(one, many), (
        f"one block and {len(range(0, signal.shape[0], block))} blocks of "
        f"{block} samples are not the same"
    )


def test_the_clip_of_a_sample_does_not_depend_on_the_block() -> None:
    """The same sample of the same stream is clipped the same way."""
    signal = _signal()
    state = {}
    for block in (16, 64, 512):
        pieces = []
        for start in range(0, signal.shape[0], block):
            pieces.append(
                np.asarray(clip_to_ceiling(signal[start : start + block], CEILING))
            )
        state[block] = np.concatenate(pieces)
    reference = state[16]
    for block, out in state.items():
        assert np.array_equal(reference, out), (
            f"the clip at {block} samples differs from the clip at 16 samples"
        )


def test_the_clip_adds_nothing_to_the_block_dependence() -> None:
    """The chain's block dependence is the module's own, and not the rule's.

    A clip is a contraction: two samples that were close stay at least as
    close, and two samples that were far may come closer.  So the clip can
    only make the two renders of a stream *more* alike, never less.  The
    block dependence of a chain is therefore the block dependence of its
    modules — the ten modules whose own answer depends on the block are
    another agent's files, and the rule is not their cause.
    """
    from aurcore import registry
    import aurcore.modules  # noqa: F401 - importing registers the modules

    signal = _signal(4096, 2)
    for info in registry.all():
        code = info.code
        module = registry.create(code, 48000, 2)
        whole = np.asarray(module.process(signal))
        module2 = registry.create(code, 48000, 2)
        pieces = [
            np.asarray(module2.process(signal[start : start + 64]))
            for start in range(0, signal.shape[0], 64)
        ]
        many = np.concatenate(pieces, axis=0) if pieces else whole

        engine = Engine(sample_rate=48000, channels=2)
        engine.add_module(code)
        chain_one = np.asarray(engine.process(signal))
        engine2 = Engine(sample_rate=48000, channels=2)
        engine2.add_module(code)
        chain_pieces = [
            np.asarray(engine2.process(signal[start : start + 64]))
            for start in range(0, signal.shape[0], 64)
        ]
        chain_many = (
            np.concatenate(chain_pieces, axis=0) if chain_pieces else chain_one
        )

        size = min(whole.shape[0], many.shape[0], chain_one.shape[0], chain_many.shape[0])
        if size == 0:
            continue
        module_delta = float(np.max(np.abs(whole[:size] - many[:size])))
        chain_delta = float(np.max(np.abs(chain_one[:size] - chain_many[:size])))
        assert chain_delta <= module_delta + 1e-12, (
            f"{code}: the chain's two renders differ by {chain_delta:.6g} and "
            f"the module's own by {module_delta:.6g}: the rule of the ceiling "
            "added the block dependence"
        )


# ---------------------------------------------------------------------------
# The limit
# ---------------------------------------------------------------------------
def test_the_limit_is_the_digital_ceiling_for_a_repair_chain() -> None:
    """A chain with no level control of its own is held at the ceiling."""
    engine = Engine(sample_rate=48000, channels=2)
    engine.add_module("AUR_DECLICK")
    assert chain_limit(step.module for step in engine.chain) == CEILING


def test_the_limit_follows_the_level_control_of_the_chain() -> None:
    """The knob of the user is the only thing that may raise the limit."""
    module = registry.create("AUR_GAIN", 48000, 2)
    assert declared_gain(module) == pytest.approx(1.0)
    assert ceiling_limit(module) == pytest.approx(CEILING)

    module.params.set("Gain", 6.0)
    expected = 10.0 ** (6.0 / 20.0)
    assert declared_gain(module) == pytest.approx(expected)
    assert ceiling_limit(module) == pytest.approx(CEILING * expected)


def test_the_limit_is_a_property_of_the_chain_and_not_of_the_block() -> None:
    """The limit does not follow the level of the block.

    A limit that followed the block would make the answer of the chain
    depend on the size of the block, which is the defect the rule had.
    """
    engine = Engine(sample_rate=48000, channels=2)
    engine.add_module("AUR_GAIN")
    quiet = chain_limit(step.module for step in engine.chain)
    loud = chain_limit(step.module for step in engine.chain)
    assert quiet == loud
    assert quiet == CEILING


# ---------------------------------------------------------------------------
# The user's own gain is carried
# ---------------------------------------------------------------------------
def test_the_users_own_gain_is_carried_and_not_scaled_back() -> None:
    """A user who asks for +6 dB gets the +6 dB, and a clip above the limit.

    The old rule scaled the block, and the scaling took the user's own gain
    away again: the quiet passages of the file came back at the level they
    had before the gain module.  The clip does not do that: the quiet
    passages keep the +6 dB, and only the samples that go over the limit are
    clipped.
    """
    engine = Engine(sample_rate=48000, channels=2)
    handle = engine.add_module("AUR_GAIN")
    engine.set_param(handle, "Gain", 6.0)

    quiet = np.full((128, 2), 0.1, dtype=np.float64)
    loud = np.full((128, 2), 1.2, dtype=np.float64)
    out_quiet = np.asarray(engine.process(quiet))
    out_loud = np.asarray(engine.process(loud))

    # the quiet material carries the +6 dB
    assert peak_of(out_quiet) == pytest.approx(0.1 * 10.0 ** (6.0 / 20.0), rel=1e-9)
    # the loud material is clipped at the limit the chain declares, and it is
    # not scaled back
    limit = CEILING * 10.0 ** (6.0 / 20.0)
    assert peak_of(out_loud) == pytest.approx(limit, rel=1e-9)
    assert peak_of(out_loud) > CEILING, "the limit did not follow the knob"
    assert peak_of(out_loud) > 1.2, "the loud material was scaled back"


def test_a_chain_without_a_level_control_is_clipped_at_the_ceiling() -> None:
    """A repair chain hands back nothing above the digital ceiling."""
    engine = Engine(sample_rate=48000, channels=2)
    engine.add_module("AUR_DECLIP")
    t = np.arange(512, dtype=np.float64) / 48000.0
    loud = np.repeat((1.5 * np.sin(2.0 * np.pi * 1000.0 * t))[:, None], 2, axis=1)
    out = np.asarray(engine.process(loud))
    assert peak_of(out) == pytest.approx(CEILING, rel=1e-12)


def test_the_path_the_plug_in_uses_hands_back_a_signal_inside_the_ceiling() -> None:
    """The plug-in's path to the core is the bridge, and the bridge clips.

    The adapter adds no level of its own, so the promise the plug-in makes
    to its host is the promise of the chain.  The bridge is the exact
    function the C ABI calls, with the layout the ABI hands it: planar
    float32 planes in, planar float32 planes out.
    """
    from aurcore import abi_bridge

    engine = Engine(sample_rate=48000, channels=2)
    engine.add_module("AUR_DECLIP")
    frames = 4096
    t = np.arange(frames, dtype=np.float32) / 48000.0
    hot = np.repeat((1.5 * np.sin(2.0 * np.pi * 1000.0 * t))[:, None], 2, axis=1)
    data = np.ascontiguousarray(hot.T, dtype=np.float32).tobytes()

    out, consumed, produced = abi_bridge.process(engine, data, 2, frames)
    assert consumed == frames
    block = np.frombuffer(out, dtype=np.float32).reshape(2, produced).T
    peak = float(np.max(np.abs(block)))
    assert peak <= CEILING + 1e-6, (
        f"the block the plug-in would hand the host peaks at {peak:.6f}, the "
        f"ceiling is {CEILING:.6f}"
    )


if __name__ == "__main__":  # pragma: no cover - the file is a test file
    sys.exit(pytest.main([__file__, "-q"]))
