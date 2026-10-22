"""The plug-in has to work in a host that is not ours.

The gate in ``tests/test_vst3_host.py`` drives the plug-in with the host
that lives in ``qa/vst3host``.  That host is the product's own harness, so
it can only prove what the product already believes.  A plug-in that is
loaded by a third party host is the case that matters: the host knows
nothing about the product, it hands the plug-in no state at all, and the
plug-in still has to publish its parameters and process the audio.

This file is that host.  It is written here, in the test, with nothing of
the product's own host in it, and it runs in a process of its own with a
working directory that is **not** the project: a host that runs the
plug-in from an installation does not have the source tree around it, and
the plug-in must not need it.

What the host does, in the order a host does it:

  1. load the module and ask its factory for the classes,
  2. create the component and the controller and connect the two,
  3. activate the audio buses and hand the plug-in its arrangement,
  4. set the plug-in up and start it,
  5. hand the component an **empty state** and see what it does with it,
  6. read the parameters the controller publishes -- before any state has
     arrived,
  7. push a block of audio through the plug-in and compare it with the
     block that went in,
  8. ask the plug-in for its state, hand that state back, move a
     parameter and check that the audio follows.

The module the gate drives is De-Click: its default chain removes the
clicks of the material, so a pass-through and a working plug-in are told
apart by the audio alone.
"""
from __future__ import annotations

import ctypes
import json
import math
import os
import subprocess
import sys
import tempfile
from ctypes import (POINTER, Structure, c_char, c_double, c_float, c_int32, c_int64,
                    c_uint32, c_uint64, c_void_p)
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FAMILY = ROOT / "build" / "vst3native"
REGISTRY = ROOT / "aurcore" / "schema" / "modules.json"

#: The module the gate drives.  Its default chain removes the clicks of
#: the material, so the audio says whether the plug-in worked.
GATE_MODULE = "AUR_DECLICK"

#: The sample rate and the block size the host asks for.
RATE = 48000
BLOCK = 256

#: How many blocks the host pushes through the plug-in.
BLOCKS = 96

#: The interface ids of the format.  They are the numbers the headers of
#: the SDK declare; the host needs them to ask for an interface.
def _uid(word1: int, word2: int, word3: int, word4: int) -> bytes:
    """The sixteen bytes of an interface id, in the order the format keeps them.

    The ids are four 32 bit words, and the format stores them the way the
    platform does: the first word in little endian pairs, the second one
    with its halves swapped, the last two as they are.
    """
    return bytes((
        word1 & 0xFF, (word1 >> 8) & 0xFF, (word1 >> 16) & 0xFF, (word1 >> 24) & 0xFF,
        (word2 >> 16) & 0xFF, (word2 >> 24) & 0xFF, word2 & 0xFF, (word2 >> 8) & 0xFF,
        word3 >> 24 & 0xFF, word3 >> 16 & 0xFF, word3 >> 8 & 0xFF, word3 & 0xFF,
        word4 >> 24 & 0xFF, word4 >> 16 & 0xFF, word4 >> 8 & 0xFF, word4 & 0xFF,
    ))


UID_IAUDIO = _uid(0x42043F99, 0xB7DA453C, 0xA569E79D, 0x9AAEC33D)
UID_ICOMPONENT = _uid(0xE831FF31, 0xF2D54301, 0x928EBBEE, 0x25697802)
UID_ICONTROLLER = _uid(0xDCD7BBE3, 0x7742448D, 0xA874AACC, 0x979C759E)
UID_CONNECTION = _uid(0x70A4156F, 0x6E6E4026, 0x989148BF, 0xAA60D8D1)

#: The result codes the host reads.
RESULT_OK = 0
RESULT_FALSE = 1

#: The flags of a parameter that says what the parameter is.
FLAG_BYPASS = 1 << 16
FLAG_PROGRAM = 1 << 15

#: The audio media type, the two bus directions and the real time process
#: mode: the numbers the headers of the format declare.
MEDIA_AUDIO = 0
BUS_INPUT = 0
BUS_OUTPUT = 1
PROCESS_REALTIME = 0
SAMPLE_32 = 0

#: The stereo arrangement: the two channels, in the order the format uses.
SPEAKER_STEREO = 0x4


# ---------------------------------------------------------------------------
#  the structures the host fills in
# ---------------------------------------------------------------------------


class ClassInfo(Structure):
    """What the factory says about one of its classes."""

    _fields_ = [
        ("cid", ctypes.c_byte * 16),
        ("cardinality", c_int32),
        ("category", c_char * 32),
        ("name", c_char * 64),
    ]


class ProcessSetup(Structure):
    """The arrangement of the processing the host asks for."""

    _fields_ = [
        ("processMode", c_int32),
        ("symbolicSampleSize", c_int32),
        ("maxSamplesPerBlock", c_int32),
        ("sampleRate", c_double),
    ]


