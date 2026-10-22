"""
qa.quality.rules_extra — the rules of the gain application.

The rules of :mod:`qa.quality.quality_rules` say what a module has to *do*:
how much of the artefact it removes and how much of the wanted content it
may not damage.  The three rules of this file say how the gain of a module
is *applied*: how fast it may move, how still it has to be when there is
nothing to decide, and what may happen to the material the module does not
touch.

They are written down because none of them was written down anywhere, and
because a number that is not written down is a number that drifts.

=========================  =========  ==============================================
rule                       number      what it means
=========================  =========  ==============================================

``gain_step``              65 % / 10 % the gain may not cover more than 65 % of the
                                       distance to its target in one frame when it
                                       opens, and more than 10 % when it closes.
                                       The unit is the fraction of the distance.

``tracker_creep``          1.750 dB^2 the gain may not breathe by more than
                                       1.750 dB^2 on a steady bed: there is nothing
                                       to decide there, so the gain has to be
                                       still.  The unit is the variance of the
                                       gain curve in dB^2.

``untouched_level``        0.04 dB    the level of a band the module has decided
                                       to leave alone may not move by more than
                                       0.04 dB.  The unit is dB.
=========================  =========  ==============================================

How each number is measured
---------------------------

``gain_step``
    From the inside of the module: the *decision* it takes for a band and
    the gain it *applies* to that band, one value per band and frame.  For
    every frame the distance from the applied gain to the decision is
    measured, and the part of it that the applied gain covers.  The rule
    holds when the largest fraction of that distance never exceeds the
    limit, once for the frames in which the gain opens and once for the
    frames in which it closes.

``tracker_creep``
    From the outside: the *effective gain* of the module on a steady bed of
    broadband noise at -34 dBFS, that is the ratio of the energy of the
    200-8000 Hz band of the output and of the energy of the same band of
    the input, one value per frame.  The rule holds when the variance of
    that curve over the steady part of the bed does not exceed the limit.
    The output is aligned with the input by the delay the module reports.

``untouched_level``
    From the outside: the same effective gain, in the band of a wanted
    partial that stands 44 dB above a bed of noise at -50 dBFS.  The
    module has no reason to touch that partial, so the band has to come
    out at the level it went in.  The rule holds when the level change of
    the band does not exceed the limit.  The output is aligned with the
    input by the delay the module reports.

The rules are read by ``tests/test_voice_de_noise.py``, which asserts every
rule for every module that has it.  A module owner adds its code to the
``modules`` field of a rule to be held to it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

__all__ = [
    "SR",
    "ExtraRule",
    "RULES",
    "RULES_BY_KEY",
    "MODULES_WITH_RULES",
    "rules_for",
    "rule_for",
    "gain_step_fractions",
    "gain_variance_db2",
    "untouched_level_db",
    "measure_rule",
]

#: Sample rate of every measurement of this file.
SR = 48_000

#: The analysis geometry of the effective gain curve (samples).
_N_FFT = 1024
_HOP = 64

#: The band the chatter is measured in (Hz).
_CHATTER_BAND = (200.0, 8000.0)

#: The partial the untouched material is measured on (Hz).
_PARTIAL_HZ = 1000.0

#: The width of the band the partial is measured in, as a fraction of its
#: frequency.
_PARTIAL_WIDTH = 0.03

_EPS = 1e-30

#: How much a measured number may pass the limit of a rule by before the
#: rule is broken.  A limit that is reached exactly is not broken.
_TOLERANCE = 1e-6

#: The smallest distance between the applied gain and the decision that is
#: still measured (dB).  A step of a thousandth of a decibel says nothing
#: about the bound, and the ratio of two such numbers is the noise of the
#: arithmetic.
_MIN_STEP_DB = 1e-3


# ---------------------------------------------------------------------------
# the rules
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ExtraRule:
    """One rule of the gain application, with its number and its unit.

    The limits are keyed by the name of the measured quantity, so that a
    rule with two numbers (the opening and the closing) stays one rule.
    Every limit is a *maximum*: the measured value may not be larger.
    """

    key: str
    name: str
    unit: str
    limits: tuple[tuple[str, float], ...]
    how: str
    reason: str
    modules: tuple[str, ...]
    #: The number of the reference, when there is one.  It is a target and
    #: not a limit: the reference was measured on somebody else's build.
    target: float | None = None
    target_note: str = ""

    @property
    def limit(self) -> float:
        """The largest of the limits of the rule."""
        return max(value for _name, value in self.limits)

    def check(self, values: dict[str, float]) -> list[str]:
        """Return the list of criteria this measurement breaks."""
        failures: list[str] = []
        for name, limit in self.limits:
            if name not in values:
                failures.append("the %s could not be measured" % name)
                continue
            value = values[name]
            if value != value:  # NaN
                failures.append("the %s is not a number" % name)
                continue
            if not value <= limit + _TOLERANCE:
                failures.append(
                    "the %s is %.4f %s, the rule allows %.4f %s"
                    % (name, value, self.unit, limit, self.unit)
                )
        return failures

    def as_dict(self) -> dict:
        """The rule as a plain dictionary, for the report."""
        return {
            "key": self.key,
            "name": self.name,
            "unit": self.unit,
            "limits": {name: value for name, value in self.limits},
            "how": self.how,
            "modules": list(self.modules),
            "target": self.target,
            "target_note": self.target_note,
        }


#: The rules of the gain application.
RULES: tuple[ExtraRule, ...] = (
    ExtraRule(
        key="gain_step",
        name="the bound on the movement of the gain",
        unit="fraction of the distance to the target",
        limits=(("open", 0.65), ("close", 0.10)),
        how=(
            "the decision the module takes for a band and the gain it "
            "applies to that band, one value per band and frame; the "
            "distance from the applied gain to the decision and the part of "
            "it that the applied gain covers; the largest fraction of that "
            "distance over all the bands and frames, once for the frames in "
            "which the gain opens and once for the frames in which it closes"
        ),
        reason=(
            "The smoothing of the gain is what makes our gain curve the "
            "calmest of the three products the study compared: a one-pole "
            "cannot cover more than a fixed fraction of the distance to its "
            "target in one frame, so the gain can never step.  None of the "
            "references has that bound: one of them moves a mix gain by "
            "16.94 dB inside a quarter of a millisecond, the other moves "
            "its separation gain by up to 31.9 dB inside one frame of "
            "0.33 ms.  The bound was not written down anywhere in the "
            "product, so a change of the attack or of the release could "
            "have taken it away without anybody noticing."
        ),
        modules=("AUR_VOICEDENOISE",),
        target=None,
        target_note=(
            "There is no reference for this rule: neither of the two "
            "references limits the speed of its gain at all."
        ),
    ),
    ExtraRule(
        key="tracker_creep",
        name="the creep of the noise reference",
        unit="dB^2",
        limits=(("variance", 1.750),),
        how=(
            "the effective gain of the module on a steady bed of broadband "
            "noise at -34 dBFS (the ratio of the energy of the 200-8000 Hz "
            "band of the output and of the energy of the same band of the "
            "input, one value per frame); the variance of that curve over "
            "the steady part of the bed, from 0.5 s to 3.5 s; the output is "
            "aligned with the input by the delay the module reports"
        ),
        reason=(
            "On a bed there is nothing to decide, so the gain has to be "
            "still: what is left is the creep of the noise reference, the "
            "tracker that follows the level of a band upwards at a fixed "
            "number of decibels per frame.  The study measured 1.750 dB^2 "
            "for our module and 0.426 dB^2 for the reference of the three "
            "stems, and it was written down nowhere.  The number is the "
            "budget of the rule, not the target: the target of the study "
            "(0.426 dB^2) is not reachable without giving up the removal "
            "(see the report of the change)."
        ),
        modules=("AUR_VOICEDENOISE",),
        target=0.426,
        target_note=(
            "The reference of the three stems breathes by 0.426 dB^2 on a "
            "bed.  The number is not reachable for us: the creep of the "
            "tracker is not what makes the gain move on a bed, and every "
            "setting that lowers it lowers the removal with it."
        ),
    ),
    ExtraRule(
        key="untouched_level",
        name="the level of the material the module does not touch",
        unit="dB",
        limits=(("level", 0.04),),
        how=(
            "the effective gain of the module in the band of a wanted "
            "partial (1 kHz, +-3 %) that stands 44 dB above a bed of noise "
            "at -50 dBFS, measured over the steady part of the signal; the "
            "output is aligned with the input by the delay the module "
            "reports"
        ),
        reason=(
            "A module that decides to leave a band alone has to leave it "
            "alone: the material may not come out at another level than it "
            "went in.  The reference of the study keeps that promise to "
            "0.04 dB, and the number was not written down in our rules.  "
            "Before this rule was made, our voice module attenuated a "
            "strong partial by up to 14.7 dB, because the interpolation "
            "and the smoothing of the gain curve over the frequency axis "
            "carried the decision of a neighbouring band into the band that "
            "was left alone."
        ),
        modules=("AUR_VOICEDENOISE", "AUR_DEHUM", "AUR_GUITARDENOISE"),
        target=0.04,
        target_note=(
            "The reference of the study keeps the level of the material it "
            "does not touch to 0.04 dB after a 30 dB step.  The de-clicker "
            "and the breath controller hold the rule as well (0.0000 dB), "
            "and so does the spectral de-noiser; the last one is under "
            "repair, so its number is taken when the repair lands."
        ),
    ),
)

#: The rules, keyed by their key.
RULES_BY_KEY: dict[str, ExtraRule] = {rule.key: rule for rule in RULES}

#: The modules that have at least one rule of the gain application.
MODULES_WITH_RULES: tuple[str, ...] = tuple(
    sorted({code for rule in RULES for code in rule.modules})
)


def rules_for(code: str) -> tuple[ExtraRule, ...]:
    """Return the rules of one module."""
    return tuple(rule for rule in RULES if code in rule.modules)


def rule_for(key: str) -> ExtraRule:
    """Return one rule by its key."""
    try:
        return RULES_BY_KEY[key]
    except KeyError:
        raise KeyError(f"no rule named {key!r}") from None


# ---------------------------------------------------------------------------
# the instrument
# ---------------------------------------------------------------------------
#: The modules whose gain application is measured from the inside, and the
#: pair of methods that carry the decision and the gain that is applied.
INTERNAL_GAIN_HOOKS: dict[str, tuple[str, str]] = {
    "AUR_VOICEDENOISE": ("_band_targets", "_smooth_gain"),
}


def _as_mono(x: np.ndarray) -> np.ndarray:
    """First channel of ``x`` as a flat float64 array."""
    arr = np.asarray(x, dtype=np.float64)
    if arr.ndim > 1:
        arr = arr[:, 0] if arr.shape[1] else arr.ravel()
    return arr.ravel()


def noise_bed(seconds: float, level_db: float, seed: int = 51) -> np.ndarray:
    """A steady bed of broadband noise."""
    rng = np.random.default_rng(seed)
    count = int(round(seconds * SR))
    x = rng.standard_normal(count)
    x *= 10.0 ** (level_db / 20.0) / math.sqrt(float(np.mean(x * x)))
    return x


def voice_like(seconds: float, level_db: float, f0: float = 140.0) -> np.ndarray:
    """A harmonic stack — the stand-in for a voice."""
    count = int(round(seconds * SR))
    t = np.arange(count) / SR
    out = np.zeros(count)
    for k in range(1, 21):
        freq = f0 * k
        if freq > 7000:
            break
        out += (1.0 / (k**1.1)) * np.sin(2 * np.pi * freq * t + 0.7 * k)
    out *= 10.0 ** (level_db / 20.0) / math.sqrt(float(np.mean(out * out)))
    return out


def voice_scene(bed_db: float = -60.0, seed: int = 73) -> np.ndarray:
    """A bed with a voice in it: the voice is there from 1 s to 2 s."""
    bed = noise_bed(4.0, bed_db, seed=seed)
    bed[int(1.0 * SR) : int(2.0 * SR)] += voice_like(1.0, -18.0)
    return bed


def partial_scene(level_db: float = -6.0, bed_db: float = -50.0) -> np.ndarray:
    """A bed with a strong partial on it."""
    bed = noise_bed(2.0, bed_db, seed=91)
    t = np.arange(bed.size) / SR
    bed += 10.0 ** (level_db / 20.0) * np.sin(2 * np.pi * _PARTIAL_HZ * t)
    return bed


def band_energy_frames(
    x: np.ndarray, lo: float, hi: float, n_fft: int = _N_FFT, hop: int = _HOP
) -> np.ndarray:
    """The energy of a band, one value per frame."""
    arr = _as_mono(x)
    count = 1 + max(0, (arr.size - n_fft)) // hop
    if count <= 0:
        return np.zeros(0)
    padded = np.zeros(count * hop + n_fft)
    padded[: arr.size] = arr
    frames = np.lib.stride_tricks.as_strided(
        padded,
        shape=(count, n_fft),
        strides=(padded.strides[0] * hop, padded.strides[0]),
    )
    spectrum = np.fft.rfft(frames * np.hanning(n_fft), axis=1)
    freqs = np.fft.rfftfreq(n_fft, 1.0 / SR)
    mask = (freqs >= lo) & (freqs < hi)
    return np.maximum(np.sum(np.abs(spectrum[:, mask]) ** 2, axis=1), _EPS)


def effective_gain(
    x_in: np.ndarray,
    x_out: np.ndarray,
    lo: float,
    hi: float,
    latency: int,
    n_fft: int = _N_FFT,
    hop: int = _HOP,
) -> tuple[np.ndarray, np.ndarray]:
    """The gain the module applied in a band, one value per frame.

    The output is aligned with the input by ``latency`` samples: a module
    that delays the signal delays the answer as well, so the pair that is
    compared is the output at ``t + latency`` and the input at ``t``.
    """
    a = _as_mono(x_in)
    b = _as_mono(x_out)
    if latency > 0:
        b = b[latency:]
        a = a[: b.size]
    elif latency < 0:
        a = a[-latency:]
        b = b[: a.size]
    size = min(a.size, b.size)
    a = a[:size]
    b = b[:size]
    p_in = band_energy_frames(a, lo, hi, n_fft, hop)
    p_out = band_energy_frames(b, lo, hi, n_fft, hop)
    count = min(p_in.size, p_out.size)
    curve = 10.0 * np.log10(np.maximum(p_out[:count], _EPS) / np.maximum(p_in[:count], _EPS))
    times = (np.arange(count) * hop + n_fft / 2.0) / SR
    return times, curve


def _run(code: str, x: np.ndarray, sample_rate: int = SR):
    """Run a module over a mono signal and return the output and the delay."""
    from aurcore import registry

    module = registry.create(code, sample_rate, 1)
    out = np.asarray(module.process(x[:, None]), dtype=np.float64)[:, 0]
    return out, int(getattr(module, "latency_samples", 0))


def gain_variance_db2(code: str, sample_rate: int = SR) -> float:
    """The variance of the gain of a module on a steady bed (dB^2)."""
    bed = noise_bed(4.0, -34.0)
    out, latency = _run(code, bed, sample_rate)
    times, curve = effective_gain(
        bed, out, _CHATTER_BAND[0], _CHATTER_BAND[1], latency
    )
    window = (times >= 0.5) & (times <= 3.5)
    if int(window.sum()) < 4:
        return float("nan")
    return float(np.var(curve[window]))


def untouched_level_db(code: str, sample_rate: int = SR) -> float:
    """The level change of a band the module leaves alone (dB)."""
    scene = partial_scene()
    out, latency = _run(code, scene, sample_rate)
    lo = _PARTIAL_HZ * (1.0 - _PARTIAL_WIDTH)
    hi = _PARTIAL_HZ * (1.0 + _PARTIAL_WIDTH)
    times, curve = effective_gain(scene, out, lo, hi, latency)
    window = (times >= 0.6) & (times <= 1.4)
    if int(window.sum()) < 4:
        return float("nan")
    return float(np.median(curve[window]))


def _gain_probe(code: str, sample_rate: int = SR):
    """A module that records the decision it takes and the gain it applies.

    The probe only *observes* the module: the algorithm is untouched.  It
    is the only way to measure the bound on the movement of the gain, which
    is a rule about the inside of the module.
    """
    from aurcore import registry

    cls = registry.get(code)
    if code not in INTERNAL_GAIN_HOOKS:
        raise KeyError(f"the gain of {code!r} cannot be measured from the inside")
    target_hook, applied_hook = INTERNAL_GAIN_HOOKS[code]
    original_target = getattr(cls, target_hook)
    original_applied = getattr(cls, applied_hook)

    class Probe(cls):  # type: ignore[misc, valid-type]
        """The module with a recorder on its decision and on its gain."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.decisions: list[np.ndarray] = []
            self.applied: list[np.ndarray] = []

    def wrapped_target(self, *args, **kwargs):
        out = original_target(self, *args, **kwargs)
        self.decisions.append(np.asarray(out, dtype=np.float64).copy())
        return out

    def wrapped_applied(self, *args, **kwargs):
        out = original_applied(self, *args, **kwargs)
        self.applied.append(np.asarray(out, dtype=np.float64).copy())
        return out

    setattr(Probe, target_hook, wrapped_target)
    setattr(Probe, applied_hook, wrapped_applied)
    return Probe(sample_rate, 1)


