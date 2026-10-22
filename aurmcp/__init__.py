"""
aurmcp — the model context server of Auralis.

The server lets an agent drive the product: list the modules, build a chain,
set the parameters, render the audio, measure the result against the rules of
the product and read its documentation.  It speaks the model context
protocol over the standard input and the standard output, and it publishes
the product as a set of tools and a set of resources.

Run it::

    python -m aurmcp

The server reads one JSON-RPC 2.0 message per line from the standard input
and writes one answer per line to the standard output.  Standard output
carries the protocol and nothing else; every diagnostic goes to the standard
error, because a stray print on the output corrupts the stream and the
client stops understanding the server.

The constraint this package honours
------------------------------------
**The product gains no dependency.**  The protocol is JSON-RPC 2.0 over a
newline delimited stream, and that is built from the standard library alone:
no package is installed, nothing is vendored, and the requirements of the
project do not change.  A dependency free server is worth stating in the
documentation of the product.

What the package holds
----------------------
``protocol``
    The JSON-RPC layer: the tool and the resource records, the error codes
    and the envelopes.
``server``
    The transport and the dispatch: the eight methods of the protocol, the
    reading of the stream and the writing of the answers.
``tools`` and ``resources``
    What the server publishes, written beside this file.

The tools and the resources are optional.  The server starts and answers
``initialize``, ``ping`` and ``tools/list`` with an empty list when they are
not written yet, so the protocol can be exercised before the product is
wired to it.

The public API
--------------
``Server``
    The server: ``handle`` for one message, ``serve`` for a stream.
``Tool`` and ``Resource``
    The two records a publisher writes.
``ToolError``
    The failure a tool raises to be told to the client.
``build_server``
    The server with every tool and every resource of the package.
``load_tools`` and ``load_resources``
    The tools and the resources of the package, or an empty list.
"""
from __future__ import annotations

import importlib
from typing import Any

from .protocol import (
    PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    Resource,
    Tool,
    ToolError,
)
from .server import DEFAULT_VERSION, Server, log

__all__ = [
    "DEFAULT_VERSION",
    "PROTOCOL_VERSION",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "Resource",
    "Server",
    "Tool",
    "ToolError",
    "build_server",
    "load_resources",
    "load_tools",
]

#: The version of the package.
__version__ = DEFAULT_VERSION


def load_tools() -> list[Tool]:
    """Return the tools the package publishes.

    The tools live in ``aurmcp.tools``.  The module is optional: the server
    starts with an empty tool list when it is not written yet.  The module
    may publish the tools as a module level ``TOOLS`` sequence, as an
    ``all_tools()`` function, or as any module level sequence of tools; all
    three are accepted.
    """
    return _collect("tools", Tool, ("TOOLS",), ("all_tools", "build_tools", "get_tools"))


def load_resources() -> list[Resource]:
    """Return the resources the package publishes.

    The resources live in ``aurmcp.resources``.  The module is optional: the
    server starts with an empty resource list when it is not written yet.
    The module may publish the resources as a module level ``RESOURCES``
    sequence, as an ``all_resources()`` function, or as any module level
    sequence of resources; all three are accepted.
    """
    return _collect(
        "resources", Resource, ("RESOURCES",), ("all_resources", "build_resources", "get_resources")
    )


def build_server() -> Server:
    """Return a server carrying every tool and every resource of the package."""
    return Server(load_tools(), load_resources(), name="auralis", version=DEFAULT_VERSION)


def _collect(
    module_name: str,
    kind: type,
    constants: tuple[str, ...],
    factories: tuple[str, ...],
) -> list[Any]:
    """Return the records a module of the package publishes.

    The module is looked up by name.  A module that is not written yet is
    not an error: the server runs with an empty list.  A module that is
    written but broken is reported on the standard error, and the server
    runs with an empty list as well, because a half wired server that
    answers the protocol is worth more than a server that does not start.
    """
    try:
        module = importlib.import_module(f".{module_name}", __package__)
    except ModuleNotFoundError as exc:
        if exc.name in (module_name, f"{__package__}.{module_name}"):
            return []
        log(f"the {module_name} module could not be imported: {exc}")
        return []
    except ImportError as exc:
        log(f"the {module_name} module could not be imported: {exc}")
        return []

    published = _first_of(module, constants)
    if published is None:
        published = _first_callable(module, factories)
    if published is None:
        published = _first_sequence(module, kind)
    if published is None:
        log(f"the {module_name} module publishes no records")
        return []

    records: list[Any] = []
    for item in published:
        if isinstance(item, kind):
            records.append(item)
        else:
            log(f"the {module_name} module publishes an item that is not a {kind.__name__}")
    return records


def _first_of(module: Any, names: tuple[str, ...]) -> Any:
    """Return the first of the named attributes of the module that is set."""
    for name in names:
        value = getattr(module, name, None)
        if value is not None:
            return value
    return None


def _first_callable(module: Any, names: tuple[str, ...]) -> Any:
    """Return the records the first of the named factories of the module builds."""
    for name in names:
        factory = getattr(module, name, None)
        if callable(factory):
            return factory()
    return None


def _first_sequence(module: Any, kind: type) -> Any:
    """Return the first module level sequence that holds records of the kind.

    The last resort of the loader: a module that publishes its records under
    a name nobody expected is still found, as long as the records are in a
    sequence of their own.
    """
    for name in dir(module):
        if name.startswith("_"):
            continue
        value = getattr(module, name, None)
        if not isinstance(value, (list, tuple)) or not value:
            continue
        if all(isinstance(item, kind) for item in value):
            return value
    return None