class BusBuffers(Structure):
    """The buffers of one audio bus."""

    _fields_ = [
        ("numChannels", c_int32),
        ("silenceFlags", c_uint64),
        ("channelBuffers32", POINTER(POINTER(c_float))),
    ]


class ProcessData(Structure):
    """One block of audio on its way through the plug-in."""

    _fields_ = [
        ("processMode", c_int32),
        ("symbolicSampleSize", c_int32),
        ("numSamples", c_int32),
        ("numInputs", c_int32),
        ("numOutputs", c_int32),
        ("inputs", POINTER(BusBuffers)),
        ("outputs", POINTER(BusBuffers)),
        ("inputParameterChanges", c_void_p),
        ("outputParameterChanges", c_void_p),
        ("inputEvents", c_void_p),
        ("outputEvents", c_void_p),
        ("processContext", c_void_p),
    ]


class ParameterInfo(Structure):
    """What the controller says about one of its parameters."""

    _fields_ = [
        ("id", c_uint32),
        ("title", ctypes.c_uint16 * 128),
        ("shortTitle", ctypes.c_uint16 * 128),
        ("units", ctypes.c_uint16 * 128),
        ("stepCount", c_int32),
        ("defaultNormalizedValue", c_double),
        ("unitId", c_int32),
        ("flags", c_int32),
    ]


# ---------------------------------------------------------------------------
#  the state stream the host hands the plug-in
# ---------------------------------------------------------------------------
#
# The plug-in reads its state from a stream and writes it to one.  The
# stream is a table of functions, so the host builds one and hands the
# plug-in the address of the object that holds it.

_STREAMS: dict[int, "_StreamState"] = {}


class _StreamState:
    """What a stream holds while the plug-in reads from it or writes to it."""

    def __init__(self, data: bytes = b"") -> None:
        self.data = data
        self.position = 0
        self.written = bytearray()
        # The object the plug-in is given has to outlive the call: the
        # table of functions it carries lives in it.
        self.holder = _StreamObject()
        self.holder.vtable = _STREAM_VTABLE


def _stream_query_interface(this, iid, obj):
    """The stream is not a sizeable one: the plug-in then reads in blocks."""
    if obj:
        ctypes.cast(obj, POINTER(c_void_p))[0] = None
    return RESULT_FALSE


def _stream_add_ref(this):
    return 1


def _stream_release(this):
    return 0


def _stream_read(this, buffer, num_bytes, num_read):
    state = _STREAMS.get(this)
    if state is None:
        if num_read:
            num_read[0] = 0
        return RESULT_FALSE

    chunk = state.data[state.position : state.position + num_bytes]
    if chunk:
        ctypes.memmove(buffer, chunk, len(chunk))
        state.position += len(chunk)

    if num_read:
        num_read[0] = len(chunk)

    return RESULT_OK


def _stream_write(this, buffer, num_bytes, num_written):
    state = _STREAMS.get(this)
    if state is None:
        if num_written:
            num_written[0] = 0
        return RESULT_FALSE

    state.written += ctypes.string_at(buffer, num_bytes)

    if num_written:
        num_written[0] = num_bytes

    return RESULT_OK


def _stream_seek(this, position, mode, result):
    state = _STREAMS.get(this)
    if state is None:
        return RESULT_FALSE

    state.position = max(0, min(len(state.data), position))

    if result:
        result[0] = state.position

    return RESULT_OK


def _stream_tell(this, position):
    state = _STREAMS.get(this)
    if state is None:
        return RESULT_FALSE

    if position:
        position[0] = state.position

    return RESULT_OK


_STREAM_VTABLE = (c_void_p * 7)()


def _build_stream_vtable() -> None:
    _STREAM_VTABLE[0] = ctypes.cast(
        ctypes.WINFUNCTYPE(c_int32, c_void_p, c_void_p, POINTER(c_void_p))(
            _stream_query_interface), c_void_p)
    _STREAM_VTABLE[1] = ctypes.cast(
        ctypes.WINFUNCTYPE(c_uint32, c_void_p)(_stream_add_ref), c_void_p)
    _STREAM_VTABLE[2] = ctypes.cast(
        ctypes.WINFUNCTYPE(c_uint32, c_void_p)(_stream_release), c_void_p)
    _STREAM_VTABLE[3] = ctypes.cast(
        ctypes.WINFUNCTYPE(c_int32, c_void_p, c_void_p, c_int32, POINTER(c_int32))(
            _stream_read), c_void_p)
    _STREAM_VTABLE[4] = ctypes.cast(
        ctypes.WINFUNCTYPE(c_int32, c_void_p, c_void_p, c_int32, POINTER(c_int32))(
            _stream_write), c_void_p)
    _STREAM_VTABLE[5] = ctypes.cast(
        ctypes.WINFUNCTYPE(c_int32, c_void_p, c_int64, c_int32, POINTER(c_int64))(
            _stream_seek), c_void_p)
    _STREAM_VTABLE[6] = ctypes.cast(
        ctypes.WINFUNCTYPE(c_int32, c_void_p, POINTER(c_int64))(_stream_tell), c_void_p)


