"""
Tests for the batch delivery surface: the delivery catalogue, the chain
augmentation and the export helpers.

The suite is deterministic and offline (CONTRACT §9): no network, no server
and no third-party service is contacted.
"""
from __future__ import annotations

import json
import math
import wave
from pathlib import Path

import numpy as np
import pytest

from aurserver import batch_formats as bf

#: The delivery targets the product documents.
DOCUMENTED_KEYS = (
    "ebu-r128",
    "ebu-r128-short",
    "atsc-a85",
    "arib-tr-b32",
    "podcast",
    "music-stream",
    "spoken-word",
)

#: The names the targets are shown under.
DOCUMENTED_NAMES = (
    "EBU R128",
    "EBU R128 short",
    "ATSC A/85",
    "ARIB TR-B32",
    "Podcast",
    "Music streaming",
    "Spoken word",
)

#: The module codes the delivery stages use.
LOUDNESS_CODE = "AUR_LOUDNESS"
DITHER_CODE = "AUR_DITHER"


# ---------------------------------------------------------------------------
# the catalogue
# ---------------------------------------------------------------------------
def test_catalogue_has_the_documented_targets() -> None:
    """Every documented delivery target is in the catalogue."""
    keys = [row.key for row in bf.delivery_targets()]
    for key in DOCUMENTED_KEYS:
        assert key in keys, f"the catalogue is missing the target {key!r}"
    assert len(keys) == len(set(keys)), "the catalogue keys are not unique"


def test_catalogue_names_match_the_documented_names() -> None:
    """The catalogue shows the documented names."""
    names = [row.name for row in bf.delivery_targets()]
    for name in DOCUMENTED_NAMES:
        assert name in names, f"the catalogue is missing the target {name!r}"


def test_catalogue_values_are_sane() -> None:
    """Every target carries a usable loudness target and a true-peak ceiling."""
    for row in bf.delivery_targets():
        assert -31.0 <= row.integrated_lufs <= -9.0, (
            f"{row.key}: the integrated target {row.integrated_lufs} LUFS is out of range"
        )
        assert -3.0 <= row.true_peak_dbtp <= 0.0, (
            f"{row.key}: the true-peak ceiling {row.true_peak_dbtp} dBTP is out of range"
        )
        assert row.codec in ("pcm", "aac"), f"{row.key}: unknown codec {row.codec!r}"
        assert row.format == "wav", f"{row.key}: unknown container {row.format!r}"
        assert row.bit_depth in (16, 24, 32), f"{row.key}: unknown depth {row.bit_depth}"
        assert row.tolerance_lu > 0.0, f"{row.key}: the tolerance must be positive"


def test_bit_depth_policy_follows_the_delivery_kind() -> None:
    """A broadcast master is 24 bit, a lossy delivery 16 bit."""
    for row in bf.delivery_targets():
        if row.kind == "broadcast":
            assert row.bit_depth == 24, f"{row.key}: a broadcast master must be 24 bit"
        elif row.kind == "lossy":
            assert row.bit_depth == 16, f"{row.key}: a lossy delivery must be 16 bit"
        else:
            pytest.fail(f"{row.key}: unknown delivery kind {row.kind!r}")


def test_catalogue_records_carry_the_delivery_stages() -> None:
    """Every catalogue record exposes the stages a client needs."""
    records = bf.catalogue()
    assert len(records) == len(bf.delivery_targets())
    for record in records:
        assert record["loudness"]["code"] == LOUDNESS_CODE
        assert record["dither"]["code"] == DITHER_CODE
        assert record["loudness"]["params"]["Target Loudness"] == record["integrated_lufs"]
        assert record["loudness"]["params"]["True Peak"] == record["true_peak_dbtp"]
        assert record["dither"]["params"]["Bit Depth"] == str(record["bit_depth"])


def test_a_target_can_be_selected_by_key_and_by_name() -> None:
    """A target is selectable by its key and by its display name."""
    by_key = bf.get_target("ebu-r128")
    by_name = bf.get_target("EBU R128")
    assert by_key is by_name
    assert by_key.integrated_lufs == -23.0


# ---------------------------------------------------------------------------
# the chain augmentation
# ---------------------------------------------------------------------------
def test_augmented_chain_has_a_loudness_stage_at_the_requested_target() -> None:
    """The augmented chain normalises onto the target of the request."""
    target = bf.get_target("music-stream")
    steps = bf.augment_chain([], target)
    loudness = [step for step in steps if step["code"] == LOUDNESS_CODE]
    assert len(loudness) == 1, "the delivery chain needs exactly one loudness stage"
    params = loudness[0]["params"]
    assert params["Target Loudness"] == target.integrated_lufs
    assert params["True Peak"] == target.true_peak_dbtp


