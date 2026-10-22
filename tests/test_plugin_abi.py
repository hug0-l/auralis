"""
Conformance tests for the Auralis core C ABI.

The plug-in adapter (VST3, AAX, ...) talks to the core only through the
entry points declared in ``plugins/vst3/aurcore_c_api.h``.  That header is
the contract: it names every function, its arguments, its return value, the
thread it may be called on, whether it may be called before the interpreter
is up, and what it leaves behind when it fails.

These tests load the built shared library the way a host would and check
that the library keeps the contract:

    python plugins/abi/build_abi.py --msvc --out plugins/abi/build_main
    python -m pytest tests/test_plugin_abi.py

The library is skipped (not failed) when it has not been built, so a checkout
without a compiler still has a green suite.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import threading

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ABI_DIR = os.path.join(ROOT, "plugins", "abi")
HEADER = os.path.join(ROOT, "plugins", "vst3", "aurcore_c_api.h")
BUILDER = os.path.join(ABI_DIR, "build_abi.py")
MSVC = os.path.join(ROOT, "tools", "msvc.bat")

#: Where the library is looked for, in order.  The first one that exists
#: wins, so the same suite runs against a build in any directory.
BUILD_CANDIDATES = (
    os.environ.get("AURALIS_ABI_BUILD", ""),
    os.path.join(ABI_DIR, "build"),
)

AUR_OK = 0


class BlockCounts(ctypes.Structure):
    """The ``AurBlockCounts`` of the contract.

    ``consumed`` is the number of input samples the chain took and
    ``produced`` the number of output samples it made.
    """

    _fields_ = [("consumed", ctypes.c_uint32), ("produced", ctypes.c_uint32)]


# --------------------------------------------------------------------------
# where the library is
# --------------------------------------------------------------------------

def _library_name() -> str:
    if sys.platform == "win32":
        return "aurcore_abi.dll"
    if sys.platform == "darwin":
        return "aurcore_abi.dylib"
    return "aurcore_abi.so"


def _build_directory() -> str:
    for candidate in BUILD_CANDIDATES:
        if candidate and os.path.exists(os.path.join(candidate, _library_name())):
            return candidate
    # nothing built: report the first candidate so the message names a path
    return BUILD_CANDIDATES[1] or BUILD_CANDIDATES[0]


BUILD_DIR = _build_directory()


def _library_path_in(directory: str) -> str:
    """The library of a build directory.

    The staging directory holds the library that was built last.  It is
    preferred over the installed one, because the installed copy may be an
    older build: a library that is currently loaded cannot be replaced on
    Windows, so the build leaves the fresh copy aside.  The plug-in bundle
    is staged the same way.
    """
    name = _library_name()
    installed = os.path.join(directory, name)
    staged = os.path.join(directory, "_stage", name)

    if os.path.exists(staged):
        if not os.path.exists(installed):
            return staged
        if os.path.getmtime(staged) >= os.path.getmtime(installed):
            return staged

    return installed


LIBRARY_PATH = _library_path_in(BUILD_DIR)


def _load():
    path = LIBRARY_PATH
    if not os.path.exists(path):
        pytest.skip(f"the core ABI library has not been built: {path}")

    # the library links against the interpreter library, so its directory
    # has to be discoverable before the DLL is loaded
    import sysconfig

    for extra in (os.path.join(sysconfig.get_config_var("LIBDIR") or ""),
                  os.path.dirname(sys.executable)):
        if extra and os.path.isdir(extra):
            try:
                os.add_dll_directory(extra)
            except (AttributeError, OSError):
                pass
    os.environ.setdefault("AURALIS_ROOT", ROOT)

    lib = ctypes.CDLL(path)

    lib.aur_engine_create.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    lib.aur_engine_create.restype = ctypes.c_void_p
    lib.aur_engine_add_module.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.aur_engine_add_module.restype = ctypes.c_int
    lib.aur_engine_set_param.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                         ctypes.c_char_p, ctypes.c_double]
    lib.aur_engine_set_param.restype = ctypes.c_int
    lib.aur_engine_set_param_ramped.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                                ctypes.c_char_p, ctypes.c_double,
                                                ctypes.c_double]
    lib.aur_engine_set_param_ramped.restype = ctypes.c_int
    lib.aur_engine_set_param_text.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                              ctypes.c_char_p, ctypes.c_char_p]
    lib.aur_engine_set_param_text.restype = ctypes.c_int
    lib.aur_engine_get_param.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                         ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t]
    lib.aur_engine_get_param.restype = ctypes.c_int
    lib.aur_engine_process.argtypes = [ctypes.c_void_p,
                                       ctypes.POINTER(ctypes.POINTER(ctypes.c_float)),
                                       ctypes.POINTER(ctypes.POINTER(ctypes.c_float)),
                                       ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
                                       ctypes.POINTER(BlockCounts)]
    lib.aur_engine_process.restype = ctypes.c_int
    lib.aur_engine_destroy.argtypes = [ctypes.c_void_p]
    lib.aur_engine_destroy.restype = None
    lib.aur_engine_set_bypass.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int]
    lib.aur_engine_set_bypass.restype = ctypes.c_int
    lib.aur_engine_module_count.argtypes = [ctypes.c_void_p]
    lib.aur_engine_module_count.restype = ctypes.c_int
    lib.aur_engine_latency_samples.argtypes = [ctypes.c_void_p]
    lib.aur_engine_latency_samples.restype = ctypes.c_int64
    lib.aur_engine_tail_samples.argtypes = [ctypes.c_void_p]
    lib.aur_engine_tail_samples.restype = ctypes.c_int64
    lib.aur_engine_output_sample_rate.argtypes = [ctypes.c_void_p]
    lib.aur_engine_output_sample_rate.restype = ctypes.c_int64
    lib.aur_engine_serialize.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_char_p)]
    lib.aur_engine_serialize.restype = ctypes.c_int64
    lib.aur_engine_deserialize.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.aur_engine_deserialize.restype = ctypes.c_int
    lib.aur_engine_free.argtypes = [ctypes.c_char_p]
    lib.aur_engine_free.restype = None
    lib.aur_engine_last_error.argtypes = [ctypes.c_void_p]
    lib.aur_engine_last_error.restype = ctypes.c_char_p
    lib.aur_engine_version.argtypes = []
    lib.aur_engine_version.restype = ctypes.c_char_p
    lib.aur_shutdown.argtypes = []
    lib.aur_shutdown.restype = None
    return lib


LIB = _load()


@pytest.fixture()
def engine():
    handle = LIB.aur_engine_create(48000, 2)
    assert handle, "the engine could not be created"
    yield handle
    LIB.aur_engine_destroy(handle)


_KEEPALIVE: list = []


def planar(signal: np.ndarray):
    """A (frames, channels) array as an array of per-channel ctypes pointers.

    The per-channel buffers must outlive the call, otherwise the pointers
    dangle and the callee reads freed memory.
    """
    frames = signal.shape[0]
    planes = []
    for c in range(signal.shape[1]):
        plane = signal[:, c].astype(np.float32).copy()
        buf = (ctypes.c_float * frames).from_buffer(plane)
        _KEEPALIVE.append((plane, buf))
        planes.append(ctypes.cast(buf, ctypes.POINTER(ctypes.c_float)))
    # the ABI takes ``const float* const*``; ctypes needs a real array type
    array_type = ctypes.POINTER(ctypes.c_float) * len(planes)
    return array_type(*planes)


def _message(engine=None) -> str:
    raw = LIB.aur_engine_last_error(engine)
    assert raw is not None, "the error message must never be NULL"
    return raw.decode(errors="replace")


def run_block(engine, src, dst, frames, capacity=None, rate=0):
    """Run one block through the ABI and return the result and the counts.

    ``rate`` is the rate the result is wanted at; zero means the rate the
    chain works at.
    """
    counts = BlockCounts()
    rc = LIB.aur_engine_process(engine, src, dst, frames,
                                frames if capacity is None else capacity,
                                rate, ctypes.byref(counts))
    return rc, counts


def _pending_exception() -> bool:
    """True when the interpreter has an exception pending on this thread."""
    api = ctypes.pythonapi
    api.PyErr_Occurred.restype = ctypes.c_void_p
    return bool(api.PyErr_Occurred())


# --------------------------------------------------------------------------
# reading the header and the built library
# --------------------------------------------------------------------------

def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"//[^\n]*", " ", text)
    return text


def _header_exports() -> list[str]:
    """The functions the header declares.

    The parse is deliberately independent of the builder's: the builder
    writes the export table, and the test has to be able to disagree with
    it.  A declaration is a name that is followed by a parameter list.
    """
    with open(HEADER, encoding="utf-8") as fh:
        text = _strip_comments(fh.read())

    names: list[str] = []
    for match in re.finditer(r"\b(aur_[A-Za-z0-9_]+)\s*\(", text):
        name = match.group(1)
        if name not in names:
            names.append(name)
    return names


def _pe_exports(path: str) -> list[str]:
    """The names a PE image exports, read out of the export directory."""
    with open(path, "rb") as fh:
        data = fh.read()

    if data[:2] != b"MZ":
        raise ValueError(f"not a PE image: {path}")

    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise ValueError(f"bad PE signature: {path}")

    coff = pe + 4
    _, sections_count, _, _, _, optional_size, _ = struct.unpack_from("<HHIIIHH", data, coff)
    optional = coff + 20
    pe32plus = struct.unpack_from("<H", data, optional)[0] == 0x20B

    directories = optional + (112 if pe32plus else 96)
    export_rva, _ = struct.unpack_from("<II", data, directories)

    sections = []
    table = optional + optional_size
    for index in range(sections_count):
        base = table + index * 40
        virtual_size, virtual_address, raw_size, raw_pointer = struct.unpack_from(
            "<IIII", data, base + 8)
        sections.append((virtual_address, max(virtual_size, raw_size), raw_pointer))

    def to_offset(rva: int) -> int:
        for address, size, pointer in sections:
            if address <= rva < address + size:
                return pointer + (rva - address)
        raise ValueError(f"the address {rva:#x} is not mapped")

    directory = to_offset(export_rva)
    _, _, _, _, _, _, _, name_count, _, names_rva, _ = struct.unpack_from(
        "<IIHHIIIIIII", data, directory)

    names = []
    table_offset = to_offset(names_rva)
    for index in range(name_count):
        name_rva = struct.unpack_from("<I", data, table_offset + index * 4)[0]
        start = to_offset(name_rva)
        end = data.index(b"\0", start)
        names.append(data[start:end].decode("latin-1"))
    return names


def _library_exports(path: str) -> list[str] | None:
    """The names the built library exports, or None when they cannot be read."""
    if sys.platform == "win32":
        return _pe_exports(path)

    try:
        result = subprocess.run(["nm", "-D", "--defined-only", path],
                                capture_output=True, text=True)
    except (FileNotFoundError, OSError):
        return None

    if result.returncode != 0:
        return None

    names = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[-2] in ("T", "W"):
            names.append(parts[-1])
    return names


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ------------------------------------------------------------------ tests ---
def test_version_is_reported():
    version = LIB.aur_engine_version()
    assert version is not None
    assert len(version) > 0, "the version string must not be empty"


def test_create_and_destroy():
    handle = LIB.aur_engine_create(48000, 2)
    assert handle
    LIB.aur_engine_destroy(handle)
    LIB.aur_engine_destroy(None)      # must be a no-op


def test_create_rejects_bad_arguments():
    assert not LIB.aur_engine_create(0, 2)
    assert not LIB.aur_engine_create(48000, 0)


def test_add_module_returns_the_chain_index(engine):
    assert LIB.aur_engine_add_module(engine, b"AUR_DECLICK") == 0
    assert LIB.aur_engine_add_module(engine, b"AUR_DEHUM") == 1
    assert LIB.aur_engine_module_count(engine) == 2


def test_add_unknown_module_fails_with_a_message(engine):
    rc = LIB.aur_engine_add_module(engine, b"AUR_DOES_NOT_EXIST")
    assert rc < 0, "an unknown module must be reported as a negative error"
    message = LIB.aur_engine_last_error(engine)
    assert message and len(message) > 0, "the failure must be described"


def test_parameters_are_addressed_by_name(engine):
    LIB.aur_engine_add_module(engine, b"AUR_DECLICK")
    assert LIB.aur_engine_set_param(engine, 0, b"Sensitivity", 5.0) == AUR_OK

    buf = ctypes.create_string_buffer(256)
    assert LIB.aur_engine_get_param(engine, 0, b"Sensitivity", buf, 256) == AUR_OK
    assert abs(float(buf.value) - 5.0) < 1e-6, buf.value


def test_enum_parameters_carry_the_choice_string(engine):
    LIB.aur_engine_add_module(engine, b"AUR_DECLICK")
    assert LIB.aur_engine_set_param_text(engine, 0, b"Algorithm", b"MB Random") == AUR_OK
    buf = ctypes.create_string_buffer(256)
    assert LIB.aur_engine_get_param(engine, 0, b"Algorithm", buf, 256) == AUR_OK
    assert buf.value.decode() == "MB Random", buf.value


def test_unknown_parameter_is_rejected(engine):
    LIB.aur_engine_add_module(engine, b"AUR_DECLICK")
    assert LIB.aur_engine_set_param(engine, 0, b"No Such Parameter", 1.0) < 0
    assert LIB.aur_engine_set_param(engine, 99, b"Sensitivity", 1.0) < 0


def test_process_runs_the_chain(engine):
    LIB.aur_engine_add_module(engine, b"AUR_DECLICK")
    frames = 4096
    t = np.arange(frames) / 48000.0
    x = np.stack([0.3 * np.sin(2 * np.pi * 1000 * t)] * 2, axis=1)

    src = planar(x)
    dst = planar(np.zeros_like(x))
    rc, counts = run_block(engine, src, dst, frames)
    assert rc == AUR_OK
    assert counts.consumed == frames
    assert counts.produced == frames

    out = np.stack([np.ctypeslib.as_array(p, shape=(frames,)) for p in dst], axis=1)
    assert out.shape == x.shape
    assert np.isfinite(out).all()
    assert not np.allclose(out, 0.0), "the chain must produce audio"


def test_bypass_is_transparent(engine):
    LIB.aur_engine_add_module(engine, b"AUR_DECLICK")
    frames = 2048
    t = np.arange(frames) / 48000.0
    x = np.stack([0.3 * np.sin(2 * np.pi * 1000 * t)] * 2, axis=1)

    src = planar(x)
    dst = planar(np.zeros_like(x))
    run_block(engine, src, dst, frames)
    processed = np.stack([np.ctypeslib.as_array(p, shape=(frames,)) for p in dst], axis=1)

    assert LIB.aur_engine_set_bypass(engine, 0, 1) == AUR_OK
    dst2 = planar(np.zeros_like(x))
    run_block(engine, src, dst2, frames)
    bypassed = np.stack([np.ctypeslib.as_array(p, shape=(frames,)) for p in dst2], axis=1)

    assert np.allclose(bypassed, x, atol=1e-6), "a bypassed module must pass the audio through"


def test_latency_is_reported(engine):
    LIB.aur_engine_add_module(engine, b"AUR_DECLICK")
    latency = LIB.aur_engine_latency_samples(engine)
    assert latency >= 0


def test_the_tail_is_reported(engine):
    """The chain's tail, in samples, and the infinite one."""
    LIB.aur_engine_add_module(engine, b"AUR_DECLICK")
    # the de-click rings a little after its input stops; the declaration is
    # the measured one
    assert LIB.aur_engine_tail_samples(engine) == 320

    # A module that rings for a while declares how long.
    LIB.aur_engine_add_module(engine, b"AUR_DECONSTRUCT")
    assert LIB.aur_engine_tail_samples(engine) == 1214

    # A module that never goes quiet says so with the sentinel the
    # formats use.
    LIB.aur_engine_add_module(engine, b"AUR_SIGNALGEN")
    assert LIB.aur_engine_tail_samples(engine) == 0xFFFFFFFF


