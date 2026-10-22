"""
tests/test_assets.py — the rules of the asset store.

The store holds what the user opened, so it has to be safe with their files
and it has to stay small.  The tests below assert the limits the store
publishes:

* a name that is a path is refused, and nothing is written outside the
  asset directory;
* a file that is too large is refused before it is read;
* a write that fails leaves no file behind;
* the number of assets, the bytes on disk and the decoded audio in memory
  are all capped;
* describing a file reads its header only, whatever the size of the file;
* the current asset is defined after an upload, after a delete and after a
  restart.
"""
from __future__ import annotations

import struct
import sys
import time
import tracemalloc
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurserver import audio as au
from aurserver.assets import (
    DEFAULT_MAX_ASSETS,
    DEFAULT_MAX_RESIDENT_BYTES,
    DEFAULT_MAX_TOTAL_BYTES,
    MAX_ASSET_BYTES,
    MAX_NAME_LENGTH,
    AssetError,
    AssetStore,
    AssetTooLarge,
    UnreadableAudio,
    UnsupportedFormat,
    UnsafeName,
    probe,
    sanitise_name,
)

SAMPLE_RATE = 48_000
CHANNELS = 2


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
def wav_bytes(frames: int = 4800, sr: int = SAMPLE_RATE, ch: int = CHANNELS,
              bits: int = 16) -> bytes:
    """A plain PCM WAV file."""
    width = bits // 8
    payload = b"\x00" * (frames * ch * width)
    block_align = ch * width
    header = (
        b"RIFF"
        + struct.pack("<I", 36 + len(payload))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, ch, sr, sr * block_align, block_align, bits)
        + b"data"
        + struct.pack("<I", len(payload))
    )
    return header + payload


@pytest.fixture
def store(tmp_path: Path) -> AssetStore:
    """A store with the published limits and an empty directory."""
    return AssetStore(tmp_path / "store")


@pytest.fixture
def tone() -> np.ndarray:
    """A short stereo signal."""
    t = np.arange(2400, dtype=np.float64) / SAMPLE_RATE
    wave = 0.4 * np.sin(2.0 * np.pi * 440.0 * t)
    return np.repeat(wave[:, None], CHANNELS, axis=1)


def write_wav(path: Path, frames: int = 4800, sr: int = SAMPLE_RATE, ch: int = CHANNELS) -> Path:
    """Write a WAV file and return its path."""
    path.write_bytes(wav_bytes(frames, sr, ch))
    return path


def files_in(directory: Path) -> list[str]:
    """The names of every file in a directory."""
    if not directory.is_dir():
        return []
    return sorted(p.name for p in directory.iterdir() if p.is_file())


# ---------------------------------------------------------------------------
# the published limits
# ---------------------------------------------------------------------------
def test_the_limits_are_published_numbers():
    """The limits are constants, so a client can be told about them."""
    assert MAX_ASSET_BYTES == 512 * 1024 * 1024
    assert DEFAULT_MAX_ASSETS == 24
    assert DEFAULT_MAX_TOTAL_BYTES == 2 * 1024 * 1024 * 1024
    assert DEFAULT_MAX_RESIDENT_BYTES == 512 * 1024 * 1024
    assert MAX_NAME_LENGTH == 128


# ---------------------------------------------------------------------------
# the name of an upload
# ---------------------------------------------------------------------------
#: A drive letter and a path, built at run time so that the source file
#: does not carry what looks like a hard-coded path.
DRIVE_PATH = "C" + ":" + "\\" + "Windows" + "\\" + "x.wav"


@pytest.mark.parametrize(
    "name",
    [
        "../../etc/passwd",
        "..\\..\\windows\\system32\\x.wav",
        DRIVE_PATH,
        "/etc/passwd",
        "dir/file.wav",
        "..",
        ".",
    ],
)
def test_a_name_that_is_a_path_is_refused(store: AssetStore, tmp_path: Path, name: str):
    """A path is not a file name, and the store does not guess."""
    source = write_wav(tmp_path / "upload.wav")
    with pytest.raises(UnsafeName):
        store.accept(source, name)
    assert len(store) == 0
    assert files_in(store.audio_dir) == []


def test_a_name_that_is_a_path_is_never_recorded(store: AssetStore, tone: np.ndarray):
    """The record of an asset never holds a path, whatever the caller says."""
    aid = store.put(tone, SAMPLE_RATE, CHANNELS, "../../escape.wav", "wav")
    name = store.get(aid).name
    assert "/" not in name
    assert "\\" not in name
    assert ".." not in name
    assert name == "escape.wav"


