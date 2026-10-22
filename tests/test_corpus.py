"""
tests/test_corpus.py — the gate of the Auralis golden corpus.

The corpus is the instrument the module regression tests measure with, so it
has to be trustworthy:

* every clip is present and is the file the manifest names,
* every claim the manifest makes is the one a measurement of the clip
  returns (:func:`corpus.measure.verify_corpus`),
* every clip carries its ground truth in a form a test can assert,
* the material the module owners need is there,
* a rebuild reproduces the files byte for byte, and
* **a clip that does not match its manifest fails the gate** - the last one is
  proved here by corrupting a copy of the corpus and by rewriting a claim in
  a copy of the manifest, never the corpus itself.

The tests never write into the corpus directory: every mutation happens in a
temporary copy.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIR = ROOT / "corpus"
MANIFEST_PATH = CORPUS_DIR / "manifest.json"
SCHEMA_PATH = CORPUS_DIR / "manifest.schema.json"

if str(CORPUS_DIR) not in sys.path:
    sys.path.insert(0, str(CORPUS_DIR))

import build_corpus  # noqa: E402  (the path has to be set up first)
import measure  # noqa: E402

#: The material the module owners asked for, and the clip that carries it.
#: The test fails when one of them is missing.
REQUIRED_MATERIAL: dict[str, str] = {
    "a voice-shaped signal with a hiss": "voice_hiss",
    "a music mixture": "music_mix",
    "a signal with a transient (a plosive)": "plosive",
    "a signal with a room tail": "reverb",
    "a clipped signal": "clipped",
    "a signal with a 50 Hz hum": "hum50",
    "a signal with sibilance": "sibilance",
    "a stereo pair with a phase difference": "stereo_phase",
    "a signal with low level rumble": "rumble",
    "a signal with a slowly drifting pitch": "pitch_drift",
}

#: The number of checks the verification has to make.  The floor keeps the
#: gate from being weakened silently: a check that is dropped shows up here.
MINIMUM_CHECKS = 200

#: The number of ground truth claims every clip has to carry.
MINIMUM_CLAIMS = 8


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _sha256(path: Path) -> str:
    """The SHA-256 checksum of a file, in hexadecimal."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_corpus(tmp_path: Path) -> Path:
    """Copy the manifest and the audio of the corpus into a temporary tree.

    Returns the root of the copy: it holds the ``audio/`` tree, exactly like
    the corpus directory itself.
    """
    root = tmp_path / "corpus"
    (root / "audio" / "test").mkdir(parents=True, exist_ok=True)
    shutil.copy2(MANIFEST_PATH, root / "manifest.json")
    for entry in measure.load_manifest():
        source = CORPUS_DIR / str(entry["file"])
        target = root / str(entry["file"])
        shutil.copy2(source, target)
    return root


def _failing_checks(report: dict) -> list[str]:
    """The names of the checks that failed."""
    return [str(item["name"]) for item in report["checks"] if not item.get("ok")]


# ---------------------------------------------------------------------------
# the manifest
# ---------------------------------------------------------------------------
def test_the_manifest_matches_its_schema():
    """Every manifest entry validates against corpus/manifest.schema.json."""
    jsonschema = pytest.importorskip("jsonschema", reason="jsonschema is not installed")
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    entries = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(entries, list) and entries
    for entry in entries:
        jsonschema.validate(entry, schema)


def test_the_manifest_has_a_unique_id_for_every_clip():
    """No two clips share an id, and every id names its file."""
    entries = measure.load_manifest()
    ids = [str(entry["id"]) for entry in entries]
    assert len(ids) == len(set(ids)), "the corpus manifest has duplicate ids"
    for entry in entries:
        assert str(entry["file"]) == f"audio/test/{entry['id']}.wav"


# ---------------------------------------------------------------------------
# the files
# ---------------------------------------------------------------------------
def test_every_clip_is_present_and_matches_its_checksum():
    """Every clip of the manifest is on disk and has the recorded checksum."""
    entries = measure.load_manifest()
    assert entries, "the manifest is empty"
    for entry in entries:
        path = CORPUS_DIR / str(entry["file"])
        assert path.exists(), f"{entry['id']}: {path} is missing"
        assert _sha256(path) == str(entry["sha256"]), f"{entry['id']}: the file was changed"


def test_every_clip_is_synthesised_and_free_of_third_party_material():
    """The corpus carries no third party material: every clip is CC0 and made here."""
    for entry in measure.load_manifest():
        assert entry["licence"] == "CC0-1.0", f"{entry['id']}: the licence is not CC0"
        assert entry["source_url"] == "synthesised", f"{entry['id']}: the source is not a synthesis"
        assert "synthesised" in str(entry["source_credit"]), f"{entry['id']}: no synthesis credit"


