"""
aurserver.audio — decoding, encoding and analysis helpers for the Auralis server.

Everything in this module works on plain numpy arrays shaped ``(n_samples,
n_channels)`` in float64, which is the shape the DSP core uses.  The wire
format is WAV; the container formats we can *read* are wav / flac / ogg / mp3.

The analysis helpers produce the payload for the composite view: a
downsampled min/max waveform envelope and a quantised spectrogram matrix.

The module also owns the **two render paths** of the product: the *full*
profile the file render uses and the *fast* profile the live preview uses.
See :data:`PROFILE_FULL` and :data:`PROFILE_FAST`.

The two processing modes
------------------------
The profile and the **mode** are two different things, and the server keeps
them apart:

* the *profile* says **which configuration** of the cost parameters a path
  uses: the file gets the full one, the preview the cheap one;
* the *mode* says **which of the two configurations of a module** the path
  runs: the one that fits the deadline of a real time host, or the best one
  the module has.

The mode is a property of the chain (:attr:`aurcore.api.Engine.processing_mode`)
and every module of the chain is given it before the chain runs.  The rule
that says which mode a path uses is here, in one place, and it is explicit:

    the preview is real time, the file render is not.

A preview is played while the user waits for it, so every module of it has to
keep inside the budget of a real time block.  A file render has no deadline,
so its modules may use the window they like best -- and they have to declare
the look ahead that costs, because the host has to be told how late the audio
is.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path
from typing import Any

import numpy as np

__all__ = [
    "AudioDecodeError",
    "FAST_PARAMETERS",
    "PATH_FILE",
    "PATH_PREVIEW",
    "PATH_PROCESSING_MODE",
    "PROCESSING_MODE_OFFLINE",
    "PROCESSING_MODE_REALTIME",
    "PROFILE_FAST",
    "PROFILE_FULL",
    "SUPPORTED_FORMATS",
    "analyse",
    "apply_processing_mode",
    "apply_profile",
    "decode_upload",
    "detect_format",
    "encode_wav",
    "processing_mode_for",
    "profile_label",
    "resample_to",
]

#: Container formats the upload endpoint accepts.
SUPPORTED_FORMATS = ("wav", "flac", "ogg", "mp3")

#: Number of min/max buckets in the waveform envelope.
WAVEFORM_BUCKETS = 2000

#: Upper bound on the rows (frequency bins) of the spectrogram matrix.
SPECTRUM_BINS = 512

#: Upper bound on the columns (time frames) of the spectrogram matrix.
SPECTRUM_FRAMES = 800

#: FFT size used for the spectrogram.
FFT_SIZE = 2048

#: Analysis floor in dBFS (also the zero point of the quantised matrix).
DEFAULT_MIN_DB = -120.0

#: Analysis ceiling in dBFS (also the full-scale point of the matrix).
DEFAULT_MAX_DB = 0.0


# ---------------------------------------------------------------------------
# the two render paths
# ---------------------------------------------------------------------------
#: The profile the file render uses: the chain exactly as the user set it.
#: A file has no deadline, so the file gets the full configuration — the
#: finer transform, the multi-resolution analysis and the look-ahead that
#: comes with them.  Nothing in the product may change this profile: what a
#: user gets in a file is the truth.
PROFILE_FULL = "full"

#: The profile the live preview uses: the same chain, computed with the
#: cheap setting of every parameter that only decides *how* the work is
#: done.  The artistic parameters are never touched, so the preview does
#: the same work as the file; it only does it with a smaller transform.
#: The preview has a deadline — the user is moving a knob — so it trades
#: the fineness of the analysis for the latency.
PROFILE_FAST = "fast"

#: The cheap setting of the parameters that decide how a module does its
#: work, per module.  Every entry is a parameter the product already
#: publishes in the "Algorithm" or the "Quality" group, and every entry is
#: documented as the cheaper of two ways of doing the same job:
#:
#: ``AUR_SPECTRALDENOISE``
#:     ``Quality`` is documented as "A is the fastest and least accurate",
#:     and ``Multi Resolution`` analyses the signal with more than one
#:     window size.  Measured on the development machine the module costs
#:     1696 transform equivalents for a 1024 sample block in the full
#:     configuration and 536 in this one.
#: ``AUR_DECLICK``
#:     ``Algorithm`` offers "Low Latency" beside the spectral ones, and it
#:     is much cheaper: measured 47 transform equivalents for a 1024 sample
#:     block against 116.  It is **not** in the table, because it is not the
#:     same repair with a smaller window: it is a repair in the time
#:     domain.  Measured against the file render on the corpus, it moves
#:     the preview by up to 5.80 dB RMS (the files with clicks and rumble),
#:     which is more than a preview may differ from the file it stands in
#:     for.  The cost of the preview is worth less than that.
#: ``AUR_SPECTRALREPAIR``
#:     ``Multi Resolution`` analyses the region with more than one window
#:     size; without it the repair runs once instead of twice.  Measured:
#:     30.0 transform equivalents for a 64 sample block against 15.1.
#:
#: A parameter that is not in this table is never moved: the fast profile
#: changes nothing it cannot pay for, and nothing it cannot run.
FAST_PARAMETERS: dict[str, dict[str, Any]] = {
    "AUR_SPECTRALDENOISE": {"Quality": "A", "Multi Resolution": False},
    "AUR_SPECTRALREPAIR": {"Multi Resolution": False},
}


def _plain(value: Any) -> Any:
    """Turn a parameter value into something JSON can carry."""
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    return str(value)


#: The verdicts of the smoke test, per module and configuration.  A verdict
#: does not change while the process runs, so it is asked once.
_ACCEPTS: dict[tuple, bool] = {}


def _accepts(
    module: Any,
    overrides: dict[str, Any],
    sample_rate: int,
    channels: int,
) -> bool:
    """True when the module runs in the cheap configuration.

    A cheap mode that does not run must not be used: the preview would
    stream dry audio and the fast path would be a lie.  The check is done
    on a throw-away instance of the module, so the state of the module that
    is really in the chain is not touched.
    """
    key = (type(module), tuple(sorted(overrides.items())))
    cached = _ACCEPTS.get(key)
    if cached is not None:
        return cached
    verdict = False
    try:
        try:
            probe = type(module)(sample_rate, channels)
        except Exception:
            probe = type(module)()
        for name, value in overrides.items():
            if not probe.params.has(name):
                raise KeyError(name)
            probe.params.set(name, value)
        probe.process(np.zeros((256, max(1, int(channels))), dtype=np.float64))
        verdict = True
    except Exception:
        verdict = False
    _ACCEPTS[key] = verdict
    return verdict


def apply_profile(engine: Any, profile: str = PROFILE_FULL) -> dict[str, Any]:
    """Put an engine into the given profile and report what was changed.

    The **full** profile changes nothing: the engine keeps the configuration
    the user set and the report says so.  It is the profile of the file
    render, and the render must not be able to change it by accident.

    The **fast** profile moves every cost parameter of the chain onto its
    cheap setting and reports what it moved: for every parameter, the value
    the file gets and the value the preview gets.  That report is what the
    interface shows, so a user who hears the fast path can be told which
    knobs are on their cheap setting.

    A cheap mode that does not run is **not** used.  The module is asked
    first, on a throw-away instance; if it refuses, the parameter stays as
    the user set it and the report names the refusal.  A preview that
    cannot run the cheap configuration must say so rather than stream dry
    audio.

    Parameters
    ----------
    engine:
        The engine to put into the profile.  Its modules are changed in
        place, so the caller must not need the full configuration of the
        same engine afterwards.
    profile:
        :data:`PROFILE_FULL` or :data:`PROFILE_FAST`.

    Returns
    -------
    dict
        ``{"profile": ..., "changed": n, "modules": [...], "refused": [...]}``.
        Every entry of ``modules`` names the module, its index in the chain
        and the parameters that were moved; every entry of ``refused`` names
        a cheap configuration the module would not run.
    """
    if profile == PROFILE_FULL:
        return {
            "profile": PROFILE_FULL,
            "changed": 0,
            "modules": [],
            "refused": [],
        }
    if profile != PROFILE_FAST:
        raise ValueError(f"unknown profile {profile!r}")

    report: dict[str, Any] = {
        "profile": PROFILE_FAST,
        "changed": 0,
        "modules": [],
        "refused": [],
    }
    sample_rate = int(getattr(engine, "sample_rate", 48000) or 48000)
    channels = int(getattr(engine, "channels", 2) or 2)
    for index, step in enumerate(getattr(engine, "chain", []) or []):
        module = getattr(step, "module", None)
        if module is None:
            continue
        code = str(getattr(module, "code", ""))
        overrides = FAST_PARAMETERS.get(code)
        if not overrides:
            continue
        # the cheap configuration has to run before it is used
        wanted = {
            name: cheap
            for name, cheap in overrides.items()
            if _has_param(module, name)
        }
        if not wanted:
            continue
        if not _accepts(module, wanted, sample_rate, channels):
            report["refused"].append(
                {
                    "index": index,
                    "code": code,
                    "parameters": {k: _plain(v) for k, v in wanted.items()},
                    "reason": "the module does not run in this configuration",
                }
            )
            continue
        moved: list[dict[str, Any]] = []
        for name, cheap in wanted.items():
            try:
                current = module.params.get(name)
            except Exception:  # pragma: no cover - a module without params
                continue
            if current == cheap:
                continue
            try:
                module.params.set(name, cheap)
            except Exception:  # pragma: no cover - a value the module refuses
                continue
            moved.append(
                {
                    "name": name,
                    "file": _plain(current),
                    "preview": _plain(cheap),
                }
            )
        if moved:
            report["changed"] += len(moved)
            report["modules"].append(
                {"index": index, "code": code, "parameters": moved}
            )
    return report


def _has_param(module: Any, name: str) -> bool:
    """True when the module has the parameter."""
    try:
        return bool(module.params.has(name))
    except Exception:  # pragma: no cover - a module without params
        return False


def profile_label(profile: str) -> str:
    """The name the interface shows for a profile."""
    if profile == PROFILE_FAST:
        return "preview: fast path"
    if profile == PROFILE_FULL:
        return "preview: full path"
    return f"preview: {profile}"


# ---------------------------------------------------------------------------
# the two processing modes
# ---------------------------------------------------------------------------
#: The mode of a chain that is played while somebody waits for it: every
#: module of the chain has to keep inside the budget of a real time block.
PROCESSING_MODE_REALTIME = "realtime"

#: The mode of a chain that is rendered into a file: there is no deadline, so
#: a module may use the window it likes best -- and it has to declare the
#: look ahead that costs.
PROCESSING_MODE_OFFLINE = "offline"

#: The live preview: the user is moving a knob and waiting for the sound.
PATH_PREVIEW = "preview"

#: The file render: the result is written into a file and there is no
#: deadline at all.
PATH_FILE = "file"

#: THE RULE of the server, in one table: the preview is real time, the file
#: render is not.
#:
#: The rule is not implied by anything: it is written down here, and both
#: paths ask this table for the mode they work in.
PATH_PROCESSING_MODE: dict[str, str] = {
    PATH_PREVIEW: PROCESSING_MODE_REALTIME,
    PATH_FILE: PROCESSING_MODE_OFFLINE,
}


def processing_mode_for(path: str) -> str:
    """The processing mode of a render path.

    ``"preview"`` gives :data:`PROCESSING_MODE_REALTIME` and ``"file"``
    gives :data:`PROCESSING_MODE_OFFLINE`.  An unknown path is a refusal:
    a path nobody named cannot be given a mode.
    """
    key = str(path).strip().lower()
    try:
        return PATH_PROCESSING_MODE[key]
    except KeyError:
        raise ValueError(
            f"unknown render path {path!r}; the paths are "
            f"{', '.join(sorted(PATH_PROCESSING_MODE))}"
        ) from None


def apply_processing_mode(engine: Any, path: str) -> str:
    """Put an engine into the mode of a render path and report the mode.

    The engine hands the mode to every module of its chain before the chain
    runs, so a module of the preview works in the configuration that fits the
    deadline and a module of the file render works in the best one it has.
    """
    mode = processing_mode_for(path)
    try:
        engine.processing_mode = mode
    except Exception:  # pragma: no cover - an engine without the property
        return mode
    return mode


class AudioDecodeError(RuntimeError):
    """Raised when a file cannot be turned into samples."""


# ---------------------------------------------------------------------------
# decoding
# ---------------------------------------------------------------------------


def decode_upload(path: str | Path) -> tuple[np.ndarray, int]:
    """Decode an audio file into float64 samples.

    Returns ``(samples, sample_rate)`` where ``samples`` has shape
    ``(n_samples, n_channels)``.  ``libsndfile`` (through :mod:`soundfile`)
    is used first; if it cannot read the file we fall back to the standard
    library :mod:`wave` reader and finally to a small hand-rolled RIFF
    parser, which copes with ``WAVE_FORMAT_EXTENSIBLE`` headers that the
    standard library refuses.
    """
    p = Path(path)
    if not p.is_file():
        raise AudioDecodeError(f"file not found: {p.name}")

    data, sr = _decode_soundfile(p)
    if data is None:
        data, sr = _decode_wave_stdlib(p)
    if data is None:
        data, sr = _decode_riff(p)
    if data is None:
        raise AudioDecodeError(
            f"unsupported or corrupt audio file: {p.name} "
            f"(supported: {', '.join(SUPPORTED_FORMATS)})"
        )
    if data.shape[0] == 0:
        raise AudioDecodeError(f"audio file contains no samples: {p.name}")
    return data, sr


def _decode_soundfile(path: Path) -> tuple[np.ndarray | None, int]:
    """Read a file through libsndfile.  Returns ``(None, 0)`` on failure."""
    try:
        import soundfile as sf
    except Exception:
        return None, 0
    try:
        data, sr = sf.read(str(path), always_2d=True, dtype="float64")
    except Exception:
        return None, 0
    if data is None:
        return None, 0
    data = np.ascontiguousarray(
        np.atleast_2d(data.T).T if data.ndim == 1 else data, dtype=np.float64
    )
    if data.ndim == 1:
        data = data[:, None]
    return data, int(sr)


def _decode_wave_stdlib(path: Path) -> tuple[np.ndarray | None, int]:
    """Read a plain PCM WAV with the standard library :mod:`wave` module."""
    try:
        with wave.open(str(path), "rb") as w:
            channels = w.getnchannels()
            width = w.getsampwidth()
            sr = w.getframerate()
            frames = w.getnframes()
            raw = w.readframes(frames)
    except Exception:
        return None, 0
    try:
        samples = _pcm_to_float(raw, width, channels)
    except Exception:
        return None, 0
    return samples, int(sr)


def _decode_riff(path: Path) -> tuple[np.ndarray | None, int]:
    """Minimal RIFF/WAVE reader used as the last-resort fallback."""
    try:
        raw = path.read_bytes()
    except Exception:
        return None, 0
    if len(raw) < 12 or raw[0:4] != b"RIFF" or raw[8:12] != b"WAVE":
        return None, 0

    fmt: bytes | None = None
    data: bytes | None = None
    pos = 12
    while pos + 8 <= len(raw):
        cid = raw[pos : pos + 4]
        size = int.from_bytes(raw[pos + 4 : pos + 8], "little")
        body = raw[pos + 8 : pos + 8 + size]
        if cid == b"fmt ":
            fmt = body
        elif cid == b"data":
            data = body
        pos += 8 + size + (size & 1)
    if fmt is None or data is None or len(fmt) < 16:
        return None, 0

    audio_format = int.from_bytes(fmt[0:2], "little")
    channels = int.from_bytes(fmt[2:4], "little")
    sr = int.from_bytes(fmt[4:8], "little")
    bits = int.from_bytes(fmt[14:16], "little")
    if audio_format == 0xFFFE and len(fmt) >= 26:
        audio_format = int.from_bytes(fmt[24:26], "little")

    try:
        if audio_format == 3 and bits == 32:
            samples = np.frombuffer(data, dtype="<f4").astype(np.float64)
        elif audio_format == 3 and bits == 64:
            samples = np.frombuffer(data, dtype="<f8").astype(np.float64)
        elif audio_format == 1:
            samples = _pcm_to_float(data, bits // 8, channels)
        else:
            return None, 0
    except Exception:
        return None, 0
    if channels <= 0:
        return None, 0
    return samples.reshape(-1, channels), int(sr)


def _pcm_to_float(raw: bytes, width: int, channels: int) -> np.ndarray:
    """Convert interleaved integer PCM bytes to float64 in ``[-1, 1)``."""
    if width == 1:  # 8-bit WAV is unsigned
        arr = np.frombuffer(raw, dtype=np.uint8).astype(np.float64)
        arr = (arr - 128.0) / 128.0
    elif width == 2:
        arr = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    elif width == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        v = (
            b[:, 0].astype(np.int64)
            | (b[:, 1].astype(np.int64) << 8)
            | (b[:, 2].astype(np.int64) << 16)
        )
        v = np.where(v & 0x800000, v - 0x1000000, v)
        arr = v.astype(np.float64) / 8388608.0
    elif width == 4:
        arr = np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2147483648.0
    else:
        raise ValueError(f"unsupported sample width: {width} bytes")
    if channels <= 0:
        raise ValueError("no channels")
    return arr.reshape(-1, channels)


def detect_format(data: bytes = b"", filename: str = "") -> str:
    """Best-effort container detection from the magic bytes, else the suffix."""
    head = bytes(data[:16])
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"fLaC":
        return "flac"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:3] == b"ID3":
        return "mp3"
    if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        return "mp3"
    suffix = Path(filename or "").suffix.lower().lstrip(".")
    if suffix == "wave":
        return "wav"
    return suffix or "unknown"


# ---------------------------------------------------------------------------
# encoding
# ---------------------------------------------------------------------------


def encode_wav(x: np.ndarray, sr: int, bit_depth: int = 24) -> bytes:
    """Encode a float64 signal as an interleaved PCM WAV file.

    ``x`` has shape ``(n_samples, n_channels)``.  ``bit_depth`` must be 16,
    24 or 32; 32 means 32-bit *integer* PCM (not IEEE float).
    """
    arr = np.asarray(x, dtype=np.float64)
    if arr.ndim == 1:
        arr = arr[:, None]
    if arr.ndim != 2:
        raise ValueError("audio must be 1-D or 2-D")
    if bit_depth not in (16, 24, 32):
        raise ValueError(f"unsupported bit depth {bit_depth}; use 16, 24 or 32")

    interleaved = np.ascontiguousarray(arr).ravel()  # sample0ch0, sample0ch1, ...
    if bit_depth == 16:
        ints = np.clip(np.round(interleaved * 32767.0), -32768, 32767).astype(np.int16)
        pcm = ints.tobytes()
    elif bit_depth == 24:
        ints = np.clip(np.round(interleaved * 8388607.0), -8388608, 8388607).astype(np.int64)
        packed = (ints & 0xFFFFFF).astype(np.uint32)
        triplets = np.empty((packed.size, 3), dtype=np.uint8)
        triplets[:, 0] = packed & 0xFF
        triplets[:, 1] = (packed >> 8) & 0xFF
        triplets[:, 2] = (packed >> 16) & 0xFF
        pcm = triplets.tobytes()
    else:
        ints = np.clip(
            np.round(interleaved * 2147483647.0), -2147483648, 2147483647
        ).astype(np.int32)
        pcm = ints.tobytes()

    channels = int(arr.shape[1])
    frames = int(arr.shape[0])
    block_align = channels * bit_depth // 8
    byte_rate = int(sr) * block_align
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(pcm),
        b"WAVE",
        b"fmt ",
        16,
        1,  # PCM
        channels,
        int(sr),
        byte_rate,
        block_align,
        bit_depth,
        b"data",
        len(pcm),
    )
    return header + pcm


def resample_to(x: np.ndarray, sr: int, target_sr: int) -> np.ndarray:
    """Resample a signal to ``target_sr``.

    Uses :func:`scipy.signal.resample_poly` when SciPy is importable and
    falls back to linear interpolation otherwise.  The channel layout is
    preserved.
    """
    arr = np.asarray(x, dtype=np.float64)
    if arr.ndim == 1:
        arr = arr[:, None]
    src = int(sr)
    dst = int(target_sr)
    if src == dst or arr.shape[0] == 0 or dst <= 0:
        return arr
    out_len = int(round(arr.shape[0] * dst / src))
    out_len = max(1, out_len)
    try:
        from scipy.signal import resample_poly

        g = math.gcd(src, dst)
        return np.ascontiguousarray(
            resample_poly(arr, dst // g, src // g, axis=0), dtype=np.float64
        )
    except Exception:
        old = np.arange(arr.shape[0], dtype=np.float64)
        new = np.linspace(0.0, max(0.0, arr.shape[0] - 1.0), out_len)
        cols = [np.interp(new, old, arr[:, c]) for c in range(arr.shape[1])]
        return np.ascontiguousarray(np.stack(cols, axis=1), dtype=np.float64)


# ---------------------------------------------------------------------------
# analysis
# ---------------------------------------------------------------------------


def analyse(
    x: np.ndarray,
    sr: int,
    *,
    waveform_buckets: int = WAVEFORM_BUCKETS,
    max_bins: int = SPECTRUM_BINS,
    max_frames: int = SPECTRUM_FRAMES,
    min_db: float = DEFAULT_MIN_DB,
    max_db: float = DEFAULT_MAX_DB,
) -> dict[str, Any]:
    """Build the composite-view payload for one signal.

    The returned dictionary contains

    ``waveform``
        A list of ``[min, max]`` pairs, one per pixel bucket.
    ``spectrogram``
        A list of rows (frequency bands); every row holds one integer per
        time frame.  Values are quantised to ``uint8`` so the JSON payload
        stays small; the decibel value of a cell ``v`` is
        ``min_db + v * (max_db - min_db) / 255``.
    ``freq_bins`` / ``frames``
        The shape of the spectrogram matrix.
    ``max_db`` / ``min_db``
        The analysis range the quantised matrix is mapped onto.
    ``bin_hz`` / ``freq_min_hz`` / ``freq_max_hz``
        The frequency axis.  Row ``i`` is the band
        ``[i * bin_hz, (i + 1) * bin_hz)`` hertz and holds the loudest
        level the analysis found inside it; the bands tile the range from
        ``freq_min_hz`` to ``freq_max_hz`` exactly, so ``bin_hz`` is the
        true distance between two rows and a consumer that turns a row
        index into a frequency by multiplying it with ``bin_hz`` is right.
    """
    arr = np.asarray(x, dtype=np.float64)
    if arr.ndim == 1:
        arr = arr[:, None]
    if arr.ndim != 2:
        raise ValueError("audio must be 1-D or 2-D")
    n_samples = int(arr.shape[0])
    channels = int(arr.shape[1])
    sample_rate = int(sr)

    waveform, n_buckets = _waveform_envelope(arr, waveform_buckets)
    matrix, n_bins, n_frames, hop, bin_hz = _spectrogram(
        arr, sample_rate, max_bins=max_bins, max_frames=max_frames, min_db=min_db, max_db=max_db
    )

    duration = n_samples / sample_rate if sample_rate else 0.0
    span = max(1, n_frames)
    return {
        "waveform": waveform,
        "waveform_buckets": n_buckets,
        "spectrogram": matrix,
        "freq_bins": int(n_bins),
        "frames": int(n_frames),
        "max_db": float(max_db),
        "min_db": float(min_db),
        "sample_rate": sample_rate,
        "channels": channels,
        "audio_frames": n_samples,
        "duration": duration,
        "freq_min_hz": 0.0,
        "freq_max_hz": sample_rate / 2.0 if sample_rate else 0.0,
        "bin_hz": float(bin_hz),
        "fft_size": FFT_SIZE,
        "frame_hop_samples": int(hop),
        "frame_seconds": (hop / sample_rate) if sample_rate else 0.0,
        "spectrogram_encoding": "uint8",
        "spectrogram_source": "mono mix (channel mean)",
        "spectrogram_scale": "dB = min_db + value * (max_db - min_db) / 255",
        "spectrogram_rows": (
            "row i is the band [i * bin_hz, (i + 1) * bin_hz) Hz and holds "
            "the loudest level inside it"
        ),
    }


def _waveform_envelope(arr: np.ndarray, buckets_wanted: int) -> tuple[list[list[float]], int]:
    """Min/max envelope with one ``[min, max]`` pair per bucket."""
    n_samples = int(arr.shape[0])
    if n_samples == 0:
        return [], 0
    buckets = int(max(1, min(int(buckets_wanted), n_samples)))
    edges = np.linspace(0, n_samples, buckets + 1).astype(np.int64)
    starts = edges[:-1]
    lows = np.minimum.reduceat(arr, starts, axis=0)
    highs = np.maximum.reduceat(arr, starts, axis=0)
    lows = lows.min(axis=1) if lows.ndim > 1 else lows
    highs = highs.max(axis=1) if highs.ndim > 1 else highs
    envelope = [
        [round(float(lo), 6), round(float(hi), 6)] for lo, hi in zip(lows, highs)
    ]
    return envelope, buckets


def _row_bands(
    sample_rate: int, n_fft: int, n_fft_bins: int, n_rows: int
) -> tuple[np.ndarray, float]:
    """Assign every FFT bin to a row band; return the mapping and the spacing.

    The bands tile the range from 0 Hz to the Nyquist frequency: band ``i``
    covers ``[i * bin_hz, (i + 1) * bin_hz)`` and every band has the same
    width.  ``bin_hz * n_rows`` is therefore the Nyquist frequency and
    ``bin_hz`` is the true distance between two rows — the number a
    consumer needs to turn a row index into a frequency.

    Parameters
    ----------
    sample_rate:
        Sample rate in hertz.
    n_fft:
        Size of the FFT.
    n_fft_bins:
        Number of bins the FFT produced (``n_fft // 2 + 1``).
    n_rows:
        Number of rows the matrix shall have.

    Returns
    -------
    tuple
        ``(row_of_bin, bin_hz)`` — the band index of every FFT bin and the
        row spacing in hertz.
    """
    nyquist = sample_rate / 2.0
    bin_hz = nyquist / float(n_rows)
    index = np.arange(n_fft_bins, dtype=np.int64)
    frequencies = index * (sample_rate / float(n_fft))
    rows = (frequencies / bin_hz).astype(np.int64)
    # the Nyquist bin sits on the top edge of the last band and belongs to it
    return np.minimum(rows, n_rows - 1), bin_hz


def _spectrogram(
    arr: np.ndarray,
    sample_rate: int,
    *,
    max_bins: int,
    max_frames: int,
    min_db: float,
    max_db: float,
) -> tuple[list[list[int]], int, int, int, float]:
    """Quantised dB spectrogram matrix, shaped ``[band][frame]``.

    The rows are frequency *bands*, not FFT bins: row ``i`` covers
    ``[i * bin_hz, (i + 1) * bin_hz)`` hertz and holds the loudest level
    the analysis found inside that band.  The bands tile the range from
    0 Hz to the Nyquist frequency exactly, so the reported spacing is the
    real distance between two rows and no row is a copy of another.
    """
    n_samples = int(arr.shape[0])
    if n_samples == 0 or sample_rate <= 0:
        return [], 0, 0, 0, 0.0

    mono = arr.mean(axis=1) if arr.shape[1] > 1 else arr[:, 0]
    n_fft = FFT_SIZE
    if n_samples < n_fft:
        mono = np.pad(mono, (0, n_fft - n_samples))
    total = mono.shape[0]

    hop = max(1, n_fft // 4)
    available = 1 + (total - n_fft) // hop
    if available > max_frames and max_frames > 1:
        # Stretch the hop so that the matrix holds exactly max_frames columns.
        hop = max(1, (total - n_fft) // (max_frames - 1))
        available = 1 + (total - n_fft) // hop

    window = np.hanning(n_fft)
    view = np.lib.stride_tricks.sliding_window_view(mono, n_fft)[::hop]
    n_frames = int(view.shape[0])
    if n_frames == 0:
        return [], 0, 0, hop, 0.0

    spectrum = np.fft.rfft(view * window, axis=1)
    magnitude = np.abs(spectrum) / max(window.sum() / 2.0, 1e-9)
    decibels = 20.0 * np.log10(np.maximum(magnitude, 1e-12))
    decibels = np.clip(decibels, min_db, max_db).T  # -> (bins, frames)

    n_fft_bins = int(decibels.shape[0])
    # The Nyquist bin sits on the top edge of the last band and is folded
    # into it, so the number of rows never exceeds the number of bins below
    # the Nyquist frequency.
    n_rows = n_fft_bins - 1 if max_bins <= 0 else min(int(max_bins), n_fft_bins - 1)
    row_of_bin, bin_hz = _row_bands(sample_rate, n_fft, n_fft_bins, n_rows)
    # The bins of a band are contiguous, so the band maximum is a reduction
    # over the groups the mapping describes.
    starts = np.searchsorted(row_of_bin, np.arange(n_rows), side="left")
    decibels = np.maximum.reduceat(decibels, starts, axis=0)
    n_bins = int(decibels.shape[0])

    span = max(1e-9, max_db - min_db)
    quantised = np.round((decibels - min_db) * (255.0 / span))
    quantised = np.clip(quantised, 0, 255).astype(np.uint8)
    return quantised.tolist(), n_bins, n_frames, hop, bin_hz
