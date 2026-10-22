"""
qa.survey.exceptions — the known open defects of the catalogue.

A module that breaks a rule has to be *listed*, with the number that was
measured and the reason the rule is broken.  The listing is what keeps the
gate honest: a module without an entry has to satisfy every rule, and a
module with an entry is only allowed to be as bad as the entry says.

Each entry carries

``limit``
    the boundary that is allowed — the worst value the module may reach,
``measured``
    the value that was measured when the entry was written,
``tolerance``
    how much worse the module may get before the gate complains again,
``reason``
    why the rule is broken and who is working on it,
``owner``
    the finding file that carries the numbers of the defect.

The entries in this file are the open defects of the survey itself.  When a
module is fixed, its entry is deleted and the gate holds it to the rule.

**Every entry has a finding file** in ``var/patches/b12/``: the file says
what the defect is, what was measured and what the owner has to do.  A
finding that has been closed says so at the top of its file, and the report
lists the closed findings apart from the open ones.

The state of this list (2026-10-21):

* the modules the survey found broken in the first round (Ambience Match,
  Deconstruct, Dialogue Isolate, EQ Match, Guitar De-Noise) have been
  repaired and are no longer listed; their finding files are marked closed;
* the Voice De-Noise has been repaired and is no longer listed either;
* the De-Clip and the De-Ess have been repaired;
* the Spectral De-Noise still damages a wanted partial by 3.86 dB.  That is
  defect 1 of the team brief, the patch of the study is history and the
  defect is open.  The module was rewritten on 2026-10-21 at 17:00 and the
  number has to be taken again when the rewrite lands;
* the **cost** of the modules is no longer listed at all: the cost of a
  block is measured next to a batch of transforms and expressed in the
  microseconds of the reference machine, so it does not move with the load
  of the machine that measures it, and every module of the catalogue
  fits into the realtime budget of a 64 sample block (1333.3 us).  The
  Music Rebalance was the last one that did not: it cost 1698 us on this
  machine in the afternoon of 2026-10-21 and 848 us on the reference
  machine after its repair, and its cost entry has been deleted with it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["Exception_", "EXCEPTIONS", "exception_for", "exception_key", "all_exceptions"]


@dataclass(frozen=True)
class Exception_:
    """One known open defect of the catalogue.

    ``known`` tells the two kinds apart: an entry that was already on the
    list when the survey started (``known`` is true) and a defect the survey
    found (``known`` is false).  The second kind points at the finding file
    of the owner of the module.
    """

    code: str
    kind: str
    limit: float
    measured: float
    reason: str
    tolerance: float = 0.75
    owner: str = ""
    known: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation."""
        return {
            "code": self.code,
            "kind": self.kind,
            "limit": self.limit,
            "measured": self.measured,
            "tolerance": self.tolerance,
            "reason": self.reason,
            "owner": self.owner,
            "known": self.known,
        }


def exception_key(code: str, kind: str, label: str = "") -> tuple[str, str]:
    """Return the key an exception is stored under."""
    if kind in ("keep", "target") and label:
        return (code, f"{kind}:{label}")
    return (code, kind)


#: Every known open defect, by key.
EXCEPTIONS: dict[tuple[str, str], Exception_] = {}


def register(
    code: str,
    kind: str,
    limit: float,
    measured: float,
    reason: str,
    tolerance: float = 0.75,
    owner: str = "",
    label: str = "",
    known: bool = True,
) -> Exception_:
    """Register a known open defect and return it."""
    entry = Exception_(
        code=code,
        kind=kind,
        limit=float(limit),
        measured=float(measured),
        reason=reason,
        tolerance=float(tolerance),
        owner=owner,
        known=known,
    )
    EXCEPTIONS[exception_key(code, kind, label)] = entry
    return entry


def exception_for(code: str, kind: str, label: str = "") -> Exception_ | None:
    """Return the exception of a module, or ``None``."""
    return EXCEPTIONS.get(exception_key(code, kind, label))


def all_exceptions() -> list[Exception_]:
    """Return every registered exception, sorted."""
    return [EXCEPTIONS[key] for key in sorted(EXCEPTIONS)]


# ---------------------------------------------------------------------------
# The open defects
# ---------------------------------------------------------------------------
# The wanted part that a module is known to damage.
register(
    "AUR_SPECTRALDENOISE",
    "keep",
    limit=-3.86,
    measured=-3.86,
    reason=(
        "known, in the team brief as defect 1: a wanted partial is crushed by "
        "the noise law.  The patch of the study (qa/quality/spectral_de_noise.patch) "
        "was applied to an earlier version of the module and is history now: it "
        "does not apply to the current file, and the defect is still open — the "
        "480 Hz partial lost 6.46 dB before the module was rewritten and loses "
        "3.86 dB now.  The module was being rewritten while the survey ran, so "
        "the number has to be taken again when the rewrite lands.  The cause is "
        "in qa/quality/STUDY.md"
    ),
    tolerance=0.75,
    owner="var/patches/b12/spectral_de_noise.md",
    label="480 Hz partial",
)