_build_stream_vtable()


class _StreamObject(Structure):
    """The object the plug-in is given as a stream."""

    _fields_ = [("vtable", POINTER(c_void_p))]


# ---------------------------------------------------------------------------
#  the parameter changes a host delivers with a block
# ---------------------------------------------------------------------------
#
#  A host that automates a parameter hands the change over with the block
#  it belongs to.  The plug-in reads it from a table of functions, so the
#  host builds one.

_CHANGES: dict[int, "_ChangesState"] = {}


class _ChangesState:
    """The changes a host delivers with a block.

    The objects that carry the tables of functions have to outlive the
    call: the plug-in calls into them.
    """

    def __init__(self, points: dict[int, float]) -> None:
        self.points = points
        self.holder = _ChangesObject()
        self.holder.vtable = _CHANGES_VTABLE
        self.queues: list[_QueueObject] = []


class _QueueObject(Structure):
    """The object that holds the points of one parameter."""

    _fields_ = [("vtable", POINTER(c_void_p))]


def _queue_query_interface(this, iid, obj):
    if obj:
        ctypes.cast(obj, POINTER(c_void_p))[0] = None
    return RESULT_FALSE


def _queue_add_ref(this):
    return 1


def _queue_release(this):
    return 0


def _queue_parameter_id(this):
    state = _CHANGES.get(this)
    return state.points and next(iter(state.points)) or 0


def _queue_point_count(this):
    state = _CHANGES.get(this)
    return len(state.points) if state else 0


def _queue_get_point(this, index, offset, value):
    state = _CHANGES.get(this)
    if state is None or index < 0 or index >= len(state.points):
        return RESULT_FALSE

    if offset:
        offset[0] = 0
    if value:
        value[0] = list(state.points.values())[index]

    return RESULT_OK


def _queue_add_point(this, offset, value, normalized):
    return RESULT_FALSE


_QUEUE_VTABLE = (c_void_p * 8)()


def _build_queue_vtable() -> None:
    _QUEUE_VTABLE[0] = ctypes.cast(
        ctypes.WINFUNCTYPE(c_int32, c_void_p, c_void_p, POINTER(c_void_p))(
            _queue_query_interface), c_void_p)
    _QUEUE_VTABLE[1] = ctypes.cast(
        ctypes.WINFUNCTYPE(c_uint32, c_void_p)(_queue_add_ref), c_void_p)
    _QUEUE_VTABLE[2] = ctypes.cast(
        ctypes.WINFUNCTYPE(c_uint32, c_void_p)(_queue_release), c_void_p)
    _QUEUE_VTABLE[3] = ctypes.cast(
        ctypes.WINFUNCTYPE(c_uint32, c_void_p)(_queue_parameter_id), c_void_p)
    _QUEUE_VTABLE[4] = ctypes.cast(
        ctypes.WINFUNCTYPE(c_int32, c_void_p)(_queue_point_count), c_void_p)
    _QUEUE_VTABLE[5] = ctypes.cast(
        ctypes.WINFUNCTYPE(c_int32, c_void_p, c_int32, POINTER(c_int32), POINTER(c_double))(
            _queue_get_point), c_void_p)
    _QUEUE_VTABLE[6] = ctypes.cast(
        ctypes.WINFUNCTYPE(c_int32, c_void_p, c_int32, c_double, POINTER(c_int32))(
            _queue_add_point), c_void_p)


_build_queue_vtable()


def _changes_query_interface(this, iid, obj):
    if obj:
        ctypes.cast(obj, POINTER(c_void_p))[0] = None
    return RESULT_FALSE


def _changes_add_ref(this):
    return 1


def _changes_release(this):
    return 0


def _changes_count(this):
    state = _CHANGES.get(this)
    return len(state.points) if state else 0


def _changes_data(this, index):
    state = _CHANGES.get(this)
    if state is None or index < 0 or index >= len(state.points):
        return 0

    holder = _QueueObject()
    holder.vtable = _QUEUE_VTABLE
    state.queues.append(holder)

    address = ctypes.cast(ctypes.byref(holder), c_void_p).value
    _CHANGES[address] = state
    return address


def _changes_add_data(this, param_id, queue):
    return RESULT_FALSE


_CHANGES_VTABLE = (c_void_p * 7)()