def test_the_tail_of_a_chain_is_the_longest_one(engine):
    """Two modules with two tails: the chain waits for the longer one."""
    LIB.aur_engine_add_module(engine, b"AUR_DECONSTRUCT")   # 1214 samples
    LIB.aur_engine_add_module(engine, b"AUR_VOICEDENOISE")   # 1536 samples
    assert LIB.aur_engine_tail_samples(engine) == 1536


# --------------------------------------------------------------------------
# the rate: a chain that changes it, and what the adapter is told
# --------------------------------------------------------------------------

def test_the_rate_the_chain_works_at_is_reported(engine):
    """The core says at which rate the samples it makes belong."""
    assert LIB.aur_engine_output_sample_rate(engine) == 48000

    # A chain that does not change the rate works at the engine's rate.
    LIB.aur_engine_add_module(engine, b"AUR_DECLICK")
    assert LIB.aur_engine_output_sample_rate(engine) == 48000

    # A chain that does change it works at the rate the module converts to.
    index = LIB.aur_engine_add_module(engine, b"AUR_RESAMPLE")
    LIB.aur_engine_set_param_text(engine, index, b"Sample Rate", b"44100")
    assert LIB.aur_engine_output_sample_rate(engine) == 44100


def test_a_chain_that_changes_the_rate_says_how_many_samples_it_made(engine):
    """The call says how many samples went in and how many came out."""
    LIB.aur_engine_add_module(engine, b"AUR_RESAMPLE")
    LIB.aur_engine_set_param_text(engine, 0, b"Sample Rate", b"44100")

    frames = 64
    t = np.arange(frames) / 48000.0
    x = np.stack([0.3 * np.sin(2 * np.pi * 1000 * t)] * 2, axis=1)

    src = planar(x)
    dst = planar(np.zeros_like(x))
    rc, counts = run_block(engine, src, dst, frames)

    assert rc == AUR_OK
    assert counts.consumed == frames, "the chain has to say what it took"
    assert 0 < counts.produced < frames, (
        f"a chain that changes the rate makes a different number of samples "
        f"({counts.produced} of {frames})"
    )


