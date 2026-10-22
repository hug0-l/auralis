"""
aurserver.app — the Auralis HTTP API.

This module is pure orchestration: it owns the session engine, the asset
store and the request/response plumbing.  All DSP lives in :mod:`aurcore`.

The routes implemented here are the ones listed in the engineering contract,
section 7, plus the asset list, the measurement service, the session
documents and the health probe:

    GET    /api/modules
    GET    /api/chain
    POST   /api/chain
    DELETE /api/chain/{index}
    POST   /api/chain/reorder
    POST   /api/chain/{index}/bypass
    POST   /api/chain/{index}/param
    GET    /api/chain/{index}/params
    POST   /api/chain/{index}/learn
    POST   /api/chain/{index}/state
    POST   /api/chain/{index}/region
    GET    /api/assets
    POST   /api/upload
    POST   /api/analyze
    POST   /api/analysis
    POST   /api/render
    GET    /api/audio/{asset_id}
    POST   /api/compare
    GET    /api/health
    GET    /api/session/list
    POST   /api/session/save
    POST   /api/session/load

Four more route groups live in their own modules and are mounted on the very
same application instance at the end of :func:`create_app`, so there is
exactly one implementation of each of them:

    GET    /api/presets                       aurserver.presets
    POST   /api/preset/save
    POST   /api/preset/load
    DELETE /api/preset
    WS     /ws/stream                         aurserver.ws
    POST   /api/batch                         aurserver.batch
    GET    /api/batch/{job_id}
    POST   /api/batch/{job_id}/cancel
    GET    /api/delivery-targets              aurserver.batch_formats

The user interface is served from the same application:

    GET    /                                   the single page
    GET    /favicon.ico
    GET    /css/*   /js/*                      the static folders

:data:`ROUTE_CONTRACT` is the single description of every one of those
routes: the method, the path, the fields of the JSON body the route
understands, an example body and the keys the answer carries.  The contract
test (``tests/test_routes.py``) is generated from that table, so a route
cannot be added to the server without a line in the table and a test.

Errors are always ``{"error": "message"}`` with a 4xx/5xx status; no stack
trace ever reaches the client.  A request body that carries a field the
route does not know is refused the same way: a promise the server cannot
keep is not silently dropped.
"""

from __future__ import annotations

import asyncio
import base64
import importlib
import json
import logging
import math
import pkgutil
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import Body, FastAPI, HTTPException, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from aurcore import analysis as core_analysis
from aurcore import models as core_models
from aurcore import registry
from aurcore.api import Engine, create_engine
from aurcore.document import document_param_report, load_document, save_document
from aurcore.serial import typed_value as tv

from . import audio as au
from .assets import (
    MAX_ASSET_BYTES,
    Asset,
    AssetStore,
    AssetTooLarge,
    UnreadableAudio,
    UnsupportedFormat,
    UnsafeName,
)

__all__ = ["app", "create_app"]

log = logging.getLogger("auralis.server")

#: Project root (the directory that holds ``aurcore``, ``aurui`` and ``var``).
ROOT = Path(__file__).resolve().parent.parent

#: The static user interface.
AURUI = ROOT / "aurui"

#: Runtime scratch directory (uploads and renders).
VAR = ROOT / "var"

#: Where preset documents are stored.
PRESET_DIR = VAR / "presets"
SESSION_DIR = VAR / "sessions"

#: Default engine format; replaced as soon as an asset with another format lands.
DEFAULT_SR = 48000
DEFAULT_CHANNELS = 2

#: Number of frames processed per engine call when rendering.
RENDER_BLOCK = 65536

#: Websocket chunk size, in frames.
STREAM_CHUNK = 8192

#: Largest upload the server accepts, in bytes.
#:
#: The number is the limit of the asset store: a file above it is refused
#: before it is read, and the store refuses to keep it.
MAX_UPLOAD_BYTES = MAX_ASSET_BYTES


# ---------------------------------------------------------------------------
# the route contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RouteSpec:
    """One line of the route contract.

    ``method`` and ``path`` are the route itself; ``path`` may carry a
    placeholder (``{index}``, ``{asset_id}``, ``{job_id}``).  ``fields`` are
    the names a JSON body may carry: a body with a field that is not on the
    list is refused.  ``example`` is a body the route accepts and ``answer``
    are the keys the answer carries.
    """

    method: str
    path: str
    fields: tuple[str, ...] = ()
    example: dict[str, Any] = field(default_factory=dict)
    answer: tuple[str, ...] = ()
    #: The status a request that follows the contract is answered with.
    status: int = 200
    #: False when the body is not JSON (the upload route takes the file itself).
    json_body: bool = True
    #: True when the body is the audio file itself.
    multipart: bool = False
    #: True when the answer is bytes and not a JSON object.
    raw: bool = False
    #: True when the route is a WebSocket and not an HTTP route.
    websocket: bool = False
    #: The placeholder of the path that names an id, when there is one.
    id_param: str | None = None
    #: The position of the module in the chain the route works on.
    index: int = 0
    #: A body that names an id that does not exist.
    unknown: dict[str, Any] | None = None
    #: A body that carries a value the route must refuse.
    bad: dict[str, Any] | None = None


