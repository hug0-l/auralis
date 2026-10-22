"""
Tests for the undo / redo history of the workbench.

Every command of :mod:`aurcore.undo` is exercised in both directions: the
command applies itself, the history reverts it and the history replays it.
The coalescing of the parameter changes (a knob drag is one undo step, not
one per frame) and the depth limit of the history are covered as well.

The tests run against a small dummy module so they do not depend on the
module tree, which is written in parallel.
"""
from __future__ import annotations

import numpy as np
import pytest

from aurcore.api import create_engine
from aurcore.modules.base import Module
from aurcore.params import ParamDesc
from aurcore.undo import (
    AddModuleCommand,
    ChangeRegionCommand,
    Command,
    LoadPresetCommand,
    RemoveModuleCommand,
    ReorderModuleCommand,
    SetParamCommand,
    ToggleBypassCommand,
    Transaction,
    UndoStack,
)

DUMMY_CODE = "AUR_TEST_UNDO"


class _UndoDummy(Module):
    """A minimal module with a float, a bool and a region."""

    code = DUMMY_CODE
    display_name = "Undo Dummy"
    category = "Utility"
    edition = "standard"
    element = "UndoDummy"

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        """Create the dummy module."""
        super().__init__(sample_rate, channels)
        self._blob = b""
        self._region: tuple[int, int, float, float] | None = None

    def describe(self) -> list[ParamDesc]:
        """The parameters of the dummy module."""
        return [
            ParamDesc(
                name="Sensitivity",
                type="float",
                default=3.0,
                minimum=0.0,
                maximum=10.0,
                unit="dB",
                group="Strength",
            ),
            ParamDesc(
                name="Threshold",
                type="float",
                default=-40.0,
                minimum=-100.0,
                maximum=0.0,
                unit="dB",
                group="Detection",
            ),
            ParamDesc(name="Enabled", type="bool", default=True, group="State"),
        ]

    def process(self, x: np.ndarray) -> np.ndarray:
        """The dummy module passes the signal through."""
        return x

    def _state_blob(self) -> bytes:
        """The dummy module keeps a small state blob."""
        return self._blob

    def _put_state_blob(self, data: bytes) -> None:
        """Restore the state blob."""
        self._blob = bytes(data)

    def set_region(self, start, end, low, high) -> None:
        """Select the region the module works on."""
        self._region = (int(start), int(end), float(low), float(high))

    def clear_region(self) -> None:
        """Remove the region the module works on."""
        self._region = None

    def region(self):
        """The region the module works on."""
        return self._region


@pytest.fixture
def undo_dummy(registry_snapshot):
    """Register the dummy module for the duration of a test."""
    from aurcore import registry

    registry.register(_UndoDummy)
    return _UndoDummy


@pytest.fixture
def engine():
    """A 48 kHz / 2-channel engine with an empty chain."""
    return create_engine(48000, 2)


class _FakeClock:
    """A clock the test drives by hand."""

    def __init__(self, start: float = 1000.0):
        """Create the clock."""
        self.now = float(start)

    def __call__(self) -> float:
        """The current time."""
        return self.now

    def advance(self, seconds: float) -> None:
        """Move the clock forward."""
        self.now += float(seconds)


def _chain_codes(engine) -> list[str]:
    """The module codes of the chain, in signal order."""
    return [step.module.code for step in engine.chain]


# ---------------------------------------------------------------------------
# the commands
# ---------------------------------------------------------------------------
def test_add_module_can_be_undone_and_redone(engine, undo_dummy):
    """Adding a module is one undoable step."""
    stack = UndoStack()
    stack.execute(AddModuleCommand(engine, DUMMY_CODE))

    assert _chain_codes(engine) == [DUMMY_CODE]
    assert stack.depth == 1

    stack.undo()
    assert _chain_codes(engine) == []

    stack.redo()
    assert _chain_codes(engine) == [DUMMY_CODE]


def test_add_module_can_undo_at_a_position(engine, undo_dummy):
    """A module added in the middle lands in the middle."""
    engine.add_module(DUMMY_CODE)
    engine.add_module(DUMMY_CODE)
    stack = UndoStack()
    stack.execute(AddModuleCommand(engine, DUMMY_CODE, index=1))

    assert len(engine.chain) == 3
    stack.undo()
    assert len(engine.chain) == 2
    stack.redo()
    assert len(engine.chain) == 3


