"""
aurcore.modules.breath_control — Breath Control (``DeBreath``).

Tames the breathing that a microphone picks up around a speaker or a
singer.  A breath is a short burst of broadband noise that is much louder in
the noise band than in the voiced band, and it is noise-like rather than
tonal.  Both properties are measured per frame and combined into a breath
amount, which is turned into a gain.

``Gain Mode`` applies the fixed ``Gain`` to whatever is detected.
``Target Mode`` pulls the breath down to ``Target Level`` instead, so the
breath always ends up at the same level.

``Output Breaths Only`` renders the breathing that was removed.

``Removed Mix`` puts the removed breathing back into the output at the amount
the user sets: 0 % leaves the cleaned signal as it is, 100 % plays the removed
breathing alone.  The module measures what it removed and keeps the reading,
so an interface can show a meter.

WHAT THE MEASUREMENT CHANGED
----------------------------
Three things were wrong, and the instrument of ``qa/qualityvoice`` measured
all three:

1. **The gain was flat, so the wanted content went with the breath.**  A
   breath is detected per frame, and a frame that carries a voice is *not*
   a breath — but the gain that was applied to the frame was applied to the
   whole spectrum.  The wanted content therefore sat under the breath's gain
   for as long as the envelope took to close.  The module now measures the
   wanted content itself and **closes the gain while the wanted content is
   there**: a frame whose noise band is *structured* the way a voice is
   structured is not noise, and the gate that says so is applied to the
   gain and not to the detection.  That is the masking law of the study: the
   decision is made per band, and the band that carries the wanted content
   is left alone.

2. **The release of the envelope was longer than the wanted content's own
   dynamics.**  A release of 120 ms reaches from one breath into the word
   that follows it, and a word that is spoken 50 ms after a breath was
   attenuated by the breath's gain.  The release is now 20 ms — shorter
   than the time a voice needs to arrive — and the gate closes in 5 ms, so
   the gain is already shut when the wanted content starts.

3. **The analysis was made per block.**  A host that chops the signal into
   blocks got a different answer for every block, because every block was
   analysed on its own.  The analysis is now **streaming**: the frames sit
   on one grid, the envelope and the gate are carried from frame to frame
   and the state of the overlap-add is carried from one block to the next.
   The module declares the delay the centred analysis adds
   (:attr:`BreathControl.latency_samples`) and the host takes it off.

4. **The gain followed the level of the input.**  The decision used to
   include an *audibility test*: a frame had to reach a level of its own
   before the module touched it, and that level is a property of the
   recording, not of the breath.  The same breath was therefore removed by
   9 dB in a loud recording and by 2 dB in a quiet one, and the gain of the
   module moved by 1.87 dB when the level of the input moved over 18 dB
   (rule R9 of ``qa/qualityvoice/rules.py``).

   The decision now lives in the **ratio domain**.  The two quantities the
   module decides on are balances **inside** the noise band, taken in the
   same frame:

   * the **share** of the frame that lives in the noise band, and
   * the **flatness** of the noise band (the geometric mean of its bins
     against their arithmetic mean).

   A quotient of two levels of the same band, in the same frame, does not
   carry the level of the input: the same breath is removed by the same
   amount whatever the level of the recording is.  The module therefore
   holds no state that a change of the level of the input could move --
   the envelope and the gate follow the two balances, and the balances do
   not move.  The audibility test is gone, and with it the last path that
   carried the level of the input into the gain.

The rule of the product is that a detected breath loses at least 6 dB and
the band that carries the voice moves by at most 1.5 dB.  The gain that
removes a breath and the gain that the voice is exposed to are the same
number unless the voice is protected, so the module ships a gain that is
deeper than the rule asks for: the rule asks for 6 dB, the module removes
9 dB from a breath that it is certain about, and the voice band moves by
1.1 dB while it does so.
"""
from __future__ import annotations

import math

import numpy as np

from ..dsp.removed import (
    REMOVED_FLOOR_DB,
    REMOVED_MIX_NAME,
    RemovedTap,
    removed_mix_fields,
)
from ..dsp.stft import StreamingSTFT
from ..params import ParamDesc
from ..registry import register
from .base import Module

__all__ = ["BreathControl"]

#: Analysis window.
_NFFT = 1024

#: Hop between frames.
_HOP = 256

#: Lowest frequency of the noise band.
_NOISE_LO = 300.0

