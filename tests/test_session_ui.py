"""Tests for the session controller of the web interface.

The module under test is ``aurui/js/session.js``: the undo / redo history and
the session document of the workbench.

Two kinds of checks live here.

*The controller against a stub.*  The JavaScript is imported with node and
driven with a stub of the api client, so the behaviour of the history is
checked without a browser: the folding of a knob drag into one step, the
undo of a module that moved, the undo of every action the interface can
perform, and the toolbar.

*The controller against the real server.*  A uvicorn server is started in
this process, the real api client (``aurui/js/api.js``) talks to it over
HTTP and the controller saves a session, breaks it and loads it back.  The
round trip is then measured value by value: the chain, the parameter values,
the bypass states, the module state, the view state and the markers.

When node is not available the behavioural checks are skipped; the purely
static ones always run.
"""

from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

# --------------------------------------------------------------------------
# Locations.
# --------------------------------------------------------------------------

REPO = Path(__file__).resolve().parents[1]
SESSION_JS = REPO / "aurui" / "js" / "session.js"
API_JS = REPO / "aurui" / "js" / "api.js"
TOKENS_CSS = REPO / "aurui" / "css" / "tokens.css"
SESSIONS_DIR = REPO / "var" / "sessions"

#: The names the controller has to offer to the shell.
REQUIRED_API = (
    "undo",
    "redo",
    "canUndo",
    "canRedo",
    "save",
    "load",
    "open",
    "newSession",
    "historyLabels",
    "destroy",
)

#: The members that have to be callable.
REQUIRED_METHODS = ("undo", "redo", "save", "load", "open", "newSession", "destroy")

#: The name of the session the live test writes.
SESSION_NAME = "session-ui-test"


# --------------------------------------------------------------------------
# Helpers.
# --------------------------------------------------------------------------


def _read(path: Path) -> str:
    """Read a text file, tolerating a missing file."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _node_json(script: str) -> object:
    """Run an ES module with node and return what it printed."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available on this machine")
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        timeout=120,
        stdin=subprocess.DEVNULL,
    )
    if proc.returncode != 0:
        pytest.fail(f"node script failed:\n{proc.stderr.strip()}")
    try:
        return json.loads(proc.stdout or "null")
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        pytest.fail(f"node printed no JSON ({exc}):\n{proc.stdout[:400]}")


def _session_json(script: str) -> dict:
    """Run a script against the session module and return its report."""
    data = _node_json(
        script.replace("__URL__", SESSION_JS.as_uri()).replace("__API__", API_JS.as_uri())
    )
    assert isinstance(data, dict), "the session script printed no report"
    return data


def _probe_wav_path() -> str:
    """Write a short recording with a hum and return its path.

    The live test uploads it, so that a module can learn a profile and the
    state of that module is not the state of a fresh one.  The file lives in
    the scratch directory of the repository.
    """
    import io
    import math
    import struct
    import wave

    rate = 48000
    frames = int(rate * 0.5)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        samples = bytearray()
        for index in range(frames):
            t = index / rate
            value = int(12000.0 * math.sin(2.0 * math.pi * 50.0 * t))
            value += int(4000.0 * math.sin(2.0 * math.pi * 1000.0 * t))
            for _ in range(2):
                samples += struct.pack("<h", value)
        handle.writeframes(bytes(samples))

    scratch = REPO / "var"
    scratch.mkdir(parents=True, exist_ok=True)
    target = scratch / "session_probe.wav"
    target.write_bytes(buffer.getvalue())
    return target.relative_to(REPO).as_posix()


# --------------------------------------------------------------------------
# The controller, driven with a stub of the api client.
# --------------------------------------------------------------------------

