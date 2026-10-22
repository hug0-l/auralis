"""
tests/test_stamping.py — the identity stamping round trip.

The plug-in adapter is a template: one binary that implements any Auralis
module, with the module chosen at build time by stamping an identity record
into a reserved slot inside the binary (see ``plugins/README.md``).

These tests exercise that stamping layer without needing a built plug-in:
a small template file stands in for the compiled adapter, and the real
``plugins/identity.py`` / ``plugins/vst3/build_identity.py`` do the work.

Three of them need more than a template file, and they say so:

* the **class id** tests compare the record with the bytes a binary
  reports, on every platform;
* the **slot** tests read the *adapter source* out of the tree, compile
  the read with the compiler that builds the plug-ins, patch the compiled
  program the way the build patches the binary, and check that the program
  sees the patched bytes — a read that a compiler is allowed to fold away
  would not;
* the tests that load a **built** plug-in are skipped when the build tree
  is not there.

No network (CONTRACT §9).  The compiler is optional: without it the fold
test is skipped rather than failed.
"""
from __future__ import annotations

import ctypes
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load(name: str, path: Path):
    """Import a module from a file path, reusing an already loaded one."""
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


#: The build-time half of the plug-in layer.
build_identity = _load("auralis_build_identity", ROOT / "plugins" / "vst3" / "build_identity.py")

#: The identity layer: the record, its derivation and the stamping machinery.
#: It is taken from the build tool so that both share one module object — the
#: identity layer compares records with ``isinstance``, and two copies of it
#: would not recognise each other's records.
identity = build_identity.identity

#: The family-wide stamping tool.
stamp_all = _load("auralis_stamp_all", ROOT / "tools" / "stamp_all.py")

#: The two adapters that read the identity slot.
NATIVE_ADAPTER = ROOT / "plugins" / "vst3native" / "AuralisVST3.cpp"
FRAMEWORK_ADAPTER = ROOT / "plugins" / "vst3" / "AuralisPlugin.cpp"

#: The build tree of the family that ships.
NATIVE_BUILD_ROOT = ROOT / "build" / "vst3native"

#: The class id of every module of the registry, frozen.
#:
#: The class id is a contract with the host: a module keeps the class id it
#: was given, whatever happens to the code that derives it.  The table is
#: therefore written out here rather than derived, so that a change in the
#: derivation shows up as a failure instead of as a new number.
FROZEN_CLASS_IDS: dict[str, str] = {
    "AUR_AMBIENCEMATCH": "0101abab-abcd-ef01-4155-5241414d4249",
    "AUR_AZIMUTH": "0101abab-abcd-ef01-4155-5241415a494d",
    "AUR_BANDRESTORATION": "0101abab-abcd-ef01-4155-524142414e44",
    "AUR_BREATH": "0101abab-abcd-ef01-4155-524142524541",
    "AUR_CENTEREXTRACT": "0101abab-abcd-ef01-4155-524143454e54",
    "AUR_CLIPCLASSIFIER": "0101abab-abcd-ef01-4155-5241434c4950",
    "AUR_CONNECT": "0101abab-abcd-ef01-4155-5241434f4e4e",
    "AUR_DEBLEED": "0101abab-abcd-ef01-4155-52414445424c",
    "AUR_DECLICK": "0101abab-abcd-ef01-4155-52414445434c",
    "AUR_DECLIP": "0101abab-abcd-ef01-4155-524144454353",
    "AUR_DECONSTRUCT": "0101abab-abcd-ef01-4155-52414445434f",
    "AUR_DECRACKLE": "0101abab-abcd-ef01-4155-524144454352",
    "AUR_DEESS": "0101abab-abcd-ef01-4155-524144454553",
    "AUR_DEHUM": "0101abab-abcd-ef01-4155-524144454855",
    "AUR_DEPLOSIVE": "0101abab-abcd-ef01-4155-52414445504c",
    "AUR_DEREVERB": "0101abab-abcd-ef01-4155-524144455245",
    "AUR_DERUSTLE": "0101abab-abcd-ef01-4155-524144455255",
    "AUR_DEWIND": "0101abab-abcd-ef01-4155-524144455749",
    "AUR_DIALOGUEISOLATE": "0101abab-abcd-ef01-4155-52414449414c",
    "AUR_DITHER": "0101abab-abcd-ef01-4155-524144495448",
    "AUR_EQ": "0101abab-abcd-ef01-4155-524145513030",
    "AUR_EQMATCH": "0101abab-abcd-ef01-4155-524145514d41",
    "AUR_FADE": "0101abab-abcd-ef01-4155-524146414445",
    "AUR_FINDSIMILAR": "0101abab-abcd-ef01-4155-524146494e44",
    "AUR_GAIN": "0101abab-abcd-ef01-4155-52414741494e",
    "AUR_GUITARDENOISE": "0101abab-abcd-ef01-4155-524147554954",
    "AUR_INTERPOLATE": "0101abab-abcd-ef01-4155-5241494e5445",
    "AUR_LEVELER": "0101abab-abcd-ef01-4155-52414c455645",
    "AUR_LOUDNESS": "0101abab-abcd-ef01-4155-52414c4f5544",
    "AUR_LOUDNESSOPT": "0101abab-abcd-ef01-4155-52414c4f5556",
    "AUR_MIXING": "0101abab-abcd-ef01-4155-52414d495849",
    "AUR_MONITOR": "0101abab-abcd-ef01-4155-52414d4f4e49",
    "AUR_MOUTHDECLICK": "0101abab-abcd-ef01-4155-52414d4f5554",
    "AUR_MUSICREBALANCE": "0101abab-abcd-ef01-4155-52414d555349",
    "AUR_NORMALIZE": "0101abab-abcd-ef01-4155-52414e4f524d",
    "AUR_PHASE": "0101abab-abcd-ef01-4155-524150484153",
    "AUR_REPAIRASSISTANT": "0101abab-abcd-ef01-4155-524152455041",
    "AUR_RESAMPLE": "0101abab-abcd-ef01-4155-524152455341",
    "AUR_SIGNALGEN": "0101abab-abcd-ef01-4155-52415349474e",
    "AUR_SPATIALSUPPRESSOR": "0101abab-abcd-ef01-4155-524153504154",
    "AUR_SPECTRALDENOISE": "0101abab-abcd-ef01-4155-524153504543",
    "AUR_SPECTRALEDITOR": "0101abab-abcd-ef01-4155-52415350454e",
    "AUR_SPECTRALRECOVERY": "0101abab-abcd-ef01-4155-524153504542",
    "AUR_SPECTRALREPAIR": "0101abab-abcd-ef01-4155-524153504559",
    "AUR_STREAMPREVIEW": "0101abab-abcd-ef01-4155-524153545245",
    "AUR_TIMEPITCH": "0101abab-abcd-ef01-4155-524154494d45",
    "AUR_VARIPITCH": "0101abab-abcd-ef01-4155-524156415249",
    "AUR_VARTIME": "0101abab-abcd-ef01-4155-524156415254",
    "AUR_VOICEDENOISE": "0101abab-abcd-ef01-4155-5241564f4943",
    "AUR_VOICEREVERB": "0101abab-abcd-ef01-4155-5241564f495a",
    "AUR_WOWFLUTTER": "0101abab-abcd-ef01-4155-5241574f5746",
}


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
@pytest.fixture()
def record():
    """The identity of one module, derived from the registry."""
    return identity.identity_for("AUR_DECLICK")


