"""Two engines in one process: create, process on one, destroy both."""
import ctypes
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("AURALIS_ROOT", str(ROOT))

lib_path = (
    sys.argv[1]
     if len(sys.argv) > 1 else str(ROOT / "plugins" / "abi" / "build_main" / "aurcore_abi.dll")
)

lib = ctypes.CDLL(lib_path)
print("loaded:", lib_path)

lib.aur_engine_create.restype = ctypes.c_void_p
lib.aur_engine_create.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
lib.aur_engine_add_module.restype = ctypes.c_int
lib.aur_engine_add_module.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
lib.aur_engine_process.restype = ctypes.c_int
lib.aur_engine_process.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.c_uint32]
lib.aur_engine_destroy.argtypes = [ctypes.c_void_p]

frames = 512
signal = (ctypes.c_float * (frames * 2))()
for i in range(0, frames * 2, 2):
    signal[i] = 0.5
    signal[i + 1] = 0.5

engines = []
for index in range(2):
    engine = lib.aur_engine_create(48000, 2)
    print(f"engine {index}:", hex(engine) if engine else "NULL")
    if not engine:
        raise SystemExit(1)
    added = lib.aur_engine_add_module(engine, b"AUR_DECLICK")
    print(f"  add_module -> {added}")
    in_ptr = (ctypes.POINTER(ctypes.c_float) * 2)(signal, signal)
    out_ptr = (ctypes.POINTER(ctypes.c_float) * 2)(signal, signal)
    print("  process ->", lib.aur_engine_process(engine, in_ptr, out_ptr, frames))
    engines.append(engine)

print("destroying the first one")
lib.aur_engine_destroy(engines[0])
print("destroyed the first one")
lib.aur_engine_destroy(engines[1])
print("destroyed the second one")
print("done")
