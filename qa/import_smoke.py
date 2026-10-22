"""Import every module of the package and report the ones that fail.

A concurrent edit that leaves a syntax error shows up here before it shows
up in a test, and the name of the file is the whole answer.
"""
import importlib
import pkgutil
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import aurcore  # noqa: E402
import aurserver  # noqa: E402

failures = []
count = 0

for package in (aurcore, aurserver):
    for module in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
        count += 1
        try:
            importlib.import_module(module.name)
        except Exception as exc:  # noqa: BLE001
            failures.append((module.name, f"{type(exc).__name__}: {exc}"))

print(f"{count} modules, {len(failures)} failed to import")
for name, message in failures:
    print(f"  {name}: {message}")