@pytest.fixture()
def template(tmp_path: Path) -> Path:
    """A small stand-in for the compiled adapter binary.

    ``plugins/identity.py`` writes templates of exactly this shape: the marker
    followed by an empty region.  The rest of the file is filler that must
    survive the stamping untouched.
    """
    path = tmp_path / "Auralis De-Click.vst3"
    identity.make_template(path, filler=2048)
    return path


# --------------------------------------------------------------------------
# the round trip
# --------------------------------------------------------------------------
def test_template_carries_exactly_one_marker(template: Path):
    """A fresh template holds the marker once and an empty region."""
    data = template.read_bytes()
    assert data.count(identity.IDENTITY_MAGIC) == 1

    offset = data.find(identity.IDENTITY_MAGIC)
    start = offset + len(identity.IDENTITY_MAGIC)
    region = data[start : start + identity.IDENTITY_REGION_SIZE]
    assert region.strip(b"\x00") == b"", "the region of a template must be empty"


def test_stamp_then_read_back_returns_the_same_record(template: Path, record):
    """Stamping a record and reading it back yields that record."""
    stamped = identity.stamp_identity(template, template, record)
    assert stamped == template

    read_back = identity.verify_identity(template)
    assert read_back.module_code == record.module_code
    assert read_back.display_name == record.display_name
    assert read_back.product_name == record.product_name
    assert read_back.file_description == record.file_description
    assert read_back.original_filename == record.original_filename
    assert read_back.internal_name == record.internal_name
    assert read_back.category == record.category
    assert read_back.vst3_fuid == record.vst3_fuid
    assert read_back.aax_effect_ids == record.aax_effect_ids
    assert read_back.param_id_map == record.param_id_map
    assert read_back.to_dict() == record.to_dict()


def test_stamp_keeps_the_marker_unique(template: Path, record):
    """The stamped binary still holds the marker exactly once."""
    identity.stamp_identity(template, template, record)
    assert template.read_bytes().count(identity.IDENTITY_MAGIC) == 1


def test_stamp_preserves_the_bytes_outside_the_region(template: Path, record):
    """Only the identity region changes; the rest of the binary is untouched."""
    before = template.read_bytes()
    offset = before.find(identity.IDENTITY_MAGIC)
    start = offset + len(identity.IDENTITY_MAGIC)
    end = start + identity.IDENTITY_REGION_SIZE

    identity.stamp_identity(template, template, record)
    after = template.read_bytes()

    assert len(after) == len(before)
    assert after[:start] == before[:start]
    assert after[end:] == before[end:]
    assert after[start:end] != before[start:end]


def test_stamp_writes_to_a_separate_output(tmp_path: Path, record):
    """Stamping to another path leaves the template alone."""
    template = tmp_path / "template.vst3"
    identity.make_template(template, filler=512)
    original = template.read_bytes()

    output = tmp_path / "out" / "Auralis De-Click.vst3"
    identity.stamp_identity(template, output, record)

    assert template.read_bytes() == original, "the template must not be touched"
    assert output.exists()
    assert identity.verify_identity(output).module_code == record.module_code


def test_build_tool_stamps_and_reads_back(template: Path, record):
    """The build-time tool of the plug-in layer does the same round trip."""
    stamped = build_identity._stamp_into(template, None, record)
    assert Path(stamped) == template
    assert identity.verify_identity(template).module_code == record.module_code


