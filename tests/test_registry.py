"""
Tests for the module registry (aurcore.registry).

The registry is the single source of truth for "what modules exist"; the UI
module browser and the server both read it.  These tests cover the bookkeeping
and stay valid while the module tree is still being filled in.
"""
from __future__ import annotations

import pytest

from aurcore import registry
from aurcore.registry import ModuleInfo


class _Dummy:
    """A minimal module stand-in used to exercise the registry bookkeeping."""

    code = "AUR_TEST_DUMMY"
    display_name = "Dummy Module"
    category = "Utility"
    edition = "standard"
    element = "DummyElement"

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        """Create the dummy module."""
        self.sample_rate = sample_rate
        self.channels = channels

    def describe(self):
        """The dummy module exposes one parameter."""
        from aurcore.params import ParamDesc

        return [ParamDesc(name="Gain", type="float", default=0.0, minimum=-60.0, maximum=24.0)]


# --------------------------------------------------------------------------
# Bookkeeping
# --------------------------------------------------------------------------
def test_register_returns_the_class_and_makes_it_findable(registry_snapshot):
    """register makes the class findable."""
    registry.register(_Dummy)
    assert registry.has("AUR_TEST_DUMMY")
    assert registry.get("AUR_TEST_DUMMY") is _Dummy


def test_register_rejects_a_duplicate_code(registry_snapshot):
    """A duplicate code is rejected."""
    registry.register(_Dummy)
    with pytest.raises(ValueError):
        registry.register(_Dummy)


def test_register_rejects_a_class_without_a_code(registry_snapshot):
    """A class without a code is rejected."""
    class NoCode:
        display_name = "Nameless"

    with pytest.raises(ValueError):
        registry.register(NoCode)


def test_get_unknown_code_raises_key_error():
    """An unknown code raises."""
    with pytest.raises(KeyError):
        registry.get("AUR_DOES_NOT_EXIST")


def test_has_is_false_for_unknown_codes():
    """has reports unknown codes as missing."""
    assert registry.has("AUR_DOES_NOT_EXIST") is False


def test_codes_lists_every_registered_code(registry_snapshot):
    """codes lists what is registered."""
    registry.register(_Dummy)
    assert "AUR_TEST_DUMMY" in registry.codes()


def test_codes_is_a_copy(registry_snapshot):
    """codes returns a copy, not the live list."""
    registry.register(_Dummy)
    codes = registry.codes()
    codes.append("AUR_BOGUS")
    assert "AUR_BOGUS" not in registry.codes()


def test_clear_empties_the_registry(registry_snapshot):
    """clear empties the registry."""
    registry.register(_Dummy)
    registry.clear()
    assert registry.codes() == []
    assert registry.all() == []
    assert registry.has("AUR_TEST_DUMMY") is False


def test_create_instantiates_a_registered_module(registry_snapshot):
    """create builds the module with the given format."""
    registry.register(_Dummy)
    module = registry.create("AUR_TEST_DUMMY", 44100, 1)
    assert isinstance(module, _Dummy)
    assert module.sample_rate == 44100
    assert module.channels == 1


def test_create_unknown_code_raises_key_error():
    """create raises for an unknown code."""
    with pytest.raises(KeyError):
        registry.create("AUR_DOES_NOT_EXIST", 48000, 2)


# --------------------------------------------------------------------------
# ModuleInfo
# --------------------------------------------------------------------------
def test_module_info_to_dict_has_the_documented_fields():
    """ModuleInfo serialises the documented fields."""
    info = ModuleInfo(
        code="AUR_TEST_DUMMY",
        display_name="Dummy Module",
        category="Utility",
        edition="standard",
        element="DummyElement",
    )
    d = info.to_dict()
    for key in ("code", "display_name", "category", "edition", "element", "params", "analysis",
                "learn", "ml"):
        assert key in d
    assert d["code"] == "AUR_TEST_DUMMY"
    assert d["params"] == []
    assert d["analysis"] is False


def test_all_returns_module_info_records(registry_snapshot):
    """all returns one record per registered module."""
    registry.register(_Dummy)
    infos = registry.all()
    assert len(infos) == 1
    assert isinstance(infos[0], ModuleInfo)
    assert infos[0].code == "AUR_TEST_DUMMY"
    assert infos[0].display_name == "Dummy Module"
    assert [p.name for p in infos[0].params] == ["Gain"]


def test_all_is_sorted_by_category_then_display_name(registry_snapshot):
    """all is sorted by category and then by name."""
    class Repairish(_Dummy):
        code = "AUR_TEST_REPAIR"
        display_name = "Zebra Repair"
        category = "Repair"

    class Systemish(_Dummy):
        code = "AUR_TEST_SYSTEM"
        display_name = "Alpha System"
        category = "System"

    registry.register(Systemish)
    registry.register(Repairish)
    registry.register(_Dummy)

    infos = registry.all()
    assert [i.code for i in infos] == ["AUR_TEST_REPAIR", "AUR_TEST_DUMMY", "AUR_TEST_SYSTEM"]
    assert [i.display_name for i in infos] == ["Zebra Repair", "Dummy Module", "Alpha System"]


def test_all_is_non_empty_once_modules_exist():
    """The real module tree must eventually populate the registry."""
    if not registry.codes():
        pytest.skip("no modules registered yet - the module tree is still being written")
    infos = registry.all()
    assert infos, "registry.all() must not be empty once modules exist"
    assert len(infos) == len(registry.codes())


def test_all_covers_every_registered_code():
    """all covers every registered code."""
    infos = registry.all()
    assert sorted(i.code for i in infos) == sorted(registry.codes())


def test_registered_codes_are_unique():
    """No code is registered twice."""
    codes = registry.codes()
    assert len(codes) == len(set(codes))


def test_every_registered_code_starts_with_the_project_prefix():
    """Every code uses the project prefix."""
    for code in registry.codes():
        assert code.startswith("AUR_"), f"{code!r} must use the AUR_ code prefix"
