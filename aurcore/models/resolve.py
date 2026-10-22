"""
aurcore.models.resolve — the seam a module asks a model through.

A module that can work with a learned model asks for it:

    binding = models.request(self, "voice")
    if binding is None:
        ... do what the module does today ...

The answer is **either a model or nothing**.  Nothing means the module does
what it does today, and the product keeps working: a model is never
required.

Two rules the seam enforces
---------------------------

**A model belongs to the offline path.**  The product has two processing
modes, ``realtime`` and ``offline``.  The modules of the real time path are
hand written: they are exact, they are cheap and they are deterministic, so
they stay.  The seam reads the mode of the module it is asked about — the
``processing_mode`` attribute the module carries — and hands out a model in
the offline mode only.  A module that does not carry the attribute is
treated as a real time module, which is the safe reading: the hand written
path is the one that always works.

**A control reads the model that is actually running.**  The resolver
records the model it bound to every module instance, and the control reads
that record.  It never reads "the first model of the library": a library
may hold several models, and the one that is bound is the one that is
running.
"""
from __future__ import annotations

import weakref
from dataclasses import dataclass
from typing import Any

from .engine import SpectralModel
from .errors import ModelError
from .library import ModelLibrary, open_library

__all__ = [
    "REALTIME",
    "OFFLINE",
    "Binding",
    "Resolver",
    "mode_of",
    "request",
    "running_model",
    "describe_running",
    "default_resolver",
    "set_default_resolver",
]

#: The mode of the real time path.
REALTIME = "realtime"

#: The mode of the offline path.
OFFLINE = "offline"


@dataclass(frozen=True)
class Binding:
    """The model that is running for one module instance.

    Attributes
    ----------
    model : SpectralModel
        The model that is running.
    problem : str
        The problem the model was asked to solve.
    """

    model: SpectralModel
    problem: str

    @property
    def name(self) -> str:
        """The name the running model publishes."""
        return self.model.manifest.name

    @property
    def version(self) -> str:
        """The version of the running model."""
        return self.model.manifest.version

    @property
    def trained_on(self) -> str:
        """The date the running model was trained."""
        return self.model.manifest.trained_on

    @property
    def epoch(self) -> int:
        """The epoch the running model was trained for."""
        return self.model.manifest.epoch

    @property
    def checksum(self) -> str:
        """The checksum the running model publishes."""
        return self.model.manifest.checksum

    def describe(self) -> dict:
        """Return a JSON-safe description of the running model."""
        described = self.model.describe()
        described["problem"] = self.problem
        return described

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Binding {self.name} {self.version} problem={self.problem}>"


def mode_of(module: Any) -> str:
    """Return the processing mode of a module.

    The mode is the ``processing_mode`` attribute of the module.  A module
    that does not carry it is a real time module: the hand written path is
    the one that always works, so the safe reading is the one that keeps
    it.

    Parameters
    ----------
    module : object
        The module to read.

    Returns
    -------
    str
        ``"realtime"`` or ``"offline"``.
    """
    raw = getattr(module, "processing_mode", REALTIME)
    text = str(raw).strip().lower()
    if text == OFFLINE:
        return OFFLINE
    return REALTIME


