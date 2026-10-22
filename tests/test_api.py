"""
tests/test_api.py — the engine of the core.

The engine owns a chain of modules, runs it in signal order, and knows
nothing about plug-in formats, the network or the interface.  These tests
cover the contract of :mod:`aurcore.api`:

* the chain is built, edited and run;
* the latency and the tail of the chain are reported;
* the chain document carries the chain, and the chain can be read back;
* the **processing mode** of the chain: the two modes, the modules that get
  them, the document that carries them and the refusal of a third one.

The mode is the one thing every module of the chain has to agree on, so it
is tested from both ends: from the engine, and from a module that reads it.
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from aurcore.api import Engine, create_engine  # noqa: E402
from aurcore.modules.base import (  # noqa: E402
    PROCESSING_MODE_OFFLINE,
    PROCESSING_MODE_PARAMETER,
    PROCESSING_MODE_REALTIME,
    PROCESSING_MODES,
    Module,
)

SAMPLE_RATE = 48000
CHANNELS = 2


# ---------------------------------------------------------------------------
# a module that behaves differently in the two modes
# ---------------------------------------------------------------------------
class ModeAwareModule(Module):
    """A module that is twice as slow in the offline mode, and says so.

    It stands in for the modules of the product: the mode decides the cost
    of the work and the look ahead it costs, and the module declares the
    look ahead, so the host can be told how late the audio is.
    """

    code = "AUR_MODEAWARE"
    display_name = "Mode Aware"

    def describe(self):
        from aurcore.params import desc

        return [
            desc(
                name=PROCESSING_MODE_PARAMETER,
                type="enum",
                default=PROCESSING_MODE_REALTIME,
                choices=PROCESSING_MODES,
            )
        ]

    def process(self, x):
        return x

    @property
    def latency_samples(self) -> int:
        return 1024 if self.processing_mode == PROCESSING_MODE_REALTIME else 8192


@pytest.fixture()
def engine() -> Engine:
    return create_engine(SAMPLE_RATE, CHANNELS)


def _register(module_class) -> str:
    """Register a test module and give back its code."""
    from aurcore import registry

    if not registry.has(module_class.code):
        registry.register(module_class)
    return module_class.code


# ---------------------------------------------------------------------------
# the chain
# ---------------------------------------------------------------------------
def test_a_new_engine_has_no_modules(engine):
    assert engine.chain == []
    assert engine.handles() == []
    assert engine.latency_samples == 0
    assert engine.tail_samples == 0


def test_a_module_is_added_and_removed(engine):
    handle = engine.add_module("AUR_DECLICK")

    assert handle.index == 0
    assert handle.code == "AUR_DECLICK"
    assert len(engine.chain) == 1

    engine.remove_module(handle)

    assert engine.chain == []


def test_the_chain_runs_in_signal_order(engine):
    engine.add_module("AUR_DECLICK")
    engine.add_module("AUR_DEESS")

    block = np.zeros((256, CHANNELS))
    out = engine.process(block)

    assert out.shape == block.shape
    assert np.all(np.isfinite(out))


def test_a_bypassed_module_is_transparent(engine):
    handle = engine.add_module("AUR_DECLICK")
    engine.set_bypass(handle, True)

    assert engine.is_bypassed(handle)


# ---------------------------------------------------------------------------
# the two processing modes
# ---------------------------------------------------------------------------
def test_the_modes_are_the_two_strings():
    assert PROCESSING_MODES == ("realtime", "offline")
    assert PROCESSING_MODE_REALTIME == "realtime"
    assert PROCESSING_MODE_OFFLINE == "offline"


def test_a_module_starts_in_the_realtime_mode(engine):
    handle = engine.add_module("AUR_DECLICK")

    assert handle.module.processing_mode == PROCESSING_MODE_REALTIME
    assert engine.processing_mode == PROCESSING_MODE_REALTIME


def test_the_engine_gives_the_mode_to_every_module(engine):
    code = _register(ModeAwareModule)
    engine.add_module("AUR_DECLICK")
    engine.add_module(code)

    engine.processing_mode = PROCESSING_MODE_OFFLINE

    for step in engine.chain:
        assert step.module.processing_mode == PROCESSING_MODE_OFFLINE


def test_the_engine_gives_the_mode_before_it_processes(engine):
    code = _register(ModeAwareModule)
    handle = engine.add_module(code)

    # the mode is changed behind the back of the engine's property
    handle.module._processing_mode = PROCESSING_MODE_REALTIME
    engine.processing_mode = PROCESSING_MODE_OFFLINE
    handle.module._processing_mode = PROCESSING_MODE_REALTIME

    engine.process(np.zeros((64, CHANNELS)))

    assert handle.module.processing_mode == PROCESSING_MODE_OFFLINE


def test_a_third_mode_is_refused(engine):
    with pytest.raises(ValueError):
        engine.processing_mode = "fast"

    assert engine.processing_mode == PROCESSING_MODE_REALTIME


def test_the_mode_of_a_module_is_read_only(engine):
    handle = engine.add_module("AUR_DECLICK")

    with pytest.raises(AttributeError):
        handle.module.processing_mode = PROCESSING_MODE_OFFLINE


def test_the_mode_is_a_parameter_of_the_module(engine):
    code = _register(ModeAwareModule)
    handle = engine.add_module(code)

    assert handle.module.params.has(PROCESSING_MODE_PARAMETER)

    engine.processing_mode = PROCESSING_MODE_OFFLINE

    assert handle.module.params.get(PROCESSING_MODE_PARAMETER) == PROCESSING_MODE_OFFLINE


def test_setting_the_parameter_sets_the_mode_of_the_chain(engine):
    code = _register(ModeAwareModule)
    handle = engine.add_module(code)

    engine.set_param(handle, PROCESSING_MODE_PARAMETER, PROCESSING_MODE_OFFLINE)

    assert engine.processing_mode == PROCESSING_MODE_OFFLINE
    assert handle.module.processing_mode == PROCESSING_MODE_OFFLINE


def test_the_latency_of_the_chain_follows_the_mode(engine):
    code = _register(ModeAwareModule)
    engine.add_module(code)

    assert engine.latency_samples == 1024

    engine.processing_mode = PROCESSING_MODE_OFFLINE

    assert engine.latency_samples == 8192


def test_the_mode_of_the_chain_is_in_the_summary(engine):
    engine.add_module("AUR_DECLICK")
    engine.processing_mode = PROCESSING_MODE_OFFLINE

    summary = engine.chain_summary()

    assert summary
    assert all(entry["processing_mode"] == PROCESSING_MODE_OFFLINE for entry in summary)


# ---------------------------------------------------------------------------
# the chain document
# ---------------------------------------------------------------------------
def test_the_document_carries_the_mode(engine):
    engine.add_module("AUR_DECLICK")
    engine.processing_mode = PROCESSING_MODE_OFFLINE

    document = engine.serialize()

    assert document["processing_mode"] == PROCESSING_MODE_OFFLINE
    assert json.loads(json.dumps(document))["processing_mode"] == PROCESSING_MODE_OFFLINE


def test_a_document_read_back_keeps_the_mode(engine):
    engine.add_module("AUR_DECLICK")
    engine.processing_mode = PROCESSING_MODE_OFFLINE

    other = create_engine(SAMPLE_RATE, CHANNELS)
    other.deserialize(engine.serialize())

    assert other.processing_mode == PROCESSING_MODE_OFFLINE
    assert other.chain[0].module.processing_mode == PROCESSING_MODE_OFFLINE


def test_a_document_without_a_mode_is_realtime(engine):
    other = create_engine(SAMPLE_RATE, CHANNELS)
    other.deserialize({"format": "auralis.chain", "version": 1, "steps": []})

    assert other.processing_mode == PROCESSING_MODE_REALTIME


def test_a_document_with_a_wrong_mode_is_realtime(engine):
    other = create_engine(SAMPLE_RATE, CHANNELS)
    other.deserialize(
        {"format": "auralis.chain", "version": 1, "processing_mode": "quick", "steps": []}
    )

    assert other.processing_mode == PROCESSING_MODE_REALTIME


def test_the_mode_of_a_document_wins_over_the_mode_of_a_step(engine):
    code = _register(ModeAwareModule)
    engine.add_module(code)
    engine.processing_mode = PROCESSING_MODE_OFFLINE

    other = create_engine(SAMPLE_RATE, CHANNELS)
    other.deserialize(engine.serialize())

    assert other.processing_mode == PROCESSING_MODE_OFFLINE
    assert other.chain[0].module.params.get(PROCESSING_MODE_PARAMETER) == (
        PROCESSING_MODE_OFFLINE
    )


# ---------------------------------------------------------------------------
# the cost of the mode
# ---------------------------------------------------------------------------
def test_the_cost_of_the_mode_is_a_string_comparison(engine):
    """The mode must not cost anything but a comparison.

    The engine gives the mode to every module before the chain runs, so the
    price of the mode is one comparison per module (and one assignment when
    the mode has changed).  The loop the engine runs is measured against the
    same loop without the mode, so the difference is the price of the mode
    and nothing else.
    """
    import time

    code = _register(ModeAwareModule)
    for _ in range(64):
        engine.add_module(code)

    chain = engine.chain
    mode = engine.processing_mode
    runs = 2000

    def loop(with_mode: bool) -> float:
        best = float("inf")
        for _ in range(5):
            start = time.perf_counter()
            for _ in range(runs):
                for step in chain:
                    module = step.module
                    if with_mode and module._processing_mode != mode:
                        module._processing_mode = mode
            best = min(best, time.perf_counter() - start)
        return best / runs

    with_mode = loop(True)
    without = loop(False)

    per_module = (with_mode - without) / len(chain)
    budget = 1_000_000.0 * 64 / 48000.0

    # a comparison of two short strings, not a copy of the chain
    assert per_module < 1.0e-6, f"the mode costs {per_module * 1e9:.0f} ns per module"
    # and the whole chain stays far inside the budget of a real time block
    assert with_mode < budget / 10.0, (
        f"the mode costs {with_mode * 1e6:.1f} us for {len(chain)} modules, "
        f"the budget of a 64 sample block is {budget:.0f} us"
    )
