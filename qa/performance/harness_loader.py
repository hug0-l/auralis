"""Load the performance harness by path, not by name.

Several modules of the tree are called ``harness``.  A bare ``import
harness`` takes whatever a previous import left in ``sys.modules``, so the
sweep can end up measuring with the wrong one.
"""
import importlib.util
import sys
from pathlib import Path


def load_harness(directory: Path):
    """Load ``<directory>/harness.py`` under a name of its own."""
    path = directory / "harness.py"
    spec = importlib.util.spec_from_file_location("aur_qa_performance_harness", path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
