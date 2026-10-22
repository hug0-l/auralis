"""Auralis — audio restoration workbench core.

The core is a pure DSP library: no user interface, no network, no plug-in
formats.  The plug-in adapters and the application both sit on top of it.
"""

from .api import Engine, ModuleHandle, create_engine
from .params import ParamDesc, ParamStore
from . import registry

__all__ = ["Engine", "ModuleHandle", "create_engine", "ParamDesc", "ParamStore", "registry"]
__version__ = "0.1.0-alpha"