#: Every route the server answers, with the body it understands and the
#: keys it answers with.  This table is the contract; the test in
#: ``tests/test_routes.py`` walks it and checks that it covers the routes
#: the application really exposes.
ROUTE_CONTRACT: tuple[RouteSpec, ...] = (
    # -- the registry ---------------------------------------------------------
    RouteSpec(
        method="GET",
        path="/api/modules",
        answer=("modules",),
    ),
    # -- the model library ----------------------------------------------------
    RouteSpec(
        method="GET",
        path="/api/models",
        answer=("models", "running"),
    ),
    # -- the chain ------------------------------------------------------------
    RouteSpec(
        method="GET",
        path="/api/chain",
        answer=("chain",),
    ),
    RouteSpec(
        method="POST",
        path="/api/chain",
        fields=("code",),
        example={"code": "AUR_DECLICK"},
        answer=("chain",),
        unknown={"code": "AUR_NO_SUCH_MODULE"},
    ),
    RouteSpec(
        method="DELETE",
        path="/api/chain/{index}",
        answer=("chain",),
        id_param="index",
        index=2,
    ),
    RouteSpec(
        method="POST",
        path="/api/chain/reorder",
        fields=("from", "to", "src", "dst"),
        example={"from": 0, "to": 0},
        answer=("chain",),
        unknown={"from": 9999, "to": 0},
    ),
    RouteSpec(
        method="POST",
        path="/api/chain/{index}/bypass",
        fields=("bypass",),
        example={"bypass": True},
        answer=("chain",),
        id_param="index",
    ),
    RouteSpec(
        method="POST",
        path="/api/chain/{index}/param",
        fields=("name", "value"),
        example={"name": "Sensitivity", "value": 3.0},
        answer=("ok", "name", "value"),
        id_param="index",
        index=2,
    ),
    RouteSpec(
        method="GET",
        path="/api/chain/{index}/params",
        answer=("params", "values", "index", "code"),
        id_param="index",
    ),
    RouteSpec(
        method="POST",
        path="/api/chain/{index}/learn",
        answer=("ok", "message"),
        id_param="index",
    ),
    # The route that chooses the model a module runs.  An empty name takes
    # the model away and the module runs its hand written path again.
    RouteSpec(
        method="POST",
        path="/api/chain/{index}/model",
        fields=("model",),
        example={"model": "voice_enhance"},
        answer=("ok", "index", "code", "model", "message"),
        id_param="index",
        index=3,
    ),
    # The two routes that carry what a parameter cannot: the state of a
    # module (what it learned, the region of the spectral editor) and the
    # region of the spectral editor itself.
    RouteSpec(
        method="POST",
        path="/api/chain/{index}/state",
        fields=("state",),
        example={"state": "AAAA"},
        answer=("ok", "index", "code", "state"),
        id_param="index",
    ),
    RouteSpec(
        method="POST",
        path="/api/chain/{index}/region",
        fields=("start", "end", "low", "high"),
        example={"start": 1.25, "end": 1.75, "low": 800.0, "high": 6400.0},
        answer=("ok", "index", "code", "region", "region_length"),
        id_param="index",
        index=1,
    ),
    # -- the audio ------------------------------------------------------------
    RouteSpec(
        method="GET",
        path="/api/assets",
        answer=("assets", "current_asset_id"),
    ),
    RouteSpec(
        method="POST",
        path="/api/upload",
        json_body=False,
        multipart=True,
        answer=("asset_id", "sample_rate", "channels", "frames", "duration"),
    ),
    RouteSpec(
        method="POST",
        path="/api/analyze",
        fields=("asset_id", "start", "end"),
        example={"asset_id": "$asset_id"},
        answer=("waveform", "spectrogram", "freq_bins", "frames", "max_db", "min_db"),
        unknown={"asset_id": "no-such-asset"},
    ),
    RouteSpec(
        method="POST",
        path="/api/analysis",
        fields=("asset_id", "start", "end"),
        example={"asset_id": "$asset_id"},
        answer=("spectrum", "waveform_stats", "loudness", "correlation", "histogram"),
        unknown={"asset_id": "no-such-asset"},
    ),
    RouteSpec(
        method="POST",
        path="/api/render",
        fields=("asset_id", "chain", "start", "end", "learned"),
        example={"asset_id": "$asset_id"},
        answer=("asset_id", "url"),
        unknown={"asset_id": "no-such-asset"},
    ),
    RouteSpec(
        method="GET",
        path="/api/audio/{asset_id}",
        raw=True,
        id_param="asset_id",
    ),
    RouteSpec(
        method="POST",
        path="/api/compare",
        fields=("asset_id", "chain_a", "chain_b", "start", "end"),
        example={"asset_id": "$asset_id"},
        answer=("a", "b"),
        unknown={"asset_id": "no-such-asset"},
    ),
    # -- the session ----------------------------------------------------------
    RouteSpec(
        method="GET",
        path="/api/health",
        answer=("ok",),
    ),
    RouteSpec(
        method="GET",
        path="/api/session/list",
        answer=("sessions",),
    ),
    RouteSpec(
        method="POST",
        path="/api/session/save",
        fields=("name", "path", "view", "markers", "audio", "history", "chain"),
        example={"name": "Route Test"},
        answer=("path", "document"),
    ),
    RouteSpec(
        method="POST",
        path="/api/session/load",
        fields=("name", "path", "view", "markers", "audio", "history", "chain"),
        example={"name": "Route Test"},
        answer=("path", "document", "chain", "summary"),
        unknown={"path": "no-such-session.json"},
    ),
    # -- the presets ----------------------------------------------------------
    RouteSpec(
        method="GET",
        path="/api/presets",
        answer=("presets",),
    ),
    RouteSpec(
        method="POST",
        path="/api/preset/save",
        fields=(
            "name",
            "category",
            "module_code",
            "module",
            "element",
            "display_name",
            "description",
            "params",
            "state",
        ),
        example={"name": "Route Test", "module_code": "AUR_DECLICK"},
        answer=("path", "preset"),
    ),
    RouteSpec(
        method="POST",
        path="/api/preset/load",
        fields=("path", "module_code"),
        example={"path": "$preset_path"},
        answer=("format", "version", "module", "params"),
        unknown={"path": "no-such-preset.json"},
    ),
    RouteSpec(
        method="DELETE",
        path="/api/preset",
        fields=("path",),
        example={"path": "$preset_path"},
        answer=("ok", "path"),
        unknown={"path": "no-such-preset.json"},
    ),
    # -- the batch ------------------------------------------------------------
    RouteSpec(
        method="POST",
        path="/api/batch",
        fields=(
            "paths",
            "format",
            "bit_depth",
            "target_lufs",
            "loudness_normalise",
            "chain",
            "out_dir",
        ),
        example={"paths": ["$asset_path"]},
        answer=("job_id", "state", "total"),
        bad={"paths": "not-a-list"},
    ),
    RouteSpec(
        method="GET",
        path="/api/batch/{job_id}",
        answer=("job_id", "state", "total", "done", "failed", "results"),
        id_param="job_id",
    ),
    RouteSpec(
        method="POST",
        path="/api/batch/{job_id}/cancel",
        answer=("job_id", "state", "cancelled"),
        id_param="job_id",
    ),
    # -- the delivery catalogue -----------------------------------------------
    RouteSpec(
        method="GET",
        path="/api/delivery-targets",
        answer=("targets",),
    ),
    # -- the streaming preview ------------------------------------------------
    RouteSpec(
        method="WS",
        path="/ws/stream",
        websocket=True,
    ),
    # -- the user interface ---------------------------------------------------
    RouteSpec(
        method="GET",
        path="/",
        raw=True,
    ),
    RouteSpec(
        method="GET",
        path="/favicon.ico",
        raw=True,
        status=404,
    ),
)

#: The value a path parameter takes when a test looks for an id that does
#: not exist.
NOT_FOUND: dict[str, str] = {
    "index": "9999",
    "asset_id": "no-such-asset",
    "job_id": "no-such-job",
}


def _path_pattern(template: str) -> re.Pattern[str]:
    """Build the matcher of one route template."""
    parts: list[str] = []
    for chunk in re.split(r"(\{[^}]+\})", template):
        if chunk.startswith("{") and chunk.endswith("}"):
            parts.append(r"[^/]+")
        else:
            parts.append(re.escape(chunk))
    return re.compile("^" + "".join(parts) + "$")


#: The contract, the most specific route first: a literal path wins over a
#: path that carries a placeholder.
_ROUTE_MATCHERS: tuple[tuple[str, re.Pattern[str], RouteSpec], ...] = tuple(
    sorted(
        ((spec.method, _path_pattern(spec.path), spec) for spec in ROUTE_CONTRACT),
        key=lambda item: item[1].pattern.count("[^/]+"),
    )
)


def route_spec(method: str, path: str) -> RouteSpec | None:
    """The contract line of a method and a concrete path, if there is one."""
    for route_method, pattern, spec in _ROUTE_MATCHERS:
        if route_method != method:
            continue
        if pattern.match(path):
            return spec
    return None


