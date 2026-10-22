"""What the machine is doing right now: memory, processes, and the load."""
import ctypes
import subprocess
import sys


def memory() -> tuple[float, float]:
    """The free and the total physical memory, in gigabytes."""
    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_uint32),
            ("memory_load", ctypes.c_uint32),
            ("total_physical", ctypes.c_uint64),
            ("available_physical", ctypes.c_uint64),
            ("total_page_file", ctypes.c_uint64),
            ("available_page_file", ctypes.c_uint64),
            ("total_virtual", ctypes.c_uint64),
            ("available_virtual", ctypes.c_uint64),
            ("available_extended_virtual", ctypes.c_uint64),
        ]

    status = MemoryStatus()
    status.length = ctypes.sizeof(MemoryStatus)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))

    gigabyte = 1024 ** 3
    return (status.available_physical / gigabyte, status.total_physical / gigabyte)


def processes() -> dict[str, int]:
    """How many of each program are running."""
    result = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True)
    counts: dict[str, int] = {}

    for line in result.stdout.splitlines():
        if not line.startswith('"'):
            continue
        name = line.split('","')[0].strip('"')
        counts[name] = counts.get(name, 0) + 1

    return counts


def main() -> int:
    free, total = memory()
    counts = processes()

    print(f"memory: {free:.1f} GB free of {total:.1f} GB "
          f"({100.0 * (total - free) / total:.0f} % used)")

    interesting = ("python.exe", "node.exe", "cl.exe", "link.exe", "ninja.exe",
                    "cmake.exe", "msbuild.exe", "cmd.exe")
    for name in sorted(interesting):
        if counts.get(name):
            print(f"  {name:<14} {counts[name]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
