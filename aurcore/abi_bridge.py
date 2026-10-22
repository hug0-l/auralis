"""
aurcore.abi_bridge — the Python side of the plug-in C ABI.

This module is called only from the C shim (``plugins/abi/aurcore_abi.c``).
It exists so that the C side never has to touch the numpy C API: the shim
passes raw bytes in and gets raw bytes back, and the reshaping happens here
where it belongs.

The audio path is deliberately allocation-light: one buffer in, one buffer out,
and the core does the rest.
"""
from __future__ import annotations

import json
from typing import Any

import numpy as np

__all__ = [
    "create_engine",
    "add_module",
    "set_param",
    "set_param_ramped",
    "set_param_text",
    "get_param",
    "set_bypass",
    "module_count",
    "latency_samples",
    "tail_samples",
    "output_sample_rate",
    "serialize",
    "deserialize",
    "process",
    "version",
]


def create_engine(sample_rate: int, channels: int):
    from .api import create_engine as _create

    return _create(int(sample_rate), int(channels))


def add_module(engine, code: str) -> int:
    """Append a module; returns its index in the chain."""
    handle = engine.add_module(code)
    return int(handle.index)


def set_param(engine, index: int, name: str, value: float) -> None:
    engine.set_param(int(index), name, value)


def set_param_ramped(engine, index: int, name: str, value: float, ramp_ms: float) -> None:
    """Set one parameter, applied over a ramp instead of as a step.

    The value moves from the one the parameter had to the one given here, in
    natural units, over ``ramp_ms`` milliseconds.  The parameter holds the
    value it is going to have right away, so the automation reaches its
    target even while the ramp runs.
    """
    engine.set_param(int(index), name, value, ramp_seconds=float(ramp_ms) / 1000.0)


def set_param_text(engine, index: int, name: str, text: str) -> None:
    engine.set_param(int(index), name, text)


def get_param(engine, index: int, name: str) -> str:
    """The current value of a parameter as text.

    Enum parameters return their choice string; numeric parameters return a
    plain decimal that ``getDoubleValue`` can parse.
    """
    value = engine.get_param(int(index), name)
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, str):
        return value
    return repr(float(value))


def set_bypass(engine, index: int, bypass: int) -> None:
    engine.set_bypass(int(index), bool(bypass))


def module_count(engine) -> int:
    return len(engine.chain)


def latency_samples(engine) -> int:
    return int(engine.latency_samples)


def tail_samples(engine) -> int:
    """The tail of the chain, in samples.

    ``aurcore.params.TAIL_INFINITE`` when a module of the chain never stops
    ringing.
    """
    return int(engine.tail_samples)


def output_sample_rate(engine) -> int:
    """The rate the chain's output runs at.

    It is the engine's own rate unless a module of the chain changes it
    (the resampler does), and the samples the chain hands over belong to
    it.
    """
    return int(engine.output_sample_rate)


def serialize(engine) -> str:
    return json.dumps(engine.serialize(), ensure_ascii=False)


def deserialize(engine, text: str) -> None:
    engine.deserialize(json.loads(text))


def process(engine, data: bytes, channels: int, frames: int,
            output_rate: int = 0) -> tuple[bytes, int, int]:
    """Run one block of audio.

    ``data`` holds ``channels`` planar float32 planes of ``frames`` samples
    each.  The result is the same layout.

    ``output_rate`` asks for the result at another rate.  Zero means the
    rate the chain works at, which is the rate of the samples the chain
    makes: a chain that changes the rate (the resampler) makes a
    different number of samples, and they belong to that rate.

    The result is ``(data, consumed, produced)``: the number of input
    samples the chain took and the number of output samples it made.
    """
    channels = int(channels)
    frames = int(frames)
    output_rate = int(output_rate)
    if channels <= 0 or frames <= 0:
        return b"", 0, 0
    block = np.frombuffer(data, dtype=np.float32).reshape(channels, frames).T
    if output_rate > 0:
        out = engine.process(block, output_rate)
    else:
        out = engine.process(block)
    produced = int(out.shape[0])
    out = np.ascontiguousarray(out, dtype=np.float32)
    if out.ndim == 1:
        out = out[:, None]
    if out.shape[1] != channels:
        if out.shape[1] == 1:
            out = np.repeat(out, channels, axis=1)
        elif out.shape[1] > channels:
            out = out[:, :channels]
        else:
            pad = np.zeros((out.shape[0], channels - out.shape[1]), dtype=np.float32)
            out = np.concatenate([out, pad], axis=1)
    # the ABI is planar: one plane per channel, so transpose before serialising
    return np.ascontiguousarray(out.T, dtype=np.float32).tobytes(), frames, produced


def version() -> str:
    from . import __version__

    return str(__version__)
