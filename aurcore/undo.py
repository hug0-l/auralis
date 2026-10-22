"""
aurcore.undo — undo / redo for the Auralis workbench.

The workbench edits a *chain* of modules.  Every edit is expressed as a
:class:`Command`: a small object that knows how to apply itself (``do``) and
how to revert itself (``undo``).  :class:`UndoStack` keeps the commands in
order, hands them back on ``undo()`` and replays them on ``redo()``.

Design notes
------------

*Identity, not position.*  A command addresses a module by the module
*instance* it found in the chain, not by a fixed index.  Removing the module
in front of it therefore does not make the command point at the wrong
module — the instance is looked up again when the command runs.

*Coalescing.*  Turning a knob produces one command per frame, which would
fill the history with hundreds of entries.  A command may therefore declare
that it continues the previous one (:meth:`Command.merge_with`); the stack
folds it into the previous entry when the two happen within the merge window
(one second by default).  The folded command keeps the value from *before*
the whole drag, so a single undo steps back over the whole gesture.

*Grouping.*  A chain edit touches several modules at once.  Those edits are
bundled in a :class:`Transaction`, which behaves like a single command and is
therefore a single entry in the history.

The stack is bounded: at most ``depth_limit`` (200) entries are kept, and the
oldest one is dropped when the limit is reached.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from . import registry
from .serial.preset_json import apply_preset, preset_from_module

__all__ = [
    "DEFAULT_DEPTH_LIMIT",
    "DEFAULT_MERGE_WINDOW",
    "Command",
    "Transaction",
    "UndoEntry",
    "UndoStack",
    "AddModuleCommand",
    "RemoveModuleCommand",
    "ReorderModuleCommand",
    "SetParamCommand",
    "ToggleBypassCommand",
    "ChangeRegionCommand",
    "LoadPresetCommand",
]

#: How many entries the history keeps before the oldest one is dropped.
DEFAULT_DEPTH_LIMIT = 200

#: Two edits of the same kind merge when they are less than this apart (s).
DEFAULT_MERGE_WINDOW = 1.0


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _chain(engine: Any) -> list:
    """The engine's chain steps (an empty list when the engine has none)."""
    return list(getattr(engine, "chain", []) or [])


def _index_of(engine: Any, module: Any) -> int | None:
    """The current index of ``module`` in the engine's chain, or ``None``."""
    if module is None:
        return None
    for index, step in enumerate(_chain(engine)):
        if step.module is module:
            return index
    return None


def _module_at(engine: Any, index: int) -> Any:
    """The module instance at ``index``, or ``None`` when the index is bad."""
    steps = _chain(engine)
    if 0 <= int(index) < len(steps):
        return steps[int(index)].module
    return None


def _resolve_module(engine: Any, index: int, cached: Any) -> Any:
    """The module a command works on.

    The instance the command captured is preferred, but it is looked up again
    when it left the chain in the meantime (a redo after the module was
    removed and added again creates a new instance).
    """
    if cached is not None and _index_of(engine, cached) is not None:
        return cached
    return _module_at(engine, index)


def _display_name(code: str) -> str:
    """The user-visible name of a module code ("AUR_DEHUM" -> "De-Hum")."""
    try:
        cls = registry.get(code)
    except KeyError:
        return str(code)
    return str(getattr(cls, "display_name", code) or code)


def _name_of(module: Any) -> str:
    """The user-visible name of a module instance."""
    if module is None:
        return ""
    return str(getattr(module, "display_name", "") or getattr(module, "code", "") or "")


def _read_region(module: Any) -> Any:
    """The region currently selected on a module (``None`` when it has none)."""
    region = getattr(module, "region", None)
    if callable(region):
        try:
            return region()
        except Exception:  # noqa: BLE001 - a broken module must not break undo
            return None
    if hasattr(module, "region"):
        return region
    return getattr(module, "selection", None)


def _clear_region(module: Any) -> bool:
    """Remove the region from a module.

    A module that offers ``clear_region`` is asked to clear itself; a module
    that only knows ``set_selection`` gets an empty selection; a module that
    only knows ``set_region`` gets an empty region.
    """
    for name in ("clear_region", "reset_region"):
        clearer = getattr(module, name, None)
        if callable(clearer):
            clearer()
            return True
    setter = getattr(module, "set_selection", None)
    if callable(setter):
        setter(None, None)
        return True
    setter = getattr(module, "set_region", None)
    if callable(setter):
        setter(0, 0, 0.0, 0.0)
        return True
    if not callable(getattr(module, "region", None)):
        try:
            module.region = None
            return True
        except Exception:  # noqa: BLE001
            return False
    return False


