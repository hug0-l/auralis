"""qa.quality.trace — follow one tone through the module, stage by stage."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import (SR, GainProbe, case_voice_hiss, case_music_mixture,  # noqa: E402
                     tone_amplitude)
from aurcore.dsp.stft import STFT  # noqa: E402
from aurcore.modules.spectral_de_noise import broadband_floor  # noqa: E402

VARIANTS = {
    "as shipped": {},
    "no whitening": {"Whitening": 0.0},
    "no enhancement": {"Enhancement": 0.0},
    "no noise-floor feats": {"Masking": 0.0, "Enhancement": 0.0,
                             "Whitening": 0.0, "Noise Floor Synthesis": 0.0},
}


def trace(case) -> None:
    """Print what happens to every tone of the case."""
    print("=" * 96)
    print(f"case {case.name}")
    for label, params in VARIANTS.items():
        module = GainProbe(SR, 1)
        for k, v in params.items():
            module.params.set(k, v)
        module.learn(case.noise[:, None])
        module.process(case.noise[:, None])
        module.clear()
        out = np.asarray(module.process(case.mixed[:, None]))[:, 0]
        gain = module.gain_curve
        engine = STFT(720, 180, window="hann")
        mag, _ = engine.analyze_block(case.mixed, center=True)
        mag2, _ = engine.analyze_block(out, center=True)
        sm = 20 * np.log10(np.maximum(mag.T, 1e-12))
        broad = broadband_floor(sm)
        print(f"  -- {label}")
        for freq in case.tones:
            a_in = tone_amplitude(case.mixed, freq)
            a_out = tone_amplitude(out, freq)
            shift = 20 * np.log10(max(a_out, 1e-12) / max(a_in, 1e-12))
            b = int(round(freq * 720 / SR))
            tonal = np.clip((sm[:, b] - broad[:, b]) / 6.0, 0.0, 1.0)
            print(f"     {freq:7.1f} Hz  amp {a_in:.6f} -> {a_out:.6f}  ({shift:+.2f} dB)"
                  f"  gain@bin {gain[:, b].mean():+7.2f} dB  tonal_mask {tonal.mean():.2f}"
                  f"  sm-broad {(sm[:, b]-broad[:, b]).mean():6.2f} dB")


if __name__ == "__main__":
    trace(case_voice_hiss())
    trace(case_music_mixture())
