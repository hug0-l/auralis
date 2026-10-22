"""
corpus.build_corpus - synthesise the Auralis golden corpus.

The corpus is the reference material every module's regression test measures
against.  This script *synthesises* it: every clip is generated from a
documented recipe, so the repository carries no third-party audio at all and
every clip has an exactly known ground truth (impulse positions and
amplitudes, hum levels, noise floors, reverberation time, clip positions).

Properties of the generated set
-------------------------------
* 48 kHz, stereo, 24-bit PCM WAV, 2 s (3 s for ``clicks.wav``).
* Fully deterministic: a fixed seed and no wall-clock or environment input.
  Running the script twice produces byte-identical files.
* One normalisation rule for every clip (see :func:`_normalise`), applied
  once, after the components are mixed.  The gain that was applied is
  recorded in the manifest, so the level of every component in the file can
  be recomputed from the recipe.
* Licence ``CC0-1.0``: the material is generated, not copied.

Usage
-----
::

    python corpus/build_corpus.py            # write the clips + manifest
    python corpus/build_corpus.py --check    # verify the files on disk only

The manifest written here validates against ``corpus/manifest.schema.json``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import wave
from pathlib import Path
from typing import Any, Callable

import numpy as np

# The measurement helper lives next to this script; it is imported so that the
# ground truth written into the manifest is exactly what a test measures.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import measure  # noqa: E402  (the path has to be set up first)

__all__ = [
    "SAMPLE_RATE",
    "BIT_DEPTH",
    "CHANNELS",
    "SEED",
    "CORPUS_DIR",
    "AUDIO_DIR",
    "MANIFEST_PATH",
    "build_all",
    "main",
]

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
CORPUS_DIR: Path = Path(__file__).resolve().parent
AUDIO_DIR: Path = CORPUS_DIR / "audio" / "test"
MANIFEST_PATH: Path = CORPUS_DIR / "manifest.json"

#: Sample rate of every clip.
SAMPLE_RATE: int = 48_000
#: Bit depth of every clip (24-bit PCM).
BIT_DEPTH: int = 24
#: Channel count of every clip.
CHANNELS: int = 2
#: Seed of the random generator - the whole corpus is reproducible from it.
SEED: int = 20_260_201
#: Length of a standard clip in seconds.
DURATION_S: float = 2.0
#: Lowest level the manifest reports (the floor of the analyser in aurcore).
LEVEL_FLOOR_DB: float = -120.0
#: Licence of the synthesised material.
LICENCE: str = "CC0-1.0"
#: ``source_url`` of a synthesised clip.
SOURCE_URL: str = "synthesised"
#: ``source_credit`` of a synthesised clip.
SOURCE_CREDIT: str = "synthesised by corpus/build_corpus.py"

_EPS: float = 1e-12


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def _db_to_amp(db: float) -> float:
    """Amplitude (linear) of a level in dBFS."""
    return float(10.0 ** (float(db) / 20.0))


def _amp_to_db(value: float, floor: float = LEVEL_FLOOR_DB) -> float:
    """Level of an amplitude in dBFS, floored so the result is finite."""
    amplitude = abs(float(value))
    if amplitude <= _EPS:
        return float(floor)
    return max(float(floor), 20.0 * math.log10(amplitude))


def _time_axis(n: int, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Time axis of ``n`` samples, in seconds."""
    return np.arange(int(n), dtype=np.float64) / float(sample_rate)


def _tone(
    freq: float,
    n: int,
    amp: float = 1.0,
    sample_rate: int = SAMPLE_RATE,
    phase: float = 0.0,
) -> np.ndarray:
    """A sine of ``freq`` Hz, ``amp`` peak amplitude, ``n`` samples long."""
    t = _time_axis(n, sample_rate)
    return float(amp) * np.sin(2.0 * math.pi * float(freq) * t + float(phase))


def _smooth_band_mask(
    freqs: np.ndarray,
    low_hz: float,
    high_hz: float,
    skirt_hz: float = 0.0,
) -> np.ndarray:
    """A brick-wall band mask with raised-cosine skirts.

    The mask is ``1`` between ``low_hz`` and ``high_hz`` and falls to ``0``
    over ``skirt_hz`` on either side, which keeps the filtered noise free of
    the ringing a hard cut would produce.
    """
    mask = np.zeros_like(freqs)
    inside = (freqs >= low_hz) & (freqs <= high_hz)
    mask[inside] = 1.0
    skirt = float(skirt_hz)
    if skirt > 0.0:
        low = (freqs > low_hz - skirt) & (freqs < low_hz)
        high = (freqs > high_hz) & (freqs < high_hz + skirt)
        mask[low] = 0.5 * (1.0 + np.cos(np.pi * (freqs[low] - (low_hz - skirt)) / skirt))
        mask[high] = 0.5 * (1.0 + np.cos(np.pi * (freqs[high] - high_hz) / skirt))
    return mask


def _band_noise(
    n: int,
    low_hz: float,
    high_hz: float,
    rng: np.random.Generator,
    skirt_hz: float = 500.0,
) -> np.ndarray:
    """Noise band-limited to ``[low_hz, high_hz]`` with cosine skirts."""
    count = int(n)
    spectrum = np.fft.rfft(rng.standard_normal(count))
    freqs = np.fft.rfftfreq(count, 1.0 / float(SAMPLE_RATE))
    mask = _smooth_band_mask(freqs, low_hz, high_hz, skirt_hz)
    return np.fft.irfft(spectrum * mask, n=count)


def _pink_noise(n: int, rng: np.random.Generator) -> np.ndarray:
    """Pink noise (power density proportional to 1/f).

    The spectrum of white noise is weighted with ``1/sqrt(f)``, which leaves
    the *power* proportional to 1/f.
    """
    count = int(n)
    spectrum = np.fft.rfft(rng.standard_normal(count))
    freqs = np.fft.rfftfreq(count, 1.0 / float(SAMPLE_RATE))
    shape = np.zeros_like(freqs)
    nonzero = freqs > 0.0
    shape[nonzero] = 1.0 / np.sqrt(freqs[nonzero])
    return np.fft.irfft(spectrum * shape, n=count)


def _raised_cosine_envelope(n: int, start_s: float, end_s: float) -> np.ndarray:
    """A raised-cosine envelope that is zero outside ``[start_s, end_s)``."""
    envelope = np.zeros(int(n), dtype=np.float64)
    first = int(round(float(start_s) * SAMPLE_RATE))
    last = int(round(float(end_s) * SAMPLE_RATE))
    first = max(0, min(first, int(n)))
    last = max(first, min(last, int(n)))
    width = last - first
    if width <= 1:
        return envelope
    ramp = 0.5 * (1.0 - np.cos(2.0 * math.pi * (np.arange(width) + 0.5) / width))
    envelope[first:last] = ramp
    return envelope


def _fade_envelope(n: int, fade_in_s: float = 0.05, fade_out_s: float = 0.05) -> np.ndarray:
    """An envelope that fades in and out with a raised cosine.

    A tone that starts at full amplitude is a step, and a step is a
    transient: the fade keeps the clips free of clicks that the manifest
    does not declare.
    """
    count = int(n)
    envelope = np.ones(count, dtype=np.float64)
    head = max(0, min(int(round(float(fade_in_s) * SAMPLE_RATE)), count))
    tail = max(0, min(int(round(float(fade_out_s) * SAMPLE_RATE)), count))
    if head > 1:
        envelope[:head] = 0.5 * (1.0 - np.cos(math.pi * np.arange(head) / head))
    if tail > 1:
        envelope[count - tail :] = 0.5 * (1.0 - np.cos(math.pi * np.arange(tail)[::-1] / tail))
    return envelope


