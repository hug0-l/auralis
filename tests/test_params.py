"""
Tests for the canonical parameter model (aurcore.params).

The parameter model is the contract between the core, the UI, the preset
writer and the plug-in adapters, so it is tested directly rather than only
through the modules.
"""
from __future__ import annotations

import dataclasses

import pytest

from aurcore.params import ParamDesc, ParamStore, dB, desc, from_dB


# --------------------------------------------------------------------------
# ParamDesc validation
# --------------------------------------------------------------------------
def test_paramdesc_is_frozen():
    """A parameter description is immutable."""
    p = ParamDesc(name="Gain", type="float", default=0.0, minimum=-60.0, maximum=24.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.name = "Other"  # type: ignore[misc]


def test_enum_requires_choices():
    """An enum parameter without choices is invalid."""
    with pytest.raises(ValueError):
        ParamDesc(name="Mode", type="enum", default=0)


def test_enum_default_falls_back_to_first_choice():
    """An invalid enum default falls back to the first choice."""
    p = ParamDesc(name="Mode", type="enum", default="Nope", choices=("Static", "Dynamic"))
    assert p.default == "Static"


def test_enum_keeps_a_valid_default():
    """A valid enum default is kept as it is."""
    p = ParamDesc(name="Mode", type="enum", default="Dynamic", choices=("Static", "Dynamic"))
    assert p.default == "Dynamic"


def test_bool_default_must_be_boolean():
    """A bool parameter with a non boolean default is invalid."""
    with pytest.raises(ValueError):
        ParamDesc(name="Enabled", type="bool", default=7)


def test_bool_default_is_normalised():
    """A bool default of 0 or 1 becomes True or False."""
    assert ParamDesc(name="Enabled", type="bool", default=1).default is True
    assert ParamDesc(name="Enabled", type="bool", default=0).default is False


def test_maximum_below_minimum_is_rejected():
    """A range with maximum below minimum is invalid."""
    with pytest.raises(ValueError):
        ParamDesc(name="Gain", type="float", default=0.0, minimum=10.0, maximum=0.0)


def test_is_continuous():
    """Only float parameters are continuous."""
    assert ParamDesc(name="Gain", type="float").is_continuous
    assert not ParamDesc(name="Gain", type="int").is_continuous
    assert not ParamDesc(name="On", type="bool").is_continuous


def test_desc_helper_builds_a_paramdesc():
    """The terse constructor builds a parameter description."""
    p = desc(name="Sensitivity", type="float", default=3.0, minimum=0.0, maximum=10.0)
    assert isinstance(p, ParamDesc)
    assert p.name == "Sensitivity"
    assert p.maximum == 10.0


# ---------------------------------------------------------------------------
# The laws of a travel
# ---------------------------------------------------------------------------
def test_an_unknown_curve_is_refused():
    """A parameter states the law its travel follows."""
    with pytest.raises(ValueError):
        ParamDesc(name="Gain", type="float", curve="exponential")


def test_a_taper_needs_a_positive_exponent():
    """The exponent of the taper law is a positive number."""
    with pytest.raises(ValueError):
        ParamDesc(name="Gain", type="float", curve="taper", curve_exponent=0.0)


def test_a_linear_travel_is_a_straight_line():
    """A linear travel moves the value with the position."""
    p = ParamDesc(name="Gain", type="float", minimum=-18.0, maximum=18.0)
    assert p.natural_value(0.0) == pytest.approx(-18.0)
    assert p.natural_value(0.5) == pytest.approx(0.0)
    assert p.natural_value(1.0) == pytest.approx(18.0)


def test_a_logarithmic_travel_is_a_straight_line_in_the_logarithm():
    """A logarithmic travel moves a frequency by the same octaves everywhere."""
    import math

    p = ParamDesc(name="Band 1 Frequency", type="float", minimum=20.0, maximum=20000.0, curve="log")
    assert p.natural_value(0.0) == pytest.approx(20.0)
    assert p.natural_value(1.0) == pytest.approx(20000.0)
    low = p.natural_value(0.1)
    high = p.natural_value(0.11)
    top = p.natural_value(0.9)
    top_high = p.natural_value(0.91)
    assert math.log2(high / low) == pytest.approx(math.log2(top_high / top), abs=1e-3)


def test_a_logarithmic_travel_needs_a_range_above_zero():
    """The logarithm of zero is not a number, so the travel stays a straight line."""
    p = ParamDesc(name="Wow", type="float", minimum=0.0, maximum=5.0, curve="log")
    assert p.natural_value(0.5) == pytest.approx(2.5)


def test_a_tapered_travel_puts_unity_in_the_middle():
    """The taper is the shape of a fader."""
    p = ParamDesc(
        name="Gain", type="float", minimum=-60.0, maximum=12.0,
        curve="taper", curve_exponent=0.264,
    )
    position = p.normalized_value(0.0)
    assert position == pytest.approx(0.5, abs=0.01)
    # the travel is finer above unity than below it: the fader gives a wide
    # span below unity and a narrow one above it
    below = p.natural_value(position - 0.01) - p.natural_value(position - 0.02)
    above = p.natural_value(position + 0.01) - p.natural_value(position)
    assert above < below


def test_the_two_directions_of_the_travel_agree():
    """A value and the position it stands at are two sides of one law."""
    import math

    for curve, low, high in (
        ("linear", -18.0, 18.0),
        ("log", 20.0, 20000.0),
        ("taper", -60.0, 12.0),
    ):
        p = ParamDesc(
            name="Control", type="float", minimum=low, maximum=high, curve=curve,
            curve_exponent=0.264,
        )
        for step in range(0, 101):
            position = step / 100.0
            value = p.natural_value(position)
            assert p.normalized_value(value) == pytest.approx(position, abs=1e-9)


def test_a_position_outside_the_travel_is_clamped():
    """The travel has ends; a position beyond them stands at the end."""
    p = ParamDesc(name="Gain", type="float", minimum=-18.0, maximum=18.0)
    assert p.natural_value(-1.0) == pytest.approx(-18.0)
    assert p.natural_value(2.0) == pytest.approx(18.0)


def test_to_dict_round_trips_every_field():
    """to_dict exposes every field of the description."""
    p = ParamDesc(
        name="Mode",
        type="enum",
        default="Static",
        minimum=0.0,
        maximum=1.0,
        unit="",
        choices=("Static", "Dynamic"),
        group="Algorithm",
        automatable=False,
        stepped=True,
        curve="log",
        help="pick one",
    )
    d = p.to_dict()
    assert d["name"] == "Mode"
    assert d["type"] == "enum"
    assert d["choices"] == ["Static", "Dynamic"]
    assert d["group"] == "Algorithm"
    assert d["automatable"] is False
    assert d["stepped"] is True
    assert d["curve"] == "log"
    assert d["help"] == "pick one"


# --------------------------------------------------------------------------
# Coercion
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw, expected",
    [
        (5.0, 5.0),
        (-10.0, 0.0),      # clamped to the minimum
        (999.0, 10.0),     # clamped to the maximum
        ("3.5", 3.5),
        ("abc", 3.0),      # unparsable -> default
        (float("nan"), 3.0),
        (None, 3.0),
    ],
)
def test_float_coercion(raw, expected):
    """Float values are clamped and unparsable input falls back to the default."""
    p = ParamDesc(name="Sensitivity", type="float", default=3.0, minimum=0.0, maximum=10.0)
    assert p.coerce(raw) == pytest.approx(expected)


