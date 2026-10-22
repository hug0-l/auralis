"""
tests/block_coherence.py — the block coherence gate, shared by the modules.

A module that is *block coherent* gives the same answer whether the host
hands it the whole recording or 64 samples at a time.  The quality harness
measures the difference between the two renders as the level of the residual
relative to the level of the one piece render; a module that is block
coherent stays at least 40 dB below it.

The measurement is the one of ``qa/survey``: the same signal is rendered
twice, once in one piece and once in blocks, the two are aligned by the delay
between them and the residual is measured.

The gate itself is at the bottom of this file: it holds the modules of the
utility family that measure or follow the level, and it holds the two rules
they all have to pass — the blocked render must not differ from the one piece
render, and the delay the module declares must be the delay the signal shows.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_SURVEY = Path(__file__).resolve().parents[1] / "qa" / "survey"
if str(_SURVEY) not in sys.path:
    sys.path.insert(0, str(_SURVEY))

import harness  # noqa: E402
import probes  # noqa: E402
import specs  # noqa: E402
import survey  # noqa: E402

__all__ = [
    "BLOCKS",
    "COHERENCE_LIMIT_DB",
    "GATE_CODES",
    "coherence_rows",
    "declared_latency",
    "host_prepare",
    "impulse_delay",
    "probe_signal",
]

#: The block sizes a plug-in host uses.
BLOCKS: tuple[int, ...] = (64, 128, 256)

#: The block coherence the quality harness asks for, in decibels.
COHERENCE_LIMIT_DB: float = survey.BLOCK_COHERENT_DB

#: The modules the gate of this file holds: the ones that measure the level
#: of the programme or that shape the material over time.  They all follow
#: the level on a grid that does not move with the block, so a host that
#: changes its block size gets the same answer.
GATE_CODES: tuple[str, ...] = (
    "AUR_LEVELER",
    "AUR_LOUDNESS",
    "AUR_STREAMPREVIEW",
    "AUR_CENTEREXTRACT",
    "AUR_FADE",
)


def probe_signal(code: str) -> np.ndarray:
    """The signal the survey measures the module with."""
    spec = specs.spec_for(code)
    probe = probes.build_probe(spec.probe, spec.duration_s)
    return np.asarray(probe.signal, dtype=np.float64)


def host_prepare(code: str, samples: int | None = None):
    """Return the callable that puts a fresh module into the host's state.

    A host knows two things about the material it plays: the parameters the
    user picked, and how long the material is.  The length is what a module
    that shapes the material over time needs to place its tail fade, so the
    callable declares it whenever the module knows about it.
    """
    spec = specs.spec_for(code)

    def _prepare(module) -> None:
        for name, value in spec.params:
            module.params.set(name, value)
        setter = getattr(module, "set_material_length", None)
        if setter is not None:
            setter(int(samples) if samples is not None else None)

    return _prepare


def coherence_rows(code: str, blocks: tuple[int, ...] = BLOCKS) -> list[dict]:
    """Measure the block coherence of a module, one row per block size."""
    signal = probe_signal(code)
    prepare = host_prepare(code, len(signal))
    rows: list[dict] = []
    for block in blocks:
        delta, lag, runaway = harness.measure_block_coherence(
            code, signal, block=int(block), prepare=prepare
        )
        rows.append(
            {
                "block": int(block),
                "delta_db": float(delta),
                "lag": int(lag),
                "runaway_db": float(runaway),
            }
        )
    return rows


def declared_latency(code: str, sample_rate: int = 48000, channels: int = 2) -> int:
    """The latency a module reports."""
    from aurcore import registry
    import aurcore.modules  # noqa: F401 - importing registers the modules

    module = registry.create(code, sample_rate, channels)
    return int(module.latency_samples)


def impulse_delay(
    code: str,
    sample_rate: int = 48000,
    channels: int = 2,
    frames: int = 20000,
) -> int:
    """The delay the signal itself shows, measured with the cross correlation.

    A burst of noise is used rather than an impulse: a click remover takes
    the impulse out of the signal, and the delay of the rest of the material
    is what the host has to compensate.  The burst is fed to every channel,
    so that a module that works on a stereo image is measured as well.
    """
    from aurcore import registry
    import aurcore.modules  # noqa: F401 - importing registers the modules

    module = registry.create(code, sample_rate, channels)
    rng = np.random.default_rng(20260101)
    burst = 0.02 * rng.standard_normal(frames)
    signal = np.repeat(burst[:, None], max(1, int(channels)), axis=1)
    out = np.asarray(module.process(signal), dtype=np.float64)
    lag = harness.cross_correlation_delay(signal[:, 0], out[:, 0])
    return -int(lag)


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("code", GATE_CODES)
def test_the_module_is_block_coherent(code):
    """A blocked render must not differ from a one piece render.

    The module follows the level of the programme or shapes the material over
    time, so the analysis has to sit on a grid that does not move with the
    block.  A host that renders in 64 sample blocks and a host that renders
    the whole file in one block have to reach the same answer.
    """
    for row in coherence_rows(code):
        assert row["delta_db"] <= COHERENCE_LIMIT_DB, (
            f"{code} at {row['block']} samples: the blocked render differs by "
            f"{row['delta_db']:.2f} dB, the gate asks for "
            f"{COHERENCE_LIMIT_DB:.1f} dB"
        )


@pytest.mark.parametrize("code", GATE_CODES)
def test_the_module_reports_the_delay_it_adds(code):
    """The module declares the latency it adds, and the signal shows it.

    A host compensates the latency a module declares, so the delay the signal
    really has has to be the number the module reports.
    """
    declared = declared_latency(code)
    measured = impulse_delay(code)
    assert measured == declared, (
        f"{code} reports {declared} samples of latency, the signal shows "
        f"{measured}"
    )


@pytest.mark.parametrize("code", GATE_CODES)
def test_the_module_keeps_the_length_and_stays_finite(code):
    """The output is as long as the input and holds no NaN or Inf."""
    from aurcore import registry
    import aurcore.modules  # noqa: F401 - importing registers the modules

    module = registry.create(code, 48000, 2)
    signal = np.zeros((4096, 2), dtype=np.float64)
    signal[:, 0] = 0.5 * np.sin(2.0 * np.pi * 200.0 * np.arange(4096) / 48000.0)
    pieces = [
        np.asarray(module.process(signal[start : start + 64]), dtype=np.float64)
        for start in range(0, signal.shape[0], 64)
    ]
    out = np.concatenate(pieces, axis=0)
    assert out.shape == signal.shape
    assert bool(np.all(np.isfinite(out)))
