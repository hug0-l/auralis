"""
aurcore.modules.find_similar — find similar material inside a recording.

The module is a *measurement* tool: it does not change the audio.  It splits
the signal into short spectral tiles, computes a compact descriptor for every
tile (log mel band energies plus a spectral shape description) and ranks the
tiles of a signal against a reference tile that the user has selected.

The ranking is a sliding sequence match: the descriptor of every frame of the
reference is compared with the descriptors of the signal, both with a small
alignment tolerance, and the region score is the mean of the per frame
correlations.  A repeated word therefore scores close to 1.0 everywhere it
appears, while unrelated material stays far below.

Public API
----------
``learn(x)``
    Store ``x`` as the reference tile.
``find(x)``
    Return the matching regions of ``x`` as ``(start_sample, end_sample, score)``.
``rank(x)``
    Return every candidate region of ``x`` sorted by score.
``score_at(x, start)``
    Return the similarity of the reference against the signal at a position.
"""
from __future__ import annotations

import io

import numpy as np

from ..params import ParamDesc, desc
from ..registry import register
from .base import Module
from .mixing import stft

__all__ = ["FindSimilar"]


def _hz_to_mel(freq: float | np.ndarray):
    """Convert a frequency in Hz to the mel scale."""
    return 2595.0 * np.log10(1.0 + np.asarray(freq, dtype=np.float64) / 700.0)


def _mel_to_hz(mel: float | np.ndarray):
    """Convert a value on the mel scale back to Hz."""
    return 700.0 * (10.0 ** (np.asarray(mel, dtype=np.float64) / 2595.0) - 1.0)