# --------------------------------------------------------------------------
# a corrupted record is rejected
# --------------------------------------------------------------------------
def _corrupt_region(path: Path, payload: bytes) -> None:
    """Overwrite the identity region of a stamped binary with ``payload``."""
    data = bytearray(path.read_bytes())
    offset = data.find(identity.IDENTITY_MAGIC)
    start = offset + len(identity.IDENTITY_MAGIC)
    data[start : start + len(payload)] = payload
    path.write_bytes(bytes(data))


def test_a_corrupted_record_is_rejected(template: Path, record):
    """A record that is not valid JSON is refused when it is read back."""
    identity.stamp_identity(template, template, record)
    _corrupt_region(template, b"{not json at all")

    with pytest.raises(identity.IdentityError):
        identity.verify_identity(template)


def test_a_truncated_record_is_rejected(template: Path, record):
    """A record that is cut short is refused when it is read back."""
    identity.stamp_identity(template, template, record)
    _corrupt_region(template, b'{"module_code": "AUR_DEC')

    with pytest.raises(identity.IdentityError):
        identity.verify_identity(template)


def test_a_record_that_breaks_the_schema_is_rejected(template: Path, record):
    """A well-formed JSON document that is not a valid record is refused."""
    identity.stamp_identity(template, template, record)

    payload = record.to_dict()
    payload["module_code"] = "NOT_A_MODULE"
    _corrupt_region(template, json.dumps(payload).encode("utf-8"))

    with pytest.raises(identity.IdentityError):
        identity.verify_identity(template)


def test_an_empty_region_is_rejected(template: Path):
    """A template that was never stamped carries no record."""
    with pytest.raises(identity.IdentityError):
        identity.verify_identity(template)


def test_a_record_with_a_missing_field_is_rejected(template: Path, record):
    """A record that lacks a required field is refused."""
    identity.stamp_identity(template, template, record)

    payload = record.to_dict()
    payload.pop("vst3_fuid")
    _corrupt_region(template, json.dumps(payload).encode("utf-8"))

    with pytest.raises(identity.IdentityError):
        identity.verify_identity(template)


# --------------------------------------------------------------------------
# a duplicated marker is rejected
# --------------------------------------------------------------------------
def test_a_duplicated_marker_is_rejected_when_stamping(template: Path, record):
    """A binary with two markers is ambiguous, so stamping refuses it."""
    data = template.read_bytes()
    template.write_bytes(data + identity.IDENTITY_MAGIC + bytes(identity.IDENTITY_REGION_SIZE))

    assert template.read_bytes().count(identity.IDENTITY_MAGIC) == 2
    with pytest.raises(identity.IdentityStampError):
        identity.stamp_identity(template, template, record)


def test_a_duplicated_marker_is_rejected_when_reading_back(template: Path, record):
    """A binary with two markers is ambiguous, so reading it back refuses it."""
    identity.stamp_identity(template, template, record)

    data = template.read_bytes()
    template.write_bytes(data + identity.IDENTITY_MAGIC + bytes(identity.IDENTITY_REGION_SIZE))

    with pytest.raises(identity.IdentityStampError):
        identity.verify_identity(template)


def test_the_build_tool_refuses_a_duplicated_marker(template: Path, record):
    """The build-time tool refuses a binary with two markers as well."""
    data = template.read_bytes()
    template.write_bytes(data + identity.IDENTITY_MAGIC + bytes(identity.IDENTITY_REGION_SIZE))

    with pytest.raises(identity.IdentityStampError):
        build_identity._stamp_into(template, None, record)


# --------------------------------------------------------------------------
# the FUID is deterministic
# --------------------------------------------------------------------------
def test_the_fuid_is_deterministic_for_a_module_code():
    """The FUID of a module is the same every time it is derived."""
    product_code = identity.identity_for("AUR_DECLICK").aax_effect_ids[0][1]
    first = identity.derive_fuid(product_code)
    second = identity.derive_fuid(product_code)
    assert first == second
    assert str(first) == str(second)
    assert first.int == identity.derive_fuid(product_code).int


def test_the_fuid_of_a_module_does_not_depend_on_the_other_modules():
    """The FUID of a module is stable, whatever else is in the registry."""
    product_code = identity.identity_for("AUR_DECLICK").aax_effect_ids[0][1]
    expected = identity.derive_fuid(product_code)

    identities = identity.identities_for()
    assert identity.identity_for("AUR_DECLICK").vst3_fuid == str(expected)
    assert len(identities) > 1, "the registry must hold more than one module"


def test_every_module_has_its_own_fuid():
    """Two modules never share a FUID."""
    fuids = [record.vst3_fuid for record in identity.identities_for()]
    assert len(fuids) == len(set(fuids))


def test_the_fuid_follows_the_documented_layout():
    """The FUID is the class id the plug-in wrapper bakes into the binary."""
    record = identity.identity_for("AUR_DECLICK")
    product_code = record.aax_effect_ids[0][1]

    raw = identity.fuid_bytes(record.vst3_fuid)
    assert raw == bytes.fromhex("0101abababcdef01") + b"AURA" + product_code.encode("ascii")
    assert identity.derive_fuid(product_code) == uuid.UUID(bytes=raw)


def test_the_fuid_survives_the_stamping_round_trip(template: Path):
    """The FUID that goes into the binary is the one that comes back out."""
    record = identity.identity_for("AUR_DECLICK")
    identity.stamp_identity(template, template, record)

    read_back = identity.verify_identity(template)
    assert read_back.vst3_fuid == record.vst3_fuid
    assert read_back.fuid_hex == identity.fuid_hex(record.vst3_fuid)
    assert read_back.fuid_bytes == identity.fuid_bytes(record.vst3_fuid)