def test_remove_module_can_be_undone_and_redone(engine, undo_dummy):
    """Removing a module keeps its parameters and its position."""
    engine.add_module(DUMMY_CODE)
    engine.add_module(DUMMY_CODE)
    engine.set_param(1, "Sensitivity", 7.5)

    stack = UndoStack()
    stack.execute(RemoveModuleCommand(engine, 1))

    assert len(engine.chain) == 1
    stack.undo()
    assert len(engine.chain) == 2
    assert engine.get_param(1, "Sensitivity") == pytest.approx(7.5)

    stack.redo()
    assert len(engine.chain) == 1


def test_remove_module_keeps_the_bypass_flag(engine, undo_dummy):
    """A removed module comes back with its power state."""
    handle = engine.add_module(DUMMY_CODE)
    engine.set_bypass(handle, True)

    stack = UndoStack()
    stack.execute(RemoveModuleCommand(engine, 0))
    stack.undo()

    assert engine.is_bypassed(0) is True


def test_reorder_module_can_be_undone_and_redone(engine, undo_dummy):
    """Moving a module in the chain is one undoable step."""
    engine.add_module(DUMMY_CODE)
    engine.add_module(DUMMY_CODE)
    engine.add_module(DUMMY_CODE)

    stack = UndoStack()
    stack.execute(ReorderModuleCommand(engine, 0, 2))

    assert _chain_codes(engine) == [DUMMY_CODE] * 3
    assert stack.depth == 1

    stack.undo()
    assert len(engine.chain) == 3
    stack.redo()
    assert len(engine.chain) == 3


def test_reorder_module_restores_the_original_order(engine, undo_dummy):
    """The order before the move is restored exactly."""
    handles = [engine.add_module(DUMMY_CODE) for _ in range(3)]
    engine.set_param(0, "Sensitivity", 1.0)
    engine.set_param(1, "Sensitivity", 2.0)
    engine.set_param(2, "Sensitivity", 3.0)
    before = [engine.get_param(i, "Sensitivity") for i in range(3)]

    stack = UndoStack()
    stack.execute(ReorderModuleCommand(engine, 0, 2))
    stack.undo()

    after = [engine.get_param(i, "Sensitivity") for i in range(3)]
    assert after == before
    assert len(handles) == 3


def test_set_param_can_be_undone_and_redone(engine, undo_dummy):
    """Changing a parameter is one undoable step."""
    handle = engine.add_module(DUMMY_CODE)
    stack = UndoStack()
    stack.execute(SetParamCommand(engine, 0, "Sensitivity", 6.0))

    assert engine.get_param(0, "Sensitivity") == pytest.approx(6.0)
    stack.undo()
    assert engine.get_param(0, "Sensitivity") == pytest.approx(3.0)
    stack.redo()
    assert engine.get_param(0, "Sensitivity") == pytest.approx(6.0)
    assert handle.index == 0


def test_toggle_bypass_can_be_undone_and_redone(engine, undo_dummy):
    """The power button is one undoable step."""
    engine.add_module(DUMMY_CODE)
    stack = UndoStack()
    stack.execute(ToggleBypassCommand(engine, 0, True))

    assert engine.is_bypassed(0) is True
    stack.undo()
    assert engine.is_bypassed(0) is False
    stack.redo()
    assert engine.is_bypassed(0) is True


def test_change_region_can_be_undone_and_redone(engine, undo_dummy):
    """Selecting a region is one undoable step."""
    engine.add_module(DUMMY_CODE)
    stack = UndoStack()
    stack.execute(ChangeRegionCommand(engine, 0, (100, 200, 80.0, 400.0)))

    assert engine.chain[0].module.region() == (100, 200, 80.0, 400.0)
    stack.undo()
    assert engine.chain[0].module.region() is None
    stack.redo()
    assert engine.chain[0].module.region() == (100, 200, 80.0, 400.0)