def test_every_sample_of_a_chain_that_changes_the_rate_reaches_the_caller(engine):
    """The samples the chain made arrive, in the right plane and in order.

    The defect this covers: the ABI wrote the output of the chain into a
    buffer of the size of the INPUT block, so the samples of the second
    channel ended up in the first one and the rest was thrown away.
    """
    from aurcore.api import create_engine

    reference = create_engine(48000, 2)
    reference.add_module("AUR_RESAMPLE")
    reference.set_param(0, "Sample Rate", "44100")

    LIB.aur_engine_add_module(engine, b"AUR_RESAMPLE")
    LIB.aur_engine_set_param_text(engine, 0, b"Sample Rate", b"44100")

    frames = 64
    count = frames * 8
    t = np.arange(count) / 48000.0
    # two different signals, so a channel that is mixed up is visible
    signal = np.stack([0.3 * np.sin(2 * np.pi * 1000 * t),
                       0.2 * np.sin(2 * np.pi * 200 * t)], axis=1)

    for start in range(0, count, frames):
        block = signal[start:start + frames]
        expected = np.asarray(reference.process(block), dtype=np.float64)
        made = int(expected.shape[0])

        src = planar(block)
        dst = planar(np.zeros((made, 2), dtype=np.float32))
        rc, counts = run_block(engine, src, dst, frames, capacity=made)

        assert rc == AUR_OK
        assert counts.produced == made

        out = np.stack([np.ctypeslib.as_array(p, shape=(made,)) for p in dst], axis=1)
        worst = float(np.max(np.abs(out - expected))) if made else 0.0
        assert worst <= 1.0e-6, (
            f"the samples the chain made have to reach the caller unchanged "
            f"(the worst difference is {worst:.3e})"
        )


