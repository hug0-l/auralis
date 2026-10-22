"""
aurcore.ceiling — the digital ceiling of the family.

A file that is written in integers holds the values from −1 to 1: a sample
outside that range is **clipped**, and the clip is a distortion that nothing
downstream can undo.  A host that writes floats has no clip, but it writes a
file that is out of range all the same, and every consumer of that file has to
clip it.

So the family has one rule about the level, and this module is where the rule
lives.

The rule
--------
**Nothing the product hands out goes above the digital ceiling.**

Where the rule is applied
-------------------------
The rule is applied at the **output** of the product, and at the output only:

* the **chain's output** — :meth:`aurcore.api.Engine.process` clips the block
  the chain makes.  That block is the file the server writes, the block the
  preview shows and the block the plug-in hands the host.
* the **plug-in's output** — the adapter
  (``plugins/vst3native/AuralisVST3.cpp``) holds the line in the paths in
  which the core does not run.

A **module's** own output is not limited any more.  The rule used to sit in
the wrapper of :mod:`aurcore.modules.base`, and it was applied to the output
of every module of the family; it is at the boundary now.  Two things follow
from that, and both of them are the point of the move:

* the quality instrument measures the **module**, not a clipped signal, so
  the numbers of the quality report are the numbers of the module;
* a module's answer does not depend on what the chain did before it, so two
  renders of the same material that differ in their history come out at the
  same level.

Clipped, not scaled
-------------------
The rule is applied by **clipping**, and not by scaling.

A scale is a gain: it moves the level of **everything** in the block, and it
depends on the level of the block it is applied to.  A clip is a **per
sample** operation: a sample above the limit becomes the limit and every
other sample is left exactly as it was.  So a clip is block independent *by
construction* — the same sample of the same stream is clipped the same way
whether the host handed the chain the whole stream in one block or in a
thousand blocks of one sample — and it does not move the level of anything
that was inside the limit.

That is what the spectral editor needs: the material outside the region the
user drew has to come back **bit for bit**, and a scale that fits the block
would move it by a gain.

The limit at the boundary
-------------------------
The limit is the **digital ceiling**, multiplied by the **gain the chain
itself declares**:

    limit = CEILING * the declared gain of the chain

The declared gain of a chain is the sum, in decibels, of the gains that the
chain's own level controls declare: the gain module, the two output mixes of
the mixer, the output gain of the EQ and the gains of its bands, the boost of
the loudness optimiser, the makeup of the de-clipper, the three component
gains of the decomposition and the band mixes of the band restoration.  The
list is in :data:`LEVEL_CONTROLS`, and a module that is not in it has no
level control of its own.

A chain of repair modules has no level control, so its declared gain is one
and its limit is the digital ceiling: **1.0**.  A chain that carries the
user's own gain has a limit above it: a user who asks the gain module for
+6 dB gets a limit of 1.995, and the file carries the +6 dB — the quiet
passages are six decibels louder than they were — and the samples that go
over the limit are **clipped** at it.  They are not scaled back: the old rule
scaled the whole block, and the scaling took the user's own gain away again.

So the exemption of the family survives the move: the knob of the user is the
source of the level, and the limit of the boundary follows it.  The knob is
never clamped by the module — the knob is the source of the level, not a
hidden limiter inside the code.

The clipped case
----------------
The rule and the ground truth of a clipped case are about the same level, so
they have to agree.  A hard clipper is memoryless: the samples it flattens
carry no information at all about the height of the waveform that was there,
so **the level of the programme a clipper destroyed cannot be read back from
the material**.  The truth of such a case is therefore stated at the
**digital ceiling**, and the module that repairs the case hands the
programme back at that same level: the height the clipping chain took away,
brought back to the level the format can hold.  A truth above the ceiling
would be a material no file could hold and a level no module may return, and
the yard would measure the module against something the product can never
deliver — the module would be charged for a level it is not allowed to
reach.  The user still decides how hot the restored peaks go: the *Makeup
Gain* of the de-clipper is a level control like any other, and the limit of
the chain follows it.
"""
from __future__ import annotations