#: The stub of the api client and the drive of the controller.
CONTROLLER_SCRIPT = r"""
const mod = await import("__URL__");

const NAMES = { AUR_DECLICK: "De-Click", AUR_DEHUM: "De-Hum", AUR_DEESS: "De-Ess" };

function makeStubApi() {
  const calls = [];
  let chain = [];
  let stored = null;
  const snapshot = () => chain.map((step) => ({ ...step, params: { ...step.params } }));
  return {
    calls,
    stored: () => stored,
    state: () => snapshot(),
    async addModule(code) {
      calls.push({ op: "addModule", code });
      chain.push({
        code,
        display_name: NAMES[code] || code,
        category: "Repair",
        edition: "standard",
        bypass: false,
        params: { Sensitivity: { Type: "Float", Value: 0.5 } },
        state: "",
      });
      return { chain: snapshot() };
    },
    async removeModule(index) {
      calls.push({ op: "removeModule", index });
      chain.splice(index, 1);
      return { chain: snapshot() };
    },
    async reorder(from, to) {
      calls.push({ op: "reorder", from, to });
      const moved = chain.splice(from, 1)[0];
      chain.splice(to, 0, moved);
      return { chain: snapshot() };
    },
    async setBypass(index, bypass) {
      calls.push({ op: "setBypass", index, bypass });
      chain[index].bypass = Boolean(bypass);
      return { chain: snapshot() };
    },
    async setParam(index, name, value) {
      calls.push({ op: "setParam", index, name, value });
      chain[index].params[name] = { Type: "Float", Value: value };
      return { ok: true, name, value };
    },
    async chain() {
      return { chain: snapshot() };
    },
    async params(index) {
      const out = {};
      for (const [name, node] of Object.entries(chain[index].params)) out[name] = node.Value;
      return { values: out };
    },
    async saveSession(document) {
      calls.push({ op: "saveSession", format: document.format });
      stored = document;
      return { path: "session.json", document };
    },
    async loadSession(id) {
      calls.push({ op: "loadSession", id });
      if (!stored) return null;
      return { path: "session.json", document: stored, summary: null };
    },
    async sessions() {
      return stored ? [{ id: "session.json", name: "session" }] : [];
    },
  };
}

const api = makeStubApi();
let clock = 10000;
const changes = [];
const controller = mod.createSessionController({
  api,
  onChange: (state) => changes.push({
    canUndo: state.canUndo,
    canRedo: state.canRedo,
    dirty: state.dirty,
    labels: state.labels.slice(),
  }),
  now: () => clock,
  viewState: () => ({
    zoom: 2.5,
    scroll: 1.5,
    frequencyRange: [100, 8000],
    selection: { t0: 0.5, t1: 2, f0: 80, f1: 400 },
    viewMode: "waveform",
    spectrogram: false,
  }),
  audio: { path: "work/interview.wav", sample_rate: 48000, channels: 2, frames: 123456 },
});

const report = { api: null, changes: null, apiCalls: null };

/* -- the public API ----------------------------------------------------- */
report.api = {
  members: Object.keys(controller).sort(),
  types: Object.fromEntries(Object.keys(controller).map((key) => [key, typeof controller[key]])),
  canUndo: controller.canUndo,
  canRedo: controller.canRedo,
  labels: controller.historyLabels.slice(),
  mergeWindowMs: controller.mergeWindowMs,
  lastLabel: controller.lastLabel,
  dirty: controller.dirty,
  transport: controller.transportName,
};

/* -- the chain is built on the server ----------------------------------- */
await controller.addModule("AUR_DECLICK");
report.labelsAfterAdd = controller.historyLabels.slice();

/* -- a knob drag: three frames inside the merge window ------------------ */
await controller.setParam(0, "Sensitivity", 1);
clock += 120;
await controller.setParam(0, "Sensitivity", 2);
clock += 120;
await controller.setParam(0, "Sensitivity", 3);
report.labelsAfterDrag = controller.historyLabels.slice();
report.valueAfterDrag = api.state()[0].params.Sensitivity.Value;
report.callsAfterDrag = api.calls.filter((call) => call.op === "setParam").length;

report.undoneDrag = await controller.undo();
report.valueAfterUndoDrag = api.state()[0].params.Sensitivity.Value;
report.labelsAfterUndoDrag = controller.historyLabels.slice();
report.canUndoAfterUndo = controller.canUndo;
report.canRedoAfterUndo = controller.canRedo;

report.redoneDrag = await controller.redo();
report.valueAfterRedoDrag = api.state()[0].params.Sensitivity.Value;
report.labelsAfterRedo = controller.historyLabels.slice();

/* -- a change outside the merge window is a step of its own ------------- */
clock += 2000;
await controller.setParam(0, "Sensitivity", 4);
report.labelsAfterWindow = controller.historyLabels.slice();
report.valueAfterWindow = api.state()[0].params.Sensitivity.Value;

report.undoneWindow = await controller.undo();
report.valueAfterUndoWindow = api.state()[0].params.Sensitivity.Value;
report.canRedoAfterUndo = controller.canRedo;
await controller.redo();

/* -- the document ------------------------------------------------------- */
const document_ = controller.buildDocument();
report.document = {
  format: document_.format,
  version: document_.version,
  audio: document_.audio,
  view: document_.view,
  markers: document_.markers,
  history: document_.history,
  chain: {
    format: document_.chain.format,
    version: document_.chain.version,
    steps: document_.chain.steps,
  },
};

/* -- a module that is switched out -------------------------------------- */
await controller.setBypass(0, true);
report.labelsAfterBypass = controller.historyLabels.slice();
report.bypassedStep = controller.buildDocument().chain.steps[0];

/* -- the mirror the shell paints from ----------------------------------- */
report.mirror = controller.chain.map((step) => ({
  code: step.code,
  display_name: step.display_name,
  bypass: step.bypass,
  values: step.values,
}));

/* -- save and load ------------------------------------------------------ */
const saved = await controller.save();
report.saved = { format: saved.format, dirty: controller.dirty };
report.saveCalls = api.calls.filter((call) => call.op === "saveSession").length;

const loaded = await controller.load(saved);
report.loaded = {
  format: loaded.format,
  chain: api.state().map((step) => ({ code: step.code, bypass: step.bypass, params: step.params })),
  canUndo: controller.canUndo,
  canRedo: controller.canRedo,
  dirty: controller.dirty,
  labels: controller.historyLabels.slice(),
};
report.undoneLoad = await controller.undo();
report.chainAfterUndoneLoad = api.state().map((step) => step.code);

await controller.removeModule(0);
report.labelsAfterRemove = controller.historyLabels.slice();
report.valueAfterRemove = api.state().length;

/* -- emptying the chain is one step ------------------------------------- */
await controller.addModule("AUR_DECLICK");
await controller.addModule("AUR_DEHUM");
const beforeClear = controller.historyLabels.length;
await controller.clearChain();
report.afterClear = {
  chain: api.state().length,
  labels: controller.historyLabels.slice(),
  steps: controller.historyLabels.length - beforeClear,
};
await controller.undo();
report.afterClearUndo = {
  chain: api.state().map((step) => step.code),
  labels: controller.historyLabels.slice(),
  redoLabels: controller.redoLabels.slice(),
};

/* -- a new session ------------------------------------------------------ */
await controller.newSession();
report.afterNewSession = {
  chain: api.state().length,
  canUndo: controller.canUndo,
  canRedo: controller.canRedo,
  dirty: controller.dirty,
  labels: controller.historyLabels.slice(),
};
report.undoneNewSession = await controller.undo();
report.afterUndoneNewSession = {
  chain: api.state().map((step) => step.code),
  labels: controller.historyLabels.slice(),
};

/* -- a module that moved: the history addresses the code --------------- */
await controller.newSession();
await controller.addModule("AUR_DEHUM");
await controller.addModule("AUR_DECLICK");
await controller.addModule("AUR_DEESS");
const movedBefore = api.state().map((step) => step.code);
await controller.addModule("AUR_DEESS");
await controller.removeModule(1);
const movedAfterRemove = api.state().map((step) => step.code);
await controller.undo();
const movedAfterUndoRemove = api.state().map((step) => step.code);
await controller.undo();
const movedAfterUndoAdd = api.state().map((step) => step.code);
report.moved = {
  before: movedBefore,
  afterRemove: movedAfterRemove,
  afterUndoRemove: movedAfterUndoRemove,
  afterUndoAdd: movedAfterUndoAdd,
  labels: controller.historyLabels.slice(),
};

report.changes = changes;
report.apiCalls = api.calls.map((call) => call.op);
process.stdout.write(JSON.stringify(report));
"""


