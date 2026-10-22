"""
aurcore.api — the public facade of the Auralis DSP core.

The core owns a *chain* of modules.  ``Engine.process`` runs the chain in
signal order.  The engine knows nothing about plugin formats, the network, or
the user interface.

The two processing modes
------------------------
The engine knows in which of the two modes the chain works:

``"realtime"``
    The chain is played while somebody waits for it: a preview in a host, a
    plug-in in a real time session.  Every module of the chain has to keep
    inside the budget of a real time block.
``"offline"``
    The chain is rendered into a file.  There is no deadline, so a module may
    use the window it likes best — and it has to declare the look ahead that
    costs, because the host has to be told how late the audio is.

The mode belongs to the chain, not to a module: the engine gives it to every
module of the chain before the chain runs (:attr:`Engine.processing_mode`).
A module reads the mode it works in through
:attr:`aurcore.modules.base.Module.processing_mode` and never writes it.

The level of the output
-----------------------
The engine is one of the two places where the rule of the digital ceiling is
applied: the block it hands back is **clipped** at the limit the chain
declares, so nothing the product writes into a file and nothing it hands to
a host is above the ceiling.  The other place is the plug-in adapter.  A
module's own output is not limited: the rule is at the output, not inside
the module.  See :mod:`aurcore.ceiling`.
"""
from __future__ import annotations

import json
from typing import Any, Iterable

import numpy as np

from . import registry
from .ceiling import CEILING, chain_limit, clip_to_ceiling, peak_of
from .params import ParamDesc, TAIL_INFINITE
from .serial import typed_value as tv

# Importing the module package populates the registry, so a caller only has
# to import ``aurcore`` (or ``aurcore.api``) to be able to instantiate
# modules.  The import is guarded so a single broken module file cannot make
# the whole core unimportable.
try:  # pragma: no cover - defensive
    from . import modules as _modules  # noqa: F401
    from .modules.base import (
        PROCESSING_MODE_OFFLINE,
        PROCESSING_MODE_PARAMETER,
        PROCESSING_MODE_REALTIME,
        PROCESSING_MODES,
    )
except Exception:  # pragma: no cover
    # The values are the contract, so they are repeated here: a module file
    # that cannot be imported must not take the core with it.
    PROCESSING_MODE_REALTIME = "realtime"
    PROCESSING_MODE_OFFLINE = "offline"
    PROCESSING_MODES = (PROCESSING_MODE_REALTIME, PROCESSING_MODE_OFFLINE)
    PROCESSING_MODE_PARAMETER = "Processing Mode"


#: The declared length of a parameter ramp, in seconds.
#:
#: A parameter change that arrives while the chain is playing is applied
#: over this time instead of as a step.  A step is a click: the output
#: jumps by the whole change at once, and the jump is heard as a tick in
#: the programme.  The product's own preview path crosses a change over
#: 22.0 ms and is taken to be good, so 20 ms is inside what the product
#: already accepts, and it is short enough that a knob still feels
#: immediate.
PARAM_RAMP_SECONDS = 0.020

#: The number of steps a ramp is made of.
#:
#: The value moves in equal steps, so the largest jump the ramp makes is
#: the whole change divided by this.  Sixteen steps over 20 ms is one step
#: every 1.25 ms.
PARAM_RAMP_STEPS = 16

#: The modules whose block must not be cut into pieces.
#:
#: A module that makes the same samples of a block whether it is given in
#: one piece or in four can be given a block in pieces, and the ramp needs
#: the block to be cut finely enough to be smooth.  A module that does the
#: analysis of the whole block at once cannot: its window does not fit
#: into a piece, and the edge of the piece shows in the output as a step of
#: its own -- a click, which is the very thing the ramp is there to stop.
#:
#: The list is measured, not guessed: the test measures every module of the
#: catalogue and fails when the list and the measurement disagree.  A
#: module may also say so itself, with a ``block_agnostic`` attribute.
CUT_SENSITIVE_MODULES = frozenset({"AUR_DEBLEED"})