import numpy as np

__all__ = [
    "CEILING",
    "LEVEL_CONTROLS",
    "TOLERANCE",
    "chain_limit",
    "ceiling_limit",
    "clip_to_ceiling",
    "declared_gain",
    "gain_of",
    "peak_of",
]

#: The digital ceiling: the largest value a sample may have.  A 16 bit file
#: holds the samples from −1 to 1 and nothing else.
CEILING = 1.0

#: How far above the ceiling a sample may be and still count as inside.
#:
#: The tolerance is there for the arithmetic, not for the signal: a double
#: precision filter that is transparent to within 1e-15 still hands back
#: 1.0000000000000002 for a full scale input, and a gate that called that a
#: defect would fail on every module that does nothing at all.  1e-6 is
#: −120 dBFS: far below anything that can be heard or written into a 16 bit
#: file (the last bit of a 16 bit file is 1/32768 = 3.05e-5), and four
#: orders of magnitude above the rounding noise of the arithmetic.
TOLERANCE = 1e-6

#: The modules that carry the user's own level control: the parameters that
#: decide how loud the module's output is.
#:
#: The value of such a parameter is the gain the module is allowed to apply,
#: and it is read from the module's own parameter table, so the range the
#: parameter declares is the range the module may reach.  A module that is
#: not in this table has no level control: it may only attenuate, and the
#: ceiling is hard for it.
#:
#: The table is also what decides the **limit of a chain**: the declared
#: gain of a chain is the sum, in decibels, of the gains that the level
#: controls of its modules declare, and the limit of the chain is the
#: digital ceiling multiplied by it.  A chain of repair modules declares
#: nothing, so its limit is the digital ceiling.
LEVEL_CONTROLS: dict[str, tuple[str, ...]] = {
    # the gain control itself
    "AUR_GAIN": ("Gain",),
    # the two output mixes of the mixer
    "AUR_MIXING": ("Left Output Mix", "Right Output Mix"),
    # the output gain of the EQ and the gains of its four bands
    "AUR_EQ": (
        "Output Gain",
        "Band 1 Gain",
        "Band 2 Gain",
        "Band 3 Gain",
        "Band 4 Gain",
    ),
    # the boost of the loudness optimiser
    "AUR_LOUDNESSOPT": ("Boost",),
    # the makeup of the de-clipper
    "AUR_DECLIP": ("Makeup Gain",),
    # the three component gains of the decomposition
    "AUR_DECONSTRUCT": ("Tonal Gain", "Noisy Gain", "Transient Gain"),
    # the band mixes of the band restoration
    "AUR_BANDRESTORATION": (
        "Band 1 Mix",
        "Band 2 Mix",
        "Band 3 Mix",
        "Band 4 Mix",
    ),
}


def peak_of(x: np.ndarray) -> float:
    """Return the largest absolute value of ``x``.

    The peak is read with ``max`` and ``min`` instead of ``abs`` so that the
    measurement does not allocate: the helper runs in the audio path, at the
    output of the chain, and an allocation there glitters in a host.

    Parameters
    ----------
    x:
        The block to measure.  It may be any shape.

    Returns
    -------
    float
        The largest absolute value, or 0.0 for an empty block.
    """
    if x is None:
        return 0.0
    arr = np.asarray(x)
    if arr.size == 0:
        return 0.0
    return max(float(arr.max()), -float(arr.min()))