#: The undo coverage: every action of the interface, one step or none.
UNDO_COVERAGE_SCRIPT = r"""
const mod = await import("__URL__");

const NAMES = { AUR_DECLICK: "De-Click", AUR_DEHUM: "De-Hum", AUR_DEESS: "De-Ess" };

function makeStubApi() {
  const calls = [];
  let chain = [];
  let stored = null;
  const snapshot = () => chain.map((step) => ({ ...step, params: { ...step.params } }));
  return {
    calls,
    state: () => snapshot(),
    async addModule(code) {
      calls.push({ op: "addModule", code });
      chain.push({
        code,
        display_name: NAMES[code] || code,
        category: "Repair",
        edition: "standard",
        bypass: false,
        params: { Sensitivity: { Type: "Float", Value: 0.5 } },
        state: "",
      });
      return { chain: snapshot() };
    },
    async removeModule(index) {
      calls.push({ op: "removeModule", index });
      chain.splice(index, 1);
      return { chain: snapshot() };
    },
    async reorder(from, to) {
      calls.push({ op: "reorder", from, to });
      const moved = chain.splice(from, 1)[0];
      chain.splice(to, 0, moved);
      return { chain: snapshot() };
    },
    async setBypass(index, bypass) {
      calls.push({ op: "setBypass", index, bypass });
      chain[index].bypass = Boolean(bypass);
      return { chain: snapshot() };
    },
    async setParam(index, name, value) {
      calls.push({ op: "setParam", index, name, value });
      chain[index].params[name] = { Type: "Float", Value: value };
      return { ok: true, name, value };
    },
    async chain() {
      return { chain: snapshot() };
    },
    async params(index) {
      const out = {};
      for (const [name, node] of Object.entries(chain[index].params)) out[name] = node.Value;
      return { values: out };
    },
    async saveSession(document) {
      calls.push({ op: "saveSession" });
      stored = document;
      return { path: "session.json", document };
    },
    async loadSession(id) {
      calls.push({ op: "loadSession", id });
      if (!stored) return null;
      return { path: "session.json", document: stored, summary: null };
    },
    async sessions() {
      return stored ? [{ id: "session.json", name: "session" }] : [];
    },
  };
}

const report = { actions: [] };

function picture(api) {
  return api.state().map((step) => ({
    code: step.code,
    bypass: step.bypass,
    params: step.params,
  }));
}

async function caseOf(name, setup, action) {
  const api = makeStubApi();
  const controller = mod.createSessionController({ api, now: () => 10000 });
  if (setup) await setup(controller, api);
  const before = picture(api);
  const beforeSteps = controller.historyLabels.length;
  await action(controller, api);
  const steps = controller.historyLabels.length - beforeSteps;
  const label = controller.historyLabels[controller.historyLabels.length - 1] || "";
  let restored = null;
  let restoredOk = null;
  if (steps > 0) {
    await controller.undo();
    restored = picture(api);
    restoredOk = JSON.stringify(before) === JSON.stringify(restored);
  }
  report.actions.push({
    name,
    steps,
    label,
    before,
    after: picture(api),
    restored,
    restoredOk,
  });
}

const addOne = async (controller) => { await controller.addModule("AUR_DECLICK"); };
const addTwo = async (controller) => {
  await controller.addModule("AUR_DECLICK");
  await controller.addModule("AUR_DEHUM");
};
const addThree = async (controller) => {
  await controller.addModule("AUR_DECLICK");
  await controller.addModule("AUR_DEHUM");
  await controller.addModule("AUR_DEESS");
};

await caseOf("add a module", null, async (controller) => {
  await controller.addModule("AUR_DECLICK");
});
await caseOf("remove a module", addTwo, async (controller) => {
  await controller.removeModule(0);
});
await caseOf("move a module", addThree, async (controller) => {
  await controller.reorder(0, 2);
});
await caseOf("switch a module out", addOne, async (controller) => {
  await controller.setBypass(0, true);
});
await caseOf("turn a knob", addOne, async (controller) => {
  await controller.setParam(0, "Sensitivity", 1);
  await controller.setParam(0, "Sensitivity", 2);
  await controller.setParam(0, "Sensitivity", 3);
});
await caseOf("empty the chain", addTwo, async (controller) => {
  await controller.clearChain();
});
await caseOf("start a new session", addOne, async (controller) => {
  await controller.newSession();
});
await caseOf("open a session", async (controller) => {
  await controller.addModule("AUR_DECLICK");
  await controller.save();
}, async (controller) => {
  await controller.load(controller.buildDocument());
});
await caseOf("save the session", addOne, async (controller) => {
  await controller.save();
});

process.stdout.write(JSON.stringify(report));
"""


#: The toolbar, driven with a tiny stand-in for the document.
TOOLBAR_SCRIPT = r"""
const mod = await import("__URL__");

class StubElement {
  constructor(doc, tag) {
    this.ownerDocument = doc;
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.attributes = {};
    this.className = "";
    this.textContent = "";
    this.disabled = false;
    this.parentNode = null;
    this.listeners = {};
    const self = this;
    this.classList = {
      add: (name) => { if (!self.classList.contains(name)) self.className =
        (self.className + " " + name).trim(); },
      remove: (name) => { self.className = self.className.split(/\s+/)
        .filter((part) => part && part !== name).join(" "); },
      toggle: (name, on) => {
        const wanted = on === undefined ? !self.classList.contains(name) : Boolean(on);
        if (wanted) self.classList.add(name);
        else self.classList.remove(name);
      },
      contains: (name) => self.className.split(/\s+/).indexOf(name) >= 0,
    };
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "id") this.ownerDocument.byId[String(value)] = this;
  }
  getAttribute(name) {
    return name in this.attributes ? this.attributes[name] : null;
  }
  appendChild(child) {
    if (child.parentNode) child.parentNode.removeChild(child);
    child.parentNode = this;
    this.children.push(child);
    return child;
  }
  append(...nodes) {
    for (const node of nodes) this.appendChild(node);
  }
  removeChild(child) {
    const at = this.children.indexOf(child);
    if (at >= 0) this.children.splice(at, 1);
    child.parentNode = null;
    return child;
  }
  replaceChildren(...nodes) {
    while (this.children.length) this.removeChild(this.children[0]);
    this.append(...nodes);
  }
  get firstChild() {
    return this.children[0] || null;
  }
  addEventListener(type, handler) {
    (this.listeners[type] = this.listeners[type] || []).push(handler);
  }
  dispatch(type) {
    for (const handler of this.listeners[type] || []) handler({ type });
  }
}

function makeDocument() {
  const doc = {
    byId: {},
    createElement(tag) { return new StubElement(doc, tag); },
    getElementById(id) { return doc.byId[id] || null; },
  };
  doc.head = new StubElement(doc, "head");
  doc.body = new StubElement(doc, "body");
  return doc;
}

const chain = [];
const api = {
  async addModule(code) {
    chain.push({ code, display_name: "De-Click", bypass: false, params: {} });
    return { chain: chain.map((step) => ({ ...step })) };
  },
  async removeModule(index) {
    chain.splice(index, 1);
    return { chain: chain.map((step) => ({ ...step })) };
  },
  async reorder(from, to) {
    const moved = chain.splice(from, 1)[0];
    chain.splice(to, 0, moved);
    return { chain: chain.map((step) => ({ ...step })) };
  },
  async setBypass(index, bypass) {
    chain[index].bypass = Boolean(bypass);
    return { chain: chain.map((step) => ({ ...step })) };
  },
  async setParam(index, name, value) {
    chain[index].params[name] = { Type: "Float", Value: value };
    return { ok: true, name, value };
  },
  async saveSession(document) {
    return { path: "session.json", document };
  },
  async loadSession() {
    throw new Error("there is no session to open");
  },
};

const errors = [];
const controller = mod.createSessionController({ api,
  onError: (err) => errors.push(String(err && err.message ? err.message : err)) });
const doc = makeDocument();
const container = new StubElement(doc, "div");
const bar = mod.renderSessionBar(container, controller);

const report = {
  style: Boolean(doc.getElementById("aur-ui-session-style")),
  buttons: Object.keys(bar.buttons).sort(),
  classes: bar.element.className,
};

function read() {
  return {
    undo: bar.buttons.undo.disabled,
    redo: bar.buttons.redo.disabled,
    label: bar.label.textContent,
    hint: bar.hint.textContent,
    undoTitle: bar.buttons.undo.getAttribute("title"),
    redoTitle: bar.buttons.redo.getAttribute("title"),
    dirty: bar.dirty.classList.contains("ss-dirty-on"),
    undoLabel: bar.undoLabel,
    redoLabel: bar.redoLabel,
  };
}

report.initial = read();

await controller.addModule("AUR_DECLICK");
report.afterAction = read();

await controller.undo();
report.afterUndo = read();

bar.buttons.undo.dispatch("click");
await new Promise((resolve) => setTimeout(resolve, 20));
report.afterClick = read();

await controller.save();
report.afterSave = read();

report.dirtyBeforeSave = controller.dirty;

/* a failing action is reported, it does not escape the button */
bar.buttons.open.dispatch("click");
await new Promise((resolve) => setTimeout(resolve, 20));
report.errors = errors;

report.destroyed = bar.destroy();
report.containerChildren = container.children.length;

process.stdout.write(JSON.stringify(report));
"""


