"""
tests/test_routes.py — the route contract test.

Every route of the server is walked once.  The walk is driven by
:data:`aurserver.app.ROUTE_CONTRACT`, the single description of the routes:
the method, the path, the fields of the body the route understands, an
example body and the keys the answer carries.  The test is generated from
that table, so a route cannot be added to the server without a line in the
table and a test beside it.

For every route the test checks

* the method and the path exist;
* the body the contract names is accepted, and the answer carries the keys
  the interface expects;
* a body the route does not understand is refused with the error envelope
  ``{"error": "..."}``;
* a missing body is refused with the error envelope, for the routes that
  take a body;
* an id that does not exist is refused with the error envelope;
* a value of the wrong type is refused with the error envelope.

    python -m pytest tests/test_routes.py -q
"""
from __future__ import annotations

import json
import math
import struct
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.routing import Mount

from aurserver.app import NOT_FOUND, ROUTE_CONTRACT, RouteSpec, app

ROOT = Path(__file__).resolve().parents[1]

#: The documentation routes the framework mounts on its own.
DOC_ROUTES = {"/api/openapi.json", "/api/docs", "/docs/oauth2-redirect", "/redoc"}

#: The table the whole test is generated from.
SPECS = list(ROUTE_CONTRACT)

#: The name of every case: the method and the path of the route.
IDS = [f"{spec.method} {spec.path}" for spec in SPECS]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _wav(seconds: float = 2.0, sample_rate: int = 48000, channels: int = 2) -> bytes:
    """A short test recording: a 1 kHz tone in two channels."""
    frames = int(seconds * sample_rate)
    data = bytearray()
    for index in range(frames):
        value = 0.25 * math.sin(2.0 * math.pi * 1000.0 * index / sample_rate)
        for _channel in range(channels):
            data += struct.pack("<h", int(value * 32767))
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt "
    header += struct.pack(
        "<IHHIIHH",
        16,
        1,
        channels,
        sample_rate,
        sample_rate * channels * 2,
        channels * 2,
        16,
    )
    header += b"data" + struct.pack("<I", len(data))
    return header + bytes(data)