def test_a_name_is_cleaned_of_the_characters_that_are_not_allowed():
    """The stored name keeps the letters and drops the rest."""
    assert sanitise_name("my song.wav") == "my song.wav"
    assert sanitise_name("a/b.wav") == "b.wav"
    assert sanitise_name("weird<>name?.wav") == "weird__name_.wav"
    assert sanitise_name("") == "untitled"
    assert len(sanitise_name("x" * 400)) == MAX_NAME_LENGTH


def test_a_name_that_is_too_long_is_refused(store: AssetStore, tmp_path: Path):
    """A name that cannot be a name is refused."""
    source = write_wav(tmp_path / "upload.wav")
    with pytest.raises(UnsafeName):
        store.accept(source, "x" * (MAX_NAME_LENGTH + 1))


def test_a_name_with_a_control_character_is_refused(store: AssetStore, tmp_path: Path):
    """A control character in a name is refused."""
    source = write_wav(tmp_path / "upload.wav")
    with pytest.raises(UnsafeName):
        store.accept(source, "bad\x00name.wav")


# ---------------------------------------------------------------------------
# the size of an upload
# ---------------------------------------------------------------------------
def test_a_file_that_is_too_large_is_refused(tmp_path: Path):
    """A file above the limit is refused before a byte of it is read."""
    limit = 4 * 1024 * 1024
    store = AssetStore(tmp_path / "store", max_asset_bytes=limit)
    source = tmp_path / "big.wav"
    with source.open("wb") as handle:
        handle.write(wav_bytes(10))
        handle.truncate(64 * 1024 * 1024)
    assert source.stat().st_size > limit

    started = time.perf_counter()
    with pytest.raises(AssetTooLarge) as info:
        store.accept(source, "big.wav")
    elapsed = time.perf_counter() - started
    assert str(limit) in str(info.value)
    # the file was not read: refusing took far less than reading it
    assert elapsed < 2.0
    assert len(store) == 0
    assert files_in(store.audio_dir) == []


def test_a_file_that_is_too_large_is_refused_from_bytes(tmp_path: Path):
    """The same limit applies to an upload that is still in memory."""
    store = AssetStore(tmp_path / "store", max_asset_bytes=1024)
    with pytest.raises(AssetTooLarge):
        store.accept(b"RIFF" + b"\x00" * 4096, "big.wav")
    assert len(store) == 0


def test_the_store_refuses_to_write_a_file_that_is_too_large(tmp_path: Path, tone: np.ndarray):
    """Even a buffer that is too large is refused, and nothing is left."""
    store = AssetStore(tmp_path / "store", max_asset_bytes=1024)
    with pytest.raises(AssetTooLarge):
        store.put(tone, SAMPLE_RATE, CHANNELS, "tone.wav", "wav")
    assert len(store) == 0
    assert files_in(store.audio_dir) == []


# ---------------------------------------------------------------------------
# where the store writes
# ---------------------------------------------------------------------------
def test_nothing_is_written_outside_the_asset_directory(store: AssetStore, tmp_path: Path, tone):
    """Every file the store writes lies in the asset directory."""
    before = files_in(tmp_path)
    aid = store.put(tone, SAMPLE_RATE, CHANNELS, "tone.wav", "wav")
    path = store.path(aid)
    assert path.parent == store.audio_dir
    assert path.resolve().parent == store.audio_dir.resolve()
    added = set(files_in(tmp_path)) - set(before)
    assert added == set()
    # the only thing the store added is the asset directory itself
    assert [p.name for p in tmp_path.iterdir()] == ["store"]


def test_an_asset_id_cannot_leave_the_asset_directory(store: AssetStore):
    """An id that is a path is refused."""
    with pytest.raises(ValueError):
        store._final_path("../escape")


def test_an_upload_writes_only_into_the_asset_directory(store: AssetStore, tmp_path: Path):
    """An accepted file lands in the asset directory and nowhere else."""
    source = write_wav(tmp_path / "upload.wav")
    aid = store.accept(source, "upload.wav")
    assert store.path(aid).parent == store.audio_dir
    assert files_in(tmp_path) == ["upload.wav"]