def _convolve(x: np.ndarray, h: np.ndarray) -> np.ndarray:
    """Linear convolution of two real signals."""
    a = np.asarray(x, dtype=np.float64).ravel()
    b = np.asarray(h, dtype=np.float64).ravel()
    total = a.size + b.size - 1
    n_fft = 1 << max(1, (total - 1).bit_length())
    spectrum = np.fft.rfft(a, n_fft) * np.fft.rfft(b, n_fft)
    return np.fft.irfft(spectrum, n_fft)[:total]


def _vowel(
    n: int,
    f0: float = 120.0,
    harmonics: int = 24,
    formants: tuple[tuple[float, float], ...] = ((700.0, 90.0), (1200.0, 110.0), (2600.0, 160.0)),
    tilt: float = 0.9,
) -> np.ndarray:
    """A vowel-like tone: a harmonic stack shaped by three formants."""
    t = _time_axis(n)
    out = np.zeros(int(n), dtype=np.float64)
    for k in range(1, int(harmonics) + 1):
        freq = f0 * k
        resonance = 0.0
        for centre, bandwidth in formants:
            resonance += 1.0 / (1.0 + ((freq - centre) / bandwidth) ** 2)
        gain = resonance / (k ** tilt)
        out += gain * np.sin(2.0 * math.pi * freq * t + 0.7 * k)
    peak = float(np.max(np.abs(out)))
    if peak > _EPS:
        out /= peak
    return out


# --------------------------------------------------------------------------
# WAV writing
# --------------------------------------------------------------------------
def _pack_pcm(data: np.ndarray, bit_depth: int) -> bytes:
    """Pack a float signal into little-endian PCM bytes.

    Full scale is ``2**(bit_depth - 1) - 1`` (so ``1.0`` maps to the largest
    positive code and round-trips exactly).
    """
    x = np.asarray(data, dtype=np.float64)
    full = (1 << (int(bit_depth) - 1)) - 1
    codes = np.clip(np.rint(x * full), -full - 1, full).astype(np.int64)
    if int(bit_depth) == 16:
        return codes.astype("<i2").tobytes()
    if int(bit_depth) == 24:
        unsigned = (codes & 0xFFFFFF).astype(np.uint32)
        b0 = unsigned & 0xFF
        b1 = (unsigned >> 8) & 0xFF
        b2 = (unsigned >> 16) & 0xFF
        return np.stack((b0, b1, b2), axis=-1).astype(np.uint8).tobytes()
    if int(bit_depth) == 32:
        return codes.astype("<i4").tobytes()
    raise ValueError(f"unsupported bit depth: {bit_depth}")