async def _read_body(receive: Any, finished: Any) -> tuple[bytes, Any]:
    """Read the whole request body and hand a replayable reader back.

    The reader offers the body once.  Every later call waits for the
    response to be finished and then reports a disconnect, which is what a
    server does when the client is gone.  A disconnect that arrives before
    the response is finished would cut the answer short, and a reader that
    never reports a disconnect would keep a file response waiting for ever.
    """
    chunks: list[bytes] = []
    more = True
    while more:
        message = await receive()
        if message.get("type") == "http.disconnect":
            break
        chunks.append(message.get("body", b""))
        more = bool(message.get("more_body", False))
    body = b"".join(chunks)
    state = {"sent": False}

    async def replay() -> dict:
        """Hand the body to the route."""
        if not state["sent"]:
            state["sent"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        await finished.wait()
        return {"type": "http.disconnect"}

    return body, replay


class BodyContractMiddleware:
    """Refuse a JSON body that carries a field the route does not know.

    A request body is a promise: it says what the client wants.  A body
    with a field the route has never heard of belongs to a client that
    believes it asked for something the server did not do, so the request
    is refused with the error envelope instead of being half-ignored.
    """

    def __init__(self, app: Any) -> None:
        """Keep the application the middleware wraps."""
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        """Check the body of one request and pass it on."""
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        spec = route_spec(scope.get("method", ""), scope.get("path", ""))
        if spec is None or not spec.json_body:
            await self.app(scope, receive, send)
            return
        content_type = ""
        for key, value in scope.get("headers", ()):
            if key == b"content-type":
                content_type = value.decode("latin-1")
                break
        if content_type.split(";")[0].strip().lower() != "application/json":
            await self.app(scope, receive, send)
            return

        # The reader of the body has to know when the answer is finished:
        # a file response waits for a disconnect and would wait for ever
        # without it.
        finished = asyncio.Event()
        body, replay = await _read_body(receive, finished)
        payload: Any = None
        if body:
            try:
                payload = json.loads(body)
            except ValueError:
                payload = None
        if isinstance(payload, dict):
            unknown = sorted(set(payload) - set(spec.fields))
            if unknown:
                response = JSONResponse(
                    status_code=400,
                    content={
                        "error": "the body carries fields this route does not know: "
                        + ", ".join(unknown)
                    },
                )
                await response(scope, replay, send)
                return

        async def send_wrapper(message: Any) -> None:
            """Mark the end of the answer for the body reader.

            The answer is sent first and marked afterwards: a file response
            stops as soon as it hears that the answer is over, and it must
            not stop before the last piece of the file has left.
            """
            await send(message)
            if message.get("type") == "http.response.body" and not message.get(
                "more_body", False
            ):
                finished.set()

        await self.app(scope, replay, send_wrapper)


# ---------------------------------------------------------------------------
# session state
# ---------------------------------------------------------------------------

_LOCK = threading.RLock()
_ENGINE: Engine | None = None
_ENGINE_SR = DEFAULT_SR
_ENGINE_CHANNELS = DEFAULT_CHANNELS

#: The asset the session works on is held by the store: it knows which one
#: it is after an upload, after a delete and after a restart.
STORE = AssetStore(VAR / "assets")

#: The state of the modules that were taken out of the chain, by code.
#:
#: The history of the interface lives in the client: an undo of a removal
#: puts the module back into the chain, and the server builds a fresh
#: instance of it.  The learned profile of a module is not a parameter and
#: no parameter route can carry it, so the server keeps the state of every
#: module it removes: the module that is put back is the module that was
#: taken out, with what it learned.
_REMOVED_STATES: dict[str, list[str]] = {}

#: How many removed modules the server keeps the state of.
MAX_REMOVED_STATES = 32

# Files left in the asset directory belong to an earlier session; drop them.
_stale = STORE.sweep()
if _stale:
    log.info("removed %d stale asset file(s) from %s", _stale, STORE.audio_dir)


# ---------------------------------------------------------------------------
# engine helpers
# ---------------------------------------------------------------------------


def _get_engine() -> Engine:
    """Return the session engine, creating it on first use."""
    global _ENGINE
    with _LOCK:
        if _ENGINE is None:
            _ENGINE = create_engine(_ENGINE_SR, _ENGINE_CHANNELS)
        return _ENGINE


def _ensure_engine(sample_rate: int, channels: int) -> Engine:
    """Return the session engine, rebuilt when the audio format changes.

    The chain (module codes, parameters and bypass flags) is carried over to
    the new engine so the user's session survives an upload in a different
    sample rate or channel count.
    """
    global _ENGINE, _ENGINE_SR, _ENGINE_CHANNELS
    with _LOCK:
        engine = _get_engine()
        if engine.sample_rate == sample_rate and engine.channels == channels:
            return engine
        steps = engine.chain_summary()
        fresh = create_engine(sample_rate, channels)
        for step in steps:
            code = step.get("code")
            if not code:
                continue
            try:
                fresh.add_module(code)
            except Exception:
                continue
            index = len(fresh.chain) - 1
            if step.get("bypass"):
                fresh.set_bypass(index, True)
            for name, value in tv.unwrap_all(step.get("params", {})).items():
                try:
                    fresh.set_param(index, name, value)
                except Exception:
                    pass
        _ENGINE = fresh
        _ENGINE_SR, _ENGINE_CHANNELS = sample_rate, channels
        return fresh


def _build_engine(chain: Any, sample_rate: int, channels: int) -> Engine:
    """Build a throw-away engine from an explicit chain description.

    ``chain`` may be a list of steps or a full ``auralis.chain`` document
    (``{"steps": [...]}``).  A step is a dict with a ``code`` and optional
    ``enabled`` / ``bypass`` / ``params`` / ``state`` fields, or a bare module
    code string.
    """
    steps = _normalise_chain(chain)
    engine = create_engine(sample_rate, channels)
    for step in steps:
        code = step["code"]
        if not registry.has(code):
            _load_registry()
        if not registry.has(code):
            raise HTTPException(status_code=404, detail=f"unknown module code: {code}")
        try:
            engine.add_module(code)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"unknown module code: {code}")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"cannot create module {code}: {exc}")
        index = len(engine.chain) - 1
        if step.get("bypass"):
            engine.set_bypass(index, True)
        for name, value in step.get("params", {}).items():
            try:
                engine.set_param(index, name, value)
            except Exception:
                log.warning("ignoring unknown parameter %r for %s", name, code)
        state = step.get("state")
        if state:
            try:
                engine.chain[index].module.load_state_base64(state)
            except Exception:
                log.warning("could not restore module state for %s", code)
    return engine


def _state_text(state: Any) -> str:
    """The base64 text of a state, whatever shape it arrives in.

    A state travels as the base64 text of the module (the chain documents of
    the client) or wrapped in the typed-value envelope of the core (the
    documents of the server).  The envelope of a binary state carries the
    bytes, so it is written back into its base64 text.
    """
    if state is None:
        return ""
    if isinstance(state, (bytes, bytearray)):
        return base64.b64encode(bytes(state)).decode("ascii")
    if isinstance(state, str):
        return state
    if isinstance(state, dict):
        raw = tv.unwrap(state, None)
        if isinstance(raw, (bytes, bytearray)):
            return base64.b64encode(bytes(raw)).decode("ascii")
        if isinstance(raw, str):
            return raw
    return ""


def _park_state(code: str, state: str) -> None:
    """Keep the state of a module that was taken out of the chain.

    The history of the interface lives in the client: an undo of a removal
    puts the module back into the chain, and the server builds a fresh
    instance of it.  What the module learned is not a parameter and no
    parameter route can carry it, so the server keeps the state of every
    module it removes.
    """
    if not code or not state:
        return
    with _LOCK:
        parked = _REMOVED_STATES.setdefault(code, [])
        parked.append(state)
        del parked[:-4]
        while len(_REMOVED_STATES) > MAX_REMOVED_STATES:
            _REMOVED_STATES.pop(next(iter(_REMOVED_STATES)), None)


def _take_parked_state(code: str) -> str:
    """Take back the state of the module that was taken out of the chain."""
    with _LOCK:
        parked = _REMOVED_STATES.get(code)
        if not parked:
            return ""
        return parked.pop()


def _push_chain(engine: Engine, chain: Any) -> None:
    """Write a chain description into the engine.

    The chain of a session document is a promise: it names the modules, their
    parameters, their bypass flag and their state.  A client that pushes a
    chain gets that chain back, so an undo that puts a module into the chain
    does not lose what the module learned.

    The chain is merged into the engine and not written over it: the modules
    it names are updated, the modules it does not name are left alone, and a
    step that carries no state leaves the state of its module alone.  A
    client that knows less about the chain than the server therefore cannot
    take anything away.
    """
    steps = _normalise_chain(chain)
    taken: set[int] = set()
    for step in steps:
        code = step["code"]
        if not registry.has(code):
            _load_registry()
        index = -1
        for position, handle in enumerate(engine.chain):
            if position in taken:
                continue
            if handle.module.code == code:
                index = position
                break
        if index < 0:
            try:
                engine.add_module(code)
            except KeyError:
                raise HTTPException(status_code=404, detail=f"unknown module code: {code}")
            except Exception as exc:
                raise HTTPException(
                    status_code=400, detail=f"cannot create module {code}: {exc}"
                )
            index = len(engine.chain) - 1
        taken.add(index)
        module = engine.chain[index].module
        if step.get("bypass") is not None:
            engine.set_bypass(index, bool(step["bypass"]))
        for name, value in step.get("params", {}).items():
            try:
                engine.set_param(index, name, value)
            except Exception:
                log.warning("ignoring unknown parameter %r for %s", name, code)
        state = step.get("state")
        if state:
            try:
                module.load_state_base64(state)
            except Exception:
                log.warning("could not restore module state for %s", code)