class _Ramp:
    """The state of one parameter that is on its way to a new value.

    The value moves in NATURAL UNITS: the plain number the parameter
    carries, not a knob position.  ``current`` is the value the parameter
    has reached, so a change that arrives while the ramp runs re-targets
    the ramp from there and the value never jumps.
    """

    __slots__ = ("start", "target", "total", "remaining", "current", "step")

    def __init__(self, start: float, target: float, total: int):
        self.start = float(start)
        self.target = float(target)
        self.total = max(1, int(total))
        self.remaining = self.total
        self.current = float(start)
        # the block is cut into pieces of at most this many samples
        self.step = max(1, self.total // PARAM_RAMP_STEPS)


class ModuleHandle:
    """Opaque reference to a module instance inside an engine chain."""
    __slots__ = ("_engine", "_index", "_module")

    def __init__(self, engine: "Engine", index: int, module):
        self._engine = engine
        self._index = index
        self._module = module

    @property
    def index(self) -> int:
        return self._index

    @property
    def module(self):
        return self._module

    @property
    def code(self) -> str:
        return self._module.code

    @property
    def display_name(self) -> str:
        return self._module.display_name

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ModuleHandle {self._module.code} @{self._index}>"


class ChainStep:
    """One entry in the chain: a module instance plus its bypass flag."""

    __slots__ = ("module", "bypass")

    def __init__(self, module, bypass: bool = False):
        self.module = module
        self.bypass = bypass


def module_tail(module) -> int:
    """The tail of one module, in samples.

    A module declares its tail as an attribute or as a property; both are
    read the same way here, and a module that declares nothing has no tail.
    The number is never negative: a module that rings forever reports
    :data:`aurcore.params.TAIL_INFINITE`, which is a positive number.
    """
    value = getattr(module, "tail_samples", 0)
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def module_output_rate(module) -> int | None:
    """The rate a module's output runs at, or None when it keeps the rate.

    A module that changes the rate says so: it declares the rate its
    output runs at (``output_sample_rate``).  A module that says nothing
    hands its output over at the rate of the stage before it, so the
    chain's rate only changes where a module says it does.
    """
    value = getattr(module, "output_sample_rate", None)
    if value is None:
        return None
    try:
        rate = int(value)
    except (TypeError, ValueError):
        return None
    return rate if rate > 0 else None


class Engine:
    """An audio engine holding a module chain.

    The engine runs the chain in signal order and holds the rule of the
    digital ceiling at its output: the block it hands back is clipped at
    the limit the chain declares.  See :mod:`aurcore.ceiling`.
    """

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self._chain: list[ChainStep] = []
        # the resampler that brings a changed rate back to the engine's own
        self._converter = None
        self._converter_key: tuple | None = None
        # the samples of the conversion that do not fit into a block yet
        self._pending = None
        # the mode the chain works in: "realtime" until somebody says otherwise
        self._processing_mode = PROCESSING_MODE_REALTIME
        # the parameters that are on their way to a new value: the chain
        # index, then the name of the parameter, then the ramp
        self._ramps: dict[int, dict[str, _Ramp]] = {}
        # True once the chain has played a block: a change that arrives
        # after that is a change the user hears, and it is applied over
        # the ramp.  A change that arrives before it is a change of the
        # setup, and it is applied at once.
        self._played = False

    # -- the processing mode --------------------------------------------------
    @property
    def processing_mode(self) -> str:
        """The mode the chain works in: ``"realtime"`` or ``"offline"``.

        The mode is the chain's, not a module's: it says which of the two
        configurations every module of the chain works in -- the one that
        fits the deadline of a real time host, or the best one it has.

        The engine gives the mode to every module of the chain before the
        chain runs, and it writes it into the ``Processing Mode`` parameter of
        every module that has one, so the state of the module carries it and
        a host that reads the parameter back sees the mode the chain is in.
        """
        return self._processing_mode

    @processing_mode.setter
    def processing_mode(self, value: str) -> None:
        """Put the chain into a mode.

        Only the two modes exist.  Anything else is refused, because a mode
        nobody knows is a mode nobody can honour.
        """
        self._set_mode(self._check_mode(value))

    @staticmethod
    def _check_mode(value: Any) -> str:
        """The mode a value names, or a refusal."""
        mode = str(value).strip().lower()
        if mode not in PROCESSING_MODES:
            raise ValueError(
                f"unknown processing mode {value!r}; the modes are "
                f"{', '.join(PROCESSING_MODES)}"
            )
        return mode

    def _set_mode(self, mode: str) -> None:
        """Put the chain into a mode that has already been checked."""
        if mode == self._processing_mode:
            return
        self._processing_mode = mode
        self._apply_mode()

    def _apply_mode(self) -> None:
        """Give every module of the chain the mode of the chain.

        The module's own attribute is what the module reads, and the mode is
        also written into the module's parameter, so the mode is part of the
        module's state and a host can set it there.
        """
        mode = self._processing_mode
        for step in self._chain:
            module = step.module
            if module._processing_mode != mode:
                module._processing_mode = mode
            try:
                if module.params.has(PROCESSING_MODE_PARAMETER):
                    if module.params.get(PROCESSING_MODE_PARAMETER) != mode:
                        module.params.set(PROCESSING_MODE_PARAMETER, mode)
            except Exception:  # pragma: no cover - a module without parameters
                pass

    # -- introspection --------------------------------------------------------
    @staticmethod
    def list_modules() -> list[registry.ModuleInfo]:
        return registry.all()

    @property
    def chain(self) -> list[ChainStep]:
        return self._chain

    def handles(self) -> list[ModuleHandle]:
        return [ModuleHandle(self, i, s.module) for i, s in enumerate(self._chain)]

    def _step(self, handle_or_index) -> ChainStep:
        if isinstance(handle_or_index, ModuleHandle):
            idx = handle_or_index.index
        else:
            idx = int(handle_or_index)
        if not (0 <= idx < len(self._chain)):
            raise IndexError(idx)
        return self._chain[idx]

    # -- chain editing --------------------------------------------------------
    def add_module(self, code: str, at: int | None = None) -> ModuleHandle:
        module = registry.create(code, self.sample_rate, self.channels)
        step = ChainStep(module)
        if at is None or at >= len(self._chain):
            self._chain.append(step)
        else:
            self._chain.insert(max(0, int(at)), step)
        self._reindex()
        return ModuleHandle(self, len(self._chain) - 1 if at is None else int(at), module)

    def remove_module(self, handle_or_index) -> None:
        step = self._step(handle_or_index)
        self._chain.remove(step)
        self._reindex()

    def clear_chain(self) -> None:
        self._chain.clear()

    def reorder(self, src: int, dst: int) -> None:
        if not (0 <= src < len(self._chain)):
            raise IndexError(src)
        dst = max(0, min(int(dst), len(self._chain) - 1))
        step = self._chain.pop(src)
        self._chain.insert(dst, step)
        self._reindex()

    def _reindex(self) -> None:
        pass  # handles are transient views; index is recomputed on access

    # -- parameters -----------------------------------------------------------
    def param_info(self, handle_or_index) -> list[ParamDesc]:
        return self._step(handle_or_index).module.params.descs()

    def get_param(self, handle_or_index, name: str):
        return self._step(handle_or_index).module.params.get(name)

    def set_param(self, handle_or_index, name: str, value,
                  ramp_seconds: float | None = None) -> None:
        """Set one parameter of one module of the chain.

        ``ramp_seconds`` asks for the change to be applied over a ramp
        instead of as a step: the value moves from the one the parameter had
        to the one given here, in natural units, over that many seconds.
        The parameter holds the value it is going to have right away, so the
        automation reaches its target even while the ramp runs, and a change
        that arrives while a ramp runs re-targets the ramp from the value
        the parameter has reached -- the value never jumps.

        ``None`` -- the default -- is the core's own rule, and the rule is
        the same for every caller: the application, the server and the
        plug-in.  A change that arrives while the chain is playing is
        applied over :data:`PARAM_RAMP_SECONDS`; a change that arrives
        before the chain has played anything is a change of the setup and
        is applied at once.  0.0 asks for the change at once, whatever the
        chain does: that is what a state that is put back where it belongs
        needs.  See :meth:`_ramp_for_a_change`.

        A parameter that is stepped -- an enum, a switch, a count -- has no
        value between two of its values to move through, so it changes at
        once.

        The mode is a parameter of a module, so setting it here changes the
        mode of the chain: a host that sets the mode on the module it owns
        changes the way the whole chain works, and the engine gives the new
        mode to every module of the chain.
        """
        step = self._step(handle_or_index)
        module = step.module
        desc = module.params.desc(name)
        coerced = desc.coerce(value)

        index = self._index_of(step)
        ramps = self._ramps.get(index)
        ramp = ramps.get(name) if ramps is not None else None

        if ramp is not None and ramp.target == coerced:
            # The value is already on its way there.  The ramp goes on and
            # the parameter keeps the value it is going to have.
            module.params.set(name, coerced)
            return

        if ramp_seconds is None:
            # the core's own rule
            ramp_seconds = self._ramp_for_a_change()

        if ramp_seconds > 0.0 and desc.is_continuous:
            # the change is applied over a ramp
            start = ramp.current if ramp is not None else module.params.get(name)
            if start != coerced:
                self._start_ramp(index, name, start, coerced, ramp_seconds)
                module.params.set(name, coerced)
                return

        # a plain change: the value is there at once, and a ramp that was
        # running for this parameter is over
        if ramp is not None:
            self._cancel_ramp(index, name)
        module.params.set(name, value)

        if module.params.canonical(name) == PROCESSING_MODE_PARAMETER:
            try:
                mode = self._check_mode(value)
            except ValueError:
                return
            self._set_mode(mode)

    # -- the ramp --------------------------------------------------------------
    def _ramp_for_a_change(self) -> float:
        """How long a change takes when the caller does not say.

        The rule is the core's own, and it is the same for every caller:
        the application, the server and the plug-in.  A value that is
        changed in one of them sounds the same as the same value that is
        changed in another, and that is the product's rule: **the same
        value must sound the same however it arrives.**

        A change that arrives while the chain is playing is applied over
        the ramp: a step is a click, and a click is heard.  A change that
        arrives before the chain has played anything is a change of the
        setup -- a document that is loaded, a chain that is built -- and it
        is applied at once, or the first block of the playback would fade
        in.  A chain that renders a file is not played by anybody, so a
        change to it is applied at once as well: a file is not allowed to
        fade in.
        """
        if self._processing_mode != PROCESSING_MODE_REALTIME:
            return 0.0

        if not self._played:
            return 0.0

        return PARAM_RAMP_SECONDS
    def _index_of(self, step: ChainStep) -> int:
        """The place of a step in the chain, or -1 when it is not in it."""
        for index, candidate in enumerate(self._chain):
            if candidate is step:
                return index
        return -1

    def _start_ramp(self, index: int, name: str, start: float, target: float,
                    ramp_seconds: float) -> None:
        """Start a ramp, or re-target the one that is running.

        A change that arrives while a ramp runs does not start a new ramp
        from the value the parameter had when the first one started: it
        starts from the value the parameter has reached, so the value never
        jumps.
        """
        total = max(1, int(round(float(ramp_seconds) * self.sample_rate)))
        ramps = self._ramps.setdefault(index, {})
        ramps[name] = _Ramp(start, target, total)

    def _cancel_ramp(self, index: int, name: str) -> None:
        """Stop the ramp of one parameter: the value is there at once."""
        ramps = self._ramps.get(index)
        if ramps is None:
            return
        ramps.pop(name, None)
        if not ramps:
            self._ramps.pop(index, None)

    def _ramp_step(self) -> int:
        """The number of samples a block is cut into while a ramp runs."""
        step = None
        for ramps in self._ramps.values():
            for ramp in ramps.values():
                if step is None or ramp.step < step:
                    step = ramp.step
        return max(1, int(step) if step is not None else 1)

    def _apply_ramps(self, samples: int) -> None:
        """Move every running ramp by ``samples`` samples.

        The value the parameters have reached is written into the modules,
        so the piece of the block that follows is processed with it.
        """
        for index in list(self._ramps.keys()):
            if index >= len(self._chain):
                self._ramps.pop(index, None)
                continue
            module = self._chain[index].module
            ramps = self._ramps[index]
            for name in list(ramps.keys()):
                ramp = ramps[name]
                elapsed = ramp.total - ramp.remaining
                value = ramp.start + (ramp.target - ramp.start) * (elapsed / float(ramp.total))
                module.params.set(name, value)
                ramp.current = value
                ramp.remaining -= int(samples)
                if ramp.remaining <= 0:
                    module.params.set(name, ramp.target)
                    ramp.current = ramp.target
                    ramps.pop(name, None)
            if not ramps:
                self._ramps.pop(index, None)

    def _restore_ramp_targets(self) -> None:
        """Put the values the parameters are going to have back into them.

        The value a parameter holds is the value it is going to have, so a
        reader -- the host, the writer of the state -- sees the target and
        not the value the ramp is passing through.
        """
        for index, ramps in self._ramps.items():
            if index >= len(self._chain):
                continue
            module = self._chain[index].module
            for name, ramp in ramps.items():
                module.params.set(name, ramp.target)

    def set_bypass(self, handle_or_index, bypass: bool) -> None:
        self._step(handle_or_index).bypass = bool(bypass)

    def is_bypassed(self, handle_or_index) -> bool:
        return self._step(handle_or_index).bypass

    # -- audio ----------------------------------------------------------------
    def _stages(self) -> list[tuple[object, int]]:
        """The steps of the chain, with the rate each of them hands over.

        The rate of a step is the rate of its output.  A step that does
        not change the rate hands its output over at the rate of the step
        before it, so the chain's rate only changes where a module says
        it does.
        """
        stages: list[tuple[object, int]] = []
        rate = self.sample_rate
        for step in self._chain:
            if step.bypass:
                continue
            declared = module_output_rate(step.module)
            if declared is not None:
                rate = declared
            stages.append((step.module, rate))
        return stages

    @property
    def output_sample_rate(self) -> int:
        """The rate the chain's output runs at.

        A chain that changes the rate (the resampler) hands its output
        over at another rate than the engine's own.  The engine walks the
        chain and knows the rate of every stage, so it knows the rate of
        its output.

        The number is the rate of the samples :meth:`process` returns when
        it is not asked for another rate.
        """
        stages = self._stages()
        return stages[-1][1] if stages else self.sample_rate

    def _converter_for(self, rate: int):
        """The resampler that brings the chain's rate back to the engine's.

        ``None`` when the chain works at the engine's rate already: there
        is nothing to convert then.
        """
        if rate == self.sample_rate:
            return None

        key = (int(rate), int(self.sample_rate))
        if self._converter is None or self._converter_key != key:
            from .modules.resample import SincResampler

            self._converter = SincResampler(
                self.sample_rate / float(rate),
                self.sample_rate,
            )
            self._converter_key = key
            # the samples of the old conversion do not belong to the new one
            self._pending = None
        return self._converter

    def _convert_back(self, buf: np.ndarray, wanted: int) -> np.ndarray:
        """The chain's output, brought back to the engine's own rate.

        The number of samples that comes back is the number that went in:
        the block the caller gets has the duration of the block it gave.
        The conversion does not fall on the block boundaries -- the read
        positions of the resampler do not divide the blocks evenly -- so
        the samples that do not fit are kept and the next block starts
        with them.
        """
        converter = self._converter_for(self.output_sample_rate)
        if converter is None:
            return buf

        produced = converter.process(buf)

        if self._pending is not None and self._pending.shape[0] > 0:
            if self._pending.shape[1] != produced.shape[1]:
                self._pending = None
            else:
                produced = np.concatenate((self._pending, produced), axis=0)

        if produced.shape[0] > wanted:
            self._pending = produced[wanted:]
            produced = produced[:wanted]
        else:
            self._pending = None
            if produced.shape[0] < wanted:
                pad = np.zeros((wanted - produced.shape[0], produced.shape[1]),
                               dtype=produced.dtype)
                produced = np.concatenate((produced, pad), axis=0)

        return produced

    def _converter_delay(self, rate: int) -> int:
        """The look ahead the conversion back costs, in engine samples.

        The figure is the one the resampler itself declares: the kernel
        of the interpolation reaches ``half_width`` samples to the right
        of the read position, so the output is held back by that much.
        """
        if rate == self.sample_rate:
            return 0
        converter = self._converter_for(rate)
        return int(converter.delay) if converter is not None else 0

    def process(self, x: np.ndarray, output_rate: int | None = None) -> np.ndarray:
        """Run the chain over a block of audio. Shape (n, channels).

        The block that comes back is the chain's output.  A chain that
        changes the rate hands it over at another rate than the engine's
        own, and the number of samples follows the rate: the samples of
        a chain that changes the rate belong to that rate.

        ``output_rate`` asks for the result at another rate.  A caller
        that cannot change its rate -- a plug-in in a host with a fixed
        bus -- asks for the rate it works at, and the chain's output is
        converted back to it, so that the block that comes back has the
        number of samples the caller asked for.  The conversion is the
        core's own resampler, and its look ahead is part of the latency
        of the engine.

        The block is **clipped** at the limit the chain declares: the
        digital ceiling, multiplied by the gain the chain's own level
        controls declare.  The clip is a per sample operation, so it does
        not move the level of anything that was inside the limit, and it
        does not depend on the size of the block.  See
        :mod:`aurcore.ceiling` for the rule.
        """
        buf = np.asarray(x, dtype=np.float64)
        if buf.ndim == 1:
            buf = buf[:, None]
        wanted = int(buf.shape[0])
        # The chain has played from now on: a change that arrives after
        # this is a change the user hears, and the core's own rule gives
        # it the ramp.
        self._played = True
        # The mode of the chain is given to every module before the chain
        # runs.  The cost of that is one string comparison per module: the
        # attribute is only written when the mode has changed, so a chain
        # that stays in its mode pays nothing but the comparison.
        mode = self._processing_mode
        if self._ramps:
            buf = self._process_with_ramps(buf, mode)
        else:
            buf = self._run_chain(buf, mode)

        if output_rate is not None and int(output_rate) != self.output_sample_rate:
            if buf.shape[0] > 0:
                buf = self._convert_back(buf, wanted)

        return self._hold_the_ceiling(buf)

    def _hold_the_ceiling(self, buf: np.ndarray) -> np.ndarray:
        """Clip the chain's output at the limit the chain declares.

        The limit is the digital ceiling multiplied by the gain the chain's
        own level controls declare, so a chain of repair modules is held at
        the digital ceiling and a chain that carries the user's own gain is
        held at the level that gain asks for.  See :mod:`aurcore.ceiling`.

        The clip is a per sample operation: a sample above the limit
        becomes the limit and every other sample is left exactly as it was.
        The level of nothing else moves, and the answer does not depend on
        the size of the block the host chopped the stream into.

        The common case costs one measurement of the peak and nothing else:
        a block that is inside the digital ceiling is inside the limit of
        every chain, because the limit is the ceiling times a gain that is
        never below one.
        """
        if buf is None or buf.size == 0:
            return buf
        peak = peak_of(buf)
        if peak <= CEILING:
            return buf
        limit = chain_limit(step.module for step in self._chain)
        if peak <= limit:
            return buf
        return clip_to_ceiling(buf, limit)

    def _run_chain(self, buf: np.ndarray, mode: str) -> np.ndarray:
        """Run the chain of modules over one block of audio."""
        for step in self._chain:
            module = step.module
            if module._processing_mode != mode:
                module._processing_mode = mode
            if step.bypass:
                continue
            out = module.process(buf)
            if out is not None:
                buf = np.asarray(out, dtype=np.float64)
                if buf.shape[1] != self.channels:
                    buf = _fix_channels(buf, self.channels)
        return buf

    def _may_cut(self) -> bool:
        """True when the chain may be given its block in pieces.

        A module that makes the same samples of a block whether it is given
        in one piece or in four can be given a block in pieces: the ramp
        needs the block to be cut finely enough to be smooth.  A module
        that does the analysis of the whole block at once cannot: its
        window does not fit into a piece, and the edge of the piece shows in
        the output as a step of its own.
        """
        for step in self._chain:
            if step.bypass:
                continue
            module = step.module
            if getattr(module, "block_agnostic", True) is False:
                return False
            if getattr(module, "code", "") in CUT_SENSITIVE_MODULES:
                return False
        return True

    def _process_with_ramps(self, buf: np.ndarray, mode: str) -> np.ndarray:
        """Run the chain over a block while a parameter is on its way.

        The block is cut into pieces of at most one step, and the value
        every moving parameter has reached is written into its module before
        each piece.  The pieces add up to the block: a module does not care
        how the block was cut, so the output of a cut block is the output of
        the whole one.

        A chain that changes the rate cannot be cut: the samples of a piece
        belong to another rate, so the ramp moves over the whole block.  The
        same goes for a chain with a module that minds the cut.
        """
        total = int(buf.shape[0])

        if total <= 0 or self.output_sample_rate != self.sample_rate or not self._may_cut():
            self._apply_ramps(total)
            out = self._run_chain(buf, mode)
            self._restore_ramp_targets()
            return out

        step = self._ramp_step()
        pieces = []
        position = 0
        while position < total:
            length = min(step, total - position)
            self._apply_ramps(length)
            pieces.append(self._run_chain(buf[position:position + length], mode))
            self._restore_ramp_targets()
            position += length

        if len(pieces) == 1:
            return pieces[0]
        return np.concatenate(pieces, axis=0)

    def learn(self, handle_or_index, x: np.ndarray) -> str:
        step = self._step(handle_or_index)
        if not getattr(type(step.module), "supports_learn", False):
            raise ValueError(f"{step.module.display_name} has no Learn action")
        step.module.learn(np.asarray(x, dtype=np.float64))
        return f"{step.module.display_name}: profile captured"

    def reset(self) -> None:
        for step in self._chain:
            step.module.reset()
        if self._converter is not None:
            self._converter.reset()
        self._pending = None
        # a reset puts the modules back to their defaults, so a value that
        # was on its way is not on its way any more
        self._ramps.clear()

    @property
    def latency_samples(self) -> int:
        """The latency of the chain, in the samples of the engine's rate.

        A module declares its latency in the samples of its own output.
        When the chain changes the rate, the stages after the change work
        at another rate than the ones before it, so the figures are
        brought into one rate before they are added up: a delay of 45
        samples at 44.1 kHz is 49 samples at 48 kHz, and the host, which
        works at 48 kHz, has to be told 49.

        The look ahead of the conversion back is part of it: when the
        chain changes the rate and the caller asks for the engine's rate,
        the signal goes through the resampler twice, and the second time
        costs its own look ahead.
        """
        stages = self._stages()
        if not stages:
            return 0

        engine_rate = float(self.sample_rate)
        total = 0.0
        for module, rate in reversed(stages):
            total += float(module.latency_samples) * (engine_rate / float(rate))

        latency = int(round(total))
        latency += self._converter_delay(stages[-1][1])
        return max(0, latency)

    @property
    def tail_samples(self) -> int:
        """The tail of the chain, in samples.

        The tail of a chain is the LONGEST tail of its modules, not the sum
        of them.  A tail says how long a module keeps ringing after the
        signal it works on has stopped, and the modules of a chain are
        clocked together, block by block, so a chain of a module that rings
        for 600 samples and one that rings for 1200 samples is described by
        1200 samples and not by 1800.

        That is also what a host needs.  A host that chains two plug-ins
        asks each of them for its own tail and waits for the longer of the
        two, because it runs them at the same time; a sum would hold a
        finished session open for as long as the chain is long.

        The price of the rule, stated plainly: in a chain of two *series*
        stages the second stage's input stops only when the first stage's
        output stops, so the last sound of such a chain arrives later than
        the rule says.  The tail of a chain is a duration for the host,
        not a prediction of the last sample of a series graph; the latency
        of the chain is reported separately, and the two together give the
        host the time it has to keep the chain running.

        A module that never stops ringing makes the tail of the chain
        infinite as well: :data:`aurcore.params.TAIL_INFINITE`.

        The figure is in the samples of the engine's rate, like the
        latency: a tail of 600 samples at 44.1 kHz is 653 samples at
        48 kHz, and the host, which works at 48 kHz, waits for 653.
        """
        stages = self._stages()
        if not stages:
            return 0

        engine_rate = float(self.sample_rate)
        tails = []
        for module, rate in stages:
            tail = module_tail(module)
            if tail == TAIL_INFINITE:
                return TAIL_INFINITE
            tails.append(int(round(tail * (engine_rate / float(rate)))))
        return max(tails) if tails else 0

    # -- serialisation --------------------------------------------------------
    def serialize(self) -> dict:
        return {
            "format": "auralis.chain",
            "version": 1,
            # the mode the chain was written in: a chain that was rendered
            # offline and is opened again in a real time host must not change
            # its meaning silently
            "processing_mode": self._processing_mode,
            "steps": [
                {
                    "code": s.module.code,
                    "enabled": not s.bypass,
                    "params": tv.wrap_all(s.module.params.to_dict()),
                    "state": tv.wrap(s.module.state_base64()),
                }
                for s in self._chain
            ],
        }

    def deserialize(self, state: dict) -> None:
        self._chain.clear()
        # The chain is replaced, so the conversion that brings a changed rate
        # back has to start again as well: its read position is counted from
        # the start of its own stream, and the stream of the chain is new.
        if self._converter is not None:
            self._converter.reset()
        self._pending = None
        # the values of the new chain are the ones the document carries, so
        # nothing is on its way any more
        self._ramps.clear()
        steps = (state or {}).get("steps", [])
        for step in steps:
            code = step.get("code")
            if not code or not registry.has(code):
                continue
            module = registry.create(code, self.sample_rate, self.channels)
            module.params.from_dict(tv.unwrap_all(step.get("params", {})))
            module.load_state_base64(tv.unwrap(step.get("state"), "") or "")
            self._chain.append(ChainStep(module, not step.get("enabled", True)))

        # The mode of the chain comes after the steps: the mode of the
        # document wins over the mode of a step, so a document is never half
        # in one mode and half in the other.
        self.apply_document_mode(state or {})

    def apply_document_mode(self, document: dict) -> str:
        """Put the chain into the mode a chain document carries.

        The mode of a chain is part of the chain document, so a session that
        was rendered offline and is opened again in a real time host does not
        change its meaning silently.  A document without a mode of its own
        falls back to the mode of the first step that carries one, which is
        what a document written before the mode was part of the chain needs.

        The mode is applied after the steps are built, so that every module
        of the chain is given it.
        """
        document = document or {}
        mode = document.get("processing_mode")
        if mode is None:
            mode = self._mode_of_steps(document.get("steps"))
        try:
            self._set_mode(self._check_mode(mode))
        except ValueError:
            self._set_mode(PROCESSING_MODE_REALTIME)
        return self._processing_mode

    @staticmethod
    def _mode_of_steps(steps: Any) -> str:
        """The mode the steps of a document carry, or the default one."""
        for step in steps or []:
            if not isinstance(step, dict):
                continue
            params = step.get("params") or {}
            if not isinstance(params, dict):
                continue
            value = tv.unwrap(params.get(PROCESSING_MODE_PARAMETER))
            if isinstance(value, str):
                mode = value.strip().lower()
                if mode in PROCESSING_MODES:
                    return mode
        return PROCESSING_MODE_REALTIME

    def chain_summary(self) -> list[dict]:
        out = []
        for i, s in enumerate(self._chain):
            out.append(
                {
                    "index": i,
                    "code": s.module.code,
                    "display_name": s.module.display_name,
                    "category": s.module.category,
                    "edition": s.module.edition,
                    "bypass": s.bypass,
                    "element": s.module.element,
                    # the mode the module works in: the chain hands every
                    # module the same mode before the chain runs
                    "processing_mode": s.module.processing_mode,
                    "params": {k: tv.wrap(v) for k, v in s.module.params.to_dict().items()},
                    # The level of what the module removed, in dBFS: the
                    # reading of the difference tap.  A module that does not
                    # remove anything reports nothing, and the interface
                    # shows no meter for it.
                    "removed_level_db": getattr(s.module, "removed_level_db", None),
                    "removed_peak_db": getattr(s.module, "removed_peak_db", None),
                }
            )
        return out


def _fix_channels(buf: np.ndarray, channels: int) -> np.ndarray:
    if buf.shape[1] == channels:
        return buf
    if buf.shape[1] == 1:
        return np.repeat(buf, channels, axis=1)
    if buf.shape[1] > channels:
        return buf[:, :channels]
    pad = np.zeros((buf.shape[0], channels - buf.shape[1]), dtype=buf.dtype)
    return np.concatenate([buf, pad], axis=1)


def create_engine(sample_rate: int = 48000, channels: int = 2) -> Engine:
    """Factory used by the server and the tests."""
    return Engine(sample_rate, channels)
