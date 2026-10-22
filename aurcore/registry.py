"""
aurcore.registry — the module registry.

A module is registered once, by importing its module file and calling
``register(cls)``.  The registry is the single source of truth for "what modules
exist", and it is the thing that hosts read to build the module browser.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .params import ParamDesc

_MODULES: dict[str, type] = {}
_ORDER: list[str] = []


@dataclass
class ModuleInfo:
    code: str
    display_name: str
    category: str
    edition: str = "standard"
    element: str = ""
    params: list[ParamDesc] = field(default_factory=list)
    analysis: bool = False   # supports an "output only" analysis render
    learn: bool = False      # exposes a Learn action
    ml: bool = False         # needs a machine-learning model
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "display_name": self.display_name,
            "category": self.category,
            "edition": self.edition,
            "element": self.element,
            "params": [p.to_dict() for p in self.params],
            "analysis": self.analysis,
            "learn": self.learn,
            "ml": self.ml,
            "description": self.description,
        }


def register(cls: type) -> type:
    """Class decorator that adds a module to the global registry."""
    code = getattr(cls, "code", None)
    if not code:
        raise ValueError(f"{cls!r} has no `code` attribute")
    if code in _MODULES:
        raise ValueError(f"duplicate module code {code!r}")
    _MODULES[code] = cls
    _ORDER.append(code)
    return cls


def get(code: str) -> type:
    try:
        return _MODULES[code]
    except KeyError:
        raise KeyError(f"unknown module code {code!r}") from None


def has(code: str) -> bool:
    return code in _MODULES


def all() -> list[ModuleInfo]:
    """Registry contents, sorted by category then display name."""
    infos: list[ModuleInfo] = []
    for code in _ORDER:
        cls = _MODULES[code]
        infos.append(
            ModuleInfo(
                code=code,
                display_name=getattr(cls, "display_name", code),
                category=getattr(cls, "category", "Utility"),
                edition=getattr(cls, "edition", "standard"),
                element=getattr(cls, "element", ""),
                params=_probe_params(cls),
                analysis=bool(getattr(cls, "analysis", False)),
                learn=bool(getattr(cls, "supports_learn", False)),
                ml=bool(getattr(cls, "ml", False)),
                description=(cls.__doc__ or "").strip().split("\n")[0],
            )
        )
    order = {"Repair": 0, "Utility": 1, "Measurement": 2, "System": 3}
    infos.sort(key=lambda i: (order.get(i.category, 9), i.display_name.lower()))
    return infos


def _probe_params(cls: type) -> list[ParamDesc]:
    """Instantiate once with a neutral context to read the parameter list."""
    try:
        inst = cls(48000, 2)  # type: ignore[call-arg]
    except Exception:
        try:
            inst = cls()  # type: ignore[call-arg]
        except Exception:
            return []
    try:
        return inst.describe()
    except Exception:
        return []


def create(code: str, sample_rate: int, channels: int = 2):
    """Instantiate a module by code."""
    cls = get(code)
    return cls(sample_rate, channels)  # type: ignore[call-arg]


def codes() -> list[str]:
    return list(_ORDER)


def clear() -> None:
    """Testing helper: empty the registry."""
    _MODULES.clear()
    _ORDER.clear()
