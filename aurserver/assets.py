"""
aurserver.assets — the in-process asset store.

The store keeps what the user opened: every uploaded or rendered file becomes
an *asset* with a short id, the path of its WAV on disk, the measurements the
user interface shows, and the id of the asset the session is working on.

Three properties make the store trustworthy:

* **The upload is safe.**  A file enters the store through
  :meth:`AssetStore.accept`, which refuses a name that is a path, refuses a
  file larger than ``max_asset_bytes`` and writes through a temporary name,
  so a half written file never survives.  Nothing is ever written outside the
  asset directory.
* **The store cannot grow without limit.**  Three budgets bound it: the
  number of assets, the bytes they occupy on disk and the decoded audio held
  in memory.  When a budget is reached the least recently used asset is
  evicted and its file is deleted.
* **Describing a file is cheap.**  :func:`probe` reads the header only, so
  the format of a 10 GB file is known in microseconds and the store never
  holds more than the metadata of a file it has not decoded.
"""

from __future__ import annotations

import json
import os
import re
import struct
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from . import audio as au

__all__ = [
    "Asset",
    "AssetError",
    "AssetStore",
    "AssetTooLarge",
    "AudioInfo",
    "DEFAULT_MAX_ASSETS",
    "DEFAULT_MAX_RESIDENT_BYTES",
    "DEFAULT_MAX_TOTAL_BYTES",
    "MAX_ASSET_BYTES",
    "MAX_NAME_LENGTH",
    "SWEEP_AGE_SECONDS",
    "UnsupportedFormat",
    "UnreadableAudio",
    "UnsafeName",
    "probe",
    "sanitise_name",
]

#: How many assets the store keeps.
DEFAULT_MAX_ASSETS = 24

#: Bit depth of the WAV files the store writes.
DEFAULT_BIT_DEPTH = 24

#: Largest file the store accepts, in bytes (512 MiB).
#:
#: The number is the size of one file at the longest sensible session
#: length: 512 MiB of 24-bit stereo PCM is about 30 minutes at 48 kHz.  A
#: file above the limit is refused before a single byte of it is read.
MAX_ASSET_BYTES = 512 * 1024 * 1024

#: Largest total size of the files in the asset directory, in bytes (2 GiB).
#:
#: The count budget alone is not a bound: 24 files of half an hour each are
#: 4 GiB on disk.  This budget is the one that keeps the scratch directory
#: of a long session small.
DEFAULT_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024

#: Largest amount of decoded audio the store keeps in memory, in bytes
#: (512 MiB).
#:
#: Decoded audio is float64: a ten minute stereo file is 440 MiB in memory
#: and 173 MiB on disk.  The store therefore keeps the *file* and decodes it
#: on demand, and holds only as many decoded buffers as this budget allows.
DEFAULT_MAX_RESIDENT_BYTES = 512 * 1024 * 1024

#: How old a stale asset file must be before the store removes it, in seconds.
SWEEP_AGE_SECONDS = 3600.0

#: Longest file name the store records.
MAX_NAME_LENGTH = 128

#: How many bytes the probe reads from the head of a file.
PROBE_HEAD_BYTES = 65536

#: How many bytes the probe reads from the tail of a file.
PROBE_TAIL_BYTES = 65536

#: Name of the file that records what the store owns, inside the store root.
MANIFEST_NAME = "store.json"

#: Suffix of the temporary file a write goes through.
PART_SUFFIX = ".part"


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------
class AssetError(RuntimeError):
    """Base class of the errors the store raises."""


class UnsafeName(AssetError):
    """The name of an upload is a path, not a file name."""


class AssetTooLarge(AssetError):
    """The file is larger than the store accepts."""


class UnsupportedFormat(AssetError):
    """The file is not an audio file the store can read."""


class UnreadableAudio(AssetError):
    """The file is in a container the store reads, but it cannot be decoded."""


# ---------------------------------------------------------------------------
# names
# ---------------------------------------------------------------------------
_PATH_RE = re.compile(r"[\\/]")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def sanitise_name(name: str) -> str:
    """Return a display name that can never be a path.

    The name is taken from the last path component, every character that is
    not a letter, a digit or one of ``._- ()[]`` becomes an underscore, a
    leading dot is dropped and the result is cut to
    :data:`MAX_NAME_LENGTH` characters.
    """
    text = str(name or "").strip()
    text = _PATH_RE.split(text)[-1]
    text = "".join(ch if (ch.isalnum() or ch in "._- ()[]") else "_" for ch in text)
    text = text.strip().lstrip(".").strip()
    return text[:MAX_NAME_LENGTH] or "untitled"