def _apply_region(module: Any, region: Any) -> None:
    """Hand a region to a module, using whatever API the module offers."""
    if region is None:
        _clear_region(module)
        return

    if isinstance(region, dict):
        start = region.get("start", 0)
        end = region.get("end", 0)
        low = region.get("low", region.get("low_hz", 0.0))
        high = region.get("high", region.get("high_hz", 0.0))
        region = (start, end, low, high)
    elif isinstance(region, (tuple, list)):
        region = tuple(region)
    else:
        region = (0, 0, 0.0, 0.0)

    setter = getattr(module, "set_region", None)
    if callable(setter) and len(region) >= 4:
        setter(region[0], region[1], region[2], region[3])
        return
    setter = getattr(module, "set_selection", None)
    if callable(setter):
        setter(region[0], region[1] if len(region) > 1 else region[0])
        return
    try:
        module.region = region
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------
class Command:
    """Base class of every undoable action.

    A command applies itself with :meth:`do` and reverts itself with
    :meth:`undo`.  ``label`` is the text the user interface shows in the undo
    menu ("Add De-Hum", "Change Sensitivity").
    """

    #: The text shown in the user interface for this action.
    label: str = "Action"

    def do(self) -> None:
        """Apply the command."""
        raise NotImplementedError

    def undo(self) -> None:
        """Revert the command."""
        raise NotImplementedError

    def merge_with(self, other: "Command") -> bool:
        """Return ``True`` when ``other`` continues this command.

        The stack asks the top of the history this question before it pushes
        a new entry.  A merging implementation keeps the state needed to
        undo the whole sequence (see :meth:`merge_from`).
        """
        return False

    def merge_from(self, other: "Command") -> None:
        """Fold the already executed ``other`` into this command.

        Called only after :meth:`merge_with` returned ``True``.  The default
        implementation does nothing, which is correct for commands that
        never merge.
        """
        return None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} {self.label!r}>"


class Transaction(Command):
    """Several commands that undo and redo as one step.

    The workbench edits a whole chain at once when a module is inserted with
    its neighbours, when a preset is applied to a group of modules or when a
    spectral repair touches the region *and* the parameters.  Those edits
    belong together, so they are bundled in a transaction.
    """

    def __init__(self, label: str = "Edit", commands: Iterable[Command] | None = None):
        """Create a transaction with an optional list of commands."""
        self.label = str(label or "Edit")
        self.commands: list[Command] = list(commands or [])

    def add(self, command: Command) -> "Transaction":
        """Append a command to the transaction and return the transaction."""
        self.commands.append(command)
        return self

    def do(self) -> None:
        """Apply every command in order (rolling back on a failure)."""
        applied: list[Command] = []
        try:
            for command in self.commands:
                command.do()
                applied.append(command)
        except Exception:
            for command in reversed(applied):
                command.undo()
            raise

    def undo(self) -> None:
        """Revert every command in reverse order."""
        for command in reversed(self.commands):
            command.undo()

    def merge_with(self, other: "Command") -> bool:
        """A transaction never merges with another command."""
        return False

    def __len__(self) -> int:
        """The number of commands in the transaction."""
        return len(self.commands)


class AddModuleCommand(Command):
    """Add a module to the chain."""

    def __init__(self, engine: Any, code: str, index: int | None = None, label: str | None = None):
        """Create the command; ``index`` is the position in the chain."""
        self.engine = engine
        self.code = str(code)
        self.index = None if index is None else int(index)
        self.label = label or f"Add {_display_name(self.code)}"
        self._module: Any = None

    @property
    def module(self) -> Any:
        """The module instance the command created (after ``do``)."""
        return self._module

    def do(self) -> None:
        """Insert the module into the chain."""
        handle = self.engine.add_module(self.code, self.index)
        self._module = handle.module

    def undo(self) -> None:
        """Take the module out of the chain again."""
        index = _index_of(self.engine, self._module)
        if index is None:
            return
        self.engine.remove_module(index)