# --------------------------------------------------------------------------
# The controller, driven against the real server.
# --------------------------------------------------------------------------

#: The live script: the round trip and the undo of a module that moved.
LIVE_SCRIPT = r"""
const BASE = "__BASE__";
const NAME = "__NAME__";

const realFetch = globalThis.fetch;
globalThis.fetch = (input, init) => {
  const url = typeof input === "string" ? input : String(input);
  return realFetch(url.startsWith("http") ? url : BASE + url, init);
};

const api = (await import("__API__")).api;
const mod = await import("__URL__");

let clock = 1000;
const view = {
  zoom: 1.0,
  scroll: 0.0,
  frequency_range: [0, 22050],
  selection: null,
  view_mode: "composite",
  spectrogram: true,
};
const markers = [];
const restores = [];

const controller = mod.createSessionController({
  api,
  now: () => clock,
  viewState: () => view,
  markers: () => markers,
  audio: { path: "work/interview.wav", sample_rate: 48000, channels: 2, frames: 123456 },
  onRestore: (payload) => {
    restores.push({ view: payload.view, markers: payload.markers });
    for (const key of Object.keys(view)) delete view[key];
    Object.assign(view, payload.view || {});
    markers.length = 0;
    for (const marker of payload.markers || []) markers.push(marker);
    // the shell shows what the document carried and hands it back
    controller.setViewState(view);
    controller.setMarkers(markers);
  },
});

const report = { moved: {}, roundTrip: {}, transport: controller.transportName };

/* -- a recording to learn from ----------------------------------------- */
const { readFileSync } = await import("node:fs");
const wav = new Uint8Array(readFileSync("__WAV__"));
const uploaded = await api.upload(new File([wav], "probe.wav", { type: "audio/wav" }));
report.uploaded = { frames: uploaded.frames, rate: uploaded.sample_rate,
  channels: uploaded.channels };

/* -- a module that moved: the history addresses the code --------------- */
await controller.newSession();
await controller.addModule("AUR_DEHUM");
await controller.addModule("AUR_DECLICK");
await controller.addModule("AUR_DEESS");
const movedBefore = (await api.chain()).chain.map((step) => step.code);
await controller.addModule("AUR_DEBLEED");
await controller.removeModule(1);
const movedAfterRemove = (await api.chain()).chain.map((step) => step.code);
await controller.undo();
const movedAfterUndoRemove = (await api.chain()).chain.map((step) => step.code);
await controller.undo();
const movedAfterUndoAdd = (await api.chain()).chain.map((step) => step.code);
report.moved = {
  before: movedBefore,
  afterRemove: movedAfterRemove,
  afterUndoRemove: movedAfterUndoRemove,
  afterUndoAdd: movedAfterUndoAdd,
  labels: controller.historyLabels.slice(),
  calls: controller.chain.map((step) => step.code),
};

/* -- the round trip ----------------------------------------------------- */
function stable(value) {
  if (Array.isArray(value)) return value.map(stable);
  if (value && typeof value === "object") {
    const out = {};
    for (const key of Object.keys(value).sort()) out[key] = stable(value[key]);
    return out;
  }
  return value;
}
function stepsOf(document_) {
  const steps = (document_.chain && document_.chain.steps) || [];
  return steps.map((step) => ({
    code: step.code,
    enabled: step.enabled,
    params: Object.fromEntries(
      Object.entries(step.params || {}).map(([name, node]) => [
        name,
        node && typeof node === "object" ? node.Value : node,
      ])
    ),
    state: (step.state && step.state.Value) || "",
  }));
}
function paramsOf(steps) {
  return Object.fromEntries(steps.map((step) => [step.code, step.params]));
}
function bypassOf(steps) {
  return steps.map((step) => `${step.code}:${step.enabled ? "on" : "off"}`);
}
function statesOf(steps) {
  return steps.map((step) => `${step.code}:${step.state.length} bytes`);
}

await controller.newSession();
/* the shell shows its own view and its own markers */
Object.assign(view, {
  zoom: 2.5,
  scroll: 1.5,
  frequency_range: [100, 8000],
  selection: { t0: 0.5, t1: 2, f0: 80, f1: 400 },
  view_mode: "waveform",
  spectrogram: false,
});
markers.push({ id: "m1", name: "Hum", start: 1.0, end: 2.5 });
controller.setViewState(view);
controller.setMarkers(markers);

await controller.addModule("AUR_DEHUM");
await controller.setParam(0, "Base Frequency", 55.0);
await controller.addModule("AUR_DECLICK");
await controller.setBypass(1, true);
await controller.addModule("AUR_DEBLEED");
await controller.setParam(2, "Strength", 0.4);

/* the document the client builds on its own: the chain routes carry no state */
const own = controller.buildDocument();
report.roundTrip.ownDocument = own.chain.steps.map((step) => ({
  code: step.code,
  bytes: String((step.state && step.state.Value) || "").length,
}));

const saved = await controller.save({ name: NAME });
const before = {
  chain: stepsOf(saved),
  view: saved.view,
  markers: saved.markers,
};
report.roundTrip.savedPath = String(saved.path || "");

/* break the session, so the load has something to restore */
await controller.removeModule(1);
await controller.setParam(0, "Base Frequency", 70.0);
report.roundTrip.broken = (await api.chain()).chain.map((step) => step.code);

const loaded = await controller.load(NAME);
const after = {
  chain: stepsOf(loaded),
  view: loaded.view,
  markers: loaded.markers,
};
report.roundTrip.restored = (await api.chain()).chain.map((step) => step.code);

/* the second save shows the state of the live chain after the load */
const second = await controller.save({ name: NAME + "-2" });
const live = stepsOf(second);

const rows = [];
function row(what, beforeValue, afterValue) {
  rows.push({ what, before: beforeValue, after: afterValue, same: beforeValue === afterValue });
}
row(
  "chain (codes in order)",
  before.chain.map((step) => step.code).join(" > "),
  after.chain.map((step) => step.code).join(" > ")
);
row(
  "parameter values",
  JSON.stringify(stable(paramsOf(before.chain))),
  JSON.stringify(stable(paramsOf(after.chain)))
);
row(
  "bypass states",
  JSON.stringify(bypassOf(before.chain)),
  JSON.stringify(bypassOf(after.chain))
);
row(
  "module state (base64)",
  JSON.stringify(statesOf(before.chain)),
  JSON.stringify(statesOf(after.chain))
);
row(
  "module state in the live chain",
  JSON.stringify(statesOf(before.chain)),
  JSON.stringify(statesOf(live))
);
row("view state", JSON.stringify(stable(before.view)), JSON.stringify(stable(after.view)));
row("markers", JSON.stringify(stable(before.markers)), JSON.stringify(stable(after.markers)));

report.roundTrip.rows = rows;
report.roundTrip.identical = rows.filter((entry) => entry.same).length;
report.roundTrip.total = rows.length;
report.roundTrip.states = before.chain.map((step) => ({
  code: step.code,
  length: step.state.length,
}));
report.roundTrip.restores = restores.length;
report.roundTrip.dirty = controller.dirty;

/* -- the undo of a removal: does the module keep its state? ------------- */
await controller.newSession();
await controller.addModule("AUR_DEHUM");
const learned = await api.learn(0);
const kept = await controller.save({ name: NAME + "-3" });
const stateBefore = (kept.chain.steps[0].state || {}).Value || "";
await controller.removeModule(0);
await controller.undo();
const restored = await controller.save({ name: NAME + "-4" });
const stateAfter = (restored.chain.steps[0].state || {}).Value || "";
report.removal = {
  learned: String((learned && (learned.message || learned.detail)) || learned || ""),
  before: stateBefore.length,
  after: stateAfter.length,
  beforeHead: stateBefore.slice(0, 40),
  afterHead: stateAfter.slice(0, 40),
  same: stateBefore === stateAfter,
};

process.stdout.write(JSON.stringify(report));
"""