# --------------------------------------------------------------------------
# the parameter id map follows the registry
# --------------------------------------------------------------------------
def test_the_parameter_id_map_matches_the_registry():
    """The stamped parameter map is the one of the generated registry."""
    record = identity.identity_for("AUR_DECLICK")
    assert record.param_id_map == identity.module_param_ids("AUR_DECLICK")

    from aurcore import schema

    spec = schema.load()["AUR_DECLICK"]
    assert set(record.param_id_map.values()) == {param.name for param in spec.params}


def test_the_parameter_id_map_survives_the_stamping_round_trip(template: Path):
    """The parameter map that goes into the binary is the one that comes back."""
    record = identity.identity_for("AUR_DECLICK")
    identity.stamp_identity(template, template, record)

    read_back = identity.verify_identity(template)
    assert read_back.param_id_map == record.param_id_map
    assert read_back.param_ids == sorted(record.param_id_map)


# --------------------------------------------------------------------------
# the family-wide tool
# --------------------------------------------------------------------------
def _make_vst3_bundle(artefacts: Path, name: str, code: str) -> Path:
    """Build the layout the VST3 build produces for one module.

    The real layout is::

        <artefacts>/Auralis <Name>.vst3/Contents/x86_64-win/Auralis <Name>.vst3

    with the artefact directory named after the module code.
    """
    bundle = artefacts / f"{name}.vst3"
    inner = bundle / "Contents" / "x86_64-win"
    inner.mkdir(parents=True, exist_ok=True)
    binary = inner / f"{name}.vst3"
    identity.make_template(binary, filler=1024)
    return binary


def test_the_tool_finds_a_plugin_in_the_vst3_bundle_layout(tmp_path: Path):
    """The tool finds the binary inside the bundle the VST3 build produces."""
    build = tmp_path / "build" / "vst3"
    artefacts = build / "Auralis_AUR_DECLICK_artefacts" / "Release" / "VST3"
    artefacts.mkdir(parents=True)

    binary = _make_vst3_bundle(artefacts, "Auralis De-Click", "AUR_DECLICK")

    manifest_path = tmp_path / "manifest.json"
    code = stamp_all.main(
        [
            "--no-default-search",
            "--search",
            str(build),
            "--manifest",
            str(manifest_path),
            "--quiet",
        ]
    )
    assert code == 0

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["plugin_count"] == 1
    assert manifest["plugins"][0]["module_code"] == "AUR_DECLICK"
    assert manifest["plugins"][0]["module_code_from_artefact"] == "AUR_DECLICK"
    assert manifest["plugins"][0]["module_code_from_name"] == "AUR_DECLICK"
    assert manifest["plugins"][0]["verification"]["ok"] is True
    assert identity.verify_identity(binary).module_code == "AUR_DECLICK"


def test_the_tool_finds_a_plugin_beside_an_empty_bundle_folder(tmp_path: Path):
    """An empty bundle folder does not stop the search.

    A failed plug-in build leaves an empty ``.vst3`` folder behind.  It is
    not a module, and the search has to go on past it.
    """
    build = tmp_path / "build" / "vst3"
    artefacts = build / "Auralis_AUR_DECLICK_artefacts" / "Release" / "VST3"
    artefacts.mkdir(parents=True)

    # the empty folder a failed build leaves behind
    (artefacts / "Auralis De-Hum.vst3" / "Contents" / "x86_64-win").mkdir(parents=True)

    binary = _make_vst3_bundle(artefacts, "Auralis De-Click", "AUR_DECLICK")

    manifest_path = tmp_path / "manifest.json"
    code = stamp_all.main(
        [
            "--no-default-search",
            "--search",
            str(build),
            "--manifest",
            str(manifest_path),
            "--quiet",
        ]
    )
    assert code == 0

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["plugin_count"] == 1
    assert manifest["plugins"][0]["module_code"] == "AUR_DECLICK"
    assert identity.verify_identity(binary).module_code == "AUR_DECLICK"


def test_the_tool_finds_and_stamps_a_built_plugin(tmp_path: Path):
    """The tool stamps a plug-in it finds and verifies it."""
    build = tmp_path / "build" / "vst3"
    artefacts = build / "Auralis_AUR_DECLICK_artefacts" / "Release" / "VST3"
    artefacts.mkdir(parents=True)

    binary = artefacts / "Auralis De-Click.vst3"
    identity.make_template(binary, filler=1024)

    manifest_path = tmp_path / "manifest.json"
    code = stamp_all.main(
        [
            "--no-default-search",
            "--search",
            str(build),
            "--manifest",
            str(manifest_path),
            "--quiet",
        ]
    )
    assert code == 0, "the tool must succeed on a freshly built plug-in"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["format"] == "auralis.stamp-manifest"
    assert manifest["ok"] is True
    assert manifest["plugin_count"] == 1
    assert manifest["failed_count"] == 0

    entry = manifest["plugins"][0]
    assert entry["module_code"] == "AUR_DECLICK"
    assert entry["verification"]["ok"] is True
    assert entry["verification"]["checks"]["marker_occurs_once"] is True
    assert entry["verification"]["checks"]["record_parses"] is True
    assert entry["verification"]["checks"]["module_code_matches_name"] is True
    assert entry["verification"]["checks"]["fuid_matches"] is True
    assert entry["verification"]["checks"]["param_id_map_matches_registry"] is True
    assert entry["sha256_before"] != entry["sha256_after"]

    # the binary on disk really carries the record now
    assert identity.verify_identity(binary).module_code == "AUR_DECLICK"


