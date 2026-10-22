"""qa.quality.sidebands — is the leftover error amplitude modulation of the tone?"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import SR, GainProbe, case_tone_noise  # noqa: E402
from aurcore.dsp.stft import STFT  # noqa: E402

case = case_tone_noise()
x = case.mixed

module = GainProbe(SR, 1)
module.learn(case.noise[:, None])
module.process(case.noise[:, None])
module.clear()
out = np.asarray(module.process(case.mixed[:, None]))[:, 0]
err = out - case.clean

# spectrum of the error around 1 kHz
n = err.size
spec = np.abs(np.fft.rfft(err * np.hanning(n))) / (n / 2)
freqs = np.fft.rfftfreq(n, 1.0 / SR)
print("error spectrum around the 1 kHz tone:")
for lo, hi in [(600, 900), (900, 1100), (1100, 1400), (1400, 2000), (2000, 4000)]:
    sel = (freqs >= lo) & (freqs < hi)
    print(f"   {lo:5d}-{hi:<5d} Hz  mean |E| = {spec[sel].mean():.6f}")

clean_spec = np.abs(np.fft.rfft(case.clean * np.hanning(n))) / (n / 2)
sel = (freqs >= 900) & (freqs < 1100)
print(f"   clean tone level in 900-1100 Hz: {clean_spec[sel].mean():.6f}")

# gain curve around the tone
gain = module.gain_curve
print("\ngain curve around the tone (dB), frames 40..49, bins 8..26:")
for b in range(8, 27):
    col = gain[40:50, b]
    print(f"   bin {b:3d} ({b*SR/720:7.1f} Hz)  mean {col.mean():+7.2f}  std {col.std():6.2f}"
          f"  min {col.min():+7.2f}  max {col.max():+7.2f}")

print("\ngain curve in the noise band (bins 120..140), frames 40..49:")
for b in range(120, 128):
    col = gain[40:50, b]
    print(f"   bin {b:3d} ({b*SR/720:7.1f} Hz)  mean {col.mean():+7.2f}  std {col.std():6.2f}")

# how fast does the gain change frame to frame at the tone skirts?
d = np.abs(np.diff(gain[:, 14:20], axis=0))
print(f"\nmean |frame-to-frame gain change| at the tone skirts: {d.mean():.3f} dB")
d2 = np.abs(np.diff(gain[:, 120:200], axis=0))
print(f"mean |frame-to-frame gain change| in the noise band  : {d2.mean():.3f} dB")
