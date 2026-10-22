"""Every constants file of the benchmark must be importable and consistent.

A file that cannot be imported is a dead file: nothing exercises it, so
nothing in it is ever checked.  This gate makes that impossible.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / "qa" / "benchmark"

#: the files that hold the recorded numbers, and what each of them must carry
FILES = {
    "reference.py": ("Reference", "REFERENCES"),
    "reference_restoration.py": ("Restoration", "RESTORATIONS"),
    "reference_voice.py": ("Voice", "VOICES"),
    "reference_surface.py": (None, None),
    "reference_model.py": ("ModelReference", "MODEL_REFERENCES"),
    "reference_masking.py": (None, None),
    "reference_bridge.py": ("Bridge", "BRIDGES"),
    "edge_cases.py": ("Edge", "EDGES"),
}


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(f"aur_qa_benchmark_test_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _files():
    return sorted(p for p in BENCHMARK.glob("*.py") if not p.name.startswith("__"))


@pytest.mark.parametrize("path", _files(), ids=lambda p: p.name)
def test_the_file_parses(path: Path):
    """A constants file must be valid Python: it is imported by a gate."""
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


@pytest.mark.parametrize("path", _files(), ids=lambda p: p.name)
def test_the_file_imports(path: Path):
    """A constants file must import: a file that cannot is a dead file."""
    module = _load(path.stem, path)
    assert module is not None


@pytest.mark.parametrize("name", sorted(FILES), ids=lambda n: n)
def test_the_known_files_are_present(name: str):
    """The files the benchmark is built on must exist."""
    assert (BENCHMARK / name).is_file(), f"{name} is missing"


@pytest.mark.parametrize("name", sorted(FILES), ids=lambda n: n)
def test_the_records_carry_a_method(name: str):
    """Every recorded number says how it was measured."""
    record_name, collection = FILES[name]
    path = BENCHMARK / name
    if not path.is_file() or record_name is None or collection is None:
        return
    module = _load(path.stem, path)
    records = getattr(module, collection, None)
    assert records, f"{name} has no {collection}"
    for record in records:
        method = getattr(record, "method", None)
        assert method, f"{getattr(record, 'name', '?')} has no method"
        assert len(method) > 10, f"{getattr(record, 'name', '?')} has a thin method"