def _normalise_chain(chain: Any) -> list[dict]:
    """Turn a chain description into a list of step dictionaries."""
    if chain is None:
        return []
    if isinstance(chain, dict):
        chain = chain.get("steps", [])
    if not isinstance(chain, list):
        raise HTTPException(status_code=400, detail="chain must be a list of steps")
    steps: list[dict] = []
    for position, item in enumerate(chain):
        if isinstance(item, str):
            steps.append({"code": item, "bypass": None, "params": {}, "state": None})
            continue
        if not isinstance(item, dict):
            raise HTTPException(
                status_code=400, detail=f"chain step {position} must be an object"
            )
        code = item.get("code") or item.get("module") or ""
        code = str(code).strip()
        if not code:
            raise HTTPException(
                status_code=400, detail=f"chain step {position} has no module code"
            )
        raw_params = item.get("params") or {}
        if not isinstance(raw_params, dict):
            raise HTTPException(
                status_code=400, detail=f"chain step {position} params must be an object"
            )
        enabled = item.get("enabled")
        bypass = item.get("bypass")
        if bypass is not None:
            is_bypassed: bool | None = bool(bypass)
        elif enabled is not None:
            is_bypassed = not bool(enabled)
        else:
            # the step says nothing about the bypass flag: the caller decides
            is_bypassed = None
        steps.append(
            {
                "code": code,
                "bypass": is_bypassed,
                "params": tv.unwrap_all(raw_params),
                "state": _state_text(item.get("state")) or None,
            }
        )
    return steps


def _load_registry() -> list[str]:
    """Import the module package so the registry is populated.

    Modules are registered by import.  A module file that fails to import
    must not take the whole registry down, so every file is imported
    individually and failures are logged.
    """
    failures: list[str] = []
    try:
        importlib.import_module("aurcore.modules")
    except Exception as exc:  # a broken module file aborts the package import
        failures.append(f"aurcore.modules: {exc}")
    try:
        package = importlib.import_module("aurcore.modules")
    except Exception:
        package = None
    if package is not None:
        for _finder, name, _ispkg in pkgutil.iter_modules(list(package.__path__)):
            if name.startswith("_"):
                continue
            try:
                importlib.import_module(f"aurcore.modules.{name}")
            except Exception as exc:
                failures.append(f"{name}: {exc}")
    if failures:
        log.warning("module registry incomplete: %s", "; ".join(failures))
    return list(registry.codes())


# ---------------------------------------------------------------------------
# asset helpers
# ---------------------------------------------------------------------------


def _get_asset(asset_id: str) -> Asset:
    """Fetch an asset or raise a 404."""
    if not asset_id:
        raise HTTPException(status_code=400, detail="asset_id is required")
    try:
        return STORE.get(asset_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown asset: {asset_id}")


def _slice(x: np.ndarray, sample_rate: int, start: float | None, end: float | None) -> np.ndarray:
    """Slice a buffer by seconds.  ``end`` <= 0 means "until the end"."""
    total = int(x.shape[0])
    if total == 0:
        return x
    start_s = float(start or 0.0)
    end_s = float(end or 0.0)
    if start_s < 0:
        start_s = max(0.0, total / sample_rate + start_s)
    first = max(0, min(total, int(round(start_s * sample_rate))))
    if end_s > start_s:
        last = max(first, min(total, int(round(end_s * sample_rate))))
    else:
        last = total
    return x[first:last]


def _render(engine: Engine, x: np.ndarray) -> tuple[np.ndarray, list[str]]:
    """Run an engine over a buffer in blocks and return the result.

    A module that raises while processing is not allowed to take the whole
    render down: the offending block is passed through unchanged and the
    failure is reported as a warning, so a broken module cannot silently
    destroy the session.  The warnings are returned to the caller.

    A module that looks ahead delays the signal, and the host has to
    compensate that delay: the render is fed the material and the silence
    of the delay, and the silence the delay puts in front of the answer is
    dropped afterwards.  The file then holds the material from the first
    sample to the last.

    The length of the material is declared to the modules that shape it
    over time, so that a fade at the tail of the file lands on the tail of
    the file.
    """
    total = int(x.shape[0])
    channels = int(x.shape[1])
    latency = int(engine.latency_samples)
    for step in engine.chain:
        setter = getattr(step.module, "set_material_length", None)
        if setter is not None:
            setter(total)
    padded = x
    if latency > 0:
        padded = np.concatenate(
            [x, np.zeros((latency, channels), dtype=np.float64)], axis=0
        )
    out = np.empty((padded.shape[0], channels), dtype=np.float64)
    warnings: list[str] = []
    for start in range(0, padded.shape[0], RENDER_BLOCK):
        chunk = padded[start : start + RENDER_BLOCK]
        try:
            processed = engine.process(chunk)
        except Exception as exc:
            warnings.append(f"block at {start}: {type(exc).__name__}: {exc}")
            log.warning("render block %d failed: %s", start, exc)
            processed = chunk
        if processed is None:
            processed = chunk
        processed = np.asarray(processed, dtype=np.float64)
        if processed.ndim == 1:
            processed = processed[:, None]
        if processed.shape[1] != channels:
            processed = _fit_channels(processed, channels)
        length = min(chunk.shape[0], processed.shape[0])
        out[start : start + length] = processed[:length]
        if length < chunk.shape[0]:
            out[start + length : start + chunk.shape[0]] = chunk[length:]
    if latency > 0:
        # the silence the delay puts in front of the answer is dropped, and
        # the tail the delay pushed behind the end of the material is taken
        # off, so that the file holds exactly the material
        out = out[latency : latency + total]
    return out, warnings


def _running_models(engine: Engine) -> list[dict[str, Any]]:
    """The model that runs for every module of a chain.

    The name is the name of the model that is **running**, and the message
    says why there is no model: the library is empty, the model that was
    asked for is not in it, the manifest does not match the engine, or the
    module is on the real time path, where a model is never loaded.
    """
    running: list[dict[str, Any]] = []
    for index, step in enumerate(getattr(engine, "chain", []) or []):
        module = getattr(step, "module", None)
        if module is None:
            continue
        described = getattr(module, "model_description", None)
        running.append(
            {
                "index": index,
                "code": getattr(module, "code", ""),
                "model": described if isinstance(described, dict) else {},
                "message": str(getattr(module, "model_message", "") or ""),
            }
        )
    return running


def _learned_restoration(
    x: np.ndarray, name: str, sample_rate: int
) -> tuple[np.ndarray, dict[str, Any]]:
    """The learned restoration of a file render: a model over the whole file.

    The stage runs **after** the chain: the chain is the hand written
    restoration, and the model refines what it produced.  It runs over the
    **whole file** in one pass, because a model that is asked once per
    sample has no business being asked at the edge of a block.

    The decision is made on the **mix** of the channels and the gain is
    applied to every channel, so the image does not move.

    A name the library does not hold is a refusal: a render that was asked
    for a model and did not get one is not the render that was asked for.
    """
    from aurcore import models

    try:
        out, model = models.run_named(name, x, sample_rate=sample_rate)
    except models.ModelError as error:
        raise HTTPException(
            status_code=400, detail=f"the model {name!r} cannot be run: {error}"
        ) from error
    described = model.describe()
    described["stage"] = "learned"
    return out, described


def _fit_channels(arr: np.ndarray, channels: int) -> np.ndarray:
    """Force an array to a given channel count."""
    have = arr.shape[1]
    if have == channels:
        return arr
    if have > channels:
        return np.ascontiguousarray(arr[:, :channels])
    pad = np.zeros((arr.shape[0], channels - have), dtype=np.float64)
    return np.ascontiguousarray(np.concatenate([arr, pad], axis=1))


def _set_current(asset_id: str | None) -> None:
    """Make an asset the one the session works on.

    The store owns the current asset: it knows which one it is after an
    upload, after a delete and after a restart.
    """
    try:
        STORE.set_current(asset_id)
    except KeyError:
        pass


async def _receive_upload(
    request: Request, content_type: str, staging: Path
) -> tuple[str, int]:
    """Write the uploaded file to disk; return its name and its size.

    The body is read in blocks and written straight to the staging file, so
    an upload never has to be in memory.  The limit is checked while the
    body is written, so a body that is too large is refused after the first
    megabyte instead of after the last one.
    """
    if "multipart/form-data" not in content_type.lower():
        written = 0
        with staging.open("wb") as handle:
            async for chunk in request.stream():
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail=_upload_too_large())
                handle.write(chunk)
        return "upload.bin", written

    match = re.search(r'boundary="?([^";]+)"?', content_type, re.IGNORECASE)
    if not match:
        raise HTTPException(status_code=400, detail="the multipart boundary is missing")
    marker = b"--" + match.group(1).strip().encode("utf-8", "replace")
    keep = len(marker) + 4
    name = ""
    written = 0
    buffer = b""
    state = "head"
    with staging.open("wb") as handle:
        async for chunk in request.stream():
            if not chunk:
                continue
            buffer += chunk
            while state != "done":
                if state == "head":
                    index = buffer.find(b"\r\n\r\n")
                    if index < 0:
                        if len(buffer) > 65536:
                            raise HTTPException(
                                status_code=400, detail="the part headers are too long"
                            )
                        break
                    head = buffer[:index].decode("utf-8", "replace")
                    buffer = buffer[index + 4 :]
                    field = ""
                    found = ""
                    for line in head.splitlines():
                        if not line.lower().startswith("content-disposition"):
                            continue
                        name_match = re.search(r'name="([^"]*)"', line)
                        file_match = re.search(r'filename="([^"]*)"', line)
                        if name_match:
                            field = name_match.group(1)
                        if file_match:
                            found = file_match.group(1)
                    if field in ("file", "audio", "upload") or (found and not field):
                        name = found or "upload.bin"
                        state = "body"
                    else:
                        state = "skip"
                    continue
                index = buffer.find(marker)
                if index < 0:
                    safe = buffer[:-keep] if len(buffer) > keep else b""
                    if safe:
                        if state == "body":
                            written += len(safe)
                            if written > MAX_UPLOAD_BYTES:
                                raise HTTPException(
                                    status_code=413, detail=_upload_too_large()
                                )
                            handle.write(safe)
                        buffer = buffer[len(safe) :]
                    break
                payload = buffer[:index]
                if payload.endswith(b"\r\n"):
                    payload = payload[:-2]
                elif payload.endswith(b"\n"):
                    payload = payload[:-1]
                if state == "body":
                    written += len(payload)
                    if written > MAX_UPLOAD_BYTES:
                        raise HTTPException(status_code=413, detail=_upload_too_large())
                    handle.write(payload)
                buffer = buffer[index + len(marker) :]
                state = "head" if state == "skip" else "done"
            if state == "done":
                break
    if state != "done":
        raise HTTPException(status_code=400, detail="the request has no 'file' part")
    return name or "upload.bin", written


