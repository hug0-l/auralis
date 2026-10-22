"""Load a built VST3 module and report what happens."""
import ctypes
import os
import sys
import traceback

path = sys.argv[1]
os.add_dll_directory(os.path.dirname(os.path.abspath(path)))

try:
    dll = ctypes.CDLL(os.path.abspath(path))
except OSError as exc:
    print(f"LOAD FAILED: {exc}")
    raise SystemExit(1)

print("loaded:", os.path.basename(path))
print("GetPluginFactory:", hasattr(dll, "GetPluginFactory"))

try:
    factory = dll.GetPluginFactory
    factory.restype = ctypes.c_void_p
    factory.argtypes = []
    pointer = factory()
    print("GetPluginFactory() ->", hex(pointer) if pointer else "NULL")
except Exception:
    print("GetPluginFactory() raised:")
    traceback.print_exc()