def _build_changes_vtable() -> None:
    _CHANGES_VTABLE[0] = ctypes.cast(
        ctypes.WINFUNCTYPE(c_int32, c_void_p, c_void_p, POINTER(c_void_p))(
            _changes_query_interface), c_void_p)
    _CHANGES_VTABLE[1] = ctypes.cast(
        ctypes.WINFUNCTYPE(c_uint32, c_void_p)(_changes_add_ref), c_void_p)
    _CHANGES_VTABLE[2] = ctypes.cast(
        ctypes.WINFUNCTYPE(c_uint32, c_void_p)(_changes_release), c_void_p)
    _CHANGES_VTABLE[3] = ctypes.cast(
        ctypes.WINFUNCTYPE(c_int32, c_void_p)(_changes_count), c_void_p)
    _CHANGES_VTABLE[4] = ctypes.cast(
        ctypes.WINFUNCTYPE(c_void_p, c_void_p, c_int32)(_changes_data), c_void_p)
    _CHANGES_VTABLE[5] = ctypes.cast(
        ctypes.WINFUNCTYPE(c_int32, c_void_p, POINTER(c_uint32), POINTER(c_void_p))(
            _changes_add_data), c_void_p)


_build_changes_vtable()


class _ChangesObject(Structure):
    """The object the host hands the plug-in with a block."""

    _fields_ = [("vtable", POINTER(c_void_p))]


def _make_changes(points: dict[int, float]) -> int:
    """The parameter changes the host delivers with a block."""
    state = _ChangesState(points)
    address = ctypes.cast(ctypes.byref(state.holder), c_void_p).value
    _CHANGES[address] = state
    return address


def _make_stream(data: bytes = b"") -> tuple[int, _StreamState]:
    """A stream the plug-in can read from or write to."""
    state = _StreamState(data)
    address = ctypes.cast(ctypes.byref(state.holder), c_void_p).value
    _STREAMS[address] = state
    return address, state


# ---------------------------------------------------------------------------
#  the host
# ---------------------------------------------------------------------------


def _method(interface, index):
    """The function at a place in the table of an interface."""
    table = ctypes.cast(interface, POINTER(POINTER(c_void_p)))[0]
    return table[index]


def _call(interface, index, restype, argtypes, *args):
    """Call one method of an interface."""
    prototype = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)
    return prototype(_method(interface, index))(interface, *args)


def _fid(data: bytes):
    """The address of a 16 byte interface or class id.

    The ids are not strings: they are sixteen bytes, and the factory reads
    them as such.
    """
    buffer = ctypes.create_string_buffer(data, 16)
    return ctypes.cast(buffer, c_void_p)


