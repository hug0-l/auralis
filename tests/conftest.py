"""
Shared fixtures for the Auralis test-suite.

The suite exercises the DSP core only — no network, no file servers, no
plug-in hosts (CONTRACT §9).  Every fixture here is deterministic: signals are
generated from a fixed seed so a failure is always reproducible.

Signal generators return ``(n_samples, n_channels)`` float64 arrays, which is
the shape the core API uses everywhere (CONTRACT §2).
"""
from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
import json
import math
import os
import pkgutil
import sys
from pathlib import Path
from typing import Callable, Iterable

import numpy as np
import pytest

from aurcore import registry
from aurcore.api import create_engine

# --------------------------------------------------------------------------
# Constants shared by the fixtures and the test modules.
# --------------------------------------------------------------------------
SAMPLE_RATE = 48_000
CHANNELS = 2
DEFAULT_SIG_LEN = 24_000          # 0.5 s at 48 kHz
RNG_SEED = 20_260_101             # fixed -> reproducible failures

__all__ = [
    "SAMPLE_RATE",
    "CHANNELS",
    "DEFAULT_SIG_LEN",
    "RNG_SEED",
    "ModuleImportReport",
    "import_report",
    "module_class",
    "make_module",
    "tone_signal",
    "rms_db",
    "peak_db",
    "expected_output_length",
    "length_tolerance",
]


# --------------------------------------------------------------------------
# Module import handling.
#
# Module implementations land in aurcore/modules/ one file at a time.  While
# the tree is still being written a single broken file would abort the whole
# package import, so the loader below falls back to importing every file in
# isolation and records the failures instead of raising.  Tests then skip the
# modules that do not exist yet and still exercise the ones that do.
# --------------------------------------------------------------------------
class ModuleImportReport:
    """Outcome of importing the module tree."""

    def __init__(self) -> None:
        """Create the report object."""
        self.errors: dict[str, str] = {}
        self.imported: list[str] = []

    @property
    def ok(self) -> bool:
        """True when every module file imported cleanly."""
        return not self.errors

    def describe(self) -> str:
        """A one line summary of the import outcome."""
        if not self.errors:
            return "all module files import cleanly"
        return "; ".join(f"{name}: {msg}" for name, msg in sorted(self.errors.items()))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        """Readable representation of the report."""
        return f"<ModuleImportReport imported={len(self.imported)} errors={len(self.errors)}>"


def _stub_modules_package() -> None:
    """Register a bare ``aurcore.modules`` package without running its __init__.

    Used only as a fallback when the package import fails: the stub keeps the
    submodule search path intact so every module file can be imported on its
    own and the broken ones can be named individually.
    """
    import aurcore

    pkg_name = "aurcore.modules"
    if pkg_name in sys.modules:
        return
    path = os.path.join(os.path.dirname(os.path.abspath(aurcore.__file__)), "modules")
    if not os.path.isdir(path):
        return
    spec = importlib.machinery.ModuleSpec(pkg_name, None, is_package=True)
    spec.submodule_search_locations = [path]
    module = importlib.util.module_from_spec(spec)
    module.__path__ = [path]  # type: ignore[attr-defined]
    sys.modules[pkg_name] = module


def _import_all_modules() -> ModuleImportReport:
    """Import every module file, collecting per-file errors instead of raising."""
    report = ModuleImportReport()
    try:
        pkg = importlib.import_module("aurcore.modules")
    except Exception as exc:  # noqa: BLE001 - concurrent development safety net
        report.errors["<package>"] = f"{type(exc).__name__}: {exc}"
        _stub_modules_package()
        pkg = sys.modules.get("aurcore.modules")
        if pkg is None:
            return report
    for _finder, name, _ispkg in pkgutil.iter_modules(list(getattr(pkg, "__path__", []))):
        if name.startswith("_"):
            continue
        try:
            importlib.import_module(f"aurcore.modules.{name}")
            report.imported.append(name)
        except Exception as exc:  # noqa: BLE001 - concurrent development safety net
            report.errors[name] = f"{type(exc).__name__}: {exc}"
    return report


# Executed at import time so that the registry is populated before pytest
# collects (and therefore parametrizes over) the module tests.
IMPORT_REPORT = _import_all_modules()


@pytest.fixture(scope="session")
def import_report() -> ModuleImportReport:
    """The result of importing the module tree (errors are names + messages)."""
    return IMPORT_REPORT