def _fill(value: Any, context: dict[str, Any]) -> Any:
    """Replace the ``$tokens`` of the contract with the real values."""
    if isinstance(value, str):
        for key, replacement in context.items():
            value = value.replace("$" + key, str(replacement))
        return value
    if isinstance(value, dict):
        return {key: _fill(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [_fill(item, context) for item in value]
    return value


def _path(
    spec: RouteSpec, context: dict[str, Any], overrides: dict[str, str] | None = None
) -> str:
    """The concrete path of one route."""
    values = {
        "index": str(spec.index),
        "asset_id": str(context["asset_id"]),
        "job_id": str(context["job_id"]),
    }
    if overrides:
        values.update(overrides)
    path = spec.path
    for key, value in values.items():
        path = path.replace("{" + key + "}", str(value))
    return path


def _payload(spec: RouteSpec, context: dict[str, Any], kind: str) -> Any:
    """The body one probe sends."""
    if kind == "example":
        return _fill(spec.example, context)
    if kind == "wrong":
        body = dict(_fill(spec.example, context))
        body["__unexpected_field__"] = True
        return body
    if kind == "missing":
        return None
    if kind == "unknown":
        return _fill(spec.unknown, context) if spec.unknown is not None else None
    if kind == "bad":
        if spec.bad is not None:
            return _fill(spec.bad, context)
        body = dict(_fill(spec.example, context))
        if spec.fields:
            body[spec.fields[0]] = {"__bad_value__": True}
        return body
    raise ValueError(f"unknown probe: {kind}")


def _call(
    client: TestClient,
    spec: RouteSpec,
    context: dict[str, Any],
    kind: str,
    overrides: dict[str, str] | None = None,
):
    """Send one request to a route and return the answer."""
    url = _path(spec, context, overrides)
    if spec.multipart:
        if kind == "example":
            return client.post(
                url, files={"file": ("test.wav", context["audio"], "audio/wav")}
            )
        if kind == "wrong":
            return client.post(url, json={"__unexpected_field__": True})
        if kind == "bad":
            return client.post(
                url, content=b"not an audio file", headers={"content-type": "audio/wav"}
            )
        return client.post(url)
    payload = _payload(spec, context, kind)
    if payload is None:
        return client.request(spec.method, url)
    return client.request(spec.method, url, json=payload)


def _assert_error_envelope(response, what: str) -> None:
    """The answer of a failure must be ``{"error": "message"}``."""
    assert 400 <= response.status_code < 600, (
        f"{what}: answered {response.status_code}, which is not a failure: "
        f"{response.text[:200]}"
    )
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001 - the failure has to be JSON
        raise AssertionError(
            f"{what}: the failure is not JSON: {response.text[:200]}"
        ) from None
    assert isinstance(payload, dict), f"{what}: the failure is not an object: {payload!r}"
    assert set(payload) == {"error"}, (
        f"{what}: the failure carries {sorted(payload)} instead of only 'error'"
    )
    assert isinstance(payload["error"], str) and payload["error"], (
        f"{what}: the error message is empty"
    )


def _app_routes(application) -> set[tuple[str, str]]:
    """Every route the application answers, with its method."""
    found: set[tuple[str, str]] = set()

    def walk(routes) -> None:
        """Collect the routes of a list, following the included routers."""
        for route in routes:
            original = getattr(route, "original_router", None)
            if original is not None:
                walk(getattr(original, "routes", []))
                continue
            if isinstance(route, Mount):
                continue
            path = getattr(route, "path", None)
            if path is None or path in DOC_ROUTES:
                continue
            methods = getattr(route, "methods", None)
            if methods:
                for method in methods:
                    found.add((method, path))
            else:
                found.add(("WS", path))

    walk(application.routes)
    return found


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def client() -> TestClient:
    """A client on the real application."""
    with TestClient(app, raise_server_exceptions=False) as tester:
        yield tester


@pytest.fixture(scope="session")
def audio() -> bytes:
    """The bytes of the test recording."""
    return _wav()


@pytest.fixture(scope="session")
def asset_id(client: TestClient, audio: bytes) -> str:
    """The id of the uploaded recording."""
    response = client.post(
        "/api/upload", files={"file": ("test.wav", audio, "audio/wav")}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["asset_id"])


@pytest.fixture(scope="session")
def job_id(client: TestClient, asset_id: str) -> str:
    """The id of a batch job that works on the uploaded recording."""
    path = f"var/assets/audio/{asset_id}.wav"
    response = client.post("/api/batch", json={"paths": [path]})
    assert response.status_code == 200, response.text
    return str(response.json()["job_id"])


@pytest.fixture()
def preset(client: TestClient) -> str:
    """A preset in the library; it is removed again when the test is done."""
    name = "Route Test " + uuid.uuid4().hex[:8]
    response = client.post(
        "/api/preset/save", json={"name": name, "module_code": "AUR_DECLICK"}
    )
    assert response.status_code == 200, response.text
    path = str(response.json()["path"])
    yield path
    (ROOT / "var" / "presets" / path).unlink(missing_ok=True)


@pytest.fixture()
def session(client: TestClient) -> str:
    """A session document; it is removed again when the test is done."""
    name = "Route Test " + uuid.uuid4().hex[:8]
    response = client.post("/api/session/save", json={"name": name})
    assert response.status_code == 200, response.text
    path = str(response.json()["path"])
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture()
def chain(client: TestClient) -> TestClient:
    """A chain with four modules.

    Position 0 is a module that can learn, position 1 is the module that
    takes a region, position 2 is a module with a sensitivity and position 3
    is a module that can run a learned model.  Every route of the chain is
    written against one of the four.
    """
    current = client.get("/api/chain").json().get("chain", [])
    for index in range(len(current) - 1, -1, -1):
        client.delete(f"/api/chain/{index}")
    client.post("/api/chain", json={"code": "AUR_DEHUM"})
    client.post("/api/chain", json={"code": "AUR_SPECTRALEDITOR"})
    client.post("/api/chain", json={"code": "AUR_DECLICK"})
    client.post("/api/chain", json={"code": "AUR_VOICEDENOISE"})
    return client


@pytest.fixture()
def context(
    asset_id: str, job_id: str, audio: bytes, preset: str, session: str
) -> dict[str, Any]:
    """The real values that replace the ``$tokens`` of the contract."""
    return {
        "asset_id": asset_id,
        "asset_path": f"var/assets/audio/{asset_id}.wav",
        "job_id": job_id,
        "audio": audio,
        "preset_path": preset,
        "session_path": session,
    }


# ---------------------------------------------------------------------------
# the coverage of the table
# ---------------------------------------------------------------------------
def test_the_contract_covers_every_route() -> None:
    """Every route of the application has a line in the contract."""
    contract = {(spec.method, spec.path) for spec in SPECS}
    reality = _app_routes(app)
    assert reality == contract, (
        "the contract and the application disagree; "
        f"only in the contract: {sorted(contract - reality)}, "
        f"only in the application: {sorted(reality - contract)}"
    )


def test_the_contract_has_no_duplicate_line() -> None:
    """No route is described twice."""
    lines = [(spec.method, spec.path) for spec in SPECS]
    assert len(lines) == len(set(lines)), f"duplicate lines: {lines}"


def test_the_contract_names_a_body_for_every_body_route() -> None:
    """A route that takes a body names the fields it understands."""
    for spec in SPECS:
        if spec.multipart:
            continue
        if spec.example and not spec.fields:
            raise AssertionError(
                f"{spec.method} {spec.path} has an example body but names no field"
            )


# ---------------------------------------------------------------------------
# the routes
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("spec", SPECS, ids=IDS)
def test_the_route_answers_the_contract(
    chain: TestClient, context: dict[str, Any], spec: RouteSpec
) -> None:
    """The route accepts the body the contract names and answers as it says."""
    if spec.websocket:
        pytest.skip("the route is a WebSocket")
    response = _call(chain, spec, context, "example")
    assert response.status_code == spec.status, (
        f"{spec.method} {spec.path}: answered {response.status_code} "
        f"instead of {spec.status}: {response.text[:300]}"
    )
    if spec.status >= 400:
        _assert_error_envelope(response, f"{spec.method} {spec.path}")
        return
    if spec.raw:
        assert response.content, f"{spec.method} {spec.path}: the answer is empty"
        return
    payload = response.json()
    assert isinstance(payload, dict), f"{spec.method} {spec.path}: the answer is not an object"
    missing = [key for key in spec.answer if key not in payload]
    assert not missing, (
        f"{spec.method} {spec.path}: the answer has no {missing}; "
        f"it carries {sorted(payload)}"
    )


@pytest.mark.parametrize("spec", SPECS, ids=IDS)
def test_a_body_the_route_does_not_understand_is_refused(
    client: TestClient, context: dict[str, Any], spec: RouteSpec
) -> None:
    """A body with a field the route does not know is refused."""
    if spec.websocket:
        pytest.skip("the route is a WebSocket")
    if not spec.json_body and not spec.multipart:
        pytest.skip("the route does not take a body")
    response = _call(client, spec, context, "wrong")
    _assert_error_envelope(
        response, f"{spec.method} {spec.path} with an unknown field"
    )


@pytest.mark.parametrize("spec", SPECS, ids=IDS)
def test_a_missing_body_is_refused(
    client: TestClient, context: dict[str, Any], spec: RouteSpec
) -> None:
    """A route that takes a body refuses to be called without one."""
    if spec.websocket:
        pytest.skip("the route is a WebSocket")
    if not spec.fields and not spec.multipart:
        pytest.skip("the route takes no body")
    response = _call(client, spec, context, "missing")
    _assert_error_envelope(response, f"{spec.method} {spec.path} without a body")


@pytest.mark.parametrize("spec", SPECS, ids=IDS)
def test_an_unknown_id_is_refused(
    client: TestClient, context: dict[str, Any], spec: RouteSpec
) -> None:
    """An id that does not exist is refused with the error envelope."""
    if spec.websocket:
        pytest.skip("the route is a WebSocket")
    if spec.id_param:
        overrides = {spec.id_param: NOT_FOUND[spec.id_param]}
        response = _call(client, spec, context, "example", overrides)
    elif spec.unknown is not None:
        response = _call(client, spec, context, "unknown")
    else:
        pytest.skip("the route names no id")
    _assert_error_envelope(response, f"{spec.method} {spec.path} with an unknown id")


@pytest.mark.parametrize("spec", SPECS, ids=IDS)
def test_a_bad_value_is_refused(
    client: TestClient, context: dict[str, Any], spec: RouteSpec
) -> None:
    """A value of the wrong type is refused with the error envelope."""
    if spec.websocket:
        pytest.skip("the route is a WebSocket")
    if spec.multipart:
        response = _call(client, spec, context, "bad")
    elif spec.fields:
        response = _call(client, spec, context, "bad")
    else:
        pytest.skip("the route takes no value")
    _assert_error_envelope(response, f"{spec.method} {spec.path} with a bad value")


# ---------------------------------------------------------------------------
# the envelope itself
# ---------------------------------------------------------------------------
def test_an_unknown_path_answers_the_error_envelope(client: TestClient) -> None:
    """A path the server does not know answers the error envelope."""
    response = client.get("/api/no-such-route")
    _assert_error_envelope(response, "GET /api/no-such-route")


def test_a_wrong_method_answers_the_error_envelope(client: TestClient) -> None:
    """A method the route does not have answers the error envelope."""
    response = client.request("PUT", "/api/modules")
    _assert_error_envelope(response, "PUT /api/modules")


def test_the_stream_reports_a_failure_in_its_own_envelope(client: TestClient) -> None:
    """The WebSocket reports a failure as an error message, not as a crash."""
    with client.websocket_connect("/ws/stream") as socket:
        socket.send_text(json.dumps({"type": "configure", "asset_id": "no-such-asset"}))
        answer = json.loads(socket.receive_text())
    assert answer.get("type") == "error", f"the stream answered {answer}"
    assert answer.get("message"), f"the stream answered {answer}"