def test_augmented_chain_has_a_dither_at_the_requested_depth() -> None:
    """The augmented chain dithers to the word length of the delivery."""
    target = bf.get_target("ebu-r128")
    steps = bf.augment_chain([], target)
    dither = [step for step in steps if step["code"] == DITHER_CODE]
    assert len(dither) == 1, "the delivery chain needs exactly one dither stage"
    assert dither[0]["params"]["Bit Depth"] == str(target.bit_depth)


def test_augmented_chain_keeps_the_input_chain_in_front() -> None:
    """The delivery stages are appended behind the chain of the user."""
    chain = [{"code": "AUR_DECLICK", "enabled": True, "params": {"Sensitivity": 3.0}}]
    steps = bf.augment_chain(chain, "atsc-a85")
    assert [step["code"] for step in steps] == [
        "AUR_DECLICK",
        LOUDNESS_CODE,
        DITHER_CODE,
    ]
    assert steps[0]["params"]["Sensitivity"] == 3.0
    # the caller's chain is never modified
    assert chain == [{"code": "AUR_DECLICK", "enabled": True, "params": {"Sensitivity": 3.0}}]


def test_augmented_chain_follows_the_requested_bit_depth() -> None:
    """The dither follows the word length that was asked for."""
    steps = bf.augment_chain([], "podcast", bit_depth=24)
    dither = [step for step in steps if step["code"] == DITHER_CODE][0]
    assert dither["params"]["Bit Depth"] == "24"


def test_augmented_chain_of_a_32_bit_delivery_has_no_dither() -> None:
    """A 32 bit master needs no dither."""
    steps = bf.augment_chain([], "ebu-r128", bit_depth=32)
    assert [step["code"] for step in steps] == [LOUDNESS_CODE]


def test_the_engine_runs_the_augmented_chain_at_the_catalogue_values() -> None:
    """The engine applies the catalogue values, not the values of the core.

    The loudness module overwrites its manual target fields when a different
    standard is selected, so this check guards the one table: the values the
    catalogue publishes are the values the engine runs with.
    """
    from aurserver.ws import build_chain_engine

    target = bf.get_target("music-stream")
    steps = bf.augment_chain([], target)
    engine = build_chain_engine(steps, 48000, 2)

    loudness = None
    dither = None
    for handle in engine.chain:
        if handle.module.code == LOUDNESS_CODE:
            loudness = handle.module
        elif handle.module.code == DITHER_CODE:
            dither = handle.module
    assert loudness is not None, "the delivery chain has no loudness stage"
    assert dither is not None, "the delivery chain has no dither stage"

    assert float(loudness.params.get("Target Loudness")) == target.integrated_lufs
    assert float(loudness.params.get("True Peak")) == target.true_peak_dbtp
    assert str(dither.params.get("Bit Depth")) == str(target.bit_depth)

    # the module must not fall back on a standard of its own while it runs
    engine.process(np.zeros((4800, 2), dtype=np.float64))
    assert float(loudness.params.get("Target Loudness")) == target.integrated_lufs
    assert float(loudness.params.get("True Peak")) == target.true_peak_dbtp


def test_the_delivery_chain_lands_the_audio_on_the_catalogue_target() -> None:
    """A file that runs through the delivery chain comes out on the target.

    This is the end of the single table: the number the catalogue publishes
    is the number the engine delivers.
    """
    from aurcore.modules.loudness_control import integrated_loudness
    from aurserver.ws import build_chain_engine

    rate = 48000
    t = np.arange(rate * 3) / rate
    tone = np.sin(2 * np.pi * 440 * t) * 0.1
    samples = np.stack([tone, tone], axis=1)

    for key in ("ebu-r128", "atsc-a85", "music-stream", "podcast"):
        target = bf.get_target(key)
        engine = build_chain_engine(bf.augment_chain([], target), rate, 2)
        out = np.asarray(engine.process(samples.copy()), dtype=np.float64)
        measured = float(integrated_loudness(out, rate))
        assert abs(measured - target.integrated_lufs) < 0.5, (
            f"{key}: the delivery chain landed on {measured:.2f} LUFS "
            f"instead of {target.integrated_lufs} LUFS"
        )


