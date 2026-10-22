"""
aurcore.modules.de_reverb — reduce the reverberant field of a recording.

The module estimates, per frequency band, how much of the signal is a
*sustained* (reverberant) component rather than a direct one, and attenuates
that part.  Four bands are used, each with its own strength, so the tail can
be tamed where it actually lives (usually the low and low-mid bands).

Method
------
1. A short-time Fourier transform gives the magnitude and phase of the block.
2. The magnitude is split into four bands.  For every band and every frame the
   band energy is tracked twice:
     * a *fast* envelope  — short window, follows the direct transients;
     * a *slow* envelope  — long window whose release time is set by
       ``Tail Length`` (an RT-60 proxy), follows the sustained floor.
   The ratio between them says how much of that band is reverb.  Both
   trackers carry their state from one block to the next, so a host that
   renders in small blocks reaches the same decision as one that renders the
   whole file in a single block.
3. The reverb part is attenuated by ``Reduction`` scaled with the band
   strength; the gain curve is smoothed in time and frequency to avoid
   artefacts (``Artifact Smoothing``).
4. ``Enhance Dry Signal`` compensates the loss of the direct component.
5. ``Output Reverb Only`` renders the removed reverberant component instead of
   the cleaned signal — the same analysis-output idiom the other modules use.
6. ``Removed Mix`` puts the removed reverberant field back into the output at
   the amount the user sets: 0 % leaves the cleaned signal as it is, 100 %
   plays the removed field alone.  The module measures what it removed and
   keeps the reading, so an interface can show a meter.

Block coherence
---------------
The analysis runs on a :class:`aurcore.dsp.stft.StreamingSTFT` engine, whose
frames sit on the same grid whatever the size of the block the host hands
over, and every measurement the module makes (the two envelope trackers, the
gain history) is carried from frame to frame.  A host that renders in small
blocks therefore reaches the same decision as one that renders the whole file
in a single block.

The gain curve is smoothed with a kernel that reaches over the frames on
both sides of the frame it works on, so the smoothing line is shifted by a
fixed number of frames and the module declares that shift as its latency
(see :attr:`DeReverb.latency_samples`); the host compensates it.
"""
from __future__ import annotations

import numpy as np

from ..dsp.removed import (
    REMOVED_FLOOR_DB,
    REMOVED_MIX_NAME,
    RemovedTap,
    removed_mix_fields,
)
from ..dsp.stft import StreamingSTFT
from ..modules.base import Module
from ..params import ParamDesc
from ..registry import register

_N_FFT = 2048
_HOP = 512

# Band edges in Hz: low, low-mid, high-mid, high.  The last entry is the
# Nyquist frequency and is filled in at run time.
_BAND_EDGES = (0.0, 250.0, 2000.0, 8000.0, 0.0)

#: The widest smoothing kernel, in frames (half of its length).
_MAX_WIDTH = 4

#: How many frames of gain history the smoothing keeps.  The kernel reaches
#: over ``_MAX_WIDTH`` frames on both sides of the frame it works on, and it
#: is centred on a frame that is already in the past, so the history holds
#: twice that many frames.
_HISTORY = 2 * _MAX_WIDTH

#: How many samples are handed to the analysis engine at a time.  The engine
#: holds a limited number of frames in flight, so a long block is fed in
#: pieces.  The frame grid does not depend on the size of the piece.
_FEED_CHUNK = 1024


def _envelope(
    values: np.ndarray,
    attack: float,
    release: float,
    state: float | None = None,
) -> np.ndarray:
    """One-pole envelope follower with separate attack and release rates.

    ``state`` is the value the tracker had when it was last called; the
    recursion continues from it, so a tracker that runs over a stream of
    blocks behaves like a tracker that runs over the whole stream.
    """
    if values.size == 0:
        return values
    out = np.empty_like(values)
    prev = float(values[0]) if state is None else float(state)
    for i, v in enumerate(values):
        coeff = attack if v > prev else release
        prev = prev + coeff * (float(v) - prev)
        out[i] = prev
    return out