class RemoveModuleCommand(Command):
    """Remove a module from the chain, keeping it restorable."""

    def __init__(self, engine: Any, index: int, label: str | None = None):
        """Create the command for the module at ``index``."""
        self.engine = engine
        self.index = int(index)
        self.label = label or f"Remove {_name_of(_module_at(engine, self.index)) or 'Module'}"
        self._module: Any = None
        self._code: str = ""
        self._preset: dict | None = None
        self._bypass: bool = False

    def do(self) -> None:
        """Remove the module and remember how to restore it."""
        module = _resolve_module(self.engine, self.index, self._module)
        if module is None:
            return
        index = _index_of(self.engine, module)
        if index is None:
            return
        steps = _chain(self.engine)
        self._module = module
        self._code = str(getattr(module, "code", "") or "")
        self._bypass = bool(steps[index].bypass)
        self._preset = preset_from_module(module)
        self.engine.remove_module(index)

    def undo(self) -> None:
        """Put the module back where it was, with its parameters intact."""
        if not self._code:
            return
        handle = self.engine.add_module(self._code, self.index)
        self._module = handle.module
        if self._preset:
            apply_preset(self._module, self._preset)
        self.engine.set_bypass(handle, self._bypass)


class ReorderModuleCommand(Command):
    """Move a module to another position in the chain."""

    def __init__(self, engine: Any, src: int, dst: int, label: str | None = None):
        """Create the command that moves the module at ``src`` to ``dst``."""
        self.engine = engine
        self.src = int(src)
        self.dst = int(dst)
        self.label = label or "Reorder Module"
        self._moved: Any = None

    @property
    def module(self) -> Any:
        """The module instance that was moved."""
        return self._moved

    def do(self) -> None:
        """Move the module to its new position."""
        steps = _chain(self.engine)
        if not (0 <= self.src < len(steps)):
            return
        self._moved = steps[self.src].module
        self.engine.reorder(self.src, self.dst)

    def undo(self) -> None:
        """Move the module back to where it came from."""
        index = _index_of(self.engine, self._moved)
        if index is None or index == self.src:
            return
        self.engine.reorder(index, self.src)


class SetParamCommand(Command):
    """Change one parameter of one module.

    Dragging a knob produces one of these per frame.  Consecutive commands
    for the same module and the same parameter merge, so the whole gesture is
    a single entry in the history.
    """

    def __init__(self, engine: Any, index: int, name: str, value: Any, label: str | None = None):
        """Create the command that sets ``name`` of the module at ``index``."""
        self.engine = engine
        self.index = int(index)
        self.name = str(name)
        self.value = value
        self.label = label or f"Change {self.name}"
        self._module: Any = _module_at(engine, self.index)
        self._old: Any = None
        self._captured = False
        if self._module is not None:
            self._capture_old()

    # -- identity -------------------------------------------------------------
    @property
    def module(self) -> Any:
        """The module instance the command works on."""
        found = _resolve_module(self.engine, self.index, self._module)
        if found is not None:
            self._module = found
        return self._module

    @property
    def module_key(self) -> int | None:
        """A stable identity of the module (used to decide about merging)."""
        module = self.module
        return None if module is None else id(module)

    @property
    def old_value(self) -> Any:
        """The value the parameter had before the first change."""
        return self._old

    # -- behaviour ------------------------------------------------------------
    def _capture_old(self) -> None:
        """Read the current value of the parameter (once)."""
        if self._captured:
            return
        index = _index_of(self.engine, self._module)
        if index is None:
            return
        try:
            self._old = self.engine.get_param(index, self.name)
        except KeyError:
            return
        self._captured = True

    def do(self) -> None:
        """Write the new value into the parameter."""
        module = self.module
        if module is None:
            return
        index = _index_of(self.engine, module)
        if index is None:
            return
        self._capture_old()
        self.engine.set_param(index, self.name, self.value)

    def undo(self) -> None:
        """Put the value back the parameter had before the change."""
        index = _index_of(self.engine, self.module)
        if index is None or not self._captured:
            return
        self.engine.set_param(index, self.name, self._old)

    def merge_with(self, other: "Command") -> bool:
        """Merge with another change of the same parameter of the same module."""
        if not isinstance(other, SetParamCommand):
            return False
        if other.name != self.name:
            return False
        key = self.module_key
        return key is not None and key == other.module_key

    def merge_from(self, other: "Command") -> None:
        """Keep the newest value but the oldest previous value."""
        if isinstance(other, SetParamCommand):
            self.value = other.value


