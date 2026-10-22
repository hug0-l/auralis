"""
tests/test_spectral_editor_ui.py — the region overlay of the web interface.

The overlay (``aurui/js/spectral_editor.js``) is a vanilla ES module, so it is
exercised with node: a small DOM stub is installed, the module is imported and
driven, and the results are read back as JSON.

What is checked
  1. the panel shows the mode, the strength and the size of the rectangle;
  2. the panel tells the truth about the region: while the server has no
     route for it, the user is told that the region is **not** applied;
  3. the region travels over ``POST /api/chain/{index}/region`` with the
     module index and the region in seconds and hertz;
  4. the region survives a round trip: the module keeps it, the chain
     document carries it as a base64 block and the interface reads it back;
  5. the five modes and the strength are reachable, and the panel shows what
     the module really holds;
  6. the gestures that are not on the rectangle reach the view underneath
     (pan, zoom, selection).

The tests need no browser, no server and no network.
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

# --------------------------------------------------------------------------
# Locations.
# --------------------------------------------------------------------------
REPO = Path(__file__).resolve().parents[1]
MODULE_JS = REPO / "aurui" / "js" / "spectral_editor.js"

#: The specification of the route the interface needs.  It is written as a
#: document (``e6_region_route.md``) and as a diff that can be applied
#: (``e6_region_route.patch``); the test reads whichever of the two is there.
ROUTE_SPEC_CANDIDATES = (
    REPO / "var" / "patches" / "e6_region_route.md",
    REPO / "var" / "patches" / "e6_region_route.patch",
)

#: The code of the module the overlay drives.
CODE = "AUR_SPECTRALEDITOR"

#: The five modes of the module, in the order of the parameter.
MODES = ["Attenuate", "Replace", "Gain", "Pattern", "Partials"]


# --------------------------------------------------------------------------
# The DOM stub.
#
# It implements the part of the DOM the overlay uses: elements, text nodes,
# attributes, classes, events that bubble, a canvas with a 2-D context, a
# query selector that understands tag names, classes, ids and attributes.
# --------------------------------------------------------------------------
DOM_STUB = r"""
export function installDom() {
  let doc = null;

  class StubNode {
    constructor(nodeType) {
      this.nodeType = nodeType;
      this.childNodes = [];
      this.parentNode = null;
      this.ownerDocument = null;
      this._listeners = new Map();
    }
    get children() { return this.childNodes.filter((n) => n.nodeType === 1); }
    get firstChild() { return this.childNodes.length ? this.childNodes[0] : null; }
    get lastChild() {
      return this.childNodes.length ? this.childNodes[this.childNodes.length - 1] : null;
    }
    get isConnected() {
      let node = this;
      while (node.parentNode) node = node.parentNode;
      return node === doc || node.nodeType === 9;
    }
    appendChild(child) {
      if (!child) return child;
      if (child.nodeType === 11) {
        for (const kid of child.childNodes.slice()) this.appendChild(kid);
        return child;
      }
      if (child.parentNode) child.parentNode.removeChild(child);
      child.parentNode = this;
      this.childNodes.push(child);
      return child;
    }
    append(...nodes) {
      for (const node of nodes) {
        if (node === null || node === undefined || node === false) continue;
        this.appendChild(typeof node === "string" ? doc.createTextNode(node) : node);
      }
    }
    removeChild(child) {
      const index = this.childNodes.indexOf(child);
      if (index >= 0) this.childNodes.splice(index, 1);
      if (child) child.parentNode = null;
      return child;
    }
    replaceChildren(...nodes) {
      while (this.firstChild) this.removeChild(this.firstChild);
      this.append(...nodes);
    }
    remove() { if (this.parentNode) this.parentNode.removeChild(this); }
    contains(node) {
      let cur = node;
      while (cur) { if (cur === this) return true; cur = cur.parentNode; }
      return false;
    }
    addEventListener(type, fn) {
      if (typeof fn !== "function") return;
      if (!this._listeners.has(type)) this._listeners.set(type, []);
      this._listeners.get(type).push(fn);
    }
    removeEventListener(type, fn) {
      const list = this._listeners.get(type);
      if (!list) return;
      const index = list.indexOf(fn);
      if (index >= 0) list.splice(index, 1);
    }
    /* The event walks up the tree until a handler stops it — the behaviour
       the overlay relies on to let the view have the gestures that are not
       on the rectangle. */
    dispatchEvent(event) {
      const ev = typeof event === "string" ? { type: event } : (event || {});
      ev.target = ev.target || this;
      ev.defaultPrevented = !!ev.defaultPrevented;
      ev.propagationStopped = false;
      if (typeof ev.preventDefault !== "function") {
        ev.preventDefault = () => { ev.defaultPrevented = true; };
      }
      if (typeof ev.stopPropagation !== "function") {
        ev.stopPropagation = () => { ev.propagationStopped = true; };
      }
      let node = this;
      while (node) {
        const list = (node._listeners.get(ev.type) || []).slice();
        for (const fn of list) fn.call(node, ev);
        if (ev.propagationStopped) break;
        node = node.parentNode;
      }
      return !ev.defaultPrevented;
    }
    focus() { doc.activeElement = this; }
    blur() { if (doc.activeElement === this) doc.activeElement = null; }
    click() { this.dispatchEvent("click"); }
    querySelector(sel) { const all = queryAll(this, sel); return all.length ? all[0] : null; }
    querySelectorAll(sel) { return queryAll(this, sel); }
    get textContent() {
      if (this.nodeType === 3) return this._text;
      return this.childNodes.map((n) => n.textContent).join("");
    }
    set textContent(value) {
      while (this.firstChild) this.removeChild(this.firstChild);
      if (value !== "" && value !== null && value !== undefined) {
        this.appendChild(doc.createTextNode(String(value)));
      }
    }
    get innerHTML() { return this.textContent; }
    set innerHTML(value) { this.textContent = value; }
  }

  class StubClassList {
    constructor(element) { this.element = element; }
    get names() { return String(this.element.className || "").split(/\s+/).filter(Boolean); }
    add(...names) {
      const set = new Set(this.names);
      for (const name of names) set.add(name);
      this.element.className = Array.from(set).join(" ");
    }
    remove(...names) {
      const set = new Set(this.names);
      for (const name of names) set.delete(name);
      this.element.className = Array.from(set).join(" ");
    }
    toggle(name, force) {
      const set = new Set(this.names);
      const has = set.has(name);
      const want = force === undefined ? !has : !!force;
      if (want) set.add(name); else set.delete(name);
      this.element.className = Array.from(set).join(" ");
      return want;
    }
    contains(name) { return this.names.indexOf(name) >= 0; }
  }

  /* The 2-D context of a canvas: the overlay only draws with it. */
  function makeContext() {
    return {
      fillStyle: "",
      strokeStyle: "",
      lineWidth: 1,
      calls: [],
      setTransform() {},
      clearRect() {},
      save() {},
      restore() {},
      fillRect(x, y, w, h) { this.calls.push(["fillRect", x, y, w, h]); },
      strokeRect(x, y, w, h) { this.calls.push(["strokeRect", x, y, w, h]); },
    };
  }

  class StubElement extends StubNode {
    constructor(tag) {
      super(1);
      this.tagName = String(tag || "div").toUpperCase();
      this.className = "";
      this.attributes = new Map();
      this.style = {};
      this.dataset = {};
      this.value = "";
      this.ownerDocument = doc;
      this.classList = new StubClassList(this);
      this._rect = null;
      this._context = null;
    }
    get id() { return this.getAttribute("id") || ""; }
    set id(value) { this.attributes.set("id", String(value)); }
    setAttribute(name, value) {
      this.attributes.set(String(name), value === true ? "" : String(value));
    }
    getAttribute(name) {
      const value = this.attributes.get(String(name));
      return value === undefined ? null : value;
    }
    hasAttribute(name) { return this.attributes.has(String(name)); }
    removeAttribute(name) { this.attributes.delete(String(name)); }
    getContext() {
      if (!this._context) this._context = makeContext();
      return this._context;
    }
    setPointerCapture() {}
    releasePointerCapture() {}
    getBoundingClientRect() {
      if (this._rect) {
        const r = this._rect;
        return {
          left: r.left || 0,
          top: r.top || 0,
          width: r.width || 0,
          height: r.height || 0,
          right: (r.left || 0) + (r.width || 0),
          bottom: (r.top || 0) + (r.height || 0),
        };
      }
      /* a canvas follows the size the overlay gave it */
      const left = Number.parseFloat(this.style.left) || 0;
      const top = Number.parseFloat(this.style.top) || 0;
      const width = Number.parseFloat(this.style.width) || 0;
      const height = Number.parseFloat(this.style.height) || 0;
      return {
        left, top, width, height,
        right: left + width,
        bottom: top + height,
      };
    }
  }

  class StubText extends StubNode {
    constructor(text) {
      super(3);
      this._text = String(text === null || text === undefined ? "" : text);
      this.ownerDocument = doc;
    }
    get textContent() { return this._text; }
    set textContent(value) {
      this._text = String(value === null || value === undefined ? "" : value);
    }
  }

  class StubFragment extends StubNode {
    constructor() { super(11); this.ownerDocument = doc; }
  }

  function walk(node, visit) {
    for (const child of node.childNodes) {
      if (child.nodeType === 1) { visit(child); walk(child, visit); }
    }
  }

  function matchesSimple(node, sel) {
    const pattern = new RegExp(
      "#([\\w-]+)|\\.([\\w-]+)|\\[([\\w-]+)" +
      "(?:=(?:\"([^\"]*)\"|'([^']*)'|([^\\]]*)))?\\]|([a-zA-Z][\\w-]*)",
      "g"
    );
    let match;
    let matched = false;
    while ((match = pattern.exec(sel)) !== null) {
      matched = true;
      if (match[1]) {
        if (node.getAttribute("id") !== match[1]) return false;
      } else if (match[2]) {
        if (!node.classList.contains(match[2])) return false;
      } else if (match[3]) {
        const wanted =
          match[4] !== undefined ? match[4] : (match[5] !== undefined ? match[5] : match[6]);
        if (wanted === undefined) {
          if (!node.hasAttribute(match[3])) return false;
        } else if (node.getAttribute(match[3]) !== wanted) {
          return false;
        }
      } else if (match[7]) {
        if (node.tagName !== match[7].toUpperCase()) return false;
      }
    }
    return matched;
  }

  function splitSelector(sel) {
    const parts = [];
    let current = "";
    let quote = null;
    for (const ch of String(sel || "")) {
      if (quote) {
        current += ch;
        if (ch === quote) quote = null;
        continue;
      }
      if (ch === '"' || ch === "'") { quote = ch; current += ch; continue; }
      if (/\s/.test(ch)) {
        if (current) { parts.push(current); current = ""; }
        continue;
      }
      current += ch;
    }
    if (current) parts.push(current);
    return parts;
  }

  function queryAll(root, sel) {
    const parts = splitSelector(sel);
    if (!parts.length) return [];
    let current = [root];
    for (const part of parts) {
      const next = [];
      for (const node of current) {
        walk(node, (el) => { if (matchesSimple(el, part)) next.push(el); });
      }
      current = next;
    }
    return current;
  }

  function findById(root, id) {
    let found = null;
    walk(root, (el) => { if (!found && el.getAttribute("id") === id) found = el; });
    return found;
  }

  doc = new StubNode(9);
  doc.ownerDocument = doc;
  doc.activeElement = null;
  doc.createElement = (tag) => new StubElement(tag);
  doc.createTextNode = (text) => new StubText(text);
  doc.createDocumentFragment = () => new StubFragment();
  doc.getElementById = (id) => findById(doc, id);
  doc.querySelector = (sel) => {
    const all = queryAll(doc, sel);
    return all.length ? all[0] : null;
  };
  doc.querySelectorAll = (sel) => queryAll(doc, sel);
  doc.getElementsByTagName = (tag) => {
    const out = [];
    const want = String(tag).toUpperCase();
    walk(doc, (el) => { if (el.tagName === want) out.push(el); });
    return out;
  };

  const html = new StubElement("html");
  const head = new StubElement("head");
  const body = new StubElement("body");
  html.appendChild(head);
  html.appendChild(body);
  doc.appendChild(html);
  doc.head = head;
  doc.body = body;
  doc.documentElement = html;

  globalThis.Node = StubNode;
  globalThis.Element = StubElement;
  globalThis.HTMLElement = StubElement;
  globalThis.Text = StubText;
  globalThis.DocumentFragment = StubFragment;
  globalThis.Event = class StubEvent {
    constructor(type) {
      this.type = type;
      this.defaultPrevented = false;
      this.target = null;
    }
    preventDefault() { this.defaultPrevented = true; }
    stopPropagation() {}
  };
  globalThis.document = doc;
  return doc;
}
"""


# --------------------------------------------------------------------------
# The driver.
#
# Every scenario installs the DOM, imports the module and returns what it
# found as a JSON value.
# --------------------------------------------------------------------------
DRIVER = r"""
import { installDom } from "./dom_stub.mjs";

