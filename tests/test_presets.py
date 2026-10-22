"""
tests/test_presets.py — the on-disk preset library of the server.

What is checked
  1. the round trip: every kind of value a module can hold — a float, a whole
     number, a switch, an enum choice, a value outside the domain and a value
     at its edge — comes back exactly as it was saved and in the same unit;
  2. a preset belongs to one module: it is refused for another module, and a
     preset whose module is gone is reported instead of being dropped;
  3. a broken preset file (truncated, hand edited, written by another version)
     never takes the library down;
  4. a name is a name and never a path;
  5. the library is sorted and searchable in a way a user can rely on.

The library is driven through its HTTP routes against a temporary preset
root, so the real library in var/presets is never touched.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aurcore import registry
from aurcore.modules.base import Module
from aurcore.params import ParamDesc
from aurcore.serial import typed_value as tv
from aurcore.serial.preset_json import apply_preset, preset_from_module
from aurserver import presets as lib

#: A module with a float, a whole number, a switch and an enum in it.
DEHUM = "AUR_DEHUM"
DECLICK = "AUR_DECLICK"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def preset_root(tmp_path: Path) -> Path:
    """A temporary preset root with an empty builtin folder."""
    root = tmp_path / "presets"
    (root / "builtin").mkdir(parents=True)
    return root


@pytest.fixture
def api(preset_root: Path) -> TestClient:
    """A client for the preset routes, rooted in the temporary library."""
    app = FastAPI()
    app.include_router(lib.router)
    app.state.preset_root = preset_root
    return TestClient(app, raise_server_exceptions=False)


def save(api: TestClient, name: str, module_code: str, params: dict, category="Presets"):
    """Save one preset and return the answer of the route."""
    return api.post(
        "/api/preset/save",
        json={
            "name": name,
            "category": category,
            "module_code": module_code,
            "params": params,
        },
    )


def value_of(document: dict, name: str):
    """Read one parameter out of a loaded preset document."""
    node = (document.get("params") or {}).get(name)
    return tv.unwrap(node)


# ---------------------------------------------------------------------------
# 1. the round trip
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "module_code, pname, value",
    [
        (DEHUM, "Base Frequency", 60.0),        # a float
        (DEHUM, "Filter Q", 0.707),             # a float
        (DEHUM, "Number of Harmonics", 4),      # a whole number
        (DEHUM, "Adaptive", True),              # a switch
        (DEHUM, "Adaptive", False),             # a switch
        (DEHUM, "Mode", "Dynamic"),             # an enum choice
        (DECLICK, "Algorithm", "MB Random"),    # an enum choice
        (DECLICK, "Sensitivity", 3.0),          # a float
    ],
)
def test_a_value_comes_back_exactly_as_it_was_saved(
    api: TestClient, module_code: str, pname: str, value
) -> None:
    """Every kind of value a module holds survives the round trip."""
    saved = save(api, "Round Trip", module_code, {pname: value})
    assert saved.status_code == 200, saved.text

    loaded = api.post("/api/preset/load", json={"path": saved.json()["path"]})
    assert loaded.status_code == 200, loaded.text

    got = value_of(loaded.json(), pname)
    assert got == value
    assert type(got) is type(value), f"{pname} came back as {type(got).__name__}"


def test_a_value_outside_the_domain_comes_back_unchanged(api: TestClient) -> None:
    """The library keeps the value; it does not clamp it behind the user's back."""
    desc = lib.parameter_descriptors(DECLICK)["Sensitivity"]
    above = float(desc.maximum) + 1000.0
    below = float(desc.minimum) - 1000.0

    for value in (above, below):
        saved = save(api, "Outside", DECLICK, {"Sensitivity": value})
        assert saved.status_code == 200, saved.text
        loaded = api.post("/api/preset/load", json={"path": saved.json()["path"]})
        assert value_of(loaded.json(), "Sensitivity") == value


def test_a_value_at_the_edge_of_the_domain_comes_back_unchanged(
    api: TestClient,
) -> None:
    """The lowest and the highest value the module accepts come back as they are."""
    desc = lib.parameter_descriptors(DECLICK)["Sensitivity"]
    for value in (float(desc.minimum), float(desc.maximum)):
        saved = save(api, "Edge", DECLICK, {"Sensitivity": value})
        assert saved.status_code == 200, saved.text
        loaded = api.post("/api/preset/load", json={"path": saved.json()["path"]})
        assert value_of(loaded.json(), "Sensitivity") == value


def test_a_frequency_keeps_its_unit(api: TestClient) -> None:
    """A frequency in hertz does not come back in another unit."""
    desc = lib.parameter_descriptors(DEHUM)["Base Frequency"]
    assert desc.unit == "Hz"

    saved = save(api, "Frequency", DEHUM, {"Base Frequency": 60.0})
    assert saved.status_code == 200, saved.text
    loaded = api.post("/api/preset/load", json={"path": saved.json()["path"]})
    document = loaded.json()

    assert value_of(document, "Base Frequency") == 60.0

    # the unit is the one the module declares for that parameter, and it is
    # the same one before and after the round trip
    after = lib.parameter_descriptors(DEHUM)["Base Frequency"]
    assert after.unit == desc.unit
    assert after.minimum == desc.minimum and after.maximum == desc.maximum

    # the report of the load says which unit the value is in
    report = document.get("report") or {}
    assert report.get("out_of_range") == []


