"""
aurmcp.tools — the tools the Auralis model context protocol server offers.

The tools are the way an agent **operates** the product: it reads the
catalogue, it builds a chain, it renders a case and it measures the result
with the project's own instruments.

Every tool in here obeys the rules of the house:

* a description says what the tool does **and what it does not do**, because
  the agent reads the description to decide which tool to call;
* the input schema is complete: the properties, their types, the required
  ones and what a valid value looks like;
* a refusal is actionable: it names the problem and says what the agent can
  do instead (the valid names, the valid range);
* the tools are safe to call in any order: the chain is the working state of
  the session, it is always readable back, and no tool leaves the product in
  a state that breaks another.

The working state lives in this module: one chain, one processing mode.  A
lock guards it, and every tool that touches it takes the lock.

The measurement discipline of the project is kept: every number carries the
instrument that produced it, and "could not be measured" is an answer.
"""
from __future__ import annotations

import importlib.util
import json
import math
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

try:  # pragma: no cover - the protocol core is built in parallel
    from .protocol import Resource, Tool, ToolError
except ImportError:  # pragma: no cover - the fallback keeps the tools importable
    @dataclass(frozen=True)
    class Tool:
        """The description of one tool: its name, its text and its handler."""

        name: str
        description: str
        input_schema: dict
        handler: Callable[[dict], Any]

    @dataclass(frozen=True)
    class Resource:
        """The description of one resource: its uri, its text and its reader."""

        uri: str
        name: str
        description: str
        mime_type: str
        reader: Callable[[], str]

    class ToolError(Exception):
        """A refusal the agent reads: it says what is wrong and what to do."""


__all__ = ["TOOLS", "TOOL_BY_NAME", "Tool", "Resource", "ToolError"]


# ---------------------------------------------------------------------------
# the tree
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent


def _repo_root() -> Path:
    """The root of the product tree.

    The root is the directory that holds ``aurcore`` and ``corpus``.  The
    module lives in ``aurmcp`` inside the tree, so the parent of the package
    is the root; the working directory is the second candidate, for the case
    where the package was copied somewhere else.
    """
    for candidate in (_HERE.parent, Path.cwd()):
        if (candidate / "aurcore").is_dir() and (candidate / "corpus").is_dir():
            return candidate
    return _HERE.parent


#: The sample rate the working chain runs at unless the agent says otherwise.
DEFAULT_SAMPLE_RATE = 48_000

#: The channel count the working chain runs at unless the agent says otherwise.
DEFAULT_CHANNELS = 2

#: The two processing modes of the product.
PROCESSING_MODES = ("realtime", "offline")

#: The name of the module parameter that carries the processing mode.
PROCESSING_MODE_PARAMETER = "Processing Mode"

#: The value that stands for "the tail never ends".
TAIL_INFINITE = 0xFFFFFFFF

#: The block size the server's render route uses, in samples.
RENDER_BLOCK = 65_536

#: The block size the cost of a block is measured with, in samples.
COST_BLOCK = 64

#: The block size the block coherence of a measurement is measured with.
COHERENCE_BLOCK = 4096