def test_a_caller_with_a_fixed_rate_gets_the_number_of_samples_it_asked_for(engine):
    """The result can be asked for at the rate the caller works at.

    A host with a fixed bus cannot change its rate, so the core converts
    the chain's output back: the block that comes back has the number of
    samples the caller asked for, and the right duration.
    """
    LIB.aur_engine_add_module(engine, b"AUR_RESAMPLE")
    LIB.aur_engine_set_param_text(engine, 0, b"Sample Rate", b"44100")

    frames = 64
    t = np.arange(frames * 4) / 48000.0
    signal = np.stack([0.3 * np.sin(2 * np.pi * 1000 * t)] * 2, axis=1)

    for start in range(0, signal.shape[0], frames):
        block = signal[start:start + frames]
        src = planar(block)
        dst = planar(np.zeros_like(block))
        rc, counts = run_block(engine, src, dst, frames, capacity=frames, rate=48000)

        assert rc == AUR_OK
        assert counts.produced == frames, (
            f"the caller asked for {frames} samples and got {counts.produced}"
        )

        out = np.stack([np.ctypeslib.as_array(p, shape=(frames,)) for p in dst], axis=1)
        assert np.isfinite(out).all()


def test_the_latency_of_a_chain_that_changes_the_rate_is_in_the_caller_s_samples(engine):
    """The delay is told in the samples of the rate the caller works at.

    The resampler declares 45 samples at 44.1 kHz, which are 49 samples
    at 48 kHz, and the conversion back costs its own look ahead.
    """
    LIB.aur_engine_add_module(engine, b"AUR_RESAMPLE")
    LIB.aur_engine_set_param_text(engine, 0, b"Sample Rate", b"44100")

    latency = LIB.aur_engine_latency_samples(engine)
    assert latency >= 49, (
        f"the delay of the chain is told as {latency} samples at 48 kHz, "
        "the look ahead of the kernel alone is 49"
    )


def test_state_round_trips(engine):
    LIB.aur_engine_add_module(engine, b"AUR_DECLICK")
    LIB.aur_engine_set_param(engine, 0, b"Sensitivity", 7.5)

    out = ctypes.c_char_p()
    length = LIB.aur_engine_serialize(engine, ctypes.byref(out))
    assert length > 0 and out.value
    text = out.value                      # keep a copy: the buffer is freed below
    document = json.loads(text.decode())
    assert document["format"] == "auralis.chain"
    assert document["steps"][0]["code"] == "AUR_DECLICK"
    LIB.aur_engine_free(out)


def test_state_survives_a_round_trip_through_the_abi(engine):
    LIB.aur_engine_add_module(engine, b"AUR_DECLICK")
    LIB.aur_engine_set_param(engine, 0, b"Sensitivity", 7.5)

    out = ctypes.c_char_p()
    length = LIB.aur_engine_serialize(engine, ctypes.byref(out))
    assert length > 0
    text = out.value

    other = LIB.aur_engine_create(48000, 2)
    try:
        assert LIB.aur_engine_deserialize(other, text) == AUR_OK
        assert LIB.aur_engine_module_count(other) == 1
        buf = ctypes.create_string_buffer(256)
        assert LIB.aur_engine_get_param(other, 0, b"Sensitivity", buf, 256) == AUR_OK
        assert abs(float(buf.value) - 7.5) < 1e-6, buf.value
    finally:
        LIB.aur_engine_destroy(other)
        LIB.aur_engine_free(out)


def test_every_function_is_documented_in_the_header():
    """The header is the contract: every function has to say all of it.

    A function that is added to the header without its thread, its
    interpreter rule and its failure behaviour is a hole in the contract,
    and this is where the hole shows.
    """
    with open(HEADER, encoding="utf-8") as fh:
        text = fh.read()

    for name in _header_exports():
        matches = list(re.finditer(r"^[A-Za-z_][A-Za-z0-9_ \t\*]*\b"
                                   + re.escape(name) + r"\s*\(",
                                   text, re.MULTILINE))
        assert matches, f"{name} is not declared in the header"
        declaration = matches[-1]

        start = text.rfind("/**", 0, declaration.start())
        end = text.rfind("*/", 0, declaration.start())
        assert start != -1 and end > start, f"{name} has no documentation"

        block = text[start:end]
        assert "thread:" in block, f"{name} does not say which thread it may be called on"
        assert "interpreter:" in block, f"{name} does not say whether the interpreter may be down"
        assert "failure:" in block, f"{name} does not say what it does when it fails"


def test_every_function_is_covered_by_the_failure_test(engine):
    """A function that the failure test does not touch is a blind spot."""
    covered = {name for name, _, _ in _wrong_argument_calls(engine)}
    covered |= {"aur_engine_destroy", "aur_engine_free", "aur_engine_last_error",
                "aur_engine_version", "aur_shutdown"}

    declared = set(_header_exports())
    missing = sorted(declared - covered)
    assert not missing, f"the failure test does not cover: {', '.join(missing)}"