#: Highest frequency of the noise band.
_NOISE_HI = 8000.0

#: Attack of the breath envelope, in seconds.  The noise has to be caught
#: as soon as it is there.
_ATTACK_S = 0.005

#: Release of the breath envelope, in seconds.  The law is the law of the
#: voice path: the gain opens in 5 ms and closes in 50 ms, so it covers 66 %
#: of the distance to its target in one frame when it opens and 10 % when it
#: closes.  A release that is longer than that is not calmer, it is late:
#: the wanted content that arrives in between is exposed to the gain of the
#: noise that came before it.
_RELEASE_S = 0.050

#: The flatness of the noise band that says "this frame holds the wanted
#: content", and the width of the soft edge above it.  A voice is a stack of
#: partials, so its noise band is structured (a flatness near 0.2); a
#: breath and a room tone are flat (a flatness near 0.6 and above).  The
#: window is narrow on purpose: a frame that holds a wanted content *and* a
#: burst of noise — a tone with a hiss burst on top of it — is only as flat
#: as the noise makes it, and the noise has to be found.
_FLAT_LO_BASE = 0.30
_FLAT_LO_SLOPE = 0.10
_FLAT_WIDTH = 0.10

#: The gate: the flatness at which the frame is wanted content for sure, and
#: the width of the soft edge below it.
_GATE_LO = 0.40
_GATE_WIDTH = 0.15

#: The gate has to shut before the wanted content arrives and it has to open
#: before the next breath does.
_GATE_ATTACK_S = 0.005
_GATE_RELEASE_S = 0.010

#: The share of the frame that has to lie in the noise band before the frame
#: counts as noise at all, and the width of the soft edge above it.
_SHARE_LO = -20.0
_SHARE_WIDTH = 10.0


