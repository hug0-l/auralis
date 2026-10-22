"""
qa.edge_rules -- the rules of the edges that are not gates yet.

The edge contract (``docs/EDGE_CONTRACT.md``) names seven rules.  Five of them
are gates of the tree; two of them -- **the silence** and **the length of the
material** -- were written down and measured, and nothing held them.  This
file carries those two, in the shape the other rule files of the project use:
every rule has a number, a unit and the way the number is measured, and the
reason the number is what it is.

    ==========================  ==========  =================================
    rule                        number       what it asks
    ==========================  ==========  =================================
    **the silence**             1.0e-6      a silent input comes back as
                                 (-120 dBFS)  silence, unless the module says
                                             that it makes a sound of its own
    **the length of the        0.5 dB      the level of the output does not
     material**                              depend on how much material the
                                             module has seen
    ==========================  ==========  =================================

The gate is :mod:`qa.check_module_edges` and the test of the gate is
:mod:`tests.test_module_edges`.  Both read the numbers from here, so the rule
and the gate cannot drift apart.

The exemptions
--------------

Both rules are about what a module does to the material, and a module that
makes a sound of its own is not talking about the material at all: the
generator makes a tone, the ambience match lays a bed under the recording.
Such a module says so, and the way it says so is the declaration the tree
already has: **a module that never goes quiet declares an infinite tail**
(:data:`aurcore.params.TAIL_INFINITE`).  The gate reads the declaration from
the module and holds no list of module codes of its own, because a list in the
gate drifts: a module that is added to the family later would be silent on
silence and the gate would not know it.

A module that has not heard enough material yet is not judged either, and it
says so in one of two ways:

* through its **latency** -- a module that carries a latency of L answers a
  material shorter than L with nothing at all, and nothing at all is not a
  level;
* through its **settling time** -- the number of frames of material a module
  needs before its answer is final (:attr:`aurcore.modules.base.Module.
  settling_frames`).  A module that estimates over time needs to hear the
  material before it can judge it, and a module that says so is not judged by
  the material it has not heard yet.

Standard library and numpy only.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "EdgeRule",
    "SILENCE_RULE",
    "MATERIAL_RULE",
    "RULES",
    "MATERIAL_LENGTHS",
    "MATERIAL_REFERENCE_FRAMES",
    "rule_for",
]


@dataclass(frozen=True)
class EdgeRule:
    """One rule of the edge of the material.

    Attributes
    ----------
    name:
        The name of the rule.
    unit:
        The unit the number is in.
    limit:
        The number the rule holds to.
    how:
        The way the number is measured.
    reason:
        Why the number is what it is.
    """

    name: str
    unit: str
    limit: float
    how: str
    reason: str


#: The lengths of the material the rule of the material is measured at, in
#: frames (samples).  They are the lengths of the study and they are the
#: lengths a host hands over: a quarter of a millisecond, two milliseconds,
#: eight, twenty one, forty two, eighty five, one hundred and seventy and
#: one milliseconds and one second.  The last two are the lengths the two
#: estimators of the family need before their answer is final.
MATERIAL_LENGTHS: tuple[int, ...] = (256, 512, 1024, 2048, 4096, 8192, 16384, 49152)

#: The length of the long material the short ones are compared with, in
#: frames.  It is longer than every length of :data:`MATERIAL_LENGTHS` and
#: longer than the settling time of every module of the family, so the level
#: of the long material is the level a module holds.
MATERIAL_REFERENCE_FRAMES = 65536


#: **The silence.**  A silent input comes back as silence.
SILENCE_RULE = EdgeRule(
    name="the silence",
    unit="1 (linear peak amplitude)",
    limit=1.0e-6,
    how=(
        "the peak of the output when the input is silence: the silence edge "
        "of the gate, driven in one call at 48 kHz in stereo.  A module that "
        "declares an infinite tail -- a module that makes a sound of its own "
        "-- is exempt, because the sound it makes is not the material."
    ),
    reason=(
        "Silence in gives silence out.  The two references of the study were "
        "measured on it and both answer with exact zeros, and the worst "
        "module of ours that declares a finite tail answers with 2.7e-14 "
        "(-268 dBFS), so the number has room to spare.  1.0e-6 is the number "
        "the study used, and it is -120 dBFS: below the noise floor of a 24 "
        "bit file (-96 dBFS) and below the last bit of a 16 bit file "
        "(3.05e-5), so a module that holds the rule hands back a signal that "
        "no file can tell from silence.  It is also the tolerance the "
        "ceiling rule of the family uses for the same reason "
        "(aurcore/ceiling.py)."
    ),
)


#: **The length of the material.**  The level of the output does not depend
#: on how long the material is.
MATERIAL_RULE = EdgeRule(
    name="the length of the material",
    unit="dB",
    limit=0.5,
    how=(
        "the peak of the output (dBFS) when the input is a full scale tone of "
        "N frames, against the peak after the long material of "
        "%d frames.  N runs over %s.  A material shorter than the latency "
        "the module declares is not judged (the module has not heard enough "
        "and says so through its latency), and neither is a material shorter "
        "than the settling time the module declares.  A module that "
        "declares an infinite tail is exempt."
        % (
            MATERIAL_REFERENCE_FRAMES,
            ", ".join(str(n) for n in MATERIAL_LENGTHS),
        )
    ),
    reason=(
        "A level estimator that depends on the length of the material is an "
        "estimator that is not ready when the material starts: the first "
        "frames of a file are the frames a user hears first.  Half a decibel "
        "is the budget because it is the level at which a change of the gain "
        "of a programme stops being heard at all, and every module of the "
        "family that holds the rule measures within 0.01 dB, so the bound "
        "has a margin of fifty over the worst module of ours.  The two "
        "modules that did not hold it -- the guitar de-noise at 4.70 dB "
        "after 512 frames and the voice de-noise at 2.55 dB after 4096 "
        "frames -- are outside it by a factor of five."
    ),
)


#: The rules of the edges, by name.
RULES: tuple[EdgeRule, ...] = (SILENCE_RULE, MATERIAL_RULE)


def rule_for(name: str) -> EdgeRule:
    """Return the rule of that name.

    Parameters
    ----------
    name:
        The name of the rule.

    Returns
    -------
    EdgeRule
        The rule.
    """
    for rule in RULES:
        if rule.name == name:
            return rule
    raise KeyError(f"no edge rule named {name!r}")
