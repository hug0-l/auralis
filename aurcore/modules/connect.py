"""
aurcore.modules.connect — hand audio to the host application.

``Connect`` is a system module: it does not change the signal, it moves it.
The module passes the audio through untouched and offers it to the host
application so that it can be opened in the editor.

``Auto Send``
    Every processed block is offered to the host automatically.  With the
    switch off the host has to call :meth:`Connect.send` explicitly (for
    example when the user picks a region in the timeline).

``Keep Source``
    The transfer is flagged as a *copy*, so the host keeps the material in
    the source timeline next to the edited version.  With the switch off the
    transfer is flagged as a *move*.
"""
from __future__ import annotations

import time

import numpy as np

from ..params import ParamDesc, desc
from ..registry import register
from .base import Module

__all__ = ["Connect"]


@register
class Connect(Module):
    """Connect — hand the audio to the host application for editing.

    The module is a pass-through.  It records the audio that was offered to
    the host so that the server can present it as an editable transfer.
    """

    code = "AUR_CONNECT"
    display_name = "Connect"
    category = "System"
    edition = "standard"
    element = "Connect"
    analysis = False
    supports_learn = False
    ml = False

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._last_transfer: dict | None = None

    # -- parameter description ------------------------------------------------
    def describe(self) -> list[ParamDesc]:
        """Return the parameter list of the module."""
        return [
            desc(
                name="Auto Send",
                type="bool",
                default=False,
                minimum=0,
                maximum=1,
                group="Transfer",
                help="Send every processed block to the host application automatically.",
            ),
            desc(
                name="Keep Source",
                type="bool",
                default=True,
                minimum=0,
                maximum=1,
                group="Transfer",
                help="Keep the source material in the timeline next to the edit.",
            ),
        ]

    # -- transfer -------------------------------------------------------------
    @property
    def last_transfer(self) -> dict | None:
        """The most recent transfer, or ``None`` when nothing was sent yet."""
        return self._last_transfer

    @property
    def has_transfer(self) -> bool:
        """True when a transfer is available."""
        return self._last_transfer is not None

    def send(self, x: np.ndarray) -> np.ndarray:
        """Offer a block of audio to the host application.

        Parameters
        ----------
        x:
            The audio to transfer, shape ``(n, channels)``.

        Returns
        -------
        numpy.ndarray
            A copy of the audio that was transferred.
        """
        a = np.asarray(x, dtype=np.float64)
        if a.ndim == 1:
            a = a[:, None]
        keep_source = bool(self.params.get("Keep Source"))
        self._last_transfer = {
            "audio": a.copy(),
            "sample_rate": int(self.sample_rate),
            "channels": int(a.shape[1]),
            "frames": int(a.shape[0]),
            "duration": float(a.shape[0]) / float(self.sample_rate) if self.sample_rate else 0.0,
            "keep_source": keep_source,
            "mode": "copy" if keep_source else "move",
            "timestamp": time.time(),
        }
        return a.copy()

    def clear_transfer(self) -> None:
        """Forget the recorded transfer."""
        self._last_transfer = None

    def reset(self) -> None:
        """Forget the recorded transfer."""
        self.clear_transfer()

    # -- processing -----------------------------------------------------------
    def process(self, x: np.ndarray) -> np.ndarray:
        """Pass the audio through, offering it to the host when auto send is on."""
        a = np.asarray(x, dtype=np.float64)
        if a.ndim == 1:
            a = a[:, None]
        if a.shape[0] == 0:
            return a
        if self.params.get("Auto Send"):
            self.send(a)
        return a