def test_float_coercion_never_returns_nan():
    """A NaN never reaches the store."""
    p = ParamDesc(name="Sensitivity", type="float", default=3.0, minimum=0.0, maximum=10.0)
    out = p.coerce(float("nan"))
    assert out == out  # not NaN
    assert 0.0 <= out <= 10.0


def test_int_coercion_rounds_and_clamps():
    """Int values are rounded and clamped to the range."""
    p = ParamDesc(name="Harmonics", type="int", default=8, minimum=0, maximum=16)
    assert p.coerce(3.4) == 3
    assert p.coerce(3.6) == 4
    assert p.coerce(-5) == 0
    assert p.coerce(999) == 16
    assert isinstance(p.coerce(3.6), int)


def test_int_coercion_of_junk_falls_back_to_default():
    """Unparsable int input falls back to the default."""
    p = ParamDesc(name="Harmonics", type="int", default=8, minimum=0, maximum=16)
    assert p.coerce("nonsense") == 8


@pytest.mark.parametrize(
    "raw, expected",
    [
        (True, True),
        (False, False),
        (1, True),
        (0, False),
        ("true", True),
        ("TRUE", True),
        ("on", True),
        ("yes", True),
        ("1", True),
        ("false", False),
        ("off", False),
        ("", False),
        (2, True),
    ],
)
def test_bool_coercion(raw, expected):
    """Booleans accept the documented truthy and falsy spellings."""
    p = ParamDesc(name="Enabled", type="bool", default=False)
    assert p.coerce(raw) is expected