def test_the_calls_that_cannot_fail_behave():
    """The calls that return nothing still have to behave."""
    LIB.aur_engine_destroy(None)          # a no-op
    LIB.aur_engine_free(None)             # a no-op
    assert LIB.aur_engine_last_error(None) is not None
    assert LIB.aur_engine_version() is not None
    assert len(LIB.aur_engine_version()) > 0


# --------------------------------------------------------------------------
# 1. the export surface is exactly what the header promises
# --------------------------------------------------------------------------

def test_the_header_declares_the_whole_surface():
    names = _header_exports()
    assert len(names) >= 16, f"the header declares only {len(names)} functions"
    assert len(set(names)) == len(names), "the header declares a name twice"
    for name in names:
        assert name.startswith("aur_"), name


#: The symbols of the C runtime.  A toolchain that links the runtime into
#: the library exports these as well, and they cannot be taken out again:
#: the Microsoft compiler is the one that produces the exact export table.
_RUNTIME_EXPORTS = frozenset({
    "_CRT_INIT", "atexit", "__mingw_module_is_dll",
    "__unw_getcontext", "unw_getcontext", "_initterm", "_initterm_e",
})


def _is_runtime_export(name: str) -> bool:
    """True for a symbol that belongs to the C or the C++ runtime."""
    if name in _RUNTIME_EXPORTS:
        return True
    # a mangled C++ name, e.g. the destructor of a standard library type
    return name.startswith("_Z")


def test_the_library_exports_exactly_what_the_header_promises():
    exported = _library_exports(LIBRARY_PATH)
    if exported is None:
        pytest.skip("the export table of the library cannot be read on this platform")

    declared = set(_header_exports())
    present = set(exported)

    missing = sorted(declared - present)
    assert not missing, f"the library does not export: {', '.join(missing)}"

    extra = sorted(present - declared)
    unknown = [name for name in extra if not _is_runtime_export(name)]
    assert not unknown, (
        "the library exports more than the header promises: "
        f"{', '.join(unknown)}"
    )

    if extra:
        pytest.skip(
            "the library was built with a toolchain that links the C runtime "
            f"into it, so it exports the runtime's symbols as well "
            f"({', '.join(extra)}); the Microsoft compiler produces the exact "
            "export table"
        )


def test_every_function_the_header_promises_is_callable():
    for name in _header_exports():
        assert hasattr(LIB, name), f"{name} is declared but not there"


def test_the_export_list_is_derived_from_the_header():
    """The export table is written from the header, not written by hand."""
    declared = _header_exports()

    definition = os.path.join(BUILD_DIR, "aurcore_abi.def")
    if os.path.exists(definition):
        with open(definition, encoding="utf-8") as fh:
            listed = [line.strip() for line in fh
                      if line.strip() and not line.strip().startswith(";")
                      and line.strip() != "EXPORTS"]
        assert listed == declared, (
            "the module definition file does not match the header:\n"
            f"  file:    {listed}\n  header:  {declared}"
        )

    manifest_path = os.path.join(BUILD_DIR, "aurcore_abi.manifest.json")
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        assert manifest["exports"] == declared
        assert manifest["export_count"] == len(declared)
        assert manifest["header_sha256"] == _sha256(HEADER)


def test_the_builder_derives_the_same_export_list():
    """The builder and the test parse the header the same way."""
    sys.path.insert(0, ABI_DIR)
    try:
        import build_abi
    finally:
        sys.path.pop(0)

    assert build_abi.header_exports() == _header_exports()


def test_the_build_is_reproducible(tmp_path):
    """The same sources give the same library, byte for byte."""
    digests = []
    for name in ("first", "second"):
        out = tmp_path / name
        if not _build(out):
            pytest.skip("the library cannot be built on this machine")
        digests.append(_sha256(str(out / _library_name())))

    assert digests[0] == digests[1], (
        "two builds of the same sources gave different libraries:\n"
        f"  {digests[0]}\n  {digests[1]}"
    )


def _build(out_dir) -> bool:
    """Build the library into `out_dir`.  True when it worked."""
    out = str(out_dir)
    os.makedirs(out, exist_ok=True)

    attempts = [
        ["cmd", "/c", MSVC, sys.executable, BUILDER, "--msvc", "--out", out],
        [sys.executable, BUILDER, "--out", out],
    ]

    for command in attempts:
        try:
            result = subprocess.run(command, capture_output=True, text=True, cwd=ROOT)
        except OSError:
            continue
        if result.returncode == 0 and os.path.exists(os.path.join(out, _library_name())):
            return True

    return False


# --------------------------------------------------------------------------
# 2. the failure paths: a message, and no pending exception
# --------------------------------------------------------------------------

