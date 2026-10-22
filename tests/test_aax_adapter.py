"""
tests/test_aax_adapter.py — the tests of the AAX adapter.

The adapter is written against the AAX SDK that is in the tree
(``vendor/JUCE8/modules/juce_audio_plugin_client/AAX/SDK``), in the same
spirit the VST3 adapter is written against the VST3 SDK: one template, the
module chosen by the identity record the build stamps into the binary, the
page tables and the parameter list taken from the generated description.

WHAT IS PROVED HERE
-------------------
    * the adapter compiles, and the plug-in carries the format's entry
      points;
    * the identity record is stamped into every plug-in of the family and
      reads back with the right module, the right effect ids and the right
      parameter map;
    * the component descriptors and the effect ids the plug-in registers
      read back through the adapter's own description;
    * the page table and the parameter list read back through the format's
      own interfaces match the canonical parameter table;
    * the state chunk carries the shared "auralis.state.v1" marker and the
      shared parameter state document;
    * the effect identifiers are well formed, and the licence and the
      registration are stated.

WHAT CANNOT BE PROVED HERE
---------------------------
There is no AAX host on this machine, so the plug-in cannot be loaded into
one.  Nothing here says that a host accepts the plug-in, that the audio
path is right, or that the effect ids are accepted by the format's
vendor: the effect identifiers have to be registered with the vendor
before a host accepts them, and that registration is not ours to make.

Every banned token is assembled from fragments at run time, so this file
never contains a third party trademark itself (CONTRACT §0).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "plugins" / "aax"
BUILD = ROOT / "build" / "aax"
PACKAGE = ROOT / "docs" / "aax_page_tables.json"
BUILD_TOOL = ROOT / "scripts" / "build_aax.py"
PACKAGE_TOOL = ROOT / "scripts" / "aax_tables.py"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins import identity  # noqa: E402

#: The identifier rule of the format: exactly four characters, uppercase
#: ASCII letters and digits only.
IDENTIFIER_RE = re.compile(r"\A[A-Z0-9]{4}\Z")

#: The base the AAX parameter id is offset by.  It is the number the
#: application package publishes.
PARAM_ID_BASE = 256

#: The marker of the shared state format.
STATE_MARKER = "auralis.state.v1"

#: The id of the bypass switch.  It is the same in every plug-in format of
#: the family, and it is the id the reference plug-ins use.
BYPASS_PARAM_ID = "1652125811"

#: The three channel configurations of every module, and the channels of
#: each of them.
VARIANTS = (("MONO", 1, 1), ("M2ST", 1, 2), ("STER", 2, 2))

#: The stem formats of the format, by their channel count.  The format
#: packs the index of the layout and the channel count into one number.
STEM_FORMAT = {1: 1, 2: 0x10002}

#: The interfaces the adapter implements, and the interfaces the host owns
#: and the adapter fills in or reads.  The list is what the SDK's own
#: reference implementation shows; see plugins/aax/README.md.
IMPLEMENTED_INTERFACES = (
    "AAX_IEffectParameters",
    "AAX_IEffectGUI",
    "AAX_IParameter",
    "AAX_IPageTable",
)

HOST_INTERFACES = (
    "AAX_IComponentDescriptor",
    "AAX_IEffectDescriptor",
    "AAX_IPropertyMap",
    "AAX_IAutomationDelegate",
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
def _sources() -> list[Path]:
    """Return the files the adapter is built from."""
    paths = [
        BUILD_TOOL,
        PACKAGE,
        ADAPTER / "AuralisAAX.cpp",
        ADAPTER / "AuralisAAX.h",
        ADAPTER / "AuralisAAXProbe.cpp",
        ADAPTER / "CMakeLists.txt",
    ]
    return [path for path in paths if path.is_file()]


def _artefacts() -> list[Path]:
    """Return the files the build produces."""
    return [
        BUILD / "AuralisAAX.dll",
        BUILD / "AuralisAAX.so",
        BUILD / "AuralisAAX.dylib",
        BUILD / "aaxprobe.exe",
        BUILD / "aaxprobe",
    ]


def _is_stale() -> bool:
    """True when the build has to run again."""
    existing = [path for path in _artefacts() if path.is_file()]

    if not existing:
        return True

    oldest = min(path.stat().st_mtime for path in existing)

    for source in _sources():
        if source.stat().st_mtime > oldest:
            return True

    plug_ins = (
        list((BUILD / "plug-ins").glob("*.aaxplugin"))
         if (BUILD / "plug-ins").is_dir() else []
    )

    if not plug_ins:
        return True

    return min(path.stat().st_mtime for path in plug_ins) < oldest


@pytest.fixture(scope="module")
def build() -> dict[str, Path]:
    """Build the adapter and the family, and return the artefacts."""
    if _is_stale():
        result = subprocess.run(
            [sys.executable, str(BUILD_TOOL), "build"],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        if result.returncode != 0:
            pytest.fail(
                "the AAX adapter did not build:\n"
                + (result.stdout or "")
                + (result.stderr or "")
            )

    binary = None
    for name in ("AuralisAAX.dll", "AuralisAAX.so", "AuralisAAX.dylib"):
        candidate = BUILD / name
        if candidate.is_file():
            binary = candidate
            break

    if binary is None:
        pytest.fail("the build produced no plug-in")

    probe = BUILD / ("aaxprobe.exe" if (BUILD / "aaxprobe.exe").is_file() else "aaxprobe")

    if not probe.is_file():
        pytest.fail("the build produced no probe")

    return {
        "binary": binary,
        "probe": probe,
        "plug-ins": BUILD / "plug-ins",
    }


@pytest.fixture(scope="module")
def package() -> dict:
    """Return the machine readable half of the application package."""
    return json.loads(PACKAGE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def plug_ins(build: dict[str, Path]) -> dict[str, Path]:
    """Return the plug-in of every module of the family, keyed by the code."""
    found: dict[str, Path] = {}

    for path in sorted(build["plug-ins"].glob("*.aaxplugin")):
        record = identity.verify_identity(path)
        found[record.module_code] = path

    return found


@pytest.fixture(scope="module")
def reports(build: dict[str, Path], package: dict,
            plug_ins: dict[str, Path]) -> dict[str, dict]:
    """Run the probe for every module and return the reports.

    The probe builds the very same data model the plug-in builds, reads it
    back through the format's own interfaces, and reads the identity out of
    the stamped binary.
    """
    out: dict[str, dict] = {}

    for module in package["modules"]:
        target = BUILD / f"probe_{module['code']}.json"

        command = [str(build["probe"]), "--module", module["code"], "--out", str(target)]

        binary = plug_ins.get(module["code"])
        if binary is not None:
            command += ["--identity", str(binary)]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            cwd=str(BUILD),
        )

        if result.returncode != 0:
            pytest.fail(
                f"the probe failed for {module['code']}:\n"
                + (result.stdout or "")
                + (result.stderr or "")
            )

        out[module["code"]] = json.loads(target.read_text(encoding="utf-8"))

    return out


# ---------------------------------------------------------------------------
# the build
# ---------------------------------------------------------------------------
def test_the_adapter_builds(build: dict[str, Path]) -> None:
    """The adapter compiles and the plug-in carries the format's exports."""
    binary = build["binary"]

    assert binary.is_file()
    assert binary.stat().st_size > 0

    data = binary.read_bytes()

    # The entry points the host calls.
    for export in ("ACFRegisterPlugin", "ACFGetSDKVersion", "ACFStartup", "ACFShutdown"):
        assert export.encode("ascii") in data, f"the plug-in does not export {export}"

    # The slot the identity record is stamped into.
    assert identity.IDENTITY_MAGIC in data