def _upload_too_large() -> str:
    """The message that says the upload is too large."""
    return f"the upload is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB"


def _current_asset() -> Asset:
    """The most recently uploaded or rendered asset."""
    asset_id = _current_asset_id()
    if not asset_id:
        raise HTTPException(status_code=400, detail="no audio is loaded")
    try:
        return STORE.get(asset_id)
    except KeyError:
        raise HTTPException(status_code=400, detail="the loaded audio is no longer available")


def _current_asset_id() -> str | None:
    """The id of the asset the session is working on, if there is one."""
    return STORE.current_asset_id


def _analysis_range(
    start: float | None, end: float | None, duration: float
) -> tuple[float | None, float | None]:
    """Normalise the selection of an analysis request.

    The rules are the ones the render route uses: a negative start counts
    back from the end of the asset and an end of zero (or less) means
    "until the end".  A range that covers the whole asset is no range at
    all, so it is dropped and the payload reports an unbounded measurement.
    """
    first = None if start is None else float(start)
    last = None if end is None else float(end)
    if first is not None and first < 0.0:
        first = max(0.0, float(duration) + first)
    if last is not None and last <= 0.0:
        last = None
    if (first is None or first <= 0.0) and (last is None or last >= float(duration)):
        return None, None
    return first, last


def _asset_path(asset_id: str) -> str:
    """The path of an asset relative to the project root.

    The batch runner resolves a relative path against the project root,
    so this is the path a client hands to it.
    """
    try:
        path = STORE.path(asset_id).resolve()
    except KeyError:
        return ""
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


# ---------------------------------------------------------------------------
# request models
# ---------------------------------------------------------------------------


class ChainAddIn(BaseModel):
    """Body of ``POST /api/chain``."""

    model_config = ConfigDict(extra="forbid")

    code: str = ""


class SessionIn(BaseModel):
    """Body of ``POST /api/session/save`` and ``POST /api/session/load``.

    ``chain`` is the chain of the session, in the shape of a chain document.
    When it is given the server writes it into the engine before it answers,
    so a client can hand the state of its modules back; when it is missing
    the server works with the chain of the live engine.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str = ""
    path: str = ""
    view: dict[str, Any] = Field(default_factory=dict)
    markers: list[dict[str, Any]] = Field(default_factory=list)
    audio: dict[str, Any] = Field(default_factory=dict)
    history: Any = None
    chain: Any = None


class ReorderIn(BaseModel):
    """Body of ``POST /api/chain/reorder``."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    src: int = Field(alias="from")
    dst: int = Field(default=0, alias="to")


class BypassIn(BaseModel):
    """Body of ``POST /api/chain/{index}/bypass``."""

    model_config = ConfigDict(extra="forbid")

    bypass: bool = False


class ParamIn(BaseModel):
    """Body of ``POST /api/chain/{index}/param``."""

    model_config = ConfigDict(extra="forbid")

    name: str = ""
    value: Any = None


class StateIn(BaseModel):
    """Body of ``POST /api/chain/{index}/state``."""

    model_config = ConfigDict(extra="forbid")

    state: str = ""


class ModelIn(BaseModel):
    """Body of ``POST /api/chain/{index}/model``."""

    model_config = ConfigDict(extra="forbid")

    #: The name of the model the module shall run.  An empty name is the
    #: choice of **no** model: the module runs its hand written path.
    model: str = ""


class RegionIn(BaseModel):
    """Body of ``POST /api/chain/{index}/region``."""

    model_config = ConfigDict(extra="forbid")

    start: float
    end: float
    low: float
    high: float


class AnalyzeIn(BaseModel):
    """Body of ``POST /api/analyze``."""

    model_config = ConfigDict(extra="forbid")

    asset_id: str = ""
    start: float | None = None
    end: float | None = None


class AnalysisIn(BaseModel):
    """Body of ``POST /api/analysis``."""

    model_config = ConfigDict(extra="forbid")

    asset_id: str = ""
    start: float | None = None
    end: float | None = None


class RenderIn(BaseModel):
    """Body of ``POST /api/render``."""

    model_config = ConfigDict(extra="forbid")

    asset_id: str = ""
    chain: Any = None
    start: float = 0.0
    end: float = 0.0
    learned: str = ""