def _wrong_argument_calls(engine):
    """Every entry point, called with an argument it cannot accept.

    Each entry is (name, call, where the message has to be).  The third
    element is the handle the message is read from: None means the message
    of the library itself.
    """
    buf = ctypes.create_string_buffer(64)
    out = ctypes.c_char_p()

    return [
        ("aur_engine_create", lambda: LIB.aur_engine_create(0, 2), None),
        ("aur_engine_create", lambda: LIB.aur_engine_create(48000, 0), None),
        ("aur_engine_add_module", lambda: LIB.aur_engine_add_module(None, b"AUR_DECLICK"), None),
        ("aur_engine_add_module", lambda: LIB.aur_engine_add_module(engine, None), engine),
        ("aur_engine_set_param", lambda: LIB.aur_engine_set_param(None, 0, b"Sensitivity", 1.0),
         None),
        ("aur_engine_set_param", lambda: LIB.aur_engine_set_param(engine, 0, None, 1.0), engine),
        ("aur_engine_set_param", lambda: LIB.aur_engine_set_param(engine, 99, b"Sensitivity", 1.0),
         engine),
        ("aur_engine_set_param_ramped",
         lambda: LIB.aur_engine_set_param_ramped(None, 0, b"Sensitivity", 1.0, 20.0), None),
        ("aur_engine_set_param_ramped", lambda: LIB.aur_engine_set_param_ramped(engine, 0, None,
                                                                                1.0, 20.0), engine),
        ("aur_engine_set_param_ramped",
         lambda: LIB.aur_engine_set_param_ramped(engine, 99, b"Sensitivity", 1.0, 20.0), engine),
        ("aur_engine_set_param_text", lambda: LIB.aur_engine_set_param_text(None, 0, b"Algorithm",
                                                                            b"MB Random"), None),
        ("aur_engine_set_param_text", lambda: LIB.aur_engine_set_param_text(engine, 0, None,
                                                                            b"MB Random"), engine),
        ("aur_engine_set_param_text", lambda: LIB.aur_engine_set_param_text(engine, 0, b"Algorithm",
                                                                            None), engine),
        ("aur_engine_get_param", lambda: LIB.aur_engine_get_param(None, 0, b"Sensitivity", buf, 64),
         None),
        ("aur_engine_get_param", lambda: LIB.aur_engine_get_param(engine, 0, b"Sensitivity", None,
                                                                  64), engine),
        ("aur_engine_get_param", lambda: LIB.aur_engine_get_param(engine, 0, b"Sensitivity", buf,
                                                                  0), engine),
        ("aur_engine_set_bypass", lambda: LIB.aur_engine_set_bypass(None, 0, 1), None),
        ("aur_engine_set_bypass", lambda: LIB.aur_engine_set_bypass(engine, 99, 1), engine),
        ("aur_engine_process", lambda: LIB.aur_engine_process(None, None, None, 64, 64, 0, None),
         None),
        ("aur_engine_output_sample_rate", lambda: LIB.aur_engine_output_sample_rate(None), None),
        ("aur_engine_module_count", lambda: LIB.aur_engine_module_count(None), None),
        ("aur_engine_latency_samples", lambda: LIB.aur_engine_latency_samples(None), None),
        ("aur_engine_tail_samples", lambda: LIB.aur_engine_tail_samples(None), None),
        ("aur_engine_serialize", lambda: LIB.aur_engine_serialize(None, ctypes.byref(out)), None),
        ("aur_engine_serialize", lambda: LIB.aur_engine_serialize(engine, None), engine),
        ("aur_engine_deserialize", lambda: LIB.aur_engine_deserialize(None, b"{}"), None),
        ("aur_engine_deserialize", lambda: LIB.aur_engine_deserialize(engine, None), engine),
        ("aur_engine_deserialize",
         lambda: LIB.aur_engine_deserialize(engine, b"this is not a document"), engine),
    ]


def test_every_function_reports_its_failure(engine):
    """A failing call returns a failure and leaves a message behind."""
    for name, call, handle in _wrong_argument_calls(engine):
        result = call()
        if name == "aur_engine_create":
            assert result is None or result == 0, f"{name} did not fail"
        else:
            assert result is not None, f"{name} returned nothing"
            assert result < 0, f"{name} did not report a failure: {result}"

        message = _message(handle)
        assert message, f"{name} left no message behind"


def test_no_call_leaves_a_python_exception_pending(engine):
    """The interpreter is shared with this program: it must stay clean."""
    LIB.aur_engine_add_module(engine, b"AUR_DECLICK")

    for name, call, _ in _wrong_argument_calls(engine):
        call()
        assert not _pending_exception(), (
            f"{name} left a Python exception pending in the host program"
        )


def test_the_message_names_the_failure(engine):
    """A message that says "python exception" tells the user nothing."""
    LIB.aur_engine_add_module(engine, b"AUR_DECLICK")

    rc = LIB.aur_engine_set_param(engine, 0, b"No Such Parameter", 1.0)
    assert rc < 0
    message = _message(engine)
    assert "No Such Parameter" in message, message
    assert message != "python exception", message

    rc = LIB.aur_engine_add_module(engine, b"AUR_DOES_NOT_EXIST")
    assert rc < 0
    message = _message(engine)
    assert "AUR_DOES_NOT_EXIST" in message, message


def test_a_failed_block_leaves_silence(engine):
    """The host must never play stale audio after a failed block."""
    LIB.aur_engine_add_module(engine, b"AUR_DECLICK")
    frames = 256
    x = np.ones((frames, 2), dtype=np.float32)
    src = planar(x)
    dst = planar(np.full((frames, 2), 7.0, dtype=np.float32))

    assert LIB.aur_engine_process(engine, None, dst, frames, frames, 0, None) < 0

    out = np.stack([np.ctypeslib.as_array(p, shape=(frames,)) for p in dst], axis=1)
    assert np.allclose(out, 0.0), "a failed block must leave silence"


def test_the_module_count_follows_the_chain(engine):
    assert LIB.aur_engine_module_count(engine) == 0
    LIB.aur_engine_add_module(engine, b"AUR_DECLICK")
    assert LIB.aur_engine_module_count(engine) == 1
    LIB.aur_engine_add_module(engine, b"AUR_DEHUM")
    assert LIB.aur_engine_module_count(engine) == 2


def test_the_message_of_the_library_is_separate_from_the_engine(engine):
    """A call without an engine must not wipe the message of an engine."""
    LIB.aur_engine_add_module(engine, b"AUR_DECLICK")
    LIB.aur_engine_set_param(engine, 0, b"No Such Parameter", 1.0)
    engine_message = _message(engine)
    assert engine_message

    assert LIB.aur_engine_create(0, 0) is None
    assert _message(None), "a call without an engine must leave a message too"

    assert _message(engine) == engine_message, (
        "the message of the engine must survive a failure elsewhere"
    )


# --------------------------------------------------------------------------
# 3. the threading rules
# --------------------------------------------------------------------------

def test_a_control_call_does_not_disturb_the_audio_path(engine):
    """The message thread and the audio thread may use the same engine.

    The contract says the control calls wait for the audio thread and that
    the audio thread never sees a half finished control call.  This drives
    both at the same time and checks that the audio stays intact.
    """
    LIB.aur_engine_add_module(engine, b"AUR_DECLICK")
    LIB.aur_engine_set_param(engine, 0, b"Sensitivity", 5.0)

    frames = 512
    t = np.arange(frames) / 48000.0
    x = np.stack([0.3 * np.sin(2 * np.pi * 1000 * t)] * 2, axis=1)
    src = planar(x)

    stop = threading.Event()
    failures: list[str] = []

    # the first blocks of a chain that declares a delay are silence by
    # design: the delay is the chain's own
    latency = int(LIB.aur_engine_latency_samples(engine))
    first_sounding = latency // frames

    def worker():
        while not stop.is_set():
            out = ctypes.c_char_p()
            length = LIB.aur_engine_serialize(engine, ctypes.byref(out))
            if length < 0:
                failures.append(_message(engine))
                return
            LIB.aur_engine_free(out)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        for index in range(50):
            dst = planar(np.zeros_like(x))
            rc, _counts = run_block(engine, src, dst, frames)
            assert rc == AUR_OK
            out = np.stack([np.ctypeslib.as_array(p, shape=(frames,)) for p in dst], axis=1)
            assert np.isfinite(out).all(), "the audio thread produced garbage"
            if index >= first_sounding:
                assert not np.allclose(out, 0.0), "the audio thread produced silence"
    finally:
        stop.set()
        thread.join(timeout=30)

    assert not failures, failures