# ---------------------------------------------------------------------------
# the working state
# ---------------------------------------------------------------------------
class _State:
    """The working state of the session: the chain and its processing mode."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.sample_rate = DEFAULT_SAMPLE_RATE
        self.channels = DEFAULT_CHANNELS
        self.mode = "realtime"
        self.engine = None  # built lazily, see _engine()


_STATE = _State()


def _engine():
    """The working engine.  It exists as soon as it is asked for."""
    if _STATE.engine is None:
        from aurcore.api import create_engine

        _STATE.engine = create_engine(_STATE.sample_rate, _STATE.channels)
    return _STATE.engine


def _snapshot_engine():
    """A fresh engine that carries the same chain as the working one.

    The measurement and the render run on the copy, so the working chain
    keeps exactly the state the agent set: a render changes the state a
    module carries between blocks, and the agent did not ask for that.
    """
    from aurcore.api import create_engine

    engine = create_engine(_STATE.sample_rate, _STATE.channels)
    engine.deserialize(_engine().serialize())
    engine.processing_mode = _STATE.mode
    return engine


# ---------------------------------------------------------------------------
# the registry and the catalogue
# ---------------------------------------------------------------------------
def _import_modules() -> None:
    """Import every module file, one by one.

    A module file that cannot be imported must not take the whole catalogue
    down, so every file is imported on its own and the failures are ignored
    here: a module that is missing is a module the agent does not find in
    the catalogue.
    """
    import importlib
    import pkgutil

    try:
        package = importlib.import_module("aurcore.modules")
    except Exception:  # noqa: BLE001 - one broken file must not break the rest
        package = None
    if package is None:
        try:
            package = importlib.import_module("aurcore.modules")
        except Exception:  # noqa: BLE001
            return
    for _finder, name, _ispkg in pkgutil.iter_modules(list(package.__path__)):
        if name.startswith("_"):
            continue
        try:
            importlib.import_module(f"aurcore.modules.{name}")
        except Exception:  # noqa: BLE001 - reported by the catalogue as missing
            continue


def _module_infos() -> list:
    """The catalogue of the registry."""
    from aurcore import registry

    _import_modules()
    return registry.all()


def _codes() -> list[str]:
    """The module codes of the catalogue."""
    return [info.code for info in _module_infos()]


def _module_info(code: str):
    """The catalogue entry of one module, or ``None``."""
    for info in _module_infos():
        if info.code == code:
            return info
    return None


def _require_module(code: str):
    """The catalogue entry of a module, or a refusal that names the codes."""
    info = _module_info(code)
    if info is not None:
        return info
    codes = _codes()
    raise ToolError(
        f"unknown module code {code!r}; the catalogue holds {len(codes)} modules: "
        + ", ".join(codes)
        + ". Call list_modules to see what each of them is."
    )


def _instance(code: str, sample_rate: int = DEFAULT_SAMPLE_RATE,
              channels: int = DEFAULT_CHANNELS):
    """A fresh instance of a module, with its parameters."""
    from aurcore import registry

    _import_modules()
    try:
        return registry.create(code, int(sample_rate), int(channels))
    except KeyError:
        _require_module(code)
        raise ToolError(f"the module {code!r} could not be created")


def _param_dicts(code: str) -> list[dict]:
    """The parameter descriptions of a module, as plain dictionaries."""
    module = _instance(code)
    return [desc.to_dict() for desc in module.params.descs()]


# ---------------------------------------------------------------------------
# the parameters: reading them and refusing them
# ---------------------------------------------------------------------------
def _describe_value(desc: dict) -> str:
    """A sentence about what a valid value of a parameter looks like."""
    kind = desc.get("type")
    unit = desc.get("unit") or ""
    if kind == "enum":
        choices = ", ".join(str(choice) for choice in desc.get("choices", []))
        return f"one of the choices: {choices}"
    if kind == "bool":
        return "true or false"
    if kind == "int":
        return f"a whole number between {desc.get('minimum')} and {desc.get('maximum')}"
    low = desc.get("minimum")
    high = desc.get("maximum")
    span = f"between {low} and {high}"
    if unit:
        span += f" {unit}"
    return f"a number {span}"


def _coerce_value(desc: dict, value: Any, module_code: str, name: str) -> Any:
    """The value a parameter really holds, or a refusal that names the range.

    The core clamps silently, so the range is checked here: a value outside
    the range is a refusal and not a quiet change of the meaning of the
    number the agent sent.
    """
    kind = desc.get("type")
    label = f"{module_code} / {name}"
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and float(value) in (0.0, 1.0):
            return bool(value)
        text = str(value).strip().lower()
        if text in ("true", "on", "yes", "1"):
            return True
        if text in ("false", "off", "no", "0"):
            return False
        raise ToolError(
            f"{label} is a switch: it takes true or false, not {value!r}. "
            "Send true or false."
        )
    if kind == "enum":
        choices = [str(choice) for choice in desc.get("choices", [])]
        text = str(value)
        if text in choices:
            return text
        raise ToolError(
            f"{label} takes one of its choices, and {value!r} is not one of them: "
            + ", ".join(choices)
            + ". Send one of the choices exactly as they are written."
        )
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ToolError(
                f"{label} is a whole number and takes {value!r}; "
                f"{_describe_value(desc)}."
            )
        low = int(desc.get("minimum", 0))
        high = int(desc.get("maximum", 0))
        number = int(round(float(value)))
        if number < low or number > high:
            raise ToolError(
                f"{label} must be {low} to {high}, and {value!r} is outside that "
                f"range. Send a value between {low} and {high}."
            )
        return number
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolError(
            f"{label} is a number and takes {value!r}; {_describe_value(desc)}."
        )
    number = float(value)
    if number != number:  # nan
        raise ToolError(f"{label} is a number and {value!r} is not one.")
    low = float(desc.get("minimum", 0.0))
    high = float(desc.get("maximum", 1.0))
    if number < low or number > high:
        unit = desc.get("unit") or ""
        raise ToolError(
            f"{label} must be between {low} and {high}"
            + (f" {unit}" if unit else "")
            + f", and {value!r} is outside that range. "
            f"Send a value between {low} and {high}"
            + (f" {unit}." if unit else ".")
        )
    return number


def _check_params(module_code: str, params: dict) -> dict:
    """The values of a parameter map, checked, or a refusal.

    Nothing is changed when the check refuses: the caller gets the refusal
    and the chain keeps what it had.
    """
    if params is None:
        return {}
    if not isinstance(params, dict):
        raise ToolError(
            f"the parameters of {module_code} must be a mapping of parameter names "
            f"to values, not {type(params).__name__}."
        )
    module = _instance(module_code)
    checked: dict[str, Any] = {}
    for name, value in params.items():
        canonical = module.params.canonical(str(name))
        if canonical is None:
            names = ", ".join(desc.name for desc in module.params.descs())
            raise ToolError(
                f"{module_code} has no parameter {name!r}; its parameters are: "
                f"{names}. Call describe_module to see what each of them does."
            )
        desc = module.params.desc(canonical).to_dict()
        checked[canonical] = _coerce_value(desc, value, module_code, str(name))
    return checked


# ---------------------------------------------------------------------------
# the chain
# ---------------------------------------------------------------------------
def _tail_of(module) -> Any:
    """The tail of a module: a number, or the word ``infinite``."""
    from aurcore.api import module_tail

    tail = int(module_tail(module))
    return "infinite" if tail == TAIL_INFINITE else tail


def _chain_summary(include_parameters: bool = True) -> dict:
    """The chain as it stands, with the summary of the chain."""
    engine = _engine()
    mode = engine.processing_mode
    steps = []
    for index, step in enumerate(engine.chain):
        module = step.module
        entry = {
            "index": index,
            "code": module.code,
            "display_name": module.display_name,
            "category": module.category,
            "edition": module.edition,
            "bypass": bool(step.bypass),
            # The engine hands the mode of the chain to every module before
            # the chain runs, so the mode of a step is the mode of the chain.
            "processing_mode": mode,
            "latency_samples": int(module.latency_samples),
            "tail_samples": _tail_of(module),
        }
        if include_parameters:
            entry["params"] = dict(module.params.to_dict())
        steps.append(entry)
    latency = int(engine.latency_samples)
    return {
        "steps": steps,
        "module_count": len(steps),
        "processing_mode": mode,
        "latency_samples": latency,
        "latency_ms": round(latency * 1000.0 / float(engine.sample_rate), 3),
        "tail_samples": _tail_of(engine),
        "sample_rate": int(engine.sample_rate),
        "channels": int(engine.channels),
    }


def _force_mode(engine, mode: str) -> None:
    """Put the engine into a mode, whatever mode it was in.

    The engine hands its mode to the modules of the chain when the mode
    *changes*, so a chain that was built while the mode was being changed
    can hold a module that still works in the old mode.  The engine is
    therefore put into the other mode and back: the public setter is the only
    door, and the state the engine ends in is the mode that was asked for.
    """
    if engine.processing_mode == mode:
        other = "offline" if mode == "realtime" else "realtime"
        engine.processing_mode = other
    engine.processing_mode = mode


def _apply_chain(modules: list[dict], sample_rate: int, channels: int) -> None:
    """Replace the working chain, after every value has been checked."""
    from aurcore.api import create_engine

    engine = create_engine(sample_rate, channels)
    # The chain starts in the mode of the session: the mode belongs to the
    # chain and not to a step, so a new chain keeps it.
    engine.processing_mode = _STATE.mode
    for entry in modules:
        handle = engine.add_module(entry["code"])
        for name, value in entry["checked"].items():
            engine.set_param(handle, name, value)
    # A parameter called "Processing Mode" is a parameter of a module, and
    # setting it moves the mode of the chain: the mode the chain ended in is
    # the mode the state keeps, and every module of the chain is given it.
    mode = engine.processing_mode
    _force_mode(engine, mode)
    _STATE.mode = mode
    _STATE.engine = engine
    _STATE.sample_rate = sample_rate
    _STATE.channels = channels


# ---------------------------------------------------------------------------
# the corpus
# ---------------------------------------------------------------------------
@dataclass
class _Instruments:
    """The measurement instruments of the project, loaded once."""

    corpus: Any
    quality: Any
    voice: Any
    survey: Any


_INSTRUMENTS: _Instruments | None = None


def _load_by_path(name: str, path: Path):
    """Load a module of the tree by path, under a name of its own."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _instruments() -> _Instruments:
    """The measurement instruments of the project.

    They are the instruments of the tree itself: the corpus helper, the
    quality instrument, the voice instrument and the survey.  They are
    loaded by path, under names of their own, so that the tree's own
    modules cannot stand in for one another.
    """
    global _INSTRUMENTS
    if _INSTRUMENTS is not None:
        return _INSTRUMENTS
    root = _repo_root()
    for path in (root, root / "corpus", root / "qa" / "quality"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    corpus = _load_by_path("aurmcp_corpus_measure", root / "corpus" / "measure.py")
    quality = _load_by_path(
        "aurmcp_quality_measurement", root / "qa" / "quality" / "measurement.py"
    )
    voice = _load_by_path(
        "aurmcp_qualityvoice_harness", root / "qa" / "qualityvoice" / "harness.py"
    )
    survey = _load_by_path("aurmcp_survey_harness", root / "qa" / "survey" / "harness.py")
    _INSTRUMENTS = _Instruments(corpus=corpus, quality=quality, voice=voice, survey=survey)
    return _INSTRUMENTS


def _manifest() -> list[dict]:
    """The manifest of the corpus."""
    root = _repo_root()
    path = root / "corpus" / "manifest.json"
    if not path.is_file():
        raise ToolError(
            "the corpus manifest is not in the tree (corpus/manifest.json); "
            "there is no corpus to work with in this checkout."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ToolError(f"the corpus manifest cannot be read: {error}") from None
    return list(data)


def _case_ids() -> list[str]:
    """The ids of the corpus cases."""
    return [str(entry.get("id", "")) for entry in _manifest()]


def _require_case(case_id: str) -> dict:
    """The manifest entry of a case, or a refusal that names the cases."""
    for entry in _manifest():
        if str(entry.get("id")) == str(case_id):
            return entry
    ids = _case_ids()
    raise ToolError(
        f"no corpus case named {case_id!r}; the corpus holds {len(ids)} cases: "
        + ", ".join(ids)
        + ". Call list_corpus_cases to see what each of them carries."
    )


def _clip(case_id: str) -> tuple[np.ndarray, int]:
    """The audio of a corpus case, with its sample rate."""
    instruments = _instruments()
    try:
        data, rate = instruments.corpus.load_clip(str(case_id))
    except Exception as error:  # noqa: BLE001 - the file may be missing
        raise ToolError(
            f"the audio of the corpus case {case_id!r} cannot be read: {error}. "
            "The case is in the manifest, but its file is not in the tree."
        ) from None
    return np.asarray(data, dtype=np.float64), int(rate)


def _benchmark_cases() -> dict:
    """The cases of the benchmark, keyed by name.

    The benchmark knows which band of a clip carries the artefact and which
    partial carries the wanted content.  The file is optional: when it is
    not in the tree the ground truth is taken from the manifest alone.
    """
    path = _repo_root() / "qa" / "benchmark" / "cases.py"
    if not path.is_file():
        return {}
    try:
        module = _load_by_path("aurmcp_benchmark_cases", path)
    except Exception:  # noqa: BLE001 - the mapping is a convenience
        return {}
    return {str(case.name): case for case in getattr(module, "CASES", ())}


# ---------------------------------------------------------------------------
# the render
# ---------------------------------------------------------------------------
def _render_buffer(engine, x: np.ndarray, block: int = RENDER_BLOCK,
                   compensate: bool = True) -> tuple[np.ndarray, list[str]]:
    """Run a chain over a buffer, the way the render route of the server does.

    A module that raises while processing does not take the whole render
    down: the block is passed through unchanged and the failure is reported
    as a warning.  A module that looks ahead delays the signal, so the
    render is fed the material and the silence of the delay, and the silence
    the delay puts in front of the answer is dropped afterwards.
    """
    total = int(x.shape[0])
    channels = int(x.shape[1])
    latency = int(engine.latency_samples)
    for step in engine.chain:
        setter = getattr(step.module, "set_material_length", None)
        if setter is not None:
            setter(total)
    padded = x
    if latency > 0:
        padded = np.concatenate(
            [x, np.zeros((latency, channels), dtype=np.float64)], axis=0
        )
    out = np.empty((padded.shape[0], channels), dtype=np.float64)
    warnings: list[str] = []
    for start in range(0, padded.shape[0], max(1, int(block))):
        chunk = padded[start : start + block]
        try:
            processed = engine.process(chunk)
        except Exception as exc:  # noqa: BLE001 - a broken module is reported
            warnings.append(f"block at {start}: {type(exc).__name__}: {exc}")
            processed = chunk
        if processed is None:
            processed = chunk
        processed = np.asarray(processed, dtype=np.float64)
        if processed.ndim == 1:
            processed = processed[:, None]
        if processed.shape[1] != channels:
            processed = _fit_channels(processed, channels)
        length = min(chunk.shape[0], processed.shape[0])
        out[start : start + length] = processed[:length]
        if length < chunk.shape[0]:
            out[start + length : start + chunk.shape[0]] = chunk[length:]
    if compensate and latency > 0:
        out = out[latency : latency + total]
    return out, warnings


def _fit_channels(arr: np.ndarray, channels: int) -> np.ndarray:
    """Force an array to a given channel count."""
    have = int(arr.shape[1])
    if have == channels:
        return arr
    if have > channels:
        return np.ascontiguousarray(arr[:, :channels])
    pad = np.zeros((arr.shape[0], channels - have), dtype=np.float64)
    return np.ascontiguousarray(np.concatenate([arr, pad], axis=1))


def _slice(x: np.ndarray, sample_rate: int, start: float, end: float) -> np.ndarray:
    """A stretch of a buffer, in seconds.  An ``end`` of zero means the end."""
    total = int(x.shape[0])
    if total == 0:
        return x
    first = max(0, min(total, int(round(float(start) * sample_rate))))
    last = total
    if float(end) > float(start):
        last = max(first, min(total, int(round(float(end) * sample_rate))))
    return x[first:last]


# ---------------------------------------------------------------------------
# the counting of transforms
# ---------------------------------------------------------------------------
class _TransformCounter:
    """Count the transforms a render performs.

    The count is a count, not a time: the number of transforms does not move
    with the load of the machine.  A transform of ``n`` points is worth
    ``n * log2(n) / 10240`` units, and one 1024 point transform is worth one
    unit.  This is the rule of the benchmark harness of the project.
    """

    def __init__(self) -> None:
        self.work = 0.0
        self.calls = 0
        self._saved: tuple = ()

    def __enter__(self) -> "_TransformCounter":
        self._saved = (np.fft.rfft, np.fft.irfft)
        counter = self

        def rfft(x, n=None, axis=-1):
            arr = np.asarray(x)
            length = int(n) if n else int(arr.shape[axis])
            counter._count(length, _batches(arr.shape, axis))
            return counter._saved[0](x, n=n, axis=axis)

        def irfft(x, n=None, axis=-1):
            arr = np.asarray(x)
            length = int(n) if n else 2 * (int(arr.shape[axis]) - 1)
            counter._count(length, _batches(arr.shape, axis))
            return counter._saved[1](x, n=n, axis=axis)

        np.fft.rfft = rfft  # type: ignore[assignment]
        np.fft.irfft = irfft  # type: ignore[assignment]
        return self

    def __exit__(self, *exc: Any) -> None:
        np.fft.rfft = self._saved[0]  # type: ignore[assignment]
        np.fft.irfft = self._saved[1]  # type: ignore[assignment]

    def _count(self, length: int, batches: int = 1) -> None:
        length = max(int(length), 1)
        count = max(int(batches), 1)
        self.calls += count
        self.work += count * length * math.log2(length) / 10240.0

    @property
    def equivalents(self) -> float:
        """The work of the render, in 1024 point transforms."""
        return self.work


def _batches(shape: Sequence[int], axis: int) -> int:
    """How many one dimensional transforms a call over ``shape`` performs."""
    axis = int(axis)
    if axis < 0:
        axis += len(shape)
    count = 1
    for index, size in enumerate(shape):
        if index != axis:
            count *= int(size)
    return max(count, 1)


# ---------------------------------------------------------------------------
# the tools
# ---------------------------------------------------------------------------
def _tool_list_modules(arguments: dict) -> dict:
    """The catalogue of the modules."""
    category = arguments.get("category")
    edition = arguments.get("edition")
    query = str(arguments.get("query") or "").strip().lower()
    infos = _module_infos()
    categories = sorted({info.category for info in infos})
    editions = sorted({info.edition for info in infos})
    picked = []
    for info in infos:
        if category and info.category != category:
            continue
        if edition and info.edition != edition:
            continue
        if query:
            haystack = f"{info.code} {info.display_name} {info.category}".lower()
            if query not in haystack:
                continue
        picked.append(
            {
                "code": info.code,
                "display_name": info.display_name,
                "category": info.category,
                "edition": info.edition,
                "parameter_count": len(info.params),
                "learn": bool(info.learn),
                "analysis": bool(info.analysis),
                "model": bool(info.ml),
            }
        )
    return {
        "count": len(picked),
        "total_modules": len(infos),
        "categories": categories,
        "editions": editions,
        "modules": picked,
    }


def _tool_describe_module(arguments: dict) -> dict:
    """One module in full."""
    code = str(arguments.get("module") or "").strip()
    if not code:
        raise ToolError(
            "no module named; send the code of a module, for example "
            "'AUR_DEHUM'. Call list_modules for the whole catalogue."
        )
    info = _require_module(code)
    parameters = _param_dicts(code)
    return {
        "code": info.code,
        "display_name": info.display_name,
        "category": info.category,
        "edition": info.edition,
        "element": info.element,
        "description": info.description,
        "learn": bool(info.learn),
        "analysis": bool(info.analysis),
        "model": bool(info.ml),
        "parameter_count": len(parameters),
        "parameters": parameters,
    }


def _tool_set_chain(arguments: dict) -> dict:
    """Replace the working chain."""
    modules = arguments.get("modules")
    if not isinstance(modules, list) or not modules:
        raise ToolError(
            "the chain is a list of steps and the list is empty or missing. Send "
            "one step per module, in signal order: "
            '[{"code": "AUR_DEHUM", "params": {"Base Frequency": 50.0}}].'
        )
    sample_rate = int(arguments.get("sample_rate") or DEFAULT_SAMPLE_RATE)
    channels = int(arguments.get("channels") or DEFAULT_CHANNELS)
    if sample_rate <= 0 or channels <= 0:
        raise ToolError(
            "the sample rate and the channel count must be positive; "
            f"got {sample_rate} and {channels}."
        )
    checked: list[dict] = []
    for index, entry in enumerate(modules):
        if not isinstance(entry, dict):
            raise ToolError(
                f"step {index} is not an object; every step is "
                '{"code": "<module code>", "params": {"<parameter>": <value>}}.'
            )
        code = str(entry.get("code") or "").strip()
        if not code:
            raise ToolError(
                f"step {index} has no module code; every step names the module it "
                "holds, for example 'AUR_DEHUM'. Call list_modules for the codes."
            )
        _require_module(code)
        checked.append(
            {
                "code": code,
                "checked": _check_params(code, entry.get("params") or {}),
            }
        )
    with _STATE.lock:
        _apply_chain(checked, sample_rate, channels)
        return _chain_summary()


def _tool_get_chain(arguments: dict) -> dict:
    """The chain as it stands."""
    include = arguments.get("include_parameters")
    include_parameters = True if include is None else bool(include)
    with _STATE.lock:
        return _chain_summary(include_parameters=include_parameters)


def _tool_set_parameter(arguments: dict) -> dict:
    """Change one parameter of one step."""
    if "step" not in arguments:
        raise ToolError(
            "no step named; send the index of the step in the chain, counted from "
            "0. Call get_chain to see the steps."
        )
    try:
        step_index = int(arguments["step"])
    except (TypeError, ValueError):
        raise ToolError(
            f"the step must be a whole number, not {arguments.get('step')!r}."
        ) from None
    name = str(arguments.get("name") or "").strip()
    if not name:
        raise ToolError(
            "no parameter named; send the name of the parameter, for example "
            "'Base Frequency'. Call describe_module for the names a module has."
        )
    if "value" not in arguments:
        raise ToolError(
            "no value named; send the value of the parameter in its natural unit "
            "(dB, Hz, ms, %), as a number, a string for a choice, or true/false "
            "for a switch."
        )
    with _STATE.lock:
        engine = _engine()
        if not (0 <= step_index < len(engine.chain)):
            raise ToolError(
                f"the chain holds {len(engine.chain)} step(s), so the step "
                f"{step_index} does not exist; the steps are counted from 0. "
                "Call get_chain to see them."
            )
        step = engine.chain[step_index]
        code = step.module.code
        canonical = step.module.params.canonical(name)
        if canonical is None:
            names = ", ".join(desc.name for desc in step.module.params.descs())
            raise ToolError(
                f"{code} has no parameter {name!r}; its parameters are: {names}. "
                "Call describe_module to see what each of them does."
            )
        desc = step.module.params.desc(canonical).to_dict()
        value = _coerce_value(desc, arguments["value"], code, name)
        engine.set_param(step_index, canonical, value)
        if canonical == PROCESSING_MODE_PARAMETER:
            # The mode is a parameter of a module, and setting it moves the
            # mode of the chain: every module of the chain is given it.
            _force_mode(engine, engine.processing_mode)
        _STATE.mode = engine.processing_mode
        summary = _chain_summary(include_parameters=False)
        return {
            "step": step_index,
            "code": code,
            "name": canonical,
            "value": step.module.params.get(canonical),
            "default": desc.get("default"),
            "unit": desc.get("unit", ""),
            "processing_mode": engine.processing_mode,
            "latency_samples": summary["latency_samples"],
            "latency_ms": summary["latency_ms"],
            "chain": summary,
        }


def _tool_set_processing_mode(arguments: dict) -> dict:
    """Put the chain into one of the two processing modes."""
    mode = str(arguments.get("mode") or "").strip().lower()
    if not mode:
        raise ToolError(
            "no mode named; the two modes of the product are 'realtime' and "
            "'offline'."
        )
    if mode not in PROCESSING_MODES:
        raise ToolError(
            f"unknown processing mode {arguments.get('mode')!r}; the modes are "
            + ", ".join(PROCESSING_MODES)
            + ". 'realtime' keeps every module inside the budget of a real time "
            "block; 'offline' lets a module use the window it likes best and "
            "costs a longer look-ahead."
        )
    with _STATE.lock:
        engine = _engine()
        previous = engine.processing_mode
        engine.processing_mode = mode
        _STATE.mode = engine.processing_mode
        summary = _chain_summary(include_parameters=False)
        return {
            "processing_mode": engine.processing_mode,
            "previous_mode": previous,
            "latency_samples": summary["latency_samples"],
            "latency_ms": summary["latency_ms"],
            "tail_samples": summary["tail_samples"],
            "note": (
                "the offline mode lets a module use its best configuration and "
                "costs a longer look-ahead; the latency of the chain carries it"
            ),
        }


def _tool_render(arguments: dict) -> dict:
    """Render a corpus case through the chain and store the WAV."""
    case_id = str(arguments.get("case") or "").strip()
    if not case_id:
        raise ToolError(
            "no case named; the render works on a case of the corpus, for example "
            "'hum50'. Call list_corpus_cases for the cases."
        )
    _require_case(case_id)
    start = float(arguments.get("start") or 0.0)
    end = float(arguments.get("end") or 0.0)
    learned = str(arguments.get("learned") or "").strip()
    with _STATE.lock:
        if not len(_engine().chain):
            raise ToolError(
                "the chain is empty; call set_chain first with the modules the "
                "render should run."
            )
        mode = _engine().processing_mode
        data, rate = _clip(case_id)
        samples = _slice(data, rate, start, end)
        engine = _snapshot_engine()
        rendered, warnings = _render_buffer(engine, samples)
        learned_model = ""
        if learned:
            # The learned restoration runs after the chain, over the whole
            # file: the chain is the hand written restoration and the model
            # refines what it produced.
            from aurcore import models

            try:
                rendered, model = models.run_named(
                    learned, rendered, sample_rate=int(rate)
                )
            except models.ModelError as error:
                raise ToolError(
                    f"the model {learned!r} cannot be run: {error}"
                ) from error
            learned_model = model.manifest.name
        path = _store_render(rendered, rate, case_id)
    frames = int(rendered.shape[0])
    return {
        "case": case_id,
        "asset_id": path["asset_id"],
        "path": path["path"],
        "relative_path": path["relative_path"],
        "url": path["url"],
        "duration_s": round(frames / float(rate), 6),
        "frames": frames,
        "sample_rate": int(rate),
        "channels": int(rendered.shape[1]),
        "processing_mode": mode,
        "latency_samples": int(engine.latency_samples),
        "warnings": warnings,
        "learned_model": learned_model,
        "note": (
            "the file is in the product's own asset store; the render ran in the "
            "processing mode the chain was in"
            + (
                "; the learned restoration ran over the whole file afterwards"
                if learned_model
                else ""
            )
        ),
    }


#: The asset store of the product, built once.  Every render goes into it,
#: and the store keeps its own budgets: the least recently used asset is
#: evicted and its file is deleted when a budget is reached.
_STORE: Any = None


def _store():
    """The asset store of the product, built on the first render."""
    global _STORE
    if _STORE is None:
        from aurserver.assets import AssetStore

        _STORE = AssetStore(_repo_root() / "var" / "assets")
    return _STORE


def _store_render(rendered: np.ndarray, rate: int, case_id: str) -> dict:
    """Write a render into the product's own asset store."""
    root = _repo_root()
    store = _store()
    asset_id = store.put(
        rendered,
        int(rate),
        int(rendered.shape[1]),
        f"render of {case_id}",
        "wav",
        prefix="out",
    )
    path = store.path(asset_id)
    try:
        relative = str(path.relative_to(root))
    except ValueError:  # pragma: no cover - the store is inside the tree
        relative = str(path)
    return {
        "asset_id": asset_id,
        "path": str(path),
        "relative_path": relative.replace("\\", "/"),
        "url": f"/api/audio/{asset_id}",
    }


def _artefact_bands(entry: dict, wanted: str | None) -> list[dict]:
    """The bands of a case that carry the artefact."""
    annotations = dict(entry.get("annotations", {}))
    bands = {str(band.get("name")): band for band in annotations.get("bands", [])}
    names: list[str] = []
    source = ""
    if wanted:
        names = [part.strip() for part in str(wanted).split(",") if part.strip()]
        source = "the band the caller named"
    else:
        case = _benchmark_cases().get(str(entry.get("id")))
        if case is not None and getattr(case, "artefact", ()):
            names = [str(name) for name in case.artefact]
            source = "the mapping of the benchmark (qa/benchmark/cases.py)"
    if not names:
        raise ToolError(
            f"the corpus does not say which band of the case "
            f"{entry.get('id')!r} carries the artefact. Name it with the 'band' "
            "argument, as a band name of the manifest or as a list of names "
            "separated by commas. The bands of the case are: "
            + ", ".join(f"{name} ({band.get('band_hz')})" for name, band in bands.items())
            + "."
        )
    picked = []
    for name in names:
        band = bands.get(name)
        if band is None:
            raise ToolError(
                f"the case {entry.get('id')!r} has no band named {name!r}; its "
                "bands are: "
                + ", ".join(f"{key} ({value.get('band_hz')})" for key, value in bands.items())
                + "."
            )
        edges = band.get("band_hz") or [0.0, 0.0]
        picked.append(
            {
                "name": name,
                "low_hz": float(edges[0]),
                "high_hz": float(edges[1]),
                "mix": str(band.get("mix", "mono")),
                "level_dbfs": band.get("level_dbfs"),
            }
        )
    picked[0]["source"] = source
    return picked


def _wanted_partials(entry: dict, bands: list[dict], wanted: str | None) -> list[dict]:
    """The partials of a case that carry wanted content."""
    annotations = dict(entry.get("annotations", {}))
    frequencies = list(annotations.get("frequencies", []))
    if wanted not in (None, ""):
        try:
            hertz = float(wanted)
        except (TypeError, ValueError):
            raise ToolError(
                f"the wanted partial is a frequency in hertz, not {wanted!r}."
            ) from None
        return [{"name": "the partial the caller named", "hz": hertz, "mix": "mono"}]
    names: list[str] = []
    case = _benchmark_cases().get(str(entry.get("id")))
    if case is not None and getattr(case, "wanted", ()):
        names = [str(name) for name in case.wanted]
    else:
        # Every partial of the manifest that does not lie inside a band that
        # carries the artefact is content that has to survive.
        for item in frequencies:
            hertz = item.get("hz")
            if hertz is None:
                continue
            inside = any(
                band["low_hz"] <= float(hertz) <= band["high_hz"] for band in bands
            )
            if not inside:
                names.append(str(item.get("name")))
    picked = []
    for name in names:
        for item in frequencies:
            if str(item.get("name")) != name:
                continue
            if item.get("hz") is None:
                continue
            picked.append(
                {
                    "name": name,
                    "hz": float(item["hz"]),
                    "mix": str(item.get("mix", "mono")),
                    "tolerance_hz": item.get("tolerance_hz"),
                }
            )
    return picked


def _tool_measure(arguments: dict) -> dict:
    """Measure the chain against the rules of the product."""
    case_id = str(arguments.get("case") or "").strip()
    if not case_id:
        raise ToolError(
            "no case named; the measurement works on a case of the corpus, for "
            "example 'hum50'. Call list_corpus_cases for the cases."
        )
    entry = _require_case(case_id)
    bands = _artefact_bands(entry, arguments.get("band"))
    partials = _wanted_partials(entry, bands, arguments.get("partial"))
    instruments = _instruments()
    quality = instruments.quality
    corpus = instruments.corpus

    with _STATE.lock:
        if not len(_engine().chain):
            raise ToolError(
                "the chain is empty; call set_chain first with the modules the "
                "measurement should run."
            )
        mode = _engine().processing_mode
        data, rate = _clip(case_id)
        engine = _snapshot_engine()
        declared_latency = int(engine.latency_samples)

    # -- the render, once aligned and once raw --------------------------------
    rendered, warnings = _render_buffer(engine, data)
    raw, _raw_warnings = _render_buffer(_snapshot_engine(), data, compensate=False)

    def mix_of(buffer: np.ndarray, mix: str) -> np.ndarray:
        """The signal of a mix of the manifest: mono, mid or side."""
        return corpus.mix_signal(buffer, mix)

    # -- the delay the chain really adds -------------------------------------
    mono_in = mix_of(data, "mono")
    mono_raw = mix_of(raw, "mono")
    measured_latency = int(quality.estimate_latency(mono_in, mono_raw, rate))

    # -- what is removed -----------------------------------------------------
    power_in = 0.0
    power_out = 0.0
    frames_in = None
    frames_out = None
    survey_artefact = None
    corpus_artefact = None
    for band in bands:
        before = mix_of(data, band["mix"])
        after = mix_of(rendered, band["mix"])
        power_in += quality.band_power(before, band["low_hz"], band["high_hz"], rate)
        power_out += quality.band_power(after, band["low_hz"], band["high_hz"], rate)
        inside = quality.band_power_frames(
            before, band["low_hz"], band["high_hz"], rate
        )
        outside = quality.band_power_frames(
            after, band["low_hz"], band["high_hz"], rate
        )
        frames_in = inside if frames_in is None else frames_in + inside
        frames_out = outside if frames_out is None else frames_out + outside
        region = instruments.survey.Region(
            lo_hz=band["low_hz"],
            hi_hz=band["high_hz"],
            label=band["name"],
        )
        level_in = instruments.survey.region_level_db(data, rate, region)
        level_out = instruments.survey.region_level_db(rendered, rate, region)
        change = instruments.survey.level_change_db(level_in, level_out)
        survey_artefact = change if survey_artefact is None else min(
            survey_artefact, change
        )
        # The ruler of the corpus: the level the manifest itself records.
        corpus_in = corpus.band_energy_db(
            data, rate, band["low_hz"], band["high_hz"], mix=band["mix"]
        )
        corpus_out = corpus.band_energy_db(
            rendered, rate, band["low_hz"], band["high_hz"], mix=band["mix"]
        )
        corpus_change = corpus_out - corpus_in
        corpus_artefact = (
            corpus_change if corpus_artefact is None else min(corpus_artefact, corpus_change)
        )
    removed_db = 10.0 * math.log10(max(power_in, 1e-30) / max(power_out, 1e-30))
    silenced = power_out <= 1e-29

    # -- what is kept --------------------------------------------------------
    shifts: list[dict] = []
    kept_db = 0.0
    survey_wanted = None
    corpus_wanted = None
    for partial in partials:
        before = mix_of(data, partial["mix"])
        after = mix_of(rendered, partial["mix"])
        level_in = quality.tone_level(before, partial["hz"], rate)
        level_out = quality.tone_level(after, partial["hz"], rate)
        shift = float(level_out - level_in)
        shifts.append(
            {
                "name": partial["name"],
                "hz": partial["hz"],
                "level_in_db": round(level_in, 4),
                "level_out_db": round(level_out, 4),
                "shift_db": round(shift, 4),
            }
        )
        kept_db = max(kept_db, abs(shift))
        region = instruments.survey.Region(
            lo_hz=max(1.0, partial["hz"] - 30.0),
            hi_hz=partial["hz"] + 30.0,
            label=partial["name"],
        )
        wanted_in = instruments.survey.region_level_db(data, rate, region)
        wanted_out = instruments.survey.region_level_db(rendered, rate, region)
        change = instruments.survey.level_change_db(wanted_in, wanted_out)
        survey_wanted = change if survey_wanted is None else max(survey_wanted, change)
        low = max(1.0, partial["hz"] - 30.0)
        high = partial["hz"] + 30.0
        corpus_in = corpus.band_energy_db(data, rate, low, high, mix=partial["mix"])
        corpus_out = corpus.band_energy_db(rendered, rate, low, high, mix=partial["mix"])
        corpus_change = corpus_out - corpus_in
        corpus_wanted = (
            corpus_change if corpus_wanted is None else max(corpus_wanted, corpus_change)
        )

    # -- the gain curve ------------------------------------------------------
    gain_var_db2 = None
    count = min(len(frames_in), len(frames_out)) if frames_in is not None else 0
    if count >= 3:
        ratio = np.maximum(frames_out[:count], 1e-30) / np.maximum(frames_in[:count], 1e-30)
        curve = 10.0 * np.log10(ratio)[int(count * 0.25) :]
        if curve.size >= 3:
            gain_var_db2 = float(np.var(curve))

    # -- the chatter of the gain ---------------------------------------------
    chatter_db = None
    if partials:
        band = bands[0]
        before = mix_of(data, band["mix"])
        after = mix_of(rendered, band["mix"])
        content = (max(1.0, partials[0]["hz"] - 30.0), partials[0]["hz"] + 30.0)
        chatter = instruments.voice.measure_chatter(
            before, after, (band["low_hz"], band["high_hz"]), content
        )
        chatter_db = float(chatter["chatter_db"])

    # -- the cost of one 64 sample block -------------------------------------
    # The cost of the reference is the cost of one 64 sample block, so ours
    # is the same figure: the chain is driven block by block, the way a host
    # drives it, and the transforms of the run are divided by the number of
    # blocks.  A module with a fixed analysis window does the same work in
    # every block, and a whole clip render would hide that.
    counter_engine = _snapshot_engine()
    blocks = 0
    with _TransformCounter() as counter:
        for start in range(0, int(data.shape[0]), COST_BLOCK):
            counter_engine.process(data[start : start + COST_BLOCK])
            blocks += 1
    cost = float(counter.equivalents) / float(max(blocks, 1))

    # -- the block coherence -------------------------------------------------
    block_coherence = None
    coherence_note = ""
    try:
        whole_engine = _snapshot_engine()
        whole, _warnings = _render_buffer(whole_engine, data, block=len(data) or 1)
        blocked_engine = _snapshot_engine()
        blocked, _warnings = _render_buffer(blocked_engine, data, block=COHERENCE_BLOCK)
        block_coherence = float(
            quality.block_coherence_db(mix_of(whole, "mono"), mix_of(blocked, "mono"))
        )
    except Exception as exc:  # noqa: BLE001 - a module may refuse a big block
        coherence_note = f"the block coherence could not be measured: {exc}"

    method = {
        "removed_db": (
            "the power of the artefact band before and after the render, the "
            "quality instrument (qa/quality/measurement.py, band_power), "
            "10*log10(power in / power out); the band is measured in the mix "
            "the manifest names for it"
        ),
        "kept_db": (
            "the level of the wanted partial before and after the render, the "
            "quality instrument (qa/quality/measurement.py, tone_level); the "
            "largest movement of the wanted partials is reported"
        ),
        "gain_var_db2": (
            "the temporal variance of the effective gain curve in the artefact "
            "band, the quality instrument (qa/quality/measurement.py, "
            "band_power_frames); the first quarter of the frames is skipped"
        ),
        "chatter_db": (
            "the roughness of the gain the chain applies in the artefact band, "
            "the voice instrument (qa/qualityvoice/harness.py, measure_chatter), "
            "with the wanted content taken out of the measurement"
        ),
        "survey_artefact_change_db": (
            "the change of the level of the artefact band, the survey instrument "
            "(qa/survey/harness.py, region_level_db and level_change_db); a "
            "negative number means the band got quieter"
        ),
        "survey_wanted_change_db": (
            "the change of the level of a 60 Hz wide band around the wanted "
            "partial, the survey instrument (qa/survey/harness.py, "
            "region_level_db and level_change_db)"
        ),
        "corpus_artefact_change_db": (
            "the change of the level of the artefact band in the ruler of the "
            "corpus (corpus/measure.py, band_energy_db), the ruler the ground "
            "truth of the manifest is written in; a negative number means the "
            "band got quieter"
        ),
        "corpus_wanted_change_db": (
            "the change of the level of a 60 Hz wide band around the wanted "
            "partial in the ruler of the corpus (corpus/measure.py, "
            "band_energy_db)"
        ),
        "cost_transform_eq_per_64_block": (
            "the transforms of the run were counted, not timed: a transform of "
            "n points is worth n*log2(n)/10240 units of a 1024 point transform "
            "(the rule of qa/benchmark/harness.py); the chain is driven in "
            "blocks of 64 samples and the count of the run is divided by the "
            "number of blocks, so the figure is the cost of one 64 sample "
            "block"
        ),
        "declared_latency_samples": (
            "the latency the chain declares: the sum of the latencies of its "
            "modules, in the samples of the engine's rate (aurcore/api.py)"
        ),
        "measured_latency_samples": (
            "the delay between the input and the raw output of the chain, the "
            "quality instrument (qa/quality/measurement.py, estimate_latency)"
        ),
        "block_coherence_db": (
            "the RMS difference between the render in one piece and the render "
            "in blocks of 4096 samples, the quality instrument "
            "(qa/quality/measurement.py, block_coherence_db)"
        ),
    }

    answer = {
        "case": case_id,
        "chain": [step.module.code for step in engine.chain],
        "processing_mode": mode,
        "sample_rate": int(rate),
        "artefact_bands": bands,
        "wanted_partials": partials,
        "removed_db": round(removed_db, 4),
        "kept_db": round(kept_db, 4) if partials else None,
        "partial_shifts": shifts,
        "gain_var_db2": None if gain_var_db2 is None else round(gain_var_db2, 6),
        "chatter_db": None if chatter_db is None else round(chatter_db, 4),
        "survey_artefact_change_db": (
            None if survey_artefact is None else round(survey_artefact, 4)
        ),
        "survey_wanted_change_db": (
            None if survey_wanted is None else round(survey_wanted, 4)
        ),
        "corpus_artefact_change_db": (
            None if corpus_artefact is None else round(corpus_artefact, 4)
        ),
        "corpus_wanted_change_db": (
            None if corpus_wanted is None else round(corpus_wanted, 4)
        ),
        "cost_transform_eq_per_64_block": round(cost, 4),
        "declared_latency_samples": declared_latency,
        "measured_latency_samples": measured_latency,
        "block_coherence_db": (
            None if block_coherence is None else round(block_coherence, 4)
        ),
        "method": method,
        "warnings": warnings,
    }
    if coherence_note:
        answer["note"] = coherence_note
    if silenced:
        answer["note"] = (
            "the artefact band was silenced completely, so the removal figure "
            "is the floor of the instrument and not a finite measurement"
        )
    if not partials:
        answer["note"] = (
            "the case names no wanted partial, so the movement of the wanted "
            "content was not measured"
        )
    return answer


def _tool_list_corpus_cases(arguments: dict) -> dict:
    """The cases of the corpus, with their ground truth."""
    tag = str(arguments.get("tag") or "").strip().lower()
    only_benchmark = bool(arguments.get("benchmark_only"))
    benchmark = _benchmark_cases()
    cases = []
    for entry in _manifest():
        annotations = dict(entry.get("annotations", {}))
        tags = [str(value).lower() for value in entry.get("tags", [])]
        if tag and tag not in tags:
            continue
        if only_benchmark and str(entry.get("id")) not in benchmark:
            continue
        bands = [
            {
                "name": str(band.get("name")),
                "band_hz": list(band.get("band_hz", [])),
                "region_s": list(band.get("region_s", [])),
                "mix": str(band.get("mix", "mono")),
                "level_dbfs": band.get("level_dbfs"),
            }
            for band in annotations.get("bands", [])
        ]
        frequencies = [
            {
                "name": str(item.get("name")),
                "hz": item.get("hz"),
                "region_s": list(item.get("region_s", [])),
                "mix": str(item.get("mix", "mono")),
                "tolerance_hz": item.get("tolerance_hz"),
            }
            for item in annotations.get("frequencies", [])
        ]
        events = [
            {
                "type": str(event.get("type")),
                "start_s": event.get("start_s"),
                "end_s": event.get("end_s"),
                "label": event.get("label"),
            }
            for event in annotations.get("events", [])
        ]
        regions = {
            str(name): list(value) for name, value in dict(annotations.get("regions", {})).items()
        }
        case = benchmark.get(str(entry.get("id")))
        cases.append(
            {
                "id": str(entry.get("id")),
                "file": str(entry.get("file")),
                "duration_s": entry.get("duration_s"),
                "sample_rate": entry.get("sample_rate"),
                "channels": entry.get("channels"),
                "tags": list(entry.get("tags", [])),
                "benchmark_case": case is not None,
                "artefact_bands": list(getattr(case, "artefact", ())) if case else [],
                "wanted_partials": list(getattr(case, "wanted", ())) if case else [],
                "ground_truth": {
                    "bands": bands,
                    "frequencies": frequencies,
                    "events": events,
                    "regions": regions,
                    "levels": dict(annotations.get("levels", {})),
                    "derived": list(annotations.get("derived", [])),
                },
            }
        )
    return {"count": len(cases), "cases": cases}


def _tool_list_models(arguments: dict) -> dict:
    """The shipped models."""
    from aurcore import models

    problem = str(arguments.get("problem") or "").strip().lower()
    budgets = _model_budgets()
    library = models.open_library()
    entries = []
    for info in library.list():
        if problem and str(info.problem).lower() != problem:
            continue
        budget = budgets.get(info.name)
        entries.append(
            {
                "name": info.name,
                "version": info.version,
                "trained_on": info.trained_on,
                "problem": info.problem,
                "task": info.task,
                "variant": info.variant,
                "path": info.path,
                "kind": "offline",
                "cost_per_64_sample_block": (
                    None
                    if budget is None
                    else {
                        "transform_equivalents": budget["transform_equivalents"],
                        "reference_microseconds": budget["reference_microseconds"],
                        "share_of_the_realtime_budget_percent": budget["share_percent"],
                        "measured_on": budget["measured_on"],
                    }
                ),
                "cost_note": (
                    "no cost budget is recorded for this model"
                    if budget is None
                    else budget["note"]
                ),
            }
        )
    return {
        "count": len(entries),
        "rule": (
            "a model belongs to the offline path: the real time path never loads "
            "one, the hand written path of every module stays the default"
        ),
        "models": entries,
    }


def _model_budgets() -> dict:
    """The recorded cost budgets of the shipped models."""
    path = _repo_root() / "qa" / "performance" / "model_budgets.py"
    if not path.is_file():
        return {}
    try:
        module = _load_by_path("aurmcp_model_budgets", path)
    except Exception:  # noqa: BLE001 - the budgets are optional
        return {}
    out: dict[str, dict] = {}
    for name, budget in dict(getattr(module, "MODEL_BUDGETS", {})).items():
        out[str(name)] = {
            "transform_equivalents": budget.measured.get(64, 0.0),
            "reference_microseconds": round(budget.measured_us, 4),
            "share_percent": round(budget.share, 4),
            "measured_on": (
                "the sweep of the project (qa/performance/sweep_models.py), "
                "counted as transform equivalents, 8 rounds of 10 s of audio, "
                "fastest round wins"
            ),
            "note": str(budget.note),
        }
    return out


def _tool_benchmark(arguments: dict) -> dict:
    """Run the benchmark and return the table."""
    root = _repo_root()
    runner = root / "qa" / "benchmark" / "run_benchmark.py"
    if not runner.is_file():
        raise ToolError(
            "the benchmark runner is not in the tree (qa/benchmark/"
            "run_benchmark.py); the benchmark cannot be run here."
        )
    cases = str(arguments.get("cases") or "").strip()
    started = time.monotonic()
    rows, text, code, command = _run_benchmark(runner, root, cases)
    elapsed = time.monotonic() - started
    behind = [row for row in rows if row.get("verdict") == "behind"]
    measured: list[str] = []
    for row in rows:
        name = str(row.get("case"))
        if name not in measured:
            measured.append(name)
    return {
        "cases": measured,
        "row_count": len(rows),
        "rows": rows,
        "table": text,
        "behind_count": len(behind),
        "exit_code": code,
        "command": command,
        "elapsed_s": round(elapsed, 3),
        "note": (
            "the benchmark is a reporting gate: a module that is behind a "
            "reference number is reported, not failed, because the reference "
            "was measured on another machine"
        ),
    }


def _run_benchmark(runner: Path, root: Path, cases: str) -> tuple[list[dict], str, int, str]:
    """Run the benchmark runner and read its table."""
    base = [sys.executable, str(runner)]
    if cases:
        base += ["--case", cases]
    with tempfile.TemporaryDirectory(prefix="auralis-benchmark-") as scratch:
        payload = Path(scratch) / "benchmark.json"
        command = base + ["--json", str(payload)]
        completed = subprocess.run(
            command,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        rows: list[dict] = []
        if payload.is_file():
            try:
                data = json.loads(payload.read_text(encoding="utf-8"))
                rows = list(data.get("rows") or [])
            except (OSError, ValueError):
                rows = []
        if not rows and not payload.is_file():
            # The runner did not write the payload: run it for the table only.
            command = base
            completed = subprocess.run(
                command,
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=1800,
                check=False,
            )
        text = completed.stdout or ""
        if completed.stderr:
            text += "\n" + completed.stderr
        printable = "python qa/benchmark/run_benchmark.py"
        if cases:
            printable += f" --case {cases}"
        return rows, text, int(completed.returncode), printable


# ---------------------------------------------------------------------------
# the schemas
# ---------------------------------------------------------------------------
_CODE_PROPERTY = {
    "type": "string",
    "description": (
        "the code of a module, as the catalogue gives it, for example "
        "'AUR_DEHUM'. The codes are listed by list_modules."
    ),
    "minLength": 1,
}

_STEP_SCHEMA = {
    "type": "object",
    "description": "one step of the chain: a module and the values of its parameters",
    "properties": {
        "code": _CODE_PROPERTY,
        "params": {
            "type": "object",
            "description": (
                "the values of the parameters of the module, keyed by parameter "
                "name, in natural units (dB, Hz, ms, %). A parameter the "
                "module does not have is refused by name; a value outside the "
                "range the module declares is refused with the range."
            ),
            "additionalProperties": True,
        },
    },
    "required": ["code"],
    "additionalProperties": False,
}


TOOLS: list[Tool] = [
    Tool(
        name="list_modules",
        description=(
            "List the catalogue of the modules: for every module its code, its "
            "display name, its category, its edition and how many parameters it "
            "has. This is the index of everything the other tools accept: "
            "set_chain and describe_module take a module code from this list. "
            "It does not list the parameters of a module (describe_module does "
            "that), it does not say what a module does to the audio (that is "
            "the help sentence of its parameters and the documents), and it "
            "does not change anything."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": (
                        "keep only the modules of this category: 'Repair', "
                        "'Utility', 'Measurement' or 'System'."
                    ),
                },
                "edition": {
                    "type": "string",
                    "description": (
                        "keep only the modules of this edition: 'standard' or "
                        "'advanced'."
                    ),
                },
                "query": {
                    "type": "string",
                    "description": (
                        "keep only the modules whose code, display name or "
                        "category carries this text, in any case."
                    ),
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        handler=_tool_list_modules,
    ),
    Tool(
        name="describe_module",
        description=(
            "Describe one module of the catalogue in full: every parameter with "
            "its name, its type, its default, its range, its unit, its "
            "choices, its group and its help sentence. Use it before set_chain "
            "or set_parameter, so that the values you send are inside the "
            "declared ranges. It does not change the chain, it does not run "
            "the module, and it does not say how the module sounds."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "module": {
                    **dict(_CODE_PROPERTY),
                    "description": (
                        "the code of the module to describe, for example "
                        "'AUR_DEHUM'. The codes are listed by list_modules."
                    ),
                },
            },
            "required": ["module"],
            "additionalProperties": False,
        },
        handler=_tool_describe_module,
    ),
    Tool(
        name="set_chain",
        description=(
            "Replace the working chain with the modules given, in signal order, "
            "each with its parameter values in natural units (dB, Hz, ms, %), "
            "and return the chain as it now stands. The chain is the working "
            "state of the session: the render and the measurement run it, and "
            "get_chain reads it back. An unknown module code, an unknown "
            "parameter name or a value outside the declared range is refused "
            "by name, and nothing is changed when the request is refused. It "
            "does not measure anything, it does not render audio, and it "
            "leaves the processing mode of the chain as it is."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "modules": {
                    "type": "array",
                    "description": (
                        "the steps of the chain, in signal order. Every step "
                        "names a module and may carry the values of its "
                        "parameters; a step without parameters keeps the "
                        "defaults of the module."
                    ),
                    "minItems": 1,
                    "items": _STEP_SCHEMA,
                },
                "sample_rate": {
                    "type": "integer",
                    "description": (
                        "the sample rate the chain runs at, in hertz. The "
                        "corpus is 48000, which is the default."
                    ),
                    "minimum": 1,
                },
                "channels": {
                    "type": "integer",
                    "description": (
                        "the number of channels the chain runs at. The corpus "
                        "is stereo, which is the default."
                    ),
                    "minimum": 1,
                },
            },
            "required": ["modules"],
            "additionalProperties": False,
        },
        handler=_tool_set_chain,
    ),
    Tool(
        name="get_chain",
        description=(
            "Read the working chain back: every step in signal order with its "
            "parameters and their current values, plus the summary of the "
            "chain (the processing mode, the declared latency, the tail and a "
            "line per module). Use it to confirm what set_chain, set_parameter "
            "and set_processing_mode did. It does not change anything, it "
            "does not run the chain over audio, and it does not measure the "
            "chain."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "include_parameters": {
                    "type": "boolean",
                    "description": (
                        "send the values of the parameters of every step. True "
                        "by default; false gives the summary of the chain "
                        "alone."
                    ),
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        handler=_tool_get_chain,
    ),
    Tool(
        name="set_parameter",
        description=(
            "Change one parameter of one step of the working chain and return "
            "the value the parameter really holds afterwards. The value is in "
            "natural units (dB, Hz, ms, %). An unknown step, an unknown "
            "parameter name or a value outside the declared range is refused "
            "and nothing changes. It does not change the processing mode "
            "(set_processing_mode does), it does not render, and it does not "
            "reset the other parameters of the step."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "step": {
                    "type": "integer",
                    "description": (
                        "the index of the step in the chain, counted from 0, in "
                        "signal order. The steps are listed by get_chain."
                    ),
                    "minimum": 0,
                },
                "name": {
                    "type": "string",
                    "description": (
                        "the name of the parameter, exactly as the module "
                        "publishes it, for example 'Base Frequency'. The names "
                        "of a module are listed by describe_module."
                    ),
                    "minLength": 1,
                },
                "value": {
                    "description": (
                        "the value of the parameter: a number for a numeric "
                        "parameter, one of the choices of the parameter for a "
                        "choice, true or false for a switch. The value is in "
                        "natural units (dB, Hz, ms, %) and it must be inside "
                        "the range the module declares."
                    ),
                    "oneOf": [
                        {"type": "number"},
                        {"type": "string"},
                        {"type": "boolean"},
                    ],
                },
            },
            "required": ["step", "name", "value"],
            "additionalProperties": False,
        },
        handler=_tool_set_parameter,
    ),
    Tool(
        name="set_processing_mode",
        description=(
            "Put the working chain into one of the two processing modes of the "
            "product: 'realtime', where every module has to keep inside the "
            "budget of a real time block, and 'offline', where a module may "
            "use the window it likes best and pays for it with a longer "
            "look-ahead. The mode belongs to the chain and not to a step, and "
            "it survives a set_chain. The answer carries the new mode and the "
            "latency the chain has in it. It does not change a parameter of a "
            "module, it does not render, and it does not switch a single "
            "module on its own."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "description": (
                        "the mode of the chain: 'realtime' or 'offline'. "
                        "'offline' gives every module its best configuration "
                        "and costs a longer look-ahead, which the answer "
                        "reports."
                    ),
                    "enum": list(PROCESSING_MODES),
                },
            },
            "required": ["mode"],
            "additionalProperties": False,
        },
        handler=_tool_set_processing_mode,
    ),
    Tool(
        name="render",
        description=(
            "Render a case of the corpus through the working chain and write "
            "the result into the product's own asset store as a WAV; the "
            "answer says where the file was written and how long it is. The "
            "render runs in the processing mode the chain is in, so call "
            "set_processing_mode first when the file render should use the "
            "best configuration of every module. With 'learned' it runs a model "
            "of the library over the rendered file afterwards: that model is an "
            "offline capability, so the render takes longer. It does not "
            "measure the result (measure does that), it does not change the "
            "chain, and it does not take audio that is not a case of the "
            "corpus."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "case": {
                    "type": "string",
                    "description": (
                        "the id of the corpus case to render, for example "
                        "'hum50'. The cases are listed by list_corpus_cases."
                    ),
                    "minLength": 1,
                },
                "start": {
                    "type": "number",
                    "description": (
                        "the start of the stretch to render, in seconds from "
                        "the start of the case. Zero, the default, is the "
                        "start of the case."
                    ),
                    "minimum": 0,
                },
                "end": {
                    "type": "number",
                    "description": (
                        "the end of the stretch to render, in seconds. Zero, "
                        "the default, is the end of the case."
                    ),
                    "minimum": 0,
                },
                "learned": {
                    "type": "string",
                    "description": (
                        "the name of a model of the library to run over the "
                        "rendered file, for example 'voice_sample_mask'. The "
                        "model is asked about the mix of the channels and its "
                        "gain is applied to every channel, so the image does "
                        "not move. It is an offline capability: it costs time "
                        "and it is not available in the real time path. The "
                        "models are listed by list_models."
                    ),
                },
            },
            "required": ["case"],
            "additionalProperties": False,
        },
        handler=_tool_render,
    ),
    Tool(
        name="measure",
        description=(
            "Measure the working chain against the rules of the product on a "
            "case of the corpus: how much of the artefact is removed, how far "
            "the wanted content moves, how much the gain curve breathes, how "
            "much the chain costs for one 64 sample block, the latency the "
            "chain declares and the latency that is measured, and how much "
            "the result depends on the block size. Every number carries the "
            "instrument that produced it, and what could not be measured is "
            "reported as such. It does not change the chain, it does not write "
            "a file, and it does not judge the chain against a quality score "
            "the project has not written a rule for."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "case": {
                    "type": "string",
                    "description": (
                        "the id of the corpus case to measure, for example "
                        "'hum50'. The cases are listed by list_corpus_cases."
                    ),
                    "minLength": 1,
                },
                "band": {
                    "type": "string",
                    "description": (
                        "the band that carries the artefact, as the name the "
                        "manifest gives it, for example 'hum'. Several bands "
                        "are separated by commas. When it is left out, the "
                        "mapping of the benchmark is used, and a case the "
                        "benchmark does not know is refused with the bands it "
                        "has."
                    ),
                },
                "partial": {
                    "type": "number",
                    "description": (
                        "the frequency in hertz of the partial that carries "
                        "the wanted content, for example 1000. When it is left "
                        "out, the partials of the manifest that lie outside "
                        "the artefact band are used."
                    ),
                },
            },
            "required": ["case"],
            "additionalProperties": False,
        },
        handler=_tool_measure,
    ),
    Tool(
        name="list_corpus_cases",
        description=(
            "List the cases of the corpus that can be rendered and measured: "
            "the clips with their length, their tags and their ground truth "
            "(the bands that carry the artefact, the partials that carry "
            "wanted content, the events, the regions and the levels the "
            "manifest records). Use it to pick the case for render and "
            "measure. It does not render, it does not measure, and it does not "
            "list audio that is not in the manifest."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "tag": {
                    "type": "string",
                    "description": (
                        "keep only the cases that carry this tag, in any case: "
                        "'hum', 'hiss', 'voice', 'denoise' and so on."
                    ),
                },
                "benchmark_only": {
                    "type": "boolean",
                    "description": (
                        "keep only the cases the benchmark measures. False by "
                        "default."
                    ),
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        handler=_tool_list_corpus_cases,
    ),
    Tool(
        name="list_models",
        description=(
            "List the shipped models: the name, the version, the date they "
            "were trained, the problem they solve and the cost of one 64 "
            "sample block of each. A model belongs to the offline path: a "
            "module asks for one only when the chain is in the offline "
            "processing mode, and the real time path never loads a model at "
            "all. It does not load a model, it does not run one, and it does "
            "not say which module asks for which model (the documents do)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "problem": {
                    "type": "string",
                    "description": (
                        "keep only the models that solve this problem: "
                        "'voice', 'voice_reverb' or 'ambience'."
                    ),
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        handler=_tool_list_models,
    ),
    Tool(
        name="benchmark",
        description=(
            "Run the benchmark of the project and return the table of our "
            "numbers against the recorded reference numbers, row by row, with "
            "the verdict and the gap. The measurement is deterministic, so two "
            "runs give the same numbers, but a run takes about half a minute. "
            "It does not change the chain, it does not touch the working "
            "state, and it does not fail when a module is behind a reference "
            "number: the reference was measured on another machine, so the "
            "runner reports the gap."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "cases": {
                    "type": "string",
                    "description": (
                        "the cases to measure, separated by commas, for "
                        "example 'hiss,hum50'. When it is left out, every case "
                        "of the benchmark is measured."
                    ),
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        handler=_tool_benchmark,
    ),
]


#: The tools, keyed by name.
TOOL_BY_NAME: dict[str, Tool] = {tool.name: tool for tool in TOOLS}