def test_the_engine_processes_a_buffer_through_the_delivery_chain() -> None:
    """The augmented chain runs without raising and keeps the length."""
    from aurserver.ws import build_chain_engine

    steps = bf.augment_chain([{"code": "AUR_DECLICK", "params": {}}], "ebu-r128")
    engine = build_chain_engine(steps, 48000, 2)
    samples = np.zeros((4800, 2), dtype=np.float64)
    samples[:, 0] = np.sin(np.arange(4800) * 0.05) * 0.2
    samples[:, 1] = samples[:, 0]
    out = np.asarray(engine.process(samples), dtype=np.float64)
    assert out.shape == samples.shape
    assert bool(np.all(np.isfinite(out)))


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------
def test_an_unknown_target_raises_a_clear_error() -> None:
    """An unknown target names itself and lists the known targets."""
    with pytest.raises(bf.UnknownTargetError) as info:
        bf.get_target("no-such-target")
    message = str(info.value)
    assert "no-such-target" in message
    for key in DOCUMENTED_KEYS:
        assert key in message, f"the error does not mention the target {key!r}"


def test_an_unknown_target_raises_when_the_chain_is_augmented() -> None:
    """The augmentation refuses an unknown target as well."""
    with pytest.raises(bf.UnknownTargetError):
        bf.augment_chain([], "no-such-target")


def test_an_unknown_target_raises_when_a_file_is_named() -> None:
    """The export helper refuses an unknown target as well."""
    with pytest.raises(bf.UnknownTargetError):
        bf.output_filename("take.wav", "no-such-target")


def test_an_empty_target_raises() -> None:
    """An empty target is refused."""
    with pytest.raises(bf.UnknownTargetError):
        bf.get_target("")


# ---------------------------------------------------------------------------
# the file names
# ---------------------------------------------------------------------------
def test_the_file_name_is_deterministic(tmp_path: Path) -> None:
    """The same source always yields the same delivery name."""
    source = tmp_path / "interview_take-3.wav"
    first = bf.output_filename(source, "ebu-r128")
    second = bf.output_filename(source, "ebu-r128")
    assert first == second
    assert first.endswith(".wav")
    assert "ebu-r128" in first


def test_the_file_name_is_collision_free(tmp_path: Path) -> None:
    """Two different sources never share a delivery name."""
    left = tmp_path / "a" / "take.wav"
    right = tmp_path / "b" / "take.wav"
    left.parent.mkdir(parents=True, exist_ok=True)
    right.parent.mkdir(parents=True, exist_ok=True)
    left.write_bytes(b"")
    right.write_bytes(b"")

    left_name = bf.output_filename(left, "ebu-r128")
    right_name = bf.output_filename(right, "ebu-r128")
    assert left_name != right_name
    assert left_name.startswith("take__")
    assert right_name.startswith("take__")


def test_the_file_names_of_a_batch_are_unique(tmp_path: Path) -> None:
    """A batch of files never produces the same output name twice."""
    sources = []
    for index in range(5):
        folder = tmp_path / f"folder{index}"
        folder.mkdir(parents=True, exist_ok=True)
        source = folder / "take.wav"
        source.write_bytes(b"")
        sources.append(source)

    paths = bf.output_paths(sources, "podcast", tmp_path / "out")
    names = [path.name for path in paths]
    assert len(names) == len(set(names))
    assert all(path.parent == tmp_path / "out" for path in paths)
    assert all(name.endswith(".wav") for name in names)


def test_the_file_name_carries_the_target(tmp_path: Path) -> None:
    """The delivery target is part of the file name."""
    source = tmp_path / "take.wav"
    assert "podcast" in bf.output_filename(source, "podcast")


def test_the_file_name_sanitises_the_source(tmp_path: Path) -> None:
    """A source name with illegal characters still yields a usable name."""
    source = tmp_path / "my take (raw) #1.wav"
    name = bf.output_filename(source, "ebu-r128")
    stem = name.rsplit(".", 1)[0]
    assert all(character.isalnum() or character in "._-" for character in stem)


# ---------------------------------------------------------------------------
# the headroom line and the gain
# ---------------------------------------------------------------------------
def test_the_headroom_line_is_the_ceiling_less_the_margin() -> None:
    """The line the runner aims at is the ceiling less the margin."""
    assert bf.headroom_line_dbtp(-1.0) == pytest.approx(-1.05)
    assert bf.headroom_line_dbtp(-2.0) == pytest.approx(-2.05)
    assert bf.headroom_line_dbtp(-1.0, margin_db=0.2) == pytest.approx(-1.2)


def test_the_plan_aims_at_the_headroom_line() -> None:
    """The correction lands on the headroom line, not on the ceiling."""
    measured = bf.DeliveryMeasurement(
        loudness_lufs=-23.0,
        peak_dbfs=-2.0,
        true_peak_dbtp=-2.0,
        sample_peak=0.79,
    )
    plan = bf.plan_gain(measured, -21.0, -1.0)
    # the file is 2 dB under the ceiling: the line allows 0.95 dB of gain
    assert plan.headroom_db == pytest.approx(0.95)
    assert plan.gain_db == pytest.approx(0.95)
    assert plan.limited_by_peak is True
    # the file the plan produces sits on the line, 0.05 dB under the ceiling
    assert -2.0 + plan.gain_db == pytest.approx(bf.headroom_line_dbtp(-1.0))


