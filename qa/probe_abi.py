"""Probe the core C ABI: create an engine, add a module, run a block."""
import ctypes
import os
import sys
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)

lib_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "plugins", "abi", "build_main",
                                                              "aurcore_abi.dll")
module_code = sys.argv[2] if len(sys.argv) > 2 else "AUR_DECLICK"

lib = ctypes.CDLL(lib_path)
print("loaded:", lib_path)

lib.aur_engine_version.restype = ctypes.c_char_p
print("abi version:", lib.aur_engine_version().decode())

lib.aur_engine_create.restype = ctypes.c_void_p
lib.aur_engine_create.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
engine = lib.aur_engine_create(48000, 2)
print("engine:", hex(engine) if engine else "NULL")
if not engine:
    raise SystemExit(1)

lib.aur_engine_add_module.restype = ctypes.c_int
lib.aur_engine_add_module.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
index = lib.aur_engine_add_module(engine, module_code.encode())
print(f"add_module({module_code}) ->", index)

lib.aur_engine_last_error.restype = ctypes.c_char_p
lib.aur_engine_last_error.argtypes = [ctypes.c_void_p]
if index < 0:
    print("last error:", (lib.aur_engine_last_error(engine) or b"").decode())

lib.aur_engine_module_count.restype = ctypes.c_int
lib.aur_engine_module_count.argtypes = [ctypes.c_void_p]
print("module count:", lib.aur_engine_module_count(engine))

# one block of silence through the engine
frames = 128
in_buf = (ctypes.c_float * (frames * 2))()
out_buf = (ctypes.c_float * (frames * 2))()
in_ptr = (ctypes.POINTER(ctypes.c_float) * 2)(in_buf, in_buf)
out_ptr = (ctypes.POINTER(ctypes.c_float) * 2)(out_buf, out_buf)

lib.aur_engine_process.restype = ctypes.c_int
lib.aur_engine_process.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.c_uint32]
result = lib.aur_engine_process(engine, in_ptr, out_ptr, frames)
print("process ->", result, "| last error:", (lib.aur_engine_last_error(engine) or b"").decode())
print("output peak:", max(abs(v) for v in out_buf))

lib.aur_engine_destroy.argtypes = [ctypes.c_void_p]
lib.aur_engine_destroy(engine)
print("destroyed")
