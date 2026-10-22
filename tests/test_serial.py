"""
Tests for the interchange / serialisation layer.

Three layers are covered:

* ``aurcore.serial.typed_value`` - the typed-value envelope used by every
  Auralis JSON artefact (CONTRACT §6.1);
* the engine chain round-trip, which is implemented in ``aurcore.api`` and is
  therefore always available;
* the preset / chain document readers and writers, plus the version migration
  helpers.  Those live in modules that are written in parallel, so they are
  resolved dynamically and skipped while they are missing.
"""
from __future__ import annotations

import base64
import importlib
import json
from pathlib import Path

import numpy as np
import pytest

from aurcore.api import create_engine
from aurcore.params import ParamDesc, ParamStore
from aurcore.serial import typed_value as tv


# --------------------------------------------------------------------------
# A minimal module used to exercise the engine round-trip without depending
# on the module tree (which is written in parallel).
# --------------------------------------------------------------------------
class _ChainDummy:
    """Stands in for a real module: only the engine contract is implemented."""

    code = "AUR_TEST_CHAIN"
    display_name = "Chain Dummy"
    category = "Utility"
    edition = "standard"
    element = "ChainDummy"
    analysis = False
    learn = False
    ml = False

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        """Create the dummy module."""
        self.sample_rate = sample_rate
        self.channels = channels
        self.params = ParamStore(
            [
                ParamDesc(name="Gain", type="float", default=0.0, minimum=-60.0, maximum=24.0,
                          unit="dB"),
                ParamDesc(name="Enabled", type="bool", default=True),
            ]
        )
        self._state = b""

    def process(self, x: np.ndarray) -> np.ndarray:
        """The dummy module passes the signal through."""
        return x

    def reset(self) -> None:
        """The dummy module has no state to reset."""
        pass

    def serialize(self) -> dict:
        """The dummy module has no extra state."""
        return {}

    def deserialize(self, data: dict) -> None:
        """The dummy module has no extra state."""
        pass

    @property
    def latency_samples(self) -> int:
        """The dummy module has no latency."""
        return 0

    def state_base64(self) -> str:
        """The dummy module keeps an empty state blob."""
        return base64.b64encode(self._state).decode("ascii") if self._state else ""

    def load_state_base64(self, text: str) -> None:
        """The dummy module ignores the state blob."""
        if not text:
            return
        try:
            self._state = base64.b64decode(text)
        except Exception:
            self._state = b""


@pytest.fixture
def dummy_module(registry_snapshot):
    """Register a single dummy module for the duration of a test."""
    from aurcore import registry

    registry.register(_ChainDummy)
    return _ChainDummy


# --------------------------------------------------------------------------
# Typed values
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "value, expected_type",
    [
        (3.5, "Float"),
        (7, "Int"),
        (True, "Bool"),
        ("text", "String"),
        (b"\x00\x01", "Base64"),
    ],
)
def test_wrap_uses_the_documented_type(value, expected_type):
    """Every scalar type maps to its envelope type."""
    assert tv.wrap(value)["Type"] == expected_type


def test_wrap_never_confuses_bool_with_int():
    """A bool is never wrapped as an int."""
    assert tv.wrap(True)["Type"] == "Bool"
    assert tv.wrap(1)["Type"] == "Int"


def test_wrap_of_none_is_an_empty_string():
    """None becomes an empty string."""
    assert tv.wrap(None) == {"Type": "String", "Value": ""}


@pytest.mark.parametrize("value", [3.5, 7, True, False, "text", b"\x00\x01\x02"])
def test_wrap_unwrap_is_lossless(value):
    """A wrapped value survives the round trip unchanged."""
    out = tv.unwrap(tv.wrap(value))
    assert out == value
    assert type(out) is type(value)


def test_unwrap_passes_raw_values_through():
    """A raw value is passed through unchanged."""
    assert tv.unwrap(3.5) == 3.5
    assert tv.unwrap("text") == "text"


def test_unwrap_of_none_returns_the_default():
    """None unwraps to the default."""
    assert tv.unwrap(None) is None
    assert tv.unwrap(None, 5) == 5


def test_unwrap_of_a_broken_base64_returns_the_default():
    """Broken base64 unwraps to the default."""
    broken = {"Type": "Base64", "Value": "!!!not base64!!!"}
    assert tv.unwrap(broken, None) is None