class ForeignHost:
    """A host that knows the format and nothing about the product."""

    def __init__(self, binary: Path) -> None:
        self.library = ctypes.WinDLL(str(binary))
        self.library.GetPluginFactory.restype = c_void_p
        self.library.GetPluginFactory.argtypes = []

        self.factory = self.library.GetPluginFactory()
        if not self.factory:
            raise RuntimeError(f"{binary} has no factory")

        self.component = self._create(UID_ICOMPONENT)
        self.processor = self._query(self.component, UID_IAUDIO)
        if not self.processor:
            raise RuntimeError(f"{binary} is not an audio processor")

        controller_id = ctypes.create_string_buffer(16)
        if _call(self.component, 5, c_int32, [c_void_p], controller_id) != RESULT_OK:
            raise RuntimeError(f"{binary} names no controller")

        self.controller = self._create(UID_ICONTROLLER, controller_id.raw)

        # The two halves are connected: each one is the connection point of
        # the other.
        self.component_point = self._query(self.component, UID_CONNECTION)
        self.controller_point = self._query(self.controller, UID_CONNECTION)
        _call(self.component_point, 3, c_int32, [c_void_p], self.controller_point)
        _call(self.controller_point, 3, c_int32, [c_void_p], self.component_point)

        _call(self.component, 3, c_int32, [c_void_p], None)
        _call(self.controller, 3, c_int32, [c_void_p], None)

        # The buses have to be activated: a plug-in that is handed a bus it
        # never activated leaves the output alone.
        for direction in (BUS_INPUT, BUS_OUTPUT):
            _call(self.component, 10, c_int32, [c_int32, c_int32, c_int32, c_int32],
                  MEDIA_AUDIO, direction, 0, 1)

        arrangement = (ctypes.c_uint64 * 1)(SPEAKER_STEREO)
        _call(self.processor, 3, c_int32,
              [POINTER(ctypes.c_uint64), c_int32, POINTER(ctypes.c_uint64), c_int32],
              arrangement, 1, arrangement, 1)

        setup = ProcessSetup(PROCESS_REALTIME, SAMPLE_32, BLOCK, float(RATE))
        _call(self.processor, 7, c_int32, [POINTER(ProcessSetup)], ctypes.byref(setup))
        _call(self.component, 11, c_int32, [c_int32], 1)
        _call(self.processor, 8, c_int32, [c_int32], 1)

    # -- the factory ---------------------------------------------------------

    def _create(self, interface_id: bytes, class_id: bytes | None = None) -> int:
        """One instance of the plug-in, as an interface."""
        if class_id is None:
            info = ClassInfo()
            count = _call(self.factory, 4, c_int32, [])
            for index in range(count):
                if _call(self.factory, 5, c_int32, [c_int32, POINTER(ClassInfo)],
                         index, ctypes.byref(info)) != RESULT_OK:
                    continue
                instance = ctypes.c_void_p()
                result = _call(self.factory, 6, c_int32,
                               [c_void_p, c_void_p, POINTER(c_void_p)],
                               _fid(bytes(info.cid)), _fid(interface_id),
                               ctypes.byref(instance))
                if result == RESULT_OK and instance.value:
                    return instance.value
            raise RuntimeError("the factory has no class that creates the interface")

        instance = ctypes.c_void_p()
        result = _call(self.factory, 6, c_int32,
                       [c_void_p, c_void_p, POINTER(c_void_p)],
                       _fid(class_id), _fid(interface_id), ctypes.byref(instance))
        if result != RESULT_OK or not instance.value:
            raise RuntimeError("the controller could not be created")
        return instance.value

    def _query(self, interface: int, interface_id: bytes) -> int:
        """Another interface of the same object."""
        other = ctypes.c_void_p()
        result = _call(interface, 0, c_int32, [c_void_p, POINTER(c_void_p)],
                       _fid(interface_id), ctypes.byref(other))
        if result != RESULT_OK:
            return 0
        return other.value or 0

    # -- the parameters ------------------------------------------------------

    def parameter_count(self) -> int:
        return _call(self.controller, 8, c_int32, [])

    def parameter_info(self, index: int) -> ParameterInfo:
        info = ParameterInfo()
        _call(self.controller, 9, c_int32, [c_int32, POINTER(ParameterInfo)],
              index, ctypes.byref(info))
        return info

    def parameter_value(self, param_id: int) -> float:
        return _call(self.controller, 14, c_double, [c_uint32], param_id)

    def set_parameter(self, param_id: int, value: float) -> int:
        return _call(self.controller, 15, c_int32, [c_uint32, c_double], param_id, value)

    # -- the state -----------------------------------------------------------

    def set_state(self, data: bytes) -> int:
        """Hand the component a state and report what it did with it."""
        address, _ = _make_stream(data)
        return _call(self.component, 12, c_int32, [c_void_p], address)

    def get_state(self) -> bytes:
        """Ask the component for its state."""
        address, state = _make_stream()
        _call(self.component, 13, c_int32, [c_void_p], address)
        return bytes(state.written)

    # -- the audio -----------------------------------------------------------

    def process(self, signal: list[list[float]],
                changes: dict[int, float] | None = None) -> list[list[float]]:
        """Push a signal through the plug-in and hand back what came out.

        The changes are the ones a host automates with: they travel with the
        block they belong to.
        """
        frames = len(signal[0])
        channels = len(signal)

        buffers_in = [BusBuffers() for _ in range(1)]
        buffers_out = [BusBuffers() for _ in range(1)]

        input_storage = []
        output_storage = []

        for channel in range(channels):
            values = (c_float * frames)(*signal[channel])
            input_storage.append(values)

        for channel in range(channels):
            values = (c_float * frames)()
            output_storage.append(values)

        pointers_in = (POINTER(c_float) * channels)(*[ctypes.cast(v, POINTER(c_float))
                                                     for v in input_storage])
        pointers_out = (POINTER(c_float) * channels)(*[ctypes.cast(v, POINTER(c_float))
                                                      for v in output_storage])

        buffers_in[0].numChannels = channels
        buffers_in[0].silenceFlags = 0
        buffers_in[0].channelBuffers32 = pointers_in
        buffers_out[0].numChannels = channels
        buffers_out[0].silenceFlags = 0
        buffers_out[0].channelBuffers32 = pointers_out

        data = ProcessData()
        data.processMode = PROCESS_REALTIME
        data.symbolicSampleSize = SAMPLE_32
        data.numSamples = frames
        data.numInputs = 1
        data.numOutputs = 1
        data.inputs = (BusBuffers * 1)(*buffers_in)
        data.outputs = (BusBuffers * 1)(*buffers_out)

        if changes:
            data.inputParameterChanges = _make_changes(dict(changes))

        _call(self.processor, 9, c_int32, [POINTER(ProcessData)], ctypes.byref(data))

        return [[float(output_storage[channel][frame]) for frame in range(frames)]
                for channel in range(channels)]

    def close(self) -> None:
        for interface in (self.processor, self.controller, self.component):
            if interface:
                _call(interface, 2, c_uint32, [])


# ---------------------------------------------------------------------------
#  the signal the host feeds the plug-in
# ---------------------------------------------------------------------------


def _signal(blocks: int = BLOCKS, block: int = BLOCK, rate: int = RATE) -> list[list[float]]:
    """A tone with a click in it, the material De-Click is for."""
    left: list[float] = []
    right: list[float] = []

    total = blocks * block

    for index in range(total):
        time = index / rate
        value = 0.5 * math.sin(2.0 * math.pi * 1000.0 * time)
        left.append(value)
        right.append(value)

    # Four clicks, the way the corpus has them.
    for click in range(4):
        at = int(rate * (0.05 + 0.1 * click))
        for offset in range(32):
            if at + offset < total:
                left[at + offset] += 0.9 * (1.0 - offset / 32.0)
                right[at + offset] += 0.9 * (1.0 - offset / 32.0)

    return [left, right]