def check_name(name: str) -> str:
    """Return the name of an upload, or refuse it.

    A name that is a path is refused rather than repaired: a client that
    sends ``../../etc/passwd`` or a drive letter and a path is not sending a
    file name, and the store does not guess what it meant.
    """
    text = str(name or "").strip()
    if not text:
        raise UnsafeName("the upload has no file name")
    if "\x00" in text or any(ord(ch) < 32 for ch in text):
        raise UnsafeName("the file name holds a control character")
    if _PATH_RE.search(text):
        raise UnsafeName(f"the file name must not be a path: {text!r}")
    if _DRIVE_RE.match(text):
        raise UnsafeName(f"the file name must not be a path: {text!r}")
    if text in (".", ".."):
        raise UnsafeName(f"the file name must not be a path: {text!r}")
    if len(text) > MAX_NAME_LENGTH:
        raise UnsafeName(f"the file name is longer than {MAX_NAME_LENGTH} characters")
    return text


# ---------------------------------------------------------------------------
# describing a file
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AudioInfo:
    """What the header of an audio file says about it.

    ``frames`` is ``None`` when the container does not carry the length in
    its header.  ``exact`` is ``False`` when the length had to be estimated.
    ``truncated`` is ``True`` when the header promises more data than the
    file holds.
    """

    format: str
    sample_rate: int
    channels: int
    frames: int | None
    bit_depth: int | None
    duration: float | None
    truncated: bool = False
    exact: bool = True

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe summary of the description."""
        return {
            "format": self.format,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "frames": self.frames,
            "bit_depth": self.bit_depth,
            "duration": self.duration,
            "truncated": self.truncated,
            "exact": self.exact,
        }


def probe(path: str | Path, label: str | None = None) -> AudioInfo:
    """Describe an audio file by reading its header only.

    The function never reads the whole file: it opens at most
    :data:`PROBE_HEAD_BYTES` bytes at the front and
    :data:`PROBE_TAIL_BYTES` at the back, so the cost does not depend on the
    size of the file.  A 10 GB file is described in microseconds.

    ``label`` is the name the file is known by; it is used to recognise the
    container of a file that carries no magic bytes and in the messages.

    Raises :class:`UnsupportedFormat` when the file is not an audio file the
    store can read, and :class:`UnreadableAudio` when the container is one
    the store reads but the file cannot be decoded.
    """
    p = Path(path)
    name = label or p.name
    size = p.stat().st_size
    with p.open("rb") as handle:
        head = handle.read(PROBE_HEAD_BYTES)
    kind = au.detect_format(head, name)
    if kind == "wav":
        return _probe_wav(head, size)
    if kind == "flac":
        return _probe_flac(head, size)
    if kind == "ogg":
        return _probe_ogg(p, head, size)
    if kind == "mp3":
        return _probe_mp3(head, size)
    raise UnsupportedFormat(
        f"unsupported or corrupt audio file: {name} "
        f"(supported: {', '.join(au.SUPPORTED_FORMATS)})"
    )


def _probe_wav(head: bytes, size: int) -> AudioInfo:
    """Describe a RIFF/WAVE file from its header."""
    if len(head) < 12 or head[0:4] != b"RIFF" or head[8:12] != b"WAVE":
        raise UnreadableAudio("not a RIFF/WAVE file")
    pos = 12
    fmt: bytes | None = None
    data_size: int | None = None
    data_at = 0
    while pos + 8 <= len(head):
        chunk_id = head[pos : pos + 4]
        chunk_size = int.from_bytes(head[pos + 4 : pos + 8], "little")
        if chunk_id == b"fmt ":
            fmt = head[pos + 8 : pos + 8 + chunk_size]
        elif chunk_id == b"data":
            data_size = chunk_size
            data_at = pos + 8
            break
        pos += 8 + chunk_size + (chunk_size & 1)
    if fmt is None or len(fmt) < 16:
        raise UnreadableAudio("the WAVE file has no format chunk")

    tag = int.from_bytes(fmt[0:2], "little")
    channels = int.from_bytes(fmt[2:4], "little")
    sample_rate = int.from_bytes(fmt[4:8], "little")
    bits = int.from_bytes(fmt[14:16], "little")
    if tag == 0xFFFE and len(fmt) >= 26:
        tag = int.from_bytes(fmt[24:26], "little")
    if channels <= 0 or sample_rate <= 0:
        raise UnreadableAudio("the WAVE file has no usable header")

    width = bits // 8 if bits in (8, 16, 24, 32, 64) else 4
    block_align = channels * max(1, width)
    frames: int | None = None
    truncated = False
    if data_size is not None:
        frames = data_size // block_align
        available = size - data_at
        truncated = data_size > max(0, available)
    duration = frames / sample_rate if frames is not None else None
    return AudioInfo(
        format="wav",
        sample_rate=sample_rate,
        channels=channels,
        frames=frames,
        bit_depth=bits or None,
        duration=duration,
        truncated=truncated,
        exact=data_size is not None,
    )


def _probe_flac(head: bytes, size: int) -> AudioInfo:
    """Describe a FLAC file from its STREAMINFO block."""
    if len(head) < 26 or head[0:4] != b"fLaC":
        raise UnreadableAudio("not a FLAC file")
    if (head[4] & 0x7F) != 0:
        raise UnreadableAudio("the FLAC file has no STREAMINFO block")
    packed = int.from_bytes(head[18:26], "big")
    sample_rate = packed >> 44
    channels = ((packed >> 41) & 0x7) + 1
    bits = ((packed >> 36) & 0x1F) + 1
    total = packed & 0xFFFFFFFFF
    if sample_rate <= 0:
        raise UnreadableAudio("the FLAC file has no usable header")
    frames = total if total > 0 else None
    duration = frames / sample_rate if frames else None
    return AudioInfo(
        format="flac",
        sample_rate=sample_rate,
        channels=channels,
        frames=frames,
        bit_depth=bits,
        duration=duration,
        truncated=False,
        exact=frames is not None,
    )


def _probe_ogg(path: Path, head: bytes, size: int) -> AudioInfo:
    """Describe an Ogg stream from its first and its last page.

    The first page carries the codec header; the last page carries the
    granule position, which is the length of the stream.  Both are read
    with a seek, so the cost does not depend on the size of the file.
    """
    if len(head) < 27 or head[0:4] != b"OggS":
        raise UnreadableAudio("not an Ogg file")
    segments = head[26]
    start = 27 + segments
    payload = head[start : start + 64]
    sample_rate = 0
    channels = 0
    preskip = 0
    if payload[:7] == b"\x01vorbis":
        if len(payload) < 16:
            raise UnreadableAudio("the Ogg file has no codec header")
        channels = payload[11]
        sample_rate = int.from_bytes(payload[12:16], "little")
    elif payload[:8] == b"OpusHead":
        if len(payload) < 19:
            raise UnreadableAudio("the Ogg file has no codec header")
        channels = payload[9]
        preskip = int.from_bytes(payload[10:12], "little")
        sample_rate = int.from_bytes(payload[12:16], "little")
    else:
        raise UnreadableAudio("the Ogg file has no known codec header")
    if channels <= 0:
        raise UnreadableAudio("the Ogg file has no usable header")

    granule = _last_ogg_granule(path, size)
    frames: int | None = None
    if granule is not None and granule > preskip:
        frames = int(granule) - preskip
    duration = frames / sample_rate if frames and sample_rate else None
    return AudioInfo(
        format="ogg",
        sample_rate=sample_rate,
        channels=channels,
        frames=frames,
        bit_depth=None,
        duration=duration,
        truncated=False,
        exact=frames is not None,
    )


def _last_ogg_granule(path: Path, size: int) -> int | None:
    """The granule position of the last Ogg page, or None."""
    if size <= 0:
        return None
    span = min(PROBE_TAIL_BYTES, size)
    with path.open("rb") as handle:
        handle.seek(size - span)
        tail = handle.read(span)
    at = tail.rfind(b"OggS")
    if at < 0 or at + 14 > len(tail):
        return None
    return int.from_bytes(tail[at + 6 : at + 14], "little", signed=True)


#: Bit rates of an MPEG-1 layer III frame, in kbit/s.
_MP3_BITRATES_V1 = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0)
#: Bit rates of an MPEG-2/2.5 layer III frame, in kbit/s.
_MP3_BITRATES_V2 = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0)
#: Sample rates of MPEG-1, in Hz.
_MP3_RATES_V1 = (44100, 48000, 32000, 0)
#: Sample rates of MPEG-2, in Hz.
_MP3_RATES_V2 = (22050, 24000, 16000, 0)


def _probe_mp3(head: bytes, size: int) -> AudioInfo:
    """Describe an MPEG audio stream from its first frame header.

    The length of a variable bit rate stream is not in the header, so the
    frame count is an estimate and is marked as such.
    """
    at = -1
    for index in range(len(head) - 1):
        if head[index] == 0xFF and (head[index + 1] & 0xE0) == 0xE0:
            at = index
            break
    if at < 0 or at + 4 > len(head):
        raise UnreadableAudio("no MPEG frame header found")
    version = (head[at + 1] >> 3) & 0x03
    layer = (head[at + 1] >> 1) & 0x03
    bitrate_index = (head[at + 2] >> 4) & 0x0F
    rate_index = (head[at + 2] >> 2) & 0x03
    padding = (head[at + 2] >> 1) & 0x01
    mode = (head[at + 3] >> 6) & 0x03
    if version == 1 or layer == 0 or bitrate_index in (0, 15) or rate_index == 3:
        raise UnreadableAudio("the MPEG frame header is not usable")
    rates = _MP3_RATES_V1 if version == 3 else _MP3_RATES_V2
    bitrates = _MP3_BITRATES_V1 if version == 3 else _MP3_BITRATES_V2
    sample_rate = rates[rate_index]
    bitrate = bitrates[bitrate_index] * 1000
    if sample_rate <= 0 or bitrate <= 0:
        raise UnreadableAudio("the MPEG frame header is not usable")
    channels = 1 if mode == 3 else 2
    frame_length = (144 * bitrate) // sample_rate + padding
    frames = None
    if frame_length > 0 and size > at:
        frames = max(0, (size - at) // frame_length)
    duration = frames / sample_rate if frames else None
    return AudioInfo(
        format="mp3",
        sample_rate=sample_rate,
        channels=channels,
        frames=frames,
        bit_depth=None,
        duration=duration,
        truncated=False,
        exact=False,
    )


# ---------------------------------------------------------------------------
# the asset record
# ---------------------------------------------------------------------------
@dataclass
class Asset:
    """One file held by the store."""

    asset_id: str
    sample_rate: int
    channels: int
    frames: int
    name: str
    format: str
    path: Path
    created: float = field(default_factory=time.time)
    bit_depth: int = DEFAULT_BIT_DEPTH
    data: np.ndarray | None = None
    bytes: int = 0
    truncated: bool = False
    declared_frames: int | None = None

    @property
    def duration(self) -> float:
        """Length of the asset in seconds."""
        return self.frames / self.sample_rate if self.sample_rate else 0.0

    @property
    def resident(self) -> bool:
        """True while the decoded buffer is still held in memory."""
        return self.data is not None

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe summary of the asset."""
        return {
            "asset_id": self.asset_id,
            "name": self.name,
            "format": self.format,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "frames": self.frames,
            "duration": self.duration,
            "created": self.created,
            "bit_depth": self.bit_depth,
            "resident": self.resident,
            "bytes": self.bytes,
            "truncated": self.truncated,
            "declared_frames": self.declared_frames,
            "url": f"/api/audio/{self.asset_id}",
        }