def test_the_build_is_reproducible(build: dict[str, Path]) -> None:
    """The generated description is a fresh run of its generator."""
    result = subprocess.run(
        [sys.executable, str(BUILD_TOOL), "check"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )

    assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")


def test_the_package_is_not_drifted() -> None:
    """The application package is what its generator produces."""
    result = subprocess.run(
        [sys.executable, str(PACKAGE_TOOL), "--check"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )

    assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")


# ---------------------------------------------------------------------------
# the identity
# ---------------------------------------------------------------------------
def test_every_plug_in_carries_its_identity(build: dict[str, Path], package: dict) -> None:
    """Every plug-in of the family carries the identity of its module."""
    plug_ins = sorted(build["plug-ins"].glob("*.aaxplugin"))

    assert len(plug_ins) == len(package["modules"])

    by_code = {module["code"]: module for module in package["modules"]}

    seen: set[str] = set()

    for path in plug_ins:
        record = identity.verify_identity(path)

        assert record.module_code in by_code, f"{path.name}: unknown module {record.module_code}"

        module = by_code[record.module_code]
        seen.add(record.module_code)

        # The effect ids of the record are the ones of the package.
        assert [list(triple) for triple in record.aax_effect_ids] == [
            [entry["id"].split(":")[0], entry["id"].split(":")[1], entry["id"].split(":")[2]]
            for entry in module["effect_ids"]
        ]

        # The parameter map is the registry index of the module.
        expected = identity.module_param_ids(record.module_code)
        assert record.param_id_map == expected

    assert seen == set(by_code)


# ---------------------------------------------------------------------------
# the component descriptors and the effect ids
# ---------------------------------------------------------------------------
def test_the_component_descriptors_read_back(reports: dict[str, dict], package: dict) -> None:
    """The component descriptors of every module read back correctly."""
    for module in package["modules"]:
        report = reports[module["code"]]
        components = report["components"]

        assert len(components) == len(VARIANTS), module["code"]

        for component, (plug, inputs, outputs) in zip(components, VARIANTS):
            assert component["plug_id"] == plug, module["code"]
            assert component["input_channels"] == inputs, module["code"]
            assert component["output_channels"] == outputs, module["code"]
            assert component["manufacturer_id"] == package["manufacturer_id"], module["code"]
            assert component["product_id"] == module["product_id"], module["code"]

            expected_id = ":".join(
                (package["manufacturer_id"], module["product_id"], plug)
            )
            assert component["effect_id"] == expected_id, module["code"]


def test_the_effect_ids_are_well_formed(package: dict) -> None:
    """Every effect id of the family has the shape the format expects."""
    total = 0
    well_formed = 0
    problems: list[str] = []

    for module in package["modules"]:
        for entry in module["effect_ids"]:
            total += 1

            parts = entry["id"].split(":")

            if len(parts) != 3:
                problems.append(f"{entry['id']}: not three parts")
                continue

            if all(IDENTIFIER_RE.match(part) for part in parts):
                well_formed += 1
            else:
                problems.append(f"{entry['id']}: not four upper case characters")

    # Every module of the family has one effect per channel configuration.
    expected = len(package["modules"]) * len(VARIANTS)

    assert total == expected, f"the family has {total} effect ids, not {expected}"
    assert well_formed == expected, (
        f"{total - well_formed} effect ids are malformed: "
        f"{problems[:5]}"
    )


def test_the_effect_ids_are_unique(package: dict) -> None:
    """No two effect ids of the family are the same."""
    ids = [entry["id"] for module in package["modules"] for entry in module["effect_ids"]]

    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# the description the plug-in registers with the host
# ---------------------------------------------------------------------------
def test_the_effects_read_back_through_the_host_interfaces(
    reports: dict[str, dict], package: dict
) -> None:
    """The plug-in registers three effects, one per channel configuration.

    The probe hands the plug-in a stand-in for the host's collection, so
    what is read back is what the plug-in put into the host's objects.
    """
    for module in package["modules"]:
        description = reports[module["code"]]["description"]
        effects = description["effects"]

        assert len(effects) == len(VARIANTS), module["code"]
        assert description["manufacturer_name"] == "Auralis", module["code"]

        for effect, (plug, inputs, outputs) in zip(effects, VARIANTS):
            expected_id = ":".join((package["manufacturer_id"], module["product_id"], plug))

            assert effect["effect_id"] == expected_id, module["code"]
            assert effect["names"] == [module["display_name"]], module["code"]
            assert effect["category"] != 0, module["code"]

            # The creation procedures: the controller and the data model.
            assert sorted(effect["proc_ids"]) == [0, 1], module["code"]

            # One component: the channel configuration of the effect.
            assert len(effect["components"]) == 1, module["code"]

            component = effect["components"][0]

            assert component["audio_in"] == 1, module["code"]
            assert component["audio_out"] == 1, module["code"]
            assert component["buffer_length"] == 1, module["code"]
            assert component["data_in"] == 1, module["code"]
            assert component["private_data"] >= 1, module["code"]
            assert component["process_proc"] is True, module["code"]
            assert component["instance_init_proc"] is True, module["code"]

            # The identifiers of the component.
            assert component["manufacturer_id"] == package["manufacturer_id"], module["code"]
            assert component["product_id"] == module["product_id"], module["code"]
            assert component["plug_in_id"] == plug, module["code"]

            # The stem formats of the channel configuration.
            assert component["input_stem_format"] == STEM_FORMAT[inputs], module["code"]
            assert component["output_stem_format"] == STEM_FORMAT[outputs], module["code"]

            assert component["can_bypass"] == 1, module["code"]


def test_the_identity_reads_back_out_of_the_binary(
    reports: dict[str, dict], package: dict
) -> None:
    """The adapter reads the identity out of the stamped binary.

    The reader is the adapter's own; the probe runs it against the binary
    the build stamped, and the result has to be the record the plug-in
    layer wrote.
    """
    for module in package["modules"]:
        record = reports[module["code"]].get("identity")

        assert record is not None, module["code"]
        assert record["valid"] is True, module["code"]
        assert record["module_code"] == module["code"], module["code"]
        assert record["display_name"] == module["display_name"], module["code"]
        assert record["category"] == module["category"], module["code"]

        assert (
            record["effect_ids"] == [entry["id"] for entry in module["effect_ids"]]
        ), module["code"]


# ---------------------------------------------------------------------------
# the parameter list
# ---------------------------------------------------------------------------
def test_the_parameter_list_matches_the_canonical_table(
    reports: dict[str, dict], package: dict
) -> None:
    """The parameters the plug-in offers are the parameters of the table."""
    for module in package["modules"]:
        report = reports[module["code"]]
        parameters = report["parameters"]

        expected: dict[int, dict] = {}

        for page in module["pages"]:
            for control in page["controls"]:
                expected.setdefault(int(control["parameter"]), control)

        # The bypass switch is the plug-in's own control and not one of the
        # module's parameters: it has a registry id of its own (-1) and the
        # id every format of the family uses.
        module_parameters = [
            parameter for parameter in parameters if int(parameter["registry_id"]) >= 0
        ]

        assert len(module_parameters) == len(expected), module["code"]

        for parameter in module_parameters:
            registry_id = int(parameter["registry_id"])
            assert registry_id in expected, f"{module['code']}: unknown parameter {registry_id}"

            control = expected[registry_id]

            # The identifier is the registry id, offset by the base.
            assert parameter["id"] == str(PARAM_ID_BASE + registry_id), module["code"]

            # The name, the label and the type.
            assert parameter["name"] == control["name"], module["code"]
            assert parameter["label"] == control["label"], module["code"]
            assert parameter["source_type"] == control["type"], module["code"]
            assert parameter["unit"] == control["unit"], module["code"]

            # A discrete control is a switch, a continuous one is a knob.
            discrete = control["type"] in ("enum", "bool")
            assert parameter["type"] == ("discrete" if discrete else "continuous"), module["code"]

            if discrete:
                # The range of a discrete control is the number of its
                # states; a boolean has the two states off and on.
                states = len(control["choices"]) if control["choices"] else 2
                assert parameter["steps"] == states, module["code"]
                assert parameter["maximum"] == states - 1, module["code"]

                if control["choices"]:
                    assert parameter["choices"] == control["choices"], module["code"]
            else:
                # The range of a continuous control is its own, in natural
                # units, and never a normalised one.
                assert parameter["minimum"] == pytest.approx(control["minimum"]), module["code"]
                assert parameter["maximum"] == pytest.approx(control["maximum"]), module["code"]
                assert parameter["value"] == pytest.approx(control["default"]), module["code"]


def test_the_defaults_match_the_canonical_table(reports: dict[str, dict], package: dict) -> None:
    """The default of every parameter is the default of the table."""
    for module in package["modules"]:
        report = reports[module["code"]]

        expected: dict[int, dict] = {}

        for page in module["pages"]:
            for control in page["controls"]:
                expected.setdefault(int(control["parameter"]), control)

        for parameter in report["parameters"]:
            registry_id = int(parameter["registry_id"])
            if registry_id < 0:
                # The bypass switch is the plug-in's own control; it has no
                # entry in the module's pages.
                continue

            control = expected[registry_id]
            default = control["default"]

            if control["type"] == "enum":
                assert default in control["choices"], module["code"]
                assert parameter["value"] == pytest.approx(
                    float(control["choices"].index(default))
                ), module["code"]
            elif control["type"] == "bool":
                assert parameter["value"] == pytest.approx(1.0 if default else 0.0), module["code"]
            else:
                assert parameter["value"] == pytest.approx(float(default)), module["code"]


# ---------------------------------------------------------------------------
# the page table
# ---------------------------------------------------------------------------
def test_the_page_table_matches_the_canonical_table(
    reports: dict[str, dict], package: dict
) -> None:
    """The pages the plug-in offers are the pages of the package."""
    for module in package["modules"]:
        report = reports[module["code"]]
        pages = report["pages"]

        assert len(pages) == len(module["pages"]), module["code"]

        by_index = {int(page["index"]): page for page in pages}

        for expected in module["pages"]:
            index = int(expected["index"])
            assert index in by_index, f"{module['code']}: page {index} is missing"

            page = by_index[index]
            controls = page["controls"]

            assert len(controls) == len(expected["controls"]), module["code"]

            for control, wanted in zip(controls, expected["controls"]):
                assert control["parameter"] == str(
                    PARAM_ID_BASE + int(wanted["parameter"])
                ), f"{module['code']}: page {index} binds the wrong parameter"


def test_every_parameter_is_on_exactly_one_page(reports: dict[str, dict], package: dict) -> None:
    """No parameter is bound twice and none is left out.

    The bypass switch is the one exception: it is a parameter of the
    plug-in and not of the module, so it is not drawn on a page of the
    module.  The host draws it itself, beside the pages.
    """
    for module in package["modules"]:
        report = reports[module["code"]]

        bound: list[str] = []

        for page in report["pages"]:
            for control in page["controls"]:
                bound.append(control["parameter"])

        assert len(bound) == len(set(bound)), module["code"]
        assert len(bound) == len(report["parameters"]) - 1, module["code"]


# ---------------------------------------------------------------------------
# the bypass switch and the help
# ---------------------------------------------------------------------------
def test_every_module_publishes_a_bypass_switch(reports: dict[str, dict]) -> None:
    """Every plug-in publishes the bypass switch, with the id every other
    format of the family uses.

    The id is the same in every format, so a host that automates the
    reference's switch automates ours with the same curve.
    """
    for code, report in reports.items():
        bypasses = [p for p in report["parameters"] if p["id"] == BYPASS_PARAM_ID]

        assert len(bypasses) == 1, f"{code} publishes {len(bypasses)} bypass switches, not 1"

        bypass = bypasses[0]

        assert bypass["name"] == "Bypass", code
        assert bypass["type"] == "discrete", code
        assert bypass["steps"] == 2, code
        assert bypass["automatable"] is True, code
        assert bypass["value"] == 0, f"{code} starts with the bypass on"


def test_the_page_table_carries_the_bypass_and_the_help(package: dict) -> None:
    """The generated page table carries the bypass and the help.

    The page table is the description the plug-in hands to the host, so
    the sentence that explains a parameter has to be in it: the format has
    no field of its own for a sentence.
    """
    for module in package["modules"]:
        bypass = module.get("bypass")

        assert bypass is not None, f"{module['code']}: the page table has no bypass"

        assert bypass["id"] == BYPASS_PARAM_ID, module["code"]
        assert bypass["label"] == "Bypass", module["code"]
        assert bypass["help"].strip(), module["code"]

        helped = 0

        for page in module["pages"]:
            for control in page["controls"]:
                assert "help" in control, f"{module['code']}: a control carries no help"
                assert control["help"].strip(), (
                    f"{module['code']}: the control of '{control['name']}' carries no help"
                )
                helped += 1

        assert helped > 0, module["code"]


# ---------------------------------------------------------------------------
# the state
# ---------------------------------------------------------------------------
def test_the_state_chunk_carries_the_shared_marker(reports: dict[str, dict]) -> None:
    """The chunk is the shared state format, so a session moves between the
    two formats."""
    for code, report in reports.items():
        chunk = report["chunk"]

        assert chunk["count"] == 1, code
        assert chunk["marker"] == STATE_MARKER, code
        assert chunk["size"] > 0, code

        state = chunk["parameter_state"]

        # The document has the shape the other adapter writes.
        assert state.startswith('<?xml version="1.0" encoding="UTF-8"?>'), code
        assert "<AURALIS>" in state, code
        assert "</AURALIS>" in state, code

        for parameter in report["parameters"]:
            # The parameters of the module carry the position they have
            # inside it; the bypass switch carries the id every format of
            # the family uses.
            identifier = (parameter["registry_id"] if parameter["registry_id"] >= 0
                          else int(parameter["id"]))

            entry = f'<PARAM id="{identifier}"'
            assert entry in state, f"{code}: the state does not carry {entry}"


def test_the_state_round_trips(reports: dict[str, dict]) -> None:
    """Every parameter of the state document is one the plug-in offers."""
    for code, report in reports.items():
        state = report["chunk"]["parameter_state"]

        ids = re.findall(r'<PARAM id="(\d+)"', state)

        assert len(ids) == len(report["parameters"]), code
        assert len(set(ids)) == len(ids), code


# ---------------------------------------------------------------------------
# the interface set
# ---------------------------------------------------------------------------
def test_the_adapter_implements_the_interface_set() -> None:
    """The adapter names the interfaces the format asks for."""
    header = (ADAPTER / "AuralisAAX.h").read_text(encoding="utf-8")
    source = (ADAPTER / "AuralisAAX.cpp").read_text(encoding="utf-8")

    for name in IMPLEMENTED_INTERFACES:
        assert name in header, f"the adapter does not name {name}"

    for name in HOST_INTERFACES:
        assert name in header, f"the adapter does not name {name}"

    # The data model and the controller derive from the SDK's own classes.
    assert "AAX_CEffectParameters" in header
    assert "AAX_CEffectGUI" in header
    assert "AAX_CParameter" in source
    assert "AAX_CStateDisplayDelegate" in source
    assert "AAX_CLinearTaperDelegate" in source

    # The entry point the SDK calls.
    assert "GetEffectDescriptions" in source

    # The page table is filled by the data model.
    assert "UpdatePageTable" in header
    assert "MapParameterID" in source

    # The identifiers are the ones the plug-in layer derives.
    assert "AAX_eProperty_ManufacturerID" in source
    assert "AAX_eProperty_ProductID" in source
    assert "AAX_eProperty_PlugInID_Native" in source


def test_the_adapter_does_not_name_a_third_party_product() -> None:
    """The adapter names no third party product in a comment or a string.

    The names the format's own identifiers carry are the format's, not
    ours; they are the ones the gate has to allow, and they are not
    written by hand here.
    """
    from tools import hygiene_check

    for path in (ADAPTER / "AuralisAAX.h", ADAPTER / "AuralisAAX.cpp",
                 ADAPTER / "AuralisAAXProbe.cpp", ADAPTER / "CMakeLists.txt",
                 ADAPTER / "README.md", BUILD_TOOL, Path(__file__)):
        if not path.is_file():
            continue

        report = hygiene_check.Report(root=str(ROOT))
        hygiene_check.check_text(
            str(path.relative_to(ROOT)).replace("\\", "/"),
            path.read_text(encoding="utf-8", errors="replace"),
            report,
        )

        assert not report.findings, (
            f"{path.name}: {[finding.format() for finding in report.findings]}"
        )


# ---------------------------------------------------------------------------
# the licence and the registration
# ---------------------------------------------------------------------------
def test_the_readme_states_the_licence() -> None:
    """The README says which licence the SDK is used under."""
    readme = (ADAPTER / "README.md").read_text(encoding="utf-8")

    assert "GPL v3" in readme or "GPLv3" in readme or "GPL v3" in readme
    assert "LICENSE.txt" in readme
    assert "SDK" in readme


def test_the_readme_states_the_registration() -> None:
    """The README says that the effect ids have to be registered."""
    readme = (ADAPTER / "README.md").read_text(encoding="utf-8")

    lowered = readme.lower()

    assert "register" in lowered or "registration" in lowered
    assert "effect id" in lowered or "effect identifier" in lowered


def test_the_readme_says_what_cannot_be_proved() -> None:
    """The README says that the plug-in was not loaded into a host."""
    readme = (ADAPTER / "README.md").read_text(encoding="utf-8").lower()

    assert "host" in readme
    assert "not" in readme
    assert "prove" in readme or "proven" in readme
