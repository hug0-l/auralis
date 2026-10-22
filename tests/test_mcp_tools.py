"""
tests.test_mcp_tools — the gate of the tools and the resources of the model
context server.

The gate checks the four rules the tools are written against:

* every tool is published with a complete schema and a description that says
  what it does **and what it does not do**;
* a refusal is actionable: it names the problem and says what the agent can
  do instead;
* the tools are safe to call in any order: the chain is the working state,
  it is always readable back, and a refused request changes nothing;
* the measurement answers carry the instrument that produced them.

The gate runs with the dependencies of the project and nothing else: the
schema is checked by hand, not with a schema library.

    python -m pytest tests/test_mcp_tools.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurmcp.resources import RESOURCES  # noqa: E402
from aurmcp.tools import TOOLS  # noqa: E402

TOOL_NAMES = (
    "benchmark",
    "describe_module",
    "get_chain",
    "list_corpus_cases",
    "list_models",
    "list_modules",
    "measure",
    "render",
    "set_chain",
    "set_parameter",
    "set_processing_mode",
)

RESOURCE_URIS = (
    "auralis://benchmark",
    "auralis://corpus",
    "auralis://docs/architecture",
    "auralis://docs/benchmark-notes",
    "auralis://docs/restoration-reference",
    "auralis://docs/status",
    "auralis://models",
    "auralis://modules",
    "auralis://parameters",
)


def tool(name: str):
    """The tool of that name."""
    for item in TOOLS:
        if item.name == name:
            return item
    raise AssertionError(f"the tool {name!r} is not published")


def call(_tool: str, **arguments):
    """Call a tool and return its answer."""
    return tool(_tool).handler(arguments)


def refusal(_tool: str, **arguments) -> str:
    """Call a tool that has to refuse, and return the message of the refusal."""
    from aurmcp.protocol import ToolError

    try:
        call(_tool, **arguments)
    except ToolError as error:
        return str(error)
    raise AssertionError(f"{_tool} did not refuse")


# ---------------------------------------------------------------------------
# the publication
# ---------------------------------------------------------------------------
def test_every_tool_is_published():
    """The eleven tools of the server are published."""
    assert sorted(t.name for t in TOOLS) == sorted(TOOL_NAMES)


def test_every_schema_is_a_complete_object_schema():
    """Every schema is an object schema with its properties described."""
    for item in TOOLS:
        schema = item.input_schema
        assert isinstance(schema, dict), item.name
        assert schema.get("type") == "object", item.name
        properties = schema.get("properties")
        assert isinstance(properties, dict), item.name
        required = schema.get("required", [])
        assert isinstance(required, list), item.name
        for name in required:
            assert name in properties, f"{item.name}: {name} is required but not described"
        for name, prop in properties.items():
            assert isinstance(prop, dict), f"{item.name}: {name}"
            assert "type" in prop or "oneOf" in prop, f"{item.name}: {name} has no type"
            assert prop.get("description"), f"{item.name}: {name} has no description"


def test_every_description_says_what_the_tool_does_not_do():
    """A description that does not say what it does not do is not complete."""
    for item in TOOLS:
        assert len(item.description) > 80, item.name
        assert "does not" in item.description, item.name


def test_every_resource_is_published_and_readable():
    """The nine resources of the server are published and answer with text."""
    assert sorted(r.uri for r in RESOURCES) == sorted(RESOURCE_URIS)
    for resource in RESOURCES:
        assert resource.mime_type.startswith("text/"), resource.uri
        assert len(resource.description) > 60, resource.uri
        if resource.uri == "auralis://benchmark":
            # reading it runs the benchmark, which takes half a minute
            continue
        text = resource.reader()
        assert isinstance(text, str) and text.strip(), resource.uri


# ---------------------------------------------------------------------------
# the refusals
# ---------------------------------------------------------------------------
def test_an_unknown_module_is_refused_by_name():
    """An unknown module code names the codes of the catalogue."""
    message = refusal("describe_module", module="AUR_NOSUCH")
    assert "AUR_NOSUCH" in message
    assert "AUR_DEHUM" in message


def test_an_unknown_parameter_is_refused_by_name():
    """An unknown parameter names the parameters of the module."""
    message = refusal(
        "set_chain",
        modules=[{"code": "AUR_DEHUM", "params": {"No Such Parameter": 1.0}}],
    )
    assert "No Such Parameter" in message
    assert "Base Frequency" in message


def test_a_value_outside_the_range_is_refused_with_the_range():
    """A value outside the range names the range."""
    message = refusal(
        "set_chain",
        modules=[{"code": "AUR_DEHUM", "params": {"Base Frequency": 900.0}}],
    )
    assert "Base Frequency" in message
    assert "500.0" in message


def test_a_value_outside_the_choices_is_refused_with_the_choices():
    """A value that is not a choice of an enum names the choices."""
    message = refusal(
        "set_chain",
        modules=[{"code": "AUR_DEHUM", "params": {"Mode": "Nonsense"}}],
    )
    assert "Static" in message


def test_an_unknown_case_is_refused_with_the_cases():
    """An unknown corpus case names the cases of the corpus."""
    message = refusal("render", case="no-such-case")
    assert "no-such-case" in message
    assert "hum50" in message


def test_an_unknown_mode_is_refused_with_the_modes():
    """An unknown processing mode names the two modes of the product."""
    message = refusal("set_processing_mode", mode="nonsense")
    assert "realtime" in message
    assert "offline" in message


def test_an_unknown_step_is_refused_with_the_length_of_the_chain():
    """An unknown step names the number of steps of the chain."""
    call("set_chain", modules=[{"code": "AUR_DEHUM"}])
    message = refusal("set_parameter", step=7, name="Base Frequency", value=50.0)
    assert "1 step" in message


# ---------------------------------------------------------------------------
# the working state
# ---------------------------------------------------------------------------
def test_the_chain_is_readable_back_after_every_call():
    """The chain is the working state and it is always readable."""
    answer = call(
        "set_chain",
        modules=[
            {"code": "AUR_DEHUM", "params": {"Base Frequency": 50.0}},
            {"code": "AUR_GAIN"},
        ],
    )
    assert [step["code"] for step in answer["steps"]] == ["AUR_DEHUM", "AUR_GAIN"]
    assert answer["module_count"] == 2

    answer = call("get_chain")
    assert answer["steps"][0]["params"]["Base Frequency"] == 50.0

    answer = call("set_parameter", step=0, name="Base Frequency", value=60.0)
    assert answer["value"] == 60.0
    assert call("get_chain")["steps"][0]["params"]["Base Frequency"] == 60.0


def test_a_refused_request_changes_nothing():
    """A refusal leaves the chain exactly as it was."""
    call("set_chain", modules=[{"code": "AUR_DEHUM"}])
    before = call("get_chain")
    refusal(
        "set_chain",
        modules=[
            {"code": "AUR_DEHUM"},
            {"code": "AUR_DEHUM", "params": {"Base Frequency": 900.0}},
        ],
    )
    after = call("get_chain")
    assert before["steps"] == after["steps"]


def test_the_processing_mode_belongs_to_the_chain():
    """The mode is set on the chain and it survives a new chain."""
    call("set_chain", modules=[{"code": "AUR_SPECTRALDENOISE"}])
    answer = call("set_processing_mode", mode="offline")
    assert answer["processing_mode"] == "offline"
    assert answer["latency_samples"] > 0
    answer = call("set_chain", modules=[{"code": "AUR_SPECTRALDENOISE"}])
    assert answer["processing_mode"] == "offline"
    answer = call("set_processing_mode", mode="realtime")
    assert answer["processing_mode"] == "realtime"
    assert answer["previous_mode"] == "offline"


# ---------------------------------------------------------------------------
# the end to end job
# ---------------------------------------------------------------------------
def test_the_hum_is_removed_and_the_tone_stays():
    """The four calls of the worked example: set, tune, render, measure."""
    call("set_chain", modules=[{"code": "AUR_DEHUM", "params": {"Base Frequency": 50.0}}])
    call("set_parameter", step=0, name="Base Frequency", value=50.0)

    written = call("render", case="hum50")
    assert written["duration_s"] > 1.0
    assert written["relative_path"].startswith("var/assets/audio/")
    assert Path(ROOT / written["relative_path"]).is_file()

    answer = call("measure", case="hum50")
    assert answer["chain"] == ["AUR_DEHUM"]
    # the hum band of the corpus went quiet, the 1 kHz tone stayed
    assert answer["corpus_artefact_change_db"] < -20.0
    assert abs(answer["corpus_wanted_change_db"]) < 1.0
    # every number carries the instrument that produced it
    for key in ("removed_db", "kept_db", "gain_var_db2", "cost_transform_eq_per_64_block"):
        assert key in answer["method"], key
    assert answer["cost_transform_eq_per_64_block"] > 0.0
    assert answer["declared_latency_samples"] == 0


def test_the_catalogue_and_the_ground_truth_are_readable():
    """The catalogue, the corpus and the models answer with their content."""
    modules = call("list_modules")
    assert modules["count"] > 40
    assert any(item["code"] == "AUR_DEHUM" for item in modules["modules"])

    described = call("describe_module", module="AUR_DEHUM")
    names = [param["name"] for param in described["parameters"]]
    assert "Base Frequency" in names
    parameter = next(p for p in described["parameters"] if p["name"] == "Base Frequency")
    assert parameter["unit"] == "Hz"
    assert parameter["minimum"] == 20.0
    assert parameter["maximum"] == 500.0

    cases = call("list_corpus_cases")
    assert cases["count"] > 5
    assert any(case["id"] == "hum50" for case in cases["cases"])

    models = call("list_models")
    assert models["count"] > 0
    assert "offline" in models["rule"]
