#!/usr/bin/env python3
"""
qa.make_demo_memo — build the damaged phone memo the measurements use.

The material is **ours**: a synthetic voice of the project, damaged the way a
phone memo in a bad room is damaged.  Nothing of anybody else's is in it.

The damage is the damage of a real memo, not a laboratory tone:

* a **mains hum** of 50 Hz with its harmonics, drifting a little;
* a **hiss** of a noisy preamp, which rises with the level of the voice;
* a few **clicks** and one **dropout** (a bumped desk);
* a **DC offset** of a faulty input stage;
* one loud passage that was **clipped** (the memo moved too close).

The result is a hard case: the hum is *louder* than the voice, so the signal
to noise of the voice against the hum is **-0.84 dB**.  The synthetic cases
of the corpus are easier than that, and a measurement on an easier case does
not say what a repair does on a real one.

The file is written to ``corpus/audio/test/voice_memo_damaged.wav``.  It is
not a case of the corpus manifest: the manifest is a generated artefact with
its own annotations, and this file is a measurement subject.

    python qa/make_demo_memo.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RATE = 48000
DURATION = 6.0
SEED = 20261022


def build(rate: int = RATE, duration: float = DURATION, seed: int = SEED) -> np.ndarray:
    """Return the damaged memo, shape ``(n_samples, 2)``."""
    sys.path.insert(0, str(ROOT / "qa" / "qualityvoice"))
    from voice_signal import synth_voice  # type: ignore

    count = int(rate * duration)
    t = np.arange(count) / rate
    rng = np.random.default_rng(seed)

    voice = np.asarray(synth_voice(sample_rate=rate), dtype=np.float64)
    if voice.ndim == 1:
        voice = np.stack([voice, voice], axis=1)
    else:
        voice = voice[:, :2]
    if voice.shape[0] < count:
        voice = np.pad(voice, ((0, count - voice.shape[0]), (0, 0)))
    voice = (voice[:count] * 0.22).copy()

    # the mains hum: 50 Hz with its harmonics, drifting a little
    drift = 0.03 * np.sin(2 * np.pi * 0.11 * t)
    hum = np.zeros(count)
    for harmonic, level in ((1, 1.0), (2, 0.55), (3, 0.30), (4, 0.16), (5, 0.08)):
        hum += level * np.sin(2 * np.pi * 50.0 * harmonic * t + drift * harmonic)
    hum = hum / 1.9 * 0.020

    # the hiss of a noisy preamp: it rises with the level
    level = np.abs(voice).max(axis=1)[:, None]
    hiss = rng.standard_normal((count, 2)) * 0.0035
    hiss *= 1.0 + 0.8 * level / max(float(level.max()), 1e-9)

    # the clicks and the dropout
    clicks = np.zeros((count, 2))
    for position, height in ((0.83, 0.9), (2.41, 0.7), (3.92, 0.85), (5.33, 0.6)):
        index = int(position * rate)
        width = 24
        shape = np.hanning(width * 2)[:width]
        clicks[index : index + width, 0] += height * shape
        clicks[index : index + width, 1] += height * shape * 0.7
    dropout = np.zeros((count, 2))
    gap = slice(int(4.30 * rate), int(4.36 * rate))
    dropout[gap, :] = 1.0

    # the DC offset and the clipped passage
    offset = 0.0035
    clipped = voice.copy()
    loud = slice(int(1.60 * rate), int(2.30 * rate))
    clipped[loud] *= 3.2

    mixed = voice + hum[:, None] + hiss + clicks * 0.5 + dropout * 0.4 + offset
    mixed[loud] = np.clip(clipped[loud], -1.0, 1.0)
    peak = float(np.max(np.abs(mixed)))
    return mixed / peak * 0.92


def main(argv: list[str] | None = None) -> int:
    """Write the memo and say what is wrong with it."""
    parser = argparse.ArgumentParser(
        prog="make_demo_memo.py",
        description="Build the damaged phone memo the measurements use.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "corpus" / "audio" / "test" / "voice_memo_damaged.wav",
        help="where the memo is written",
    )
    args = parser.parse_args(argv)

    mixed = build()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    import soundfile as sf

    sf.write(str(args.out), mixed.astype(np.float32), RATE, subtype="PCM_16")
    print(f"wrote {args.out}")

    sys.path.insert(0, str(ROOT))
    from corpus import measure

    bands = {
        "the voice 120-3000 Hz": (120.0, 3000.0),
        "the hum 45-55 Hz": (45.0, 55.0),
        "the rumble 20-80 Hz": (20.0, 80.0),
        "the hiss 5000-20000 Hz": (5000.0, 20000.0),
    }
    levels = measure.band_levels_db(mixed, RATE, bands)
    print("what is wrong with it, measured with the corpus's own ruler:")
    for name, level in levels.items():
        print(f"   {name:<26} {level:>9.3f} dBFS")
    snr = levels["the voice 120-3000 Hz"] - levels["the hum 45-55 Hz"]
    print(f"   the voice against the hum: {snr:+.2f} dB")
    print(f"   the DC offset: {float(np.mean(mixed.mean(axis=1))) * 1000:.3f} mV")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