class ToggleBypassCommand(Command):
    """Switch a module in or out of the chain (the power button)."""

    def __init__(self, engine: Any, index: int, bypass: bool, label: str | None = None):
        """Create the command that sets the bypass flag of the module."""
        self.engine = engine
        self.index = int(index)
        self.bypass = bool(bypass)
        name = _name_of(_module_at(engine, self.index)) or "Module"
        self.label = label or (f"Bypass {name}" if self.bypass else f"Enable {name}")
        self._module: Any = _module_at(engine, self.index)
        self._previous: bool | None = None

    @property
    def module(self) -> Any:
        """The module instance the command works on."""
        return self._module

    def do(self) -> None:
        """Set the bypass flag."""
        module = _resolve_module(self.engine, self.index, self._module)
        if module is None:
            return
        self._module = module
        index = _index_of(self.engine, module)
        if index is None:
            return
        if self._previous is None:
            self._previous = bool(self.engine.is_bypassed(index))
        self.engine.set_bypass(index, self.bypass)

    def undo(self) -> None:
        """Restore the previous bypass flag."""
        module = _resolve_module(self.engine, self.index, self._module)
        if module is None:
            return
        self._module = module
        index = _index_of(self.engine, module)
        if index is None or self._previous is None:
            return
        self.engine.set_bypass(index, self._previous)


class ChangeRegionCommand(Command):
    """Change the region a module works on (the spectral edit area)."""

    def __init__(self, engine: Any, index: int, region: Any, label: str | None = None):
        """Create the command that selects ``region`` on the module."""
        self.engine = engine
        self.index = int(index)
        self.region = region
        self.label = label or "Change Region"
        self._module: Any = _module_at(engine, self.index)
        self._previous: Any = None
        self._captured = False

    @property
    def module(self) -> Any:
        """The module instance the command works on."""
        return self._module

    def do(self) -> None:
        """Hand the new region to the module."""
        module = _resolve_module(self.engine, self.index, self._module)
        if module is None:
            return
        self._module = module
        if not self._captured:
            self._previous = _read_region(self._module)
            self._captured = True
        _apply_region(self._module, self.region)

    def undo(self) -> None:
        """Restore the previous region."""
        module = _resolve_module(self.engine, self.index, self._module)
        if module is None:
            return
        self._module = module
        if not self._captured:
            return
        _apply_region(self._module, self._previous)


class LoadPresetCommand(Command):
    """Load a preset into a module."""

    def __init__(
        self,
        engine: Any,
        index: int,
        preset: dict,
        name: str | None = None,
        label: str | None = None,
    ):
        """Create the command that applies ``preset`` to the module."""
        self.engine = engine
        self.index = int(index)
        self.preset = dict(preset or {})
        self.name = str(name or "Preset")
        self.label = label or f"Load {self.name}"
        self._module: Any = _module_at(engine, self.index)
        self._before: dict | None = None

    @property
    def module(self) -> Any:
        """The module instance the command works on."""
        return self._module

    def do(self) -> None:
        """Apply the preset to the module."""
        module = _resolve_module(self.engine, self.index, self._module)
        if module is None:
            return
        self._module = module
        if self._before is None:
            self._before = preset_from_module(self._module)
        apply_preset(self._module, self.preset)

    def undo(self) -> None:
        """Restore the parameters the module had before the preset."""
        module = _resolve_module(self.engine, self.index, self._module)
        if module is None:
            return
        self._module = module
        if self._before is None:
            return
        apply_preset(self._module, self._before)


# ---------------------------------------------------------------------------
# the stack
# ---------------------------------------------------------------------------
@dataclass
class UndoEntry:
    """One entry of the history."""

    command: Command
    label: str = ""
    timestamp: float = 0.0
    merges: int = 0

    def to_dict(self) -> dict:
        """A JSON-safe summary of the entry."""
        return {
            "label": self.label,
            "timestamp": self.timestamp,
            "merges": self.merges,
        }