@register
class DeReverb(Module):
    """Reduce the reverberant component of a recording."""

    code = "AUR_DEREVERB"
    display_name = "De-Reverb"
    category = "Repair"
    edition = "standard"
    element = "Dereverb"
    analysis = True

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        # the streaming analysis: one frame grid for every block size
        self._engine = StreamingSTFT(_N_FFT, _HOP, window="hann")
        self._channels = max(int(channels), 1)
        # the tracker state, one entry per channel and band
        self._fast_env: list[dict[int, float]] = []
        self._slow_env: list[dict[int, float]] = []
        # the raw gain of the last frames, the history of the smoother
        self._gain_history: np.ndarray | None = None
        # the line that keeps the delay the same for every block size
        self._fifo = np.zeros((0, self._channels), dtype=np.float64)
        # the difference tap: what the module took away
        self._tap = RemovedTap(self._channels)
        #: The level of the removed reverberant field of the last block, dBFS.
        self.removed_level_db = REMOVED_FLOOR_DB
        #: The peak of the removed reverberant field of the last block, dBFS.
        self.removed_peak_db = REMOVED_FLOOR_DB
        self._last_reduction = None

    # -- contract -------------------------------------------------------------
    def describe(self) -> list[ParamDesc]:
        return [
            ParamDesc(name="Reduction", type="float", default=0.5, minimum=0.0, maximum=1.0,
                      group="Reduction", help="How much of the reverberant field is removed."),
            ParamDesc(name="Tail Length", type="float", default=1.0, minimum=0.1, maximum=10.0,
                      unit="s", group="Tail",
                      help="Decay time used to separate the sustained field from the direct one."),
            ParamDesc(name="Artifact Smoothing", type="float", default=0.5, minimum=0.0,
                      maximum=1.0,
                      group="Smoothing", help="Smooths the gain curve to avoid audible artefacts."),
            ParamDesc(name="Band Reduction Low", type="float", default=1.0, minimum=0.0,
                      maximum=1.0,
                      group="Bands", help="How much of the band below 250 Hz is removed.",
                      aliases=("Band Strength Low",)),
            ParamDesc(name="Band Reduction Low-Mid", type="float", default=1.0, minimum=0.0,
                      maximum=1.0,
                      group="Bands", help="How much of the band from 250 Hz to 2 kHz is removed.",
                      aliases=("Band Strength Low-Mid",)),
            ParamDesc(name="Band Reduction High-Mid", type="float", default=1.0, minimum=0.0,
                      maximum=1.0,
                      group="Bands", help="How much of the band from 2 kHz to 8 kHz is removed.",
                      aliases=("Band Strength High-Mid",)),
            ParamDesc(name="Band Reduction High", type="float", default=1.0, minimum=0.0,
                      maximum=1.0,
                      group="Bands", help="How much of the band above 8 kHz is removed.",
                      aliases=("Band Strength High",)),
            ParamDesc(name="Enhance Dry Signal", type="bool", default=False, group="Mix",
                      help="Compensates the direct signal for the energy that was removed."),
            ParamDesc(name="Output Reverb Only", type="bool", default=False, group="Output",
                      help="Renders the removed reverberant component instead of the cleaned "
                      "signal."),
            ParamDesc(**removed_mix_fields("reverberant field")),
        ]

    def reset(self) -> None:
        self._engine.reset()
        self._fast_env = []
        self._slow_env = []
        self._gain_history = None
        self._fifo = np.zeros((0, self._channels), dtype=np.float64)
        self._tap.reset()
        self.removed_level_db = REMOVED_FLOOR_DB
        self.removed_peak_db = REMOVED_FLOOR_DB
        self._last_reduction = None

    @property
    def latency_samples(self) -> int:
        """Samples of delay the module adds.

        The centred analysis needs the samples that follow the frame it
        works on, so the output of the module is late by that much.  The
        number does not depend on the size of the block the host hands over.
        """
        return int(self._engine.latency_samples)

    # -- processing -----------------------------------------------------------
    def _band_edges_bins(self) -> list[tuple[int, int]]:
        freqs = np.fft.rfftfreq(_N_FFT, 1.0 / float(self.sample_rate))
        edges: list[tuple[int, int]] = []
        for i in range(4):
            lo = int(np.searchsorted(freqs, _BAND_EDGES[i], side="left"))
            top = _BAND_EDGES[i + 1]
            hi = len(freqs) if top <= 0 else int(np.searchsorted(freqs, top, side="left"))
            edges.append((min(lo, len(freqs) - 1), max(hi, min(lo + 1, len(freqs)))))
        return edges

    def _band_ratio(self, energy: np.ndarray, ch: int, band: int, tail_s: float) -> np.ndarray:
        """Fraction of the band energy that looks sustained, per frame.

        The two trackers are *stateful*: they continue where the previous
        block left off, one state per channel and band.  A tracker that
        restarted at every block boundary could never run ahead of the other
        one, so a host that renders in small blocks would read the whole
        signal as reverberant and turn the wanted sound down with it.
        """
        if energy.size == 0:
            return energy
        while len(self._fast_env) <= ch:
            self._fast_env.append({})
            self._slow_env.append({})
        fast_state = self._fast_env[ch]
        slow_state = self._slow_env[ch]
        # the fast tracker follows the transients, the slow one the sustained floor
        attack = 0.55
        release = float(min(0.95, 0.15 + 0.8 / max(0.05, tail_s)))
        fast = _envelope(energy, attack, min(0.9, release + 0.25),
                         state=fast_state.get(band))
        slow = _envelope(energy, min(0.35, attack * 0.5), release * 0.25,
                         state=slow_state.get(band))
        fast_state[band] = float(fast[-1])
        slow_state[band] = float(slow[-1])
        floor = np.minimum(fast, slow)
        span = np.maximum(fast - floor, 1e-12)
        ratio = np.clip((fast - slow) / span, 0.0, 1.0)
        return ratio

    def process(self, x: np.ndarray) -> np.ndarray:
        a = np.asarray(x, dtype=np.float64)
        if a.ndim == 1:
            a = a[:, None]
        if a.shape[0] == 0:
            return a
        if a.shape[1] != self._channels:
            # the engine cannot change its channel count inside a stream
            self._channels = int(a.shape[1])
            self._engine.reset()
            self._gain_history = None
            self._fast_env = []
            self._slow_env = []
            self._fifo = np.zeros((0, self._channels), dtype=np.float64)
            self._tap = RemovedTap(self._channels)
        pieces: list[np.ndarray] = []
        for start in range(0, a.shape[0], _FEED_CHUNK):
            chunk = a[start : start + _FEED_CHUNK]
            mag, phase = self._engine.analyze(chunk)
            if mag.shape[-1]:
                mag, phase = self._modify(mag, phase)
            pieces.append(self._engine.synthesize(mag, phase))
        raw = (
            np.concatenate(pieces, axis=0)
            if pieces
            else np.zeros((0, self._channels), dtype=np.float64)
        )
        wet = self._delay(raw, int(a.shape[0]))
        if bool(self.params.get("Output Reverb Only")):
            # the module renders the removed field on its own: the mix does
            # not apply, because the output already is the removed field
            return wet
        out = self._tap.apply(
            a, wet, float(self.params.get(REMOVED_MIX_NAME)), self.latency_samples
        )
        self.removed_level_db = self._tap.level_db
        self.removed_peak_db = self._tap.peak_db
        return out

    def _delay(self, raw: np.ndarray, count: int) -> np.ndarray:
        """Delay the output by the latency of the analysis and take ``count``.

        The engine hands out the samples as soon as the frames that cover
        them are ready, which depends on the size of the blocks it is fed.
        The module keeps its own line of ``latency_samples`` samples, so the
        delay is the same whatever the host uses and the result does not
        depend on the block size.
        """
        fifo = self._fifo
        if fifo.shape[0] == 0:
            fifo = np.zeros((self.latency_samples, self._channels), dtype=np.float64)
        fifo = np.concatenate([fifo, raw], axis=0)
        take = min(count, int(fifo.shape[0]))
        out = fifo[:take]
        self._fifo = fifo[take:]
        if take < count:
            out = np.concatenate(
                [out, np.zeros((count - take, self._channels), dtype=np.float64)], axis=0
            )
        return out

    def _modify(self, mag: np.ndarray, phase: np.ndarray):
        """Apply the reverberation gain to the frames the engine has analysed.

        ``mag`` and ``phase`` are shaped ``(channels, bins, frames)``.
        """
        reduction = float(self.params.get("Reduction"))
        tail_s = float(self.params.get("Tail Length"))
        smoothing = float(self.params.get("Artifact Smoothing"))
        enhance = bool(self.params.get("Enhance Dry Signal"))
        output_only = bool(self.params.get("Output Reverb Only"))
        strengths = [
            float(self.params.get("Band Reduction Low")),
            float(self.params.get("Band Reduction Low-Mid")),
            float(self.params.get("Band Reduction High-Mid")),
            float(self.params.get("Band Reduction High")),
        ]

        # a large change of the amount is a new setting, not a fade: the
        # cross-frame smoothing must not carry the old gain over
        if self._last_reduction is None or abs(reduction - self._last_reduction) > 1e-6:
            self._last_reduction = reduction
            self._fast_env = []
            self._slow_env = []

        edges = self._band_edges_bins()
        n_ch, n_bins, n_frames = mag.shape
        gain_db = np.zeros((n_ch, n_bins, n_frames), dtype=np.float64)
        reverb_mag = np.zeros((n_ch, n_bins, n_frames), dtype=np.float64)

        for c in range(n_ch):
            for bi, (lo, hi) in enumerate(edges):
                if hi <= lo:
                    continue
                energy = np.sum(mag[c, lo:hi, :], axis=0)
                ratio = self._band_ratio(energy, c, bi, tail_s)
                depth = reduction * strengths[bi]
                # the reverberant share is what the slow tracker cannot explain
                late = np.clip(1.0 - ratio, 0.0, 1.0)
                # the reverberant part is attenuated, the direct part untouched
                band_gain = 20.0 * np.log10(np.maximum(1.0 - late * depth, 1e-3))
                band_gain = np.where(late > 0.02, band_gain, 0.0)
                gain_db[c, lo:hi, :] = band_gain[None, :]
                reverb_mag[c, lo:hi, :] = mag[c, lo:hi, :] * late[None, :]

        gain_db = self._smooth(gain_db, smoothing)
        linear = 10.0 ** (gain_db / 20.0)
        dry = mag - reverb_mag
        processed = dry + reverb_mag * linear
        if enhance and reduction > 0.0:
            processed = processed * (1.0 + 0.35 * reduction * reduction)
        if output_only:
            processed = reverb_mag
        return processed, phase

    def _smooth(self, gain_db: np.ndarray, smoothing: float) -> np.ndarray:
        """Smooth the gain curve over time.

        The kernel reaches over the frames on both sides of the frame it
        works on, so the curve that is applied to a frame is the curve of a
        frame that is already in the past: the smoothing lags the gain by
        the width of the kernel.  The history of the raw gain carries the
        frames the kernel needs from one block to the next, so the smoothed
        curve is the same whether the host renders in small blocks or in one
        piece.
        """
        n_ch, n_bins, n_frames = gain_db.shape
        if n_frames == 0:
            return gain_db
        width = 1 + int(round(float(smoothing) * 3))
        width = max(1, min(int(width), _MAX_WIDTH))
        kernel = np.hanning(2 * width + 1)
        kernel /= kernel.sum()
        history = self._gain_history
        if history is None or history.shape[:2] != (n_ch, n_bins):
            history = np.zeros((n_ch, n_bins, _HISTORY), dtype=np.float64)
        combined = np.concatenate([history, gain_db], axis=2)
        # the kernel is centred on the frame that is ``width`` frames back
        base = _HISTORY - width
        out = np.zeros_like(gain_db)
        for m in range(-width, width + 1):
            index = base + m
            if index < 0:
                continue
            out += kernel[m + width] * combined[:, :, index : index + n_frames]
        self._gain_history = combined[:, :, combined.shape[2] - _HISTORY :]
        return out