def _difference(a: list[list[float]], b: list[list[float]]) -> dict:
    """How much two signals differ."""
    changed = 0
    total = 0
    biggest = 0.0
    square = 0.0

    for channel in range(len(a)):
        for index in range(len(a[channel])):
            total += 1
            delta = abs(a[channel][index] - b[channel][index])
            if delta > 0.0:
                changed += 1
            biggest = max(biggest, delta)
            square += delta * delta

    return {
        "samples": total,
        "changed": changed,
        "max_difference": biggest,
        "rms_difference": (square / total) ** 0.5 if total else 0.0,
    }


def _peak_at(signal: list[list[float]], at: int, width: int = 64) -> float:
    """The biggest sample of a signal around a place."""
    values = [abs(signal[channel][index])
              for channel in range(len(signal))
              for index in range(at, min(at + width, len(signal[channel])))]
    return max(values) if values else 0.0


# ---------------------------------------------------------------------------
#  what the host reports
# ---------------------------------------------------------------------------


def run_host(binary: Path, module: str) -> dict:
    """Run the host in a process of its own and read its report.

    The process runs with a working directory that is not the project: a
    host that loads the plug-in from an installation has no source tree
    around it.
    """
    with tempfile.TemporaryDirectory() as work:
        report = Path(work) / "report.json"

        environment = dict(os.environ)
        # A host does not set these: the plug-in has to find its runtime
        # and its core by itself.
        environment.pop("PYTHONHOME", None)
        environment.pop("PYTHONPATH", None)
        environment.pop("AURALIS_ROOT", None)
        environment.pop("AURALIS_RUNTIME_DIR", None)
        environment.pop("AURALIS_VST3HOST_DEPS", None)

        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--host", str(binary),
             "--module", module, "--json", str(report)],
            capture_output=True, text=True, cwd=work, env=environment, timeout=600,
        )

        if not report.is_file():
            raise AssertionError(
                "the host wrote no report (exit %d):\n%s" % (result.returncode,
                                                             result.stderr[-2000:])
            )

        data = json.loads(report.read_text(encoding="utf-8"))

    data["_exit_code"] = result.returncode
    data["_stderr"] = result.stderr[-4000:]
    return data


def _chain_of(state: bytes) -> dict:
    """The chain the plug-in carries, read out of its state.

    The state of the plug-in is the marker of the convention, the chain
    and the parameters, each of them closed by a zero byte.
    """
    marker = b"auralis.state.v1"
    if not state.startswith(marker):
        return {}

    pieces = state[len(marker) :].split(b"\x00")
    if len(pieces) < 2:
        return {}

    try:
        return json.loads(pieces[1].decode("utf-8"))
    except ValueError:
        return {}


def _package_of_the_host() -> str:
    """The place the core package is at, when the host's own interpreter sees it."""
    try:
        import aurcore  # noqa: PLC0415 - the import is the measurement
    except Exception:
        return ""
    return getattr(aurcore, "__file__", "") or ""


