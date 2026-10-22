"""
tests.test_module_robustness — the gate against uninitialised memory and
against the digital ceiling.

A module that allocates a buffer with ``numpy.empty`` and then fills it in
parts carries a defect: the part that is never written holds whatever the
allocator handed out.  The symptom is not a crash but a wrong answer that
changes from run to run — the same input comes back different, and once in a
while the module returns silence.

A signal that leaves the product above the digital ceiling carries a defect
of the other kind: the product writes files, and a file that holds a sample
outside [−1, 1] is clipped by the writer.  The clip is a distortion that
nothing downstream can undo.

The gate has five parts.

``test_output_is_finite``
    No module may return a NaN or an infinity, and none may raise.

``test_output_keeps_the_length``
    A module has to hand back the number of samples it was given.  The only
    exceptions are the modules that resample or stretch time on purpose; they
    are recognised by their own parameters.

``test_output_does_not_depend_on_the_allocator``
    The allocator is poisoned: every buffer that the core allocates comes back
    filled with a marker instead of with whatever was in the memory.  The
    module is run twice with two different markers and the two results have to
    be identical.  A module that reads even one uninitialised sample comes
    back different and the gate fails.

``test_two_runs_agree``
    Two runs of the same block have to give the same answer.  The modules
    that draw noise seed their generator with a fixed number, so a fresh
    instance always produces the same samples.

``test_the_chain_stays_inside_the_ceiling``
    The **chain** hands back a signal inside the digital ceiling, unless one
    of its own level controls asks for more.  The rule of the level is not
    inside a module any more: it is at the output of the product, so this is
    where it is held.  A plug-in is a chain of one module — that is exactly
    how the adapter builds it — so this is also the promise the plug-in
    makes to its host.

The test is deterministic: the markers are fixed numbers and every module is
built from a fixed seed, so a failure is always reproducible.
"""
from __future__ import annotations

import os
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurcore import registry  # noqa: E402
from aurcore.api import Engine  # noqa: E402
from aurcore.ceiling import (  # noqa: E402
    CEILING,
    LEVEL_CONTROLS,
    TOLERANCE,
    chain_limit,
    declared_gain,
    peak_of,
)

#: The sample rate every module is exercised at.
SAMPLE_RATE = 48_000

#: The channel count every module is exercised at.
CHANNELS = 2

#: The block lengths the sweep uses.  The short ones matter: a module that
#: only misbehaves on a block shorter than its own analysis window is not
#: caught by a sweep that always uses 4096 samples.
FRAME_SIZES = (1, 2, 64, 480, 4096)

#: The two markers the allocator is poisoned with.  They have opposite signs
#: and a magnitude that no audio signal reaches, so a buffer that is read
#: before it is written shows up at once.
POISON_A = 1.0e300
POISON_B = -1.0e300

#: The directory the core lives in.  Only the buffers that are allocated by
#: the core are poisoned: NumPy's own scratch space is none of the core's
#: business.
CORE_DIR = os.path.normcase(str(ROOT / "aurcore"))