def test_enum_coercion_keeps_known_choices():
    """A known choice is stored unchanged."""
    p = ParamDesc(name="Mode", type="enum", default="Static", choices=("Static", "Dynamic"))
    assert p.coerce("Dynamic") == "Dynamic"
    assert p.coerce("Static") == "Static"


def test_enum_coercion_falls_back_to_the_default():
    """An unknown choice falls back to the default."""
    p = ParamDesc(name="Mode", type="enum", default="Dynamic", choices=("Static", "Dynamic"))
    assert p.coerce("Sideways") == "Dynamic"
    assert p.coerce(3) == "Dynamic"
    assert p.coerce(None) == "Dynamic"


def test_db_helpers_are_inverse():
    """The decibel helpers convert back and forth."""
    assert from_dB(dB(0.5)) == pytest.approx(0.5)
    assert from_dB(0.0) == pytest.approx(1.0)
    assert dB(0.0) < -100.0  # clamped, never -inf


# --------------------------------------------------------------------------
# ParamStore
# --------------------------------------------------------------------------
@pytest.fixture
def store() -> ParamStore:
    """A store with one parameter of each type."""
    return ParamStore(
        [
            ParamDesc(name="Gain", type="float", default=0.0, minimum=-60.0, maximum=24.0,
                      unit="dB"),
            ParamDesc(name="Harmonics", type="int", default=8, minimum=0, maximum=16),
            ParamDesc(name="Enabled", type="bool", default=True),
            ParamDesc(name="Mode", type="enum", default="Static", choices=("Static", "Dynamic")),
        ]
    )


def test_store_starts_at_the_defaults(store: ParamStore):
    """A fresh store holds the defaults."""
    assert store.get("Gain") == pytest.approx(0.0)
    assert store.get("Harmonics") == 8
    assert store.get("Enabled") is True
    assert store.get("Mode") == "Static"


def test_store_set_and_get(store: ParamStore):
    """Values can be set and read back."""
    store.set("Gain", 6.0)
    assert store.get("Gain") == pytest.approx(6.0)
    store.set("Enabled", 0)
    assert store.get("Enabled") is False


def test_store_set_clamps(store: ParamStore):
    """Values outside the range are clamped."""
    store.set("Gain", 1000.0)
    assert store.get("Gain") == pytest.approx(24.0)
    store.set("Gain", -1000.0)
    assert store.get("Gain") == pytest.approx(-60.0)
    store.set("Harmonics", 999)
    assert store.get("Harmonics") == 16


def test_store_set_coerces(store: ParamStore):
    """Values are coerced to the declared type."""
    store.set("Harmonics", 4.6)
    assert store.get("Harmonics") == 5
    assert isinstance(store.get("Harmonics"), int)
    store.set("Gain", "3")
    assert store.get("Gain") == pytest.approx(3.0)


def test_store_get_unknown_parameter_raises(store: ParamStore):
    """Reading an unknown parameter raises."""
    with pytest.raises(KeyError):
        store.get("Nope")


def test_store_set_unknown_parameter_raises(store: ParamStore):
    """Writing an unknown parameter raises."""
    with pytest.raises(KeyError):
        store.set("Nope", 1.0)


def test_store_reset_restores_defaults(store: ParamStore):
    """reset restores every default."""
    store.set("Gain", 12.0)
    store.set("Mode", "Dynamic")
    store.set("Enabled", False)
    store.reset()
    assert store.get("Gain") == pytest.approx(0.0)
    assert store.get("Mode") == "Static"
    assert store.get("Enabled") is True


def test_store_to_dict_is_a_snapshot(store: ParamStore):
    """to_dict returns a copy, not the live state."""
    d = store.to_dict()
    assert set(d) == {"Gain", "Harmonics", "Enabled", "Mode"}
    d["Gain"] = 999.0
    assert store.get("Gain") == pytest.approx(0.0)


def test_store_from_dict_applies_values(store: ParamStore):
    """from_dict applies the values it knows."""
    store.from_dict({"Gain": -6.0, "Mode": "Dynamic"})
    assert store.get("Gain") == pytest.approx(-6.0)
    assert store.get("Mode") == "Dynamic"


def test_store_from_dict_tolerates_unknown_parameters(store: ParamStore):
    """Unknown keys are ignored, not fatal."""
    store.from_dict({"Gain": 3.0, "Removed Parameter": 12.0})
    assert store.get("Gain") == pytest.approx(3.0)
    assert "Removed Parameter" not in store
    assert "Removed Parameter" not in store.to_dict()


