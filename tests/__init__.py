"""Auralis test-suite package marker.

Keeping ``tests`` a package means pytest inserts the repository root on
``sys.path``, so ``import aurcore`` works from any working directory and the
test modules can share helpers through ``tests.conftest``.
"""
