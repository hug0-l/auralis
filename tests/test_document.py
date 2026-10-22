"""
Tests for the session document.

A document is what the workbench writes when the user saves: the audio the
session works on, the module chain, the state of the view, the markers of the
timeline and a summary of the undo history.  These tests cover the round trip
(save, load, compare) and the fingerprint of the audio file, which tells the
workbench that the file on disk changed since the session was saved.

The tests run against a small dummy module so they do not depend on the
module tree, which is written in parallel.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

from aurcore.api import create_engine
from aurcore.document import (
    DEFAULT_VIEW,
    DOCUMENT_FORMAT,
    DOCUMENT_VERSION,
    HASH_SAMPLE_BYTES,
    audio_status,
    build_document,
    content_hash,
    document_audio,
    document_history,
    document_markers,
    document_param_report,
    document_view,
    load_document,
    normalise_markers,
    normalise_view,
    save_document,
)
from aurcore.modules.base import Module
from aurcore.params import ParamDesc
from aurcore.undo import AddModuleCommand, SetParamCommand, UndoStack

DUMMY_CODE = "AUR_TEST_DOCUMENT"
INSERT_CODE = "AUR_TEST_INSERT"


class _DocumentDummy(Module):
    """A minimal module with a float and a bool parameter."""

    code = DUMMY_CODE
    display_name = "Document Dummy"
    category = "Utility"
    edition = "standard"
    element = "DocumentDummy"

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        """Create the dummy module."""
        super().__init__(sample_rate, channels)

    def describe(self) -> list[ParamDesc]:
        """The parameters of the dummy module."""
        return [
            ParamDesc(
                name="Sensitivity",
                type="float",
                default=3.0,
                minimum=0.0,
                maximum=10.0,
                unit="dB",
                group="Strength",
            ),
            ParamDesc(name="Enabled", type="bool", default=True, group="State"),
        ]

    def process(self, x: np.ndarray) -> np.ndarray:
        """The dummy module passes the signal through."""
        return x


@pytest.fixture
def document_dummy(registry_snapshot):
    """Register the dummy module for the duration of a test."""
    from aurcore import registry

    registry.register(_DocumentDummy)
    return _DocumentDummy


class _InsertDummy(Module):
    """A module whose parameter list a test may change.

    The list is a class attribute, so an insertion in the middle of it is
    what an insertion in the middle of the canonical table does to a module:
    the parameters after it get another id.
    """

    code = INSERT_CODE
    display_name = "Insert Dummy"
    category = "Utility"
    edition = "standard"
    element = "InsertDummy"

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
_ORIGINAL_PARAMS = list(_InsertDummy.PARAMS)


@pytest.fixture
def insert_dummy(registry_snapshot):
    """Register the module whose parameter list a test may change."""
    from aurcore import registry

    registry.register(_InsertDummy)
    _InsertDummy.PARAMS = list(_ORIGINAL_PARAMS)
    try:
        yield _InsertDummy
    finally:
        _InsertDummy.PARAMS = list(_ORIGINAL_PARAMS)


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


@pytest.fixture
def engine():
    """A 48 kHz / 2-channel engine with an empty chain."""
    return create_engine(48000, 2)


@pytest.fixture
def audio_file(tmp_path: Path) -> Path:
    """A small file that stands in for the audio of the session."""
    target = tmp_path / "session.wav"
    target.write_bytes(b"RIFF" + bytes(range(256)) * 4)
    return target


def _audio_ref(path: Path) -> dict:
    """The audio reference of the session."""
    return {
        "path": str(path),
        "sample_rate": 48000,
        "channels": 2,
        "frames": 1024,
        "name": "session.wav",
    }


def _view() -> dict:
    """The view state of the session."""
    return {
        "zoom": 2.5,
        "scroll": 1.25,
        "frequency_range": [80.0, 8000.0],
        "selection": {"t0": 1.0, "t1": 2.5, "f0": 80.0, "f1": 400.0},
        "view_mode": "spectrogram",
        "spectrogram": True,
    }


def _markers() -> list[dict]:
    """The markers of the session."""
    return [
        {"id": "m1", "name": "Hum", "start": 1.0, "end": 2.5, "kind": "region"},
        {"id": "m2", "name": "Click", "start": 4.0, "kind": "marker"},
    ]


# ---------------------------------------------------------------------------
# the shape of the document
# ---------------------------------------------------------------------------
def test_the_document_has_the_expected_format(tmp_path: Path, audio_file: Path):
    """The document carries the format, the version and every block."""
    document = save_document(tmp_path / "session.json", None, _audio_ref(audio_file))

    assert document["format"] == DOCUMENT_FORMAT
    assert document["version"] == DOCUMENT_VERSION
    assert set(document) >= {"audio", "chain", "view", "markers", "history"}
    assert document["chain"]["format"] == "auralis.chain"


def test_the_document_is_valid_json(tmp_path: Path, audio_file: Path):
    """The written file is readable JSON."""
    target = tmp_path / "session.json"
    save_document(target, None, _audio_ref(audio_file))

    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["format"] == DOCUMENT_FORMAT


def test_the_document_stores_the_audio_reference(tmp_path: Path, audio_file: Path):
    """The audio block carries the path, the format and the fingerprint."""
    document = save_document(tmp_path / "session.json", None, _audio_ref(audio_file))
    audio = document_audio(document)

    assert audio["path"] == str(audio_file)
    assert audio["sample_rate"] == 48000
    assert audio["channels"] == 2
    assert audio["frames"] == 1024
    assert audio["content_hash"] == content_hash(audio_file)
    assert audio["size"] == audio_file.stat().st_size


def test_an_audio_reference_may_be_an_object(tmp_path: Path, audio_file: Path):
    """The audio reference may be an object with the attributes."""
    from types import SimpleNamespace

    reference = SimpleNamespace(
        path=str(audio_file),
        sample_rate=48000,
        channels=2,
        frames=1024,
        name="session.wav",
    )
    document = save_document(tmp_path / "session.json", None, reference)
    audio = document_audio(document)

    assert audio["path"] == str(audio_file)
    assert audio["sample_rate"] == 48000
    assert audio["content_hash"] == content_hash(audio_file)


def test_the_document_stores_the_history_summary(tmp_path: Path, audio_file: Path, engine,
                                                 document_dummy):
    """The undo history is summarised in the document."""
    stack = UndoStack()
    stack.execute(AddModuleCommand(engine, DUMMY_CODE))
    stack.execute(SetParamCommand(engine, 0, "Sensitivity", 6.0))

    document = save_document(
        tmp_path / "session.json", engine, _audio_ref(audio_file), None, None, history=stack
    )
    history = document_history(document)

    assert history["depth"] == 2
    assert history["labels"] == ["Add Document Dummy", "Change Sensitivity"]
    assert history["can_undo"] is True


# ---------------------------------------------------------------------------
# the round trip
# ---------------------------------------------------------------------------
def test_the_document_round_trip_restores_the_chain(tmp_path: Path, audio_file: Path, engine,
                                                    document_dummy):
    """Save, load: the chain, the parameters and the power states are the same."""
    engine.add_module(DUMMY_CODE)
    engine.add_module(DUMMY_CODE)
    engine.set_param(0, "Sensitivity", 7.5)
    engine.set_bypass(1, True)

    target = tmp_path / "session.json"
    save_document(target, engine, _audio_ref(audio_file), _view(), _markers())

    restored = create_engine(48000, 2)
    document = load_document(target, restored)

    assert [step.module.code for step in restored.chain] == [DUMMY_CODE, DUMMY_CODE]
    assert restored.get_param(0, "Sensitivity") == pytest.approx(7.5)
    assert restored.is_bypassed(0) is False
    assert restored.is_bypassed(1) is True
    assert document["format"] == DOCUMENT_FORMAT


def test_the_document_round_trip_keeps_the_view_state(tmp_path: Path, audio_file: Path, engine,
                                                      document_dummy):
    """The view state survives the round trip."""
    target = tmp_path / "session.json"
    save_document(target, engine, _audio_ref(audio_file), _view(), _markers())

    document = load_document(target)
    view = document_view(document)

    assert view["zoom"] == pytest.approx(2.5)
    assert view["scroll"] == pytest.approx(1.25)
    assert view["frequency_range"] == [80.0, 8000.0]
    assert view["selection"] == {"t0": 1.0, "t1": 2.5, "f0": 80.0, "f1": 400.0}
    assert view["view_mode"] == "spectrogram"


def test_the_document_round_trip_keeps_the_markers(tmp_path: Path, audio_file: Path, engine,
                                                   document_dummy):
    """The markers survive the round trip."""
    target = tmp_path / "session.json"
    save_document(target, engine, _audio_ref(audio_file), _view(), _markers())

    document = load_document(target)
    markers = document_markers(document)

    assert len(markers) == 2
    assert markers[0]["id"] == "m1"
    assert markers[0]["name"] == "Hum"
    assert markers[0]["start"] == pytest.approx(1.0)
    assert markers[0]["end"] == pytest.approx(2.5)
    assert markers[0]["kind"] == "region"
    assert markers[1]["kind"] == "marker"
    assert markers[1]["end"] is None


def test_the_document_round_trip_keeps_the_audio(tmp_path: Path, audio_file: Path, engine,
                                                 document_dummy):
    """The audio reference survives the round trip."""
    target = tmp_path / "session.json"
    save_document(target, engine, _audio_ref(audio_file))

    document = load_document(target)
    audio = document_audio(document)

    assert audio["path"] == str(audio_file)
    assert audio["content_hash"] == content_hash(audio_file)
    assert audio["name"] == "session.wav"


def test_loading_a_document_without_an_engine_returns_the_document(tmp_path: Path,
                                                                   audio_file: Path):
    """A document can be read without an engine."""
    target = tmp_path / "session.json"
    save_document(target, None, _audio_ref(audio_file), _view(), _markers())

    document = load_document(target)
    assert document["format"] == DOCUMENT_FORMAT
    assert document_view(document)["zoom"] == pytest.approx(2.5)


def test_loading_a_document_replaces_the_chain(tmp_path: Path, audio_file: Path, engine,
                                               document_dummy):
    """The chain of the engine is replaced by the chain of the document."""
    engine.add_module(DUMMY_CODE)

    target = tmp_path / "session.json"
    save_document(target, None, _audio_ref(audio_file))

    load_document(target, engine)
    assert engine.chain == []


# ---------------------------------------------------------------------------
# the audio fingerprint
# ---------------------------------------------------------------------------
def test_an_unchanged_audio_file_is_not_reported(tmp_path: Path, audio_file: Path):
    """The file the session was built on is recognised."""
    document = build_document(audio_ref=_audio_ref(audio_file))
    status = audio_status(document)

    assert status["state"] == "unchanged"
    assert status["expected_hash"] == status["actual_hash"]


def test_a_changed_audio_file_is_detected(tmp_path: Path, audio_file: Path):
    """A file that was replaced is reported as changed."""
    document = build_document(audio_ref=_audio_ref(audio_file))

    audio_file.write_bytes(b"RIFF" + bytes(range(255, -1, -1)) * 4)
    status = audio_status(document)

    assert status["state"] == "changed"
    assert status["actual_hash"] != status["expected_hash"]
    assert status["reason"]


def test_a_missing_audio_file_is_reported(tmp_path: Path, audio_file: Path):
    """A file that is gone is reported as missing."""
    document = build_document(audio_ref=_audio_ref(audio_file))
    audio_file.unlink()

    status = audio_status(document)
    assert status["state"] == "missing"


def test_a_document_without_a_fingerprint_is_unknown(tmp_path: Path, audio_file: Path):
    """A document without a content hash cannot judge the file."""
    document = build_document(audio_ref=_audio_ref(audio_file))
    document["audio"]["content_hash"] = ""

    status = audio_status(document)
    assert status["state"] == "unknown"


def test_the_content_hash_of_a_missing_file_is_empty(tmp_path: Path):
    """A file that cannot be read has no fingerprint."""
    assert content_hash(tmp_path / "nothing-here.wav") == ""


def test_the_content_hash_only_reads_the_head_and_the_tail(tmp_path: Path):
    """The fingerprint is built from the head, the tail, the size and the time.

    A change in the middle of a long file is therefore not noticed when the
    size and the modification time stay the same.
    """
    import hashlib

    target = tmp_path / "long.wav"
    payload = bytes(range(256)) * (3 * 1024 * 1024 // 256)
    target.write_bytes(payload)

    digest = hashlib.sha256()
    digest.update(b"auralis.audio.v1")
    stat = target.stat()
    digest.update(str(stat.st_size).encode("ascii"))
    digest.update(str(stat.st_mtime_ns).encode("ascii"))
    digest.update(payload[:HASH_SAMPLE_BYTES])
    digest.update(payload[-HASH_SAMPLE_BYTES:])
    assert content_hash(target) == "sha256:" + digest.hexdigest()

    # A change in the middle of the file is not part of the sample.
    changed = bytearray(payload)
    changed[len(changed) // 2] = 0xFF
    target.write_bytes(bytes(changed))
    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert content_hash(target) == "sha256:" + digest.hexdigest()


def test_a_touched_file_counts_as_changed(tmp_path: Path, audio_file: Path):
    """The modification time is part of the fingerprint."""
    document = build_document(audio_ref=_audio_ref(audio_file))

    stat = audio_file.stat()
    os.utime(audio_file, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000_000))
    assert audio_status(document)["state"] == "changed"


# ---------------------------------------------------------------------------
# normalisation and migration
# ---------------------------------------------------------------------------
def test_the_view_aliases_are_normalised():
    """The spellings of the user interface are renamed."""
    view = normalise_view({"frequencyRange": [100.0, 5000.0], "viewMode": "waveform"})

    assert view["frequency_range"] == [100.0, 5000.0]
    assert view["view_mode"] == "waveform"
    assert view["zoom"] == pytest.approx(DEFAULT_VIEW["zoom"])


def test_the_view_keeps_unknown_keys():
    """A key a newer build added is kept."""
    view = normalise_view({"tool": "spectral"})
    assert view["tool"] == "spectral"


def test_the_markers_are_normalised():
    """Markers get an id, a name and a kind."""
    markers = normalise_markers([{"start": 2.0, "end": 1.0}])

    assert markers[0]["id"] == "m1"
    assert markers[0]["kind"] == "region"
    assert markers[0]["start"] == pytest.approx(1.0)
    assert markers[0]["end"] == pytest.approx(2.0)


def test_a_legacy_document_is_migrated(tmp_path: Path, engine, document_dummy):
    """A version 0 document is upgraded and its chain is restored."""
    legacy = {
        "format": "auralis.document",
        "version": 0,
        "audio": {"path": "work/old.wav", "sr": 44100, "ch": 1, "length": 2048},
        "chain": [
            {
                "code": DUMMY_CODE,
                "on": True,
                "settings": {"Sensitivity": 4.5},
            }
        ],
        "view": {"spectro": False},
    }
    target = tmp_path / "legacy.json"
    target.write_text(json.dumps(legacy), encoding="utf-8")

    restored = create_engine(48000, 2)
    document = load_document(target, restored)

    assert document["version"] == 1
    assert document_audio(document)["sample_rate"] == 44100
    assert document_audio(document)["channels"] == 1
    assert document_audio(document)["frames"] == 2048
    assert document_view(document)["spectrogram"] is False
    assert len(restored.chain) == 1
    assert restored.get_param(0, "Sensitivity") == pytest.approx(4.5)


def test_the_migrations_are_reported(tmp_path: Path, audio_file: Path):
    """The migrations that ran are reported to the caller."""
    from aurcore.document import document_migrations

    target = tmp_path / "session.json"
    save_document(target, None, _audio_ref(audio_file))
    document = load_document(target)
    assert document_migrations(document) == []

    legacy = tmp_path / "legacy.json"
    legacy.write_text(
        json.dumps({"format": "auralis.document", "version": 0, "audio": {}}),
        encoding="utf-8",
    )
    assert document_migrations(load_document(legacy)) == ["document_v0_to_v1"]


def test_a_file_that_is_not_a_document_is_rejected(tmp_path: Path):
    """A file with another format is not a session document."""
    target = tmp_path / "other.json"
    target.write_text(json.dumps({"format": "auralis.preset", "version": 1}), encoding="utf-8")

    with pytest.raises(ValueError):
        load_document(target)


# ---------------------------------------------------------------------------
# the identity of a parameter
# ---------------------------------------------------------------------------
def test_the_document_carries_the_id_of_every_parameter(
    tmp_path: Path, audio_file: Path, engine, insert_dummy
):
    """The chain of the document says which id every parameter had."""
    engine.add_module(insert_dummy.code)
    engine.set_param(0, "Threshold", 0.25)

    document = save_document(tmp_path / "session.json", engine, _audio_ref(audio_file))
    params = document["chain"]["steps"][0]["params"]

    assert set(params) == {"Threshold", "Ratio", "Width", "Enabled"}
    assert params["Threshold"]["Id"] == 0
    assert params["Ratio"]["Id"] == 1
    assert params["Width"]["Id"] == 2
    assert params["Enabled"]["Id"] == 3
    assert params["Threshold"]["Sig"] == "float:0:1"
    assert params["Width"]["Sig"] == "float:0:10:ms"
    assert params["Enabled"]["Sig"] == "bool"


def test_a_session_written_before_an_insertion_loads_onto_the_right_parameters(
    tmp_path: Path, audio_file: Path, engine, insert_dummy
):
    """A parameter inserted in the middle does not move the values of a session."""
    engine.add_module(insert_dummy.code)
    engine.set_param(0, "Threshold", 0.11)
    engine.set_param(0, "Ratio", 2.22)
    engine.set_param(0, "Width", 3.33)

    target = tmp_path / "session.json"
    save_document(target, engine, _audio_ref(audio_file))

    _insert_a_parameter(insert_dummy)

    restored = create_engine(48000, 2)
    load_document(target, restored)

    assert restored.get_param(0, "Threshold") == pytest.approx(0.11)
    assert restored.get_param(0, "Ratio") == pytest.approx(2.22)
    assert restored.get_param(0, "Width") == pytest.approx(3.33)
    # the new parameter keeps its own default
    assert restored.get_param(0, "Release") == pytest.approx(1.0)


def test_the_parameters_that_moved_are_reported(
    tmp_path: Path, audio_file: Path, engine, insert_dummy
):
    """A load says which parameters the insertion moved."""
    engine.add_module(insert_dummy.code)
    engine.set_param(0, "Threshold", 0.11)

    target = tmp_path / "session.json"
    save_document(target, engine, _audio_ref(audio_file))

    _insert_a_parameter(insert_dummy)

    restored = create_engine(48000, 2)
    document = load_document(target, restored)
    report = document_param_report(document)

    moved = {entry["name"]: (entry["was"], entry["now"]) for entry in report["moved"]}
    assert moved == {"Width": (2, 3), "Enabled": (3, 4)}
    assert report["applied"] == 4
    assert report["unknown"] == []


def test_a_parameter_that_no_longer_exists_is_reported_and_skipped(
    tmp_path: Path, audio_file: Path, engine, insert_dummy
):
    """A parameter the module does not know is reported, never handed on."""
    engine.add_module(insert_dummy.code)
    engine.set_param(0, "Threshold", 0.11)
    engine.set_param(0, "Ratio", 2.22)

    target = tmp_path / "session.json"
    save_document(target, engine, _audio_ref(audio_file))

    # "Ratio" is gone from the module.
    insert_dummy.PARAMS = [
        insert_dummy.PARAMS[0], insert_dummy.PARAMS[2], insert_dummy.PARAMS[3]
    ]

    restored = create_engine(48000, 2)
    document = load_document(target, restored)
    report = document_param_report(document)

    assert report["unknown"] == ["Ratio"]
    # the value did not land on a neighbour
    assert restored.get_param(0, "Threshold") == pytest.approx(0.11)
    assert restored.get_param(0, "Width") == pytest.approx(3.0)
    # and it is kept, so the next save does not lose it
    assert restored.chain[0].module.extra_params["Ratio"] == pytest.approx(2.22)


def test_a_parameter_whose_meaning_changed_is_reported(
    tmp_path: Path, audio_file: Path, engine, insert_dummy
):
    """A load says when a parameter kept its name but changed its meaning."""
    engine.add_module(insert_dummy.code)
    engine.set_param(0, "Threshold", 0.11)

    target = tmp_path / "session.json"
    save_document(target, engine, _audio_ref(audio_file))

    # "Threshold" has another range now.
    insert_dummy.PARAMS = [dict(insert_dummy.PARAMS[0], maximum=2.0)] + \
        insert_dummy.PARAMS[1:]

    restored = create_engine(48000, 2)
    document = load_document(target, restored)
    report = document_param_report(document)

    changed = {entry["name"]: (entry["was"], entry["now"]) for entry in report["changed"]}
    assert changed == {"Threshold": ("float:0:1", "float:0:2")}
    # the value still lands on the parameter it belongs to
    assert restored.get_param(0, "Threshold") == pytest.approx(0.11)


def test_a_document_without_the_ids_still_loads(
    tmp_path: Path, audio_file: Path, engine, insert_dummy
):
    """A document written before the identity was carried still loads."""
    engine.add_module(insert_dummy.code)
    engine.set_param(0, "Threshold", 0.11)
    engine.set_param(0, "Width", 3.33)

    target = tmp_path / "session.json"
    save_document(target, engine, _audio_ref(audio_file))

    data = json.loads(target.read_text(encoding="utf-8"))
    for node in data["chain"]["steps"][0]["params"].values():
        node.pop("Id", None)
        node.pop("Sig", None)
    target.write_text(json.dumps(data), encoding="utf-8")

    _insert_a_parameter(insert_dummy)

    restored = create_engine(48000, 2)
    document = load_document(target, restored)
    report = document_param_report(document)

    # the name is the identity: the values land on their own parameter
    assert restored.get_param(0, "Threshold") == pytest.approx(0.11)
    assert restored.get_param(0, "Width") == pytest.approx(3.33)
    # and the load has nothing to report about a document that says no id
    assert report["moved"] == []
    assert report["changed"] == []
    assert report["unknown"] == []


def test_a_document_that_carries_only_the_ids_is_read_as_a_position(
    tmp_path: Path, audio_file: Path, engine, insert_dummy
):
    """The old, id-only document names a position, and the load says so."""
    engine.add_module(insert_dummy.code)
    engine.set_param(0, "Threshold", 0.11)
    engine.set_param(0, "Width", 3.33)

    target = tmp_path / "session.json"
    save_document(target, engine, _audio_ref(audio_file))

    data = json.loads(target.read_text(encoding="utf-8"))
    params = data["chain"]["steps"][0]["params"]
    data["chain"]["steps"][0]["params"] = {
        str(index): node for index, node in enumerate(params.values())
    }
    target.write_text(json.dumps(data), encoding="utf-8")

    _insert_a_parameter(insert_dummy)

    restored = create_engine(48000, 2)
    document = load_document(target, restored)
    report = document_param_report(document)

    # The id is the position the parameter had when the document was
    # written, so after the insertion the value of "Width" lands on the
    # parameter that took its place, and the value of "Enabled" lands on
    # "Width".
    assert restored.get_param(0, "Threshold") == pytest.approx(0.11)
    assert restored.get_param(0, "Ratio") == pytest.approx(2.0)
    assert restored.get_param(0, "Release") == pytest.approx(3.33)
    assert restored.get_param(0, "Width") == pytest.approx(1.0)
    # and the load says that it read a position
    assert [entry["position"] for entry in report["positional"]] == [0, 1, 2, 3]


def test_a_document_that_names_an_old_name_still_loads(
    tmp_path: Path, audio_file: Path, engine, insert_dummy
):
    """A parameter that was renamed keeps its old name as an alias."""
    insert_dummy.PARAMS[0] = dict(insert_dummy.PARAMS[0], aliases=("Gate",))

    engine.add_module(insert_dummy.code)
    engine.set_param(0, "Threshold", 0.11)

    target = tmp_path / "session.json"
    save_document(target, engine, _audio_ref(audio_file))

    # The document names the name the parameter had before it was renamed.
    data = json.loads(target.read_text(encoding="utf-8"))
    params = data["chain"]["steps"][0]["params"]
    params["Gate"] = params.pop("Threshold")

    restored = create_engine(48000, 2)
    document = load_document(target, restored)

    assert restored.get_param(0, "Threshold") == pytest.approx(0.11)
    assert document_param_report(document)["unknown"] == []


def test_the_report_of_a_load_is_not_part_of_the_document(
    tmp_path: Path, audio_file: Path, engine, insert_dummy
):
    """The report belongs to the load, not to the document."""
    engine.add_module(insert_dummy.code)

    target = tmp_path / "session.json"
    save_document(target, engine, _audio_ref(audio_file))

    restored = create_engine(48000, 2)
    document = load_document(target, restored)
    assert "param_report" in document

    again = save_document(tmp_path / "again.json", restored, _audio_ref(audio_file))
    assert "param_report" not in again
    assert "param_report" not in json.loads(
        (tmp_path / "again.json").read_text(encoding="utf-8")
    )