# ---------------------------------------------------------------------------
# the ground truth
# ---------------------------------------------------------------------------
def test_the_corpus_verifies_against_its_own_ground_truth():
    """Every claim the manifest makes is the one a measurement returns."""
    report = measure.verify_corpus()
    failed = _failing_checks(report)
    assert not failed, "the corpus does not match its manifest: " + "; ".join(failed)
    assert len(report["checks"]) >= MINIMUM_CHECKS, (
        f"the verification makes only {len(report['checks'])} checks, "
        f"at least {MINIMUM_CHECKS} are expected"
    )
    assert report["clips"] == len(measure.load_manifest())


def test_every_clip_carries_assertable_ground_truth():
    """Every clip states its levels, its frequencies, its positions and its duration."""
    for entry in measure.load_manifest():
        clip_id = str(entry["id"])
        annotations = dict(entry.get("annotations", {}))
        levels = dict(annotations.get("levels", {}))
        assert float(entry["duration_s"]) > 0.0, f"{clip_id}: no duration"
        for key in ("peak_dbfs", "rms_dbfs", "total_rms_dbfs"):
            assert key in levels, f"{clip_id}: no {key}"
        assert annotations.get("events"), f"{clip_id}: no events"
        frequencies = annotations.get("frequencies", [])
        assert frequencies, f"{clip_id}: no frequency ground truth"
        for frequency in frequencies:
            assert "hz" in frequency, f"{clip_id}: a frequency claim has no frequency"
            if frequency["hz"] is not None:
                assert float(frequency.get("tolerance_hz", 0.0)) > 0.0, (
                    f"{clip_id}: a frequency claim has no tolerance"
                )
        claims = measure.ground_truth_claims(entry)
        assert len(claims) >= MINIMUM_CLAIMS, (
            f"{clip_id}: only {len(claims)} ground truth claims, "
            f"at least {MINIMUM_CLAIMS} are expected"
        )


def test_the_manifest_carries_the_material_the_modules_need():
    """Every kind of material a module owner asked for is in the corpus."""
    ids = {str(entry["id"]) for entry in measure.load_manifest()}
    missing = [
        f"{what} (expected the clip {clip_id!r})"
        for what, clip_id in REQUIRED_MATERIAL.items()
        if clip_id not in ids
    ]
    assert not missing, "the corpus is missing: " + "; ".join(missing)


# ---------------------------------------------------------------------------
# the gate: a wrong clip has to fail
# ---------------------------------------------------------------------------
def test_a_corrupted_clip_fails_the_gate(tmp_path):
    """A clip whose measurement does not match its manifest fails the gate."""
    root = _copy_corpus(tmp_path)
    entry = measure.manifest_entry("hum50")
    path = root / str(entry["file"])

    # The clip is turned up by 1.5: its levels no longer match the manifest.
    samples, sample_rate = measure.read_wav(path)
    build_corpus._write_wav(path, samples * 1.5, sample_rate, build_corpus.BIT_DEPTH)

    report = measure.verify_corpus(manifest=root / "manifest.json", audio_dir=root)
    assert not report["ok"], "a corrupted clip passed the gate"
    failed = _failing_checks(report)
    assert failed, "the gate reported no failure"
    assert any(name.startswith("hum50:") for name in failed), (
        "the gate did not notice the corrupted clip: " + "; ".join(failed)
    )


def test_a_missing_clip_fails_the_gate(tmp_path):
    """A clip that is not on disk fails the gate."""
    root = _copy_corpus(tmp_path)
    entry = measure.manifest_entry("clicks")
    (root / str(entry["file"])).unlink()

    report = measure.verify_corpus(manifest=root / "manifest.json", audio_dir=root)
    assert not report["ok"], "a missing clip passed the gate"
    assert any(name.startswith("clicks:") for name in _failing_checks(report))


def test_a_rewritten_claim_fails_the_gate(tmp_path):
    """A manifest that claims the wrong level fails the gate."""
    root = _copy_corpus(tmp_path)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest:
        if entry["id"] == "hum50":
            entry["annotations"]["levels"]["peak_dbfs"] = (
                float(entry["annotations"]["levels"]["peak_dbfs"]) + 1.0
            )
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    report = measure.verify_corpus(manifest=root / "manifest.json", audio_dir=root)
    assert not report["ok"], "a wrong claim passed the gate"
    assert any("hum50: peak" in name for name in _failing_checks(report))