def test_load_preset_can_be_undone_and_redone(engine, undo_dummy):
    """Loading a preset is one undoable step."""
    engine.add_module(DUMMY_CODE)
    engine.set_param(0, "Sensitivity", 3.0)

    preset = {
        "format": "auralis.preset",
        "version": 1,
        "module": {"code": DUMMY_CODE, "element": "UndoDummy"},
        "params": {"Sensitivity": {"Type": "Float", "Value": 9.0}},
        "state": {"Type": "Base64", "Value": ""},
    }
    stack = UndoStack()
    stack.execute(LoadPresetCommand(engine, 0, preset, name="Warm Dummy"))

    assert engine.get_param(0, "Sensitivity") == pytest.approx(9.0)
    stack.undo()
    assert engine.get_param(0, "Sensitivity") == pytest.approx(3.0)
    stack.redo()
    assert engine.get_param(0, "Sensitivity") == pytest.approx(9.0)


def test_a_transaction_is_one_undo_step(engine, undo_dummy):
    """Several commands in a transaction undo together."""
    stack = UndoStack()
    transaction = Transaction(
        "Add module and set it up",
        [
            AddModuleCommand(engine, DUMMY_CODE),
            SetParamCommand(engine, 0, "Sensitivity", 5.0),
        ],
    )
    stack.execute(transaction)

    assert len(engine.chain) == 1
    assert engine.get_param(0, "Sensitivity") == pytest.approx(5.0)
    assert stack.depth == 1

    stack.undo()
    assert len(engine.chain) == 0

    stack.redo()
    assert len(engine.chain) == 1
    assert engine.get_param(0, "Sensitivity") == pytest.approx(5.0)


def test_a_transaction_label_is_kept(engine, undo_dummy):
    """The transaction carries the label of the whole edit."""
    stack = UndoStack()
    transaction = Transaction("Chain edit", [AddModuleCommand(engine, DUMMY_CODE)])
    stack.execute(transaction)
    assert stack.undo_label == "Chain edit"


# ---------------------------------------------------------------------------
# coalescing
# ---------------------------------------------------------------------------
def test_dragging_a_knob_is_one_undo_entry(engine, undo_dummy):
    """A knob drag produces one entry, not one per frame."""
    engine.add_module(DUMMY_CODE)
    clock = _FakeClock()
    stack = UndoStack(clock=clock)

    for value in (4.0, 5.0, 6.0, 7.0):
        stack.execute(SetParamCommand(engine, 0, "Sensitivity", value))
        clock.advance(0.02)

    assert stack.depth == 1
    assert engine.get_param(0, "Sensitivity") == pytest.approx(7.0)

    stack.undo()
    assert engine.get_param(0, "Sensitivity") == pytest.approx(3.0)

    stack.redo()
    assert engine.get_param(0, "Sensitivity") == pytest.approx(7.0)


def test_a_pause_breaks_the_merge(engine, undo_dummy):
    """Two changes more than a second apart are two entries."""
    engine.add_module(DUMMY_CODE)
    clock = _FakeClock()
    stack = UndoStack(clock=clock)

    stack.execute(SetParamCommand(engine, 0, "Sensitivity", 4.0))
    clock.advance(2.0)
    stack.execute(SetParamCommand(engine, 0, "Sensitivity", 5.0))

    assert stack.depth == 2
    stack.undo()
    assert engine.get_param(0, "Sensitivity") == pytest.approx(4.0)


def test_different_parameters_do_not_merge(engine, undo_dummy):
    """Two different knobs are two entries."""
    engine.add_module(DUMMY_CODE)
    clock = _FakeClock()
    stack = UndoStack(clock=clock)

    stack.execute(SetParamCommand(engine, 0, "Sensitivity", 4.0))
    stack.execute(SetParamCommand(engine, 0, "Threshold", -20.0))

    assert stack.depth == 2


def test_different_modules_do_not_merge(engine, undo_dummy):
    """The same knob of two modules are two entries."""
    engine.add_module(DUMMY_CODE)
    engine.add_module(DUMMY_CODE)
    clock = _FakeClock()
    stack = UndoStack(clock=clock)

    stack.execute(SetParamCommand(engine, 0, "Sensitivity", 4.0))
    stack.execute(SetParamCommand(engine, 1, "Sensitivity", 4.0))

    assert stack.depth == 2


def test_other_commands_do_not_merge_with_a_parameter_change(engine, undo_dummy):
    """A parameter change does not swallow another command."""
    engine.add_module(DUMMY_CODE)
    clock = _FakeClock()
    stack = UndoStack(clock=clock)

    stack.execute(SetParamCommand(engine, 0, "Sensitivity", 4.0))
    stack.execute(ToggleBypassCommand(engine, 0, True))

    assert stack.depth == 2
    assert stack.undo_label == "Bypass Undo Dummy"


