"""
aurmcp.protocol — the JSON-RPC layer of the Auralis model context server.

The layer holds the two records the server publishes (a tool and a resource)
and the handful of JSON-RPC 2.0 constants the transport needs.  Nothing here
imports anything but the standard library: the model context protocol is
JSON-RPC 2.0 over a newline delimited stream, and that needs nothing else.
The product gains no dependency by gaining this server.

Two rules are set down here and kept everywhere else in the package:

    * stdout carries the protocol and nothing else.  A diagnostic belongs on
      stderr, because a stray print on stdout corrupts the stream and the
      client stops understanding the server.
    * a tool that fails raises ``ToolError`` and comes back as a *result*
      with ``isError`` set, not as a JSON-RPC error object.  The client
      reads the message of a result; an error object hides it.

The revision this server speaks is the handshake family of the protocol:
``initialize`` first, then the ``notifications/initialized`` notification.
The newest revision of that family is ``2025-11-25``.  The stateless
revision ``2026-07-28`` replaces the handshake with a version field on every
request and is not implemented here; a client that asks for it is answered
with the newest revision this server does implement.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

__all__ = [
    "CONTENT_TYPES",
    "INTERNAL_ERROR",
    "INVALID_PARAMS",
    "INVALID_REQUEST",
    "METHOD_NOT_FOUND",
    "PARSE_ERROR",
    "PROTOCOL_VERSION",
    "RESOURCE_NOT_FOUND",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "InternalError",
    "InvalidParams",
    "MethodNotFound",
    "Resource",
    "ResourceNotFound",
    "Tool",
    "ToolError",
    "content_blocks",
    "failure",
    "negotiate_version",
    "success",
    "text_content",
]


# ---------------------------------------------------------------------------
# the protocol revision
# ---------------------------------------------------------------------------
#: The revision the server speaks when the client asks for one it does not
#: know.  It is the newest revision of the handshake family.
PROTOCOL_VERSION = "2025-11-25"

#: The revisions the server knows.  A client that names one of them gets the
#: same revision back; a client that names anything else is answered with
#: ``PROTOCOL_VERSION`` and may disconnect when it cannot speak that one.
SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = (
    "2024-11-05",
    "2025-03-26",
    "2025-06-18",
    "2025-11-25",
)


# ---------------------------------------------------------------------------
# the error codes
# ---------------------------------------------------------------------------
#: The message could not be parsed.  The line is skipped and the session
#: goes on.
PARSE_ERROR = -32700

#: The message is not a JSON-RPC request.
INVALID_REQUEST = -32600

#: The server does not implement the method.
METHOD_NOT_FOUND = -32601

#: The parameters of the request are missing or wrong.
INVALID_PARAMS = -32602

#: The server failed while carrying the request out.
INTERNAL_ERROR = -32603

#: The code the protocol adds for a resource that does not exist.
RESOURCE_NOT_FOUND = -32002


# ---------------------------------------------------------------------------
# the failures the dispatch raises
# ---------------------------------------------------------------------------
class ToolError(Exception):
    """A tool failed in a way the client should be told about.

    The message of the exception is the message the client reads: the server
    turns it into a result with ``isError`` set, never into a JSON-RPC error
    object.
    """


class MethodNotFound(Exception):
    """The client asked for a method this server does not implement."""


class InvalidParams(Exception):
    """The parameters of a request are missing or of the wrong shape."""


class ResourceNotFound(Exception):
    """The client asked for a resource the server does not publish."""


class InternalError(Exception):
    """The server failed while carrying a request out."""


# ---------------------------------------------------------------------------
# the records the server publishes
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Tool:
    """One callable tool: a name, a description, a schema and a handler.

    ``input_schema`` is a JSON schema as a plain dictionary, in the shape
    the protocol publishes it: an object schema with a ``properties`` map.
    The handler takes the arguments of the call as a dictionary and returns
    the answer; the server wraps the answer into the content of a result.
    """

    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], Any]


@dataclass(frozen=True)
class Resource:
    """One readable resource: a uri, a name, a description and a reader.

    The reader takes no argument and returns the text of the resource.  The
    mime type says how the client should read it.
    """

    uri: str
    name: str
    description: str
    mime_type: str
    reader: Callable[[], str]


# ---------------------------------------------------------------------------
# the envelopes
# ---------------------------------------------------------------------------
def success(message_id: Any, result: dict) -> dict:
    """Return the response object of a request that succeeded."""
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def failure(message_id: Any, code: int, message: str, data: Any = None) -> dict:
    """Return the response object of a request that failed.

    ``message_id`` is the id of the request, or ``None`` when the request
    could not be read at all - a line that does not parse, for instance.
    """
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": message_id, "error": error}


def negotiate_version(requested: Any) -> str:
    """Return the revision to answer an ``initialize`` request with.

    A client that names a revision the server knows gets that revision back.
    A client that names anything else - or nothing at all - gets
    ``PROTOCOL_VERSION``, the newest revision the server implements.
    """
    if isinstance(requested, str) and requested in SUPPORTED_PROTOCOL_VERSIONS:
        return requested
    return PROTOCOL_VERSION


# ---------------------------------------------------------------------------
# the content of a tool result
# ---------------------------------------------------------------------------
#: The content types the protocol knows.  A tool that returns a mapping of
#: one of these types is taken at its word.
CONTENT_TYPES = frozenset({"text", "image", "audio", "resource", "resource_link"})


def text_content(text: str) -> dict:
    """Return one text content block."""
    return {"type": "text", "text": text}


def _is_content_block(mapping: dict) -> bool:
    """Is the mapping a content block the protocol knows?"""
    kind = mapping.get("type")
    if kind not in CONTENT_TYPES:
        return False
    if kind == "text":
        return isinstance(mapping.get("text"), str)
    return True


def content_blocks(value: Any) -> list[dict]:
    """Return the content blocks for whatever a tool handler returned.

    A string becomes one text block, a mapping becomes a text block that
    carries the JSON form of the mapping, a list of mappings that all name a
    known content type is passed through unchanged, and anything else is
    rendered with ``str``.  The rule keeps the shape of the protocol out of
    the tool: the handler returns data, the server wraps it.
    """
    if value is None:
        return [text_content("")]
    if isinstance(value, str):
        return [text_content(value)]
    if isinstance(value, dict):
        if _is_content_block(value):
            return [value]
        return [_json_content(value)]
    if isinstance(value, (list, tuple)):
        items = list(value)
        if items and all(isinstance(item, dict) and _is_content_block(item) for item in items):
            return items
        return [_json_content(items)]
    return [text_content(str(value))]


def _json_content(value: Any) -> dict:
    """Return a text block that carries the JSON form of ``value``."""
    text = json.dumps(value, indent=2, ensure_ascii=False, default=str)
    return text_content(text)
