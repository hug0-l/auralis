"""
aurcore.modules.base — the module contract.

Every processing module in Auralis derives from :class:`Module`.  A module is
realtime-safe: ``process`` performs no I/O, no network, no unbounded work, and
never raises through the call boundary.

The two processing modes
------------------------
A module works in one of **two** configurations: the one that fits the
deadline of a real time host, and the best one it has.  The mode the module
works in is :attr:`Module.processing_mode`, and it is one of two strings:

``"realtime"``
    The module has to keep inside the budget of a real time block.  Every
    module of the chain is measured against it, and the gate that measures it
    is the one that says a module is too slow.
``"offline"``
    The module may take as long as it likes — there is no deadline — but it
    then **has** to declare the look ahead it costs, because the host has to
    be told how late the audio is.

The mode is not a musical setting: it changes the *cost* of the work and the
*latency* the work costs, never the meaning of the audio.  A module reads it
through :attr:`Module.processing_mode`; the engine of the chain owns it and
gives it to every module before the chain runs.

The level of the output
-----------------------
A module's own output may go over the digital ceiling: the rule of the level
is not inside the module.  It is applied at the **output** of the product —
the chain's output and the plug-in's output — and it is applied by clipping.
See :mod:`aurcore.ceiling` for the rule, for the limit it uses and for the
reason it clips instead of scaling.
"""
from __future__ import annotations

import base64
import json
from typing import Any

import numpy as np

from ..params import ParamDesc, ParamStore

#: The mode a module works in when the chain is played while the user waits
#: for it: every module has to keep inside the budget of a real time block.
PROCESSING_MODE_REALTIME = "realtime"

#: The mode a module works in when there is no deadline: the file render.
#: A module may take longer here, and it has to declare the look ahead it
#: costs.
PROCESSING_MODE_OFFLINE = "offline"

#: The two modes, and nothing else.
PROCESSING_MODES: tuple[str, ...] = (PROCESSING_MODE_REALTIME, PROCESSING_MODE_OFFLINE)

#: The name of the module parameter that carries the mode.
#:
#: The mode is a parameter of a module, so a host can set it the way it sets
#: every other parameter, and the state of the module carries it.  The engine
#: of the chain writes the mode of the chain into the parameter of every
#: module that has one, and reads the chain's mode back from the parameter
#: when a host sets it on a module.
PROCESSING_MODE_PARAMETER = "Processing Mode"


class Module:
    """Base class for all Auralis processing modules."""

    code: str = "AUR_MODULE"
    display_name: str = "Module"
    category: str = "Utility"
    edition: str = "standard"
    element: str = ""
    analysis: bool = False
    # NOTE: the capability flag is deliberately NOT called ``learn``:
    # ``learn`` is the method below, and a same-named class attribute
    # would shadow it and report every module as learnable.
    supports_learn: bool = False
    ml: bool = False

    #: True when the module makes a sound of its own — a generator.  A
    #: generator adds its own signal to the incoming audio, and the level of
    #: that signal is the user's own control, so the module is not held to
    #: the digital ceiling: the user asked for the tone, and the tone is at
    #: the level the user asked for.  The rule of the level is applied at the
    #: output of the product, and there it holds the line for every module
    #: alike.
    generates: bool = False

    #: The mode the module works in.  The engine of the chain sets it on every
    #: module of the chain before the chain runs, so a module reads it through
    #: :attr:`processing_mode` and never writes it.
    _processing_mode: str = PROCESSING_MODE_REALTIME

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.params = ParamStore(self.describe())

    # -- contract -------------------------------------------------------------
    def describe(self) -> list[ParamDesc]:
        """Return the module's full parameter list. Override in subclasses."""
        return []

    @property
    def processing_mode(self) -> str:
        """The mode this module works in: ``"realtime"`` or ``"offline"``.

        The property is read only on purpose: the engine of the chain owns the
        mode of the chain and hands it to every module, so two modules of the
        same chain cannot disagree about the mode they work in because one of
        them changed its mind.
        """
        return self._processing_mode

    def mode_latency(self, realtime: int, offline: int) -> int:
        """The look ahead of this module in the mode it is in.

        A module that uses a longer window in the offline mode delays more,
        and the host has to be told how much.  This is the one line that says
        it::

            @property
            def latency_samples(self) -> int:
                return self.mode_latency(realtime=1024, offline=8192)
        """
        return int(offline if self.processing_mode == PROCESSING_MODE_OFFLINE else realtime)

    def process(self, x: np.ndarray) -> np.ndarray:
        """Process a block of audio. ``x`` has shape (n_samples, n_channels)."""
        return x

    def reset(self) -> None:
        """Clear internal state (learned profiles, filters, envelopes)."""

    def learn(self, x: np.ndarray) -> None:
        """Capture a profile from ``x``. Only called when ``learn`` is True."""
        raise NotImplementedError(f"{self.code} does not support learning")

    def serialize(self) -> dict:
        """Return the module's non-parameter state as a JSON-safe dict."""
        return {}

    def deserialize(self, data: dict) -> None:
        """Restore state produced by :meth:`serialize`."""

    @property
    def latency_samples(self) -> int:
        return 0

    @property
    def tail_samples(self) -> int:
        """How long the module keeps ringing after the input stopped.

        The number is in samples and counts from the end of the module's own
        latency, so the time from the input stopping until the output is
        quiet is ``latency_samples + tail_samples``.

        A module that keeps making a sound of its own -- a generator, a bed
        of ambience -- never goes quiet and reports
        :data:`aurcore.params.TAIL_INFINITE`.
        """
        return 0

    @property
    def settling_frames(self) -> int:
        """How many frames of material the module needs before it is ready.

        A module that estimates over time has to hear the material before it
        can judge it, and a module that says so is not judged by the
        material it has not heard yet: the gate of the edges
        (``qa/check_module_edges.py``) measures the level of the output only
        from the settling time on, so that a module that is still settling is
        not held to the level of a material it has not heard yet.

        The number is in frames (samples) of material the module has been
        given, counted from the first sample it was given.  Zero means the
        module is ready at once, which is the answer of every module that
        does not estimate over time.
        """
        return 0

    # -- helpers --------------------------------------------------------------
    def _state_blob(self) -> bytes:
        return b""

    def _put_state_blob(self, data: bytes) -> None:
        return None

    def state_base64(self) -> str:
        blob = self._state_blob()
        return base64.b64encode(blob).decode("ascii") if blob else ""

    def load_state_base64(self, text: str) -> None:
        if not text:
            return
        try:
            self._put_state_blob(base64.b64decode(text))
        except Exception:
            pass

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "params": self.params.to_dict(),
            "state": self.state_base64(),
        }

    def from_dict(self, data: dict) -> None:
        self.params.from_dict(data.get("params", {}))
        self.load_state_base64(data.get("state", ""))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} {self.code} sr={self.sample_rate} ch={self.channels}>"


def as_float32(x: np.ndarray) -> np.ndarray:
    return np.asarray(x, dtype=np.float32)


def as_float64(x: np.ndarray) -> np.ndarray:
    return np.asarray(x, dtype=np.float64)