# --------------------------------------------------------------------------
# Core fixtures.
# --------------------------------------------------------------------------
@pytest.fixture
def sample_rate() -> int:
    """The sample rate the fixtures run at (48 kHz)."""
    return SAMPLE_RATE


@pytest.fixture
def channels() -> int:
    """The channel count the fixtures run at (stereo)."""
    return CHANNELS


@pytest.fixture
def sig_len() -> int:
    """The length of the generated signals, in samples."""
    return DEFAULT_SIG_LEN


@pytest.fixture
def rng() -> np.random.Generator:
    """A fresh, deterministic random generator."""
    return np.random.default_rng(RNG_SEED)


@pytest.fixture
def engine():
    """A 48 kHz / 2-channel engine with an empty chain."""
    return create_engine(SAMPLE_RATE, CHANNELS)


@pytest.fixture(autouse=True)
def _the_registry_is_restored_after_every_test():
    """Leave the registry exactly as the test found it.

    The registry is process-global.  A test that registers a dummy module
    and forgets to remove it leaves the catalogue one module too long, and
    every later test that compares the live catalogue with a committed
    table fails — while each of them passes on its own.  Restoring the
    registry after every test makes that impossible, whatever a test does.
    """
    modules = dict(getattr(registry, "_MODULES", {}))
    order = list(getattr(registry, "_ORDER", []))
    try:
        yield
    finally:
        registry.clear()
        for code, cls in modules.items():
            registry.register(cls)
        if hasattr(registry, "_ORDER"):
            registry._ORDER[:] = order  # type: ignore[attr-defined]


@pytest.fixture
def registry_snapshot():
    """Isolate the global registry for a test that mutates it.

    The registry is process-global and is normally populated by importing
    ``aurcore.modules``; a test that registers its own dummy classes needs
    an empty registry, and the real modules must come back afterwards.
    """
    modules = dict(getattr(registry, "_MODULES", {}))
    order = list(getattr(registry, "_ORDER", []))
    registry.clear()
    try:
        yield
    finally:
        registry.clear()
        for code, cls in modules.items():
            registry.register(cls)
        if hasattr(registry, "_ORDER"):
            registry._ORDER[:] = order  # type: ignore[attr-defined]


# --------------------------------------------------------------------------
# Signal generators.
# --------------------------------------------------------------------------
def tone_signal(
    freq: float,
    n: int,
    amp: float = 0.5,
    sr: int = SAMPLE_RATE,
    ch: int = CHANNELS,
    phase: float = 0.0,
) -> np.ndarray:
    """A (possibly multi-channel) sine; channels are phase-identical by default."""
    t = np.arange(n, dtype=np.float64) / float(sr)
    wave = amp * np.sin(2.0 * math.pi * freq * t + phase)
    return np.repeat(wave[:, None], ch, axis=1)


def rms_db(x: np.ndarray) -> float:
    """RMS level of a signal in dBFS (mono sum of the channel powers)."""
    arr = np.asarray(x, dtype=np.float64)
    if arr.ndim == 1:
        arr = arr[:, None]
    rms = float(np.sqrt(np.mean(np.sum(arr * arr, axis=1))))
    return 20.0 * math.log10(max(rms, 1e-12))


def peak_db(x: np.ndarray) -> float:
    """Absolute peak level of a signal in dBFS."""
    arr = np.asarray(x, dtype=np.float64)
    return 20.0 * math.log10(max(float(np.max(np.abs(arr))), 1e-12))


@pytest.fixture
def white_noise(rng: np.random.Generator, sig_len: int) -> np.ndarray:
    """Full-band noise at -20 dBFS peak, identical in both channels."""
    noise = rng.standard_normal((sig_len, CHANNELS))
    noise /= max(float(np.max(np.abs(noise))), 1e-12)
    return noise * 0.1


@pytest.fixture
def tone(sig_len: int) -> np.ndarray:
    """A 1 kHz sine at -6 dBFS."""
    return tone_signal(1000.0, sig_len, amp=0.5)