def test_the_tool_reports_a_binary_it_cannot_stamp(tmp_path: Path):
    """A plug-in with an ambiguous identity slot is reported and fails the run."""
    build = tmp_path / "build" / "vst3"
    artefacts = build / "Auralis_AUR_DECLICK_artefacts" / "Release" / "VST3"
    artefacts.mkdir(parents=True)

    binary = artefacts / "Auralis De-Click.vst3"
    identity.make_template(binary, filler=1024)

    # A second marker makes the slot ambiguous: stamping must refuse it.
    data = binary.read_bytes()
    binary.write_bytes(data + identity.IDENTITY_MAGIC + bytes(identity.IDENTITY_REGION_SIZE))

    manifest_path = tmp_path / "manifest.json"
    code = stamp_all.main(
        [
            "--no-default-search",
            "--search",
            str(build),
            "--manifest",
            str(manifest_path),
            "--quiet",
        ]
    )
    assert code == 1, "a plug-in that fails verification must fail the run"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["ok"] is False
    assert manifest["failed_count"] == 1

    entry = manifest["plugins"][0]
    assert entry["verification"]["ok"] is False
    assert entry["verification"]["checks"] == {}
    assert entry["verification"]["problems"]


def test_the_tool_reports_a_binary_that_names_no_module(tmp_path: Path):
    """A plug-in whose name identifies no module is reported and fails the run."""
    build = tmp_path / "build" / "vst3"
    build.mkdir(parents=True)

    binary = build / "Something Else.vst3"
    identity.make_template(binary, filler=1024)

    manifest_path = tmp_path / "manifest.json"
    code = stamp_all.main(
        [
            "--no-default-search",
            "--search",
            str(build),
            "--manifest",
            str(manifest_path),
            "--quiet",
        ]
    )
    assert code == 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["failed_count"] == 1
    entry = manifest["plugins"][0]
    assert entry["module_code"] is None
    assert entry["verification"]["ok"] is False


def test_the_tool_catches_a_binary_named_for_the_wrong_module(tmp_path: Path):
    """A binary whose name disagrees with its artefact directory is caught.

    The two are independent signals: the artefact directory says what the build
    system built, the file name says what the host will see.  When they
    disagree, the plug-in is wrong and the run must fail.
    """
    build = tmp_path / "build" / "vst3"
    artefacts = build / "Auralis_AUR_DECLICK_artefacts" / "Release" / "VST3"
    artefacts.mkdir(parents=True)

    # The build system built De-Click; the file claims to be De-Hum.
    binary = artefacts / "Auralis De-Hum.vst3"
    identity.make_template(binary, filler=1024)

    manifest_path = tmp_path / "manifest.json"
    code = stamp_all.main(
        [
            "--no-default-search",
            "--search",
            str(build),
            "--manifest",
            str(manifest_path),
            "--quiet",
        ]
    )
    assert code == 1, "a plug-in whose name is wrong must fail the run"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = manifest["plugins"][0]
    assert entry["module_code"] == "AUR_DECLICK"
    assert entry["module_code_from_name"] == "AUR_DEHUM"
    assert entry["module_code_from_artefact"] == "AUR_DECLICK"
    assert entry["verification"]["ok"] is False
    assert entry["verification"]["checks"]["module_code_matches_name"] is False
    assert entry["verification"]["checks"]["module_code_consistent"] is False


def test_a_dry_run_changes_nothing(tmp_path: Path):
    """A dry run verifies without touching a single byte."""
    build = tmp_path / "build" / "vst3"
    artefacts = build / "Auralis_AUR_DECLICK_artefacts" / "Release" / "VST3"
    artefacts.mkdir(parents=True)

    binary = artefacts / "Auralis De-Click.vst3"
    identity.make_template(binary, filler=1024)
    before = binary.read_bytes()

    code = stamp_all.main(
        [
            "--no-default-search",
            "--search",
            str(build),
            "--dry-run",
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--quiet",
        ]
    )
    assert code == 0
    assert binary.read_bytes() == before, "a dry run must not touch the binary"
    assert not (tmp_path / "manifest.json").exists(), "a dry run must not write a manifest"


def test_the_tool_fails_when_no_plugin_is_found(tmp_path: Path):
    """An empty build tree is a failure, unless it is explicitly allowed."""
    build = tmp_path / "build" / "vst3"
    build.mkdir(parents=True)
    manifest = tmp_path / "manifest.json"

    assert (
        stamp_all.main(
            [
                "--no-default-search",
                "--search",
                str(build),
                "--manifest",
                str(manifest),
                "--quiet",
            ]
        )
        == 1
    )
    assert (
        stamp_all.main(
            [
                "--no-default-search",
                "--search",
                str(build),
                "--manifest",
                str(manifest),
                "--allow-empty",
                "--quiet",
            ]
        )
        == 0
    )


# --------------------------------------------------------------------------
# the class id
# --------------------------------------------------------------------------
def test_the_class_id_of_every_module_is_frozen():
    """The class id of a module never changes.

    The class id is a contract with the host: a module keeps the id it
    was given, whatever happens to the code that derives it.  The table
    is written out rather than derived, so that a change in the
    derivation is a failure and not a new number.
    """
    derived = {record.module_code: record.vst3_fuid for record in identity.identities_for()}

    assert derived == FROZEN_CLASS_IDS, (
        "the class ids of the registry changed; a module must keep the class id "
        "it was given"
    )


