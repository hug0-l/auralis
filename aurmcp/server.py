"""
aurmcp.server — the transport and the dispatch of the Auralis model context server.

The server reads newline delimited JSON from a stream, hands every message to
the dispatcher and writes the answers back, one JSON object per line.  The
stream is the protocol: stdout carries the answers and nothing else, and
every diagnostic goes to stderr.  A line that does not parse is reported as
a parse error and the session goes on; a bad line must not kill the server.

The dispatcher answers eight methods:

    ``initialize``              the revision, the capabilities and the name
    ``notifications/initialized`` a notification, and so it is not answered
    ``ping``                    an empty result, the liveness probe
    ``tools/list``              the published tools and their schemas
    ``tools/call``              the result of one tool
    ``resources/list``          the published resources
    ``resources/read``          the text of one resource
    ``prompts/list``            an empty list; the server publishes no prompts

Any other method is a JSON-RPC error with the code -32601.  A message
without an ``id`` is a notification and is not answered.

The server carries no dependency beyond the standard library, and it runs
with an empty tool list and an empty resource list: the protocol answers
``initialize``, ``ping`` and ``tools/list`` before the tools are written.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Sequence

from .protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    RESOURCE_NOT_FOUND,
    InternalError,
    InvalidParams,
    MethodNotFound,
    Resource,
    ResourceNotFound,
    Tool,
    ToolError,
    content_blocks,
    failure,
    negotiate_version,
    success,
)

__all__ = ["DEFAULT_VERSION", "Server", "log"]


#: The version the server reports to the client.
DEFAULT_VERSION = "0.1.0"


def log(message: str) -> None:
    """Write a diagnostic to the standard error.

    Every diagnostic of this server goes here.  Standard output carries the
    protocol and nothing else, so a stray print on it corrupts the stream.
    """
    print(message, file=sys.stderr, flush=True)


class Server:
    """The model context server: a dispatcher over a set of tools and resources.

    The server is the whole protocol: it owns the tools it publishes, the
    resources it reads and the revision it speaks.  It is built once and
    then either driven message by message with ``handle`` or run over a
    stream with ``serve``.

    The server publishes no prompts.  ``prompts/list`` answers with an empty
    list, and the description of the server says so.
    """

    #: The description of the server, given to the reader of the code.
    description = (
        "Auralis, a spectral audio restoration workstation, driven by an "
        "agent over the model context protocol: the module list, the chain, "
        "the parameters, the render and the measurement of the product, and "
        "its documentation as resources.  The server publishes no prompts."
    )

    def __init__(
        self,
        tools: Sequence[Tool],
        resources: Sequence[Resource],
        *,
        name: str = "auralis",
        version: str = DEFAULT_VERSION,
    ):
        """Build the server over a set of tools and a set of resources.

        The tools are addressed by their name and the resources by their
        uri, so a duplicate name or a duplicate uri is a mistake the server
        refuses rather than one it hides.
        """
        self.name = name
        self.version = version
        self.tools: dict[str, Tool] = {}
        for tool in tools:
            if tool.name in self.tools:
                raise ValueError(f"two tools share the name {tool.name!r}")
            self.tools[tool.name] = tool
        self.resources: dict[str, Resource] = {}
        for resource in resources:
            if resource.uri in self.resources:
                raise ValueError(f"two resources share the uri {resource.uri!r}")
            self.resources[resource.uri] = resource
        self.initialized = False
        self._methods = {
            "initialize": self._initialize,
            "ping": self._ping,
            "tools/list": self._tools_list,
            "tools/call": self._tools_call,
            "resources/list": self._resources_list,
            "resources/read": self._resources_read,
            "prompts/list": self._prompts_list,
        }

    # ------------------------------------------------------------------
    # the dispatch
    # ------------------------------------------------------------------
    def handle(self, message: dict) -> dict | None:
        """Handle one incoming JSON-RPC message.

        Returns the response object, or None for a notification (a message
        without an "id"), which must not be answered.
        """
        if not isinstance(message, dict):
            return failure(None, INVALID_REQUEST, "Invalid Request")
        method = message.get("method")
        if not isinstance(method, str):
            # A response to a request of ours.  The server asks nothing of
            # the client, so there is nothing to do with it.
            if "result" in message or "error" in message:
                return None
            return failure(message.get("id"), INVALID_REQUEST, "Invalid Request")

        message_id = message.get("id")
        is_request = "id" in message
        params = message.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            if not is_request:
                return None
            return failure(message_id, INVALID_PARAMS, "Invalid params")

        if method.startswith("notifications/"):
            self._notification(method, params)
            return None

        try:
            result = self._dispatch(method, params)
        except MethodNotFound:
            if not is_request:
                return None
            return failure(
                message_id, METHOD_NOT_FOUND, "Method not found", {"method": method}
            )
        except InvalidParams as exc:
            if not is_request:
                return None
            return failure(message_id, INVALID_PARAMS, "Invalid params", {"reason": str(exc)})
        except ResourceNotFound as exc:
            if not is_request:
                return None
            return failure(
                message_id, RESOURCE_NOT_FOUND, "Resource not found", {"reason": str(exc)}
            )
        except InternalError as exc:
            if not is_request:
                return None
            return failure(message_id, INTERNAL_ERROR, "Internal error", {"reason": str(exc)})
        except Exception as exc:  # the safety net: the session goes on
            log(f"the method {method!r} failed: {exc!r}")
            if not is_request:
                return None
            return failure(message_id, INTERNAL_ERROR, "Internal error", {"reason": str(exc)})

        if not is_request:
            # A request method that carries no id: the work is done and the
            # answer is dropped, because a notification is not answered.
            return None
        return success(message_id, result)

    def _dispatch(self, method: str, params: dict) -> Any:
        """Carry the method out and return its result."""
        handler = self._methods.get(method)
        if handler is None:
            raise MethodNotFound(method)
        return handler(params)

    def _notification(self, method: str, params: dict) -> None:
        """Carry a notification out.  A notification is never answered."""
        if method == "notifications/initialized":
            self.initialized = True
            return
        # Every other notification is a thing the server does not do.  It is
        # ignored on purpose: the client gets no answer either way.
        return

    # ------------------------------------------------------------------
    # the methods
    # ------------------------------------------------------------------
    def _initialize(self, params: dict) -> dict:
        """Answer the handshake: the revision, the capabilities and the name."""
        version = negotiate_version(params.get("protocolVersion"))
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {}, "resources": {}},
            "serverInfo": {"name": self.name, "version": self.version},
        }

    def _ping(self, params: dict) -> dict:
        """Answer the liveness probe with an empty result."""
        return {}

    def _tools_list(self, params: dict) -> dict:
        """Publish the tools and their schemas."""
        return {
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": tool.input_schema,
                }
                for tool in self.tools.values()
            ]
        }

    def _tools_call(self, params: dict) -> dict:
        """Carry one tool out and wrap its answer into a result.

        A tool that raises ``ToolError`` comes back as a result with
        ``isError`` set and the message of the exception: the client reads
        the message, so it must not be hidden in a JSON-RPC error object.
        Any other failure of the handler is reported the same way.
        """
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise InvalidParams("the call carries no tool name")
        tool = self.tools.get(name)
        if tool is None:
            raise InvalidParams(f"unknown tool: {name}")
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise InvalidParams("the arguments are not an object")
        try:
            value = tool.handler(arguments)
        except ToolError as exc:
            return {"content": content_blocks(_message_of(exc)), "isError": True}
        except Exception as exc:
            log(f"the tool {name!r} failed: {exc!r}")
            text = f"the tool failed: {type(exc).__name__}: {exc}"
            return {"content": content_blocks(text), "isError": True}
        return {"content": content_blocks(value), "isError": False}

    def _resources_list(self, params: dict) -> dict:
        """Publish the resources the client may read."""
        return {
            "resources": [
                {
                    "uri": resource.uri,
                    "name": resource.name,
                    "description": resource.description,
                    "mimeType": resource.mime_type,
                }
                for resource in self.resources.values()
            ]
        }

    def _resources_read(self, params: dict) -> dict:
        """Read one resource and return its text."""
        uri = params.get("uri")
        if not isinstance(uri, str) or not uri:
            raise InvalidParams("the read carries no resource uri")
        resource = self.resources.get(uri)
        if resource is None:
            raise ResourceNotFound(f"unknown resource: {uri}")
        try:
            text = resource.reader()
        except Exception as exc:
            log(f"the resource {uri!r} failed: {exc!r}")
            raise InternalError(f"the resource failed: {type(exc).__name__}: {exc}") from exc
        if not isinstance(text, str):
            text = str(text)
        return {
            "contents": [
                {
                    "uri": resource.uri,
                    "mimeType": resource.mime_type,
                    "text": text,
                }
            ]
        }

    def _prompts_list(self, params: dict) -> dict:
        """Publish the prompts of the server.  There are none."""
        return {"prompts": []}

    # ------------------------------------------------------------------
    # the transport
    # ------------------------------------------------------------------
    def serve(self, stdin=None, stdout=None) -> int:
        """Read newline-delimited JSON from stdin, write the answers to stdout.

        The stream is read one line at a time, so a client that writes a
        long answer and waits for it is served while it waits, and the
        server never holds the whole conversation in memory.  A blank line
        is skipped, a line that does not parse is reported as a parse error
        and the session goes on, and the end of the input ends the session
        with the status 0.
        """
        source = _binary_view(stdin) if stdin is not None else _binary_view(sys.stdin)
        sink = stdout if stdout is not None else sys.stdout
        try:
            for raw in source:
                text = _decode(raw)
                if text is None:
                    log("a line is not valid UTF-8 and was skipped")
                    self._write(sink, failure(None, PARSE_ERROR, "Parse error"))
                    continue
                stripped = text.strip()
                if not stripped:
                    continue
                try:
                    message = json.loads(stripped)
                except ValueError as exc:
                    log(f"a line could not be parsed and was skipped: {exc}")
                    self._write(sink, failure(None, PARSE_ERROR, "Parse error"))
                    continue
                response = self.handle(message)
                if response is None:
                    continue
                self._write(sink, response)
        except OSError as exc:
            log(f"the input stream failed: {exc}")
            return 1
        return 0

    @staticmethod
    def _write(sink: Any, payload: dict) -> None:
        """Write one response object to the output stream.

        The object is one line of JSON and nothing else: the stream is the
        protocol, and a line that is not a response breaks it.
        """
        text = json.dumps(payload, ensure_ascii=False, default=str) + "\n"
        try:
            sink.write(text)
        except UnicodeEncodeError:
            # The output stream cannot hold the character; the escaped form
            # carries the same text and fits every encoding.
            sink.write(json.dumps(payload, ensure_ascii=True, default=str) + "\n")
        except TypeError:
            # A binary stream: the bytes are UTF-8.
            sink.write(text.encode("utf-8"))
        flush = getattr(sink, "flush", None)
        if flush is not None:
            flush()


# ---------------------------------------------------------------------------
# the helpers
# ---------------------------------------------------------------------------
def _message_of(exc: Exception) -> str:
    """Return the message of an exception, or its kind when it has none."""
    text = str(exc).strip()
    if text:
        return text
    return type(exc).__name__


def _binary_view(stream: Any) -> Any:
    """Return the binary view of a stream when it has one.

    Reading the bytes and decoding them as UTF-8 is the only way to be sure
    of the encoding on every machine: the text layer of the standard input
    follows the code page of the console, and a client writes UTF-8.
    """
    buffer = getattr(stream, "buffer", None)
    return buffer if buffer is not None else stream


def _decode(raw: Any) -> str | None:
    """Decode one raw line, or return None when it is not valid UTF-8."""
    if isinstance(raw, bytes):
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if isinstance(raw, str):
        return raw
    return str(raw)