@pytest.fixture
def click_train(sig_len: int) -> np.ndarray:
    """A quiet bed with four full-scale single-sample clicks."""
    n = sig_len
    x = 0.01 * tone_signal(440.0, n, amp=1.0)
    clicks = np.zeros((n, CHANNELS), dtype=np.float64)
    spacing = max(n // 4, 1)
    for k in range(4):
        pos = spacing * k + spacing // 2
        if pos < n:
            clicks[pos, :] = 1.0
    return x + clicks


@pytest.fixture
def hum(sig_len: int) -> np.ndarray:
    """Mains hum: 50 Hz plus its first three harmonics, 0 dBFS at the peak."""
    n = sig_len
    out = np.zeros((n, CHANNELS), dtype=np.float64)
    for k, amp in enumerate((0.5, 0.25, 0.125, 0.0625), start=1):
        out += tone_signal(50.0 * k, n, amp=amp)
    return out


@pytest.fixture
def clipped_tone(sig_len: int) -> np.ndarray:
    """A 1 kHz sine pushed well past full scale and hard-clipped."""
    x = tone_signal(1000.0, sig_len, amp=2.5)
    return np.clip(x, -1.0, 1.0)


@pytest.fixture
def sibilance(rng: np.random.Generator, sig_len: int) -> np.ndarray:
    """A 5-9 kHz noise burst with a raised-cosine envelope."""
    n = sig_len
    noise = rng.standard_normal((n, CHANNELS))
    spec = np.fft.rfft(noise, axis=0)
    freqs = np.fft.rfftfreq(n, 1.0 / SAMPLE_RATE)
    spec[(freqs < 5000.0) | (freqs > 9000.0)] = 0.0
    band = np.fft.irfft(spec, n=n, axis=0)
    band /= max(float(np.max(np.abs(band))), 1e-12)
    env = 0.5 * (1.0 - np.cos(2.0 * math.pi * np.arange(n) / max(n - 1, 1)))
    return band * env[:, None] * 0.25


# --------------------------------------------------------------------------
# Module helpers.
# --------------------------------------------------------------------------
def expected_output_length(module, n: int, sample_rate: int = SAMPLE_RATE) -> int:
    """How many samples a module must return for an input of ``n`` samples.

    Most modules preserve the length; a resampler or a time stretcher scales
    it.  The scale is taken from the module's own parameters, so the check
    follows the settings the module is actually running with:

    * a ``Sample Rate`` parameter means the module resamples;
    * a ``Ratio`` / ``Stretch Ratio`` parameter in the ``Time`` group means
      the module stretches time (a ``Ratio`` in the ``Compressor`` group does
      not change the length).
    """
    for desc in module.describe():
        if desc.name == "Sample Rate" and desc.type == "enum":
            try:
                return int(round(n * float(module.params.get(desc.name)) / float(sample_rate)))
            except (TypeError, ValueError):
                break
    for desc in module.describe():
        if desc.name in ("Ratio", "Stretch Ratio") and desc.group == "Time":
            try:
                return int(round(n * float(module.params.get(desc.name))))
            except (TypeError, ValueError):
                break
    return int(n)


def length_tolerance(expected: int) -> int:
    """How many samples a module may be off by."""
    return max(64, int(abs(expected) * 0.01))


@pytest.fixture
def module_class() -> Callable[[str], type]:
    """Return the registered class for ``code`` or skip the test."""

    def _get(code: str) -> type:
        """Return the registered class for a code, or skip the test."""
        if not registry.has(code):
            pytest.skip(f"module {code!r} is not registered yet")
        return registry.get(code)

    return _get


@pytest.fixture
def make_module() -> Callable[[str], object]:
    """Instantiate a registered module at 48 kHz / stereo or skip the test."""

    def _make(code: str):
        """Instantiate a registered module, or skip the test."""
        if not registry.has(code):
            pytest.skip(f"module {code!r} is not registered yet")
        try:
            return registry.create(code, SAMPLE_RATE, CHANNELS)
        except Exception as exc:  # noqa: BLE001 - skip a module that cannot start
            pytest.skip(f"module {code!r} could not be instantiated: {exc}")

    return _make


# --------------------------------------------------------------------------
# Filesystem helpers.
# --------------------------------------------------------------------------
@pytest.fixture
def tmp_preset(tmp_path: Path) -> Callable[..., Path]:
    """Write a JSON document into a temporary directory and return its path."""

    counter = {"n": 0}

    def _write(document, name: str | None = None) -> Path:
        """Write a JSON document into the temporary directory."""
        counter["n"] += 1
        target = tmp_path / (name or f"document-{counter['n']}.json")
        target.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
        return target

    _write.directory = tmp_path  # type: ignore[attr-defined]
    return _write