def gain_of(module, name: str) -> float:
    """Return the linear gain a level parameter of ``module`` asks for.

    The unit comes from the module's own parameter table: a parameter in dB
    (or dBFS) is a gain of ``10 ** (value / 20)``, a parameter in percent is
    a gain of ``value / 100``, and a parameter in any other unit says
    nothing about the level and counts as no gain at all.

    Parameters
    ----------
    module:
        The module the parameter belongs to.
    name:
        The name of the parameter.

    Returns
    -------
    float
        The gain the parameter asks for, never below zero.
    """
    try:
        value = float(module.params.get(name))
    except Exception:  # noqa: BLE001 - a module without the parameter
        return 1.0
    unit = ""
    try:
        for desc in module.params.descs():
            if desc.name == name:
                unit = str(desc.unit or "")
                break
    except Exception:  # noqa: BLE001 - a module without a parameter table
        unit = ""
    if unit in ("dB", "dBFS"):
        return 10.0 ** (value / 20.0)
    if unit == "%":
        return max(0.0, value / 100.0)
    return 1.0


def declared_gain(module) -> float:
    """Return the gain the level control of ``module`` asks for.

    Several parameters of a module can be level controls (the four bands of
    the EQ, the two mixes of the mixer).  Their gains add up, because each of
    them can raise the level on its own; the ones that lower the level are
    left out, because a module that lowers the level cannot go over the
    ceiling through them.

    A module that has no level control declares a gain of one: it may only
    attenuate, and the ceiling is hard for it.

    Parameters
    ----------
    module:
        The module the gain is read from.

    Returns
    -------
    float
        The linear gain the module's own level control declares.
    """
    names = LEVEL_CONTROLS.get(getattr(module, "code", ""), ())
    total = 0.0
    for name in names:
        gain = gain_of(module, name)
        if gain > 1.0:
            total += 20.0 * np.log10(gain)
    return 10.0 ** (total / 20.0)


def ceiling_limit(module, inp: np.ndarray | None = None) -> float:
    """Return the largest peak the chain of one module may hand back.

    The limit is the digital ceiling, multiplied by the gain the module's own
    level control declares.  It is a property of the chain and not of the
    block: a limit that followed the level of the block would make the
    answer depend on the size of the block the host chopped the stream into,
    and the family holds every chain to the opposite of that.

    Parameters
    ----------
    module:
        The module the limit is for.
    inp:
        The block the module was given.  It is accepted and ignored: the
        level of the block is not part of the limit, because a block is what
        the host chops the stream into.

    Returns
    -------
    float
        The limit, in signal units.
    """
    return CEILING * declared_gain(module)


def chain_limit(modules) -> float:
    """Return the largest peak a chain may hand back.

    The limit is the digital ceiling, multiplied by the gain the chain itself
    declares: the sum, in decibels, of the gains that the level controls of
    the chain's own modules declare.  A chain of repair modules declares
    nothing, so its limit is the digital ceiling; a chain that carries the
    user's own gain declares it, and the limit follows the knob.

    Parameters
    ----------
    modules:
        The modules of the chain, in signal order.

    Returns
    -------
    float
        The limit, in signal units.
    """
    total = 0.0
    for module in modules:
        gain = declared_gain(module)
        if gain > 1.0:
            total += 20.0 * np.log10(gain)
    return CEILING * (10.0 ** (total / 20.0))


def clip_to_ceiling(out: np.ndarray, limit: float) -> np.ndarray:
    """Clip ``out`` at ``limit`` and hand it back.

    The clip is a **per sample** operation: a sample above the limit becomes
    the limit and every other sample is left exactly as it was.  It is
    therefore block independent by construction, and it does not move the
    level of anything that was inside the limit.

    The signal is **clipped, never scaled**: a scale is a gain, and a gain
    moves the level of the whole block — of the material the user did not
    touch as much as of the material they did.

    Parameters
    ----------
    out:
        The block the chain produced.
    limit:
        The largest peak the block may have.

    Returns
    -------
    numpy.ndarray
        The block, clipped where it went over the limit.  The block is
        handed back untouched when it is already inside the limit.
    """
    if out is None:
        return out
    arr = np.asarray(out)
    if arr.size == 0:
        return arr
    if peak_of(arr) <= limit:
        return arr
    return np.clip(arr, -limit, limit)