def test_the_plan_keeps_the_headroom_when_the_target_is_reachable() -> None:
    """A target that fits under the line leaves the margin alone."""
    measured = bf.DeliveryMeasurement(
        loudness_lufs=-30.0,
        peak_dbfs=-20.0,
        true_peak_dbtp=-20.0,
        sample_peak=0.1,
    )
    plan = bf.plan_gain(measured, -14.0, -1.0)
    assert plan.limited_by_peak is False
    assert plan.gain_db == pytest.approx(16.0)
    # the file the plan produces is 4 dB under the ceiling: the margin is kept
    assert -20.0 + plan.gain_db < bf.headroom_line_dbtp(-1.0)


def test_a_file_without_a_loudness_gets_no_gain() -> None:
    """A file the meter cannot measure is not corrected."""
    measured = bf.DeliveryMeasurement(
        loudness_lufs=None,
        peak_dbfs=-2.0,
        true_peak_dbtp=-2.0,
        sample_peak=0.79,
    )
    plan = bf.plan_gain(measured, -21.0, -1.0)
    assert plan.gain_db == 0.0
    assert plan.wanted_db == 0.0
    assert plan.limited_by_peak is False


# ---------------------------------------------------------------------------
# the metadata and the writer
# ---------------------------------------------------------------------------
def test_the_metadata_describes_the_delivery(tmp_path: Path) -> None:
    """The metadata carries the delivery facts of the target."""
    target = bf.get_target("atsc-a85")
    metadata = bf.delivery_metadata(tmp_path / "take.wav", target)
    assert metadata["auralis.delivery.target"] == target.name
    assert metadata["auralis.delivery.integrated_lufs"] == target.integrated_lufs
    assert metadata["auralis.delivery.true_peak_dbtp"] == target.true_peak_dbtp
    assert metadata["auralis.delivery.bit_depth"] == target.bit_depth
    assert metadata["auralis.delivery.codec"] == target.codec
    assert metadata["auralis.generated"]


def test_the_writer_produces_a_readable_delivery_file(tmp_path: Path) -> None:
    """The delivered file is a valid WAV file with the metadata inside."""
    target = bf.get_target("ebu-r128")
    samples = np.zeros((2400, 2), dtype=np.float64)
    samples[:, 0] = np.sin(np.arange(2400) * 0.05) * 0.25
    samples[:, 1] = samples[:, 0]

    out = tmp_path / "delivery.wav"
    metadata = bf.write_delivery(
        out, samples, 48000, target, chain=bf.augment_chain([], target)
    )
    assert out.is_file()

    with wave.open(str(out), "rb") as handle:
        assert handle.getnchannels() == 2
        assert handle.getframerate() == 48000
        assert handle.getsampwidth() == 3  # 24 bit
        assert handle.getnframes() == 2400

    payload = out.read_bytes()
    assert b"LIST" in payload and b"INFO" in payload
    comment = payload.split(b"ICMT", 1)[1]
    length = int.from_bytes(comment[:4], "little")
    decoded = json.loads(comment[4 : 4 + length].decode("utf-8").rstrip("\x00"))
    assert decoded["auralis.delivery.target"] == target.name
    assert decoded["auralis.delivery.integrated_lufs"] == target.integrated_lufs
    assert decoded["auralis.chain"] == [LOUDNESS_CODE, DITHER_CODE]
    assert metadata["auralis.delivery.key"] == target.key


def test_the_loudness_of_a_delivered_file_can_be_measured(tmp_path: Path) -> None:
    """A delivered file can be measured, so a report can carry the value."""
    target = bf.get_target("ebu-r128")
    rate = 48000
    t = np.arange(rate) / rate
    samples = np.stack([np.sin(2 * np.pi * 440 * t) * 0.25] * 2, axis=1)
    out = tmp_path / "delivery.wav"
    bf.write_delivery(out, samples, rate, target)

    measured = bf.measure_loudness(out)
    assert measured is not None
    assert math.isfinite(measured)
    # a 440 Hz tone at -12 dBFS sits around -13 LUFS after the K-weighting
    assert -20.0 < measured < -10.0
    # the measurement of one file is stable
    assert bf.measure_loudness(out) == measured


def test_the_loudness_of_a_missing_file_is_unknown(tmp_path: Path) -> None:
    """A file that cannot be read carries no measurement."""
    assert bf.measure_loudness(tmp_path / "nothing.wav") is None