def test_the_merge_window_can_be_switched_off(engine, undo_dummy):
    """A merge window of zero keeps every change in its own entry."""
    engine.add_module(DUMMY_CODE)
    stack = UndoStack(merge_window=0.0)

    stack.execute(SetParamCommand(engine, 0, "Sensitivity", 4.0))
    stack.execute(SetParamCommand(engine, 0, "Sensitivity", 5.0))

    assert stack.depth == 2


# ---------------------------------------------------------------------------
# the history
# ---------------------------------------------------------------------------
def test_the_depth_limit_drops_the_oldest_entries(engine, undo_dummy):
    """The history keeps at most ``depth_limit`` entries."""
    engine.add_module(DUMMY_CODE)
    clock = _FakeClock()
    stack = UndoStack(depth_limit=3, clock=clock)

    for value in (1.0, 2.0, 3.0, 4.0, 5.0):
        stack.execute(SetParamCommand(engine, 0, "Sensitivity", value))
        clock.advance(2.0)

    assert stack.depth == 3

    stack.undo()
    stack.undo()
    stack.undo()
    assert stack.can_undo is False
    # The two oldest entries are gone, so the value stays at the second one.
    assert engine.get_param(0, "Sensitivity") == pytest.approx(2.0)


def test_undo_of_an_empty_history_does_nothing(engine):
    """Undoing without an entry is harmless."""
    stack = UndoStack()
    assert stack.can_undo is False
    assert stack.can_redo is False
    assert stack.undo() is None
    assert stack.redo() is None


def test_a_new_command_clears_the_redo_history(engine, undo_dummy):
    """Redo is only possible while nothing new happened."""
    engine.add_module(DUMMY_CODE)
    stack = UndoStack()
    stack.execute(SetParamCommand(engine, 0, "Sensitivity", 4.0))
    stack.undo()
    assert stack.can_redo is True

    stack.execute(SetParamCommand(engine, 0, "Threshold", -20.0))
    assert stack.can_redo is False


def test_clear_forgets_the_whole_history(engine, undo_dummy):
    """``clear`` empties the history."""
    engine.add_module(DUMMY_CODE)
    stack = UndoStack()
    stack.execute(SetParamCommand(engine, 0, "Sensitivity", 4.0))
    stack.clear()

    assert stack.depth == 0
    assert stack.can_undo is False
    assert stack.can_redo is False


def test_the_labels_describe_the_action(engine, undo_dummy):
    """Every entry carries the text the user interface shows."""
    stack = UndoStack()
    stack.execute(AddModuleCommand(engine, DUMMY_CODE))
    assert stack.undo_label == "Add Undo Dummy"

    stack.execute(SetParamCommand(engine, 0, "Sensitivity", 5.0))
    assert stack.undo_label == "Change Sensitivity"

    stack.execute(ToggleBypassCommand(engine, 0, True))
    assert stack.undo_label == "Bypass Undo Dummy"

    stack.execute(RemoveModuleCommand(engine, 0))
    assert stack.undo_label == "Remove Undo Dummy"

    stack.execute(ChangeRegionCommand(engine, 0, (0, 10, 0.0, 100.0)))
    assert stack.undo_label == "Change Region"


def test_the_summary_describes_the_history(engine, undo_dummy):
    """The summary is what a session document stores."""
    engine.add_module(DUMMY_CODE)
    stack = UndoStack()
    stack.execute(AddModuleCommand(engine, DUMMY_CODE))
    stack.execute(SetParamCommand(engine, 0, "Sensitivity", 5.0))

    summary = stack.summary()
    assert summary["depth"] == 2
    assert summary["labels"] == ["Add Undo Dummy", "Change Sensitivity"]
    assert summary["can_undo"] is True
    assert summary["can_redo"] is False
    assert summary["next_undo"] == "Change Sensitivity"


def test_the_base_command_never_merges():
    """The default merge hook says no."""
    command = Command()
    assert command.merge_with(Command()) is False


def test_the_base_command_has_to_be_implemented():
    """``do`` and ``undo`` are abstract."""
    command = Command()
    with pytest.raises(NotImplementedError):
        command.do()
    with pytest.raises(NotImplementedError):
        command.undo()