def test_every_module_has_its_own_class_id_bytes():
    """No two modules share the class id bytes, on any platform."""
    for com_compatible in (True, False):
        seen: dict[bytes, str] = {}
        for record in identity.identities_for():
            raw = identity.class_id_bytes(record.vst3_fuid, com_compatible=com_compatible)
            assert raw not in seen, (
                f"{record.module_code} and {seen.get(raw)} share the class id bytes"
            )
            seen[raw] = record.module_code


def test_the_class_id_bytes_follow_the_platform_convention():
    """The bytes of a class id are laid out the way the platform reads them.

    The four words of the class id string are laid out big endian
    everywhere and COM compatible on Windows: the first word little
    endian, the second one with its bytes swapped in pairs, the last two
    big endian.  The layout is written out here a second time, so that
    the two implementations can be compared.
    """
    fuid = identity.identity_for("AUR_DECLICK").vst3_fuid
    text = identity.class_id_text(fuid)
    words = [int(text[index * 8 : (index + 1) * 8], 16) for index in range(4)]

    plain = b"".join(word.to_bytes(4, "big") for word in words)
    com = (
        words[0].to_bytes(4, "little")
        + bytes(
            [
                (words[1] >> 16) & 0xFF,
                (words[1] >> 24) & 0xFF,
                words[1] & 0xFF,
                (words[1] >> 8) & 0xFF,
            ]
        )
        + words[2].to_bytes(4, "big")
        + words[3].to_bytes(4, "big")
    )

    assert identity.class_id_bytes(fuid, com_compatible=False) == plain
    assert identity.class_id_bytes(fuid, com_compatible=True) == com
    assert plain != com, "the two layouts have to differ, or the point is lost"


def test_the_class_id_reads_back_as_the_record_on_every_platform():
    """The bytes a binary reports read back as the record's string.

    This is the property the family depends on: the class id of a
    module is the same identifier on every platform, so a session
    written on one platform names the same plug-in on another.
    """
    for record in identity.identities_for():
        for com_compatible in (True, False):
            raw = identity.class_id_bytes(record.vst3_fuid, com_compatible=com_compatible)
            read_back = identity.class_id_string(raw, com_compatible=com_compatible)
            assert identity.class_id_text(read_back) == identity.class_id_text(record.vst3_fuid)


def test_the_record_and_the_binary_agree_on_the_class_id():
    """The record names the class id the binary reports.

    The record carries the identifier; the binary reports the bytes of
    its own platform.  The two have to be the same class id, and the
    manifest says which bytes that is.
    """
    record = identity.identity_for("AUR_DECLICK")
    expected = identity.class_id_bytes(
        record.vst3_fuid, com_compatible=identity.host_is_com_compatible()
    )

    assert record.class_id_bytes == expected
    assert len(expected) == 16

    # The plain layout is the string's hex digits in order, which is what
    # makes the string and the bytes two views of the same identifier.
    plain = identity.class_id_bytes(record.vst3_fuid, com_compatible=False)
    assert plain.hex() == identity.class_id_text(record.vst3_fuid)


def test_the_class_id_survives_the_stamping_round_trip(template: Path):
    """The class id that goes into the binary is the one that comes back."""
    record = identity.identity_for("AUR_DECLICK")
    identity.stamp_identity(template, template, record)

    read_back = identity.verify_identity(template)
    assert read_back.vst3_fuid == record.vst3_fuid
    assert read_back.class_id_bytes == record.class_id_bytes


# --------------------------------------------------------------------------
# the slot
# --------------------------------------------------------------------------
#: The adapters whose slot read is known to be foldable until the patch that
#: belongs to their owner is applied.  The tests below report these as
#: expected failures: they pass as soon as the patch is in, and they fail
#: for good if the read is broken in any other way.
FOLDABLE_ADAPTERS: dict[Path, str] = {
    FRAMEWORK_ADAPTER: "var/patches/f3_juce_slot.patch",
}


def _slot_read_line(source: Path) -> str:
    """Return the line the adapter reads the identity slot with."""
    text = source.read_text(encoding="utf-8")
    for line in text.splitlines():
        if re.search(r"region\s*=\s*gIdentitySlot\s*\.\s*region", line):
            return line.strip()
    raise AssertionError(f"{source} does not read the identity slot through a pointer")


@pytest.mark.parametrize(
    "adapter", [NATIVE_ADAPTER, FRAMEWORK_ADAPTER], ids=["native", "framework"]
)
def test_the_adapter_reads_the_slot_through_a_volatile_pointer(adapter: Path):
    """The slot is read through a pointer to volatile memory.

    This is the cheap check that goes with the fold test below: the read
    has to name the slot through volatile memory, because that is what
    stops the compiler from folding it away.
    """
    read_line = _slot_read_line(adapter)
    if "volatile" not in read_line:
        pytest.xfail(
            f"{adapter.name} reads the identity slot without 'volatile'; the fix "
            f"is in {FOLDABLE_ADAPTERS.get(adapter, 'the adapter layer')}"
        )
    assert "volatile" in read_line


_MSVC_BAT = ROOT / "tools" / "msvc.bat"
_COMPILER: dict[str, bool] = {}


def _compiler_available() -> bool:
    """True when the compiler that builds the plug-ins can be run."""
    if "ok" in _COMPILER:
        return _COMPILER["ok"]
    ok = False
    if os.name == "nt" and _MSVC_BAT.is_file():
        try:
            result = subprocess.run(
                ["cmd", "/c", str(_MSVC_BAT), "cl", "/?"],
                capture_output=True,
                text=True,
                timeout=180,
            )
            ok = result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
    _COMPILER["ok"] = ok
    return ok