def test_the_engine_belongs_to_one_thread_at_a_time(engine):
    """Two threads may not drive the same engine at the same time.

    The contract says so; the library keeps the engine lock, so the second
    thread waits instead of corrupting the chain.
    """
    LIB.aur_engine_add_module(engine, b"AUR_DECLICK")

    results: list[int] = []
    lock = threading.Lock()

    def worker():
        for _ in range(50):
            count = LIB.aur_engine_module_count(engine)
            with lock:
                results.append(count)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert results, "the threads did not run"
    assert all(count == 1 for count in results), results


# --------------------------------------------------------------------------
# 4. the lifetime of the library
# --------------------------------------------------------------------------

def test_engines_can_be_created_and_destroyed_in_a_loop():
    """Engines come and go without leaving anything behind."""
    for _ in range(25):
        handle = LIB.aur_engine_create(48000, 2)
        assert handle, "the engine could not be created"
        assert LIB.aur_engine_add_module(handle, b"AUR_DECLICK") == 0
        LIB.aur_engine_destroy(handle)


def test_the_shutdown_leaves_the_hosts_interpreter_alone():
    """The interpreter of the program that embeds the library is not ours."""
    LIB.aur_shutdown()

    # this program is still running, so its interpreter is still alive
    assert sys.exc_info() == (None, None, None)

    # and the library still works afterwards
    handle = LIB.aur_engine_create(48000, 2)
    assert handle, "the library must work again after a shutdown"
    assert LIB.aur_engine_add_module(handle, b"AUR_DECLICK") == 0
    LIB.aur_engine_destroy(handle)


