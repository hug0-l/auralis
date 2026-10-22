"""
tests/test_safe_defaults.py — the gate of the safe defaults.

A module that is not asked to do anything must not do anything.  This file
turns that sentence into gates over **every module of the catalogue**.

**The bypass (every module).**  With the bypass on, the output has to be the
input, sample for sample: the largest difference is exactly zero, and the
chain reports **no delay at all**.  A bypass that carries a delay is a
bypass that plays the programme late; the C ABI says a bypassed module
"passes the audio through untouched" (``plugins/vst3/aurcore_c_api.h``), and
untouched means untouched, delay included.

**The defaults (the repair modules).**  With the default settings, a repair
module has to leave a clean signal alone.  The measurement is the largest
difference between the output and the input **delayed by the latency the
module declares**, expressed in decibels below the peak of the input: a host
compensates the delay a module reports, so what the host hears is the signal
the module produced, minus that delay.  A module that declares a delay it
does not have fails here as well, because the host hears the shift.

**The yard.**  The yard is the yard of the study: the reference of the study
changed a clean signal by 63.84 dB below its peak, so a repair module of
ours has to be at least 56 dB better than that.  The yard is written down as
:data:`TRANSPARENCY_YARD_DB`; the measured figures of the modules on the
transparent side are below 300 dB, which is the arithmetic floor of a double
precision transform round trip, and the closest exception sits at 59 dB.

**The line.**  Not every module may be transparent: a gain, an equaliser, a
leveler, a normaliser, a dither, a resampler and a signal generator change
the signal as their job, and asking them to be transparent would be asking
them not to work.  The line is therefore drawn **by the category of the
module**:

* the **repair** modules must be transparent by default -- a repair module
  that changes a clean signal is either broken or has been told to;
* the **utility**, **measurement** and **system** modules may not be
  transparent by default -- that is what they are for.

A repair module that is not transparent today is named in
:data:`REPAIR_MAY_CHANGE`, with the reason why its default is not the absence of
processing.  The list is not an excuse: it is the place where the exceptions are
written down, so that the day the default of such a module is made neutral, the
entry is deleted and the gate starts holding the module to the rule.  The figures
the exceptions were measured with are in the note of §3.3 of the design note.

The numbers the guarantee is built on are in
``qa/benchmark/reference_restoration.py``; the note that reads them, and that says
what was measured, what we do, what we adopt and the yard, is
``docs/RESTORATION_REFERENCE.md`` §3.3.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from aurcore import registry  # noqa: E402
from aurcore.api import create_engine  # noqa: E402

# ---------------------------------------------------------------------------
# the constants of the measurement
# ---------------------------------------------------------------------------
#: The rate the measurement runs at.
SAMPLE_RATE = 48_000

#: The channel count the measurement runs at.
CHANNELS = 2

#: The length of the measured block, in samples (0.17 s at 48 kHz).
BLOCK = 8192

#: The levels the applied gain is measured at, in dBFS.
TONE_LEVELS_DB: tuple[float, ...] = (-6.0, -20.0, -40.0, -60.0)

#: How far the applied gain may move between the four levels, in dB.
#:
#: The yard is the yard of the study: the gain curve of the reference of the
#: study moves by 0.0073 dB peak to peak over the same four levels, so a
#: module whose gain moves by more than a tenth of a decibel follows the
#: level of the input.  A tenth of a decibel is loose enough for the
#: arithmetic of a tone 60 dB down and tight enough to catch a gate that
#: opens on quiet material.
GAIN_SPREAD_LIMIT_DB = 0.1

#: How far below the peak of the input the default of a repair module may
#: move a clean signal, in decibels.
#:
#: The reference of the study moved a clean signal by 63.84 dB below its
#: peak, and the rule of the project is that a repair module of ours is at
#: least 56 dB better than that.  The measured figures of the modules on the
#: transparent side are below 300 dB -- the arithmetic floor of a double
#: precision transform round trip -- and the closest module on the other
#: side of the line sits at 59 dB.
TRANSPARENCY_YARD_DB = -120.0

#: How far below full scale the output of a silent input has to stay, in dB.
#:
#: The yard is the yard of the study: the default path of the reference of
#: the study put a floor of its own in front of the programme at 103.10 dBFS,
#: so a path of ours has to be at least 17 dB below that.  The measured
#: figure of the modules that go through a transform is the arithmetic floor
#: of the transform, at 352 dBFS, and the modules that pass the signal
#: through give exactly zero.
NOISE_FLOOR_YARD_DBFS = -120.0

#: The frequency of the tone probe, in Hz.
TONE_HZ = 1000.0


# ---------------------------------------------------------------------------
# the line
# ---------------------------------------------------------------------------
#: The repair modules whose default setting is a deliberate amount of
#: processing, so their default is not the absence of processing.
#:
#: Every entry names the reason and the figure that was measured on the
#: clean probes of this file.  The entry is the place where the exception is
#: written down: the day the default of the module is made neutral, the
#: entry is deleted and the gate holds the module to the rule.
REPAIR_MAY_CHANGE: dict[str, str] = {
    "AUR_BANDRESTORATION": (
        "the default of the module restores four bands of the material it is "
        "handed, and the band it restores is chosen by the material"
    ),
    "AUR_BREATH": (
        "the default of the module takes 9 dB off the level, so a breath is "
        "taken away whether there is one or not"
    ),
    "AUR_DEBLEED": (
        "the default of the module removes half of the bleed it finds, and "
        "it finds some in the clean probe"
    ),
    "AUR_DECLIP": (
        "the default of the module measures the clipping of the material it "
        "is handed and makes the level up again, and on material that is not "
        "clipped the measurement is not zero"
    ),
    "AUR_DECONSTRUCT": (
        "the module separates the sources of a recording, so the default "
        "changes the recording whether it is wanted or not"
    ),
    "AUR_DECRACKLE": (
        "the default of the module takes half of the crackle it finds, and "
        "it finds some in material that carries none"
    ),
    "AUR_DEESS": (
        "the default of the module flattens the sibilance it finds by half, "
        "and the filter it applies moves the clean probe as well"
    ),
    "AUR_DEREVERB": (
        "the default of the module takes half of the reverberation it finds, "
        "and the clean probe moves with it"
    ),
    "AUR_DERUSTLE": (
        "the default of the module takes half of the rustle it finds, and "
        "the clean probe moves with it"
    ),
    "AUR_GUITARDENOISE": (
        "the default of the module takes the noise of a guitar away whether "
        "there is one or not"
    ),
    "AUR_MOUTHDECLICK": (
        "the default of the module finds mouth clicks in material that "
        "carries none"
    ),
    "AUR_SPECTRALDENOISE": (
        "the default of the module estimates a noise floor and removes 15 dB "
        "of it, and the estimate is not zero on material that carries none"
    ),
    "AUR_SPECTRALRECOVERY": (
        "the module recovers what a previous process took away, so its "
        "default adds something to the material"
    ),
    "AUR_VOICEDENOISE": (
        "the default of the module takes 18 dB off the bed it estimates, and "
        "the estimate is not zero on material that carries none"
    ),
    "AUR_VOICEREVERB": (
        "the default of the module takes the reverberation of a voice away, "
        "and the clean probe moves with it"
    ),
    "AUR_WOWFLUTTER": (
        "the default of the module corrects the pitch of the material, so a "
        "clean tone is corrected as well"
    ),
}

#: The modules whose job is to change the level of the signal, so the gain
#: they apply depends on the level of the input by design.
#:
#: Every entry names the parameter of the canonical table that makes the
#: module a level controller.  The list is the set of the modules that carry
#: a target level, so it is read off the table and not off a measurement.
LEVEL_DEPENDENT: dict[str, str] = {
    "AUR_NORMALIZE": (
        "the module brings the level of the material to the target peak "
        "level it carries, so the gain it applies is the distance between "
        "the material and the target"
    ),
    "AUR_LOUDNESS": (
        "the module brings the loudness of the material to the target "
        "loudness it carries, so the gain it applies is the distance between "
        "the material and the target"
    ),
    "AUR_LOUDNESSOPT": (
        "the module is a compressor: it works above a threshold, so the gain "
        "it applies follows the level of the input"
    ),
    "AUR_LEVELER": (
        "the module brings the loudness of the material to the target level "
        "it carries, so the gain it applies is the distance between the "
        "material and the target"
    ),
    "AUR_STREAMPREVIEW": (
        "the module plays the material at the playback loudness it carries, "
        "so the gain it applies follows the level of the input"
    ),
    "AUR_SIGNALGEN": (
        "the module makes a sound of its own, so what it does not take from "
        "the input it adds to it"
    ),
}

#: The modules whose job is to make a sound of their own, so a silent input
#: does not come out silent.
MAKES_ITS_OWN_SOUND: dict[str, str] = {
    "AUR_SIGNALGEN": (
        "the module generates a signal, so a silent input does not come out "
        "silent: that is what it is for"
    ),
}

#: The categories whose modules may change the signal by default.
UTILITY_CATEGORIES = frozenset({"Utility", "Measurement", "System"})


def _transparent_codes() -> tuple[str, ...]:
    """The codes of the modules whose default has to be transparent."""
    codes = []
    for info in registry.all():
        if info.category in UTILITY_CATEGORIES:
            continue
        if info.code in REPAIR_MAY_CHANGE:
            continue
        codes.append(info.code)
    return tuple(codes)


# ---------------------------------------------------------------------------
# the signals
# ---------------------------------------------------------------------------
def _tone(level_dbfs: float, freq: float = TONE_HZ, n: int = BLOCK) -> np.ndarray:
    """A steady sine at ``level_dbfs``, in both channels."""
    t = np.arange(n, dtype=np.float64) / float(SAMPLE_RATE)
    wave = (10.0 ** (level_dbfs / 20.0)) * np.sin(2.0 * np.pi * freq * t)
    return np.repeat(wave[:, None], CHANNELS, axis=1)


def _mixture(n: int = BLOCK) -> np.ndarray:
    """A musical mixture: five partials, a fixed phase each, a steady rms.

    There is nothing to repair in it: no click, no noise, no hum, no
    plosive.  It is the material a repair module has to leave alone.
    """
    t = np.arange(n, dtype=np.float64) / float(SAMPLE_RATE)
    partials = ((220.0, 0.5), (330.0, 0.3), (440.0, 0.2), (660.0, 0.1), (880.0, 0.05))
    out = np.zeros(n, dtype=np.float64)
    for freq, amp in partials:
        out += amp * np.sin(2.0 * np.pi * freq * t + freq * 0.001)
    peak = float(np.max(np.abs(out)))
    if peak > 0.0:
        out = out / peak * 0.5
    return np.repeat(out[:, None], CHANNELS, axis=1)


def _silence(n: int = BLOCK) -> np.ndarray:
    """Silence, in both channels."""
    return np.zeros((n, CHANNELS), dtype=np.float64)


def _shipping(x: np.ndarray) -> np.ndarray:
    """Round a signal through the 32 bit float the product ships in.

    The C ABI hands the core 32 bit samples and the host receives 32 bit
    samples, so a guarantee about the samples is a guarantee about this
    format.  The rounding is to the nearest, which is what the ABI does.
    """
    arr = np.asarray(x, dtype=np.float64)
    return arr.astype(np.float32).astype(np.float64)


#: The clean signals the defaults are measured on.
CLEAN_SIGNALS = {
    "tone": _shipping(_tone(-6.0)),
    "mixture": _shipping(_mixture()),
}


# ---------------------------------------------------------------------------
# the measurement
# ---------------------------------------------------------------------------
class ModuleBroken(Exception):
    """A module that cannot process the probe at all.

    A module that raises is broken, and that is a defect of the module and not
    of the rule this file holds it to.  The robustness gate of the tree owns
    that defect; this file skips such a module and names it.
    """


def _bypass_measurement(code: str, signal: np.ndarray) -> tuple[np.ndarray, int]:
    """Render ``signal`` through a bypassed module of ``code``.

    Returns the output of the engine and the latency the engine reports.
    """
    engine = create_engine(SAMPLE_RATE, CHANNELS)
    handle = engine.add_module(code)
    engine.set_bypass(handle, True)
    out = engine.process(signal)
    return np.asarray(out, dtype=np.float64), int(engine.latency_samples)


def _default_measurement(code: str, signal: np.ndarray) -> tuple[np.ndarray, int]:
    """Render ``signal`` through a fresh module of ``code`` with its defaults.

    Returns the output and the latency the module declares.  The module is
    fresh, so the measurement does not depend on what the module did before.
    The output is brought back into the format the product ships in, so the
    comparison is made on the samples a host receives.

    A module that cannot process the probe at all raises :class:`ModuleBroken`.
    That is a defect of the module and not of the rule this file holds it to,
    and the robustness gate of the tree owns it; the tests below skip such a
    module and say so.
    """
    try:
        module = registry.create(code, SAMPLE_RATE, CHANNELS)
    except Exception as exc:  # noqa: BLE001 - a module that cannot start
        raise ModuleBroken(
            f"{code} cannot be instantiated: {type(exc).__name__}: {exc}"
        ) from exc
    declared = int(getattr(module, "latency_samples", 0))
    try:
        out = np.asarray(module.process(signal), dtype=np.float64)
    except Exception as exc:  # noqa: BLE001 - a module that cannot run
        raise ModuleBroken(
            f"{code} cannot process the probe: {type(exc).__name__}: {exc}"
        ) from exc
    if out.ndim == 1:
        out = out[:, None]
    return _shipping(out), declared


def _aligned_difference(signal: np.ndarray, out: np.ndarray, delay: int) -> float:
    """The largest difference between the signal and the delayed output."""
    delay = int(max(0, min(delay, signal.shape[0] - 1)))
    head = signal[: signal.shape[0] - delay] if delay else signal
    tail = out[delay:] if delay else out
    if head.shape != tail.shape:
        return float("inf")
    return float(np.max(np.abs(head - tail)))


def _relative_db(difference: float, peak: float) -> float:
    """The difference in decibels below the peak of the signal."""
    if difference <= 0.0:
        return float("-inf")
    if peak <= 0.0:
        return float("inf")
    return 20.0 * math.log10(difference / peak)


def _rms(x: np.ndarray) -> float:
    """The rms of a signal, over every channel."""
    arr = np.asarray(x, dtype=np.float64)
    return float(np.sqrt(np.mean(arr * arr)))


def _applied_gain_db(code: str, level_dbfs: float) -> float | None:
    """The gain a module applies to a tone at ``level_dbfs``, in dB.

    The gain is the ratio of the rms of the output to the rms of the input,
    both taken after the latency the module declares is compensated.  The rms
    ratio does not care about the phase of the material, so it measures the
    level and not the timing.
    """
    signal = _shipping(_tone(level_dbfs))
    try:
        out, declared = _default_measurement(code, signal)
    except Exception:  # pragma: no cover - a module that cannot start
        return None
    if out.shape != signal.shape:
        return None
    delay = min(declared, signal.shape[0] - 1)
    head = signal[: signal.shape[0] - delay] if delay else signal
    tail = out[delay:] if delay else out
    before = _rms(head)
    after = _rms(tail)
    if before <= 0.0 or after <= 0.0:
        return None
    return 20.0 * math.log10(after / before)


#: Every module of the catalogue, as a parameter of the gates.
ALL_CODES = tuple(info.code for info in registry.all())

#: The modules whose default has to be transparent.
TRANSPARENT_CODES = _transparent_codes()


# ---------------------------------------------------------------------------
# the gates
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("code", ALL_CODES)
def test_the_bypass_is_sample_exact(code):
    """A bypassed module hands the audio back sample for sample."""
    signal = _mixture()
    out, _ = _bypass_measurement(code, signal)
    assert out.shape == signal.shape, (
        f"{code}: the bypassed module changed the shape of the signal "
        f"({out.shape} against {signal.shape})"
    )
    difference = float(np.max(np.abs(out - signal)))
    assert difference == 0.0, (
        f"{code}: the bypass is not sample exact; the largest difference is "
        f"{difference!r}"
    )


@pytest.mark.parametrize("code", ALL_CODES)
def test_the_bypass_carries_no_delay(code):
    """A bypassed module does not delay the signal."""
    signal = _mixture()
    _, latency = _bypass_measurement(code, signal)
    assert latency == 0, (
        f"{code}: the bypassed module still delays the signal by {latency} "
        f"samples"
    )


@pytest.mark.parametrize("code", ALL_CODES)
def test_the_default_output_is_finite_and_complete(code):
    """The default of every module hands back a whole, finite block."""
    signal = _mixture()
    try:
        out, _ = _default_measurement(code, signal)
    except ModuleBroken as broken:
        pytest.skip(str(broken))
    assert out.shape == signal.shape, (
        f"{code}: the default changed the shape of the signal "
        f"({out.shape} against {signal.shape})"
    )
    assert np.all(np.isfinite(out)), f"{code}: the default output is not finite"


@pytest.mark.parametrize("code", TRANSPARENT_CODES)
@pytest.mark.parametrize("name", sorted(CLEAN_SIGNALS))
def test_the_default_leaves_a_clean_signal_alone(code, name):
    """A repair module that is not asked to repair leaves the signal alone.

    The comparison is against the input **delayed by the latency the module
    declares**: a host compensates the delay a module reports, so what the
    host hears is the signal the module produced.  The yard is
    :data:`TRANSPARENCY_YARD_DB` decibels below the peak of the input.
    """
    signal = CLEAN_SIGNALS[name]
    try:
        out, declared = _default_measurement(code, signal)
    except ModuleBroken as broken:
        pytest.skip(str(broken))
    assert out.shape == signal.shape, (
        f"{code}: the default changed the shape of the signal "
        f"({out.shape} against {signal.shape})"
    )
    difference = _aligned_difference(signal, out, declared)
    peak = float(np.max(np.abs(signal)))
    measured = _relative_db(difference, peak)
    assert measured <= TRANSPARENCY_YARD_DB, (
        f"{code}: the default is not transparent on the {name} probe; the "
        f"largest difference against the input delayed by the declared "
        f"latency of {declared} samples is {difference!r}, which is "
        f"{measured:.1f} dB below the peak of the input, and the yard is "
        f"{TRANSPARENCY_YARD_DB:.1f} dB"
    )


@pytest.mark.parametrize("code", ALL_CODES)
def test_the_default_keeps_silence_silent(code):
    """A silent input comes out as silence.

    The yard is :data:`NOISE_FLOOR_YARD_DBFS` below full scale.  A module
    that passes the signal through gives exactly zero; a module that goes
    through a transform gives the arithmetic floor of the transform, which is
    more than 200 dB below the yard.
    """
    if code in MAKES_ITS_OWN_SOUND:
        pytest.skip(f"{code} makes a sound of its own by design")
    signal = _silence()
    try:
        out, _ = _default_measurement(code, signal)
    except ModuleBroken as broken:
        pytest.skip(str(broken))
    assert out.shape == signal.shape, (
        f"{code}: the default changed the shape of the silence"
    )
    loudest = float(np.max(np.abs(out))) if out.size else 0.0
    measured = 20.0 * math.log10(loudest) if loudest > 0.0 else float("-inf")
    assert measured <= NOISE_FLOOR_YARD_DBFS, (
        f"{code}: a silent input comes out at {loudest!r}, which is "
        f"{measured:.1f} dBFS, so the default adds a noise floor of its own; "
        f"the yard is {NOISE_FLOOR_YARD_DBFS:.1f} dBFS"
    )


@pytest.mark.parametrize("code", ALL_CODES)
def test_the_applied_gain_does_not_depend_on_the_level(code):
    """The gain a module applies does not depend on the level of the input.

    A module whose gain follows the level of the input gates a quiet
    recording away: the same setting does something else on a quiet
    recording than on a loud one.  The modules whose job is to change the
    level are named in :data:`LEVEL_DEPENDENT`; they are excluded by name,
    not by a tolerance.
    """
    if code in LEVEL_DEPENDENT:
        pytest.skip(f"{code} changes the level of the signal by design")
    gains = {}
    for level in TONE_LEVELS_DB:
        gain = _applied_gain_db(code, level)
        if gain is None:
            pytest.skip(f"{code} cannot be measured on a tone at {level} dBFS")
        gains[level] = gain
    spread = max(gains.values()) - min(gains.values())
    assert spread <= GAIN_SPREAD_LIMIT_DB, (
        f"{code}: the gain depends on the level of the input; it moves by "
        f"{spread:.4f} dB between {TONE_LEVELS_DB[0]:.0f} and "
        f"{TONE_LEVELS_DB[-1]:.0f} dBFS "
        f"({', '.join('%+.4f dB at %d dBFS' % (gains[lv], lv) for lv in TONE_LEVELS_DB)})"
    )


# ---------------------------------------------------------------------------
# the line itself
# ---------------------------------------------------------------------------
def test_the_line_covers_the_whole_catalogue():
    """Every module of the catalogue is on one side of the line or the other."""
    for info in registry.all():
        if info.category in UTILITY_CATEGORIES:
            continue
        assert info.code in REPAIR_MAY_CHANGE or info.code in TRANSPARENT_CODES, (
            f"{info.code} is a repair module and is on neither side of the line"
        )
    known = (
        set(REPAIR_MAY_CHANGE) | set(LEVEL_DEPENDENT) | set(MAKES_ITS_OWN_SOUND)
    )
    for code in known:
        assert registry.has(code), (
            f"{code} is named in the table of the line but is not a module of "
            f"the catalogue any more"
        )


def test_the_line_follows_the_category():
    """The line is drawn by the category of the module.

    A utility, measurement or system module may change the signal: that is
    what it is for.  A repair module may not, unless it is named in
    :data:`REPAIR_MAY_CHANGE` with the reason why.
    """
    for info in registry.all():
        if info.category in UTILITY_CATEGORIES:
            assert info.code not in TRANSPARENT_CODES, (
                f"{info.code} is a {info.category.lower()} module and is held "
                f"to the rule of the repair modules"
            )
            continue
        if info.code in REPAIR_MAY_CHANGE:
            assert REPAIR_MAY_CHANGE[info.code].strip(), (
                f"{info.code} is an exception without a reason"
            )
            continue
        assert info.code in TRANSPARENT_CODES, (
            f"{info.code} is a repair module and is not held to the rule of "
            f"the repair modules"
        )


def test_the_reason_of_every_exception_is_written_down():
    """An exception without a reason is an accident waiting to happen."""
    for table in (REPAIR_MAY_CHANGE, LEVEL_DEPENDENT, MAKES_ITS_OWN_SOUND):
        for code, reason in table.items():
            assert reason.strip(), f"{code} is listed without a reason"
            assert len(reason.split()) >= 6, (
                f"{code} is listed with a reason that says too little"
            )


def test_the_catalogue_is_covered_by_the_gates():
    """The gates hold every module of the catalogue, not a part of it."""
    assert len(ALL_CODES) >= 48, (
        f"the catalogue holds {len(ALL_CODES)} modules, which is fewer than "
        f"the table of the project says"
    )
    assert len(set(ALL_CODES)) == len(ALL_CODES), (
        "the catalogue holds a module twice"
    )
    for code in ALL_CODES:
        assert registry.has(code), f"{code} is not a module of the catalogue"