# ---------------------------------------------------------------------------
# the store
# ---------------------------------------------------------------------------
class AssetStore:
    """A thread-safe LRU store of audio assets.

    The store owns three budgets: the number of assets it tracks, the bytes
    those assets occupy in the asset directory and the decoded audio it
    holds in memory.  Every budget is a hard limit; when one is reached the
    least recently used asset is evicted and its file is deleted.
    """

    def __init__(
        self,
        root: str | Path,
        max_assets: int = DEFAULT_MAX_ASSETS,
        bit_depth: int = DEFAULT_BIT_DEPTH,
        max_asset_bytes: int = MAX_ASSET_BYTES,
        max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
        max_resident_bytes: int = DEFAULT_MAX_RESIDENT_BYTES,
    ) -> None:
        self.root = Path(root)
        self.audio_dir = self.root / "audio"
        self.max_assets = max(1, int(max_assets))
        self.bit_depth = int(bit_depth)
        self.max_asset_bytes = int(max_asset_bytes)
        self.max_total_bytes = int(max_total_bytes)
        self.max_resident_bytes = int(max_resident_bytes)
        self._items: "OrderedDict[str, Asset]" = OrderedDict()
        self._lock = threading.RLock()
        self._current: str | None = None
        self._manifest_path = self.root / MANIFEST_NAME
        self._started = time.time()
        self._shared = False
        self.recover()

    # -- ids ------------------------------------------------------------------
    def _new_id(self, prefix: str) -> str:
        stem = uuid.uuid4().hex[:12]
        return f"{prefix}_{stem}" if prefix else stem

    def _final_path(self, asset_id: str) -> Path:
        """The path of the WAV of an asset, inside the asset directory."""
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", asset_id):
            raise ValueError(f"not a valid asset id: {asset_id!r}")
        path = self.audio_dir / f"{asset_id}.wav"
        if path.resolve().parent != self.audio_dir.resolve():
            raise ValueError("the asset would be written outside the asset directory")
        return path

    # -- core operations ------------------------------------------------------
    def put(
        self,
        data: np.ndarray,
        sr: int,
        ch: int | None = None,
        name: str = "untitled",
        fmt: str = "wav",
        prefix: str = "as",
        asset_id: str | None = None,
        current: bool = True,
    ) -> str:
        """Store a decoded buffer and return its asset id.

        ``data`` is float64 ``(n_samples, n_channels)``; ``sr`` is the sample
        rate, ``ch`` the channel count (taken from ``data`` when omitted),
        ``name`` the original file name and ``fmt`` the original container.

        The buffer is written to the asset directory through a temporary
        name and renamed into place, so a half written file never survives a
        failure.  The decoded buffer stays in memory only while the resident
        budget allows it.

        ``current`` is ``False`` for an asset that is not what the session
        works on — a comparison, for instance.
        """
        arr = np.asarray(data, dtype=np.float64)
        if arr.ndim == 1:
            arr = arr[:, None]
        if arr.ndim != 2:
            raise ValueError("audio must be 1-D or 2-D")
        sample_rate = int(sr)
        if sample_rate <= 0:
            raise ValueError("sample rate must be positive")
        channels = int(ch) if ch else int(arr.shape[1])
        if channels != arr.shape[1]:
            arr = _fit_channels(arr, channels)

        aid = asset_id or self._new_id(prefix)
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        target = self._final_path(aid)
        self._write_wav(target, arr, sample_rate, self.bit_depth)
        size = target.stat().st_size
        if size > self.max_asset_bytes:
            _unlink(target)
            raise AssetTooLarge(
                f"the file is {size} bytes; the store accepts at most "
                f"{self.max_asset_bytes} bytes"
            )

        asset = Asset(
            asset_id=aid,
            sample_rate=sample_rate,
            channels=channels,
            frames=int(arr.shape[0]),
            name=sanitise_name(name),
            format=str(fmt or "wav"),
            path=target,
            bit_depth=self.bit_depth,
            data=arr,
            bytes=size,
        )
        with self._lock:
            self._items[aid] = asset
            self._items.move_to_end(aid)
            if current:
                self._current = aid
            self._trim()
            self._save_manifest()
        return aid

    def accept(
        self,
        source: str | Path | bytes,
        name: str,
        fmt: str | None = None,
        prefix: str = "up",
    ) -> str:
        """Take an uploaded file into the store and return its asset id.

        ``source`` is the path of the uploaded file or its bytes.  The name
        is checked (a name that is a path is refused), the size is checked
        against ``max_asset_bytes`` *before* the file is read, the header is
        read to describe the file and only then is the audio decoded.

        The uploaded file becomes the current asset.

        Raises :class:`UnsafeName`, :class:`AssetTooLarge` or
        :class:`UnsupportedFormat`.
        """
        safe = check_name(name)
        temp: Path | None = None
        if isinstance(source, (bytes, bytearray, memoryview)):
            payload = bytes(source)
            if len(payload) > self.max_asset_bytes:
                raise AssetTooLarge(
                    f"the file is {len(payload)} bytes; the store accepts at most "
                    f"{self.max_asset_bytes} bytes"
                )
            if not payload:
                raise UnreadableAudio("the uploaded file is empty")
            handle = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            handle.write(payload)
            handle.close()
            temp = Path(handle.name)
            path = temp
        else:
            path = Path(source)
            if not path.is_file():
                raise UnsupportedFormat(f"the uploaded file is missing: {safe}")
            size = path.stat().st_size
            if size > self.max_asset_bytes:
                raise AssetTooLarge(
                    f"the file is {size} bytes; the store accepts at most "
                    f"{self.max_asset_bytes} bytes"
                )
            if size == 0:
                raise UnreadableAudio("the uploaded file is empty")
        try:
            info = probe(path, safe)
            samples, sample_rate = _decode_file(path, safe)
        finally:
            if temp is not None:
                _unlink(temp)

        kind = str(fmt or info.format or "wav")
        aid = self._new_id(prefix)
        written = self._write_decoded(aid, samples, sample_rate, safe, kind)
        asset = Asset(
            asset_id=aid,
            sample_rate=int(sample_rate),
            channels=int(samples.shape[1]),
            frames=int(samples.shape[0]),
            name=safe,
            format=kind,
            path=written,
            bit_depth=self.bit_depth,
            data=samples,
            bytes=written.stat().st_size,
            truncated=bool(info.truncated),
            declared_frames=info.frames,
        )
        with self._lock:
            self._items[aid] = asset
            self._items.move_to_end(aid)
            self._current = aid
            self._trim()
            self._save_manifest()
        return aid

    def describe(
        self,
        path: str | Path,
        name: str | None = None,
        fmt: str | None = None,
        prefix: str = "up",
    ) -> str:
        """Register a file the store already holds, without decoding it.

        The store keeps the measurements of the file and nothing else: no
        sample of it is read, so a described file costs a few hundred bytes
        of memory.  Only files inside the asset directory can be described,
        because the store serves what it tracks.
        """
        p = Path(path).resolve()
        if p.parent != self.audio_dir.resolve():
            raise ValueError("only files in the asset directory can be described")
        size = p.stat().st_size
        if size > self.max_asset_bytes:
            raise AssetTooLarge(
                f"the file is {size} bytes; the store accepts at most "
                f"{self.max_asset_bytes} bytes"
            )
        info = probe(p)
        aid = self._new_id(prefix)
        asset = Asset(
            asset_id=aid,
            sample_rate=int(info.sample_rate),
            channels=int(info.channels),
            frames=int(info.frames or 0),
            name=sanitise_name(name or p.stem),
            format=str(fmt or info.format),
            path=p,
            bit_depth=int(info.bit_depth or self.bit_depth),
            data=None,
            bytes=size,
            truncated=bool(info.truncated),
            declared_frames=info.frames,
        )
        with self._lock:
            self._items[aid] = asset
            self._items.move_to_end(aid)
            self._trim()
            self._save_manifest()
        return aid

    def get(self, asset_id: str) -> Asset:
        """Return the asset, reloading the samples from disk if needed.

        The samples are read without the lock held, so a slow disk does not
        stop the rest of the server.

        Raises ``KeyError`` when the id is unknown.
        """
        with self._lock:
            asset = self._items.get(asset_id)
            if asset is None:
                raise KeyError(asset_id)
            self._items.move_to_end(asset_id)
            if asset.data is not None:
                return asset
        data = self._read_back(asset)
        if data is not None:
            with self._lock:
                if asset.asset_id in self._items and asset.data is None:
                    asset.data = data
                    self._trim_resident(keep=asset_id)
        return asset

    def path(self, asset_id: str) -> Path:
        """Return the on-disk WAV path of an asset."""
        with self._lock:
            asset = self._items.get(asset_id)
            if asset is None:
                raise KeyError(asset_id)
            self._items.move_to_end(asset_id)
            return asset.path

    def delete(self, asset_id: str) -> bool:
        """Remove an asset and its file.  Returns False when unknown.

        When the asset that is deleted is the current one, the current
        becomes the most recently used of the assets that are left, or
        ``None`` when the store is empty.
        """
        with self._lock:
            asset = self._items.pop(asset_id, None)
            if asset is None:
                return False
            asset.data = None
            _unlink(asset.path)
            if self._current == asset_id:
                self._current = next(reversed(self._items), None)
            self._save_manifest()
            return True

    def list(self) -> list[dict[str, Any]]:
        """Summaries of every known asset, oldest first."""
        with self._lock:
            return [a.to_dict() for a in self._items.values()]

    def clear(self) -> None:
        """Drop every asset and delete every file."""
        with self._lock:
            for asset in self._items.values():
                asset.data = None
                _unlink(asset.path)
            self._items.clear()
            self._current = None
            self._save_manifest()

    def sweep(self, age_seconds: float = SWEEP_AGE_SECONDS) -> int:
        """Delete the files left behind by an earlier session.

        The store records what it owns in a manifest, so a file that is not
        tracked and not in the manifest belongs to a run that ended badly.
        Such a file is removed when it is older than ``age_seconds``; a
        temporary file (a write that was interrupted) is removed at once,
        because it can never be useful.

        A live server that shares the asset directory keeps its own files:
        the store does not touch a directory another instance owns.

        Returns the number of files removed.
        """
        if not self.audio_dir.is_dir():
            return 0
        if self._shared:
            return 0
        removed = 0
        cutoff = time.time() - float(age_seconds)
        with self._lock:
            keep = {a.path.name for a in self._items.values()}
            for path in sorted(self.audio_dir.glob("*")):
                if not path.is_file():
                    continue
                if path.name in keep:
                    continue
                try:
                    if path.suffix == PART_SUFFIX:
                        path.unlink()
                        removed += 1
                        continue
                    if path.stat().st_mtime > cutoff:
                        continue
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed

    # -- the current asset ----------------------------------------------------
    @property
    def current_asset_id(self) -> str | None:
        """The id of the asset the session is working on."""
        with self._lock:
            return self._current

    def set_current(self, asset_id: str | None) -> None:
        """Make an asset the current one (``None`` clears it)."""
        with self._lock:
            if asset_id is not None and asset_id not in self._items:
                raise KeyError(asset_id)
            self._current = asset_id
            self._save_manifest()

    # -- the state of the store ------------------------------------------------
    def stats(self) -> dict[str, Any]:
        """The numbers that describe the size of the store."""
        with self._lock:
            return {
                "assets": len(self._items),
                "max_assets": self.max_assets,
                "bytes": self._total_bytes(),
                "max_total_bytes": self.max_total_bytes,
                "resident_bytes": self._resident_bytes(),
                "max_resident_bytes": self.max_resident_bytes,
                "max_asset_bytes": self.max_asset_bytes,
                "current": self._current,
            }

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def __contains__(self, asset_id: object) -> bool:
        with self._lock:
            return asset_id in self._items

    # -- internals ------------------------------------------------------------
    def _read_back(self, asset: Asset) -> np.ndarray | None:
        try:
            data, _sr = au.decode_upload(asset.path)
        except Exception:
            return None
        return data

    def _write_decoded(
        self,
        aid: str,
        samples: np.ndarray,
        sample_rate: int,
        name: str,
        kind: str,
    ) -> Path:
        """Write decoded audio into the asset directory and return its path."""
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        target = self._final_path(aid)
        self._write_wav(target, np.asarray(samples, dtype=np.float64), sample_rate, self.bit_depth)
        return target

    def _write_wav(self, target: Path, arr: np.ndarray, sample_rate: int, bit_depth: int) -> None:
        """Write a WAV through a temporary name and rename it into place.

        The temporary file is removed when anything fails, so a file that was
        only half written never survives.
        """
        part = target.with_name(target.name + PART_SUFFIX)
        try:
            with part.open("wb") as handle:
                _write_pcm_wav(handle, arr, sample_rate, bit_depth)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(part, target)
        except BaseException:
            _unlink(part)
            raise

    def _total_bytes(self) -> int:
        total = 0
        for asset in self._items.values():
            total += int(asset.bytes)
        return total

    def _resident_bytes(self) -> int:
        total = 0
        for asset in self._items.values():
            if asset.data is not None:
                total += int(asset.data.nbytes)
        return total

    def _drop_buffer(self, asset: Asset) -> None:
        asset.data = None

    def _trim_resident(self, keep: str | None = None) -> None:
        """Drop decoded buffers until the resident budget is met.

        The buffer of ``keep`` is never dropped: it is the one the caller
        asked for.  A single buffer larger than the budget is therefore
        allowed to exceed it.
        """
        if self._resident_bytes() <= self.max_resident_bytes:
            return
        for asset in list(self._items.values()):
            if asset.asset_id == keep:
                continue
            if asset.data is None:
                continue
            self._drop_buffer(asset)
            if self._resident_bytes() <= self.max_resident_bytes:
                return

    def _trim(self) -> None:
        """Drop the least recently used assets until every budget is met."""
        self._trim_resident()
        while len(self._items) > self.max_assets or self._total_bytes() > self.max_total_bytes:
            victim = self._oldest(exclude=self._current)
            if victim is None:
                victim = self._oldest(exclude=None)
                if victim is None:
                    return
            self._evict(victim)

    def _oldest(self, exclude: str | None) -> str | None:
        for asset_id in self._items:
            if asset_id != exclude:
                return asset_id
        return None

    def _evict(self, asset_id: str) -> None:
        asset = self._items.pop(asset_id, None)
        if asset is None:
            return
        self._drop_buffer(asset)
        _unlink(asset.path)
        if self._current == asset_id:
            self._current = next(reversed(self._items), None)

    # -- the manifest ----------------------------------------------------------
    def _manifest_payload(self) -> dict[str, Any]:
        return {
            "version": 1,
            "pid": os.getpid(),
            "started": self._started,
            "current": self._current,
            "assets": {
                aid: {
                    "name": asset.name,
                    "format": asset.format,
                    "sample_rate": asset.sample_rate,
                    "channels": asset.channels,
                    "frames": asset.frames,
                    "bit_depth": asset.bit_depth,
                    "created": asset.created,
                    "bytes": asset.bytes,
                    "truncated": asset.truncated,
                    "declared_frames": asset.declared_frames,
                    "file": asset.path.name,
                }
                for aid, asset in self._items.items()
            },
        }

    def _save_manifest(self) -> None:
        """Record what the store owns, so a restart can take it back."""
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            part = self._manifest_path.with_name(self._manifest_path.name + PART_SUFFIX)
            part.write_text(
                json.dumps(self._manifest_payload(), indent=1, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(part, self._manifest_path)
        except OSError:
            pass

    def recover(self) -> int:
        """Take back the assets of an earlier run of the server.

        The store starts empty on every server start, so without this the
        files of the previous session would be orphans: on disk, but owned by
        nobody.  The manifest says which files were the store's; they are
        adopted again (described, not decoded) and the current asset of the
        earlier session becomes the current asset again.

        A directory that a live server still owns is left alone.

        Returns the number of assets that were taken back.
        """
        if not self._manifest_path.is_file():
            return 0
        try:
            payload = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        if not isinstance(payload, dict):
            return 0
        owner = int(payload.get("pid") or 0)
        if owner and owner != os.getpid() and _process_alive(owner):
            # another server is using this directory
            self._shared = True
            return 0

        assets = payload.get("assets")
        if not isinstance(assets, dict):
            return 0
        taken = 0
        for asset_id, record in assets.items():
            if not isinstance(record, dict):
                continue
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(asset_id)):
                continue
            file_name = str(record.get("file") or "")
            if not file_name or not file_name.endswith(".wav"):
                continue
            path = self.audio_dir / file_name
            if not path.is_file():
                continue
            try:
                size = path.stat().st_size
                if size > self.max_asset_bytes:
                    _unlink(path)
                    continue
                info = probe(path)
            except (OSError, UnsupportedFormat):
                _unlink(path)
                continue
            asset = Asset(
                asset_id=str(asset_id),
                sample_rate=int(info.sample_rate),
                channels=int(info.channels),
                frames=int(info.frames or 0),
                name=sanitise_name(record.get("name") or path.stem),
                format=str(record.get("format") or info.format),
                path=path,
                bit_depth=int(info.bit_depth or self.bit_depth),
                data=None,
                bytes=size,
                truncated=bool(info.truncated),
                declared_frames=info.frames,
            )
            with self._lock:
                self._items[str(asset_id)] = asset
                self._items.move_to_end(str(asset_id))
            taken += 1
        with self._lock:
            self._trim()
            current = payload.get("current")
            self._current = (
                str(current) if current in self._items else next(reversed(self._items), None)
            )
            self._save_manifest()
        return taken


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _unlink(path: Path) -> None:
    """Delete a file, ignoring the errors of a file that is not there."""
    try:
        path.unlink()
    except OSError:
        pass


def _process_alive(pid: int) -> bool:
    """True when a process with that id is running."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _decode_file(path: Path, name: str) -> tuple[np.ndarray, int]:
    """Decode an audio file, turning a failure into a clear error."""
    try:
        return au.decode_upload(path)
    except au.AudioDecodeError as exc:
        raise UnreadableAudio(str(exc).replace(path.name, name)) from exc
    except Exception as exc:  # pragma: no cover - defensive
        raise UnreadableAudio(f"cannot decode the audio: {exc}") from exc


def _write_pcm_wav(handle: Any, arr: np.ndarray, sample_rate: int, bit_depth: int) -> None:
    """Write a PCM WAV to an open file, one block at a time.

    The bytes are the bytes :func:`aurserver.audio.encode_wav` writes; the
    difference is that the file is written in blocks, so the whole encoded
    file never has to be in memory at once.
    """
    if bit_depth not in (16, 24, 32):
        raise ValueError(f"unsupported bit depth {bit_depth}; use 16, 24 or 32")
    data = np.ascontiguousarray(arr).ravel()
    channels = int(arr.shape[1])
    frames = int(arr.shape[0])
    block_align = channels * bit_depth // 8
    byte_rate = int(sample_rate) * block_align
    data_size = data.size * (bit_depth // 8)
    handle.write(
        struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF",
            36 + data_size,
            b"WAVE",
            b"fmt ",
            16,
            1,  # PCM
            channels,
            int(sample_rate),
            byte_rate,
            block_align,
            bit_depth,
            b"data",
            data_size,
        )
    )
    step = 1 << 16
    for start in range(0, data.size, step):
        piece = data[start : start + step]
        if bit_depth == 16:
            ints = np.clip(np.round(piece * 32767.0), -32768, 32767).astype(np.int16)
            handle.write(ints.tobytes())
        elif bit_depth == 24:
            ints = np.clip(np.round(piece * 8388607.0), -8388608, 8388607).astype(np.int64)
            packed = (ints & 0xFFFFFF).astype(np.uint32)
            triplets = np.empty((packed.size, 3), dtype=np.uint8)
            triplets[:, 0] = packed & 0xFF
            triplets[:, 1] = (packed >> 8) & 0xFF
            triplets[:, 2] = (packed >> 16) & 0xFF
            handle.write(triplets.tobytes())
        else:
            ints = np.clip(
                np.round(piece * 2147483647.0), -2147483648, 2147483647
            ).astype(np.int32)
            handle.write(ints.tobytes())


def _fit_channels(arr: np.ndarray, channels: int) -> np.ndarray:
    """Force an array to a given channel count (pad with zeros / drop extras)."""
    have = arr.shape[1]
    if have == channels:
        return arr
    if have > channels:
        return np.ascontiguousarray(arr[:, :channels])
    pad = np.zeros((arr.shape[0], channels - have), dtype=np.float64)
    return np.ascontiguousarray(np.concatenate([arr, pad], axis=1))