class CompareIn(BaseModel):
    """Body of ``POST /api/compare``."""

    model_config = ConfigDict(extra="forbid")

    asset_id: str = ""
    chain_a: Any = None
    chain_b: Any = None
    start: float = 0.0
    end: float = 0.0


class PresetLoadIn(BaseModel):
    """Body of ``POST /api/preset/load``."""

    model_config = ConfigDict(extra="forbid")

    path: str = ""
    module_code: str = ""


# ---------------------------------------------------------------------------
# application
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    """Build the Auralis FastAPI application."""
    application = FastAPI(
        title="Auralis Server",
        version="0.1.0-alpha",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    # The body contract runs inside the CORS middleware, so a refusal is
    # answered with the CORS headers a browser needs.
    application.add_middleware(BodyContractMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )

    @application.exception_handler(StarletteHTTPException)
    async def _http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return JSONResponse(status_code=exc.status_code, content={"error": detail})

    @application.exception_handler(RequestValidationError)
    async def _validation_exception(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        problems = []
        for item in exc.errors():
            where = ".".join(str(p) for p in item.get("loc", ()))
            problems.append(f"{where}: {item.get('msg', 'invalid value')}")
        return JSONResponse(
            status_code=422, content={"error": "; ".join(problems) or "invalid request"}
        )

    @application.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500, content={"error": f"internal server error: {type(exc).__name__}"}
        )

    # -- registry ------------------------------------------------------------
    @application.get("/api/modules")
    def list_modules() -> dict:
        """Every module the registry knows about."""
        try:
            _load_registry()
            modules = [info.to_dict() for info in registry.all()]
        except Exception as exc:
            log.exception("cannot read the module registry")
            raise HTTPException(
                status_code=500, detail=f"module registry unavailable: {exc}"
            ) from exc
        return {"modules": modules}

    # -- session -------------------------------------------------------------
    @application.get("/api/session/list")
    def list_sessions() -> dict:
        """The session documents that were saved."""
        sessions = []
        if SESSION_DIR.is_dir():
            for path in sorted(SESSION_DIR.glob("*.json")):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                sessions.append(
                    {
                        "name": path.stem,
                        "path": str(path),
                        "saved": data.get("saved", ""),
                        "steps": len((data.get("chain") or {}).get("steps", [])),
                    }
                )
        return {"sessions": sessions}

    @application.post("/api/session/save")
    def save_session(body: SessionIn) -> dict:
        """Write the session document of the current chain.

        The document is built from the chain the engine holds.  A body that
        carries a chain pushes it into the engine first, so a client can hand
        the state of its modules back to the server: the document then
        carries that state, and an undo that puts a module into the chain
        does not lose what the module learned.
        """
        name = (body.name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name is required")

        # The name becomes a file name, so it may not carry a path.
        safe = "".join(ch for ch in name if ch.isalnum() or ch in " -_").strip()
        if not safe:
            raise HTTPException(status_code=400, detail="the name has no usable characters")

        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        path = SESSION_DIR / f"{safe}.json"

        with _LOCK:
            engine = _get_engine()
            if body.chain is not None:
                _push_chain(engine, body.chain)
            try:
                document = save_document(
                    path,
                    engine=engine,
                    audio_ref=body.audio or None,
                    view_state=body.view or None,
                    markers=body.markers or None,
                    history=body.history,
                )
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(
                    status_code=400, detail=f"cannot write the session: {exc}"
                )

        return {"path": str(path), "document": document}

    @application.post("/api/session/load")
    def load_session(body: SessionIn) -> dict:
        """Read a session document and restore its chain.

        The chain of the document is restored into the engine; a body that
        carries a chain pushes that chain into the engine afterwards, so the
        state the client holds wins over the state of the file.
        """
        path = Path(body.path) if body.path else None

        if path is None and body.name:
            path = SESSION_DIR / f"{body.name}.json"

        if path is None or not path.is_file():
            raise HTTPException(status_code=404, detail="the session document does not exist")

        with _LOCK:
            engine = _get_engine()
            try:
                document = load_document(path, engine=engine)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(
                    status_code=400, detail=f"cannot read the session: {exc}"
                )

            if body.chain is not None:
                _push_chain(engine, body.chain)

            # The document's own chain block is returned under "chain", so
            # that the answer has the same shape as the document itself; the
            # live summary of the engine is under "summary".
            #
            # The report of the parameters says what the load did with them:
            # how many were applied, and which ones the module does not know
            # any more, which ones moved and which ones changed their
            # meaning.  A parameter that is unknown is reported and skipped,
            # never handed to a neighbour.
            return {
                "path": str(path),
                "document": document,
                "chain": document.get("chain"),
                "summary": engine.chain_summary(),
                "param_report": document_param_report(document),
            }

    # -- chain ---------------------------------------------------------------
    @application.get("/api/chain")
    def get_chain() -> dict:
        """The current chain, in signal order."""
        with _LOCK:
            return {"chain": _get_engine().chain_summary()}

    @application.post("/api/chain")
    def add_module(body: ChainAddIn) -> dict:
        """Append a module to the chain.

        A module that was taken out of the chain before comes back with the
        state it had: what it learned is not a parameter, so the server keeps
        it while the module is out of the chain.
        """
        code = (body.code or "").strip()
        if not code:
            raise HTTPException(status_code=400, detail="code is required")
        with _LOCK:
            engine = _get_engine()
            try:
                handle = engine.add_module(code)
            except KeyError:
                _load_registry()
                try:
                    handle = engine.add_module(code)
                except KeyError:
                    raise HTTPException(
                        status_code=404, detail=f"unknown module code: {code}"
                    )
            except Exception as exc:
                raise HTTPException(
                    status_code=400, detail=f"cannot create module {code}: {exc}"
                )
            state = _take_parked_state(code)
            if state:
                handle.module.load_state_base64(state)
            return {"chain": engine.chain_summary()}

    @application.delete("/api/chain/{index}")
    def remove_module(index: int) -> dict:
        """Remove a module from the chain.

        The state of the module is kept by the server, so an undo that puts
        the module back does not lose what it learned.
        """
        with _LOCK:
            engine = _get_engine()
            if not (0 <= index < len(engine.chain)):
                raise HTTPException(
                    status_code=404, detail=f"chain index {index} is out of range"
                )
            module = engine.chain[index].module
            _park_state(getattr(module, "code", ""), module.state_base64())
            engine.remove_module(index)
            return {"chain": engine.chain_summary()}

    @application.post("/api/chain/reorder")
    def reorder_chain(body: ReorderIn) -> dict:
        """Move a module to another position in the chain."""
        with _LOCK:
            engine = _get_engine()
            if not (0 <= body.src < len(engine.chain)):
                raise HTTPException(
                    status_code=404, detail=f"chain index {body.src} is out of range"
                )
            engine.reorder(body.src, body.dst)
            return {"chain": engine.chain_summary()}

    @application.post("/api/chain/{index}/bypass")
    def set_bypass(index: int, body: BypassIn) -> dict:
        """Bypass or engage a module."""
        with _LOCK:
            engine = _get_engine()
            if not (0 <= index < len(engine.chain)):
                raise HTTPException(
                    status_code=404, detail=f"chain index {index} is out of range"
                )
            engine.set_bypass(index, bool(body.bypass))
            return {"chain": engine.chain_summary()}

    @application.post("/api/chain/{index}/param")
    def set_param(index: int, body: ParamIn) -> dict:
        """Set one parameter of a module."""
        if not body.name:
            raise HTTPException(status_code=400, detail="name is required")
        with _LOCK:
            engine = _get_engine()
            if not (0 <= index < len(engine.chain)):
                raise HTTPException(
                    status_code=404, detail=f"chain index {index} is out of range"
                )
            try:
                engine.set_param(index, body.name, body.value)
            except KeyError as exc:
                raise HTTPException(
                    status_code=400, detail=f"unknown parameter: {body.name}"
                ) from exc
            value = engine.get_param(index, body.name)
            return {"ok": True, "name": body.name, "value": value}

    @application.post("/api/chain/{index}/state")
    def set_module_state(index: int, body: StateIn) -> dict:
        """Write the state of a module back into it.

        The state is the base64 blob of the module: the profile it learned,
        the region of the spectral editor, ...  The other chain routes cannot
        carry it, so an undo that puts a module back into the chain would
        lose that state.
        """
        with _LOCK:
            engine = _get_engine()
            if not (0 <= index < len(engine.chain)):
                raise HTTPException(
                    status_code=404, detail=f"chain index {index} is out of range"
                )
            text = (body.state or "").strip()
            if not text:
                raise HTTPException(status_code=400, detail="the state is empty")
            module = engine.chain[index].module
            module.load_state_base64(text)
            return {
                "ok": True,
                "index": index,
                "code": getattr(module, "code", ""),
                "state": module.state_base64(),
            }

    @application.get("/api/models")
    def model_library() -> dict:
        """The models the library holds, and the model that runs.

        The listing reads the manifests of the model files without reading a
        single weight, so it is cheap.  A file that cannot be read is still
        listed: the entry carries the reason.

        The second half of the answer is the model that **runs** for every
        module of the chain.  It is read out of the module that runs, never
        out of "the first model of the library": a control that shows the
        first entry shows a model that does not run.
        """
        try:
            library = core_models.open_library()
            entries = [info.to_dict() for info in library.list()]
        except Exception as exc:  # noqa: BLE001 - a broken library is an answer
            entries = []
            log.warning("could not list the model library: %s", exc)
        with _LOCK:
            running = _running_models(_get_engine())
        return {"models": entries, "running": running}

    @application.post("/api/chain/{index}/model")
    def select_module_model(index: int, body: ModelIn) -> dict:
        """Choose the model a module runs.

        The name is the name the model publishes.  An empty name takes the
        model away and the module runs its hand written path again.

        The answer carries the model that runs now and the message that says
        why there is no model: a model that is not in the library, a model
        whose manifest does not match the engine, or the real time path,
        where a model is never loaded.
        """
        with _LOCK:
            engine = _get_engine()
            if not (0 <= index < len(engine.chain)):
                raise HTTPException(
                    status_code=404, detail=f"chain index {index} is out of range"
                )
            module = engine.chain[index].module
            selector = getattr(module, "select_model", None)
            if not callable(selector):
                raise HTTPException(
                    status_code=400,
                    detail=f"the module at index {index} runs no model",
                )
            message = selector(body.model)
            return {
                "ok": True,
                "index": index,
                "code": getattr(module, "code", ""),
                "model": getattr(module, "model_name", ""),
                "message": str(message or ""),
            }

    @application.post("/api/chain/{index}/region")
    def set_region(index: int, body: RegionIn) -> dict:
        """Hand the region (the spectral edit area) to a module."""
        with _LOCK:
            engine = _get_engine()
            if not (0 <= index < len(engine.chain)):
                raise HTTPException(
                    status_code=404, detail=f"chain index {index} is out of range"
                )
            module = engine.chain[index].module
            setter = getattr(module, "set_region", None)
            if not callable(setter):
                raise HTTPException(
                    status_code=400,
                    detail=f"the module at index {index} does not take a region",
                )
            values = (body.start, body.end, body.low, body.high)
            if not all(math.isfinite(float(value)) for value in values):
                raise HTTPException(
                    status_code=400, detail="the region needs finite numbers"
                )
            duration = 0.0
            try:
                duration = float(_current_asset().duration)
            except Exception:  # noqa: BLE001 - no audio is not an error
                duration = 0.0
            if duration > 0.0 and not (0.0 <= body.start and body.end <= duration):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"the region lies outside the audio "
                        f"(0.000-{duration:.3f} s)"
                    ),
                )
            setter(*values)
            region = module.get_region() if hasattr(module, "get_region") else None
            try:
                length = float(engine.get_param(index, "Region Length"))
            except Exception:  # noqa: BLE001 - not every module has the parameter
                length = 0.0
            return {
                "ok": True,
                "index": index,
                "code": module.code,
                "region": region,
                "region_length": length,
            }

    @application.get("/api/chain/{index}/params")
    def get_params(index: int) -> dict:
        """Parameter descriptions and current values of one module."""
        with _LOCK:
            engine = _get_engine()
            if not (0 <= index < len(engine.chain)):
                raise HTTPException(
                    status_code=404, detail=f"chain index {index} is out of range"
                )
            descs = engine.param_info(index)
            values = {}
            for desc in descs:
                try:
                    values[desc.name] = engine.get_param(index, desc.name)
                except Exception:
                    continue
            return {
                "params": [d.to_dict() for d in descs],
                "values": values,
                "index": index,
                "code": engine.chain[index].module.code,
            }

    @application.post("/api/chain/{index}/learn")
    def learn(index: int) -> dict:
        """Capture a profile for a module from the loaded audio."""
        with _LOCK:
            engine = _get_engine()
            if not (0 <= index < len(engine.chain)):
                raise HTTPException(
                    status_code=404, detail=f"chain index {index} is out of range"
                )
            asset = _current_asset()
            if asset.data is None:
                raise HTTPException(status_code=400, detail="no audio is loaded")
            try:
                message = engine.learn(index, asset.data)
            except NotImplementedError as exc:
                raise HTTPException(
                    status_code=400, detail=f"this module cannot learn: {exc}"
                ) from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(
                    status_code=500, detail=f"learn failed: {type(exc).__name__}"
                ) from exc
            return {"ok": True, "message": message}

    # -- audio ---------------------------------------------------------------
    @application.post("/api/upload")
    async def upload(request: Request) -> dict:
        """Accept an audio file and hand back an asset id.

        The body is a ``multipart/form-data`` document with one part named
        ``file``.  The body is written to disk in blocks, so an upload never
        has to be in memory, and the asset store decides what is accepted:
        the name, the size and the format of the file.
        """
        content_type = request.headers.get("content-type", "")
        scratch = VAR / "uploads"
        scratch.mkdir(parents=True, exist_ok=True)
        staging = scratch / f"{time.time_ns()}.upload"
        try:
            name, _size = await _receive_upload(request, content_type, staging)
            try:
                asset_id = STORE.accept(staging, name, prefix="up")
            except UnsafeName as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except AssetTooLarge as exc:
                raise HTTPException(status_code=413, detail=str(exc)) from exc
            except UnsupportedFormat as exc:
                raise HTTPException(status_code=415, detail=str(exc)) from exc
            except UnreadableAudio as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            try:
                staging.unlink()
            except OSError:
                pass

        asset = STORE.get(asset_id)
        _ensure_engine(asset.sample_rate, asset.channels)
        _set_current(asset_id)
        return {
            "asset_id": asset_id,
            "sample_rate": int(asset.sample_rate),
            "channels": int(asset.channels),
            "frames": int(asset.frames),
            "duration": asset.duration,
        }

    @application.post("/api/analyze")
    def analyze(body: AnalyzeIn) -> dict:
        """Waveform envelope and spectrogram for one asset."""
        asset = _get_asset(body.asset_id)
        if asset.data is None:
            raise HTTPException(status_code=400, detail="the asset has no samples")
        samples = _slice(asset.data, asset.sample_rate, body.start, body.end)
        try:
            payload = au.analyse(samples, asset.sample_rate)
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"analysis failed: {type(exc).__name__}"
            ) from exc
        payload["asset_id"] = asset.asset_id
        return payload

    @application.post("/api/analysis")
    def analysis(body: AnalysisIn) -> dict:
        """Measurement payload of one asset.

        The payload is the one the analysis panel renders: the magnitude
        spectrum, the waveform statistics, the loudness figures, the inter
        channel correlation and the peak histogram.  ``start`` and ``end``
        bound the measured range in seconds; an end of zero (or less) means
        "until the end of the asset".

        The error contract is the one of ``/api/analyze``: a request that
        names no audio is a 400, one that names an unknown asset a 404.
        """
        asset = _get_asset(body.asset_id)
        if asset.data is None:
            raise HTTPException(status_code=400, detail="the asset has no samples")
        start, end = _analysis_range(body.start, body.end, asset.duration)
        try:
            payload = core_analysis.analyze(
                asset.data,
                asset.sample_rate,
                start=start,
                end=end,
                channels=asset.channels,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"analysis failed: {type(exc).__name__}"
            ) from exc
        payload["asset_id"] = asset.asset_id
        return payload

    @application.get("/api/assets")
    def list_assets() -> dict:
        """Every asset the store holds.

        The batch panel needs the list to offer the files that are already
        loaded: every record carries the path the batch runner reads the
        file from.
        """
        current = _current_asset_id()
        assets = []
        for summary in STORE.list():
            record = dict(summary)
            asset_id = str(record.get("asset_id", ""))
            record["path"] = _asset_path(asset_id)
            record["current"] = bool(asset_id and asset_id == current)
            assets.append(record)
        return {"assets": assets, "current_asset_id": current}

    @application.post("/api/render")
    def render(body: RenderIn) -> dict:
        """Run a chain over an asset and store the result as a new asset."""
        asset = _get_asset(body.asset_id)
        if asset.data is None:
            raise HTTPException(status_code=400, detail="the asset has no samples")
        samples = _slice(asset.data, asset.sample_rate, body.start, body.end)
        if body.chain is None:
            engine = _ensure_engine(asset.sample_rate, asset.channels)
        else:
            engine = _build_engine(body.chain, asset.sample_rate, asset.channels)
        # The mode of the path: a file render has no deadline, so every
        # module of the chain works in the best configuration it has, and
        # declares the look ahead that costs.  The rule is in
        # aurserver/audio.py (PATH_PROCESSING_MODE); the preview asks the
        # same table for its mode and gets "realtime".
        au.apply_processing_mode(engine, au.PATH_FILE)
        try:
            rendered, warnings = _render(engine, samples)
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"render failed: {type(exc).__name__}: {exc}"
            ) from exc
        asset_id = STORE.put(
            rendered,
            asset.sample_rate,
            asset.channels,
            f"render of {asset.name}",
            "wav",
            prefix="out",
        )
        _set_current(asset_id)
        response = {"asset_id": asset_id, "url": f"/api/audio/{asset_id}"}
        if warnings:
            response["warnings"] = warnings
        # The models that ran, and the reason there is no model: a render
        # that fell back to the hand written path says so.
        running = _running_models(engine)
        if body.learned:
            # The learned restoration runs after the chain, over the whole
            # file: the chain is the hand written restoration and the model
            # refines what it produced.
            rendered, described = _learned_restoration(
                rendered, body.learned, asset.sample_rate
            )
            asset_id = STORE.put(
                rendered,
                asset.sample_rate,
                asset.channels,
                f"render of {asset.name}",
                "wav",
                prefix="out",
            )
            _set_current(asset_id)
            response["asset_id"] = asset_id
            response["url"] = f"/api/audio/{asset_id}"
            running.append(
                {
                    "index": -1,
                    "code": "AUR_LEARNED",
                    "model": described,
                    "message": "",
                }
            )
        response["models"] = running
        return response

    @application.get("/api/audio/{asset_id}")
    def audio(asset_id: str) -> Response:
        """Serve the WAV file of an asset."""
        try:
            path = STORE.path(asset_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"unknown asset: {asset_id}")
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"unknown asset: {asset_id}")
        return FileResponse(path, media_type="audio/wav", filename=f"{asset_id}.wav")

    @application.post("/api/compare")
    def compare(body: CompareIn) -> dict:
        """Render the same audio through two chains and return both URLs."""
        asset = _get_asset(body.asset_id)
        if asset.data is None:
            raise HTTPException(status_code=400, detail="the asset has no samples")
        samples = _slice(asset.data, asset.sample_rate, body.start, body.end)
        engine_a = (
            _ensure_engine(asset.sample_rate, asset.channels)
            if body.chain_a is None
            else _build_engine(body.chain_a, asset.sample_rate, asset.channels)
        )
        engine_b = (
            _ensure_engine(asset.sample_rate, asset.channels)
            if body.chain_b is None
            else _build_engine(body.chain_b, asset.sample_rate, asset.channels)
        )
        # Both sides of the comparison are file renders: the mode is the
        # mode of the path, not of the chain.
        au.apply_processing_mode(engine_a, au.PATH_FILE)
        au.apply_processing_mode(engine_b, au.PATH_FILE)
        try:
            out_a, warn_a = _render(engine_a, samples)
            out_b, warn_b = _render(engine_b, samples)
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"render failed: {type(exc).__name__}: {exc}"
            ) from exc
        id_a = STORE.put(
            out_a, asset.sample_rate, asset.channels, f"compare a of {asset.name}", "wav",
            prefix="out", current=False,
        )
        id_b = STORE.put(
            out_b, asset.sample_rate, asset.channels, f"compare b of {asset.name}", "wav",
            prefix="out", current=False,
        )
        response = {
            "a": f"/api/audio/{id_a}",
            "b": f"/api/audio/{id_b}",
            "asset_ids": {"a": id_a, "b": id_b},
        }
        warnings = warn_a + warn_b
        if warnings:
            response["warnings"] = warnings
        return response

    # -- health --------------------------------------------------------------
    @application.get("/api/health")
    def health() -> dict:
        """Liveness probe."""
        return {"ok": True}

    # -- presets -------------------------------------------------------------
    # The preview resolver reads the asset store from the application state,
    # so it is published before the sub-services are mounted.
    application.state.assets = STORE

    # The preset library, the streaming preview, the batch processor and the
    # delivery catalogue live in their own modules with their own route
    # tables.  They are mounted on this very application instance, so there
    # is exactly one implementation of each of their routes.
    #
    # Each module is mounted on its own: one that cannot be imported must
    # not take the others down with it.
    for _name in ("presets", "ws", "batch", "batch_formats"):
        try:
            _module = importlib.import_module(f".{_name}", package=__package__)
            _module.register(application)
        except Exception as exc:  # pragma: no cover - optional sub-services
            log.warning("cannot register the %s service: %s", _name, exc)

    return application


