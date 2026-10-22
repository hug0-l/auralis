"""The order of the parameters is part of the contract.

The parameter id is the position of the parameter inside its module, so the
canonical table has to list a module's rows in the order the module declares
them.  If the two orders differ, the plug-in publishes one parameter at an
id the host will drive, and the automation lands on the wrong control.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurcore import registry

TABLE = ROOT / "aurcore" / "schema" / "params.tsv"


def _table_rows() -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    with TABLE.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if not line.strip() or line.startswith("#"):
                continue
            fields = line.split("\t")
            rows.setdefault(fields[0], []).append(fields[5])
    return rows


def _codes() -> list[str]:
    return [info.code for info in registry.all()]


@pytest.mark.parametrize("code", _codes(), ids=lambda c: c)
def test_the_table_carries_every_parameter_of_the_module(code: str):
    """A parameter the module declares must be in the table."""
    described = [desc.name for desc in registry.get(code)().describe()]
    in_table = _table_rows().get(code, [])
    missing = [name for name in described if name not in in_table]
    assert not missing, f"{code} declares {missing} and the table does not carry them"


@pytest.mark.parametrize("code", _codes(), ids=lambda c: c)
def test_the_order_of_the_table_is_the_order_of_the_module(code: str):
    """The rows must be in the order the module declares them."""
    described = [desc.name for desc in registry.get(code)().describe()]
    in_table = _table_rows().get(code, [])
    assert in_table == described, (
        f"{code}: the table lists {in_table} and the module declares {described}"
    )


@pytest.mark.parametrize("code", _codes(), ids=lambda c: c)
def test_no_parameter_is_listed_twice(code: str):
    """A module must not list the same parameter twice."""
    in_table = _table_rows().get(code, [])
    assert len(in_table) == len(set(in_table)), f"{code} lists a parameter twice"


def test_every_registered_module_is_in_the_table():
    """The table and the registry must agree on the set of modules."""
    in_table = set(_table_rows())
    registered = set(_codes())
    assert registered - in_table == set(), "a registered module has no rows"
    assert in_table - registered == set(), "the table has a module the registry does not know"