const [, , scenario, payloadJson, moduleUrl] = process.argv;
const payload = JSON.parse(payloadJson || "{}");
installDom();
const mod = await import(moduleUrl);

/* The composite view the overlay draws on: 10 s over 800 px, 0 – 24 kHz
   over 400 px, linear. */
function makeView(selection) {
  return {
    duration: 10,
    zoom: 1,
    scroll: 0,
    frequencyRange: { lo: 0, hi: 24000 },
    _logFreq: false,
    _layout: { w: 800, h: 400, specH: 400, waveTop: 400, waveH: 200 },
    _timeToX(t) { return t * 80; },
    _xToTime(x) { return x / 80; },
    _freqToY(f) { return (1 - f / 24000) * 400; },
    _displayFreq(v) { return (1 - v) * 24000; },
    selection: selection || null,
  };
}

/* The stage: the container of the canvases. */
function makeStage() {
  const wrap = document.createElement("div");
  wrap.id = "canvas-wrap";
  wrap._rect = { left: 0, top: 0, width: 800, height: 600 };
  const spec = document.createElement("canvas");
  spec.id = "spec-canvas";
  spec._rect = { left: 0, top: 0, width: 800, height: 400 };
  wrap.append(spec);
  document.body.append(wrap);
  return wrap;
}