def _write_wav(path: Path, data: np.ndarray, sample_rate: int = SAMPLE_RATE,
               bit_depth: int = BIT_DEPTH) -> None:
    """Write a ``(n, channels)`` float array as a PCM WAV file."""
    x = np.asarray(data, dtype=np.float64)
    if x.ndim == 1:
        x = x[:, None]
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _pack_pcm(x, bit_depth)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(int(x.shape[1]))
        handle.setsampwidth(int(bit_depth) // 8)
        handle.setframerate(int(sample_rate))
        handle.writeframes(payload)


def _sha256(path: Path) -> str:
    """SHA-256 checksum of a file."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------
def _normalise(
    data: np.ndarray,
    kind: str,
    target_db: float,
) -> tuple[np.ndarray, list[float]]:
    """Scale a mix so that it reaches its documented level.

    Parameters
    ----------
    data:
        The mixed signal, shape ``(n, channels)``.
    kind:
        ``"peak"`` scales the whole mix so that the largest absolute sample
        equals ``target_db``; ``"rms"`` scales every channel so that its RMS
        equals ``target_db``; ``"none"`` leaves the signal alone (used by the
        clips that are *supposed* to be at full scale).
    target_db:
        The target level in dBFS.

    Returns
    -------
    tuple
        The scaled signal and the list of the per-channel gains that were
        applied (one entry for ``"peak"``/``"none"``, one per channel for
        ``"rms"``).
    """
    x = np.asarray(data, dtype=np.float64)
    if x.ndim == 1:
        x = x[:, None]
    if kind == "none":
        return x, [1.0] * x.shape[1]
    if kind == "peak":
        current = float(np.max(np.abs(x)))
        if current <= _EPS:
            return x, [1.0] * x.shape[1]
        gain = _db_to_amp(target_db) / current
        return x * gain, [gain] * x.shape[1]
    if kind == "rms":
        target = _db_to_amp(target_db)
        gains: list[float] = []
        out = np.empty_like(x)
        for channel in range(x.shape[1]):
            current = float(np.sqrt(np.mean(x[:, channel] ** 2)))
            gain = target / current if current > _EPS else 1.0
            out[:, channel] = x[:, channel] * gain
            gains.append(gain)
        return out, gains
    raise ValueError(f"unknown normalisation kind: {kind}")


# --------------------------------------------------------------------------
# Manifest assembly
# --------------------------------------------------------------------------
def _levels_of(data: np.ndarray) -> dict[str, float]:
    """The level figures every clip carries."""
    mono = data.mean(axis=1)
    return {
        "peak_dbfs": _amp_to_db(float(np.max(np.abs(data)))),
        "rms_dbfs": _amp_to_db(float(np.sqrt(np.mean(mono ** 2)))),
        "total_rms_dbfs": _amp_to_db(float(np.sqrt(np.mean(np.sum(data ** 2, axis=1))))),
    }


def _clip(
    clip_id: str,
    data: np.ndarray,
    events: list[dict[str, Any]],
    levels: dict[str, float],
    tags: list[str],
    positions: list[list[float]] | None = None,
    regions: dict[str, list[float]] | None = None,
    bands: list[dict[str, Any]] | None = None,
    frequencies: list[dict[str, Any]] | None = None,
    phase: dict[str, Any] | None = None,
    derived: list[dict[str, Any]] | None = None,
    mix: str = "mono",
) -> tuple[dict[str, Any], np.ndarray]:
    """Assemble one manifest entry and return it with its signal."""
    entry: dict[str, Any] = {
        "id": clip_id,
        "file": f"audio/test/{clip_id}.wav",
        "licence": LICENCE,
        "source_url": SOURCE_URL,
        "source_credit": SOURCE_CREDIT,
        "sample_rate": SAMPLE_RATE,
        "channels": int(data.shape[1]),
        "duration_s": round(int(data.shape[0]) / float(SAMPLE_RATE), 9),
        "split": "test",
        "tags": tags,
        "annotations": {"events": events, "mix": mix},
    }
    if positions:
        entry["annotations"]["positions"] = positions
    if regions:
        entry["annotations"]["regions"] = regions
    if bands:
        entry["annotations"]["bands"] = bands
    if frequencies:
        entry["annotations"]["frequencies"] = frequencies
    if phase:
        entry["annotations"]["phase"] = phase
    if derived:
        entry["annotations"]["derived"] = derived
    entry["annotations"]["levels"] = {k: round(float(v), 6) for k, v in levels.items()}
    return entry, data


def _frequency(
    name: str,
    hz: float | None,
    region: tuple[float, float] | list[float] | None = None,
    mix: str = "mono",
    search_hz: tuple[float, float] | None = None,
    tolerance_hz: float = 2.0,
    duration: float = DURATION_S,
    method: str = "peak",
) -> dict[str, Any]:
    """One frequency ground truth entry.

    The entry is the assertable form of a frequency: the frequency of the
    strongest partial of the clip inside ``search_hz``, measured over
    ``region_s`` on the ``mix`` signal.  ``hz`` is ``None`` when the clip
    holds no partial at all (the silence clip).

    ``method`` is ``"peak"`` (the strongest partial) or ``"centroid"`` (the
    centre of mass of the power spectrum, which is how the two noise clips
    are told apart).
    """
    if region is not None:
        start = float(region[0])
        end = float(region[1])
    else:
        start = 0.0
        end = float(duration)
    if search_hz is None:
        if hz is None:
            search_hz = (20.0, 20000.0)
        else:
            span = max(5.0, 0.02 * abs(float(hz)))
            search_hz = (float(hz) - span, float(hz) + span)
    entry: dict[str, Any] = {
        "name": name,
        "hz": None if hz is None else float(hz),
        "region_s": [start, end],
        "mix": mix,
        "search_hz": [float(search_hz[0]), float(search_hz[1])],
        "tolerance_hz": float(tolerance_hz),
    }
    if method != "peak":
        entry["method"] = method
    return entry


#: One band of a ground truth: the name, the band edges, the window, the
#: signal and - optionally - the minimum excess over the rest of the clip.
BandItem = tuple[
    str,
    tuple[float, float],
    tuple[float, float] | None,
    str,
]


def _band_truths(
    signal: np.ndarray,
    items: list[BandItem | tuple[str, tuple[float, float], tuple[float, float] | None, str, float]],
    duration: float,
) -> list[dict[str, Any]]:
    """The ground truth of a list of band measurements.

    Parameters
    ----------
    signal:
        The signal the bands are measured on - normally the finished mix
        of the clip, so that the figure is exactly what a measurement of
        the clip returns.
    items:
        One ``(name, band_hz, region_s, mix)`` tuple per band.  A fifth
        element sets ``min_excess_db``: how much louder the band has to be
        inside the window than outside it.
    duration:
        Length of the clip, used when a band has no region of its own.

    Returns
    -------
    list
        One dictionary per band: ``name``, ``band_hz``, ``region_s``,
        ``mix`` and ``level_dbfs``.
    """
    out: list[dict[str, Any]] = []
    for item in items:
        min_excess: float | None = None
        if len(item) == 5:
            name, band, region, mix, min_excess = item  # type: ignore[misc]
        else:
            name, band, region, mix = item  # type: ignore[misc]
        start = region[0] if region is not None else 0.0
        end = region[1] if region is not None else duration
        level = measure.band_energy_db(signal, SAMPLE_RATE, band[0], band[1], start, end, mix)
        entry: dict[str, Any] = {
            "name": name,
            "band_hz": [float(band[0]), float(band[1])],
            "region_s": [float(start), float(end)],
            "mix": mix,
            "level_dbfs": round(float(level), 4),
        }
        if min_excess is not None:
            entry["min_excess_db"] = float(min_excess)
        out.append(entry)
    return out


# --------------------------------------------------------------------------
# Clip recipes
# --------------------------------------------------------------------------
def _build_clicks(rng: np.random.Generator) -> tuple[dict[str, Any], np.ndarray]:
    """A quiet tone bed with six single-sample impulses.

    The bed is 480 Hz at -40 dBFS.  480 Hz has exactly 100 samples per period
    at 48 kHz, so every click position - all of them multiples of 12000
    samples - sits on a zero crossing of the bed: the impulse is *added* to a
    bed sample that is zero, and the value of the click sample is therefore
    exactly the amplitude of the click.
    """
    duration = 3.0
    n = int(round(duration * SAMPLE_RATE))
    bed_hz = 480.0
    bed_db = -40.0
    bed = _tone(bed_hz, n, _db_to_amp(bed_db))

    click_db = [0.0, -4.0, -8.0, -12.0, -16.0, -20.0]
    click_times = [0.25 + 0.5 * k for k in range(len(click_db))]

    data = np.repeat(bed[:, None], CHANNELS, axis=1)
    events: list[dict[str, Any]] = []
    positions: list[list[float]] = []
    for seconds, level in zip(click_times, click_db):
        index = int(round(seconds * SAMPLE_RATE))
        amplitude = _db_to_amp(level)
        data[index, :] += amplitude
        positions.append([index / SAMPLE_RATE, (index + 1) / SAMPLE_RATE])
        events.append(
            {
                "type": "click",
                "start_s": index / SAMPLE_RATE,
                "end_s": (index + 1) / SAMPLE_RATE,
                "label": f"single-sample impulse, {level:+.0f} dBFS",
                "amplitude_dbfs": level,
            }
        )

    data, gains = _normalise(data, "peak", 0.0)
    gain = gains[0]
    levels = _levels_of(data)
    levels.update(
        {
            "bed_dbfs": _amp_to_db(_db_to_amp(bed_db) * gain),
            "bed_hz": bed_hz,
            "click_count": float(len(click_db)),
            "click_interval_s": 0.5,
            "full_scale_click_dbfs": 0.0,
            "normalisation_gain": gain,
            "bit_depth": float(BIT_DEPTH),
        }
    )
    bands = _band_truths(
        bed * gain,
        [("bed", (450.0, 510.0), (0.05, 0.20), "mono")],
        duration,
    )
    return _clip(
        "clicks",
        data,
        events,
        levels,
        ["synthetic", "click", "impulse", "transient", "declick"],
        positions=positions,
        regions={"clicks": [0.0, duration], "bed": [0.05, 0.20]},
        bands=bands,
        frequencies=[
            _frequency(
                "bed",
                bed_hz,
                region=(0.05, 0.20),
                search_hz=(450.0, 510.0),
                tolerance_hz=2.0,
                duration=duration,
            )
        ],
    )


def _build_hum50(rng: np.random.Generator) -> tuple[dict[str, Any], np.ndarray]:
    """A 1 kHz reference tone with 50 Hz mains hum and its 100 Hz harmonic."""
    n = int(round(DURATION_S * SAMPLE_RATE))
    tone_hz, tone_db = 1000.0, -9.0
    hum_hz, hum_db = 50.0, -25.0
    harmonic_hz, harmonic_db = 100.0, -31.0

    tone = _tone(tone_hz, n, _db_to_amp(tone_db))
    hum = _tone(hum_hz, n, _db_to_amp(hum_db))
    harmonic = _tone(harmonic_hz, n, _db_to_amp(harmonic_db))
    mix = tone + hum + harmonic

    data = np.repeat(mix[:, None], CHANNELS, axis=1)
    data, gains = _normalise(data, "peak", -6.0)
    gain = gains[0]

    events = [
        {
            "type": "hum",
            "start_s": 0.0,
            "end_s": DURATION_S,
            "label": "50 Hz mains hum with 100 Hz harmonic",
        },
        {
            "type": "music",
            "start_s": 0.0,
            "end_s": DURATION_S,
            "label": "1 kHz reference tone",
        },
    ]
    levels = _levels_of(data)
    levels.update(
        {
            "tone_hz": tone_hz,
            "tone_dbfs": _amp_to_db(_db_to_amp(tone_db) * gain),
            "hum_hz": hum_hz,
            "hum_dbfs": _amp_to_db(_db_to_amp(hum_db) * gain),
            "hum_harmonic_hz": harmonic_hz,
            "hum_harmonic_dbfs": _amp_to_db(_db_to_amp(harmonic_db) * gain),
            "hum_below_tone_db": tone_db - hum_db,
            "normalisation_gain": gain,
            "bit_depth": float(BIT_DEPTH),
        }
    )
    bands = _band_truths(
        data,
        [
            ("tone", (950.0, 1050.0), None, "mono"),
            ("hum", (45.0, 55.0), None, "mono"),
            ("hum_harmonic", (95.0, 105.0), None, "mono"),
        ],
        DURATION_S,
    )
    return _clip(
        "hum50",
        data,
        events,
        levels,
        ["synthetic", "hum", "mains", "harmonic", "dehum"],
        bands=bands,
        frequencies=[
            _frequency("tone", tone_hz, search_hz=(950.0, 1050.0), tolerance_hz=5.0),
            _frequency("hum", hum_hz, search_hz=(45.0, 55.0), tolerance_hz=1.0),
            _frequency(
                "hum_harmonic",
                harmonic_hz,
                search_hz=(95.0, 105.0),
                tolerance_hz=1.0,
            ),
        ],
    )


def _build_hiss(rng: np.random.Generator) -> tuple[dict[str, Any], np.ndarray]:
    """White noise at -60 dBFS RMS, independent in the two channels."""
    n = int(round(DURATION_S * SAMPLE_RATE))
    data = rng.standard_normal((n, CHANNELS))
    data, gains = _normalise(data, "rms", -60.0)

    events = [
        {
            "type": "hiss",
            "start_s": 0.0,
            "end_s": DURATION_S,
            "label": "white noise, steady",
        }
    ]
    levels = _levels_of(data)
    levels.update(
        {
            "noise_floor_dbfs": -60.0,
            "noise_type": 0.0,  # 0 = white, 1 = pink
            "gain_ch0": gains[0],
            "gain_ch1": gains[1],
            "bit_depth": float(BIT_DEPTH),
        }
    )
    bands = _band_truths(
        data,
        [
            ("broadband", (20.0, 20000.0), None, "mono"),
            ("low", (20.0, 250.0), None, "mono"),
            ("high", (5000.0, 20000.0), None, "mono"),
        ],
        DURATION_S,
    )
    return _clip(
        "hiss",
        data,
        events,
        levels,
        ["synthetic", "hiss", "noise", "white", "denoise"],
        bands=bands,
        frequencies=[
            # The centre of mass of the power spectrum of white noise is
            # the middle of the band: (f2^2 - f1^2) / (2 * (f2 - f1)).
            _frequency(
                "centroid",
                (20000.0**2 - 20.0**2) / (2.0 * (20000.0 - 20.0)),
                method="centroid",
                tolerance_hz=300.0,
            )
        ],
    )


def _build_pink_hiss(rng: np.random.Generator) -> tuple[dict[str, Any], np.ndarray]:
    """Pink noise at -60 dBFS RMS, independent in the two channels."""
    n = int(round(DURATION_S * SAMPLE_RATE))
    data = np.stack([_pink_noise(n, rng), _pink_noise(n, rng)], axis=1)
    data, gains = _normalise(data, "rms", -60.0)

    events = [
        {
            "type": "hiss",
            "start_s": 0.0,
            "end_s": DURATION_S,
            "label": "pink noise, steady",
        }
    ]
    levels = _levels_of(data)
    levels.update(
        {
            "noise_floor_dbfs": -60.0,
            "noise_type": 1.0,  # 0 = white, 1 = pink
            "gain_ch0": gains[0],
            "gain_ch1": gains[1],
            "bit_depth": float(BIT_DEPTH),
        }
    )
    bands = _band_truths(
        data,
        [
            ("broadband", (20.0, 20000.0), None, "mono"),
            ("low", (20.0, 250.0), None, "mono"),
            ("high", (5000.0, 20000.0), None, "mono"),
        ],
        DURATION_S,
    )
    return _clip(
        "pink_hiss",
        data,
        events,
        levels,
        ["synthetic", "hiss", "noise", "pink", "denoise"],
        bands=bands,
        frequencies=[
            # The centre of mass of a spectrum whose power is proportional
            # to 1/f: (f2 - f1) / ln(f2 / f1).
            _frequency(
                "centroid",
                (20000.0 - 20.0) / math.log(20000.0 / 20.0),
                method="centroid",
                tolerance_hz=120.0,
            )
        ],
    )


def _build_sibilance(rng: np.random.Generator) -> tuple[dict[str, Any], np.ndarray]:
    """A 1 kHz tone with a 6 kHz sibilance burst."""
    n = int(round(DURATION_S * SAMPLE_RATE))
    tone_hz, tone_db = 1000.0, -14.0
    burst_start, burst_end = 0.60, 0.90
    band_low, band_high = 5000.0, 7000.0
    burst_peak_db = -6.0

    tone = _tone(tone_hz, n, _db_to_amp(tone_db))
    burst = _band_noise(n, band_low, band_high, rng, skirt_hz=500.0)
    burst_peak = float(np.max(np.abs(burst)))
    if burst_peak > _EPS:
        burst = burst / burst_peak * _db_to_amp(burst_peak_db)
    envelope = _raised_cosine_envelope(n, burst_start, burst_end)
    burst *= envelope

    data = np.repeat((tone + burst)[:, None], CHANNELS, axis=1)
    data, gains = _normalise(data, "peak", -6.0)
    gain = gains[0]

    events = [
        {
            "type": "sibilance",
            "start_s": burst_start,
            "end_s": burst_end,
            "label": "6 kHz sibilance burst",
            "center_hz": 6000.0,
            "band_hz": [band_low, band_high],
        },
        {
            "type": "music",
            "start_s": 0.0,
            "end_s": DURATION_S,
            "label": "1 kHz reference tone",
        },
    ]
    burst_signal = burst * gain
    levels = _levels_of(data)
    levels.update(
        {
            "tone_hz": tone_hz,
            "tone_dbfs": _amp_to_db(_db_to_amp(tone_db) * gain),
            "sibilance_center_hz": 6000.0,
            "sibilance_band_low_hz": band_low,
            "sibilance_band_high_hz": band_high,
            "sibilance_peak_dbfs": _amp_to_db(_db_to_amp(burst_peak_db) * gain),
            "sibilance_burst_rms_dbfs": _amp_to_db(
                float(np.sqrt(np.mean(burst_signal ** 2)))
            ),
            "normalisation_gain": gain,
            "bit_depth": float(BIT_DEPTH),
        }
    )
    bands = _band_truths(
        data,
        [
            ("tone", (950.0, 1050.0), None, "mono"),
            # The burst has to be louder inside its window than outside it:
            # that is what makes the window the ground truth of the event.
            ("sibilance", (5000.0, 7000.0), (burst_start, burst_end), "mono", 6.0),
        ],
        DURATION_S,
    )
    return _clip(
        "sibilance",
        data,
        events,
        levels,
        ["synthetic", "sibilance", "deess", "high-frequency"],
        regions={"sibilance": [burst_start, burst_end]},
        bands=bands,
        frequencies=[
            _frequency("tone", tone_hz, search_hz=(950.0, 1050.0), tolerance_hz=5.0)
        ],
    )


def _build_plosive(rng: np.random.Generator) -> tuple[dict[str, Any], np.ndarray]:
    """A vowel-like tone with a low-frequency plosive burst."""
    n = int(round(DURATION_S * SAMPLE_RATE))
    f0 = 120.0
    tone_db = -14.0
    burst_start, burst_end = 0.50, 0.54
    burst_hz = 80.0
    burst_peak_db = -4.0

    vowel = _vowel(n, f0=f0) * _db_to_amp(tone_db)

    burst_len = int(round((burst_end - burst_start) * SAMPLE_RATE))
    burst_t = _time_axis(burst_len)
    shape = np.exp(-burst_t / 0.008)
    low = _tone(burst_hz, burst_len, 1.0) * shape
    rumble = _band_noise(burst_len, 0.0, 300.0, rng, skirt_hz=200.0) * shape
    burst = low + 0.4 * rumble
    burst_peak = float(np.max(np.abs(burst)))
    if burst_peak > _EPS:
        burst = burst / burst_peak * _db_to_amp(burst_peak_db)

    mix = vowel.copy()
    first = int(round(burst_start * SAMPLE_RATE))
    last = min(first + burst.size, n)
    mix[first:last] += burst[: last - first]

    data = np.repeat(mix[:, None], CHANNELS, axis=1)
    data, gains = _normalise(data, "peak", -6.0)
    gain = gains[0]

    events = [
        {
            "type": "plosive",
            "start_s": burst_start,
            "end_s": burst_end,
            "label": "low-frequency plosive burst",
            "center_hz": burst_hz,
            "band_hz": [0.0, 300.0],
        },
        {
            "type": "speech",
            "start_s": 0.0,
            "end_s": DURATION_S,
            "label": "vowel-like tone, 120 Hz fundamental",
        },
    ]
    levels = _levels_of(data)
    levels.update(
        {
            "f0_hz": f0,
            "tone_dbfs": _amp_to_db(_db_to_amp(tone_db) * gain),
            "burst_hz": burst_hz,
            "burst_peak_dbfs": _amp_to_db(_db_to_amp(burst_peak_db) * gain),
            "normalisation_gain": gain,
            "bit_depth": float(BIT_DEPTH),
        }
    )
    bands = _band_truths(
        data,
        [
            ("vowel", (60.0, 3000.0), None, "mono"),
            # The burst band is measured over the whole clip: a 40 ms
            # window cannot resolve a 60 Hz band.
            ("burst", (40.0, 100.0), None, "mono"),
        ],
        DURATION_S,
    )
    return _clip(
        "plosive",
        data,
        events,
        levels,
        ["synthetic", "plosive", "deplosive", "transient", "low-frequency"],
        regions={"plosive": [burst_start, burst_end]},
        bands=bands,
        frequencies=[
            _frequency("f0", f0, search_hz=(100.0, 140.0), tolerance_hz=2.0),
            # The burst is a decaying partial, so the peak of its spectrum
            # sits a few hertz below the nominal frequency.
            _frequency("burst", burst_hz, search_hz=(60.0, 100.0), tolerance_hz=6.0),
        ],
    )


def _build_reverb(rng: np.random.Generator) -> tuple[dict[str, Any], np.ndarray]:
    """A 440 Hz tone convolved with a synthesised impulse response.

    The impulse response is a direct sound plus exponentially decaying
    noise.  The amplitude decay constant is ``RT60 / ln(1000)``, so the
    reverberation time of the room is exactly 1.2 s.

    The starter is a short (5 ms) 440 Hz tone burst: a long excitation
    would make the decay of the tail unreadable, because the envelope of a
    narrow band decay fluctuates over the length of the excitation.
    """
    n = int(round(DURATION_S * SAMPLE_RATE))
    tone_hz = 440.0
    rt60 = 1.2
    tau = rt60 / math.log(1000.0)
    ir_length = int(round(1.5 * SAMPLE_RATE))
    starter_start, starter_length = 0.20, 0.005

    ir_t = _time_axis(ir_length)
    ir = rng.standard_normal(ir_length) * np.exp(-ir_t / tau) * 0.01
    ir[0] = 1.0  # the direct sound

    starter = np.zeros(n, dtype=np.float64)
    first = int(round(starter_start * SAMPLE_RATE))
    last = min(first + int(round(starter_length * SAMPLE_RATE)), n)
    starter[first:last] = _tone(tone_hz, last - first, 1.0)

    wet = _convolve(starter, ir)[:n]
    data = np.repeat(wet[:, None], CHANNELS, axis=1)
    data, gains = _normalise(data, "peak", -6.0)
    gain = gains[0]

    events = [
        {
            "type": "reverb",
            "start_s": starter_start + starter_length,
            "end_s": DURATION_S,
            "label": "reverberant tail, RT60 1.2 s",
        },
        {
            "type": "music",
            "start_s": starter_start,
            "end_s": DURATION_S,
            "label": "440 Hz tone in a reverberant room",
        },
    ]
    levels = _levels_of(data)
    levels.update(
        {
            "tone_hz": tone_hz,
            "rt60_s": rt60,
            "ir_decay_tau_s": tau,
            "ir_length_s": ir_length / SAMPLE_RATE,
            "starter_start_s": starter_start,
            "starter_length_s": starter_length,
            "normalisation_gain": gain,
            "bit_depth": float(BIT_DEPTH),
        }
    )
    bands = _band_truths(
        data,
        # The tone band is measured over the whole clip: the five
        # millisecond starter is far too short to resolve a 60 Hz band.
        [("tone", (410.0, 470.0), None, "mono")],
        DURATION_S,
    )
    return _clip(
        "reverb",
        data,
        events,
        levels,
        ["synthetic", "reverb", "room", "dereverb", "tail"],
        regions={"rt60": [0.35, 1.75], "tail": [0.21, 2.0]},
        bands=bands,
        frequencies=[
            # The tail of a room is the tone convolved with a broadband
            # impulse response: its spectrum is the spectrum of the room
            # around 440 Hz, so the centre of mass is the robust figure.
            _frequency(
                "tone",
                tone_hz,
                region=(0.21, 2.0),
                search_hz=(300.0, 700.0),
                tolerance_hz=8.0,
                method="centroid",
            )
        ],
    )


def _build_clipped(rng: np.random.Generator) -> tuple[dict[str, Any], np.ndarray]:
    """A 1 kHz tone pushed to twice full scale and hard clipped."""
    n = int(round(DURATION_S * SAMPLE_RATE))
    tone_hz = 1000.0
    drive = 2.0
    threshold = 0.999

    over = _tone(tone_hz, n, drive)
    clipped = np.clip(over, -1.0, 1.0)
    data = np.repeat(clipped[:, None], CHANNELS, axis=1)
    data, gains = _normalise(data, "peak", 0.0)
    gain = gains[0]

    # Sample runs that sit at full scale (one channel - both are identical).
    flagged = np.abs(data[:, 0]) >= threshold
    edges = np.diff(flagged.astype(np.int8))
    starts = [int(v) + 1 for v in np.flatnonzero(edges == 1)]
    stops = [int(v) + 1 for v in np.flatnonzero(edges == -1)]
    if flagged.size and bool(flagged[0]):
        starts.insert(0, 0)
    if flagged.size and bool(flagged[-1]):
        stops.append(int(flagged.size))
    runs = sorted(zip(starts, stops))
    clipped_samples = int(sum(stop - start for start, stop in runs))

    events = [
        {
            "type": "clipping",
            "start_s": 0.0,
            "end_s": DURATION_S,
            "label": "hard-clipped 1 kHz tone",
        }
    ]
    positions = [[start / SAMPLE_RATE, stop / SAMPLE_RATE] for start, stop in runs[:16]]
    levels = _levels_of(data)
    levels.update(
        {
            "tone_hz": tone_hz,
            "drive": drive,
            "clip_threshold": threshold,
            "clipped_sample_count": float(clipped_samples),
            "clip_run_count": float(len(runs)),
            "clipped_fraction": clipped_samples / float(data.shape[0]),
            "normalisation_gain": gain,
            "bit_depth": float(BIT_DEPTH),
        }
    )
    bands = _band_truths(
        data,
        [("tone", (950.0, 1050.0), None, "mono")],
        DURATION_S,
    )
    return _clip(
        "clipped",
        data,
        events,
        levels,
        ["synthetic", "clipping", "distortion", "declip", "full-scale"],
        positions=positions,
        regions={"clipped": [0.0, DURATION_S]},
        bands=bands,
        frequencies=[
            _frequency("tone", tone_hz, search_hz=(950.0, 1050.0), tolerance_hz=5.0)
        ],
    )


def _build_silence(rng: np.random.Generator) -> tuple[dict[str, Any], np.ndarray]:
    """Digital silence."""
    n = int(round(DURATION_S * SAMPLE_RATE))
    data = np.zeros((n, CHANNELS), dtype=np.float64)

    events = [
        {
            "type": "silence",
            "start_s": 0.0,
            "end_s": DURATION_S,
            "label": "digital silence",
        }
    ]
    levels = _levels_of(data)
    levels.update(
        {
            "mid_peak_dbfs": LEVEL_FLOOR_DB,
            "side_peak_dbfs": LEVEL_FLOOR_DB,
            "mid_rms_dbfs": LEVEL_FLOOR_DB,
            "side_rms_dbfs": LEVEL_FLOOR_DB,
            "bit_depth": float(BIT_DEPTH),
        }
    )
    return _clip(
        "silence",
        data,
        events,
        levels,
        ["synthetic", "silence", "quiet"],
        regions={"silence": [0.0, DURATION_S]},
        frequencies=[
            # Digital silence has no partial at all: the claim is that the
            # measurement finds none.
            _frequency("none", None, tolerance_hz=0.0)
        ],
    )


def _build_stereo_mid(rng: np.random.Generator) -> tuple[dict[str, Any], np.ndarray]:
    """Pure mid content: both channels carry the same signal."""
    n = int(round(DURATION_S * SAMPLE_RATE))
    tone_hz = 440.0
    tone_db = -14.0

    mid = _tone(tone_hz, n, _db_to_amp(tone_db))
    data = np.repeat(mid[:, None], CHANNELS, axis=1)
    data, gains = _normalise(data, "peak", -6.0)
    gain = gains[0]

    events = [
        {
            "type": "music",
            "start_s": 0.0,
            "end_s": DURATION_S,
            "label": "440 Hz tone, mid only",
        }
    ]
    levels = _levels_of(data)
    levels.update(
        {
            "tone_hz": tone_hz,
            "mid_dbfs": _amp_to_db(_db_to_amp(tone_db) * gain),
            "side_dbfs": LEVEL_FLOOR_DB,
            "normalisation_gain": gain,
            "bit_depth": float(BIT_DEPTH),
        }
    )
    bands = _band_truths(
        data,
        [("mid", (410.0, 470.0), None, "mid")],
        DURATION_S,
    )
    return _clip(
        "stereo_mid",
        data,
        events,
        levels,
        ["synthetic", "stereo", "mid", "center", "ms"],
        bands=bands,
        frequencies=[
            _frequency(
                "mid",
                tone_hz,
                mix="mid",
                search_hz=(410.0, 470.0),
                tolerance_hz=3.0,
            )
        ],
    )


def _build_stereo_side(rng: np.random.Generator) -> tuple[dict[str, Any], np.ndarray]:
    """Pure side content: the two channels are opposite."""
    n = int(round(DURATION_S * SAMPLE_RATE))
    tone_hz = 220.0
    tone_db = -20.0

    side = _tone(tone_hz, n, _db_to_amp(tone_db))
    data = np.stack([side.copy(), -side], axis=1)
    data, gains = _normalise(data, "peak", -6.0)
    gain = gains[0]

    events = [
        {
            "type": "music",
            "start_s": 0.0,
            "end_s": DURATION_S,
            "label": "220 Hz tone, side only",
        }
    ]
    levels = _levels_of(data)
    levels.update(
        {
            "tone_hz": tone_hz,
            "mid_dbfs": LEVEL_FLOOR_DB,
            "side_dbfs": _amp_to_db(_db_to_amp(tone_db) * gain),
            "normalisation_gain": gain,
            "bit_depth": float(BIT_DEPTH),
        }
    )
    bands = _band_truths(
        data,
        [("side", (200.0, 240.0), None, "side")],
        DURATION_S,
    )
    return _clip(
        "stereo_side",
        data,
        events,
        levels,
        ["synthetic", "stereo", "side", "azimuth", "ms"],
        bands=bands,
        mix="side",
        frequencies=[
            _frequency(
                "side",
                tone_hz,
                mix="side",
                search_hz=(200.0, 240.0),
                tolerance_hz=3.0,
            )
        ],
    )


def _build_voice_hiss(rng: np.random.Generator) -> tuple[dict[str, Any], np.ndarray]:
    """A vowel-like voice with a hiss floor underneath it.

    The voice is *identical* in the two channels, the hiss is *independent*:
    the mid signal therefore carries the voice and the side signal carries
    the noise, and both levels are measured exactly.

    The signal-to-noise ratio is the difference of the peak of the mid and
    the RMS of the side.
    """
    n = int(round(DURATION_S * SAMPLE_RATE))
    f0 = 130.0
    voice_db = -16.0
    hiss_db = -40.0

    voice = _vowel(n, f0=f0, harmonics=30) * _db_to_amp(voice_db)
    hiss_left = rng.standard_normal(n) * _db_to_amp(hiss_db)
    hiss_right = rng.standard_normal(n) * _db_to_amp(hiss_db)
    data = np.stack([voice + hiss_left, voice + hiss_right], axis=1)
    data, gains = _normalise(data, "peak", -6.0)
    gain = gains[0]

    # The side signal is the difference of two independent noise fields
    # divided by two: its RMS is the noise RMS over the square root of two.
    side_factor = 1.0 / math.sqrt(2.0)
    # The mid carries the voice and half of the noise of each channel; the
    # noise is more than 25 dB below the voice, so the RMS of the mid is
    # the RMS of the voice.
    voice_rms = _amp_to_db(float(np.sqrt(np.mean(voice**2))) * gain)
    hiss_rms = _amp_to_db(_db_to_amp(hiss_db) * gain * side_factor)

    events = [
        {
            "type": "speech",
            "start_s": 0.0,
            "end_s": DURATION_S,
            "label": "vowel-like tone, 130 Hz fundamental",
        },
        {
            "type": "hiss",
            "start_s": 0.0,
            "end_s": DURATION_S,
            "label": "steady hiss, independent in the two channels",
        },
    ]
    levels = _levels_of(data)
    levels.update(
        {
            "f0_hz": f0,
            "voice_rms_dbfs": voice_rms,
            "hiss_rms_dbfs": hiss_rms,
            "hiss_channel_rms_dbfs": _amp_to_db(_db_to_amp(hiss_db) * gain),
            "snr_db": voice_rms - hiss_rms,
            "mid_rms_dbfs": voice_rms,
            "side_rms_dbfs": hiss_rms,
            "normalisation_gain": gain,
            "bit_depth": float(BIT_DEPTH),
        }
    )
    bands = _band_truths(
        data,
        [
            ("voice", (100.0, 4000.0), None, "mid"),
            ("hiss", (5000.0, 20000.0), None, "side"),
            ("broadband", (20.0, 20000.0), None, "mono"),
        ],
        DURATION_S,
    )
    return _clip(
        "voice_hiss",
        data,
        events,
        levels,
        ["synthetic", "speech", "voice", "hiss", "denoise", "snr"],
        bands=bands,
        frequencies=[
            _frequency("f0", f0, mix="mid", search_hz=(110.0, 150.0), tolerance_hz=3.0)
        ],
        derived=[
            {
                "name": "snr",
                "value": round(voice_rms - hiss_rms, 6),
                "tolerance": 0.3,
                "terms": [
                    {"key": "mid_rms_dbfs", "sign": 1.0},
                    {"key": "side_rms_dbfs", "sign": -1.0},
                ],
            }
        ],
    )


def _build_music_mix(rng: np.random.Generator) -> tuple[dict[str, Any], np.ndarray]:
    """A mixture of three musical parts: a bass, a triad and a kick drum.

    The bass is a 110 Hz sine, the triad is a 220/330/440 Hz stack and the
    drum is a 70 Hz burst with an exponential decay.  The two drum hits are
    the only transients of the clip.
    """
    n = int(round(DURATION_S * SAMPLE_RATE))
    bass_hz, bass_db = 110.0, -28.0
    chord_hz = (220.0, 330.0, 440.0)
    chord_db = -26.0
    kick_hz, kick_db = 70.0, -6.0
    kick_times = (0.30, 1.10)
    kick_length_s = 0.15
    kick_tau_s = 0.025

    # The parts start at a zero crossing, so the mixture begins without a
    # step: only the drum hits are transients.
    envelope = _fade_envelope(n, 0.0, 0.02)
    bass = _tone(bass_hz, n, _db_to_amp(bass_db)) * envelope
    chord = np.zeros(n, dtype=np.float64)
    for frequency in chord_hz:
        chord += _tone(frequency, n, _db_to_amp(chord_db))

    kick = np.zeros(n, dtype=np.float64)
    length = int(round(kick_length_s * SAMPLE_RATE))
    shape = np.exp(-_time_axis(length) / kick_tau_s)
    for start_s in kick_times:
        first = int(round(start_s * SAMPLE_RATE))
        last = min(first + length, n)
        # The hit starts at its peak: the attack of a drum is a step.
        kick[first:last] += _tone(kick_hz, last - first, 1.0,
                                  phase=math.pi / 2.0) * shape[: last - first]
    kick_peak = float(np.max(np.abs(kick)))
    if kick_peak > _EPS:
        kick = kick / kick_peak * _db_to_amp(kick_db)

    data = np.repeat((bass + chord + kick)[:, None], CHANNELS, axis=1)
    data, gains = _normalise(data, "peak", -6.0)
    gain = gains[0]

    events: list[dict[str, Any]] = [
        {
            "type": "music",
            "start_s": 0.0,
            "end_s": DURATION_S,
            "label": "bass line, triad and kick drum",
        }
    ]
    for start_s in kick_times:
        events.append(
            {
                "type": "click",
                "start_s": start_s,
                "end_s": start_s + kick_length_s,
                "label": "kick drum hit, 70 Hz burst",
                "center_hz": kick_hz,
                "band_hz": [40.0, 100.0],
                "amplitude_dbfs": _amp_to_db(_db_to_amp(kick_db) * gain),
            }
        )

    levels = _levels_of(data)
    levels.update(
        {
            "bass_hz": bass_hz,
            "bass_dbfs": _amp_to_db(_db_to_amp(bass_db) * gain),
            "chord_hz_0": chord_hz[0],
            "chord_hz_1": chord_hz[1],
            "chord_hz_2": chord_hz[2],
            "chord_dbfs": _amp_to_db(_db_to_amp(chord_db) * gain),
            "kick_hz": kick_hz,
            "kick_dbfs": _amp_to_db(_db_to_amp(kick_db) * gain),
            "kick_count": float(len(kick_times)),
            "normalisation_gain": gain,
            "bit_depth": float(BIT_DEPTH),
        }
    )
    bands = _band_truths(
        data,
        [
            ("drum", (55.0, 85.0), (0.30, 0.45), "mono", 6.0),
            ("bass", (100.0, 120.0), None, "mono"),
            ("chord", (200.0, 500.0), None, "mono"),
            ("broadband", (20.0, 20000.0), None, "mono"),
        ],
        DURATION_S,
    )
    return _clip(
        "music_mix",
        data,
        events,
        levels,
        ["synthetic", "music", "mixture", "bass", "drum", "rebalance"],
        regions={"drum": [0.30, 0.45], "chord": [0.1, 1.9]},
        bands=bands,
        frequencies=[
            _frequency("bass", bass_hz, search_hz=(100.0, 120.0), tolerance_hz=2.0),
            _frequency("chord_low", chord_hz[0], search_hz=(210.0, 230.0), tolerance_hz=2.0),
            _frequency("chord_mid", chord_hz[1], search_hz=(320.0, 340.0), tolerance_hz=2.0),
            _frequency("chord_high", chord_hz[2], search_hz=(430.0, 450.0), tolerance_hz=2.0),
            _frequency("kick", kick_hz, search_hz=(55.0, 85.0), tolerance_hz=3.0),
        ],
    )


def _build_stereo_phase(rng: np.random.Generator) -> tuple[dict[str, Any], np.ndarray]:
    """A stereo pair whose two channels differ in phase by a quarter period.

    Both channels carry the same 440 Hz tone, the right one a quarter of a
    period later.  The mid and the side therefore carry the same level, the
    mono mix is 3 dB below the channels and the correlation is zero.
    """
    n = int(round(DURATION_S * SAMPLE_RATE))
    tone_hz = 440.0
    tone_db = -6.0
    phase_deg = 90.0

    left = _tone(tone_hz, n, _db_to_amp(tone_db))
    right = _tone(tone_hz, n, _db_to_amp(tone_db), phase=math.radians(phase_deg))
    data = np.stack([left, right], axis=1)
    data, gains = _normalise(data, "peak", -6.0)
    gain = gains[0]

    # The mid and the side of two tones a quarter period apart have the
    # same level: both are the tone over the square root of two.
    both = _amp_to_db(_db_to_amp(tone_db) * gain) - 20.0 * math.log10(math.sqrt(2.0))

    events = [
        {
            "type": "music",
            "start_s": 0.0,
            "end_s": DURATION_S,
            "label": "440 Hz tone, right channel a quarter period later",
        }
    ]
    levels = _levels_of(data)
    levels.update(
        {
            "tone_hz": tone_hz,
            "tone_dbfs": _amp_to_db(_db_to_amp(tone_db) * gain),
            "phase_deg": phase_deg,
            "correlation": 0.0,
            "mid_peak_dbfs": both,
            "side_peak_dbfs": both,
            "normalisation_gain": gain,
            "bit_depth": float(BIT_DEPTH),
        }
    )
    bands = _band_truths(
        data,
        [
            ("mid", (410.0, 470.0), None, "mid"),
            ("side", (410.0, 470.0), None, "side"),
            ("broadband", (20.0, 20000.0), None, "mono"),
        ],
        DURATION_S,
    )
    return _clip(
        "stereo_phase",
        data,
        events,
        levels,
        ["synthetic", "stereo", "phase", "quarter-period", "ms"],
        bands=bands,
        mix="mid",
        frequencies=[
            _frequency(
                "tone",
                tone_hz,
                mix="mid",
                search_hz=(410.0, 470.0),
                tolerance_hz=3.0,
            )
        ],
        phase={
            "name": "inter-channel",
            "degrees": phase_deg,
            "correlation": 0.0,
            "region_s": [0.0, DURATION_S],
            "tolerance_deg": 2.0,
            "tolerance_correlation": 0.01,
        },
    )


def _build_rumble(rng: np.random.Generator) -> tuple[dict[str, Any], np.ndarray]:
    """A programme tone with a low level rumble underneath it.

    The rumble is a 40 Hz sine plus band limited noise between 20 Hz and
    80 Hz - the handling noise of a microphone - and it is more than 20 dB
    below the programme it sits under.
    """
    n = int(round(DURATION_S * SAMPLE_RATE))
    programme_hz, programme_db = 1000.0, -12.0
    rumble_hz, rumble_db = 40.0, -34.0
    rumble_noise_db = -40.0

    programme = _tone(programme_hz, n, _db_to_amp(programme_db))
    rumble_tone = _tone(rumble_hz, n, _db_to_amp(rumble_db))
    rumble_noise = _band_noise(n, 20.0, 80.0, rng, skirt_hz=10.0)
    noise_rms = float(np.sqrt(np.mean(rumble_noise**2)))
    if noise_rms > _EPS:
        rumble_noise = rumble_noise / noise_rms * _db_to_amp(rumble_noise_db)

    data = np.repeat((programme + rumble_tone + rumble_noise)[:, None], CHANNELS, axis=1)
    data, gains = _normalise(data, "peak", -6.0)
    gain = gains[0]

    events = [
        {
            "type": "noise",
            "start_s": 0.0,
            "end_s": DURATION_S,
            "label": "low level rumble, 40 Hz with 20-80 Hz noise",
        },
        {
            "type": "music",
            "start_s": 0.0,
            "end_s": DURATION_S,
            "label": "1 kHz programme tone",
        },
    ]
    levels = _levels_of(data)
    levels.update(
        {
            "programme_hz": programme_hz,
            "programme_dbfs": _amp_to_db(_db_to_amp(programme_db) * gain),
            "rumble_hz": rumble_hz,
            "rumble_tone_dbfs": _amp_to_db(_db_to_amp(rumble_db) * gain),
            "rumble_noise_dbfs": _amp_to_db(_db_to_amp(rumble_noise_db) * gain),
            "normalisation_gain": gain,
            "bit_depth": float(BIT_DEPTH),
        }
    )
    bands = _band_truths(
        data,
        [
            ("rumble", (20.0, 80.0), None, "mono"),
            ("programme", (950.0, 1050.0), None, "mono"),
            ("broadband", (20.0, 20000.0), None, "mono"),
        ],
        DURATION_S,
    )
    band_level = {str(band["name"]): float(band["level_dbfs"]) for band in bands}
    rumble_below = band_level["programme"] - band_level["rumble"]
    return _clip(
        "rumble",
        data,
        events,
        levels,
        ["synthetic", "rumble", "noise", "low-frequency", "high-pass"],
        regions={"rumble": [0.0, DURATION_S]},
        bands=bands,
        frequencies=[
            _frequency("rumble", rumble_hz, search_hz=(30.0, 50.0), tolerance_hz=2.0),
            _frequency(
                "programme",
                programme_hz,
                search_hz=(950.0, 1050.0),
                tolerance_hz=5.0,
            ),
        ],
        derived=[
            {
                "name": "rumble_below_programme",
                "value": round(rumble_below, 6),
                "tolerance": 0.3,
                "terms": [
                    {"band": "programme", "sign": 1.0},
                    {"band": "rumble", "sign": -1.0},
                ],
            }
        ],
    )


def _build_pitch_drift(rng: np.random.Generator) -> tuple[dict[str, Any], np.ndarray]:
    """A tone whose pitch drifts slowly upwards.

    The clip holds a 440 Hz tone for the first 0.4 s, a linear glide from
    440 Hz to 480 Hz over the next 1.2 s and the 480 Hz tone for the rest.
    The phase is continuous, so the glide has no click.
    """
    n = int(round(DURATION_S * SAMPLE_RATE))
    start_hz, end_hz = 440.0, 480.0
    glide_start_s, glide_end_s = 0.4, 1.6
    tone_db = -6.0

    times = _time_axis(n)
    frequency = np.full(n, start_hz, dtype=np.float64)
    glide = (times >= glide_start_s) & (times < glide_end_s)
    slope = (end_hz - start_hz) / (glide_end_s - glide_start_s)
    frequency[glide] = start_hz + slope * (times[glide] - glide_start_s)
    frequency[times >= glide_end_s] = end_hz
    phase = 2.0 * math.pi * np.cumsum(frequency) / float(SAMPLE_RATE)
    tone = _db_to_amp(tone_db) * np.sin(phase)

    data = np.repeat(tone[:, None], CHANNELS, axis=1)
    data, gains = _normalise(data, "peak", -6.0)
    gain = gains[0]

    events = [
        {
            "type": "music",
            "start_s": 0.0,
            "end_s": DURATION_S,
            "label": "tone with a drifting pitch, 440 Hz to 480 Hz",
        }
    ]
    levels = _levels_of(data)
    levels.update(
        {
            "start_hz": start_hz,
            "end_hz": end_hz,
            "drift_hz": end_hz - start_hz,
            "drift_hz_per_s": slope,
            "drift_start_s": glide_start_s,
            "drift_end_s": glide_end_s,
            "tone_dbfs": _amp_to_db(_db_to_amp(tone_db) * gain),
            "normalisation_gain": gain,
            "bit_depth": float(BIT_DEPTH),
        }
    )
    bands = _band_truths(
        data,
        [
            ("start", (410.0, 470.0), (0.0, glide_start_s), "mono"),
            ("end", (460.0, 500.0), (glide_end_s, DURATION_S), "mono"),
        ],
        DURATION_S,
    )
    return _clip(
        "pitch_drift",
        data,
        events,
        levels,
        ["synthetic", "pitch", "drift", "glide", "wow"],
        regions={
            "start": [0.0, glide_start_s],
            "glide": [glide_start_s, glide_end_s],
            "end": [glide_end_s, DURATION_S],
        },
        bands=bands,
        frequencies=[
            _frequency(
                "start",
                start_hz,
                region=(0.0, glide_start_s),
                search_hz=(410.0, 470.0),
                tolerance_hz=3.0,
            ),
            _frequency(
                "end",
                end_hz,
                region=(glide_end_s, DURATION_S),
                search_hz=(460.0, 500.0),
                tolerance_hz=3.0,
            ),
        ],
    )


#: The recipes, in the order they are written to the manifest.
BUILDERS: tuple[Callable[[np.random.Generator], tuple[dict[str, Any], np.ndarray]], ...] = (
    _build_clicks,
    _build_hum50,
    _build_hiss,
    _build_pink_hiss,
    _build_sibilance,
    _build_plosive,
    _build_reverb,
    _build_clipped,
    _build_silence,
    _build_stereo_mid,
    _build_stereo_side,
    _build_voice_hiss,
    _build_music_mix,
    _build_stereo_phase,
    _build_rumble,
    _build_pitch_drift,
)


# --------------------------------------------------------------------------
# The build
# --------------------------------------------------------------------------
def build_all(output_dir: Path | None = None, write: bool = True) -> list[dict[str, Any]]:
    """Build every clip and the manifest entries.

    Parameters
    ----------
    output_dir:
        Directory the WAV files are written to.  ``None`` means
        ``corpus/audio/test``.
    write:
        When ``False`` nothing is written to disk and the checksum of every
        entry is left empty.

    Returns
    -------
    list
        The manifest entries, in the order they appear in the manifest.
    """
    target = Path(output_dir) if output_dir is not None else AUDIO_DIR
    entries: list[dict[str, Any]] = []
    for index, builder in enumerate(BUILDERS):
        # Every clip gets its own generator so the noise of one clip can
        # never influence another one.
        rng = np.random.default_rng(SEED + index)
        entry, data = builder(rng)
        if write:
            path = target / f"{entry['id']}.wav"
            _write_wav(path, data, SAMPLE_RATE, BIT_DEPTH)
            entry["sha256"] = _sha256(path)
        else:
            entry["sha256"] = ""
        entries.append(entry)
    return entries


def main(argv: list[str] | None = None) -> int:
    """Command line entry point."""
    parser = argparse.ArgumentParser(description="Build the Auralis golden corpus.")
    parser.add_argument("--out", type=Path, default=None, help="output directory for the WAV files")
    parser.add_argument("--check", action="store_true", help="only verify the files on disk")
    parser.add_argument("--quiet", action="store_true", help="do not print the summary")
    args = parser.parse_args(argv)

    entries = build_all(output_dir=args.out, write=not args.check)
    if args.check:
        directory = Path(args.out) if args.out is not None else AUDIO_DIR
        missing = [
            entry["id"] for entry in entries if not (directory / f"{entry['id']}.wav").exists()
        ]
        if missing:
            print("missing clips: " + ", ".join(missing), file=sys.stderr)
            return 1
        print(f"{len(entries)} clips present")
        return 0

    MANIFEST_PATH.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")

    if not args.quiet:
        print(f"wrote {len(entries)} clips to {AUDIO_DIR}")
        print(f"wrote {MANIFEST_PATH}")
        for entry in entries:
            print(f"  {entry['id']:<14} {entry['duration_s']:>4.1f} s  {entry['sha256'][:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
