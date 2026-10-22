"""
aurcore.params — the canonical parameter model.

Parameter identity is the human-readable NAME. There are no numeric parameter ids in
the core. Values are stored in natural units (dB, Hz, ms, %); the UI converts to a
0..1 knob position and the core never sees normalised values.

The travel of a parameter
-------------------------
A host that automates a parameter works with a *position* on a travel: the
number 0..1 that says where on the travel the control stands.  The natural
value that position means is decided by the **curve** of the parameter:

``linear``
    The straight line between the minimum and the maximum.  One percent of
    the travel moves the value by one percent of the range.  This is the law
    a threshold wants: a threshold is judged against the signal, not against
    unity.

``log``
    The straight line *in the logarithm* of the value.  One percent of the
    travel moves a frequency by the same number of octaves everywhere, so the
    bottom of the range is as fine as the top.  This is the law a frequency
    wants: a control that is linear in hertz moves 1.58 octaves per percent
    of travel at 100 Hz and 0.03 octaves at 10 kHz, which is useless where a
    100 Hz region has to be set.

``taper``
    The power law ``v = lo + (hi - lo) * n ** p``.  With ``p`` below one the
    travel spends most of its length below unity and little above it, which
    is the shape of a gain fader: a wide cut, a narrow boost, and the same
    exponent over the whole travel.  The exponent is carried by the
    parameter itself, beside its range.

The name is the key the preset layer and the undo layer use, so a parameter
that is renamed keeps its old name as an **alias**: a document that names the
old parameter still loads, and the old name is still accepted.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Literal, Union

ParamType = Literal["float", "int", "bool", "enum"]

Number = Union[float, int]
Value = Union[float, int, bool, str]

#: The straight line between the minimum and the maximum.
CURVE_LINEAR = "linear"

#: The straight line in the logarithm of the value.
CURVE_LOG = "log"

#: The power law ``v = lo + (hi - lo) * n ** p``.
CURVE_TAPER = "taper"

#: The laws a parameter may follow.
CURVES: tuple[str, ...] = (CURVE_LINEAR, CURVE_LOG, CURVE_TAPER)

#: The exponent of the taper law: the shape of a gain fader, with a wide
#: span below unity and a narrow one above it.  It is the exponent that puts
#: unity in the middle of a range whose span below unity is five times the
#: span above it, which is the shape the reference gain control has.
TAPER_EXPONENT = 0.264

#: The tail of a module that never stops ringing.
#:
#: A module that keeps making a sound of its own -- a generator, a bed of
#: ambience -- does not go quiet when its input stops, so it cannot say how
#: long it rings.  It says this instead, and the value is the one the
#: plug-in formats use for an infinite tail, so an adapter can hand it to
#: the host without translating it.
TAIL_INFINITE = 0xFFFFFFFF


@dataclass(frozen=True)
class ParamDesc:
    """Declarative description of one module parameter."""

    name: str
    type: ParamType = "float"
    default: Value = 0.0
    minimum: float = 0.0
    maximum: float = 1.0
    unit: str = ""
    choices: tuple[str, ...] = ()
    group: str = ""
    automatable: bool = True
    stepped: bool = False
    curve: str = "linear"  # "linear" | "log" | "taper"
    help: str = ""
    #: The exponent of the taper law.  It only means something when the curve
    #: is ``taper``; the value is carried beside the range so that every
    #: tapered parameter states its own shape.
    curve_exponent: float = TAPER_EXPONENT
    #: The names this parameter had before it was renamed.  A document that
    #: carries an old name still loads: the store resolves the alias onto the
    #: canonical name.
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.type == "enum":
            if not self.choices:
                raise ValueError(f"enum parameter {self.name!r} needs choices")
            if self.default not in self.choices:
                object.__setattr__(self, "default", self.choices[0])
            # an enum has no numeric domain; the range is the choice index
            object.__setattr__(self, "minimum", 0.0)
            object.__setattr__(self, "maximum", float(max(len(self.choices) - 1, 0)))
        if self.type == "bool":
            if self.default not in (True, False, 0, 1):
                raise ValueError(f"bool parameter {self.name!r} needs a boolean default")
            object.__setattr__(self, "default", bool(self.default))
        if self.maximum < self.minimum:
            raise ValueError(f"parameter {self.name!r} has maximum < minimum")
        if self.curve not in CURVES:
            raise ValueError(
                f"parameter {self.name!r} has the unknown curve {self.curve!r}; "
                f"the laws are {', '.join(CURVES)}"
            )
        if self.curve == CURVE_TAPER and not (0.0 < float(self.curve_exponent)):
            raise ValueError(
                f"parameter {self.name!r} tapers with the exponent "
                f"{self.curve_exponent!r}, which is not a positive number"
            )
        if self.name in self.aliases:
            raise ValueError(f"parameter {self.name!r} is its own alias")

    @property
    def is_continuous(self) -> bool:
        return self.type == "float"

    # -- the travel -----------------------------------------------------------
    def natural_value(self, position: float) -> float:
        """The natural value at a position on the travel.

        The position is the number 0..1 a host automates: 0 is the bottom of
        the travel, 1 the top.  The law that turns a position into a value is
        the curve of the parameter.
        """
        n = min(max(float(position), 0.0), 1.0)
        low = float(self.minimum)
        high = float(self.maximum)
        if high <= low:
            return low
        if self.curve == CURVE_LOG and low > 0.0:
            return low * math.exp(n * math.log(high / low))
        if self.curve == CURVE_TAPER:
            return low + (high - low) * (n ** float(self.curve_exponent))
        return low + n * (high - low)

    def normalized_value(self, value: float) -> float:
        """The position on the travel that a natural value stands at.

        This is the inverse of :meth:`natural_value`; the two agree to within
        a rounding error of the float they are made of.
        """
        low = float(self.minimum)
        high = float(self.maximum)
        if high <= low:
            return 0.0
        v = min(max(float(value), low), high)
        if self.curve == CURVE_LOG and low > 0.0:
            return math.log(v / low) / math.log(high / low)
        if self.curve == CURVE_TAPER:
            span = (v - low) / (high - low)
            span = min(max(span, 0.0), 1.0)
            return span ** (1.0 / float(self.curve_exponent))
        return (v - low) / (high - low)

    # -- coercion -------------------------------------------------------------
    def coerce(self, value: Any) -> Value:
        """Clamp and type-coerce a raw value into this parameter's domain."""
        if self.type == "bool":
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "on", "yes")
            return bool(value)
        if self.type == "enum":
            text = str(value)
            return text if text in self.choices else self.default
        if self.type == "int":
            try:
                iv = int(round(float(value)))
            except (TypeError, ValueError):
                iv = int(self.default)
            iv = max(int(self.minimum), min(int(self.maximum), iv))
            return iv
        try:
            fv = float(value)
        except (TypeError, ValueError):
            fv = float(self.default)
        if fv != fv:  # NaN
            fv = float(self.default)
        fv = max(float(self.minimum), min(float(self.maximum), fv))
        if self.curve == CURVE_LOG and self.minimum > 0:
            fv = max(fv, self.minimum)
        return fv

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "default": self.default,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "unit": self.unit,
            "choices": list(self.choices),
            "group": self.group,
            "automatable": self.automatable,
            "stepped": self.stepped,
            "curve": self.curve,
            "help": self.help,
            "curve_exponent": self.curve_exponent,
            "aliases": list(self.aliases),
        }