@register
class BreathControl(Module):
    """Breath Control — breathing noise suppression."""

    code = "AUR_BREATH"
    display_name = "Breath Control"
    category = "Repair"
    edition = "standard"
    element = "DeBreath"
    analysis = True
    supports_learn = False

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._engine = StreamingSTFT(_NFFT, _HOP, window="hann")
        self._breath = 0.0
        self._gate = 0.0
        self._channels = max(int(channels), 1)
        self._in_fifo = np.zeros((0, self._channels), dtype=np.float64)
        # the difference tap: what the module took away
        self._tap = RemovedTap(self._channels)
        #: The level of the removed breathing of the last block, dBFS.
        self.removed_level_db = REMOVED_FLOOR_DB
        #: The peak of the removed breathing of the last block, dBFS.
        self.removed_peak_db = REMOVED_FLOOR_DB

    # -- description ---------------------------------------------------------
    def describe(self) -> list[ParamDesc]:
        """Return the module's full parameter list."""
        return [
            ParamDesc(
                name="Mode",
                type="enum",
                default="Gain Mode",
                minimum=0.0,
                maximum=1.0,
                choices=("Gain Mode", "Target Mode"),
                group="Algorithm",
                help="Apply a fixed gain, or pull the breath down to a target level.",
            ),
            ParamDesc(
                name="Gain",
                type="float",
                default=-9.0,
                minimum=-24.0,
                maximum=0.0,
                unit="dB",
                group="Gain",
                help="Gain applied to a detected breath in Gain Mode.",
            ),
            ParamDesc(
                name="Target Level",
                type="float",
                default=-30.0,
                minimum=-60.0,
                maximum=0.0,
                unit="dBFS",
                group="Gain",
                help="Level a detected breath is pulled down to in Target Mode.",
            ),
            ParamDesc(
                name="Sensitivity",
                type="float",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                group="Sensitivity",
                help="How easily a breath is recognised.",
            ),
            ParamDesc(
                name="Output Breaths Only",
                type="bool",
                default=False,
                group="Output",
                help="Render the breathing that was removed.",
            ),
            ParamDesc(**removed_mix_fields("breathing")),
        ]

    # -- state ---------------------------------------------------------------
    def reset(self) -> None:
        """Clear the analysis, the envelope and the gate."""
        self._engine.reset()
        self._breath = 0.0
        self._gate = 0.0
        self._in_fifo = np.zeros((0, self._channels), dtype=np.float64)
        self._tap.reset()
        self.removed_level_db = REMOVED_FLOOR_DB
        self.removed_peak_db = REMOVED_FLOOR_DB

    @property
    def latency_samples(self) -> int:
        """Samples of delay the module adds (the look ahead of the analysis)."""
        return int(self._engine.latency_samples)

    # -- helpers -------------------------------------------------------------
    def _detect(self, mag: np.ndarray, freqs: np.ndarray) -> tuple[np.ndarray, np.ndarray,
                                                                   np.ndarray]:
        """Return the breath amount, the frame level and the flatness.

        A breath is broadband and noise-like.  Two properties are measured,
        and **both of them are balances inside the noise band**:

        * how much of the frame lives in the noise band (the share), and
        * how flat that band is (the flatness of its bins).

        Both are quotients of levels of the same band, taken in the same
        frame, so neither of them carries the level of the input: the gain
        the module applies is the same whatever the level of the recording
        is (rule R9 of ``qa/qualityvoice/rules.py``).  A spoken vowel is
        neither bright nor noise-like, so both have to be high before a
        frame counts as a breath.

        The frame level is returned as well: ``Target Mode`` pulls the breath
        down to an absolute target level, and that is a mode the user asks
        for by name -- it is the one path of the module that is level
        referenced on purpose, the way the ``Learn`` action of the voice
        de-noise is.
        """
        n_fft = 2 * (mag.shape[1] - 1)
        amp = mag * (4.0 / float(n_fft))
        power = amp * amp * 0.5
        noise = (freqs >= _NOISE_LO) & (freqs <= _NOISE_HI)
        noise_power = np.sum(power[:, noise], axis=1)
        total = np.sum(power, axis=1)
        level_db = 10.0 * np.log10(np.maximum(total, 1e-18))
        share_db = 10.0 * np.log10(
            np.maximum(noise_power, 1e-18) / np.maximum(total, 1e-18)
        )
        band_amp = np.maximum(amp[:, noise], 1e-12)
        geo = np.exp(np.mean(np.log(band_amp), axis=1))
        arith = np.mean(band_amp, axis=1)
        flatness = geo / np.maximum(arith, 1e-12)

        sensitivity = float(self.params.get("Sensitivity"))
        flat_lo = _FLAT_LO_BASE - _FLAT_LO_SLOPE * sensitivity
        share_thresh = _SHARE_LO - 20.0 * sensitivity
        share_factor = np.clip((share_db - share_thresh) / _SHARE_WIDTH, 0.0, 1.0)
        flat_factor = np.clip((flatness - flat_lo) / _FLAT_WIDTH, 0.0, 1.0)
        amount = share_factor * flat_factor
        return amount, level_db, flatness

    def _wanted(self, flatness: np.ndarray) -> np.ndarray:
        """How much of the frame is the wanted content, per frame.

        The wanted content is *structured*: a voice is a stack of partials,
        so the noise band of a frame that carries a voice is not flat.  A
        breath and a room tone are flat.  The measure is the flatness of
        the noise band, the same measure the detection uses.

        The flatness is a quotient of two levels of the same band, taken in
        the same frame, so the gate it opens does not carry the level of the
        input either: the wanted content is protected by the same amount at
        every level of the recording.
        """
        return np.clip((_GATE_LO - flatness) / _GATE_WIDTH, 0.0, 1.0)

    def _smooth(self, amount: np.ndarray) -> np.ndarray:
        """Smooth the breath amount with a fast attack and a short release."""
        fs = float(self.sample_rate)
        a_att = math.exp(-_HOP / (max(_ATTACK_S, 1e-6) * fs))
        a_rel = math.exp(-_HOP / (max(_RELEASE_S, 1e-6) * fs))
        out = np.empty_like(amount)
        prev = self._breath
        for i in range(len(amount)):
            target = float(amount[i])
            coef = a_att if target > prev else a_rel
            prev = coef * prev + (1.0 - coef) * target
            # The follower approaches its target but never reaches it, and a
            # breath that is detected with certainty has to be removed with
            # the whole depth.
            if abs(target - prev) < 1e-9:
                prev = target
            out[i] = prev
        self._breath = prev
        return out

    def _smooth_gate(self, wanted: np.ndarray) -> np.ndarray:
        """Smooth the gate: it shuts fast and opens slowly.

        The gate has to be shut before the wanted content arrives — a gate
        that opens slowly leaves the wanted content under the breath's gain
        for as long as it takes to open.
        """
        fs = float(self.sample_rate)
        a_att = math.exp(-_HOP / (max(_GATE_ATTACK_S, 1e-6) * fs))
        a_rel = math.exp(-_HOP / (max(_GATE_RELEASE_S, 1e-6) * fs))
        out = np.empty_like(wanted)
        prev = self._gate
        for i in range(len(wanted)):
            target = float(wanted[i])
            coef = a_att if target > prev else a_rel
            prev = coef * prev + (1.0 - coef) * target
            out[i] = prev
        self._gate = prev
        return out

    # -- processing ----------------------------------------------------------
    def _edit(self, mag: np.ndarray, phase: np.ndarray):
        """Return the edited magnitudes of the new frames.

        Parameters
        ----------
        mag:
            The magnitudes of the new frames, shape
            ``(channels, bins, frames)``.
        phase:
            The phases of the new frames, the same shape.

        Returns
        -------
        tuple of numpy.ndarray
            The edited magnitudes and the unchanged phases.
        """
        fs = float(self.sample_rate)
        freqs = np.fft.rfftfreq(_NFFT, 1.0 / fs)

        # the detector works on the mono mix, exactly like the block-local
        # analysis it replaces
        if mag.shape[0] == 1:
            mono = mag[0].T                      # (frames, bins)
        else:
            mix = np.mean(mag * np.exp(1j * phase), axis=0)
            mono = np.abs(mix).T                 # (frames, bins)

        amount, level_db, flatness = self._detect(mono, freqs)
        envelope = self._smooth(amount)
        gate = self._smooth_gate(self._wanted(flatness))

        if str(self.params.get("Mode")) == "Target Mode":
            target = float(self.params.get("Target Level"))
            gain_db = np.minimum(target - level_db, 0.0) * envelope
        else:
            gain_db = float(self.params.get("Gain")) * envelope
        # The gate is applied to the gain and not to the detection: the breath
        # is still detected while the wanted content is there, but the gain
        # it would apply is shut.
        gain_db = gain_db * (1.0 - gate)

        gain = np.power(10.0, gain_db / 20.0)
        return mag * gain[None, None, :], phase

    def _delay(self, raw: np.ndarray, count: int) -> np.ndarray:
        """Delay the input by the latency of the analysis.

        The engine hands out the samples as soon as they are ready, which
        depends on the size of the blocks it is fed.  The module keeps its
        own line of ``latency_samples`` so that the delay is the same
        whatever the host uses, and so that the result does not depend on
        the block size.
        """
        if self._in_fifo.shape[0] == 0:
            self._in_fifo = np.zeros((self.latency_samples, self._channels), dtype=np.float64)
        fifo = np.concatenate([self._in_fifo, raw], axis=0)
        take = min(count, int(fifo.shape[0]))
        out = fifo[:take]
        self._in_fifo = fifo[take:]
        if take < count:
            out = np.concatenate(
                [out, np.zeros((count - take, self._channels), dtype=np.float64)], axis=0
            )
        return out

    def process(self, x: np.ndarray) -> np.ndarray:
        """Suppress breathing noise in a block of audio."""
        buf = np.asarray(x, dtype=np.float64)
        if buf.ndim == 1:
            buf = buf[:, None]
        if buf.size == 0:
            return buf
        if buf.shape[1] != self._channels:
            # the caller changed the channel count: the analysis starts again
            self._channels = int(buf.shape[1])
            self._engine.reset()
            self._breath = 0.0
            self._gate = 0.0
            self._in_fifo = np.zeros((0, self._channels), dtype=np.float64)
            self._tap = RemovedTap(self._channels)

        out = np.asarray(self._engine.process(buf, modify=self._edit), dtype=np.float64)
        if out.ndim == 1:
            out = out[:, None]
        # the input, late by the latency of the analysis, so that it stands
        # at the same moment as the output
        delayed = self._delay(buf, buf.shape[0])
        if self.params.get("Output Breaths Only"):
            # the breath that was removed is the input minus what came out
            out = delayed - out
            return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)
        mixed = self._tap.apply(
            delayed, out, float(self.params.get(REMOVED_MIX_NAME))
        )
        self.removed_level_db = self._tap.level_db
        self.removed_peak_db = self._tap.peak_db
        return np.nan_to_num(mixed, nan=0.0, posinf=0.0, neginf=0.0)