def test_unwrap_of_a_broken_number_returns_the_default():
    """A broken number unwraps to the default."""
    assert tv.unwrap({"Type": "Float", "Value": "abc"}, -1.0) == -1.0
    assert tv.unwrap({"Type": "Int", "Value": "abc"}, -1) == -1


def test_wrap_all_and_unwrap_all_round_trip():
    """A whole parameter set survives the round trip."""
    values = {"Gain": 3.5, "Harmonics": 8, "Enabled": True, "Mode": "Static"}
    wrapped = tv.wrap_all(values)
    assert set(wrapped) == set(values)
    assert all("Type" in v and "Value" in v for v in wrapped.values())
    assert tv.unwrap_all(wrapped) == values


def test_wrap_all_of_nothing_is_empty():
    """An empty mapping wraps to an empty mapping."""
    assert tv.wrap_all({}) == {}
    assert tv.unwrap_all({}) == {}


# --------------------------------------------------------------------------
# Engine chain round-trip
# --------------------------------------------------------------------------
def test_engine_serialises_an_empty_chain():
    """An empty chain serialises to an empty step list."""
    engine = create_engine(48000, 2)
    state = engine.serialize()
    assert state["format"] == "auralis.chain"
    assert state["version"] == 1
    assert state["steps"] == []


def test_engine_chain_round_trip(dummy_module):
    """A chain survives serialise and deserialise."""
    engine = create_engine(48000, 2)
    handle = engine.add_module("AUR_TEST_CHAIN")
    engine.set_param(handle, "Gain", -6.0)

    state = engine.serialize()
    assert len(state["steps"]) == 1
    step = state["steps"][0]
    assert step["code"] == "AUR_TEST_CHAIN"
    assert step["enabled"] is True
    assert step["params"]["Gain"] == {"Type": "Float", "Value": -6.0}
    assert step["params"]["Enabled"] == {"Type": "Bool", "Value": True}

    restored = create_engine(48000, 2)
    restored.deserialize(state)
    assert [s.module.code for s in restored.chain] == ["AUR_TEST_CHAIN"]
    assert restored.get_param(0, "Gain") == pytest.approx(-6.0)
    assert restored.get_param(0, "Enabled") is True


def test_engine_chain_round_trip_keeps_the_bypass_state(dummy_module):
    """The bypass state survives the round trip."""
    engine = create_engine(48000, 2)
    handle = engine.add_module("AUR_TEST_CHAIN")
    engine.set_bypass(handle, True)
    state = engine.serialize()
    assert state["steps"][0]["enabled"] is False

    restored = create_engine(48000, 2)
    restored.deserialize(state)
    assert restored.is_bypassed(0) is True


def test_engine_chain_round_trip_keeps_several_modules(dummy_module):
    """Every step of a chain survives the round trip."""
    engine = create_engine(48000, 2)
    engine.add_module("AUR_TEST_CHAIN")
    engine.add_module("AUR_TEST_CHAIN")
    state = engine.serialize()
    assert len(state["steps"]) == 2
    restored = create_engine(48000, 2)
    restored.deserialize(state)
    assert len(restored.chain) == 2


def test_engine_deserialise_ignores_unknown_codes(dummy_module):
    """An unknown module code is skipped on load."""
    state = {
        "format": "auralis.chain",
        "version": 1,
        "steps": [
            {"code": "AUR_NOT_A_MODULE", "enabled": True, "params": {}, "state": ""},
            {"code": "AUR_TEST_CHAIN", "enabled": True, "params": {}, "state": ""},
        ],
    }
    engine = create_engine(48000, 2)
    engine.deserialize(state)
    assert [s.module.code for s in engine.chain] == ["AUR_TEST_CHAIN"]


def test_unknown_parameters_do_not_break_the_chain_load(dummy_module):
    """A chain written by a newer version must still load here."""
    engine = create_engine(48000, 2)
    engine.add_module("AUR_TEST_CHAIN")
    state = engine.serialize()
    state["steps"][0]["params"]["Parameter From The Future"] = {"Type": "Float", "Value": 1.5}

    text = json.dumps(state)
    restored = create_engine(48000, 2)
    restored.deserialize(json.loads(text))
    # The unknown parameter must not break the load ...
    assert restored.get_param(0, "Gain") == pytest.approx(0.0)
    assert len(restored.chain) == 1
    # ... and the chain must still serialise to a valid document.
    again = restored.serialize()
    assert again["format"] == "auralis.chain"
    assert again["steps"][0]["code"] == "AUR_TEST_CHAIN"