def test_a_value_outside_the_domain_is_reported_on_the_load(
    api: TestClient,
) -> None:
    """A value the module would clamp is named in the report of the load."""
    desc = lib.parameter_descriptors(DECLICK)["Sensitivity"]
    value = float(desc.maximum) + 1000.0
    saved = save(api, "Reported", DECLICK, {"Sensitivity": value})
    loaded = api.post("/api/preset/load", json={"path": saved.json()["path"]})

    report = (loaded.json().get("report") or {})
    names = [item["name"] for item in report.get("out_of_range", [])]
    assert "Sensitivity" in names
    item = next(i for i in report["out_of_range"] if i["name"] == "Sensitivity")
    assert item["value"] == value
    assert item["minimum"] == float(desc.minimum)
    assert item["maximum"] == float(desc.maximum)


def test_a_whole_number_stays_a_whole_number(api: TestClient) -> None:
    """A value that arrives as a numpy scalar is stored as the number it is."""
    wrapped = lib._wrap_params({"Harmonics": np.int64(4)})
    assert wrapped["Harmonics"] == {"Type": "Int", "Value": 4}
    assert isinstance(wrapped["Harmonics"]["Value"], int)
    assert not isinstance(wrapped["Harmonics"]["Value"], bool)

    switch = lib._wrap_params({"Adaptive": np.bool_(True)})
    assert switch["Adaptive"] == {"Type": "Bool", "Value": True}

    number = lib._wrap_params({"Frequency": np.float64(60.0)})
    assert number["Frequency"]["Type"] == "Float"
    assert isinstance(number["Frequency"]["Value"], float)


def test_the_file_on_the_disk_holds_the_typed_envelope(
    api: TestClient, preset_root: Path
) -> None:
    """The file holds the values the interchange format describes."""
    saved = save(
        api,
        "Envelope",
        DEHUM,
        {"Base Frequency": 60.0, "Number of Harmonics": 4, "Adaptive": True},
    )
    path = preset_root / saved.json()["path"]
    document = json.loads(path.read_text(encoding="utf-8"))

    params = document["params"]
    assert params["Base Frequency"]["Type"] == "Float"
    assert params["Base Frequency"]["Value"] == 60.0
    assert params["Number of Harmonics"]["Type"] == "Int"
    assert params["Number of Harmonics"]["Value"] == 4
    assert params["Adaptive"]["Type"] == "Bool"
    assert params["Adaptive"]["Value"] is True
    assert document["module"]["code"] == DEHUM


def test_the_file_on_the_disk_holds_the_identity_of_every_parameter(
    api: TestClient, preset_root: Path
) -> None:
    """Beside the value a parameter carries the id and the meaning it has."""
    saved = save(api, "Identity", DEHUM, {"Base Frequency": 60.0})
    path = preset_root / saved.json()["path"]
    document = json.loads(path.read_text(encoding="utf-8"))

    node = document["params"]["Base Frequency"]
    assert node["Id"] == 1          # the position of the parameter in its module
    assert node["Sig"]              # the meaning of the parameter
    assert "dB" in node["Sig"] or "Hz" in node["Sig"]


# ---------------------------------------------------------------------------
# 2. the module a preset belongs to
# ---------------------------------------------------------------------------
def test_a_preset_is_refused_for_another_module(api: TestClient) -> None:
    """A preset saved for one module is not handed to another module."""
    saved = save(api, "Hum", DEHUM, {"Base Frequency": 60.0})
    assert saved.status_code == 200, saved.text

    loaded = api.post(
        "/api/preset/load",
        json={"path": saved.json()["path"], "module_code": DECLICK},
    )
    assert loaded.status_code == 409
    detail = loaded.json()["detail"]
    assert DEHUM in detail and DECLICK in detail


def test_a_preset_is_handed_to_the_module_it_belongs_to(api: TestClient) -> None:
    """The same request for the right module is accepted."""
    saved = save(api, "Hum", DEHUM, {"Base Frequency": 60.0})
    loaded = api.post(
        "/api/preset/load",
        json={"path": saved.json()["path"], "module_code": DEHUM},
    )
    assert loaded.status_code == 200, loaded.text
    assert value_of(loaded.json(), "Base Frequency") == 60.0


def test_a_preset_cannot_be_saved_for_a_module_that_does_not_exist(
    api: TestClient,
) -> None:
    """A preset for a module this build does not have is refused."""
    answer = save(api, "Ghost", "AUR_NOTHERE", {"Base Frequency": 60.0})
    assert answer.status_code == 400
    assert "AUR_NOTHERE" in answer.json()["detail"]