def test_store_from_dict_coerces(store: ParamStore):
    """Values loaded from a document are coerced."""
    store.from_dict({"Gain": 999.0, "Harmonics": "7", "Enabled": "true", "Mode": "bogus"})
    assert store.get("Gain") == pytest.approx(24.0)
    assert store.get("Harmonics") == 7
    assert store.get("Enabled") is True
    assert store.get("Mode") == "Static"


def test_store_from_dict_ignores_none(store: ParamStore):
    """from_dict tolerates None."""
    store.from_dict(None)  # type: ignore[arg-type]
    assert store.get("Gain") == pytest.approx(0.0)


def test_store_duplicate_parameter_is_rejected():
    """A duplicate parameter name is rejected."""
    with pytest.raises(KeyError):
        ParamStore(
            [
                ParamDesc(name="Gain", type="float", default=0.0),
                ParamDesc(name="Gain", type="float", default=1.0),
            ]
        )


def test_store_descs_returns_a_copy(store: ParamStore):
    """descs returns a copy of the description list."""
    descs = store.descs()
    assert len(descs) == 4
    assert descs[0].name == "Gain"
    descs.clear()
    assert len(store.descs()) == 4


def test_store_desc_lookup_and_membership(store: ParamStore):
    """The store can be searched by name."""
    assert store.desc("Gain").type == "float"
    assert store.has("Gain")
    assert not store.has("Nope")
    assert "Gain" in store
    assert len(store) == 4
    with pytest.raises(KeyError):
        store.desc("Nope")


def test_store_set_raw_skips_coercion(store: ParamStore):
    """set_raw stores the value as it is."""
    store.set_raw("Gain", 999.0)
    assert store.get("Gain") == pytest.approx(999.0)


def test_store_groups_are_unique_and_ordered():
    """The group list keeps the order of first appearance."""
    s = ParamStore(
        [
            ParamDesc(name="A", type="float", group="One"),
            ParamDesc(name="B", type="float", group="Two"),
            ParamDesc(name="C", type="float", group="One"),
        ]
    )
    assert s.groups() == ["One", "Two"]


def test_store_extend_keeps_order():
    """extend appends the new parameters."""
    s = ParamStore([ParamDesc(name="A", type="float")])
    s.extend([ParamDesc(name="B", type="float"), ParamDesc(name="C", type="float")])
    assert [d.name for d in s.descs()] == ["A", "B", "C"]


# ---------------------------------------------------------------------------
# The names a parameter had before
# ---------------------------------------------------------------------------
@pytest.fixture
def renamed() -> ParamStore:
    """A store with a parameter that was renamed."""
    return ParamStore(
        [
            ParamDesc(
                name="Reduction",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                aliases=("Strength", "Reduction Strength"),
            )
        ]
    )


def test_a_renamed_parameter_keeps_its_old_name(renamed: ParamStore):
    """A parameter that was renamed still answers to the name it had."""
    assert renamed.has("Strength")
    assert renamed.has("Reduction Strength")
    assert renamed.canonical("Strength") == "Reduction"
    assert renamed.canonical("Reduction") == "Reduction"
    assert renamed.canonical("Nope") is None


def test_an_old_name_sets_the_parameter_it_belongs_to(renamed: ParamStore):
    """Writing an old name moves the parameter that carries it."""
    renamed.set("Strength", 0.25)
    assert renamed.get("Reduction") == pytest.approx(0.25)
    assert renamed.get("Strength") == pytest.approx(0.25)


def test_a_document_that_names_an_old_parameter_loads(renamed: ParamStore):
    """A document that carries an old name is applied to the parameter."""
    renamed.from_dict({"Strength": 0.75})
    assert renamed.get("Reduction") == pytest.approx(0.75)
    assert "Strength" not in renamed.to_dict()


def test_an_old_name_is_not_a_second_parameter(renamed: ParamStore):
    """The alias does not add a parameter to the store."""
    assert len(renamed) == 1
    assert set(renamed.to_dict()) == {"Reduction"}


def test_an_unknown_name_is_still_refused(renamed: ParamStore):
    """A name the store does not know is still an error."""
    with pytest.raises(KeyError):
        renamed.get("Nope")
    with pytest.raises(KeyError):
        renamed.set("Nope", 1.0)


def test_two_parameters_cannot_share_an_alias():
    """An alias names one parameter only."""
    with pytest.raises(KeyError):
        ParamStore(
            [
                ParamDesc(name="Reduction", type="float", aliases=("Strength",)),
                ParamDesc(name="Strength", type="float"),
            ]
        )