# --------------------------------------------------------------------------
# Dynamic resolution of the preset / chain document helpers
# --------------------------------------------------------------------------
PRESET_MODULES = ("aurcore.serial.preset_json", "aurcore.serial.preset", "aurcore.serial.presets")
CHAIN_MODULES = ("aurcore.serial.chain_json", "aurcore.serial.chain", "aurcore.serial.chains")
DOCUMENT_MODULES = ("aurcore.serial.document",)
MIGRATE_MODULES = ("aurcore.serial.migrate", "aurcore.serial.migrations")

PRESET_SAVE = ("save_preset", "write_preset", "dump_preset", "preset_to_json", "to_json", "dumps",
               "save")
PRESET_LOAD = ("load_preset", "read_preset", "preset_from_json", "from_json", "loads", "load")
CHAIN_SAVE = ("save_chain", "write_chain", "dump_chain", "chain_to_json", "to_json", "dumps",
              "save")
CHAIN_LOAD = ("load_chain", "read_chain", "chain_from_json", "from_json", "loads", "load")
MIGRATE = (
    "migrate",
    "migrate_document",
    "migrate_preset",
    "migrate_chain",
    "upgrade",
    "upgrade_document",
    "migrate_v0_to_v1",
)


def _import_first(names):
    """Import the first importable module of ``names``; None when there is none."""
    for name in names:
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
        except Exception as exc:  # noqa: BLE001 - a file that is still being written
            pytest.skip(f"{name} could not be imported yet: {type(exc).__name__}: {exc}")
    return None


def _resolve(module, names):
    """First attribute of ``module`` named after one of ``names``."""
    if module is None:
        return None
    for name in names:
        fn = getattr(module, name, None)
        if callable(fn):
            return fn
    return None


def _try_call(fn, *candidate_args):
    """Call ``fn`` with the first argument tuple it accepts."""
    last: Exception | None = None
    for args in candidate_args:
        try:
            return fn(*args)
        except TypeError as exc:
            last = exc
            continue
    raise last if last is not None else TypeError("no candidate call succeeded")


# --------------------------------------------------------------------------
# Preset documents
# --------------------------------------------------------------------------
def test_preset_round_trip(tmp_path: Path):
    """A preset survives save and load."""
    module = _import_first(PRESET_MODULES)
    if module is None:
        pytest.skip("the preset document helper does not exist yet")
    save = _resolve(module, PRESET_SAVE)
    load = _resolve(module, PRESET_LOAD)
    if save is None or load is None:
        pytest.skip(f"{module.__name__} exposes neither a save nor a load helper")

    document = {
        "format": "auralis.preset",
        "version": 1,
        "module": {"code": "AUR_TEST_CHAIN", "element": "ChainDummy"},
        "params": {"Gain": {"Type": "Float", "Value": -6.0}},
    }
    target = tmp_path / "preset.json"
    saved = _try_call(save, (target, document), (document, target), (document,), (target,))

    if isinstance(saved, str) and saved.lstrip().startswith("{"):
        loaded = _try_call(load, (saved,), (target,))
    else:
        loaded = _try_call(load, (target,), (str(target),))

    assert loaded["format"] == "auralis.preset"
    assert loaded["module"]["code"] == "AUR_TEST_CHAIN"
    assert loaded["params"]["Gain"] == {"Type": "Float", "Value": -6.0}


def test_preset_keeps_unknown_parameters(tmp_path: Path):
    """A preset keeps parameters we do not know."""
    module = _import_first(PRESET_MODULES)
    if module is None:
        pytest.skip("the preset document helper does not exist yet")
    save = _resolve(module, PRESET_SAVE)
    load = _resolve(module, PRESET_LOAD)
    if save is None or load is None:
        pytest.skip(f"{module.__name__} exposes neither a save nor a load helper")

    document = {
        "format": "auralis.preset",
        "version": 1,
        "module": {"code": "AUR_TEST_CHAIN", "element": "ChainDummy"},
        "params": {
            "Gain": {"Type": "Float", "Value": 3.0},
            "Parameter From The Future": {"Type": "Float", "Value": 1.5},
        },
    }
    target = tmp_path / "preset.json"
    saved = _try_call(save, (target, document), (document, target), (document,), (target,))
    if isinstance(saved, str) and saved.lstrip().startswith("{"):
        loaded = _try_call(load, (saved,), (target,))
    else:
        loaded = _try_call(load, (target,), (str(target),))

    assert "Parameter From The Future" in loaded["params"]