def _mel_filterbank(sample_rate: int, n_fft: int, n_bands: int = 32,
                    fmin: float = 40.0, fmax: float | None = None) -> np.ndarray:
    """Build a triangular mel filterbank of shape ``(n_bands, n_fft // 2 + 1)``.

    Every filter is normalised to unit sum, so a band reports the *average*
    power of its frequency range.  Without that normalisation the wider high
    frequency bands would collect more energy and every spectrum would show
    the same rising tilt, which would blur the difference between two
    sounds.
    """
    fmax = float(fmax if fmax is not None else sample_rate / 2.0)
    mel = np.linspace(_hz_to_mel(fmin), _hz_to_mel(fmax), n_bands + 2)
    hz = _mel_to_hz(mel)
    bins = np.floor((n_fft + 1) * hz / float(sample_rate)).astype(int)
    bins = np.clip(bins, 0, n_fft // 2)
    fb = np.zeros((n_bands, n_fft // 2 + 1), dtype=np.float64)
    for i in range(n_bands):
        left, centre, right = int(bins[i]), int(bins[i + 1]), int(bins[i + 2])
        if centre <= left:
            centre = min(left + 1, n_fft // 2)
        if right <= centre:
            right = min(centre + 1, n_fft // 2)
        if centre == left:
            continue
        for k in range(left, centre):
            fb[i, k] = (k - left) / float(centre - left)
        for k in range(centre, right):
            fb[i, k] = (right - k) / float(right - centre)
        total = fb[i].sum()
        if total > 0.0:
            fb[i] /= total
    return fb


@register
class FindSimilar(Module):
    """Find Similar — locate repetitions of a reference sound.

    The module never modifies the audio; it is a measurement tool that
    reports where the reference tile occurs inside a signal.

    Parameters
    ----------
    Similarity:
        The score a region has to reach to be reported by :meth:`find`.
    """

    code = "AUR_FINDSIMILAR"
    display_name = "Find Similar"
    category = "Measurement"
    edition = "standard"
    element = "FindSimilar"
    analysis = True
    supports_learn = True
    ml = False

    N_FFT = 1024
    HOP = 256
    N_BANDS = 32
    JITTER = 1
    FLOOR_DECADES = 6.0     # 60 dB below the frame peak
    SHAPE_WEIGHT = 0.3      # weight of the spectral shape descriptors

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        super().__init__(sample_rate, channels)
        self._filterbank = _mel_filterbank(sample_rate, self.N_FFT, self.N_BANDS)
        self._freqs = np.fft.rfftfreq(self.N_FFT, 1.0 / float(sample_rate))
        self._reference: np.ndarray | None = None
        self._reference_len = 0
        self._max_reference = int(2.0 * sample_rate)

    # -- parameter description ------------------------------------------------
    def describe(self) -> list[ParamDesc]:
        """Return the parameter list of the module."""
        return [
            desc(
                name="Similarity",
                type="float",
                default=0.8,
                minimum=0.0,
                maximum=1.0,
                group="Search",
                help="Minimum score a region has to reach to be reported as a match.",
            ),
        ]

    # -- state ----------------------------------------------------------------
    def reset(self) -> None:
        """Forget the reference tile."""
        self._reference = None
        self._reference_len = 0

    @property
    def has_reference(self) -> bool:
        """True when a reference tile has been learned."""
        return self._reference is not None

    @property
    def reference_length(self) -> int:
        """Length of the learned reference tile in samples."""
        return int(self._reference_len)

    def _state_blob(self) -> bytes:
        """Serialise the learned reference tile."""
        if self._reference is None:
            return b""
        buffer = io.BytesIO()
        np.savez(buffer, features=self._reference, meta=np.array([self._reference_len]))
        return buffer.getvalue()

    def _put_state_blob(self, data: bytes) -> None:
        """Restore a reference tile produced by :meth:`_state_blob`."""
        try:
            with np.load(io.BytesIO(data)) as archive:
                self._reference = np.asarray(archive["features"], dtype=np.float64)
                self._reference_len = int(np.asarray(archive["meta"]).ravel()[0])
        except Exception:
            self._reference = None
            self._reference_len = 0

    # -- feature extraction ---------------------------------------------------
    def _frame_features(self, x: np.ndarray) -> np.ndarray:
        """Return the descriptor of every analysis frame of ``x``.

        The descriptor holds the log mel band energies relative to the peak
        of the frame and a short spectral shape description (centroid,
        flatness, roll off).  The mel energies are floored 60 dB below the
        frame peak so that the noise floor of a band cannot dominate the
        comparison, and the whole vector is mean centred and normalised, so
        the dot product of two descriptors is their correlation in
        ``[-1, 1]`` and is independent of the level.
        """
        a = np.asarray(x, dtype=np.float64)
        if a.ndim == 1:
            a = a[:, None]
        if a.shape[0] == 0:
            return np.zeros((0, self.N_BANDS + 3), dtype=np.float64)
        magnitude = np.abs(stft(a, self.N_FFT, self.HOP))
        if magnitude.shape[2] > 1:
            magnitude = magnitude.mean(axis=2)
        else:
            magnitude = magnitude[:, :, 0]
        bands = self._filterbank @ magnitude.T          # (n_bands, n_frames)
        log_mel = np.log10(bands + 1e-10)
        peak = log_mel.max(axis=0, keepdims=True)
        log_mel = np.maximum(log_mel, peak - self.FLOOR_DECADES)
        log_mel = (log_mel - peak).T                     # relative to the frame peak
        log_mel = log_mel - log_mel.mean(axis=1, keepdims=True)
        log_mel = log_mel / np.maximum(
            np.linalg.norm(log_mel, axis=1, keepdims=True), 1e-12
        )

        power = magnitude ** 2
        total = power.sum(axis=1) + 1e-20
        centroid = (power * self._freqs[None, :]).sum(axis=1) / total
        mean_power = power.mean(axis=1) + 1e-20
        flatness = np.exp(np.log(power + 1e-20).mean(axis=1)) / mean_power
        cumulative = np.cumsum(power, axis=1)
        roll_index = np.argmax(cumulative >= 0.85 * cumulative[:, -1:], axis=1)
        rolloff = self._freqs[roll_index]
        shape = np.column_stack(
            [
                centroid / (self.sample_rate / 2.0),
                flatness,
                rolloff / (self.sample_rate / 2.0),
            ]
        )
        shape = shape - shape.mean(axis=1, keepdims=True)
        shape = shape / np.maximum(np.linalg.norm(shape, axis=1, keepdims=True), 1e-12)
        shape = shape * self.SHAPE_WEIGHT

        features = np.concatenate([log_mel, shape], axis=1)
        features = features - features.mean(axis=1, keepdims=True)
        return features / np.maximum(np.linalg.norm(features, axis=1, keepdims=True), 1e-12)

    # -- learning -------------------------------------------------------------
    def learn(self, x: np.ndarray) -> None:
        """Store ``x`` as the reference tile used for the search."""
        a = np.asarray(x, dtype=np.float64)
        if a.ndim == 1:
            a = a[:, None]
        if a.shape[0] == 0:
            return
        if a.shape[0] > self._max_reference:
            energy = np.sum(a ** 2, axis=1)
            cumulative = np.concatenate([[0.0], np.cumsum(energy)])
            window = self._max_reference
            sums = cumulative[window:] - cumulative[:-window]
            start = int(np.argmax(sums))
            a = a[start:start + window]
        self._reference = self._frame_features(a)
        self._reference_len = int(a.shape[0])

    # -- searching ------------------------------------------------------------
    def _score_curve(self, features: np.ndarray) -> np.ndarray:
        """Return the score of every possible alignment of the reference.

        The reference is a *sequence* of frames, so the score of an alignment
        compares the reference frame ``r`` with the signal frame ``s + r``.
        A small alignment tolerance (the jitter) lets a match survive a
        slightly different start position.
        """
        if self._reference is None or features.shape[0] == 0:
            return np.zeros(0, dtype=np.float64)
        correlation = self._reference @ features.T      # (n_ref_frames, n_frames)
        n_ref, n_sig = correlation.shape
        rows = np.arange(n_ref)[:, None]
        columns = np.arange(n_sig)[None, :]
        best = None
        for offset in range(-self.JITTER, self.JITTER + 1):
            index = columns + rows + offset
            valid = (index >= 0) & (index < n_sig)
            band = np.take_along_axis(correlation, np.clip(index, 0, n_sig - 1), axis=1)
            band = np.where(valid, band, -np.inf)
            best = band if best is None else np.maximum(best, band)
        valid = n_sig - n_ref + 1
        scores = np.zeros(n_sig, dtype=np.float64)
        if valid > 0:
            scores[:valid] = np.maximum(best[:, :valid].mean(axis=0), 0.0)
        return scores

    def rank(self, x: np.ndarray) -> list[tuple[int, int, float]]:
        """Return every candidate region of ``x`` sorted by descending score."""
        if self._reference is None:
            return []
        features = self._frame_features(x)
        scores = self._score_curve(features)
        out = []
        for index, score in enumerate(scores):
            start = index * self.HOP
            out.append((start, start + self._reference_len, float(score)))
        out.sort(key=lambda item: -item[2])
        return out

    def find(self, x: np.ndarray) -> list[tuple[int, int, float]]:
        """Return the regions of ``x`` that match the reference tile.

        Returns
        -------
        list of (start_sample, end_sample, score)
            The matching regions, ordered by position.  The score is in
            ``[0, 1]``; 1.0 means the region is identical to the reference.
        """
        if self._reference is None:
            return []
        features = self._frame_features(x)
        scores = self._score_curve(features)
        threshold = float(self.params.get("Similarity"))
        candidates = [(i,
                       float(scores[i])) for i in range(scores.shape[0]) if scores[i] >= threshold]
        candidates.sort(key=lambda item: -item[1])
        accepted: list[tuple[int, int, float]] = []
        for index, score in candidates:
            start = index * self.HOP
            end = start + self._reference_len
            if any(start < other_end and other_start < end for other_start, other_end,
                   _ in accepted):
                continue
            accepted.append((start, end, score))
        accepted.sort(key=lambda item: item[0])
        return accepted

    def score_at(self, x: np.ndarray, start_sample: int) -> float:
        """Return the similarity of the reference against ``x`` at a position."""
        if self._reference is None:
            return 0.0
        features = self._frame_features(x)
        scores = self._score_curve(features)
        index = int(start_sample) // self.HOP
        if index < 0 or index >= scores.shape[0]:
            return 0.0
        return float(scores[index])

    # -- processing -----------------------------------------------------------
    def process(self, x: np.ndarray) -> np.ndarray:
        """Pass the audio through unchanged (this module only measures)."""
        return x
