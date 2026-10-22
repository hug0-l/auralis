"""
Tests for the model context server of Auralis (aurmcp).

The protocol core is JSON-RPC 2.0 over a newline delimited stream, and the
tests below cover the three things that break a client when they are wrong:
the shape of the answers, the error behaviour, and the rule that the output
stream carries the protocol and nothing else.

The server is exercised in two ways: message by message through ``handle``,
and over a real stream through ``serve`` and through the process itself.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from aurmcp import Resource, Server, Tool, ToolError
from aurmcp.protocol import PROTOCOL_VERSION, SUPPORTED_PROTOCOL_VERSIONS

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# the fixtures
# --------------------------------------------------------------------------
def _ok(_arguments: dict) -> str:
    """A tool that works."""
    return "fine"


def _boom(_arguments: dict) -> str:
    """A tool that fails in the way the client should be told about."""
    raise ToolError("the module refused the parameter")


def _crash(_arguments: dict) -> str:
    """A tool that fails in a way nobody planned for."""
    raise RuntimeError("the kernel of the module fell over")


@pytest.fixture
def server() -> Server:
    """A server carrying one tool of each kind and one resource."""
    tools = [
        Tool("ok", "a tool that works", {"type": "object", "properties": {}}, _ok),
        Tool("boom", "a tool that fails", {"type": "object", "properties": {}}, _boom),
        Tool("crash", "a tool that crashes", {"type": "object", "properties": {}}, _crash),
    ]
    resources = [
        Resource("aur://doc", "the doc", "the documentation", "text/plain", lambda: "the text")
    ]
    return Server(tools, resources)


@pytest.fixture
def empty_server() -> Server:
    """A server with no tool and no resource, the state before they are written."""
    return Server([], [])


# --------------------------------------------------------------------------
# the handshake
# --------------------------------------------------------------------------
def test_initialize_answers_the_revision_the_capabilities_and_the_name(empty_server):
    """initialize opens the session."""
    answer = empty_server.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    )
    assert answer is not None
    assert answer["jsonrpc"] == "2.0"
    assert answer["id"] == 1
    result = answer["result"]
    assert result["protocolVersion"] == PROTOCOL_VERSION
    assert result["capabilities"] == {"tools": {}, "resources": {}}
    assert result["serverInfo"]["name"] == "auralis"
    assert "version" in result["serverInfo"]


def test_initialize_echoes_a_revision_the_server_knows(empty_server):
    """A known revision is echoed back."""
    for revision in SUPPORTED_PROTOCOL_VERSIONS:
        answer = empty_server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": revision},
            }
        )
        assert answer["result"]["protocolVersion"] == revision


def test_initialize_answers_our_own_revision_for_an_unknown_one(empty_server):
    """An unknown revision is answered with the revision of the server."""
    answer = empty_server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "1900-01-01"},
        }
    )
    assert answer["result"]["protocolVersion"] == PROTOCOL_VERSION


def test_the_initialized_notification_is_not_answered(empty_server):
    """A notification is not answered."""
    assert empty_server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert empty_server.initialized is True


def test_ping_answers_an_empty_result(empty_server):
    """ping is the liveness probe."""
    answer = empty_server.handle({"jsonrpc": "2.0", "id": 2, "method": "ping"})
    assert answer["result"] == {}


# --------------------------------------------------------------------------
# the tools
# --------------------------------------------------------------------------
def test_tools_list_publishes_the_name_the_description_and_the_schema(server):
    """tools/list publishes every tool."""
    answer = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    tools = answer["result"]["tools"]
    assert [tool["name"] for tool in tools] == ["ok", "boom", "crash"]
    assert tools[0]["description"] == "a tool that works"
    assert tools[0]["inputSchema"] == {"type": "object", "properties": {}}


def test_tools_list_answers_an_empty_list_without_tools(empty_server):
    """The server runs before the tools are written."""
    answer = empty_server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    assert answer["result"] == {"tools": []}


def test_a_tool_that_works_comes_back_as_a_result(server):
    """The result of a tool carries the content and the flag."""
    answer = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "ok", "arguments": {}},
        }
    )
    assert answer["id"] == 4
    assert answer["result"]["isError"] is False
    assert answer["result"]["content"] == [{"type": "text", "text": "fine"}]


def test_a_tool_that_raises_tool_error_comes_back_as_a_result_with_is_error(server):
    """A failure the client should read is a result, not an error object."""
    answer = server.handle(
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "boom"}}
    )
    assert "error" not in answer
    assert answer["result"]["isError"] is True
    assert "the module refused the parameter" in answer["result"]["content"][0]["text"]


def test_a_tool_that_crashes_comes_back_as_a_result_with_is_error(server):
    """An unplanned failure is reported the same way."""
    answer = server.handle(
        {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "crash"}}
    )
    assert "error" not in answer
    assert answer["result"]["isError"] is True


def test_a_call_of_an_unknown_tool_is_an_invalid_params_error(server):
    """An unknown tool is a protocol error, not a tool failure."""
    answer = server.handle(
        {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "nope"}}
    )
    assert answer["error"]["code"] == -32602


def test_a_call_without_a_name_is_an_invalid_params_error(server):
    """A call that names no tool is a protocol error."""
    answer = server.handle({"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {}})
    assert answer["error"]["code"] == -32602


# --------------------------------------------------------------------------
# the resources
# --------------------------------------------------------------------------
def test_resources_list_publishes_the_uri_the_name_and_the_mime_type(server):
    """resources/list publishes every resource."""
    answer = server.handle({"jsonrpc": "2.0", "id": 9, "method": "resources/list"})
    assert answer["result"]["resources"] == [
        {
            "uri": "aur://doc",
            "name": "the doc",
            "description": "the documentation",
            "mimeType": "text/plain",
        }
    ]


def test_resources_read_returns_the_text_of_the_resource(server):
    """resources/read reads one resource."""
    answer = server.handle(
        {"jsonrpc": "2.0", "id": 10, "method": "resources/read", "params": {"uri": "aur://doc"}}
    )
    assert answer["result"]["contents"] == [
        {"uri": "aur://doc", "mimeType": "text/plain", "text": "the text"}
    ]


def test_a_read_of_an_unknown_resource_is_a_resource_not_found_error(server):
    """An unknown resource is a protocol error."""
    answer = server.handle(
        {"jsonrpc": "2.0", "id": 11, "method": "resources/read", "params": {"uri": "aur://none"}}
    )
    assert answer["error"]["code"] == -32002


# --------------------------------------------------------------------------
# the prompts
# --------------------------------------------------------------------------
def test_prompts_list_answers_an_empty_list(empty_server):
    """The server publishes no prompts."""
    answer = empty_server.handle({"jsonrpc": "2.0", "id": 12, "method": "prompts/list"})
    assert answer["result"] == {"prompts": []}


# --------------------------------------------------------------------------
# the failures
# --------------------------------------------------------------------------
def test_an_unknown_method_is_a_method_not_found_error(empty_server):
    """An unknown method is a protocol error."""
    answer = empty_server.handle({"jsonrpc": "2.0", "id": 13, "method": "no/such/method"})
    assert answer["error"]["code"] == -32601
    assert answer["error"]["message"] == "Method not found"


def test_a_message_without_a_method_is_an_invalid_request_error(empty_server):
    """A message that is not a request is a protocol error."""
    answer = empty_server.handle({"jsonrpc": "2.0", "id": 14})
    assert answer["error"]["code"] == -32600


def test_a_request_without_an_id_is_not_answered(empty_server):
    """A request method without an id is a notification."""
    assert empty_server.handle({"jsonrpc": "2.0", "method": "ping"}) is None
    assert empty_server.handle({"jsonrpc": "2.0", "method": "tools/list"}) is None


def test_a_response_of_the_client_is_ignored(empty_server):
    """The server asks nothing of the client, so its answers are dropped."""
    assert empty_server.handle({"jsonrpc": "2.0", "id": 15, "result": {}}) is None


# --------------------------------------------------------------------------
# the transport
# --------------------------------------------------------------------------
def test_serve_answers_every_request_of_the_stream(empty_server):
    """serve reads the stream and writes the answers."""
    source = io.StringIO(
        "\n".join(
            [
                '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}',
                '{"jsonrpc":"2.0","method":"notifications/initialized"}',
                '{"jsonrpc":"2.0","id":2,"method":"ping"}',
            ]
        )
        + "\n"
    )
    sink = io.StringIO()
    assert empty_server.serve(source, sink) == 0
    answers = [json.loads(line) for line in sink.getvalue().splitlines() if line.strip()]
    assert [answer["id"] for answer in answers] == [1, 2]


def test_serve_reports_a_bad_line_and_goes_on(empty_server):
    """A line that does not parse is a parse error and does not kill the server."""
    source = io.StringIO(
        "{not json\n" + '{"jsonrpc":"2.0","id":2,"method":"ping"}' + "\n"
    )
    sink = io.StringIO()
    assert empty_server.serve(source, sink) == 0
    answers = [json.loads(line) for line in sink.getvalue().splitlines() if line.strip()]
    assert len(answers) == 2
    assert answers[0]["error"]["code"] == -32700
    assert answers[0]["id"] is None
    assert answers[1]["result"] == {}


def test_serve_skips_the_blank_lines(empty_server):
    """A blank line carries no message."""
    source = io.StringIO("\n   \n" + '{"jsonrpc":"2.0","id":2,"method":"ping"}' + "\n")
    sink = io.StringIO()
    assert empty_server.serve(source, sink) == 0
    answers = [json.loads(line) for line in sink.getvalue().splitlines() if line.strip()]
    assert len(answers) == 1


def test_serve_writes_nothing_but_the_answers(empty_server):
    """The output stream carries the protocol and nothing else."""
    source = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\n')
    sink = io.StringIO()
    empty_server.serve(source, sink)
    lines = sink.getvalue().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {"jsonrpc": "2.0", "id": 1, "result": {}}


# --------------------------------------------------------------------------
# the process
# --------------------------------------------------------------------------
def _run(lines: list[str]) -> tuple[list[dict], int]:
    """Feed the lines to the server process and return the answers."""
    text = "".join(line + "\n" for line in lines)
    done = subprocess.run(
        [sys.executable, "-m", "aurmcp"],
        input=text.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(ROOT),
        timeout=60,
    )
    out = done.stdout.decode("utf-8")
    answers = [json.loads(line) for line in out.splitlines() if line.strip()]
    return answers, done.returncode


def test_the_process_answers_the_handshake():
    """The smoke test: one line in, one answer out, the status 0."""
    answers, status = _run(['{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'])
    assert status == 0
    assert len(answers) == 1
    assert answers[0]["result"]["protocolVersion"] == PROTOCOL_VERSION


def test_the_process_answers_a_bad_line_and_goes_on():
    """A bad line does not kill the session."""
    answers, status = _run(["{not json", '{"jsonrpc":"2.0","id":2,"method":"ping"}'])
    assert status == 0
    assert len(answers) == 2
    assert answers[0]["error"]["code"] == -32700
    assert answers[1]["result"] == {}


def test_the_process_answers_before_the_input_is_closed():
    """The server reads a line at a time: the answer comes before the end."""
    process = subprocess.Popen(
        [sys.executable, "-m", "aurmcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(ROOT),
    )
    try:
        process.stdin.write(b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n')
        process.stdin.flush()
        answered: list[str] = []

        def _read() -> None:
            answered.append(process.stdout.readline().decode("utf-8"))

        reader = threading.Thread(target=_read, daemon=True)
        reader.start()
        reader.join(timeout=30)
        assert answered, "the server answered nothing while the input was open"
        assert json.loads(answered[0])["id"] == 1
    finally:
        process.stdin.close()
        process.wait(timeout=30)