# ---------------------------------------------------------------------------
# The awkward inputs.
# ---------------------------------------------------------------------------
def awkward_signal(name: str, frames: int, channels: int = CHANNELS) -> np.ndarray:
    """Return one of the inputs that break a module."""
    t = np.arange(frames, dtype=np.float64) / float(SAMPLE_RATE)
    if name == "silence":
        return np.zeros((frames, channels), dtype=np.float64)
    if name == "dc":
        return np.full((frames, channels), 0.5, dtype=np.float64)
    if name == "square":
        wave = np.sign(np.sin(2.0 * np.pi * 220.0 * t))
        return np.repeat(wave[:, None], channels, axis=1)
    if name == "impulse":
        impulse = np.zeros((frames, channels), dtype=np.float64)
        if frames:
            impulse[frames // 2, 0] = 1.0
        return impulse
    if name == "tone":
        wave = 0.5 * np.sin(2.0 * np.pi * 1000.0 * t)
        return np.repeat(wave[:, None], channels, axis=1)
    raise ValueError(f"unknown input {name!r}")


INPUT_NAMES = ("silence", "dc", "square", "impulse", "tone")


# ---------------------------------------------------------------------------
# The digital ceiling.
# ---------------------------------------------------------------------------
#: The digital ceiling: the largest value a sample may have.  A file that is
#: written in integers holds the samples from −1 to 1 and nothing else, so a
#: sample outside that range is clipped by the writer.
CEILING_VALUE = CEILING

#: How far above the ceiling a sample may be and still count as inside.
#:
#: The tolerance is there for the arithmetic and not for the signal.  A
#: double precision filter that is transparent to within 1e-15 still hands
#: back 1.0000000000000002 for a full scale input, so a gate that called that
#: a defect would fail every module that does nothing at all.  1e-6 is
#: −120 dBFS: far below anything anybody can hear, four orders of magnitude
#: below the last bit of a 16 bit file (1/32768 = 3.05e-5), and four orders
#: of magnitude above the rounding noise of the arithmetic.
#:
#: A repair module may only attenuate, so its tolerance is this one and no
#: more: 1e-6 is "zero" for a signal, and a repair module that raises the
#: level of the material by a thousandth of a decibel is broken.
CEILING_TOLERANCE = TOLERANCE

#: The number of frames of every edge case.
CEILING_FRAMES = 4096

#: The number of frames of silence that follow every edge case, so that the
#: ring-out of a module is measured as well: a module that rings past the
#: ceiling is just as wrong as one that does it in the first block.
CEILING_RING = 1024

#: The block size the edge cases are fed in.  It is the block size the study
#: of the edges used, so the numbers of the two are comparable.
CEILING_BLOCK = 256

#: The modules that carry the user's own level control: the parameters that
#: decide how loud the module's output is.  A module that is not in this
#: table may only attenuate, and the ceiling is hard for it.
#:
#: The *range* a module may reach is read from the module's own parameter
#: table, so this table only says which parameter is the level control.  The
#: gate reads the table of the core (which is the rule itself) and checks
#: that the two agree, so the rule cannot drift away from the gate.
LEVEL_CONTROL_TABLE = LEVEL_CONTROLS

#: The modules that make a sound of their own.  A generator adds its own
#: signal to the incoming audio, and the level of that signal is the user's
#: own control, so the level it adds is not the module's business.  The
#: generator is checked by a test of its own: the gate asks whether the level
#: it adds is the level its own parameters declare.  The *chain* it is in
#: still hands back a signal inside the ceiling: the rule is at the output.
GENERATORS = ("AUR_SIGNALGEN",)


def ceiling_signal(name: str, frames: int, channels: int = CHANNELS) -> np.ndarray:
    """Return one of the signals that sit at the digital ceiling.

    ``sine_full``
        A full scale sine: 1 kHz at the ceiling.
    ``square_full``
        A full scale square: the level the format can carry, on every
        sample, full bandwidth.
    ``impulse_train``
        A full scale impulse train: one sample of +1.0 every 48 samples.
    ``clipped``
        A signal that has already been clipped: a loud sine with flat tops.
        It is the case a file arrives in.
    ``lowfreq``
        A signal at 0 dBFS with a strong low frequency: 40 Hz with its
        harmonics, scaled onto the ceiling.
    ``dc_full``
        A full scale constant.
    ``impulse``
        One single sample of 1.0.
    """
    t = np.arange(frames, dtype=np.float64) / float(SAMPLE_RATE)
    if name == "sine_full":
        wave = np.sin(2.0 * np.pi * 1000.0 * t)
    elif name == "square_full":
        wave = np.sign(np.sin(2.0 * np.pi * 220.0 * t))
    elif name == "impulse_train":
        wave = np.zeros(frames, dtype=np.float64)
        wave[::48] = 1.0
    elif name == "clipped":
        wave = np.clip(4.0 * np.sin(2.0 * np.pi * 997.0 * t), -1.0, 1.0)
    elif name == "lowfreq":
        wave = np.zeros(frames, dtype=np.float64)
        for harmonic, amp in ((1, 1.0), (2, 0.25), (3, 0.12), (4, 0.06)):
            wave += amp * np.sin(2.0 * np.pi * 40.0 * harmonic * t)
        peak = float(np.max(np.abs(wave)))
        if peak > 0.0:
            wave = wave / peak
    elif name == "dc_full":
        wave = np.ones(frames, dtype=np.float64)
    elif name == "impulse":
        wave = np.zeros(frames, dtype=np.float64)
        wave[0] = 1.0
    else:
        raise ValueError(f"unknown ceiling input {name!r}")
    return np.repeat(wave[:, None], channels, axis=1)


#: The edge cases the ceiling is measured on.
CEILING_CASES = (
    "sine_full",
    "square_full",
    "impulse_train",
    "clipped",
    "lowfreq",
    "dc_full",
    "impulse",
)


def run_blocks(
    code: str,
    signal: np.ndarray,
    block: int = CEILING_BLOCK,
    prepare=None,
) -> np.ndarray:
    """Run one module over one signal, block by block, the way a host does.

    The blocks are fed through the engine, so the module sees the stream the
    way it sees it in a plug-in, and the ring-out of the module is part of
    the result.

    ``prepare`` is called with the module the chain holds, before the first
    block: it is how the gate puts a module into the state the test wants
    it in.
    """
    engine = Engine(sample_rate=SAMPLE_RATE, channels=CHANNELS)
    try:
        handle = engine.add_module(code)
        assert handle is not None, f"module {code} is not registered"
        if prepare is not None:
            prepare(handle.module)
        pieces = []
        for start in range(0, signal.shape[0], block):
            chunk = signal[start:start + block]
            if chunk.shape[0] == 0:
                continue
            pieces.append(np.asarray(engine.process(chunk), dtype=np.float64))
        if not pieces:
            return np.zeros((0, CHANNELS), dtype=np.float64)
        return np.concatenate(pieces, axis=0)
    finally:
        close = getattr(engine, "close", None)
        if callable(close):
            close()


def chain_limit_for(code: str) -> float:
    """Return the largest peak the chain of one module may hand back.

    The limit is the digital ceiling, multiplied by the gain the chain's own
    level control declares.  A chain that has no level control may only
    attenuate, so its limit is the ceiling itself: a full scale input has to
    come back inside [−1, 1].

    The number is read from the rule of the core, so the gate and the rule
    cannot drift apart.
    """
    module = registry.create(code, SAMPLE_RATE, CHANNELS)
    return chain_limit([module])


# ---------------------------------------------------------------------------
# The poisoned allocator.
# ---------------------------------------------------------------------------
class PoisonedAllocator:
    """Hand out poisoned buffers instead of the memory the allocator had.

    Only the allocations that the core itself asks for are poisoned.  The
    caller is looked at in the call stack: a buffer that NumPy allocates for
    its own bookkeeping is left alone.
    """

    def __init__(self, marker: float) -> None:
        self.marker = float(marker)
        self.poisoned = 0
        self._real_empty = np.empty
        self._real_empty_like = np.empty_like

    @staticmethod
    def _core_allocated() -> bool:
        """True when the code that asked for the buffer lives in the core."""
        try:
            frame = sys._getframe(2)
        except ValueError:  # pragma: no cover - the stack is never that shallow
            return False
        while frame is not None:
            name = os.path.normcase(frame.f_code.co_filename)
            if name.startswith(CORE_DIR):
                return True
            if "site-packages" in name or "pythoncore" in name:
                return False
            frame = frame.f_back
        return False

    def _fill(self, array: np.ndarray) -> np.ndarray:
        if self._core_allocated():
            self.poisoned += 1
            array[...] = self.marker
        return array

    def empty(self, *args, **kwargs):
        """Stand in for ``numpy.empty``."""
        return self._fill(self._real_empty(*args, **kwargs))

    def empty_like(self, *args, **kwargs):
        """Stand in for ``numpy.empty_like``."""
        return self._fill(self._real_empty_like(*args, **kwargs))

    def __enter__(self) -> "PoisonedAllocator":
        np.empty = self.empty  # type: ignore[assignment]
        np.empty_like = self.empty_like  # type: ignore[assignment]
        return self

    def __exit__(self, *exc_info) -> bool:
        np.empty = self._real_empty  # type: ignore[assignment]
        np.empty_like = self._real_empty_like  # type: ignore[assignment]
        return False


# ---------------------------------------------------------------------------
# Running a module.
# ---------------------------------------------------------------------------
def module_codes() -> list[str]:
    """Return the code of every registered module."""
    return sorted(info.code for info in registry.all())


def run_module(code: str, signal: np.ndarray, marker: float | None = None) -> np.ndarray:
    """Run one module over one block and return what it hands back.

    With ``marker`` the allocator is poisoned, so every buffer the core
    allocates comes back filled with that number instead of with the memory
    the allocator had.
    """
    engine = Engine(sample_rate=SAMPLE_RATE, channels=CHANNELS)
    try:
        handle = engine.add_module(code)
        assert handle is not None, f"module {code} is not registered"
        if marker is None:
            out = engine.process(signal)
        else:
            with PoisonedAllocator(marker):
                with np.errstate(all="ignore"):
                    out = engine.process(signal)
        return np.asarray(out, dtype=np.float64)
    finally:
        close = getattr(engine, "close", None)
        if callable(close):
            close()


def expected_length(code: str, frames: int) -> int:
    """How many samples the module has to hand back.

    Most modules keep the length of the block.  A resampler and a time
    stretcher change it on purpose, and they are recognised by their own
    parameters.
    """
    module = registry.create(code, SAMPLE_RATE, CHANNELS)
    for desc in module.describe():
        if desc.name == "Sample Rate" and desc.type == "enum":
            try:
                return int(round(frames * float(module.params.get(desc.name)) / float(SAMPLE_RATE)))
            except (TypeError, ValueError):
                break
    for desc in module.describe():
        if desc.name in ("Ratio", "Stretch Ratio") and desc.group == "Time":
            try:
                return int(round(frames * float(module.params.get(desc.name))))
            except (TypeError, ValueError):
                break
    return int(frames)


#: Every (code, input name, block length) the gate walks over.
CASES = [
    (code, name, frames)
    for code in module_codes()
    for frames in FRAME_SIZES
    for name in INPUT_NAMES
]
CASE_IDS = [f"{code}-{name}-{frames}" for code, name, frames in CASES]


# ---------------------------------------------------------------------------
# The gate.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("code,name,frames", CASES, ids=CASE_IDS)
def test_output_is_finite(code: str, name: str, frames: int):
    """A module must not raise and must not return a NaN or an infinity."""
    signal = awkward_signal(name, frames)
    out = run_module(code, signal)
    assert out.ndim == 2, f"{code} returned a {out.ndim}-dimensional array"
    assert out.shape[1] == CHANNELS, f"{code} returned {out.shape[1]} channels"
    bad = int(np.count_nonzero(~np.isfinite(out)))
    assert bad == 0, f"{code} returned {bad} non-finite sample(s) for {name}"


@pytest.mark.parametrize("code,name,frames", CASES, ids=CASE_IDS)
def test_output_keeps_the_length(code: str, name: str, frames: int):
    """A module must hand back the number of samples it was given."""
    signal = awkward_signal(name, frames)
    out = run_module(code, signal)
    wanted = expected_length(code, frames)
    assert out.shape[0] == wanted, (
        f"{code} returned {out.shape[0]} samples for a block of {frames} "
        f"({name}); it has to return {wanted}"
    )


@pytest.mark.parametrize("code,name,frames", CASES, ids=CASE_IDS)
def test_output_does_not_depend_on_the_allocator(code: str, name: str, frames: int):
    """The result must not depend on what the allocator handed out.

    The core allocates its buffers with ``numpy.empty``, so a buffer that is
    filled in parts holds whatever was in the memory.  The allocator is
    poisoned twice with two different markers: if the module reads even one
    of those samples the two results differ, and the gate fails.
    """
    signal = awkward_signal(name, frames)
    first = run_module(code, signal, marker=POISON_A)
    second = run_module(code, signal, marker=POISON_B)
    assert first.shape == second.shape, (
        f"{code} returned {first.shape} with one allocator and {second.shape} with the other"
    )
    if not np.array_equal(first, second, equal_nan=True):
        difference = np.abs(first - second)
        difference = difference[np.isfinite(difference)]
        worst = float(difference.max()) if difference.size else 0.0
        differing = int(np.count_nonzero(~np.isclose(first, second, equal_nan=True)))
        raise AssertionError(
            f"{code} depends on what the allocator handed out: {differing} "
            f"sample(s) differ by up to {worst:.6g} for {name}"
        )


@pytest.mark.parametrize("code", module_codes())
def test_two_runs_agree(code: str):
    """Two runs of the same block must give the same answer.

    The modules that draw noise seed their generator with a fixed number, so
    a fresh instance always produces the same samples.  A result that changes
    from run to run is the symptom of a buffer that is read before it is
    written.
    """
    signal = awkward_signal("tone", 1024)
    first = run_module(code, signal)
    second = run_module(code, signal)
    assert np.array_equal(first, second, equal_nan=True), (
        f"{code} returned a different result for the same input on a second run"
    )


def test_the_poison_reaches_the_core():
    """The poison has to reach the core, or the gate above tests nothing.

    The envelope follower allocates its buffer with ``numpy.empty`` and fills
    it one column at a time, so it is a cheap probe for the mechanism.
    """
    from aurcore.dsp.util import envelope_follower

    signal = np.abs(np.sin(2.0 * np.pi * 100.0 * np.arange(256) / 48000.0))[:, None]
    with PoisonedAllocator(POISON_A) as poison:
        envelope_follower(signal, 48000.0)
    assert poison.poisoned > 0, (
        "the poisoned allocator did not reach the core, so the gate would "
        "pass without testing anything"
    )


# ---------------------------------------------------------------------------
# The gate against the digital ceiling.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("code", module_codes())
def test_the_chain_stays_inside_the_ceiling(code: str):
    """The chain may not hand back a signal above the digital ceiling.

    The rule of the family lives at the **output** of the product now, and
    this is the output: the chain's own block.  The limit of the test is
    therefore the ceiling, multiplied by the gain the chain's own level
    control declares — which is 1.0 for every chain that may only attenuate,
    and for every chain at its default settings.

    A plug-in is a chain of one module (that is how the adapter builds it),
    so this is also the promise the plug-in makes to its host: nothing above
    the ceiling leaves the plug-in.

    The tolerance is :data:`CEILING_TOLERANCE` (1e-6, −120 dBFS).  It is
    there for the rounding of the arithmetic and not for the signal: a
    chain that may only attenuate may not raise the level of the material by
    a thousandth of a decibel.
    """
    limit = chain_limit_for(code)
    tail = np.zeros((CEILING_RING, CHANNELS), dtype=np.float64)
    for name in CEILING_CASES:
        signal = ceiling_signal(name, CEILING_FRAMES)
        out = run_blocks(code, np.concatenate((signal, tail), axis=0))
        peak = peak_of(out)
        assert peak <= limit + CEILING_TOLERANCE, (
            f"{code} handed back a peak of {peak:.6f} for {name}, the limit "
            f"is {limit:.6f} (the ceiling is {CEILING_VALUE:.6f} and the "
            f"chain's own level control allows {limit / CEILING_VALUE:.6f})"
        )


@pytest.mark.parametrize("code", module_codes())
def test_the_written_file_never_exceeds_the_ceiling(code: str, tmp_path):
    """A file the product writes never holds a sample outside [−1, 1].

    The product writes files, and a file that holds a sample outside the
    range of the format is clipped by the writer — a distortion that nothing
    downstream can undo.  The writer is the one of the server
    (:func:`aurserver.audio.encode_wav`), and the file is read back here, so
    the gate holds the file and not the block that went into it.
    """
    from aurserver.audio import encode_wav

    signal = ceiling_signal("square_full", 2048)
    out = run_blocks(code, signal)
    path = tmp_path / "render.wav"
    path.write_bytes(encode_wav(out, SAMPLE_RATE, 16))
    with wave.open(str(path), "rb") as handle:
        frames = handle.getnframes()
        raw = handle.readframes(frames)
    written = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32767.0
    peak = float(np.max(np.abs(written))) if written.size else 0.0
    assert peak <= CEILING_VALUE + CEILING_TOLERANCE, (
        f"the file of {code} holds a sample at {peak:.6f}, the ceiling is "
        f"{CEILING_VALUE:.6f}"
    )


@pytest.mark.parametrize("code", module_codes())
def test_the_level_control_of_a_chain_is_inside_its_own_range(code: str):
    """The exemption of the level control is the parameter's own range.

    A chain that carries the user's own gain control may take the signal
    over the ceiling — that is the user's decision — but it may not take it
    further than its own knob asks for.  The knob is set to the top of its
    declared range here, and the chain has to stay inside the level that
    range declares.
    """
    names = LEVEL_CONTROL_TABLE.get(code, ())
    if not names:
        pytest.skip(f"{code} has no level control: it may only attenuate")

    def _prepare(module):
        """Put the level control of the module at the top of its range."""
        for name in names:
            desc = None
            for candidate in module.describe():
                if candidate.name == name:
                    desc = candidate
                    break
            assert desc is not None, f"{code} has no parameter called {name!r}"
            if desc.type == "enum":
                continue
            module.params.set(name, desc.maximum)

    # the limit the chain declares with the knob at the top of its range
    probe = registry.create(code, SAMPLE_RATE, CHANNELS)
    _prepare(probe)
    limit = CEILING_VALUE * declared_gain(probe)
    signal = ceiling_signal("sine_full", 2048)
    out = run_blocks(code, signal, prepare=_prepare)
    peak = peak_of(out)
    assert peak <= limit + CEILING_TOLERANCE, (
        f"{code} handed back a peak of {peak:.6f} with its level control at "
        f"the top of its range, the limit is {limit:.6f}"
    )


def test_the_generator_adds_the_level_it_declares():
    """The generator makes a sound of its own, and the level is the user's.

    The level of the generated signal is the user's own control (the
    ``Amplitude`` and ``Level`` parameters), so the module is not held to
    the ceiling: the rule is at the output of the product, and the chain
    holds the line there.  What the gate asks is that the level the module
    adds is the level its own parameters declare.
    """
    for code in GENERATORS:
        module = registry.create(code, SAMPLE_RATE, CHANNELS)
        assert getattr(module, "generates", False), (
            f"{code} makes a sound of its own and has to say so"
        )
        silence = np.zeros((4096, CHANNELS), dtype=np.float64)
        generated = run_blocks(code, silence)
        amplitude = 10.0 ** (float(module.params.get("Amplitude")) / 20.0)
        level = 10.0 ** (float(module.params.get("Level")) / 20.0)
        # the tone is at the amplitude its parameter declares; the noise is
        # Gaussian, so its peak is a few times the level it is scaled to
        assert peak_of(generated) <= amplitude + 8.0 * level + CEILING_TOLERANCE, (
            f"{code} adds {peak_of(generated):.6f}, its own parameters "
            f"declare {amplitude:.6f} of tone and {level:.6f} of noise"
        )


def test_the_generator_is_clipped_at_the_output_of_the_chain():
    """The chain the generator is in hands back a signal inside the ceiling.

    The generator's own sound may be above the ceiling — the user asked for
    it — but the rule is at the output of the product, so the block the
    chain hands back is inside the ceiling.
    """
    for code in GENERATORS:
        silence = np.zeros((4096, CHANNELS), dtype=np.float64)
        out = run_blocks(code, silence)
        assert peak_of(out) <= CEILING_VALUE + CEILING_TOLERANCE, (
            f"the chain of {code} handed back a peak of {peak_of(out):.6f}"
        )


def test_the_ceiling_rule_and_the_gate_agree():
    """The rule of the core and the rule of the gate are the same rule.

    The gate reads the table of the core, so the two cannot drift apart
    silently: the table has to name a parameter that the module really has,
    and the parameter has to be expressed in a unit the rule understands.
    """
    for code, names in LEVEL_CONTROL_TABLE.items():
        module = registry.create(code, SAMPLE_RATE, CHANNELS)
        described = {desc.name: desc for desc in module.describe()}
        for name in names:
            assert name in described, (
                f"{code} has no parameter called {name!r}, so the level "
                f"control of the rule does not exist"
            )
            assert described[name].unit in ("dB", "dBFS", "%"), (
                f"{code}: the parameter {name!r} is expressed in "
                f"{described[name].unit!r}, which says nothing about the level"
            )
