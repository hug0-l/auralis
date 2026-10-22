"""
aurcore.dsp.removed -- the difference tap: what a module took away.

A restoration module answers one half of its own question with its output:
*what is left*.  It does not answer the other half: *what did you take away*.
A user who cannot hear the removed part cannot tell a clean repair from a
destructive one, and a repair that hides its damage cannot be trusted.

This module is the shared primitive for that second output.

The three pieces
----------------
* :func:`removed_signal` is the difference itself: the dry signal minus the
  signal the module returned.
* :func:`mix_removed` blends the difference back into the output at the
  amount the user sets.  At 0 % the output is the processed signal, bit for
  bit; at 100 % it is the difference.
* :func:`removed_level_db` measures the difference, so that an interface can
  show a meter.

:class:`RemovedTap` holds the state a module needs to use all three: the
delay line that keeps the dry signal aligned with a delayed output, the level
that was measured last and the peak that goes with it.

The mix
-------
``Removed Mix`` is a crossfade between two signals, not a gain:

    output = (1 - a) * processed + a * removed

with ``a`` the amount in percent.  The two signals add up to the input, so
the middle of the travel is the half gain original, and the top of the
travel is the removed signal on its own.  The ends are exact: at 0 % the
output *is* the processed signal, sample for sample, and at 100 % it *is*
the removed signal.

The alignment
-------------
A module whose analysis looks ahead returns a signal that is late against
the input.  The difference of a late output and a dry input that is on time
is not the difference at all: it is the dry signal minus the processed
signal, taken half a window apart.  The tap therefore keeps a line of the
dry signal and hands it back delayed by the latency the module declares, so
that the difference is always taken between two signals that stand at the
same moment.

Conventions
-----------
* the audio is ``float64`` in ``[-1, 1]``; ``1.0`` is ``0 dBFS``;
* the level of the difference is the amplitude convention (``20*log10``) of
  the root mean square over the block, in dBFS;
* the peak is the amplitude convention of the largest sample of the block;
* the mix amount is a percentage: ``0`` is the processed signal and ``100``
  is the difference.
"""
from __future__ import annotations

import math

import numpy as np

__all__ = [
    "REMOVED_FLOOR_DB",
    "REMOVED_MIX_DEFAULT",
    "REMOVED_MIX_GROUP",
    "REMOVED_MIX_MAXIMUM",
    "REMOVED_MIX_MINIMUM",
    "REMOVED_MIX_NAME",
    "REMOVED_MIX_UNIT",
    "RemovedTap",
    "mix_removed",
    "removed_level_db",
    "removed_mix_fields",
    "removed_peak_db",
    "removed_signal",
]


#: The name the parameter carries in every module that removes something.
REMOVED_MIX_NAME = "Removed Mix"

#: The unit of the mix amount.  It is a percentage and not a decibel offset:
#: the control is a position between two signals (the processed one and the
#: removed one) and not a gain, and the removed signal is not a gain.
REMOVED_MIX_UNIT = "%"

#: The bottom of the travel: the processed signal, untouched.
REMOVED_MIX_MINIMUM = 0.0

#: The top of the travel: the removed signal on its own.
REMOVED_MIX_MAXIMUM = 100.0

#: Where the control rests.  Nothing is mixed back, so the output of a
#: module that carries the parameter is the output it had before the
#: parameter existed.
REMOVED_MIX_DEFAULT = 0.0

#: The group the parameter is shown in.
REMOVED_MIX_GROUP = "Mix"

#: The floor of the level readings, in dBFS.  A difference of nothing has no
#: level, and the floor is where the meter rests.
REMOVED_FLOOR_DB = -120.0


# ---------------------------------------------------------------------------
# the difference
# ---------------------------------------------------------------------------
def removed_signal(dry: np.ndarray, wet: np.ndarray) -> np.ndarray:
    """Return what the module removed: the dry signal minus the wet one.

    Parameters
    ----------
    dry:
        The signal that went into the module.
    wet:
        The signal the module returned.

    Returns
    -------
    numpy.ndarray
        ``dry - wet``, with the shape of the two signals.

    Raises
    ------
    ValueError
        When the two signals do not have the same shape.  A difference of
        two signals of different shapes is not a difference.
    """
    d = np.asarray(dry, dtype=np.float64)
    w = np.asarray(wet, dtype=np.float64)
    if d.shape != w.shape:
        raise ValueError(
            f"the dry signal has the shape {d.shape} and the processed one "
            f"{w.shape}; a difference needs two signals of the same shape"
        )
    return d - w