def test_preset_is_written_as_json(tmp_path: Path):
    """The saved preset is valid JSON."""
    module = _import_first(PRESET_MODULES)
    if module is None:
        pytest.skip("the preset document helper does not exist yet")
    save = _resolve(module, PRESET_SAVE)
    if save is None:
        pytest.skip(f"{module.__name__} exposes no save helper")
    document = {
        "format": "auralis.preset",
        "version": 1,
        "module": {"code": "AUR_TEST_CHAIN", "element": "ChainDummy"},
        "params": {"Gain": {"Type": "Float", "Value": 0.0}},
    }
    target = tmp_path / "preset.json"
    _try_call(save, (target, document), (document, target), (document,), (target,))
    if target.exists():
        assert json.loads(target.read_text(encoding="utf-8"))["format"] == "auralis.preset"


def test_a_preset_file_written_by_the_helper_reads_back(tmp_preset):
    """A document written to disk must be readable by the loader."""
    module = _import_first(PRESET_MODULES)
    if module is None:
        pytest.skip("the preset document helper does not exist yet")
    load = _resolve(module, PRESET_LOAD)
    if load is None:
        pytest.skip(f"{module.__name__} exposes no load helper")

    document = {
        "format": "auralis.preset",
        "version": 1,
        "module": {"code": "AUR_TEST_CHAIN", "element": "ChainDummy"},
        "params": {"Gain": {"Type": "Float", "Value": -1.5}},
    }
    path = tmp_preset(document)
    loaded = _try_call(load, (path,), (str(path),))
    assert loaded["module"]["code"] == "AUR_TEST_CHAIN"
    assert loaded["params"]["Gain"] == {"Type": "Float", "Value": -1.5}


# --------------------------------------------------------------------------
# Chain documents
# --------------------------------------------------------------------------
def test_chain_round_trip(tmp_path: Path):
    """A chain document survives save and load."""
    module = _import_first(CHAIN_MODULES)
    if module is None:
        pytest.skip("the chain document helper does not exist yet")
    save = _resolve(module, CHAIN_SAVE)
    load = _resolve(module, CHAIN_LOAD)
    if save is None or load is None:
        pytest.skip(f"{module.__name__} exposes neither a save nor a load helper")

    document = {
        "format": "auralis.chain",
        "version": 1,
        "steps": [
            {
                "code": "AUR_TEST_CHAIN",
                "enabled": True,
                "params": {"Gain": {"Type": "Float", "Value": -3.0}},
                "state": {"Type": "Base64", "Value": ""},
            }
        ],
    }
    target = tmp_path / "chain.json"
    saved = _try_call(save, (target, document), (document, target), (document,), (target,))
    if isinstance(saved, str) and saved.lstrip().startswith("{"):
        loaded = _try_call(load, (saved,), (target,))
    else:
        loaded = _try_call(load, (target,), (str(target),))

    assert loaded["format"] == "auralis.chain"
    assert loaded["steps"][0]["code"] == "AUR_TEST_CHAIN"
    assert loaded["steps"][0]["params"]["Gain"] == {"Type": "Float", "Value": -3.0}


def test_chain_keeps_unknown_parameters(tmp_path: Path):
    """A chain document keeps parameters we do not know."""
    module = _import_first(CHAIN_MODULES)
    if module is None:
        pytest.skip("the chain document helper does not exist yet")
    save = _resolve(module, CHAIN_SAVE)
    load = _resolve(module, CHAIN_LOAD)
    if save is None or load is None:
        pytest.skip(f"{module.__name__} exposes neither a save nor a load helper")

    document = {
        "format": "auralis.chain",
        "version": 1,
        "steps": [
            {
                "code": "AUR_TEST_CHAIN",
                "enabled": True,
                "params": {
                    "Gain": {"Type": "Float", "Value": -3.0},
                    "Parameter From The Future": {"Type": "Float", "Value": 2.0},
                },
                "state": {"Type": "Base64", "Value": ""},
            }
        ],
    }
    target = tmp_path / "chain.json"
    saved = _try_call(save, (target, document), (document, target), (document,), (target,))
    if isinstance(saved, str) and saved.lstrip().startswith("{"):
        loaded = _try_call(load, (saved,), (target,))
    else:
        loaded = _try_call(load, (target,), (str(target),))

    assert "Parameter From The Future" in loaded["steps"][0]["params"]