class ParamStore:
    """Ordered, name-keyed container of a module's parameter values."""

    __slots__ = ("_descs", "_values", "_aliases")

    def __init__(self, descs: Iterable[ParamDesc] = ()):
        self._descs: list[ParamDesc] = []
        self._values: dict[str, Value] = {}
        self._aliases: dict[str, str] = {}
        for d in descs:
            self.add(d)

    # -- construction ---------------------------------------------------------
    def add(self, desc: ParamDesc) -> None:
        if desc.name in self._values or desc.name in self._aliases:
            raise KeyError(f"duplicate parameter {desc.name!r}")
        for alias in desc.aliases:
            if alias in self._values:
                raise KeyError(f"duplicate parameter {alias!r}")
            if alias in self._aliases:
                raise KeyError(f"duplicate parameter {alias!r}")
            self._aliases[alias] = desc.name
        self._descs.append(desc)
        self._values[desc.name] = desc.coerce(desc.default)

    def extend(self, descs: Iterable[ParamDesc]) -> "ParamStore":
        for d in descs:
            self.add(d)
        return self

    # -- access ---------------------------------------------------------------
    def descs(self) -> list[ParamDesc]:
        return list(self._descs)

    def canonical(self, name: str) -> str | None:
        """The canonical name of a parameter.

        A parameter that was renamed keeps its old name as an alias, so the
        old name resolves onto the canonical one.  ``None`` is returned for a
        name the store does not know.
        """
        if name in self._values:
            return name
        return self._aliases.get(name)

    def desc(self, name: str) -> ParamDesc:
        canonical = self.canonical(name)
        if canonical is None:
            raise KeyError(name)
        for d in self._descs:
            if d.name == canonical:
                return d
        raise KeyError(name)

    def has(self, name: str) -> bool:
        return self.canonical(name) is not None

    def get(self, name: str) -> Value:
        canonical = self.canonical(name)
        if canonical is None:
            raise KeyError(f"unknown parameter {name!r}") from None
        return self._values[canonical]

    def set(self, name: str, value: Any) -> None:
        canonical = self.canonical(name)
        if canonical is None:
            raise KeyError(f"unknown parameter {name!r}") from None
        self._values[canonical] = self.desc(canonical).coerce(value)

    def set_raw(self, name: str, value: Any) -> None:
        """Set without coercion (used when restoring persisted state)."""
        canonical = self.canonical(name)
        if canonical is None:
            raise KeyError(f"unknown parameter {name!r}") from None
        self._values[canonical] = value

    # -- bulk -----------------------------------------------------------------
    def reset(self) -> None:
        for d in self._descs:
            self._values[d.name] = d.coerce(d.default)

    def to_dict(self) -> dict[str, Value]:
        return dict(self._values)

    def from_dict(self, data: dict[str, Any]) -> None:
        for k, v in (data or {}).items():
            canonical = self.canonical(k)
            if canonical is not None:
                self._values[canonical] = self.desc(canonical).coerce(v)

    def groups(self) -> list[str]:
        seen: list[str] = []
        for d in self._descs:
            g = d.group or ""
            if g not in seen:
                seen.append(g)
        return seen

    def __len__(self) -> int:
        return len(self._descs)

    def __contains__(self, name: str) -> bool:
        return self.has(name)

    def __iter__(self):
        return iter(self._descs)


def desc(**kwargs) -> ParamDesc:
    """Terse constructor: desc(name="Sensitivity", type="float", default=3.0,
    minimum=0, maximum=10)."""
    return ParamDesc(**kwargs)


def dB(value: float) -> float:
    """Linear gain <-> dB helpers used across modules."""
    import math

    return 20.0 * math.log10(max(value, 1e-12))


def from_dB(value_db: float) -> float:
    import math

    return 10.0 ** (value_db / 20.0)