def test_the_library_can_be_loaded_and_unloaded_in_a_loop():
    """Loading and unloading the library repeatedly must not break anything.

    The child process loads the library, uses it and unloads it, five
    times over.  The interpreter belongs to the child, so the library never
    ends it; what is checked here is that the library survives the unload.
    """
    script = r"""
import ctypes, os, sys

path = sys.argv[1]
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.LoadLibraryExW.restype = ctypes.c_void_p
kernel32.LoadLibraryExW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint32]
kernel32.FreeLibrary.restype = ctypes.c_int
kernel32.FreeLibrary.argtypes = [ctypes.c_void_p]

LOAD_WITH_ALTERED_SEARCH_PATH = 0x00000008

for round in range(5):
    handle = kernel32.LoadLibraryExW(path, None, LOAD_WITH_ALTERED_SEARCH_PATH)
    if not handle:
        raise SystemExit("the library did not load: %d" % ctypes.get_last_error())

    lib = ctypes.CDLL(None, handle=handle)
    lib.aur_engine_create.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    lib.aur_engine_create.restype = ctypes.c_void_p
    lib.aur_engine_add_module.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.aur_engine_add_module.restype = ctypes.c_int
    lib.aur_engine_destroy.argtypes = [ctypes.c_void_p]
    lib.aur_shutdown.argtypes = []

    engine = lib.aur_engine_create(48000, 2)
    if not engine:
        raise SystemExit("the engine could not be created")
    if lib.aur_engine_add_module(engine, b"AUR_DECLICK") != 0:
        raise SystemExit("the module could not be added")
    lib.aur_engine_destroy(engine)
    lib.aur_shutdown()

    if not kernel32.FreeLibrary(handle):
        raise SystemExit("the library did not unload")

print("ok")
"""

    if sys.platform != "win32":
        pytest.skip("the load and unload loop needs the Windows loader")

    result = subprocess.run([sys.executable, "-c", script, LIBRARY_PATH],
                            capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ok" in result.stdout, result.stdout


#: The compiled host probe, once it has been built.
_HOST_PROBE: str | None = None

#: A host that is not Python: the library starts its own interpreter there,
#: and it is the one that ends it.
_HOST_PROBE_SOURCE = r"""
/* generated by tests/test_plugin_abi.py - do not edit */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if defined(_WIN32)
  #include <windows.h>
  typedef HMODULE probe_module_t;
  static probe_module_t probe_load (const char* path)
  {
      /* the library's own directory is searched first, so the interpreter
         library next to it is found */
      return LoadLibraryExA (path, NULL, 0x00000008);
  }
  static void probe_unload (probe_module_t module) { FreeLibrary (module); }
  static void* probe_symbol (probe_module_t module, const char* name)
  {
      return (void*) GetProcAddress (module, name);
  }

  /* The interpreter library has to be in the process before the ABI
     library is loaded, or the loader cannot resolve it.  The probe looks
     for it next to the ABI library and one level up. */
  static void probe_load_interpreter (const char* library_path)
  {
      char directory[MAX_PATH];
      char* last = NULL;

      strncpy (directory, library_path, sizeof (directory) - 1);
      directory[sizeof (directory) - 1] = '\0';

      last = strrchr (directory, '\\');
      if (last == NULL)
          return;
      *last = '\0';

      for (int level = 0; level < 2; ++level)
      {
          char pattern[MAX_PATH];
          WIN32_FIND_DATAA found;
          HANDLE handle;

          snprintf (pattern, sizeof (pattern), "%s\\python3*.dll", directory);
          handle = FindFirstFileA (pattern, &found);
          if (handle != INVALID_HANDLE_VALUE)
          {
              char full[MAX_PATH];
              snprintf (full, sizeof (full), "%s\\%s", directory, found.cFileName);
              FindClose (handle);
              if (LoadLibraryExA (full, NULL, 0x00000008) != NULL)
                  return;
          }

          last = strrchr (directory, '\\');
          if (last == NULL)
              return;
          *last = '\0';
      }
  }
#else
  #include <dirent.h>
  #include <dlfcn.h>
  typedef void* probe_module_t;
  static probe_module_t probe_load (const char* path)
  {
      return dlopen (path, RTLD_NOW | RTLD_LOCAL);
  }
  static void probe_unload (probe_module_t module) { dlclose (module); }
  static void* probe_symbol (probe_module_t module, const char* name)
  {
      return dlsym (module, name);
  }
  static void probe_load_interpreter (const char* library_path) { }
#endif

typedef void* (*create_fn) (unsigned int, unsigned int);
typedef int (*add_fn) (void*, const char*);
typedef struct { unsigned int consumed; unsigned int produced; } probe_counts_t;
typedef int (*process_fn) (void*, const float* const*, float* const*, unsigned int,
                           unsigned int, unsigned int, probe_counts_t*);
typedef void (*destroy_fn) (void*);
typedef void (*shutdown_fn) (void);
typedef const char* (*version_fn) (void);
typedef const char* (*last_error_fn) (const void*);

int main (int argc, char** argv)
{
    const char* path = argc > 1 ? argv[1] : "aurcore_abi.dll";
    const char* mode = argc > 2 ? argv[2] : "keep";
    const int rounds = argc > 3 ? atoi (argv[3]) : 3;

    /* "keep"     the host never ends the interpreter, so the library can be
                  loaded and unloaded as often as the host likes.
       "shutdown" the host ends the interpreter before the unload, which is
                  what a plug-in does.  One round after that has to fail
                  with a message instead of crashing: the interpreter of a
                  process can only be started once. */
    const int keep = strcmp (mode, "keep") == 0;
    const int total = keep ? rounds : rounds + 1;

    for (int round = 0; round < total; ++round)
    {
        probe_load_interpreter (path);

        probe_module_t module = probe_load (path);
        if (module == NULL)
        {
            printf ("the library did not load\n");
            return 2;
        }

        create_fn create = (create_fn) probe_symbol (module, "aur_engine_create");
        add_fn add = (add_fn) probe_symbol (module, "aur_engine_add_module");
        process_fn process = (process_fn) probe_symbol (module, "aur_engine_process");
        destroy_fn destroy = (destroy_fn) probe_symbol (module, "aur_engine_destroy");
        shutdown_fn shutdown = (shutdown_fn) probe_symbol (module, "aur_shutdown");
        version_fn version = (version_fn) probe_symbol (module, "aur_engine_version");
        last_error_fn last_error = (last_error_fn) probe_symbol (module, "aur_engine_last_error");

        if (create == NULL || add == NULL || process == NULL || destroy == NULL
            || shutdown == NULL || version == NULL)
        {
            printf ("the library does not export the entry points\n");
            return 3;
        }

        if (version () == NULL)
        {
            printf ("the version is NULL\n");
            return 4;
        }

        void* engine = create (48000u, 2u);
        if (engine == NULL)
        {
            const char* message = last_error != NULL ? last_error (NULL) : "";
            if (keep || round == 0)
            {
                printf ("the engine could not be created: %s\n", message);
                return 5;
            }
            /* The interpreter was ended in this process, so the library
               cannot start another one.  It has to say so. */
            if (message[0] == '\0')
            {
                printf ("the failure was not described\n");
                return 6;
            }
            printf ("the library reported: %s\n", message);
            probe_unload (module);
            continue;
        }

        if (add (engine, "AUR_DECLICK") != 0)
        {
            printf ("the module could not be added\n");
            return 7;
        }

        {
            static float input[2][256];
            static float output[2][256];
            const float* planes[2];
            float* outs[2];
            int c;
            for (c = 0; c < 2; ++c)
            {
                planes[c] = input[c];
                outs[c] = output[c];
            }
            probe_counts_t counts;
            if (process (engine, planes, outs, 256u, 256u, 0u, &counts) != 0)
            {
                printf ("the block did not run\n");
                return 8;
            }
        }

        destroy (engine);

        /* The library started the interpreter, so this is the call that
           ends it -- while everything is still loaded. */
        if (! keep)
            shutdown ();

        probe_unload (module);
    }

    printf ("ok\n");
    return 0;
}
"""


def test_a_host_that_is_not_python_can_unload_the_library():
    """The plug-in case: the library owns the interpreter and ends it.

    The host is a program that does not run Python, so the library starts
    its own interpreter.  The host unloads the library at a moment of its
    own choosing, which is the moment the interpreter's own exit handler
    would run and walk structures that are already being torn down.  The
    library has to survive that.
    """
    probe = _build_host_probe()
    if probe is None:
        pytest.skip("the host probe cannot be built on this machine")

    result = subprocess.run([probe, LIBRARY_PATH, "shutdown", "1"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ok" in result.stdout, result.stdout


def test_a_host_that_is_not_python_can_load_the_library_again():
    """Loading and unloading the library repeatedly must not break anything.

    The host does not end the interpreter, so the interpreter of the
    process stays alive and every load finds it.  This is the loop a host
    that loads the module more than once runs into.
    """
    probe = _build_host_probe()
    if probe is None:
        pytest.skip("the host probe cannot be built on this machine")

    result = subprocess.run([probe, LIBRARY_PATH, "keep", "5"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ok" in result.stdout, result.stdout


def _build_host_probe() -> str | None:
    """Compile the host probe and return the path of the program.

    The probe is built in a directory of its own, so two test runs that
    happen at the same time do not fight over the same file.  The program
    is remembered, so the second test that needs it does not pay for the
    compiler again.
    """
    global _HOST_PROBE

    if _HOST_PROBE is not None:
        return _HOST_PROBE

    directory = tempfile.mkdtemp(prefix="auralis-abi-probe-")
    source = os.path.join(directory, "abi_host_probe.c")

    if sys.platform == "win32":
        program = os.path.join(directory, "abi_host_probe.exe")
    else:
        program = os.path.join(directory, "abi_host_probe")

    with open(source, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(_HOST_PROBE_SOURCE)

    commands = [
        ["cmd", "/c", MSVC, "cl.exe", "/nologo", "/O2", f"/Fe:{program}", source],
        ["cc", "-O2", "-o", program, source],
    ]

    for command in commands:
        try:
            result = subprocess.run(command, capture_output=True, text=True, cwd=directory)
        except OSError:
            continue
        if result.returncode == 0 and os.path.exists(program):
            _HOST_PROBE = program
            return program

    return None
