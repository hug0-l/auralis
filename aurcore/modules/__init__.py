"""
aurcore.modules — every processing module.

Importing this package imports each module file, which registers the module
classes with :mod:`aurcore.registry`.
"""

import importlib
import pkgutil

for _finder, _name, _ispkg in pkgutil.iter_modules(__path__):
    if _name.startswith("_"):
        continue
    importlib.import_module(f"{__name__}.{_name}")