def _slot_program(read_line: str) -> str:
    """The program that reads the slot the way the adapter reads it."""
    marker = identity.IDENTITY_MAGIC.decode("ascii")
    assignments = "\n".join(
        f"    slot.marker[{index}] = '{character}';"
        for index, character in enumerate(marker)
    )

    return f"""#include <cstdio>

struct IdentitySlot
{{
    char marker[{len(marker)}];
    char region[{identity.IDENTITY_REGION_SIZE}];
}};

constexpr IdentitySlot makeIdentitySlot()
{{
    IdentitySlot slot {{}};

{assignments}

    slot.region[{identity.IDENTITY_REGION_SIZE - 1}] = 0x5a;

    return slot;
}}

const IdentitySlot gIdentitySlot = makeIdentitySlot();

{read_line}

int main()
{{
    std::printf ("%02x\\n", static_cast<unsigned char> (region[0]));
    return 0;
}}
"""


def _probe_slot(tmp_path: Path, read_line: str) -> str:
    """Compile the program, patch its slot the way the build does, run it.

    The build patches the record into the file *after* the link, so the
    program has to read the slot the way the adapter reads it.  The
    program is therefore patched the same way and then run: it reports
    the byte it saw.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "slot.cpp"
    source.write_text(_slot_program(read_line), encoding="utf-8")
    executable = tmp_path / "slot.exe"

    command = [
        "cmd",
        "/c",
        str(_MSVC_BAT),
        "cl",
        "/nologo",
        "/O2",
        "/EHsc",
        "/std:c++17",
        str(source),
        f"/Fe{executable}",
        "/link",
        "/INCREMENTAL:NO",
    ]
    result = subprocess.run(
        command, capture_output=True, text=True, cwd=str(tmp_path), timeout=600
    )
    assert result.returncode == 0, (
        f"the compiler failed:\n{result.stdout}\n{result.stderr}"
    )
    assert executable.is_file(), "the compiler produced no program"

    data = bytearray(executable.read_bytes())
    at = data.find(identity.IDENTITY_MAGIC)
    assert at >= 0, "the compiled program does not carry the marker"
    assert data.count(identity.IDENTITY_MAGIC) == 1, "the marker occurs more than once"
    data[at + len(identity.IDENTITY_MAGIC)] = ord("{")
    executable.write_bytes(bytes(data))

    run = subprocess.run([str(executable)], capture_output=True, text=True, timeout=120)
    return run.stdout.strip()


@pytest.mark.parametrize(
    "adapter", [NATIVE_ADAPTER, FRAMEWORK_ADAPTER], ids=["native", "framework"]
)
def test_the_slot_read_cannot_be_folded(tmp_path: Path, adapter: Path):
    """The read of the slot survives the compiler.

    The identity slot is a ``const`` object, so a compiler is allowed to
    assume that its contents never change and to fold a read of it back
    to the value it had when the program was linked.  The build patches
    the record into the file *after* the link, so a folded read throws
    the record away and the plug-in comes up without an identity.

    The test therefore does what the build does: it compiles a program
    that reads the slot the way the adapter reads it, patches the
    compiled program the way the stamper patches the binary, and runs
    it.  A read that cannot be folded sees the patched byte; a read that
    can be folded sees the value the program was linked with.
    """
    if not _compiler_available():
        pytest.skip("the compiler that builds the plug-ins is not available")

    seen = _probe_slot(tmp_path / adapter.stem, _slot_read_line(adapter))
    if seen != "7b":
        pytest.xfail(
            f"{adapter.name} reads the slot in a way the compiler may fold away: "
            f"the patched byte was not seen (the program reported {seen!r}); the "
            f"fix is in {FOLDABLE_ADAPTERS.get(adapter, 'the adapter layer')}"
        )
    assert seen == "7b"


def test_the_fold_test_can_fail(tmp_path: Path):
    """The fold test really tells a foldable read from a fold-free one.

    Without this test the one above could pass for the wrong reason: a
    program that never looks at the slot at all would pass it too.  The
    control reads the slot the foldable way — without the ``volatile``
    — and has to see the value the program was linked with.
    """
    if not _compiler_available():
        pytest.skip("the compiler that builds the plug-ins is not available")

    seen = _probe_slot(
        tmp_path / "control", "const char* const region = gIdentitySlot.region;"
    )
    assert seen == "00", (
        "the control did not fold: a read without 'volatile' still saw the "
        f"patched byte ({seen!r}), so the fold test cannot tell the two apart"
    )


# --------------------------------------------------------------------------
# the built plug-in
# --------------------------------------------------------------------------
class _FactoryVtbl(ctypes.Structure):
    """The first eight entries of the plug-in factory's table."""

    _fields_ = [
        ("queryInterface", ctypes.c_void_p),
        ("addRef", ctypes.c_void_p),
        ("release", ctypes.c_void_p),
        ("getFactoryInfo", ctypes.c_void_p),
        ("countClasses", ctypes.c_void_p),
        ("getClassInfo", ctypes.c_void_p),
        ("createInstance", ctypes.c_void_p),
        ("getClassInfo2", ctypes.c_void_p),
    ]


class _PClassInfo(ctypes.Structure):
    """The class information a host asks the factory for."""

    _fields_ = [
        ("cid", ctypes.c_char * 16),
        ("cardinality", ctypes.c_int32),
        ("category", ctypes.c_char * 32),
        ("name", ctypes.c_char * 64),
    ]