def _host_main(argv: list[str]) -> int:
    """The host itself: it loads the plug-in and writes its report."""
    binary = ""
    module = ""
    out = ""

    index = 0
    while index < len(argv):
        if argv[index] == "--host":
            binary = argv[index + 1]
            index += 2
        elif argv[index] == "--module":
            module = argv[index + 1]
            index += 2
        elif argv[index] == "--json":
            out = argv[index + 1]
            index += 2
        else:
            index += 1

    if not binary:
        print("the host needs the module to load", file=sys.stderr)
        return 2

    # The package the core imports has to be found by the plug-in and not by
    # the host: the host of this file runs on an interpreter of its own, and
    # that interpreter must not be able to see the package before the
    # plug-in is loaded, or the gate measures the wrong thing.
    visible = _package_of_the_host()

    host = ForeignHost(Path(binary))

    report: dict = {"ok": True, "module": module}
    report["package_visible_to_the_host"] = visible

    # 1. an empty state, the way a host that knows nothing hands one over.
    report["empty_state_accepted"] = host.set_state(b"") == RESULT_OK

    # 2. the parameters, before any state has arrived.
    parameters = []
    for position in range(host.parameter_count()):
        info = host.parameter_info(position)
        parameters.append({
            "id": int(info.id),
            "title": bytes(info.title).decode("utf-16-le", "replace").rstrip("\x00"),
            "flags": int(info.flags),
            "step": int(info.stepCount),
            "default": float(info.defaultNormalizedValue),
        })

    report["parameters"] = parameters
    report["parameter_count"] = len(parameters)

    # 3. the audio, with the default chain and no state at all.
    signal = _signal()
    processed = host.process(signal)
    report["audio"] = _difference(signal, processed)

    # the clicks are what the module is for: they have to be smaller in
    # the output than they were in the input.
    click_peaks = []
    for click in range(4):
        at = int(RATE * (0.05 + 0.1 * click))
        click_peaks.append({
            "at": at,
            "input": _peak_at(signal, at),
            "output": _peak_at(processed, at),
        })

    report["clicks"] = click_peaks

    # 4. the state the plug-in hands out, and the state it takes back.
    state = host.get_state()
    report["state_bytes"] = len(state)
    report["state_accepted"] = host.set_state(state) == RESULT_OK
    report["chain"] = _chain_of(state)

    # the same signal again, with the state the plug-in holds now: this is
    # the reference the moved parameter is measured against.
    restored = host.process(signal)
    report["state_audio"] = _difference(processed, restored)

    # 5. a parameter that moves, and the audio that follows.
    moved = None
    for parameter in parameters:
        if parameter["flags"] & (FLAG_BYPASS | FLAG_PROGRAM):
            continue
        if parameter["step"] != 0:
            continue   # a list has no middle: the host would have to pick one
        moved = parameter
        break

    if moved is not None:
        before = host.parameter_value(moved["id"])
        after = 0.0 if before > 0.5 else 1.0
        host.set_parameter(moved["id"], after)
        report["moved_parameter"] = {
            "id": moved["id"],
            "title": moved["title"],
            "before": before,
            "after": host.parameter_value(moved["id"]),
        }

        moved_signal = host.process(signal)
        report["moved_audio"] = _difference(restored, moved_signal)

        # The same change, delivered the way a host automates a parameter:
        # with the block it belongs to.
        host.set_parameter(moved["id"], before)
        automated = host.process(signal, changes={moved["id"]: after})
        report["automated_audio"] = _difference(restored, automated)
        report["automated_against_controller"] = _difference(moved_signal, automated)

    host.close()

    if out:
        Path(out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    else:
        print(json.dumps(report, indent=2))

    return 0


# ---------------------------------------------------------------------------
#  the gate
# ---------------------------------------------------------------------------


def _registry() -> dict:
    try:
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if isinstance(data, dict):
        return {k: v for k, v in data.items() if not k.startswith("_")}
    return {}


def _binary(code: str) -> Path | None:
    """The built plug-in of a module, when it is there."""
    directory = FAMILY / code
    if not directory.is_dir():
        return None
    binaries = sorted(directory.rglob("*.vst3.dll"))
    return binaries[0] if binaries else None


@pytest.fixture(scope="module")
def report() -> dict:
    """What the foreign host measured on the module the gate drives."""
    if not hasattr(ctypes, "WinDLL"):
        pytest.skip("the host of this gate loads the module the way Windows does")

    binary = _binary(GATE_MODULE)
    if binary is None:
        pytest.skip(f"the plug-in of {GATE_MODULE} is not built: {FAMILY}")

    return run_host(binary, GATE_MODULE)


def test_the_plug_in_publishes_its_parameters_before_any_state(report: dict) -> None:
    """A host that sends no state still gets the module's parameters.

    The bypass switch is the one parameter the format reserves for the
    plug-in; every other one belongs to the module the plug-in hosts.
    """
    registry = _registry()
    module = registry.get(GATE_MODULE, {})
    expected = {int(p["id"]): p["name"] for p in module.get("params", [])}

    if not expected:
        pytest.skip("the registry does not know the module the gate drives")

    published = {p["id"]: p["title"] for p in report["parameters"]}

    missing = [name for pid, name in expected.items() if pid not in published]
    assert not missing, (
        f"the plug-in does not publish {len(missing)} of the {len(expected)} parameters "
        f"of {GATE_MODULE}: {missing[:5]}"
    )

    wrong = [
        (pid, name, published[pid])
        for pid, name in expected.items()
        if published.get(pid) != name
    ]
    assert not wrong, f"the plug-in publishes the wrong parameters: {wrong[:5]}"

    # The bypass switch is published as well: a host that wants to switch
    # the module off needs it.
    bypass = [p for p in report["parameters"] if p["flags"] & FLAG_BYPASS]
    assert len(bypass) == 1, "the plug-in publishes no bypass switch"

    print(
        f"\n{GATE_MODULE} publishes {len(report['parameters'])} parameter(s) "
        f"before any state arrives: {len(expected)} of the module, the bypass "
        f"switch and the program change"
    )


def test_the_plug_in_built_the_default_chain_of_its_module(report: dict) -> None:
    """The chain the plug-in made for itself is the module's own chain.

    The application builds the chain of a module the same way: one step,
    the module, and the parameters at their defaults.  The plug-in has to
    build that chain when no state tells it what to build.
    """
    chain = report.get("chain") or {}
    steps = chain.get("steps") or []

    assert len(steps) == 1, (
        f"the plug-in built a chain of {len(steps)} step(s); the default chain of a "
        "module is the module itself"
    )

    assert steps[0].get("code") == GATE_MODULE, (
        f"the chain of the plug-in holds {steps[0].get('code')} and not {GATE_MODULE}"
    )
    assert steps[0].get("enabled", True) is True, "the chain of the plug-in is bypassed"

    registry = _registry()
    module = registry.get(GATE_MODULE, {})
    defaults = {
        str(p["name"]): p["default"]
        for p in module.get("params", [])
    }

    values = {
        name: entry.get("Value")
        for name, entry in (steps[0].get("params") or {}).items()
        if isinstance(entry, dict)
    }

    wrong = {
        name: (defaults[name], values.get(name))
        for name in defaults
        if name in values and values[name] != defaults[name]
    }
    assert not wrong, (
        f"the chain of the plug-in does not carry the defaults of the module: {wrong}"
    )

    print(
        f"\n{GATE_MODULE} built the chain of the module by itself: one step, "
        f"{len(values)} parameter(s) at their defaults"
    )


def test_an_empty_state_is_refused_and_the_plug_in_keeps_working(report: dict) -> None:
    """A state that is not ours is refused; the plug-in does not change."""
    assert report["empty_state_accepted"] is False, (
        "the plug-in accepted an empty state: a host that knows nothing must not "
        "be able to put the plug-in into a state it cannot work in"
    )


def test_the_core_came_up(report: dict) -> None:
    """The core of the plug-in is there: it imported its package.

    This is the defect the study ran into: the core could not be imported
    outside the project, so there was no engine, so there was no chain,
    and the plug-in passed the audio through unchanged.
    """
    said = report.get("_stderr", "")

    assert "could not be imported" not in said, (
        "the core of the plug-in did not come up:\n" + said[-1500:]
    )
    assert "could not create the core engine" not in said, (
        "the plug-in made no engine:\n" + said[-1500:]
    )
    assert "the shared runtime was not found" not in said, (
        "the plug-in found no runtime:\n" + said[-1500:]
    )

    # The host runs on an interpreter of its own.  When that interpreter can
    # see the package, the core would find it there and the gate would
    # measure the host instead of the plug-in.
    assert report.get("package_visible_to_the_host", "") == "", (
        "the interpreter of the host can import the core package "
        f"({report.get('package_visible_to_the_host')}), so the gate would not "
        "measure whether the plug-in finds its core by itself"
    )


def test_the_plug_in_processes_the_audio_with_no_state(report: dict) -> None:
    """The audio is not the input: the module's default chain is running.

    This is the defect the study found: a plug-in that takes its chain out
    of the state and gets no state publishes the bypass switch and hands
    the audio back bit for bit.
    """
    audio = report["audio"]

    assert audio["changed"] > 0, (
        "the plug-in handed the audio back bit for bit: it did not process it"
    )
    assert audio["max_difference"] > 1.0e-3, (
        f"the audio barely changed: the biggest difference is "
        f"{audio['max_difference']}"
    )

    # De-Click is for clicks: the clicks have to be smaller in the output.
    for click in report["clicks"]:
        assert click["output"] < click["input"] * 0.9, (
            f"the click at {click['at']} came out at {click['output']} and went in "
            f"at {click['input']}: the module's default chain did not run"
        )

    print(
        f"\n{GATE_MODULE} processed the audio with no state: "
        f"{audio['changed']} of {audio['samples']} samples changed, the biggest "
        f"difference is {audio['max_difference']:.4f}"
    )


def test_the_state_path_replaces_the_chain_and_the_audio_follows(report: dict) -> None:
    """A host that sends a state may replace the chain, and the audio moves."""
    assert report["state_bytes"] > 0, "the plug-in has no state to hand out"
    assert report["state_accepted"] is True, (
        "the plug-in refused the state it had handed out itself"
    )

    moved = report.get("moved_parameter")
    assert moved is not None, "the plug-in publishes no parameter a host may move"

    assert moved["before"] != moved["after"], (
        "the parameter did not move: the host told the plug-in another value and "
        "the plug-in kept the old one"
    )

    audio = report["moved_audio"]
    assert audio["max_difference"] > 1.0e-3, (
        "the parameter moved but the audio did not follow: the chain of the plug-in "
        "does not use the values the host sends"
    )

    # The same change, delivered the way a host automates a parameter: with
    # the block it belongs to.  A DAW does it this way and not through the
    # controller.
    automated = report.get("automated_audio")
    assert automated is not None, "the host did not deliver an automated change"

    assert automated["max_difference"] > 1.0e-3, (
        "the change the host automated did not reach the audio: the plug-in reads "
        "the changes of a block but does not act on them"
    )

    against = report.get("automated_against_controller", {})
    print(
        f"\n{GATE_MODULE} took its own state back and the parameter "
        f"'{moved['title']}' moved from {moved['before']:.3f} to {moved['after']:.3f}; "
        f"the audio changed by {audio['max_difference']:.4f}, and the change the host "
        f"automated differs from the one it told the controller by "
        f"{against.get('max_difference', 0.0):.6f}"
    )


if __name__ == "__main__":
    raise SystemExit(_host_main(sys.argv[1:]))