# --------------------------------------------------------------------------
# The in-process server.
# --------------------------------------------------------------------------


def _free_port() -> int:
    """Return a port that is free right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class LiveServer:
    """A uvicorn server running in a thread of this process."""

    def __init__(self) -> None:
        import uvicorn

        from aurserver.app import app

        self.port = _free_port()
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(
            target=self.server.run, name="auralis-session-server", daemon=True
        )

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, timeout: float = 60.0) -> None:
        """Start the server and wait until it answers."""
        import urllib.error
        import urllib.request

        self.thread.start()
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(self.base_url + "/api/health", timeout=2.0):
                    return
            except (urllib.error.URLError, OSError):  # noqa: PERF203 - the server starts
                time.sleep(0.25)
        raise RuntimeError("the in-process server did not become healthy")

    def stop(self) -> None:
        """Ask the server to leave and wait for the thread."""
        self.server.should_exit = True
        self.thread.join(timeout=15)


@pytest.fixture(scope="module")
def live_run():
    """The real server, the real api client: one run of the live script."""
    server = LiveServer()
    server.start()
    try:
        yield _live_json(LIVE_SCRIPT, server.base_url, SESSION_NAME)
    finally:
        server.stop()
        for name in (SESSION_NAME, SESSION_NAME + "-2"):
            path = SESSIONS_DIR / f"{name}.json"
            try:
                path.unlink()
            except OSError:
                pass


def _live_json(script: str, base_url: str, name: str) -> dict:
    """Run a script against the live server and return its report."""
    data = _node_json(
        script.replace("__URL__", SESSION_JS.as_uri())
        .replace("__API__", API_JS.as_uri())
        .replace("__BASE__", base_url)
        .replace("__NAME__", name)
        .replace("__WAV__", _probe_wav_path())
    )
    assert isinstance(data, dict), "the live script printed no report"
    return data


# --------------------------------------------------------------------------
# 1. The module.
# --------------------------------------------------------------------------


def test_the_session_module_parses_and_exports_its_api():
    """The module loads without a browser and offers what the shell needs."""
    data = _session_json(
        r"""
const mod = await import("__URL__");
process.stdout.write(JSON.stringify({
  exports: Object.keys(mod).sort(),
  mergeWindowMs: mod.MERGE_WINDOW_MS,
  documentFormat: mod.DOCUMENT_FORMAT,
  documentVersion: mod.DOCUMENT_VERSION,
  chainFormat: mod.CHAIN_FORMAT,
  routes: mod.SESSION_ROUTES,
}));
"""
    )
    for name in ("createSessionController", "renderSessionBar", "MERGE_WINDOW_MS"):
        assert name in data["exports"], f"session.js does not export {name}"
    assert data["mergeWindowMs"] == 1000, "the merge window is not one second"
    assert data["documentFormat"] == "auralis.document"
    assert data["documentVersion"] == 1
    assert data["chainFormat"] == "auralis.chain"
    assert data["routes"]["save"] == "/api/session/save"
    assert data["routes"]["load"] == "/api/session/load"


# --------------------------------------------------------------------------
# 2. The public API of the controller.
# --------------------------------------------------------------------------


def test_the_controller_offers_the_documented_api():
    """Every member the shell uses is there and has the right kind."""
    data = _session_json(CONTROLLER_SCRIPT)
    api = data["api"]
    missing = [name for name in REQUIRED_API if name not in api["members"]]
    assert not missing, f"the controller does not offer: {missing}"
    for name in REQUIRED_METHODS:
        assert api["types"][name] == "function", f"{name} is not callable"
    for name in ("canUndo", "canRedo"):
        assert api["types"][name] == "boolean", f"{name} is not a flag"
    assert api["types"]["historyLabels"] == "object", "historyLabels is not a list"
    assert api["labels"] == [], "a fresh controller has no history"
    assert api["canUndo"] is False
    assert api["canRedo"] is False
    assert api["dirty"] is False
    assert api["mergeWindowMs"] == 1000


def test_the_controller_survives_a_module_without_a_browser():
    """The module touches no document while it is imported."""
    data = _session_json(
        r"""