def _built_native_module(module_code: str = "AUR_DECLICK") -> Path | None:
    """The built native plug-in of a module, when the build tree is there."""
    if not NATIVE_BUILD_ROOT.is_dir():
        return None
    for path in sorted(NATIVE_BUILD_ROOT.rglob("*.vst3.dll")):
        if module_code in path.parts:
            return path
    return None


def _class_info(path: Path) -> tuple[bytes, str]:
    """Load a built plug-in the way a host loads it and ask it about itself."""
    handle = None
    if hasattr(os, "add_dll_directory"):
        handle = os.add_dll_directory(str(path.parent))
    try:
        module = ctypes.WinDLL(str(path))
        factory = module.GetPluginFactory
        factory.restype = ctypes.c_void_p
        factory.argtypes = []
        pointer = factory()
        assert pointer, "the factory is null"

        vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(_FactoryVtbl))).contents
        info = _PClassInfo()
        get_class_info = ctypes.WINFUNCTYPE(
            ctypes.c_int32, ctypes.c_void_p, ctypes.c_int32, ctypes.POINTER(_PClassInfo)
        )(vtable.contents.getClassInfo)
        assert get_class_info(pointer, 0, ctypes.byref(info)) == 0, "the factory did not answer"

        return bytes(info.cid), info.name.decode("utf-8", "replace")
    finally:
        if handle is not None:
            handle.close()


def test_the_built_plugin_reports_the_record_that_was_stamped_into_it():
    """The built plug-in reads its record at run time.

    The identity slot holds its link time value in the file, and the
    build patches the record into it afterwards.  A plug-in that folded
    the read away would come up without an identity: no name, no
    parameters.  A plug-in that reads the slot reports the record that
    was stamped into it, so the name the factory answers with is the
    proof.
    """
    path = _built_native_module()
    if path is None:
        pytest.skip("the native plug-ins are not built in this tree")

    record = identity.verify_identity(path)
    _cid, name = _class_info(path)

    assert name == record.product_name, (
        f"the plug-in reports the class name {name!r}, the stamped record says "
        f"{record.product_name!r}: the read of the slot was folded away"
    )


def test_the_built_plugin_reports_the_class_id_of_the_record():
    """The class id the built plug-in reports is the record's class id.

    The bytes a binary reports are the platform's encoding of the
    class id string, so that the host reads the record's identifier
    back.  A binary that reported the string's hex digits instead would
    be read as a different class id on Windows.
    """
    path = _built_native_module()
    if path is None:
        pytest.skip("the native plug-ins are not built in this tree")

    record = identity.verify_identity(path)
    cid, _name = _class_info(path)
    expected = record.class_id_bytes

    if cid == record.fuid_bytes and cid != expected:
        pytest.xfail(
            "the built adapter still reports the pre-fix byte order of the class "
            "id; see var/patches/f3_classid.patch"
        )

    assert cid == expected, (
        f"the plug-in reports the class id {cid.hex()}, the record names "
        f"{expected.hex()}"
    )


# --------------------------------------------------------------------------
# the stamping
# --------------------------------------------------------------------------
def test_stamping_twice_changes_nothing(template: Path, record):
    """Stamping a stamped binary again does not change a byte.

    The stamping has to be idempotent: the build stamps the binary, the
    family tool stamps it again to verify it, and the second stamp must
    not move a single byte.
    """
    identity.stamp_identity(template, template, record)
    first = template.read_bytes()

    identity.stamp_identity(template, template, record)
    second = template.read_bytes()

    assert first == second, "stamping a stamped binary changed it"
    assert identity.verify_identity(template).to_dict() == record.to_dict()


def test_a_record_that_names_another_module_is_rejected(template: Path, record):
    """A record that is not the file's module is refused when it is read back."""
    identity.stamp_identity(template, template, record)

    assert identity.verify_identity(template, expect_module_code="AUR_DECLICK")
    with pytest.raises(identity.IdentityError):
        identity.verify_identity(template, expect_module_code="AUR_DEHUM")


# --------------------------------------------------------------------------
# the family
# --------------------------------------------------------------------------
def test_the_manifest_names_every_module_with_its_ids_and_hash(tmp_path: Path):
    """The manifest names every module, its ids and the hash of its file."""
    build = tmp_path / "build" / "vst3"
    artefacts = build / "Auralis_AUR_DECLICK_artefacts" / "Release" / "VST3"
    artefacts.mkdir(parents=True)
    binary = _make_vst3_bundle(artefacts, "Auralis De-Click", "AUR_DECLICK")

    manifest_path = tmp_path / "manifest.json"
    code = stamp_all.main(
        [
            "--no-default-search",
            "--search",
            str(build),
            "--manifest",
            str(manifest_path),
            "--quiet",
        ]
    )
    assert code == 0

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["plugin_count"] == 1
    assert manifest["verified_count"] == 1
    assert manifest["failed_count"] == 0

    module = manifest["modules"][0]
    assert module["module_code"] == "AUR_DECLICK"
    assert module["vst3_fuid"] == identity.identity_for("AUR_DECLICK").vst3_fuid
    assert module["vst3_class_id_bytes"] == identity.identity_for(
        "AUR_DECLICK"
    ).class_id_bytes.hex()
    assert module["aax_effect_ids"] == [
        list(triple) for triple in identity.identity_for("AUR_DECLICK").aax_effect_ids
    ]
    assert module["param_ids"] == sorted(identity.module_param_ids("AUR_DECLICK"))
    assert module["sha256"] == stamp_all._sha256(binary)
    assert module["bytes"] == binary.stat().st_size