class Resolver:
    """Hands modules the model that solves a problem — or nothing.

    Parameters
    ----------
    library : ModelLibrary, optional
        The library the models come from.  Without it there is no model at
        all, and every request answers nothing.
    """

    def __init__(self, library: ModelLibrary | None = None):
        """Build a resolver over a library."""
        self.library = library
        # The bindings are held weakly: a module that goes away takes its
        # binding with it, and a new module never inherits the model of a
        # module that was collected.
        self._bindings: "weakref.WeakKeyDictionary[Any, Binding]" = (
            weakref.WeakKeyDictionary()
        )
        self._errors: dict[int, str] = {}

    # -- the bindings -----------------------------------------------------
    def bound(self, module: Any) -> Binding | None:
        """Return the model that is actually running for ``module``.

        This is what a control reads.  It never returns "the first model of
        the library": it returns the model that was bound to this module
        instance, or nothing.
        """
        try:
            return self._bindings.get(module)
        except TypeError:
            return None

    def detach(self, module: Any) -> None:
        """Forget the model of a module (the module is going away)."""
        try:
            del self._bindings[module]
        except (KeyError, TypeError):
            pass
        self._errors.pop(id(module), None)

    def last_error(self, module: Any = None) -> str:
        """Return the reason the last request for a module found no model."""
        if module is None:
            if not self._errors:
                return ""
            return self._errors[sorted(self._errors)[-1]]
        return self._errors.get(id(module), "")

    # -- the request ------------------------------------------------------
    def request(self, module: Any, problem: str, **kwargs) -> Binding | None:
        """Return the model that solves ``problem`` for ``module``, or nothing.

        The request never raises: the audio path must not break because a
        model is missing or broken.  The reason is kept and can be read
        with :meth:`last_error`.

        Parameters
        ----------
        module : object
            The module that asks.  Its ``processing_mode`` decides whether
            a model is handed out at all, and its sample rate and channel
            count decide whether the model may run.
        problem : str
            The problem to solve: ``"voice"``, ``"voice_reverb"`` or
            ``"ambience"``.
        **kwargs
            ``sample_rate`` and ``channels`` override what is read from the
            module.

        Returns
        -------
        Binding or None
            The running model, or nothing.
        """
        key = id(module)
        existing = self.bound(module)
        if existing is not None and existing.problem == problem:
            # The model that is already running is the model that runs.
            return existing

        if mode_of(module) != OFFLINE:
            self._errors.pop(key, None)
            return None
        if self.library is None:
            self._errors[key] = "no model library is configured"
            return None

        rate = kwargs.get("sample_rate", getattr(module, "sample_rate", None))
        channels = kwargs.get("channels", getattr(module, "channels", None))
        try:
            entry = self.library.for_problem(problem)
            if entry is None:
                self._errors[key] = f"the library holds no model for the problem {problem!r}"
                return None
            model = self.library.load(
                entry.name, sample_rate=rate, channels=channels
            )
        except ModelError as error:
            self._errors[key] = str(error)
            return None
        except Exception as error:  # noqa: BLE001 - the audio path must not break
            self._errors[key] = f"{type(error).__name__}: {error}"
            return None

        binding = Binding(model=model, problem=problem)
        try:
            self._bindings[module] = binding
        except TypeError:
            self._errors[key] = "the module cannot hold a binding"
            return None
        self._errors.pop(key, None)
        return binding

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        where = self.library.directory.name if self.library is not None else "none"
        return f"<Resolver library={where} bound={len(self._bindings)}>"


# ---------------------------------------------------------------------------
# the default resolver
# ---------------------------------------------------------------------------
_DEFAULT: Resolver | None = None


def default_resolver() -> Resolver:
    """Return the resolver the product uses.

    The shipped library is opened the first time it is needed, so importing
    the package does no I/O.
    """
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = Resolver(open_library())
    return _DEFAULT


def set_default_resolver(resolver: Resolver | None) -> None:
    """Set (or clear) the resolver the product uses."""
    global _DEFAULT
    _DEFAULT = resolver


def request(module: Any, problem: str, **kwargs) -> Binding | None:
    """Ask the default resolver for the model that solves ``problem``.

    Parameters
    ----------
    module : object
        The module that asks.
    problem : str
        The problem to solve.
    **kwargs
        ``resolver``, ``sample_rate`` and ``channels``.

    Returns
    -------
    Binding or None
        The running model, or nothing.
    """
    resolver = kwargs.pop("resolver", None)
    if resolver is None:
        resolver = default_resolver()
    return resolver.request(module, problem, **kwargs)


def running_model(module: Any, resolver: Resolver | None = None) -> Binding | None:
    """Return the model that is actually running for ``module``.

    Parameters
    ----------
    module : object
        The module to read.
    resolver : Resolver, optional
        The resolver that holds the bindings.  The default resolver is
        used without it.

    Returns
    -------
    Binding or None
        The model that is running, or nothing when the module runs without
        one.
    """
    if resolver is None:
        resolver = default_resolver()
    return resolver.bound(module)


def describe_running(module: Any, resolver: Resolver | None = None) -> dict | None:
    """Describe the model that is actually running for ``module``.

    Returns
    -------
    dict or None
        The description of the running model, or nothing when the module
        runs without one.
    """
    binding = running_model(module, resolver)
    if binding is None:
        return None
    return binding.describe()