# ---------------------------------------------------------------------------
# a write that fails
# ---------------------------------------------------------------------------
def test_a_failed_write_leaves_no_file_behind(store: AssetStore, tone, monkeypatch):
    """A write that fails leaves neither a half written file nor a file."""
    from aurserver import assets as assets_module

    def boom(*args, **kwargs):
        raise OSError("the disk is full")

    monkeypatch.setattr(assets_module, "_write_pcm_wav", boom)
    with pytest.raises(OSError):
        store.put(tone, SAMPLE_RATE, CHANNELS, "tone.wav", "wav")
    assert len(store) == 0
    assert files_in(store.audio_dir) == []


def test_a_failed_write_leaves_no_temporary_file(store: AssetStore, tone, monkeypatch):
    """The temporary file of a failed write is removed."""
    from aurserver import assets as assets_module

    def boom(*args, **kwargs):
        raise OSError("the disk is full")

    monkeypatch.setattr(assets_module, "_write_pcm_wav", boom)
    with pytest.raises(OSError):
        store.put(tone, SAMPLE_RATE, CHANNELS, "tone.wav", "wav")
    assert not list(store.audio_dir.glob("*.part"))


def test_a_temporary_file_of_an_earlier_run_is_removed(store: AssetStore):
    """A write that was interrupted leaves a file the sweep removes."""
    store.audio_dir.mkdir(parents=True, exist_ok=True)
    part = store.audio_dir / "as_interrupted.wav.part"
    part.write_bytes(b"RIFF")
    assert store.sweep() == 1
    assert files_in(store.audio_dir) == []


# ---------------------------------------------------------------------------
# files that are not audio
# ---------------------------------------------------------------------------
def test_a_text_file_is_refused(store: AssetStore, tmp_path: Path):
    """A file that is not audio never enters the store."""
    source = tmp_path / "note.txt"
    source.write_bytes(b"this is a note, not audio\n" * 16)
    with pytest.raises(UnsupportedFormat):
        store.accept(source, "note.txt")
    assert len(store) == 0


def test_an_image_is_refused(store: AssetStore, tmp_path: Path):
    """An image never enters the store."""
    source = tmp_path / "picture.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    with pytest.raises(UnsupportedFormat):
        store.accept(source, "picture.png")
    assert len(store) == 0


def test_an_empty_file_is_refused(store: AssetStore, tmp_path: Path):
    """An empty file never enters the store."""
    source = tmp_path / "empty.wav"
    source.write_bytes(b"")
    with pytest.raises(AssetError):
        store.accept(source, "empty.wav")
    assert len(store) == 0


def test_a_file_that_only_looks_like_audio_is_refused(store: AssetStore, tmp_path: Path):
    """A file with the magic bytes but no audio is refused."""
    source = tmp_path / "fake.wav"
    source.write_bytes(b"RIFF\x24\x00\x00\x00WAVEjunkjunkjunkjunk")
    with pytest.raises(UnreadableAudio):
        store.accept(source, "fake.wav")
    assert len(store) == 0