const mod = await import("__URL__");
const controller = mod.createSessionController({ api: null });
process.stdout.write(JSON.stringify({
  hasDocument: typeof globalThis.document !== "undefined",
  members: Object.keys(controller).sort(),
  canUndo: controller.canUndo,
  labels: controller.historyLabels.slice(),
}));
"""
    )
    assert data["hasDocument"] is False, "the harness has no document — as expected"
    assert "undo" in data["members"], "the controller was not built without a browser"
    assert data["canUndo"] is False
    assert data["labels"] == []


# --------------------------------------------------------------------------
# 3. The coalescing rule.
# --------------------------------------------------------------------------


def test_a_knob_drag_is_one_step_of_the_history():
    """Three frames of a drag are one step, not three."""
    data = _session_json(CONTROLLER_SCRIPT)
    assert data["callsAfterDrag"] == 3, "the drag sent three updates to the server"
    assert data["labelsAfterDrag"] == ["Add De-Click", "Change Sensitivity"], (
        f"the drag filled the history: {data['labelsAfterDrag']}"
    )
    assert data["valueAfterDrag"] == 3


def test_one_undo_steps_back_over_the_whole_gesture():
    """The undo of a drag restores the value from before the drag."""
    data = _session_json(CONTROLLER_SCRIPT)
    assert data["undoneDrag"] == "Change Sensitivity"
    assert data["valueAfterUndoDrag"] == 0.5, (
        "the undo did not restore the value from before the drag"
    )
    assert data["labelsAfterUndoDrag"] == ["Add De-Click"]
    assert data["canUndoAfterUndo"] is True
    assert data["canRedoAfterUndo"] is True


def test_a_redo_replays_the_newest_value_of_the_drag():
    """The redo of a drag ends at the last value of the drag."""
    data = _session_json(CONTROLLER_SCRIPT)
    assert data["redoneDrag"] == "Change Sensitivity"
    assert data["valueAfterRedoDrag"] == 3
    assert data["labelsAfterRedo"] == ["Add De-Click", "Change Sensitivity"]


def test_a_change_outside_the_merge_window_is_a_step_of_its_own():
    """A second later the next change opens a new step."""
    data = _session_json(CONTROLLER_SCRIPT)
    assert data["labelsAfterWindow"] == [
        "Add De-Click",
        "Change Sensitivity",
        "Change Sensitivity",
    ], f"the window did not close: {data['labelsAfterWindow']}"
    assert data["valueAfterWindow"] == 4
    assert data["undoneWindow"] == "Change Sensitivity"
    assert data["valueAfterUndoWindow"] == 3, "the undo did not step back one edit"
    assert data["canRedoAfterUndo"] is True


# --------------------------------------------------------------------------
# 4. The labels.
# --------------------------------------------------------------------------


def test_the_history_labels_are_sensible():
    """Every label names the action in words."""
    data = _session_json(CONTROLLER_SCRIPT)
    patterns = (
        r"^Add .+",
        r"^Remove .+",
        r"^Reorder .+",
        r"^Bypass .+",
        r"^Enable .+",
        r"^Change .+",
        r"^Clear Chain$",
        r"^New Session$",
        r"^Open .+",
    )
    labels = (
        list(data["labelsAfterWindow"])
        + list(data["labelsAfterBypass"])
        + list(data["labelsAfterRemove"])
        + list(data["afterNewSession"]["labels"])
        + list(data["loaded"]["labels"])
    )
    assert labels, "the history has no labels"
    for label in labels:
        assert isinstance(label, str) and label.strip(), f"empty label: {label!r}"
        assert any(re.match(pattern, label) for pattern in patterns), (
            f"the label {label!r} does not name an action"
        )
    assert data["labelsAfterAdd"] == ["Add De-Click"]
    assert data["labelsAfterBypass"][-1] == "Bypass De-Click"
    assert data["labelsAfterRemove"][-1] == "Remove De-Click", (
        f"the removal is not in the history: {data['labelsAfterRemove']}"
    )


# --------------------------------------------------------------------------
# 5. Undo and redo go through the server.
# --------------------------------------------------------------------------


def test_undo_and_redo_are_carried_out_by_the_server():
    """Every step of the history is an ordinary request to the server."""
    data = _session_json(CONTROLLER_SCRIPT)
    ops = data["apiCalls"]
    assert ops.count("setParam") >= 5, f"the parameter edits did not reach the server: {ops}"
    assert "addModule" in ops
    assert "removeModule" in ops
    assert "setBypass" in ops
    assert ops.index("addModule") < ops.index("setParam"), "the order of the calls is wrong"


def test_the_controller_tells_its_listener_about_every_change():
    """The onChange callback sees the state after every action."""
    data = _session_json(CONTROLLER_SCRIPT)
    changes = data["changes"]
    assert len(changes) >= 6, f"the listener heard too little: {len(changes)}"
    assert changes[0]["labels"] == ["Add De-Click"], "the first action was not recorded"
    assert changes[0]["canUndo"] is True
    assert any(change["dirty"] for change in changes), "the listener never saw a changed session"
    assert changes[-1]["canUndo"] is True


# --------------------------------------------------------------------------
# 6. The session document.
# --------------------------------------------------------------------------


def test_the_session_document_carries_the_chain_and_the_view():
    """The document follows the format of aurcore/document.py."""
    data = _session_json(CONTROLLER_SCRIPT)
    document = data["document"]
    assert document["format"] == "auralis.document"
    assert document["version"] == 1

    assert document["chain"]["format"] == "auralis.chain"
    assert document["chain"]["version"] == 1
    steps = document["chain"]["steps"]
    assert len(steps) == 1, f"the document has {len(steps)} steps"
    assert steps[0]["code"] == "AUR_DECLICK"
    assert steps[0]["enabled"] is True, "the module was switched out before the save"
    assert steps[0]["params"]["Sensitivity"]["Type"] in ("Float", "Int")
    assert steps[0]["params"]["Sensitivity"]["Value"] == 4
    assert steps[0]["state"]["Type"] == "Base64"

    assert data["bypassedStep"]["enabled"] is False, "a switched out module is not enabled"

    view = document["view"]
    assert view["zoom"] == 2.5
    assert view["scroll"] == 1.5
    assert view["frequency_range"] == [100, 8000]
    assert view["view_mode"] == "waveform"
    assert view["spectrogram"] is False
    assert view["selection"]["t0"] == 0.5
    assert view["selection"]["f1"] == 400

    audio = document["audio"]
    assert audio["path"] == "work/interview.wav"
    assert audio["sample_rate"] == 48000
    assert audio["channels"] == 2
    assert audio["frames"] == 123456

    history = document["history"]
    assert history["depth"] == len(history["labels"])
    assert history["depth"] > 0
    assert history["can_undo"] is True
    assert history["depth_limit"] == 200


def test_a_session_can_be_saved_and_loaded_again():
    """The document travels to the server and restores the chain."""
    data = _session_json(CONTROLLER_SCRIPT)
    assert data["saveCalls"] == 1, "the document was not sent to the server"
    assert data["saved"]["format"] == "auralis.document"
    assert data["saved"]["dirty"] is False, "the session is still dirty after the save"

    loaded = data["loaded"]
    assert loaded["format"] == "auralis.document"
    assert len(loaded["chain"]) == 1, "the chain was not restored"
    assert loaded["chain"][0]["code"] == "AUR_DECLICK"
    assert loaded["chain"][0]["params"]["Sensitivity"]["Value"] == 4
    assert loaded["chain"][0]["bypass"] is True, "the switched out state was not restored"
    assert loaded["dirty"] is False


def test_the_load_is_one_step_of_the_history():
    """A loaded session can be undone: the chain that was there before is back."""
    data = _session_json(CONTROLLER_SCRIPT)
    assert data["loaded"]["labels"][-1] == "Open session", (
        f"the load is not one step: {data['loaded']['labels']}"
    )
    assert data["loaded"]["canUndo"] is True
    assert data["undoneLoad"] == "Open session"
    assert data["chainAfterUndoneLoad"] == ["AUR_DECLICK"], (
        "the undo of the load did not bring the chain back"
    )


def test_a_new_session_empties_the_chain_and_can_be_undone():
    """A new session starts from nothing — and one undo brings the chain back."""
    data = _session_json(CONTROLLER_SCRIPT)
    assert data["afterNewSession"]["chain"] == 0
    assert data["afterNewSession"]["canUndo"] is True, "a new session is not undoable"
    assert data["afterNewSession"]["canRedo"] is False
    assert data["afterNewSession"]["labels"][-1] == "New Session"
    assert data["undoneNewSession"] == "New Session"
    assert data["afterUndoneNewSession"]["chain"] == ["AUR_DECLICK", "AUR_DEHUM"], (
        "the undo of a new session did not bring the chain back"
    )


def test_the_mirror_carries_the_plain_values_of_the_chain():
    """The shell paints the chain from the mirror of the controller."""
    data = _session_json(CONTROLLER_SCRIPT)
    mirror = data["mirror"]
    assert len(mirror) == 1, "the mirror does not have the chain"
    assert mirror[0]["code"] == "AUR_DECLICK"
    assert mirror[0]["display_name"] == "De-Click"
    assert mirror[0]["bypass"] is True
    assert mirror[0]["values"]["Sensitivity"] == 4


def test_emptying_the_chain_is_one_step_of_the_history():
    """The modules come back in one undo."""
    data = _session_json(CONTROLLER_SCRIPT)
    assert data["afterClear"]["chain"] == 0
    assert data["afterClear"]["steps"] == 1, "the clear filled the history"
    assert data["afterClear"]["labels"][-1] == "Clear Chain"
    assert data["afterClearUndo"]["chain"] == ["AUR_DECLICK", "AUR_DEHUM"], (
        "the modules did not come back"
    )
    assert data["afterClearUndo"]["redoLabels"][-1] == "Clear Chain", (
        "the clear is not the step that can be redone"
    )


# --------------------------------------------------------------------------
# 7. The undo coverage: every action of the interface.
# --------------------------------------------------------------------------

#: The actions of the interface that are not an edit of the session.
NOT_AN_EDIT = ("save the session",)


def test_every_action_of_the_interface_is_one_undoable_step():
    """Every action that changes the session is one step of the history."""
    data = _session_json(UNDO_COVERAGE_SCRIPT)
    actions = data["actions"]
    names = [entry["name"] for entry in actions]
    assert names, "no action was driven"

    for entry in actions:
        name = entry["name"]
        if name in NOT_AN_EDIT:
            assert entry["steps"] == 0, f"{name} is not an edit, but it made a step"
            continue
        assert entry["steps"] == 1, (
            f"{name} is not one step of the history: {entry['steps']} steps"
        )
        assert entry["label"], f"{name} has no label"
        assert entry["restoredOk"] is True, (
            f"the undo of {name} did not bring the session back: "
            f"before {entry['before']} after {entry['restored']}"
        )


def test_the_interface_offers_every_action_the_shell_needs():
    """The actions the workbench performs are all there."""
    data = _session_json(UNDO_COVERAGE_SCRIPT)
    names = {entry["name"] for entry in data["actions"]}
    for expected in (
        "add a module",
        "remove a module",
        "move a module",
        "switch a module out",
        "turn a knob",
        "empty the chain",
        "start a new session",
        "open a session",
        "save the session",
    ):
        assert expected in names, f"the interface cannot {expected}"


# --------------------------------------------------------------------------
# 8. The history addresses the module, not the index.
# --------------------------------------------------------------------------

def test_the_undo_survives_a_module_that_moved():
    """Add a module, remove one in the middle, undo both: the chain is back."""
    data = _session_json(CONTROLLER_SCRIPT)
    moved = data["moved"]
    assert moved["before"] == ["AUR_DEHUM", "AUR_DECLICK", "AUR_DEESS"]
    assert moved["afterRemove"] == ["AUR_DEHUM", "AUR_DEESS", "AUR_DEESS"], (
        "the module in the middle was not removed"
    )
    assert moved["afterUndoRemove"] == [
        "AUR_DEHUM",
        "AUR_DECLICK",
        "AUR_DEESS",
        "AUR_DEESS",
    ], f"the undo did not put the module back: {moved['afterUndoRemove']}"
    assert moved["afterUndoAdd"] == moved["before"], (
        f"the chain did not come back as it was: {moved['afterUndoAdd']}"
    )


# --------------------------------------------------------------------------
# 9. The toolbar.
# --------------------------------------------------------------------------


def test_the_toolbar_follows_the_history():
    """The buttons, the label, the hint and the dirty marker follow the state."""
    data = _session_json(TOOLBAR_SCRIPT)
    assert data["style"] is True, "the stylesheet was not injected"
    assert data["buttons"] == ["open", "redo", "save", "undo"]
    assert data["classes"] == "ss-root"

    # nothing has happened yet: nothing to undo, nothing to redo, no change
    assert data["initial"]["undo"] is True, "undo is not disabled at the start"
    assert data["initial"]["redo"] is True, "redo is not disabled at the start"
    assert data["initial"]["dirty"] is False
    assert data["initial"]["label"] == "", "the toolbar names an action that never happened"
    assert data["initial"]["hint"] == "no history", (
        f"the toolbar lies about the history: {data['initial']['hint']!r}"
    )
    assert data["initial"]["undoTitle"] == "Nothing to undo"
    assert data["initial"]["redoTitle"] == "Nothing to redo"

    # one action: it can be undone, it cannot be redone, the session changed
    assert data["afterAction"]["undo"] is False, "undo stays disabled after an action"
    assert data["afterAction"]["redo"] is True, "redo is enabled although there is nothing to redo"
    assert data["afterAction"]["label"] == "Add De-Click"
    assert data["afterAction"]["dirty"] is True, "the session is not marked as changed"
    assert data["afterAction"]["undoLabel"] == "Add De-Click"
    assert data["afterAction"]["redoLabel"] == ""
    assert "undo: Add De-Click" in data["afterAction"]["hint"]
    assert "redo:" not in data["afterAction"]["hint"]
    assert data["afterAction"]["undoTitle"] == "Undo Add De-Click"

    # after the undo: the action can be redone, the label names it
    assert data["afterUndo"]["redo"] is False, "redo stays disabled after an undo"
    assert data["afterUndo"]["undo"] is True
    assert data["afterUndo"]["redoLabel"] == "Add De-Click"
    assert "redo: Add De-Click" in data["afterUndo"]["hint"]
    assert "undo:" not in data["afterUndo"]["hint"]

    # the button works on its own
    assert data["afterClick"]["redo"] is False, "the redo button does not work"
    assert data["afterClick"]["label"] == "Add De-Click"

    # after the save: no unsaved changes
    assert data["afterSave"]["dirty"] is False, "the session stays dirty after the save"
    assert data["dirtyBeforeSave"] is False
    assert data["errors"], "a failing action was not reported"
    assert data["destroyed"] is True
    assert data["containerChildren"] == 0, "the toolbar stays in the document"


# --------------------------------------------------------------------------
# 10. The round trip against the real server.
# --------------------------------------------------------------------------


def test_the_session_round_trips_through_the_server(live_run):
    """Save a session, break it, load it: every value comes back."""
    assert live_run["transport"] == "server", (
        f"the document did not travel through the server: {live_run['transport']!r}"
    )
    trip = live_run["roundTrip"]
    rows = trip["rows"]
    assert rows, "the round trip measured nothing"

    print("\nround trip (save -> break -> load):")
    for row in rows:
        mark = "same" if row["same"] else "DIFFERENT"
        print(f"  {row['what']:<30} {mark}")
        if not row["same"]:
            print(f"      before: {row['before']}")
            print(f"      after : {row['after']}")

    assert trip["identical"] == trip["total"], (
        f"{trip['total'] - trip['identical']} of {trip['total']} values did not come back"
    )
    assert trip["restored"] == ["AUR_DEHUM", "AUR_DECLICK", "AUR_DEBLEED"], (
        f"the chain was not restored: {trip['restored']}"
    )
    assert trip["dirty"] is False, "the loaded session is marked as changed"


def test_the_module_state_travels_with_the_session(live_run):
    """The state of a module is part of the document, not an empty field."""
    trip = live_run["roundTrip"]
    states = {entry["code"]: entry["length"] for entry in trip["states"]}
    assert states.get("AUR_DEHUM", 0) > 0, (
        "the module has no state, so the round trip proves nothing"
    )
    assert states.get("AUR_DEBLEED", 0) > 0, (
        "the module has no state, so the round trip proves nothing"
    )
    row = next(
        entry for entry in trip["rows"] if entry["what"] == "module state (base64)"
    )
    assert row["same"] is True, f"the module state did not come back: {row}"
    live = next(
        entry
        for entry in trip["rows"]
        if entry["what"] == "module state in the live chain"
    )
    assert live["same"] is True, f"the state was not restored into the modules: {live}"


def test_the_undo_survives_a_module_that_moved_on_the_server(live_run):
    """The history finds its module by code, on the real server as well."""
    moved = live_run["moved"]
    assert moved["before"] == ["AUR_DEHUM", "AUR_DECLICK", "AUR_DEESS"]
    assert moved["afterRemove"] == ["AUR_DEHUM", "AUR_DEESS", "AUR_DEBLEED"]
    assert moved["afterUndoRemove"] == [
        "AUR_DEHUM",
        "AUR_DECLICK",
        "AUR_DEESS",
        "AUR_DEBLEED",
    ], f"the undo did not put the module back: {moved['afterUndoRemove']}"
    assert moved["afterUndoAdd"] == moved["before"], (
        f"the chain did not come back as it was: {moved['afterUndoAdd']}"
    )


def test_the_client_document_has_no_state_until_the_server_writes_one(live_run):
    """The chain routes carry no state: the session route is what fills it.

    This is why the document has to travel through the server: the chain
    summary of ``GET /api/chain`` carries no state, so a document the client
    builds on its own is empty where the server's document is not.
    """
    trip = live_run["roundTrip"]
    own = {entry["code"]: entry["bytes"] for entry in trip["ownDocument"]}
    written = {entry["code"]: entry["length"] for entry in trip["states"]}
    assert own.get("AUR_DEHUM", 0) == 0, (
        f"the chain summary carries a state after all: {own}"
    )
    assert written.get("AUR_DEHUM", 0) > 0, (
        f"the server wrote no state either: {written}"
    )


def test_the_undo_of_a_removal_keeps_the_state_of_the_module(live_run):
    """A module that comes back carries its state again."""
    removal = live_run["removal"]
    assert removal["before"] > 0, "the module has no state, so the check proves nothing"
    assert removal["same"] is True, (
        f"the state of the module was lost: {removal['before']} bytes before, "
        f"{removal['after']} bytes after the undo"
    )


# --------------------------------------------------------------------------
# 11. The house rules.
# --------------------------------------------------------------------------

#: A colour literal inside a declaration value.
_COLOUR_IN_VALUE = re.compile(
    r"""(?<![\w-])#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b"""
)
_COLOUR_FUNCTION = re.compile(r"""\b(?:rgb|rgba|hsl|hsla|hwb|lab|lch|oklab|oklch)\s*\(""")