/* The transport: the way the region reaches the module. */
function makeTransport(options = {}) {
  const calls = { find: 0, apply: [], params: 0 };
  return {
    calls,
    async findIndex() {
      calls.find += 1;
      return options.index === undefined ? 2 : options.index;
    },
    async applyRegion(index, region) {
      calls.apply.push({ index, region: { ...region } });
      if (options.failStatus) {
        const error = new Error(options.failMessage || "Not Found");
        error.status = options.failStatus;
        throw error;
      }
      return {
        ok: true,
        index,
        region: { ...region },
        region_length: region.end - region.start,
      };
    },
    async readParams(index) {
      calls.params += 1;
      return {
        index,
        values: {
          Mode: options.mode || "Attenuate",
          Strength: options.strength === undefined ? 0.5 : options.strength,
          "Region Length": options.length === undefined ? 0.5 : options.length,
        },
      };
    },
  };
}

/* The pointer of a gesture. */
function pointer(type, x, y) {
  return {
    type,
    clientX: x,
    clientY: y,
    button: 0,
    pointerId: 1,
    preventDefault() { this.defaultPrevented = true; },
    stopPropagation() { this.propagationStopped = true; },
  };
}

const REGION = { start: 1.0, end: 2.0, low: 1000, high: 5000 };

const scenarios = {
  /* the panel of a fresh editor */
  async panel() {
    const wrap = makeStage();
    const editor = mod.createSpectralEditor({ container: wrap });
    const status = wrap.querySelector("#spectral-editor-status");
    return {
      options: Array.from(editor.panel.querySelectorAll("option")).map((o) => o.value),
      readout: editor.panel.querySelector(".se-readout").textContent,
      statusText: status ? status.textContent : null,
      statusRole: status ? status.getAttribute("role") : null,
      statusColour: status ? status.style.color : null,
      overlayId: editor.element.id,
      panelId: editor.panel.id,
      state: editor.getState(),
    };
  },

  /* the rectangle is drawn and the readout follows it */
  async drawn() {
    const wrap = makeStage();
    const editor = mod.createSpectralEditor({ container: wrap });
    editor.render(makeView(), { region: REGION, mode: "Gain", strength: 0.75 });
    const context = editor.element.getContext("2d");
    return {
      region: editor.getRegion(),
      readout: editor.panel.querySelector(".se-readout").textContent,
      statusText: wrap.querySelector("#spectral-editor-status").textContent,
      drawn: context.calls.map((c) => c[0]),
      label: editor.getState().label,
    };
  },

  /* the route is missing: the user has to be told */
  async missingRoute() {
    const wrap = makeStage();
    const transport = makeTransport({ failStatus: 404, failMessage: "Not Found" });
    const editor = mod.createSpectralEditor({ container: wrap, transport });
    editor.render(makeView(), { region: REGION });
    await editor.applyRegion();
    const status = wrap.querySelector("#spectral-editor-status");
    return {
      calls: transport.calls.apply,
      statusText: status.textContent,
      statusColour: status.style.color,
      label: editor.getState().label,
      kind: editor.getState().status.kind,
    };
  },

  /* the route is there: the region is applied and confirmed */
  async applied() {
    const wrap = makeStage();
    const transport = makeTransport({ mode: "Replace", strength: 0.25, length: 1 });
    const editor = mod.createSpectralEditor({ container: wrap, transport });
    editor.render(makeView(), { region: REGION });
    await editor.applyRegion();
    const status = wrap.querySelector("#spectral-editor-status");
    return {
      calls: transport.calls.apply,
      found: transport.calls.find,
      params: transport.calls.params,
      statusText: status.textContent,
      statusColour: status.style.color,
      label: editor.getState().label,
      kind: editor.getState().status.kind,
    };
  },

  /* a drag on the rectangle: the module is told about it */
  async drag() {
    const wrap = makeStage();
    const transport = makeTransport();
    const editor = mod.createSpectralEditor({ container: wrap, transport });
    editor.render(makeView(), { region: REGION });
    const overlay = editor.element;
    overlay.dispatchEvent(pointer("pointerdown", 120, 350));
    overlay.dispatchEvent(pointer("pointermove", 160, 330));
    overlay.dispatchEvent(pointer("pointerup", 160, 330));
    await new Promise((resolve) => setTimeout(resolve, 0));
    return {
      region: editor.getRegion(),
      calls: transport.calls.apply,
      statusText: wrap.querySelector("#spectral-editor-status").textContent,
    };
  },

  /* the gestures that are not on the rectangle reach the view */
  async passThrough() {
    const wrap = makeStage();
    const transport = makeTransport();
    const editor = mod.createSpectralEditor({ container: wrap, transport });
    editor.render(makeView(), { region: REGION });
    const seen = [];
    /* the composite view listens on the container of the canvases */
    wrap.addEventListener("pointerdown", (ev) => seen.push(["pointerdown", ev.clientX]));
    wrap.addEventListener("pointermove", (ev) => seen.push(["pointermove", ev.clientX]));
    wrap.addEventListener("wheel", (ev) => seen.push(["wheel", ev.clientX]));
    const overlay = editor.element;

    /* outside of the rectangle: the view may pan or select */
    const outside = pointer("pointerdown", 400, 100);
    overlay.dispatchEvent(outside);
    const outsideSeen = seen.slice();

    /* inside of the rectangle: the overlay takes the gesture */
    seen.length = 0;
    const inside = pointer("pointerdown", 120, 350);
    overlay.dispatchEvent(inside);
    const insideSeen = seen.slice();

    /* a wheel over the rectangle is not the overlay's business */
    seen.length = 0;
    const wheel = pointer("wheel", 120, 350);
    wheel.deltaY = -100;
    overlay.dispatchEvent(wheel);

    return {
      outsideSeen,
      insideSeen,
      wheelSeen: seen.slice(),
      regionAfter: editor.getRegion(),
      stopped: inside.propagationStopped === true,
      notStopped: outside.propagationStopped !== true,
    };
  },

  /* the five modes and the strength */
  async modes() {
    const wrap = makeStage();
    const transport = makeTransport({ mode: "Attenuate", strength: 0.5 });
    const editor = mod.createSpectralEditor({ container: wrap, transport });
    editor.render(makeView(), { region: REGION });
    const select = editor.panel.querySelector("select");
    const slider = editor.panel.querySelector("input");
    const readouts = [];

    for (const mode of ["Attenuate", "Replace", "Gain", "Pattern", "Partials"]) {
      select.value = mode;
      select.dispatchEvent("change");
      readouts.push(editor.panel.querySelector(".se-readout").textContent);
    }

    slider.value = "0.8";
    slider.dispatchEvent("input");
    const afterSlider = editor.panel.querySelector(".se-readout").textContent;
    const value = editor.panel.querySelector(".se-value").textContent;
    const state = editor.getState();

    /* the module is asked what it holds, and the answer is shown */
    await editor.applyRegion();
    const confirmed = wrap.querySelector("#spectral-editor-status").textContent;

    return {
      readouts,
      afterSlider,
      value,
      mode: state.mode,
      strength: state.strength,
      confirmed,
    };
  },

  /* the region comes back out of a chain document */
  async document() {
    const document_ = payload.document || null;
    const region = mod.regionFromDocument(document_, payload.code || "AUR_SPECTRALEDITOR");
    const wrap = makeStage();
    const editor = mod.createSpectralEditor({ container: wrap });
    editor.render(makeView(), { region, mode: "Attenuate", strength: 0.5 });
    return {
      region,
      drawn: editor.getRegion(),
      readout: editor.panel.querySelector(".se-readout").textContent,
    };
  },

  /* a session was loaded: the region is the module's own */
  async fromSession() {
    const region = payload.region || null;
    const wrap = makeStage();
    const transport = makeTransport();
    const editor = mod.createSpectralEditor({ container: wrap, transport });
    editor.setModuleIndex(payload.index === undefined ? 2 : payload.index);
    editor.setState({ region, regionApplied: true });
    return {
      region: editor.getRegion(),
      statusText: wrap.querySelector("#spectral-editor-status").textContent,
      label: editor.getState().label,
      kind: editor.getState().status.kind,
      calls: transport.calls.apply,
    };
  },

  /* the state block of a module */
  async state() {
    const out = {};
    for (const [key, value] of Object.entries(payload.blocks || {})) {
      out[key] = mod.regionFromState(value);
    }
    return out;
  },

  /* the transport over HTTP */
  async http() {
    const seen = [];
    const transport = mod.createHttpTransport({
      fetch: async (url, init) => {
        seen.push({ url, method: (init && init.method) || "GET", body: init && init.body });
        if (payload.route404 && String(url).indexOf("/region") >= 0) {
          return {
            ok: false,
            status: 404,
            statusText: "Not Found",
            text: async () => JSON.stringify({ error: "Not Found" }),
          };
        }
        return {
          ok: true,
          status: 200,
          statusText: "OK",
          text: async () => JSON.stringify(payload.answer || { ok: true }),
        };
      },
    });

    const out = { seen };
    out.index = await transport.findIndex();
    if (payload.apply !== false) {
      try {
        out.answer = await transport.applyRegion(2, { start: 1, end: 2, low: 100, high: 200 });
      } catch (err) {
        out.error = { message: err.message, status: err.status };
      }
    }
    if (payload.params) {
      out.params = await transport.readParams(2);
    }
    return out;
  },
};