def removed_level_db(removed: np.ndarray, floor_db: float = REMOVED_FLOOR_DB) -> float:
    """Return the level of the removed signal in dBFS.

    The level is the root mean square over the whole block, with the
    amplitude convention of the project (``20*log10``): a full scale sine
    reads ``0 dBFS``.

    Parameters
    ----------
    removed:
        The difference, as :func:`removed_signal` returns it.
    floor_db:
        The level that is reported when the difference is silent, or holds
        a value that is not a number.

    Returns
    -------
    float
        The level in dBFS.
    """
    arr = np.asarray(removed, dtype=np.float64)
    if arr.size == 0:
        return float(floor_db)
    # ``vdot`` flattens the two operands itself, so the block is measured
    # over every sample of every channel in one pass.
    total = float(np.vdot(arr, arr))
    if not math.isfinite(total) or total <= 0.0:
        return float(floor_db)
    return float(max(floor_db, 20.0 * math.log10(math.sqrt(total / arr.size))))


def removed_peak_db(removed: np.ndarray, floor_db: float = REMOVED_FLOOR_DB) -> float:
    """Return the peak level of the removed signal in dBFS.

    The peak is the largest sample of the block, with the amplitude
    convention of the project.

    Parameters
    ----------
    removed:
        The difference, as :func:`removed_signal` returns it.
    floor_db:
        The level that is reported when the difference is silent, or holds
        a value that is not a number.

    Returns
    -------
    float
        The peak in dBFS.
    """
    arr = np.asarray(removed, dtype=np.float64)
    if arr.size == 0:
        return float(floor_db)
    # the largest sample and the smallest one: the peak is the larger of the
    # two, and the block is not copied to find it
    peak = max(float(arr.max()), -float(arr.min()))
    if not math.isfinite(peak) or peak <= 0.0:
        return float(floor_db)
    return float(max(floor_db, 20.0 * math.log10(peak)))


# ---------------------------------------------------------------------------
# the mix
# ---------------------------------------------------------------------------
def mix_removed(
    dry: np.ndarray,
    wet: np.ndarray,
    percent: float,
    removed: np.ndarray | None = None,
) -> np.ndarray:
    """Blend the removed signal into the processed one.

    The mix is a crossfade between the two signals:

    * at 0 % the output **is** the processed signal, sample for sample;
    * at 100 % the output **is** the removed signal, sample for sample;
    * in between the output is the linear blend of the two.

    Parameters
    ----------
    dry:
        The signal that went into the module, aligned with the output.
    wet:
        The signal the module returned.
    percent:
        The amount, in percent.  Values below 0 are read as 0 and values
        above 100 as 100.
    removed:
        The difference, when the caller has already computed it.  It is
        computed from ``dry`` and ``wet`` when it is not given.

    Returns
    -------
    numpy.ndarray
        The output of the module.
    """
    amount = float(percent) / 100.0
    wet_arr = np.asarray(wet, dtype=np.float64)
    if amount <= 0.0:
        # the processed signal, bit for bit: nothing is computed at all
        return wet_arr
    difference = (
        removed_signal(dry, wet_arr) if removed is None
        else np.asarray(removed, dtype=np.float64)
    )
    if amount >= 1.0:
        # the removed signal, bit for bit
        return difference
    return (1.0 - amount) * wet_arr + amount * difference


def removed_mix_fields(subject: str) -> dict:
    """Return the fields of the parameter every removal module carries.

    The name, the unit, the range and the default are shared, so that the
    control means the same thing in every module of the catalogue; only the
    sentence of help names the thing the module removes.

    Parameters
    ----------
    subject:
        The thing the module removes, in the words the module uses for it:
        ``"hum"``, ``"reverberant field"``, ``"rumble"``, ``"breathing"``,
        ``"clicks"``, ``"noise"``.

    Returns
    -------
    dict
        The keyword arguments of :class:`aurcore.params.ParamDesc`.
    """
    return {
        "name": REMOVED_MIX_NAME,
        "type": "float",
        "default": REMOVED_MIX_DEFAULT,
        "minimum": REMOVED_MIX_MINIMUM,
        "maximum": REMOVED_MIX_MAXIMUM,
        "unit": REMOVED_MIX_UNIT,
        "group": REMOVED_MIX_GROUP,
        "help": (
            f"Mixes the removed {subject} back into the output: 0 % leaves the "
            f"processed signal as it is, 100 % plays the removed {subject} alone."
        ),
    }