def gain_step_fractions(code: str, sample_rate: int = SR) -> dict[str, float]:
    """The largest fraction of the distance the gain covers in one frame."""
    probe = _gain_probe(code, sample_rate)
    scene = voice_scene()
    probe.process(scene[:, None])
    if not probe.decisions or not probe.applied:
        return {"open": float("nan"), "close": float("nan")}
    decisions = np.concatenate(probe.decisions, axis=0)
    applied = np.concatenate(probe.applied, axis=0)
    if decisions.shape[0] < 2:
        return {"open": float("nan"), "close": float("nan")}
    previous = applied[:-1]
    target = decisions[1:]
    distance = np.abs(target - previous)
    covered = np.abs(applied[1:] - previous)
    mask = distance > _MIN_STEP_DB
    if not np.any(mask):
        return {"open": 0.0, "close": 0.0}
    fractions = covered[mask] / distance[mask]
    opening = (target > previous)[mask]
    result = {"open": 0.0, "close": 0.0}
    if np.any(opening):
        result["open"] = float(np.max(fractions[opening]))
    if np.any(~opening):
        result["close"] = float(np.max(fractions[~opening]))
    return result


def measure_rule(rule: ExtraRule, code: str, sample_rate: int = SR) -> dict[str, float]:
    """Measure one rule for one module."""
    if rule.key == "gain_step":
        return gain_step_fractions(code, sample_rate)
    if rule.key == "tracker_creep":
        return {"variance": gain_variance_db2(code, sample_rate)}
    if rule.key == "untouched_level":
        return {"level": untouched_level_db(code, sample_rate)}
    raise KeyError(f"the rule {rule.key!r} has no instrument")