class UndoStack:
    """The undo / redo history of one session.

    ``execute`` runs a command and records it, ``undo`` reverts the last
    entry and ``redo`` replays it.  The history is bounded: when it grows
    past ``depth_limit`` the oldest entry is dropped.
    """

    def __init__(
        self,
        depth_limit: int = DEFAULT_DEPTH_LIMIT,
        merge_window: float = DEFAULT_MERGE_WINDOW,
        clock: Callable[[], float] = time.monotonic,
    ):
        """Create an empty history."""
        self.depth_limit = max(1, int(depth_limit))
        self.merge_window = max(0.0, float(merge_window))
        self._clock = clock
        self._undo: list[UndoEntry] = []
        self._redo: list[UndoEntry] = []

    # -- state ----------------------------------------------------------------
    @property
    def can_undo(self) -> bool:
        """True when there is something to undo."""
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        """True when there is something to redo."""
        return bool(self._redo)

    @property
    def depth(self) -> int:
        """The number of entries that can be undone."""
        return len(self._undo)

    @property
    def undo_label(self) -> str | None:
        """The label of the entry ``undo()`` would revert."""
        return self._undo[-1].label if self._undo else None

    @property
    def redo_label(self) -> str | None:
        """The label of the entry ``redo()`` would replay."""
        return self._redo[-1].label if self._redo else None

    def entries(self) -> list[UndoEntry]:
        """A copy of the undo entries, oldest first."""
        return list(self._undo)

    def labels(self) -> list[str]:
        """The labels of the undo entries, oldest first."""
        return [entry.label for entry in self._undo]

    def summary(self) -> dict:
        """A JSON-safe summary of the history (stored in a session document)."""
        return {
            "depth": len(self._undo),
            "labels": [entry.label for entry in self._undo],
            "redo_labels": [entry.label for entry in self._redo],
            "merges": sum(entry.merges for entry in self._undo),
            "can_undo": self.can_undo,
            "can_redo": self.can_redo,
            "next_undo": self.undo_label,
            "next_redo": self.redo_label,
            "depth_limit": self.depth_limit,
        }

    # -- editing --------------------------------------------------------------
    def execute(self, command: Command) -> Command:
        """Run a command and record it in the history.

        The command is executed first; when it continues the entry on top of
        the history (and the merge window has not passed) it is folded into
        that entry instead of becoming a new one.
        """
        if command is None:
            raise ValueError("execute() needs a command")
        command.do()
        self._redo.clear()
        if not self._merge(command):
            self._undo.append(
                UndoEntry(command=command, label=command.label, timestamp=self._now())
            )
            self._trim()
        return command

    def undo(self) -> Command | None:
        """Revert the last entry and return the command (or ``None``)."""
        if not self._undo:
            return None
        entry = self._undo.pop()
        entry.command.undo()
        self._redo.append(entry)
        return entry.command

    def redo(self) -> Command | None:
        """Replay the last undone entry and return the command (or ``None``)."""
        if not self._redo:
            return None
        entry = self._redo.pop()
        entry.command.do()
        entry.timestamp = self._now()
        self._undo.append(entry)
        return entry.command

    def clear(self) -> None:
        """Forget the whole history."""
        self._undo.clear()
        self._redo.clear()

    # -- internals ------------------------------------------------------------
    def _now(self) -> float:
        """The current time of the history clock."""
        try:
            return float(self._clock())
        except Exception:  # noqa: BLE001 - a broken clock must not break undo
            return 0.0

    def _merge(self, command: Command) -> bool:
        """Fold ``command`` into the entry on top of the history."""
        if self.merge_window <= 0.0 or not self._undo:
            return False
        top = self._undo[-1]
        try:
            mergeable = top.command.merge_with(command)
        except Exception:  # noqa: BLE001 - a broken command must not break undo
            mergeable = False
        if not mergeable:
            return False
        now = self._now()
        if now - top.timestamp > self.merge_window:
            return False
        try:
            top.command.merge_from(command)
        except Exception:  # noqa: BLE001
            return False
        top.label = command.label
        top.timestamp = now
        top.merges += 1
        return True

    def _trim(self) -> None:
        """Drop the oldest entries until the history fits the depth limit."""
        while len(self._undo) > self.depth_limit:
            self._undo.pop(0)

    def __len__(self) -> int:
        """The number of entries that can be undone."""
        return len(self._undo)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<UndoStack depth={len(self._undo)} can_undo={self.can_undo}>"