# ---------------------------------------------------------------------------
# the tap
# ---------------------------------------------------------------------------
class RemovedTap:
    """The state a module needs to show what it removed.

    The tap keeps the dry signal in a line, hands it back delayed by the
    latency the module declares, takes the difference against the processed
    signal and measures it.

    Attributes
    ----------
    level_db:
        The level of the difference of the last block, in dBFS.
    peak_db:
        The peak of the difference of the last block, in dBFS.
    """

    def __init__(self, channels: int = 2):
        self.channels = max(1, int(channels))
        self.level_db = REMOVED_FLOOR_DB
        self.peak_db = REMOVED_FLOOR_DB
        self._line = np.zeros((0, self.channels), dtype=np.float64)
        self._latency = 0

    # -- state ----------------------------------------------------------------
    def reset(self) -> None:
        """Forget the line and the levels."""
        self.level_db = REMOVED_FLOOR_DB
        self.peak_db = REMOVED_FLOOR_DB
        self._line = np.zeros((0, self.channels), dtype=np.float64)
        self._latency = 0

    # -- the line -------------------------------------------------------------
    def align(self, dry: np.ndarray, latency: int = 0) -> np.ndarray:
        """Return the dry signal, delayed so that it lines up with the output.

        Parameters
        ----------
        dry:
            The block that went into the module.
        latency:
            The delay the module adds to its output, in samples.

        Returns
        -------
        numpy.ndarray
            The dry signal, late by ``latency`` samples.  It has the shape
            and the length of ``dry``.

        Notes
        -----
        A module that changes its delay — a filter topology that changes, a
        setting that switches the algorithm — starts its line again, primed
        with silence.  The difference is exact from the moment the new delay
        is in force.
        """
        arr = np.asarray(dry, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[:, None]
        delay = max(0, int(latency))
        if delay != self._latency or self._line.shape[1] != arr.shape[1]:
            # the delay of the module changed (or the channel count did):
            # the line starts again, so that it holds exactly the delay the
            # module declares
            self._line = np.zeros((delay, arr.shape[1]), dtype=np.float64)
            self._latency = delay
        if delay == 0:
            if self._line.shape != (0, arr.shape[1]):
                self._line = np.zeros((0, arr.shape[1]), dtype=np.float64)
            return arr
        self._line = np.concatenate((self._line, arr), axis=0)
        take = min(int(arr.shape[0]), int(self._line.shape[0]))
        out = self._line[:take]
        self._line = self._line[take:]
        if take < int(arr.shape[0]):
            pad = np.zeros((int(arr.shape[0]) - take, arr.shape[1]), dtype=np.float64)
            out = np.concatenate((out, pad), axis=0)
        return out

    # -- the measurement ------------------------------------------------------
    def measure(self, removed: np.ndarray) -> float:
        """Measure the difference and remember the reading.

        Parameters
        ----------
        removed:
            The difference, as :func:`removed_signal` returns it.

        Returns
        -------
        float
            The level of the difference in dBFS.
        """
        self.level_db = removed_level_db(removed)
        self.peak_db = removed_peak_db(removed)
        return self.level_db

    # -- the whole thing ------------------------------------------------------
    def apply(
        self,
        dry: np.ndarray,
        wet: np.ndarray,
        percent: float,
        latency: int = 0,
        removed: np.ndarray | None = None,
    ) -> np.ndarray:
        """Return the output of the module.

        The dry signal is aligned with the output, the difference against the
        processed signal is measured, and the difference is blended back in
        at the amount the user set.

        The call never raises: a dry signal that does not have the shape of
        the processed one leaves the output alone, because a broken module
        must not take the stream down with it.

        Parameters
        ----------
        dry:
            The block that went into the module.
        wet:
            The signal the module returned.
        percent:
            The amount of the removed signal to mix back in, in percent.
        latency:
            The delay the module adds to its output, in samples.
        removed:
            The difference, when the caller has already computed it.

        Returns
        -------
        numpy.ndarray
            The output of the module.
        """
        wet_arr = np.asarray(wet, dtype=np.float64)
        aligned = self.align(dry, latency)
        if aligned.shape != wet_arr.shape:
            # the two signals do not stand at the same moment: the output is
            # left alone and the meter rests
            self.level_db = REMOVED_FLOOR_DB
            self.peak_db = REMOVED_FLOOR_DB
            return wet_arr
        difference = (
            removed_signal(aligned, wet_arr) if removed is None
            else np.asarray(removed, dtype=np.float64)
        )
        self.measure(difference)
        return mix_removed(aligned, wet_arr, percent, removed=difference)