const run = scenarios[scenario];
if (!run) {
  process.stderr.write(`unknown scenario: ${scenario}\n`);
  process.exit(2);
}
const result = await run(mod, payload);
process.stdout.write(JSON.stringify(result === undefined ? null : result));
"""


# --------------------------------------------------------------------------
# Helpers.
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    """The directory that holds the node driver of the scenarios."""
    if not shutil.which("node"):
        pytest.skip("node is not available on this machine")
    base = tmp_path_factory.mktemp("spectral_editor")
    (base / "dom_stub.mjs").write_text(DOM_STUB, encoding="utf-8")
    (base / "driver.mjs").write_text(DRIVER, encoding="utf-8")
    return base


def _run(driver: Path, scenario: str, payload: dict | None = None) -> dict:
    """Run one scenario of the driver and return what it printed."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available on this machine")
    proc = subprocess.run(
        [
            node,
            str(driver / "driver.mjs"),
            scenario,
            json.dumps(payload or {}),
            MODULE_JS.as_uri(),
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        timeout=120,
    )
    if proc.returncode != 0:
        pytest.fail(
            f"the node scenario {scenario!r} failed:\n{proc.stderr.strip()}\n{proc.stdout[:800]}"
        )
    try:
        return json.loads(proc.stdout or "null")
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        pytest.fail(
            f"the node scenario {scenario!r} printed no JSON ({exc}):\n{proc.stdout[:400]}"
        )


# --------------------------------------------------------------------------
# 1. The panel.
# --------------------------------------------------------------------------
def test_the_panel_offers_the_five_modes_of_the_module(driver):
    """The mode selector holds the five modes of the parameter."""
    data = _run(driver, "panel")
    assert data["options"] == MODES


def test_the_panel_shows_the_mode_the_strength_and_the_size(driver):
    """The readout names the mode, the strength and the size of the region."""
    data = _run(driver, "drawn")
    assert data["region"] == {"start": 1.0, "end": 2.0, "low": 1000.0, "high": 5000.0}
    assert "Gain" in data["readout"]
    assert "75 %" in data["readout"]
    assert "1.000 s" in data["readout"]
    assert "1000–5000 Hz" in data["readout"]
    assert data["drawn"] == [
        "fillRect",
        "strokeRect",
        "fillRect",
        "fillRect",
        "fillRect",
        "fillRect",
    ]


def test_a_fresh_editor_says_that_there_is_no_region(driver):
    """Without a region the panel asks for one."""
    data = _run(driver, "panel")
    assert "no region" in data["statusText"]
    assert data["statusRole"] == "status"


def test_a_region_that_was_not_sent_yet_is_not_called_applied(driver):
    """Before the route answered, the panel does not claim a repair."""
    data = _run(driver, "drawn")
    assert "not applied" in data["label"]
    assert "has not been sent to the module yet" in data["statusText"]
    assert "NOT applied" not in data["statusText"]


def test_the_panel_and_the_overlay_have_their_own_ids(driver):
    """The elements the overlay creates carry ids of their own."""
    data = _run(driver, "panel")
    assert data["overlayId"] == "spectral-editor-overlay"
    assert data["panelId"] == "spectral-editor-panel"


# --------------------------------------------------------------------------
# 2. The truth about the region.
# --------------------------------------------------------------------------
def test_the_region_is_not_applied_while_the_route_is_missing(driver):
    """The user is told that the rectangle repairs nothing."""
    data = _run(driver, "missingRoute")
    assert data["kind"] == "missing"
    assert "NOT applied" in data["statusText"]
    assert "no route" in data["statusText"]
    assert "not applied" in data["label"]
    assert data["statusColour"] == "var(--danger)"


def test_the_region_travels_to_the_module_with_its_index(driver):
    """The route is called with the index and the region in seconds/hertz."""
    data = _run(driver, "applied")
    assert len(data["calls"]) == 1
    assert data["calls"][0]["index"] == 2
    assert data["calls"][0]["region"] == {
        "start": 1.0,
        "end": 2.0,
        "low": 1000.0,
        "high": 5000.0,
    }


def test_the_panel_says_that_the_region_is_applied(driver):
    """After the route answered, the panel says so."""
    data = _run(driver, "applied")
    assert data["kind"] == "applied"
    assert "applied to module 2" in data["statusText"]
    assert "not applied" not in data["statusText"]
    assert data["statusColour"] == "var(--accent-2)"


def test_the_panel_shows_what_the_module_really_holds(driver):
    """The parameters of the module are read back and shown."""
    data = _run(driver, "applied")
    assert data["params"] == 1
    assert "Replace" in data["statusText"]
    assert "25 %" in data["statusText"]


def test_a_drag_on_the_rectangle_reaches_the_module(driver):
    """The end of a drag hands the new region to the module."""
    data = _run(driver, "drag")
    assert len(data["calls"]) == 1
    assert data["calls"][0]["index"] == 2
    # the rectangle was dragged 40 px to the right (0.5 s) and 20 px up
    # (1.2 kHz), so the whole rectangle moved with it
    region = data["region"]
    assert region["start"] == pytest.approx(1.5)
    assert region["end"] == pytest.approx(2.5)
    assert region["low"] == pytest.approx(2200.0)
    assert region["high"] == pytest.approx(6200.0)
    assert data["calls"][0]["region"] == region
    assert "applied" in data["statusText"]


# --------------------------------------------------------------------------
# 3. The state round trip.
# --------------------------------------------------------------------------
def _chain_document(region: dict) -> dict:
    """The chain document of a spectral editor that holds the region."""
    from aurcore.api import create_engine

    engine = create_engine(48_000, 2)
    handle = engine.add_module(CODE)
    handle.module.set_region(
        region["start"], region["end"], region["low"], region["high"]
    )
    return engine.serialize()


def test_the_module_keeps_the_region_and_the_document_carries_it():
    """The region is the state of the module and travels as a base64 block."""
    region = {"start": 1.0, "end": 2.0, "low": 100.0, "high": 500.0}
    document = _chain_document(region)
    step = document["steps"][0]
    assert step["code"] == CODE
    assert step["state"]["Type"] == "String"
    payload = base64.b64decode(step["state"]["Value"]).decode("utf-8")
    assert json.loads(payload) == {
        "start": 1.0,
        "end": 2.0,
        "low": 100.0,
        "high": 500.0,
    }


def test_the_interface_reads_the_region_out_of_the_document(driver):
    """The rectangle comes back after a save and a load of the session."""
    region = {"start": 1.25, "end": 2.5, "low": 250.0, "high": 4000.0}
    document = _chain_document(region)
    data = _run(driver, "document", {"document": document})
    assert data["region"] == region
    assert data["drawn"] == region
    assert "1.250 s" in data["readout"]


def test_the_interface_reads_the_region_out_of_a_session_document(driver):
    """A session document (the chain under a key) is read as well."""
    region = {"start": 0.5, "end": 1.5, "low": 500.0, "high": 2000.0}
    chain = _chain_document(region)
    document = {
        "format": "auralis.document",
        "version": 1,
        "chain": chain,
    }
    data = _run(driver, "document", {"document": document})
    assert data["region"] == region
    assert data["drawn"] == region


def test_a_document_without_a_region_yields_nothing(driver):
    """A chain without the spectral editor has no region to read."""
    data = _run(driver, "document", {"document": {"chain": {"steps": []}}})
    assert data["region"] is None
    assert data["drawn"] is None


def test_a_loaded_session_shows_the_region_as_applied(driver):
    """After a session was loaded the rectangle is the module's own."""
    region = {"start": 1.25, "end": 2.5, "low": 250.0, "high": 4000.0}
    data = _run(driver, "fromSession", {"region": region, "index": 2})
    assert data["region"] == region
    assert data["kind"] == "applied"
    assert "applied to module 2" in data["statusText"]
    assert "applied" in data["label"]
    # the region was not sent again — the module already held it
    assert data["calls"] == []


def test_the_state_block_is_read_in_both_shapes(driver):
    """The bare region and the region under a key are both read."""
    bare = base64.b64encode(
        json.dumps({"start": 1, "end": 2, "low": 3, "high": 4}).encode("utf-8")
    ).decode("ascii")
    nested = base64.b64encode(
        json.dumps({"region": {"start": 5, "end": 6, "low": 7, "high": 8}}).encode("utf-8")
    ).decode("ascii")
    data = _run(
        driver,
        "state",
        {"blocks": {"bare": bare, "nested": nested, "rubbish": "not base64 at all"}},
    )
    assert data["bare"] == {"start": 1.0, "end": 2.0, "low": 3.0, "high": 4.0}
    assert data["nested"] == {"start": 5.0, "end": 6.0, "low": 7.0, "high": 8.0}
    assert data["rubbish"] is None


# --------------------------------------------------------------------------
# 4. The modes and the strength.
# --------------------------------------------------------------------------
def test_every_mode_is_reachable_and_the_readout_follows(driver):
    """Each of the five modes can be chosen and shows up in the readout."""
    data = _run(driver, "modes")
    assert len(data["readouts"]) == 5
    for mode, readout in zip(MODES, data["readouts"]):
        assert readout.startswith(mode)
    assert data["mode"] == "Partials"


def test_the_strength_is_reachable_and_the_readout_follows(driver):
    """The slider changes the strength the module is told about."""
    data = _run(driver, "modes")
    assert "80 %" in data["afterSlider"]
    assert data["value"] == "80 %"
    assert data["strength"] == 0.8


def test_the_panel_names_what_the_module_holds(driver):
    """The confirmed values of the module are shown next to the status."""
    data = _run(driver, "modes")
    assert "the module holds" in data["confirmed"]
    assert "Attenuate" in data["confirmed"]


# --------------------------------------------------------------------------
# 5. The gestures that belong to the view.
# --------------------------------------------------------------------------
def test_a_gesture_outside_of_the_rectangle_reaches_the_view(driver):
    """The pan, the zoom and the selection of the view keep working."""
    data = _run(driver, "passThrough")
    assert data["outsideSeen"] == [["pointerdown", 400]]
    assert data["notStopped"] is True
    assert data["wheelSeen"] == [["wheel", 120]]


def test_a_gesture_on_the_rectangle_stays_with_the_editor(driver):
    """The overlay takes the gesture that is on the rectangle."""
    data = _run(driver, "passThrough")
    assert data["insideSeen"] == []
    assert data["stopped"] is True


def test_a_gesture_outside_of_the_rectangle_does_not_move_it(driver):
    """The rectangle is not touched by a gesture that is not on it."""
    data = _run(driver, "passThrough")
    assert data["regionAfter"] == {
        "start": 1.0,
        "end": 2.0,
        "low": 1000.0,
        "high": 5000.0,
    }


# --------------------------------------------------------------------------
# 6. The transport over HTTP.
# --------------------------------------------------------------------------
def test_the_transport_posts_the_region_to_the_route_of_the_chain(driver):
    """The body of the post is the region, the index is in the path."""
    data = _run(driver, "http", {"answer": {"ok": True, "index": 2}})
    assert data["seen"][0]["url"] == "/api/chain"
    assert data["seen"][1]["url"] == "/api/chain/2/region"
    assert data["seen"][1]["method"] == "POST"
    assert json.loads(data["seen"][1]["body"]) == {
        "start": 1,
        "end": 2,
        "low": 100,
        "high": 200,
    }


def test_the_transport_reports_a_missing_route(driver):
    """A 404 of the route is an error with the status of the answer."""
    data = _run(driver, "http", {"route404": True})
    assert data["error"]["status"] == 404
    assert "Not Found" in data["error"]["message"]


# --------------------------------------------------------------------------
# 7. The specification of the route.
# --------------------------------------------------------------------------
def _route_spec() -> str:
    """The text of the specification of the route."""
    for path in ROUTE_SPEC_CANDIDATES:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    pytest.fail(
        "the route the interface needs is not written down: none of "
        + ", ".join(str(path.name) for path in ROUTE_SPEC_CANDIDATES)
        + " is there"
    )


def test_the_route_is_specified_for_the_server_agent():
    """The route the interface needs is written down."""
    text = _route_spec()
    assert "POST /api/chain/{index}/region" in text
    for field in ("start", "end", "low", "high"):
        assert f"`{field}`" in text
    assert "seconds" in text and "hertz" in text
    for status in ("404", "400", "422"):
        assert status in text
    assert "set_region" in text


def test_the_specification_names_the_route_the_interface_calls():
    """The specification and the interface agree on the path."""
    text = _route_spec()
    source = MODULE_JS.read_text(encoding="utf-8")
    route = re.search(r'REGION_ROUTE\s*=\s*"([^"]+)"', source)
    assert route is not None
    assert route.group(1) in text


def test_the_specification_and_the_interface_agree_on_the_body():
    """The four fields of the body are the same on both sides."""
    text = _route_spec()
    source = MODULE_JS.read_text(encoding="utf-8")
    body = re.search(r"body: JSON\.stringify\(\{(.*?)\}\)", source, re.S)
    assert body is not None
    for field in ("start", "end", "low", "high"):
        assert re.search(rf"\b{field}:", body.group(1)), f"the body has no {field}"
        assert f"`{field}`" in text, f"the specification has no {field}"


# --------------------------------------------------------------------------
# 8. The hygiene of the file.
# --------------------------------------------------------------------------
def test_the_module_mentions_no_third_party_trademark():
    """The naming rule of the contract holds for the overlay as well.

    The two words are spelled in pieces on purpose: this file is part of the
    codebase, and the codebase must not contain a third party trademark even
    in the scanner that looks for one.
    """
    forbidden = ("r" + "x", "izo" + "tope")
    for path in (MODULE_JS, Path(__file__)):
        text = path.read_text(encoding="utf-8").lower()
        for token in forbidden:
            assert token not in text, f"{path.name} mentions a third party trademark"