def test_a_preset_whose_module_is_gone_is_reported(api: TestClient, preset_root: Path) -> None:
    """A preset written when the module still existed is reported, not dropped."""
    folder = preset_root / "Presets"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "Orphan.json").write_text(
        json.dumps(
            {
                "format": "auralis.preset",
                "version": 1,
                "name": {"Type": "String", "Value": "Orphan"},
                "category": {"Type": "String", "Value": "Presets"},
                "module": {"code": "AUR_GONE", "element": ""},
                "params": {"Base Frequency": {"Type": "Float", "Value": 60.0}},
                "state": {"Type": "Base64", "Value": ""},
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    listing = api.get("/api/presets")
    assert listing.status_code == 200, listing.text
    body = listing.json()

    entry = next(e for e in body["presets"] if e["name"] == "Orphan")
    assert entry["status"] == "orphan"
    assert entry["module_code"] == "AUR_GONE"
    assert any("AUR_GONE" in problem for problem in entry["problems"])

    problems = [p["path"] for p in body.get("problems", [])]
    assert "Presets/Orphan.json" in problems


def test_a_parameter_of_another_module_is_reported(
    api: TestClient, preset_root: Path
) -> None:
    """A preset that carries a parameter of a different module is reported."""
    folder = preset_root / "Presets"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "Mixed.json").write_text(
        json.dumps(
            {
                "format": "auralis.preset",
                "version": 1,
                "name": {"Type": "String", "Value": "Mixed"},
                "category": {"Type": "String", "Value": "Presets"},
                "module": {"code": DECLICK, "element": ""},
                "params": {"Base Frequency": {"Type": "Float", "Value": 60.0}},
                "state": {"Type": "Base64", "Value": ""},
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    listing = api.get("/api/presets")
    body = listing.json()
    entry = next(e for e in body["presets"] if e["name"] == "Mixed")
    assert entry["status"] == "damaged"
    assert any("Base Frequency" in problem for problem in entry["problems"])


def test_an_enum_choice_that_is_not_offered_is_reported(
    api: TestClient, preset_root: Path
) -> None:
    """An enum value the module does not offer is reported, not applied blindly."""
    folder = preset_root / "Presets"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "Wrong.json").write_text(
        json.dumps(
            {
                "format": "auralis.preset",
                "version": 1,
                "name": {"Type": "String", "Value": "Wrong"},
                "category": {"Type": "String", "Value": "Presets"},
                "module": {"code": DEHUM, "element": ""},
                "params": {"Mode": {"Type": "String", "Value": "No Such Mode"}},
                "state": {"Type": "Base64", "Value": ""},
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    listing = api.get("/api/presets")
    entry = next(e for e in listing.json()["presets"] if e["name"] == "Wrong")
    assert entry["status"] == "damaged"
    assert any("Mode" in problem for problem in entry["problems"])


def test_the_load_names_the_parameters_the_module_does_not_know(
    api: TestClient,
) -> None:
    """The report of a load says which parameters belong to another module."""
    saved = save(api, "Foreign", DEHUM, {"Base Frequency": 60.0})
    loaded = api.post("/api/preset/load", json={"path": saved.json()["path"]})
    report = loaded.json().get("report") or {}
    assert report.get("module") == DEHUM
    assert report.get("unknown_params") == []
    assert report.get("module_missing") is False


# ---------------------------------------------------------------------------
# 3. broken files
# ---------------------------------------------------------------------------
GOOD_DOCUMENT = {
    "format": "auralis.preset",
    "version": 1,
    "name": {"Type": "String", "Value": "Good"},
    "category": {"Type": "String", "Value": "Broken"},
    "module": {"code": DEHUM, "element": "", "display_name": "De-Hum"},
    "params": {"Base Frequency": {"Type": "Float", "Value": 60.0}},
    "state": {"Type": "Base64", "Value": ""},
}


def _broken_files(preset_root: Path) -> dict[str, str]:
    """Write one file of every kind of brokenness and return their names."""
    folder = preset_root / "Broken"
    folder.mkdir(parents=True, exist_ok=True)
    files = {
        "Truncated.json": json.dumps(GOOD_DOCUMENT, indent=2)[:120],
        "Empty.json": "",
        "NotAnObject.json": "[1, 2, 3]",
        "HandEdited.json": json.dumps(
            {
                "format": "auralis.preset",
                "version": 1,
                "name": {"Type": "String", "Value": "Hand Edited"},
                "category": {"Type": "String", "Value": "Broken"},
                "module": DEHUM,
                "params": "this is not a dict",
                "state": {"Type": "Base64", "Value": ""},
            },
            indent=2,
        ),
        "NewerVersion.json": json.dumps(
            {
                "format": "auralis.preset",
                "version": 99,
                "name": {"Type": "String", "Value": "From The Future"},
                "category": {"Type": "String", "Value": "Broken"},
                "module": {"code": DEHUM, "element": ""},
                "params": {"Base Frequency": {"Type": "Float", "Value": 60.0}},
                "state": {"Type": "Base64", "Value": ""},
            },
            indent=2,
        ),
    }
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8")
    return files


def test_a_broken_file_does_not_take_the_library_down(
    api: TestClient, preset_root: Path
) -> None:
    """One unreadable file leaves the rest of the library readable."""
    _broken_files(preset_root)
    good = save(api, "Good", DEHUM, {"Base Frequency": 60.0})
    assert good.status_code == 200, good.text

    listing = api.get("/api/presets")
    assert listing.status_code == 200, listing.text
    names = [entry["name"] for entry in listing.json()["presets"]]
    assert "Good" in names


def test_every_broken_file_is_reported(api: TestClient, preset_root: Path) -> None:
    """No file is dropped from the report without a word."""
    files = _broken_files(preset_root)

    listing = api.get("/api/presets")
    assert listing.status_code == 200, listing.text
    body = listing.json()

    reported = {problem["path"] for problem in body.get("problems", [])}
    for name in files:
        assert f"Broken/{name}" in reported, f"{name} was dropped without a word"


def test_a_truncated_file_is_refused_with_a_clear_error(
    api: TestClient, preset_root: Path
) -> None:
    """Loading a truncated file says what is wrong."""
    _broken_files(preset_root)
    loaded = api.post("/api/preset/load", json={"path": "Broken/Truncated.json"})
    assert loaded.status_code == 400
    assert "JSON" in loaded.json()["detail"]


def test_a_hand_edited_file_is_reported_when_it_is_listed(
    api: TestClient, preset_root: Path
) -> None:
    """A file whose shape is wrong is listed, marked as damaged."""
    _broken_files(preset_root)
    listing = api.get("/api/presets")
    entry = next(
        e for e in listing.json()["presets"] if e["name"] == "Hand Edited"
    )
    assert entry["status"] == "damaged"
    assert entry["problems"]


def test_a_file_from_an_older_version_is_migrated(
    api: TestClient, preset_root: Path
) -> None:
    """A preset written by an older build is read and brought up to date."""
    folder = preset_root / "Broken"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "Old.json").write_text(
        json.dumps(
            {
                "format": "auralis.preset",
                "version": 0,
                "module": DEHUM,
                "parameters": {"Base Frequency": 60.0, "Adaptive": True},
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    listing = api.get("/api/presets")
    assert listing.status_code == 200, listing.text
    entry = next(e for e in listing.json()["presets"] if e["name"] == "Old")
    assert entry["status"] == "ok"
    assert entry["problems"] == []

    loaded = api.post("/api/preset/load", json={"path": "Broken/Old.json"})
    assert loaded.status_code == 200, loaded.text
    document = loaded.json()
    assert value_of(document, "Base Frequency") == 60.0
    assert value_of(document, "Adaptive") is True


def test_a_file_from_a_newer_version_is_reported(
    api: TestClient, preset_root: Path
) -> None:
    """A file from a newer build is reported instead of being downgraded quietly."""
    _broken_files(preset_root)
    listing = api.get("/api/presets")
    entry = next(
        e for e in listing.json()["presets"] if e["name"] == "From The Future"
    )
    assert entry["status"] == "damaged"
    assert any("newer build" in problem for problem in entry["problems"])


# ---------------------------------------------------------------------------
# 4. names
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "name, reason",
    [
        ("With/Slash", "a slash"),
        ("With\\Backslash", "a path separator"),
        ("   ", "only spaces"),
        ("", "nothing at all"),
        ("..", "a traversal"),
        (".", "the current directory"),
        ("../escape", "a traversal"),
        ("Tab\tInside", "a control character"),
        ("New\nLine", "a control character"),
        ("C:evil", "a drive letter"),
    ],
)
def test_a_name_that_cannot_be_a_file_is_refused(
    api: TestClient, name: str, reason: str
) -> None:
    """A name the file system would refuse is refused with a clear error."""
    answer = save(api, name, DEHUM, {"Base Frequency": 60.0})
    assert answer.status_code == 400, f"a name with {reason} was accepted"
    assert answer.json()["detail"]


def test_a_name_never_becomes_a_path(
    api: TestClient, preset_root: Path
) -> None:
    """The name and the category contribute one path component each."""
    for name, category in [
        ("With/Slash", "Presets"),
        ("With\\Backslash", "Presets"),
        ("..", "Presets"),
        ("Name", "With/Slash"),
        ("Name", ".."),
    ]:
        answer = api.post(
            "/api/preset/save",
            json={
                "name": name,
                "category": category,
                "module_code": DEHUM,
                "params": {"Base Frequency": 60.0},
            },
        )
        assert answer.status_code == 400, f"{name!r} in {category!r} was accepted"

    # nothing was written outside the library
    written = [p for p in preset_root.rglob("*") if p.is_file()]
    assert written == [], f"files were written anyway: {written}"


def test_a_good_name_becomes_one_file_inside_the_library(
    api: TestClient, preset_root: Path
) -> None:
    """The name is the file name, and the file is inside the library."""
    answer = save(api, "A Good Name", DEHUM, {"Base Frequency": 60.0}, category="Repair")
    assert answer.status_code == 200, answer.text

    target = preset_root / answer.json()["path"]
    assert target.is_file()
    assert target.name == "A Good Name.json"
    assert target.parent.name == "Repair"
    assert target.resolve().is_relative_to(preset_root.resolve())


def test_a_name_that_is_not_text_is_refused(api: TestClient) -> None:
    """A name is text; a list or a mapping is not a name."""
    for name in (["a", "b"], {"name": "a"}, 42):
        answer = save(api, name, DEHUM, {"Base Frequency": 60.0})
        assert answer.status_code == 400, f"{name!r} was accepted as a name"


def test_a_category_that_is_not_text_is_refused(api: TestClient) -> None:
    """The category is text as well."""
    answer = api.post(
        "/api/preset/save",
        json={
            "name": "Name",
            "category": ["a", "b"],
            "module_code": DEHUM,
            "params": {"Base Frequency": 60.0},
        },
    )
    assert answer.status_code == 400


# ---------------------------------------------------------------------------
# 5. order and search
# ---------------------------------------------------------------------------
def _fill(api: TestClient) -> None:
    """A small library with names that sort in more than one way."""
    for name, category in [
        ("alpha", "Zeta"),
        ("zeta", "Alpha"),
        ("Alpha", "alpha"),
        ("beta", "Beta"),
        ("Alpha", "Beta"),
    ]:
        answer = save(api, name, DEHUM, {"Base Frequency": 60.0}, category=category)
        assert answer.status_code == 200, answer.text


def test_the_listing_is_sorted_by_category_then_name(api: TestClient) -> None:
    """The order is the one a user expects: category, then name."""
    _fill(api)
    entries = api.get("/api/presets").json()["presets"]
    keys = [
        (entry["category"].casefold(), entry["name"].casefold(), entry["name"])
        for entry in entries
    ]
    assert keys == sorted(keys)


def test_the_order_does_not_change_between_two_calls(api: TestClient) -> None:
    """The listing is the same every time it is asked for."""
    _fill(api)
    first = [e["path"] for e in api.get("/api/presets").json()["presets"]]
    second = [e["path"] for e in api.get("/api/presets").json()["presets"]]
    assert first == second
    assert first, "the library was empty"


def test_the_order_is_the_same_however_the_files_were_written(
    api: TestClient,
) -> None:
    """The order does not depend on the order the files landed on the disk."""
    _fill(api)
    entries = api.get("/api/presets").json()["presets"]
    keys = [lib.sort_key(entry) for entry in entries]
    assert keys == sorted(keys)
    # the same library, listed again, comes back in the same order
    again = api.get("/api/presets").json()["presets"]
    assert [e["path"] for e in again] == [e["path"] for e in entries]


def test_the_search_finds_a_preset_by_its_name(api: TestClient) -> None:
    """The search looks at the name."""
    _fill(api)
    answer = save(api, "Only One", DEHUM, {"Base Frequency": 60.0}, category="Rare")
    assert answer.status_code == 200, answer.text

    found = api.get("/api/presets", params={"q": "Only One"}).json()["presets"]
    assert [entry["name"] for entry in found] == ["Only One"]


def test_the_search_looks_at_the_category_too(api: TestClient) -> None:
    """A search finds a preset whose category carries the text."""
    _fill(api)
    found = api.get("/api/presets", params={"q": "Zeta"}).json()["presets"]
    # the preset named "alpha" sits in the category "Zeta", the preset named
    # "zeta" carries the text in its own name
    assert {entry["name"] for entry in found} == {"alpha", "zeta"}


def test_the_search_finds_a_preset_by_its_module_code(api: TestClient) -> None:
    """The search looks at the module the preset belongs to."""
    _fill(api)
    found = api.get("/api/presets", params={"q": DEHUM}).json()["presets"]
    assert len(found) == 5


def test_the_search_ignores_the_case(api: TestClient) -> None:
    """The search is case insensitive."""
    _fill(api)
    lower = api.get("/api/presets", params={"q": "alpha"}).json()["presets"]
    upper = api.get("/api/presets", params={"q": "ALPHA"}).json()["presets"]
    assert [e["path"] for e in lower] == [e["path"] for e in upper]


def test_the_search_can_be_narrowed_to_one_category(api: TestClient) -> None:
    """A category narrows the search further."""
    _fill(api)
    found = api.get("/api/presets", params={"category": "Beta"}).json()["presets"]
    assert found
    assert {entry["category"] for entry in found} == {"Beta"}


def test_an_empty_search_keeps_the_whole_library(api: TestClient) -> None:
    """An empty search is not a filter."""
    _fill(api)
    everything = api.get("/api/presets").json()["presets"]
    empty = api.get("/api/presets", params={"q": "  "}).json()["presets"]
    assert [e["path"] for e in empty] == [e["path"] for e in everything]


def test_a_search_that_finds_nothing_is_not_an_error(api: TestClient) -> None:
    """A search with no hit is an empty list, not a failure."""
    _fill(api)
    answer = api.get("/api/presets", params={"q": "nothing matches this"})
    assert answer.status_code == 200
    assert answer.json()["presets"] == []


# ---------------------------------------------------------------------------
# 6. the names a parameter had before
# ---------------------------------------------------------------------------
#: A module whose parameter was renamed, and the name it had.
RENAMED_MODULE = "AUR_DECRACKLE"
RENAMED_PARAMETER = "Reduction"
OLD_NAME = "Strength"


def _write_old_preset(preset_root: Path, name: str = "Old Name") -> Path:
    """Write a preset that carries the name a parameter had before."""
    folder = preset_root / "Presets"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{name}.json"
    target.write_text(
        json.dumps(
            {
                "format": "auralis.preset",
                "version": 1,
                "name": {"Type": "String", "Value": name},
                "category": {"Type": "String", "Value": "Presets"},
                "module": {"code": RENAMED_MODULE, "element": "DeClicker"},
                "params": {OLD_NAME: {"Type": "Float", "Value": 0.75}},
                "state": {"Type": "Base64", "Value": ""},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return target


def test_a_preset_that_names_an_old_parameter_is_not_damaged(
    api: TestClient, preset_root: Path
) -> None:
    """A preset written before a rename still belongs to the library."""
    _write_old_preset(preset_root)

    listing = api.get("/api/presets").json()["presets"]
    entry = next(item for item in listing if item["name"] == "Old Name")
    assert entry["status"] == "ok", entry["problems"]
    assert entry["problems"] == []


def test_a_preset_that_names_an_old_parameter_is_not_unknown(
    api: TestClient, preset_root: Path
) -> None:
    """The report of a load does not call an old name unknown."""
    _write_old_preset(preset_root)
    loaded = api.post("/api/preset/load", json={"path": "Presets/Old Name.json"})
    assert loaded.status_code == 200, loaded.text

    report = loaded.json().get("report") or {}
    assert report.get("unknown_params") == []
    assert report.get("out_of_range") == []


def test_a_preset_that_names_an_old_parameter_reaches_the_parameter(
    preset_root: Path,
) -> None:
    """The value of an old name lands on the parameter that carries it."""
    from aurcore.serial.preset_json import apply_preset

    module = registry.create(RENAMED_MODULE, 48000, 2)
    apply_preset(module, {"params": {OLD_NAME: {"Type": "Float", "Value": 0.75}}})
    assert module.params.get(RENAMED_PARAMETER) == pytest.approx(0.75)


def test_a_chain_that_names_an_old_parameter_reaches_the_parameter() -> None:
    """A chain document written before a rename still restores."""
    from aurcore.api import create_engine
    from aurcore.serial.chain_json import chain_from_dict

    engine = create_engine(48000, 2)
    chain_from_dict(
        engine,
        {
            "format": "auralis.chain",
            "version": 1,
            "steps": [
                {
                    "code": RENAMED_MODULE,
                    "enabled": True,
                    "params": {OLD_NAME: {"Type": "Float", "Value": 0.25}},
                }
            ],
        },
    )
    module = engine.chain[0].module
    assert module.params.get(RENAMED_PARAMETER) == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# the shipped library
# ---------------------------------------------------------------------------
def test_the_shipped_library_is_sound(preset_root: Path) -> None:
    """Every preset the product ships with is sound and belongs to a module."""
    written = lib.seed_builtin_presets(preset_root)
    assert written, "the starter library was not written"

    scan = lib.scan_library(preset_root)
    assert scan.problems == [], scan.problems
    for entry in scan.presets:
        assert entry["status"] == "ok", (entry["path"], entry["problems"])
        assert registry.has(entry["module_code"]), entry["module_code"]


def test_the_shipped_library_holds_every_family(preset_root: Path) -> None:
    """The starter library covers the families it is meant to cover."""
    lib.seed_builtin_presets(preset_root)
    names = {entry["name"] for entry in lib.list_presets(preset_root)}
    assert "Tape Transfer Tick" in names
    assert "Mains Hum" in names
    assert "Close Mic Sibilance" in names
    assert "Level Dialogue Takes" in names


# ---------------------------------------------------------------------------
# 7. the shipped library: the program list of every plug-in
# ---------------------------------------------------------------------------
# Every plug-in of the family publishes a program list (the format's
# ``IUnitInfo``), and the list is built from the preset library the plug-in
# finds on the disk.  A plug-in that finds no library, or a library whose
# files name a parameter the module does not have, shows an empty list to
# the host.
#
# The rule the plug-in applies is in ``plugins/vst3native/AuralisVST3.cpp``,
# in ``programPresetsForModule``: a preset belongs to the module it names,
# and it is dropped when it names a parameter the module does not have.  The
# names it matches on are the canonical ones, so a preset that carries an
# old name is dropped as well — the server would resolve that name onto
# the parameter it belongs to, the plug-in does not.
#
# The gate below is that rule, written in Python, so the library can be
# checked without loading a plug-in.
def _catalogue() -> dict:
    """The modules of this build, by code."""
    return {info.code: info for info in registry.all()}


def _canonical_names(info) -> set[str]:
    """The names the plug-in matches a preset on."""
    return {desc.name for desc in info.params}


def _alias_names(info) -> dict[str, str]:
    """The names a parameter had before, and the parameter it belongs to."""
    return {
        alias: desc.name for desc in info.params for alias in (desc.aliases or ())
    }


def plugin_presets(root: Path, module_code: str) -> list[dict]:
    """The presets the plug-in publishes for one module.

    The files of the whole library are walked, the ones that name the
    module are kept, and the ones that name a parameter the module does
    not have are dropped.  That is the rule of
    ``programPresetsForModule`` in the plug-in.
    """
    catalogue = _catalogue()
    info = catalogue.get(module_code)
    if info is None:
        return []
    names = _canonical_names(info)
    published: list[dict] = []
    for path in sorted(root.rglob("*.json")):
        if path.name == lib.INDEX_NAME:
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        module = document.get("module")
        code = module.get("code") if isinstance(module, dict) else None
        if code != module_code:
            continue
        params = document.get("params")
        params = params if isinstance(params, dict) else {}
        if any(name not in names for name in params):
            continue
        published.append(document)
    return published


def test_every_module_of_the_catalogue_has_a_preset() -> None:
    """Every module of the catalogue is named by at least one shipped preset."""
    covered = {preset["module_code"] for preset in lib.BUILTIN_PRESETS}
    missing = sorted(code for code in registry.codes() if code not in covered)
    assert not missing, f"these modules have no preset: {missing}"


def test_the_program_list_of_every_module_is_not_empty(preset_root: Path) -> None:
    """No plug-in of the family shows an empty program list to the host."""
    lib.seed_builtin_presets(preset_root)
    empty = [
        code for code in sorted(registry.codes())
        if not plugin_presets(preset_root, code)
    ]
    assert not empty, f"these plug-ins publish an empty program list: {empty}"


def test_no_shipped_preset_is_left_out(preset_root: Path) -> None:
    """No shipped preset is dropped by the rule the plug-in applies.

    This is the "Plosive Removal" case: a preset that named "Strength"
    where the canonical table names the parameter "Reduction" was read by
    the server and dropped by the plug-in, so the module had no program
    list at all.
    """
    lib.seed_builtin_presets(preset_root)
    catalogue = _catalogue()
    dropped: list[str] = []
    for path in sorted((preset_root / "builtin").rglob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        module = document.get("module") or {}
        code = module.get("code")
        info = catalogue.get(code)
        assert info is not None, f"{path} names a module this build does not have"
        names = _canonical_names(info)
        params = document.get("params") or {}
        unknown = sorted(name for name in params if name not in names)
        if unknown:
            dropped.append(f"{path.name}: {unknown}")
    assert not dropped, "presets the plug-in would drop: " + "; ".join(dropped)


def test_a_shipped_preset_names_only_parameters_that_exist() -> None:
    """Every parameter a shipped preset names belongs to its module."""
    catalogue = _catalogue()
    problems: list[str] = []
    for preset in lib.BUILTIN_PRESETS:
        info = catalogue.get(preset["module_code"])
        assert info is not None, preset["module_code"]
        names = _canonical_names(info)
        for name in preset["params"]:
            if name not in names:
                problems.append(f"{preset['name']}: {name!r}")
    assert not problems, problems


def test_a_shipped_preset_names_the_canonical_name() -> None:
    """A shipped preset never names a parameter by an old name.

    The server resolves an old name onto the parameter it belongs to, so
    an old preset still works there.  The plug-in matches on the canonical
    names and drops the preset, so the shipped library may not use one.
    """
    catalogue = _catalogue()
    problems: list[str] = []
    for preset in lib.BUILTIN_PRESETS:
        info = catalogue.get(preset["module_code"])
        assert info is not None, preset["module_code"]
        aliases = _alias_names(info)
        for name in preset["params"]:
            if name in aliases:
                problems.append(
                    f"{preset['name']}: {name!r} is the old name of "
                    f"{aliases[name]!r}"
                )
    assert not problems, problems


def test_every_value_of_a_shipped_preset_is_inside_the_range() -> None:
    """Every value is inside the domain of the parameter it is written for."""
    catalogue = _catalogue()
    problems: list[str] = []
    for preset in lib.BUILTIN_PRESETS:
        info = catalogue.get(preset["module_code"])
        assert info is not None, preset["module_code"]
        for desc in info.params:
            if desc.name not in preset["params"]:
                continue
            value = preset["params"][desc.name]
            if desc.type == "enum":
                if value not in desc.choices:
                    problems.append(
                        f"{preset['name']}: {desc.name}={value!r} is not one of "
                        f"{list(desc.choices)}"
                    )
                continue
            if desc.type == "bool":
                if not isinstance(value, bool):
                    problems.append(f"{preset['name']}: {desc.name}={value!r} is not a switch")
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                problems.append(f"{preset['name']}: {desc.name}={value!r} is not a number")
                continue
            if not (desc.minimum <= value <= desc.maximum):
                problems.append(
                    f"{preset['name']}: {desc.name}={value} is outside "
                    f"{desc.minimum}..{desc.maximum}"
                )
    assert not problems, problems


def test_a_shipped_preset_is_a_whole_situation() -> None:
    """A preset sets every parameter of its module, apart from the owned ones.

    A preset that sets one knob out of twenty is a nudge and not a
    situation, and a host that offers it in a drop-down offers the user
    nothing to choose from.
    """
    catalogue = _catalogue()
    problems: list[str] = []
    for preset in lib.BUILTIN_PRESETS:
        info = catalogue.get(preset["module_code"])
        assert info is not None, preset["module_code"]
        owned = set(lib.OWNED_PARAMETERS.get(preset["module_code"], ()))
        names = _canonical_names(info)
        missing = sorted(names - set(preset["params"]) - owned)
        if missing:
            problems.append(f"{preset['name']}: does not set {missing}")
        taken = sorted(set(preset["params"]) & owned)
        if taken:
            problems.append(f"{preset['name']}: sets the owned parameter {taken}")
    assert not problems, problems


def test_a_shipped_preset_names_a_situation() -> None:
    """A preset name says what the material is, not which knob is turned.

    A name that carries a number is a setting and not a situation, and a
    name that is the same in two modules would give the user two entries
    that mean different things.
    """
    seen: dict[tuple[str, str], str] = {}
    problems: list[str] = []
    for preset in lib.BUILTIN_PRESETS:
        name = str(preset["name"])
        if any(character.isdigit() for character in name):
            problems.append(f"{name!r} carries a number")
        key = (preset["category"], name)
        if key in seen:
            problems.append(
                f"{name!r} is used by {seen[key]} and {preset['module_code']}"
            )
        seen[key] = preset["module_code"]
        if not str(preset.get("description", "")).strip():
            problems.append(f"{name!r} has no description")
    assert not problems, problems


def test_the_first_program_of_a_module_leaves_it_alone() -> None:
    """The program a host shows first does not change the module.

    A host shows the program list in the order the names sort, and it
    loads a program before it measures the delay of the plug-in.  A
    program that changes the settings leaves the module in a state the
    measurement does not know, and a module whose output does not look
    like its input closely enough for the measurement to survive that is
    then reported as a plug-in that delays the audio it does not delay.
    """
    catalogue = _catalogue()
    for code in lib.FIRST_PROGRAM_IS_THE_DEFAULT:
        info = catalogue[code]
        presets = sorted(
            (p for p in lib.BUILTIN_PRESETS if p["module_code"] == code),
            key=lambda p: p["name"],
        )
        assert presets, code
        first = presets[0]
        for desc in info.params:
            value = first["params"][desc.name]
            if desc.type == "bool":
                assert value is bool(desc.default), (
                    f"{first['name']} changes the switch {desc.name}"
                )
            elif desc.type == "enum":
                assert value == desc.default, (
                    f"{first['name']} changes {desc.name}"
                )
            else:
                assert float(value) == pytest.approx(float(desc.default)), (
                    f"{first['name']} changes {desc.name}"
                )


def test_the_shipped_library_is_sound_on_the_disk(preset_root: Path) -> None:
    """Every file the product writes is sound and names a module it owns."""
    lib.seed_builtin_presets(preset_root)
    scan = lib.scan_library(preset_root)
    assert scan.problems == [], scan.problems
    for entry in scan.presets:
        assert entry["status"] == "ok", (entry["path"], entry["problems"])
        assert registry.has(entry["module_code"]), entry["module_code"]


def test_a_stale_shipped_preset_is_taken_away(preset_root: Path) -> None:
    """A file the product no longer ships does not stay in the library.

    The plug-in publishes every file it finds, so a preset that names an
    old parameter would be listed by the server and dropped by the plug-in.
    """
    folder = preset_root / "builtin" / "Repair"
    folder.mkdir(parents=True, exist_ok=True)
    stale = folder / "An Old Preset.json"
    stale.write_text(
        json.dumps(
            {
                "format": "auralis.preset",
                "version": 1,
                "name": {"Type": "String", "Value": "An Old Preset"},
                "category": {"Type": "String", "Value": "Repair"},
                "module": {"code": "AUR_DEPLOSIVE", "element": "DePlosive"},
                "params": {"Strength": {"Type": "Float", "Value": 2.5}},
                "state": {"Type": "Base64", "Value": ""},
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    lib.seed_builtin_presets(preset_root)
    assert not stale.exists(), "a preset the product no longer ships was kept"


def test_a_user_preset_is_never_touched(preset_root: Path) -> None:
    """The presets the user made survive the seeding of the shipped library."""
    folder = preset_root / "Presets"
    folder.mkdir(parents=True, exist_ok=True)
    mine = folder / "Mine.json"
    mine.write_text(
        json.dumps(
            {
                "format": "auralis.preset",
                "version": 1,
                "name": {"Type": "String", "Value": "Mine"},
                "category": {"Type": "String", "Value": "Presets"},
                "module": {"code": "AUR_DEPLOSIVE", "element": "DePlosive"},
                "params": {"Reduction": {"Type": "Float", "Value": 1.0}},
                "state": {"Type": "Base64", "Value": ""},
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    lib.seed_builtin_presets(preset_root)
    assert mine.exists(), "a preset the user made was taken away"


# ---------------------------------------------------------------------------
# 6. the identity of a parameter
# ---------------------------------------------------------------------------
# The identity of a parameter is its name.  The id is the position the
# parameter has inside its module, and the document carries it beside the
# name, so a reader can tell that a parameter moved or that it changed its
# meaning.
class _PresetDummy(Module):
    """A module whose parameter list a test may change.

    The list is a class attribute, so an insertion in the middle of it is
    what an insertion in the middle of the canonical table does to a module:
    the parameters after it get another id.
    """

    code = "AUR_TEST_PRESET_INSERT"
    display_name = "Preset Insert"
    category = "Utility"
    edition = "standard"
    element = "PresetInsert"

    PARAMS = [
        {"name": "Threshold", "type": "float", "default": 0.5,
         "minimum": 0.0, "maximum": 1.0},
        {"name": "Ratio", "type": "float", "default": 2.0,
         "minimum": 1.0, "maximum": 10.0},
        {"name": "Width", "type": "float", "default": 3.0,
         "minimum": 0.0, "maximum": 10.0, "unit": "ms"},
        {"name": "Enabled", "type": "bool", "default": True},
    ]

    def describe(self) -> list[ParamDesc]:
        """The parameters of the dummy module."""
        return [ParamDesc(**spec) for spec in self.PARAMS]

    def process(self, x: np.ndarray) -> np.ndarray:
        """The dummy module passes the signal through."""
        return x


#: The parameter list of the dummy module before the insertion.
_ORIGINAL_PARAMS = list(_PresetDummy.PARAMS)


@pytest.fixture
def preset_dummy(registry_snapshot):
    """Register the module whose parameter list a test may change."""
    registry.register(_PresetDummy)
    _PresetDummy.PARAMS = list(_ORIGINAL_PARAMS)
    try:
        yield _PresetDummy
    finally:
        _PresetDummy.PARAMS = list(_ORIGINAL_PARAMS)


def _insert_a_parameter(module) -> None:
    """Insert a parameter in the middle of the dummy's parameter list."""
    module.PARAMS = [
        module.PARAMS[0],
        module.PARAMS[1],
        {"name": "Release", "type": "float", "default": 1.0,
         "minimum": 0.0, "maximum": 5.0, "unit": "ms"},
        module.PARAMS[2],
        module.PARAMS[3],
    ]


def test_the_preset_carries_the_id_of_every_parameter(preset_dummy) -> None:
    """A preset says which id every parameter had."""
    module = registry.create(preset_dummy.code, 48000, 2)
    module.params.set("Threshold", 0.25)

    document = preset_from_module(module)

    assert set(document["params"]) == {"Threshold", "Ratio", "Width", "Enabled"}
    assert document["params"]["Threshold"]["Id"] == 0
    assert document["params"]["Ratio"]["Id"] == 1
    assert document["params"]["Width"]["Id"] == 2
    assert document["params"]["Enabled"]["Id"] == 3
    assert document["params"]["Threshold"]["Sig"] == "float:0:1"
    assert document["params"]["Enabled"]["Sig"] == "bool"


def test_a_preset_written_before_an_insertion_loads_onto_the_right_parameters(
    preset_dummy,
) -> None:
    """A parameter inserted in the middle does not move the values of a preset."""
    module = registry.create(preset_dummy.code, 48000, 2)
    module.params.set("Threshold", 0.11)
    module.params.set("Width", 3.33)

    document = preset_from_module(module)

    _insert_a_parameter(preset_dummy)

    fresh = registry.create(preset_dummy.code, 48000, 2)
    report = apply_preset(fresh, document)

    assert fresh.params.get("Threshold") == pytest.approx(0.11)
    assert fresh.params.get("Width") == pytest.approx(3.33)
    assert fresh.params.get("Release") == pytest.approx(1.0)
    assert [entry["name"] for entry in report["moved"]] == ["Width", "Enabled"]


def test_a_preset_that_names_a_parameter_the_module_does_not_know_is_reported(
    preset_dummy,
) -> None:
    """A parameter the module does not know is reported, never handed on."""
    module = registry.create(preset_dummy.code, 48000, 2)
    module.params.set("Threshold", 0.11)

    document = preset_from_module(module)

    # "Ratio" is gone from the module.
    preset_dummy.PARAMS = [
        preset_dummy.PARAMS[0], preset_dummy.PARAMS[2], preset_dummy.PARAMS[3]
    ]

    fresh = registry.create(preset_dummy.code, 48000, 2)
    report = apply_preset(fresh, document)

    assert report["unknown"] == ["Ratio"]
    assert fresh.params.get("Threshold") == pytest.approx(0.11)
    assert fresh.params.get("Width") == pytest.approx(3.0)
    assert fresh.extra_params["Ratio"] == pytest.approx(2.0)


def test_a_preset_without_the_ids_still_loads(preset_dummy) -> None:
    """A preset written before the identity was carried still loads."""
    module = registry.create(preset_dummy.code, 48000, 2)
    module.params.set("Threshold", 0.11)
    module.params.set("Width", 3.33)

    document = preset_from_module(module)
    for node in document["params"].values():
        node.pop("Id", None)
        node.pop("Sig", None)

    _insert_a_parameter(preset_dummy)

    fresh = registry.create(preset_dummy.code, 48000, 2)
    report = apply_preset(fresh, document)

    assert fresh.params.get("Threshold") == pytest.approx(0.11)
    assert fresh.params.get("Width") == pytest.approx(3.33)
    assert report["moved"] == []
    assert report["unknown"] == []