def test_a_moved_click_fails_the_gate(tmp_path):
    """A manifest that claims a click in the wrong place fails the gate."""
    root = _copy_corpus(tmp_path)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest:
        if entry["id"] == "clicks":
            events = entry["annotations"]["events"]
            events[0]["start_s"] = float(events[0]["start_s"]) + 0.01
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    report = measure.verify_corpus(manifest=root / "manifest.json", audio_dir=root)
    assert not report["ok"], "a moved click passed the gate"
    assert any("click at" in name for name in _failing_checks(report))


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------
def test_a_rebuild_is_byte_identical(tmp_path):
    """A rebuild of the corpus reproduces every file byte for byte."""
    entries = build_corpus.build_all(output_dir=tmp_path, write=True)
    declared = measure.load_manifest()
    assert len(entries) == len(declared), "the rebuild has a different number of clips"
    for built, entry in zip(entries, declared):
        clip_id = str(entry["id"])
        assert built["id"] == clip_id
        path = tmp_path / f"{clip_id}.wav"
        assert path.exists(), f"{clip_id}: the rebuild did not write the clip"
        assert _sha256(path) == str(entry["sha256"]), (
            f"{clip_id}: the rebuild is not byte identical to the file on disk"
        )


def test_a_rebuild_reproduces_the_manifest(tmp_path):
    """A rebuild reproduces every claim of the manifest, level by level."""
    entries = build_corpus.build_all(output_dir=tmp_path, write=True)
    declared = measure.load_manifest()
    for built, entry in zip(entries, declared):
        clip_id = str(entry["id"])
        assert built["annotations"] == entry["annotations"], (
            f"{clip_id}: the rebuild does not reproduce the ground truth"
        )
        assert float(built["duration_s"]) == float(entry["duration_s"])
        assert int(built["channels"]) == int(entry["channels"])
        assert int(built["sample_rate"]) == int(entry["sample_rate"])


def test_the_generator_is_seeded(tmp_path):
    """Two builds of the same generator produce the same samples."""
    first = build_corpus.build_all(output_dir=tmp_path / "a", write=True)
    second = build_corpus.build_all(output_dir=tmp_path / "b", write=True)
    for left, right in zip(first, second):
        assert left["sha256"] == right["sha256"], f"{left['id']}: the two builds differ"


# ---------------------------------------------------------------------------
# the measurements themselves
# ---------------------------------------------------------------------------
def test_the_measurement_helper_finds_what_the_manifest_claims():
    """A spot check of the figures a module test reads out of the manifest."""
    for clip_id in ("clicks", "hum50", "sibilance"):
        entry = measure.manifest_entry(clip_id)
        samples, sample_rate = measure.load_clip(clip_id)
        for band in entry["annotations"]["bands"]:
            edges = band["band_hz"]
            region = band.get("region_s")
            measured = measure.band_energy_db(
                samples,
                sample_rate,
                float(edges[0]),
                float(edges[1]),
                region[0] if region else None,
                region[1] if region else None,
                band.get("mix", "mono"),
            )
            assert abs(measured - float(band["level_dbfs"])) < 0.3, (
                f"{clip_id}: the band {band['name']} reads {measured:.2f} dBFS, "
                f"the manifest claims {band['level_dbfs']} dBFS"
            )


def test_the_phase_of_the_stereo_pair_is_the_declared_one():
    """The right channel of the phase clip leads the left one by 90 degrees."""
    entry = measure.manifest_entry("stereo_phase")
    declared = dict(entry["annotations"]["phase"])
    samples, sample_rate = measure.load_clip("stereo_phase")
    measured = measure.phase_difference_deg(samples, sample_rate)
    assert measured is not None
    assert abs(measured - float(declared["degrees"])) <= float(declared["tolerance_deg"])
    correlation = measure.interchannel_correlation(samples, sample_rate)
    assert abs(correlation - float(declared["correlation"])) <= float(
        declared["tolerance_correlation"]
    )


def test_the_pitch_of_the_drifting_clip_drifts():
    """The drifting clip starts at one frequency and ends at another."""
    entry = measure.manifest_entry("pitch_drift")
    frequencies = {
        str(item["name"]): item for item in entry["annotations"]["frequencies"]
    }
    samples, sample_rate = measure.load_clip("pitch_drift")
    for name, claim in frequencies.items():
        region = claim["region_s"]
        measured = measure.dominant_frequency(
            samples,
            sample_rate,
            region[0],
            region[1],
            claim.get("mix", "mono"),
            claim["search_hz"][0],
            claim["search_hz"][1],
        )
        assert measured is not None
        assert abs(measured - float(claim["hz"])) <= float(claim["tolerance_hz"]), (
            f"{name}: measured {measured:.2f} Hz, the manifest claims {claim['hz']} Hz"
        )
    assert float(frequencies["end"]["hz"]) > float(frequencies["start"]["hz"])


def test_the_voice_and_the_hiss_are_in_different_channels():
    """The voice is in the mid of the voice clip and the hiss in the side."""
    samples, _sample_rate = measure.load_clip("voice_hiss")
    mid, side = measure.mid_side(samples)
    assert measure.rms_db(mid) > measure.rms_db(side) + 10.0
    assert np.max(np.abs(mid)) > np.max(np.abs(side))


def test_the_manifest_entry_of_every_clip_names_its_own_file():
    """A clip that is not in the manifest is not found by the helper."""
    with pytest.raises(KeyError):
        measure.manifest_entry("a_clip_that_does_not_exist")