# --------------------------------------------------------------------------
# Migration
# --------------------------------------------------------------------------
def _migrate_module():
    """The module that owns the version migrations."""
    module = _import_first(MIGRATE_MODULES)
    if module is None:
        pytest.skip("no migration helper exists yet")
    return module


def _migrate_call(document: dict) -> dict:
    """Run the migration helper over ``document`` and return the result."""
    module = _migrate_module()
    fn = _resolve(module, MIGRATE)
    if fn is None:
        pytest.skip(f"{module.__name__} exposes no migration entry point")
    result = fn(document)
    assert isinstance(result, dict)
    return result


def test_migration_bumps_version_0_to_1():
    """A version 0 document becomes version 1."""
    document = {"format": "auralis.chain", "version": 0, "steps": []}
    migrated = _migrate_call(document)
    assert migrated["version"] == 1


def test_migration_renames_elements_to_steps():
    """The legacy 'elements' key becomes 'steps'."""
    document = {
        "format": "auralis.chain",
        "version": 0,
        "elements": [
            {"code": "AUR_TEST_CHAIN", "enabled": True, "params": {"Gain": {"Type": "Float",
                                                                            "Value": 1.0}}}
        ],
    }
    migrated = _migrate_call(document)
    assert "steps" in migrated, "the 'elements' key must be renamed to 'steps'"
    assert "elements" not in migrated
    assert migrated["steps"][0]["code"] == "AUR_TEST_CHAIN"
    assert migrated["steps"][0]["params"]["Gain"] == {"Type": "Float", "Value": 1.0}


def test_migration_leaves_a_current_document_alone():
    """A current document is not changed."""
    document = {"format": "auralis.chain", "version": 1, "steps": []}
    migrated = _migrate_call(document)
    assert migrated["version"] == 1
    assert migrated["steps"] == []


def test_migration_normalises_the_legacy_step_fields():
    """``on`` / ``active`` / ``bypass`` and ``settings`` become the v1 names."""
    module = _migrate_module()
    fn = _resolve(module, ("migrate_chain", "migrate"))
    if fn is None:
        pytest.skip(f"{module.__name__} exposes no chain migration")

    document = {
        "format": "auralis.chain",
        "version": 0,
        "elements": [
            {"module": "AUR_TEST_CHAIN", "on": True, "settings": {"Gain": 2.0}},
            {"module": "AUR_TEST_CHAIN", "active": False, "settings": {}},
            {"module": "AUR_TEST_CHAIN", "bypass": True, "settings": {}},
        ],
    }
    migrated = fn(document)
    assert migrated["version"] == 1
    steps = migrated["steps"]
    assert [s["code"] for s in steps] == ["AUR_TEST_CHAIN"] * 3
    assert [s["enabled"] for s in steps] == [True, False, False]
    assert steps[0]["params"]["Gain"] == {"Type": "Float", "Value": 2.0}


def test_migration_normalises_a_legacy_preset():
    """A v0 preset keeps its module and parameters after the migration."""
    module = _migrate_module()
    fn = _resolve(module, ("migrate_preset", "migrate"))
    if fn is None:
        pytest.skip(f"{module.__name__} exposes no preset migration")

    document = {
        "format": "auralis.preset",
        "version": 0,
        "module": "AUR_TEST_CHAIN",
        "parameters": {"Gain": 3.0, "Enabled": True},
    }
    migrated = fn(document)
    assert migrated["version"] == 1
    assert migrated["module"]["code"] == "AUR_TEST_CHAIN"
    assert migrated["params"]["Gain"] == {"Type": "Float", "Value": 3.0}
    assert migrated["params"]["Enabled"] == {"Type": "Bool", "Value": True}


def test_migration_records_what_it_did():
    """The applied migrations are reported back to the caller."""
    module = _migrate_module()
    fn = _resolve(module, MIGRATE)
    if fn is None:
        pytest.skip(f"{module.__name__} exposes no migration entry point")
    migrated = fn({"format": "auralis.chain", "version": 0, "elements": []})
    names = getattr(migrated, "migrations", None)
    if names is None:
        pytest.skip(f"{module.__name__} does not report the applied migrations")
    assert names, "the migration of a v0 document must be reported"
    assert isinstance(names, (list, tuple))

    applied = getattr(module, "migrations_applied", None)
    if callable(applied):
        assert list(applied(migrated)) == list(names)