#: The names that may not appear in a source of the project.
#:
#: The names are assembled from fragments at run time: writing them out
#: would put the very strings the rule forbids into this file.
FORBIDDEN_NAMES = (
    "i" + "Zotope",
    "izo" + "tope",
    "R" + "X" + " Audio Editor",
    "R" + "X" + " 11",
    "R" + "2R",
)


def test_the_module_hard_codes_no_colour():
    """Every colour of the toolbar comes from a token of css/tokens.css."""
    text = _read(SESSION_JS)
    assert text, "aurui/js/session.js is missing"
    literals = _COLOUR_IN_VALUE.findall(text) + _COLOUR_FUNCTION.findall(text)
    assert not literals, f"session.js hard-codes colours: {sorted(set(literals))}"

    tokens = set(re.findall(r"(--[A-Za-z0-9_-]+)\s*:", _read(TOKENS_CSS)))
    used = set(re.findall(r"var\(\s*(--[A-Za-z0-9_-]+)", text))
    unknown = sorted(used - tokens)
    assert not unknown, f"session.js uses variables tokens.css does not define: {unknown}"


def test_the_module_names_no_third_party_product():
    """The sources of the project carry only our own names."""
    text = _read(SESSION_JS)
    for name in FORBIDDEN_NAMES:
        assert name not in text, f"session.js names {name!r}"
    assert not re.search(r"\bRX\b", text), "session.js names a third-party product"


def test_the_module_creates_no_element_id():
    """The module must not collide with the ids of the document."""
    text = _read(SESSION_JS)
    created = re.findall(r"""\bid\s*:\s*["']([A-Za-z][\w-]*)["']""", text)
    created += re.findall(r"""setAttribute\(\s*["']id["']\s*,\s*["']([A-Za-z][\w-]*)["']""", text)
    assert not created, f"session.js creates element ids: {created}"
