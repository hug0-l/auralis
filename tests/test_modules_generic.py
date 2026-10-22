"""
Generic contract tests, applied to every registered module.

CONTRACT §9 asks for a parameter-presence check, a range-clamp check and a
process smoke test for every module.  These tests do exactly that, for every
module the registry knows about, so a new module is covered the moment it is
registered.

Modules that are not written yet are simply not in the registry and therefore
not parameterised here.
"""
from __future__ import annotations

import numpy as np
import pytest

from aurcore import registry
from aurcore.api import create_engine
from tests.conftest import expected_output_length, length_tolerance

CODES = list(registry.codes())


def _require(code: str):
    """Return the registered class for a code, or skip the test."""
    if not registry.has(code):
        pytest.skip(f"module {code!r} is not registered yet")
    return registry.get(code)


# --------------------------------------------------------------------------
# Parameters
# --------------------------------------------------------------------------
@pytest.mark.parametrize("code", CODES)
def test_module_exposes_parameters(code: str):
    """Every module exposes a sane parameter list."""
    cls = _require(code)
    module = registry.create(code, 48000, 2)
    descs = module.describe()
    assert descs, f"{code} exposes no parameters - a hidden knob is a bug"
    names = [d.name for d in descs]
    assert len(names) == len(set(names)), f"{code} lists a parameter twice"
    for d in descs:
        assert d.name, f"{code} has a parameter without a name"
        assert d.type in ("float", "int", "bool", "enum"), f"{code}/{d.name}: bad type {d.type!r}"
        if d.type == "enum":
            assert d.choices, f"{code}/{d.name}: enum without choices"
            assert d.default in d.choices, f"{code}/{d.name}: default is not a choice"
        if d.type == "bool":
            assert isinstance(d.default, bool), f"{code}/{d.name}: bool default must be a bool"
    assert len(module.params.descs()) == len(descs)


@pytest.mark.parametrize("code", CODES)
def test_module_parameters_are_clamped(code: str):
    """Setting a value outside the range clamps it - it never raises."""
    cls = _require(code)
    module = registry.create(code, 48000, 2)
    float_params = [d for d in module.describe() if d.type == "float" and d.maximum > d.minimum]
    if not float_params:
        pytest.skip(f"{code} has no continuous parameter to clamp")
    for d in float_params:
        module.params.set(d.name, d.maximum + 1000.0)
        assert module.params.get(d.name) == pytest.approx(d.maximum)
        module.params.set(d.name, d.minimum - 1000.0)
        assert module.params.get(d.name) == pytest.approx(d.minimum)
        module.params.reset()
        assert module.params.get(d.name) == pytest.approx(d.default)


@pytest.mark.parametrize("code", CODES)
def test_module_parameters_are_typed(code: str):
    """The stored value always has the declared type."""
    _require(code)
    module = registry.create(code, 48000, 2)
    for d in module.describe():
        module.params.set(d.name, d.default)
        value = module.params.get(d.name)
        if d.type == "bool":
            assert isinstance(value, bool)
        elif d.type == "int":
            assert isinstance(value, int) and not isinstance(value, bool)
        elif d.type == "enum":
            assert value in d.choices
        else:
            assert isinstance(value, float)


# --------------------------------------------------------------------------
# Processing
# --------------------------------------------------------------------------
@pytest.mark.parametrize("code", CODES)
def test_module_process_is_a_smoke_test(code: str, white_noise: np.ndarray):
    """Processing must not raise, must keep the length and must stay finite."""
    _require(code)
    module = registry.create(code, 48000, 2)
    out = module.process(white_noise.copy())
    assert out is not None, f"{code} returned nothing"
    arr = np.asarray(out)
    assert arr.ndim == 2, f"{code} must return a (n, channels) array"
    expected = expected_output_length(module, white_noise.shape[0])
    assert abs(arr.shape[0] - expected) <= length_tolerance(expected), (
        f"{code} returned {arr.shape[0]} samples, expected about {expected}"
    )
    assert arr.shape[1] == white_noise.shape[1], f"{code} changed the channel count"
    assert np.all(np.isfinite(arr)), f"{code} produced a non-finite sample"


@pytest.mark.parametrize("code", CODES)
def test_module_process_keeps_the_length_over_several_blocks(code: str, white_noise: np.ndarray):
    """Streaming the signal in blocks must not change the total length."""
    _require(code)
    module = registry.create(code, 48000, 2)
    block = 1024
    total = (len(white_noise) // block) * block
    produced = 0
    for start in range(0, total, block):
        chunk = white_noise[start : start + block]
        out = np.asarray(module.process(chunk.copy()))
        produced += out.shape[0]
        assert np.all(np.isfinite(out)), f"{code} produced a non-finite sample"
    expected = expected_output_length(module, total)
    assert abs(produced - expected) <= length_tolerance(expected), (
        f"{code} returned {produced} samples for {total} input samples, expected about {expected}"
    )


@pytest.mark.parametrize("code", CODES)
def test_module_reset_is_safe(code: str, white_noise: np.ndarray):
    """reset clears the state and the module still runs."""
    _require(code)
    module = registry.create(code, 48000, 2)
    module.process(white_noise.copy())
    module.reset()
    out = np.asarray(module.process(white_noise.copy()))
    expected = expected_output_length(module, white_noise.shape[0])
    assert abs(out.shape[0] - expected) <= length_tolerance(expected)
    assert np.all(np.isfinite(out))


@pytest.mark.parametrize("code", CODES)
def test_module_latency_is_not_negative(code: str):
    """A module never reports a negative latency."""
    _require(code)
    module = registry.create(code, 48000, 2)
    assert module.latency_samples >= 0


@pytest.mark.parametrize("code", CODES)
def test_module_bypass_is_bit_identical(code: str, white_noise: np.ndarray, engine):
    """A bypassed module must pass the signal through untouched."""
    _require(code)
    handle = engine.add_module(code)
    engine.set_bypass(handle, True)
    out = engine.process(white_noise.copy())
    assert np.array_equal(out, white_noise), f"{code} is not bit-identical when bypassed"


@pytest.mark.parametrize("code", CODES)
def test_module_can_be_created_through_the_engine(code: str, engine):
    """The engine can add the module and read its parameters."""
    _require(code)
    handle = engine.add_module(code)
    assert handle.code == code
    assert handle.display_name
    infos = engine.param_info(handle)
    assert infos, f"{code} exposes no parameters through the engine"
    assert [d.name for d in infos] == [d.name for d in registry.get(code)(48000, 2).describe()]


@pytest.mark.parametrize("code", CODES)
def test_module_survives_a_json_round_trip_of_its_parameters(code: str):
    """The parameters of every module must survive the serialisation format."""
    _require(code)
    from aurcore.serial import typed_value as tv

    module = registry.create(code, 48000, 2)
    values = module.params.to_dict()
    wrapped = tv.wrap_all(values)
    assert tv.unwrap_all(wrapped) == values
    module.params.from_dict(tv.unwrap_all(wrapped))
    assert module.params.to_dict() == values