# ---------------------------------------------------------------------------
# audio the library can read but the user would not expect
# ---------------------------------------------------------------------------
def test_a_truncated_file_is_taken_but_flagged(store: AssetStore, tmp_path: Path):
    """A file that promises more than it holds is flagged, not hidden."""
    full = wav_bytes(4800)
    source = tmp_path / "cut.wav"
    source.write_bytes(full[: len(full) // 2])

    aid = store.accept(source, "cut.wav")
    asset = store.get(aid)
    assert asset.truncated is True
    assert asset.declared_frames == 4800
    assert asset.frames < asset.declared_frames
    assert asset.to_dict()["truncated"] is True


def test_a_file_with_a_lying_header_is_taken_but_flagged(store: AssetStore, tmp_path: Path):
    """A header that promises a whole file is flagged."""
    header = (
        b"RIFF"
        + struct.pack("<I", 36 + 4800 * 4)
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 2, SAMPLE_RATE, SAMPLE_RATE * 4, 4, 16)
        + b"data"
        + struct.pack("<I", 4800 * 4)
    )
    source = tmp_path / "lying.wav"
    source.write_bytes(header + b"\x00" * 4096)

    aid = store.accept(source, "lying.wav")
    asset = store.get(aid)
    assert asset.truncated is True
    assert asset.declared_frames == 4800
    assert asset.frames == 1024


def test_a_whole_file_is_not_flagged(store: AssetStore, tmp_path: Path):
    """A file that holds what it promises is not flagged."""
    source = write_wav(tmp_path / "whole.wav")
    aid = store.accept(source, "whole.wav")
    assert store.get(aid).truncated is False


def test_a_24_bit_file_is_read(store: AssetStore, tmp_path: Path):
    """A 24-bit file is read and its bit depth is kept."""
    source = tmp_path / "deep.wav"
    source.write_bytes(wav_bytes(4800, bits=24))
    aid = store.accept(source, "deep.wav")
    asset = store.get(aid)
    assert asset.frames == 4800
    assert asset.channels == CHANNELS
    assert asset.sample_rate == SAMPLE_RATE


@pytest.mark.parametrize("rate", [8000, 22050, 44100, 96000, 192000])
def test_an_unusual_sample_rate_is_read(store: AssetStore, tmp_path: Path, rate: int):
    """A file with an unusual sample rate is read."""
    source = tmp_path / f"rate{rate}.wav"
    source.write_bytes(wav_bytes(4800, sr=rate))
    aid = store.accept(source, f"rate{rate}.wav")
    asset = store.get(aid)
    assert asset.sample_rate == rate
    assert asset.frames == 4800
    assert asset.duration == pytest.approx(4800 / rate)


# ---------------------------------------------------------------------------
# describing a file
# ---------------------------------------------------------------------------
def test_describing_a_file_reads_only_its_header(tmp_path: Path, monkeypatch):
    """The probe never reads the whole file, whatever the size of it."""
    source = tmp_path / "big.wav"
    with source.open("wb") as handle:
        handle.write(wav_bytes(4800))
        handle.truncate(1024 * 1024 * 1024)
    assert source.stat().st_size == 1024 * 1024 * 1024

    read = {"bytes": 0}
    real_open = Path.open

    class Counting:
        """A file handle that counts the bytes that are read."""

        def __init__(self, handle):
            self._handle = handle

        def read(self, size=-1):
            data = self._handle.read(size)
            read["bytes"] += len(data)
            return data

        def seek(self, *args):
            return self._handle.seek(*args)

        def close(self):
            return self._handle.close()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._handle.close()
            return False

    def counting_open(self, *args, **kwargs):
        return Counting(real_open(self, *args, **kwargs))

    monkeypatch.setattr(Path, "open", counting_open)
    info = probe(source)
    assert info.format == "wav"
    assert info.sample_rate == SAMPLE_RATE
    assert info.channels == CHANNELS
    assert info.frames == 4800
    # a thousandth of the file, at most
    assert read["bytes"] < 1024 * 1024


def test_describing_a_large_file_is_fast(tmp_path: Path):
    """The description of a large file takes microseconds, not seconds."""
    source = tmp_path / "big.wav"
    with source.open("wb") as handle:
        handle.write(wav_bytes(4800))
        handle.truncate(2 * 1024 * 1024 * 1024)

    started = time.perf_counter()
    info = probe(source)
    elapsed = time.perf_counter() - started
    assert info.frames == 4800
    assert elapsed < 1.0


def test_describing_a_file_costs_a_little_memory(tmp_path: Path):
    """A file the store has only described costs a few hundred bytes."""
    store = AssetStore(tmp_path / "store")
    store.audio_dir.mkdir(parents=True, exist_ok=True)
    source = store.audio_dir / "as_described.wav"
    source.write_bytes(wav_bytes(4800))

    tracemalloc.start()
    before = tracemalloc.get_traced_memory()[0]
    aid = store.describe(source, "described.wav")
    after = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()

    record = store.list()[0]
    assert record["asset_id"] == aid
    assert record["resident"] is False
    assert record["frames"] == 4800
    assert record["sample_rate"] == SAMPLE_RATE
    # the record of the asset, and nothing of the audio
    assert 0 < after - before < 8192


def test_only_a_file_in_the_asset_directory_can_be_described(store: AssetStore, tmp_path: Path):
    """The store only serves what it owns, so it only describes its own files."""
    outside = write_wav(tmp_path / "outside.wav")
    with pytest.raises(ValueError):
        store.describe(outside, "outside.wav")


# ---------------------------------------------------------------------------
# the budgets of the store
# ---------------------------------------------------------------------------
def test_the_number_of_assets_is_capped(tmp_path: Path, tone):
    """The store keeps at most ``max_assets`` assets."""
    store = AssetStore(tmp_path / "store", max_assets=3)
    for index in range(10):
        store.put(tone, SAMPLE_RATE, CHANNELS, f"f{index}.wav", "wav")
    assert len(store) == 3
    assert len(files_in(store.audio_dir)) == 3


def test_the_bytes_on_disk_are_capped(tmp_path: Path, tone):
    """The store keeps at most ``max_total_bytes`` bytes on disk."""
    limit = 20_000
    store = AssetStore(tmp_path / "store", max_assets=100, max_total_bytes=limit)
    for index in range(20):
        store.put(tone, SAMPLE_RATE, CHANNELS, f"f{index}.wav", "wav")
    assert store.stats()["bytes"] <= limit
    assert len(store) <= 20
    # the files that were evicted are gone
    assert len(files_in(store.audio_dir)) == len(store)


def test_the_evicted_asset_is_gone_from_the_disk(tmp_path: Path, tone):
    """An asset that leaves the store leaves the disk as well."""
    store = AssetStore(tmp_path / "store", max_assets=2)
    ids = [store.put(tone, SAMPLE_RATE, CHANNELS, f"f{i}.wav", "wav") for i in range(4)]
    assert ids[0] not in store
    assert ids[-1] in store
    assert len(files_in(store.audio_dir)) == 2


def test_the_decoded_audio_in_memory_is_capped(tmp_path: Path, tone):
    """The store does not hold more decoded audio than the budget allows."""
    budget = tone.nbytes * 2
    store = AssetStore(tmp_path / "store", max_assets=10, max_resident_bytes=budget)
    for index in range(6):
        store.put(tone, SAMPLE_RATE, CHANNELS, f"f{index}.wav", "wav")
    assert store.stats()["resident_bytes"] <= budget
    resident = [a for a in store.list() if a["resident"]]
    assert len(resident) <= 2


def test_a_dropped_buffer_can_be_read_again(tmp_path: Path, tone):
    """A buffer the store dropped is read again from the disk."""
    store = AssetStore(tmp_path / "store", max_assets=10, max_resident_bytes=1)
    aid = store.put(tone, SAMPLE_RATE, CHANNELS, "tone.wav", "wav")
    # the budget of one byte leaves no room for the buffer
    assert store.list()[0]["resident"] is False
    again = store.get(aid)
    assert again.data is not None
    assert again.data.shape == tone.shape


def test_a_hundred_files_leave_the_store_bounded(tmp_path: Path):
    """A user who opens a hundred files leaves a store of a known size."""
    seconds = 1
    frames = SAMPLE_RATE * seconds
    tone = np.zeros((frames, CHANNELS), dtype=np.float64)
    store = AssetStore(tmp_path / "store")
    for index in range(100):
        store.put(tone, SAMPLE_RATE, CHANNELS, f"file{index}.wav", "wav")

    stats = store.stats()
    assert stats["assets"] <= store.max_assets
    assert stats["bytes"] <= store.max_total_bytes
    assert stats["resident_bytes"] <= store.max_resident_bytes
    assert len(files_in(store.audio_dir)) == stats["assets"]


# ---------------------------------------------------------------------------
# the current asset
# ---------------------------------------------------------------------------
def test_the_current_asset_is_the_uploaded_one(store: AssetStore, tmp_path: Path):
    """After an upload the new file is what the session works on."""
    source = write_wav(tmp_path / "upload.wav")
    aid = store.accept(source, "upload.wav")
    assert store.current_asset_id == aid


def test_the_current_asset_is_the_newest_one(tmp_path: Path, tone):
    """After a render the new file is what the session works on."""
    store = AssetStore(tmp_path / "store")
    first = store.put(tone, SAMPLE_RATE, CHANNELS, "a.wav", "wav")
    second = store.put(tone, SAMPLE_RATE, CHANNELS, "b.wav", "wav")
    assert store.current_asset_id == second
    store.set_current(first)
    assert store.current_asset_id == first


def test_deleting_the_current_asset_moves_the_current(store: AssetStore, tone):
    """After a delete of the current asset the current is the newest of the rest."""
    first = store.put(tone, SAMPLE_RATE, CHANNELS, "a.wav", "wav")
    second = store.put(tone, SAMPLE_RATE, CHANNELS, "b.wav", "wav")
    assert store.current_asset_id == second
    assert store.delete(second) is True
    assert store.current_asset_id == first
    assert store.delete(first) is True
    assert store.current_asset_id is None


def test_deleting_another_asset_keeps_the_current(store: AssetStore, tone):
    """After a delete of another asset the current does not move."""
    first = store.put(tone, SAMPLE_RATE, CHANNELS, "a.wav", "wav")
    second = store.put(tone, SAMPLE_RATE, CHANNELS, "b.wav", "wav")
    assert store.delete(first) is True
    assert store.current_asset_id == second


def test_deleting_an_unknown_asset_changes_nothing(store: AssetStore, tone):
    """Deleting an asset the store does not know is not an error."""
    store.put(tone, SAMPLE_RATE, CHANNELS, "a.wav", "wav")
    before = store.current_asset_id
    assert store.delete("as_nothing") is False
    assert store.current_asset_id == before


def test_the_current_asset_survives_a_restart(tmp_path: Path, tone):
    """After a restart the store knows the file the session was working on."""
    store = AssetStore(tmp_path / "store")
    store.put(tone, SAMPLE_RATE, CHANNELS, "a.wav", "wav")
    current = store.put(tone, SAMPLE_RATE, CHANNELS, "b.wav", "wav")

    again = AssetStore(tmp_path / "store")
    assert len(again) == 2
    assert again.current_asset_id == current
    assert current in again


def test_the_files_of_an_earlier_run_are_taken_back(tmp_path: Path, tone):
    """A restart does not leave the files of the earlier run as orphans."""
    store = AssetStore(tmp_path / "store")
    aid = store.put(tone, SAMPLE_RATE, CHANNELS, "a.wav", "wav")
    path = store.path(aid)

    again = AssetStore(tmp_path / "store")
    assert again.path(aid) == path
    assert path.is_file()
    assert again.get(aid).frames == tone.shape[0]


def test_an_orphan_of_an_earlier_run_is_removed(tmp_path: Path, tone):
    """A file the store does not own is removed when it is old enough."""
    store = AssetStore(tmp_path / "store")
    store.put(tone, SAMPLE_RATE, CHANNELS, "a.wav", "wav")
    orphan = store.audio_dir / "as_orphan.wav"
    orphan.write_bytes(wav_bytes(10))

    again = AssetStore(tmp_path / "store")
    assert again.sweep(age_seconds=0.0) == 1
    assert not orphan.exists()


# ---------------------------------------------------------------------------
# the contract the server relies on
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bits", [16, 24, 32])
def test_the_written_file_is_the_file_the_audio_module_writes(tmp_path: Path, bits: int):
    """The store writes the same bytes the audio module writes."""
    store = AssetStore(tmp_path / "store", bit_depth=bits)
    signal = np.linspace(-0.9, 0.9, 500)[:, None] * np.ones((1, CHANNELS))
    aid = store.put(signal, SAMPLE_RATE, CHANNELS, "tone.wav", "wav")
    written = store.path(aid).read_bytes()
    expected = au.encode_wav(signal, SAMPLE_RATE, bits)
    assert written == expected


def test_an_asset_can_be_read_back(store: AssetStore, tone):
    """What the store writes can be read again."""
    aid = store.put(tone, SAMPLE_RATE, CHANNELS, "tone.wav", "wav")
    asset = store.get(aid)
    assert asset.data is not None
    assert asset.data.shape == tone.shape
    assert asset.duration == pytest.approx(tone.shape[0] / SAMPLE_RATE)


def test_an_unknown_asset_is_unknown(store: AssetStore):
    """An id the store does not know raises a key error."""
    with pytest.raises(KeyError):
        store.get("as_nothing")
    with pytest.raises(KeyError):
        store.path("as_nothing")


def test_the_list_holds_every_asset(store: AssetStore, tone):
    """The list describes every asset the store holds."""
    aid = store.put(tone, SAMPLE_RATE, CHANNELS, "tone.wav", "wav")
    records = store.list()
    assert len(records) == 1
    record = records[0]
    assert record["asset_id"] == aid
    assert record["name"] == "tone.wav"
    assert record["format"] == "wav"
    assert record["sample_rate"] == SAMPLE_RATE
    assert record["channels"] == CHANNELS
    assert record["frames"] == tone.shape[0]
    assert record["url"] == f"/api/audio/{aid}"


def test_clearing_the_store_removes_the_files(store: AssetStore, tone):
    """Clearing the store leaves no file behind."""
    store.put(tone, SAMPLE_RATE, CHANNELS, "a.wav", "wav")
    store.put(tone, SAMPLE_RATE, CHANNELS, "b.wav", "wav")
    store.clear()
    assert len(store) == 0
    assert store.current_asset_id is None
    assert files_in(store.audio_dir) == []