def json_dumps(document: Any) -> str:
    """Serialise a document for the preset directory."""
    return json.dumps(document, indent=2, ensure_ascii=False)


def _resolve_inside(root: Path, candidate: str) -> Path:
    """Resolve ``candidate`` and refuse anything outside ``root``."""
    root = root.resolve()
    path = Path(candidate)
    if not path.is_absolute():
        path = root / path
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    try:
        resolved.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="path is outside the preset directory")
    return resolved


# ---------------------------------------------------------------------------
# static user interface
# ---------------------------------------------------------------------------

app = create_app()

for _mount, _folder in (("/css", AURUI / "css"), ("/js", AURUI / "js")):
    try:
        app.mount(
            _mount,
            StaticFiles(directory=str(_folder), check_dir=False),
            name=_mount.strip("/"),
        )
    except Exception:  # pragma: no cover - the UI folder may not exist yet
        log.warning("cannot mount %s from %s", _mount, _folder)


@app.get("/", include_in_schema=False)
def index() -> Response:
    """The single-page application shell."""
    page = AURUI / "index.html"
    if not page.is_file():
        raise HTTPException(status_code=404, detail="the user interface is not installed")
    return FileResponse(page, media_type="text/html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    """Optional icon; 404 when the project does not ship one."""
    icon = AURUI / "favicon.ico"
    if not icon.is_file():
        raise HTTPException(status_code=404, detail="no icon")
    return FileResponse(icon, media_type="image/x-icon")
