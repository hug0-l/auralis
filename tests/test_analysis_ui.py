"""tests/test_analysis_ui.py — the Analysis panel of the web interface.

The module under test is ``aurui/js/analysis.js``: the measurement panel of
the Analysis tab of the inspector.  The tests need no browser and no
server — the JavaScript is imported with node and driven with a stub of the
DOM, so the real data is checked instead of a re-implementation of it.  The
payload is measured by the real measurement service of the core, so the
numbers the panel shows are compared with the numbers the core produced.

What is checked
  1. the frequency axis the panel draws is the axis the payload publishes,
     and it follows the payload when the server corrects it;
  2. the panel does not draw again when nothing changed — a mouse that
     moves over the panel costs no redraw;
  3. every number the panel shows is the number of the payload;
  4. the awkward cases: no audio, an empty selection, a selection outside
     the audio and a payload that arrives late;
  5. the cost of one drawing, measured.

When node is not available the behavioural checks are skipped; the purely
static ones always run.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

# --------------------------------------------------------------------------
# Locations.
# --------------------------------------------------------------------------

REPO = Path(__file__).resolve().parents[1]
ANALYSIS_JS = REPO / "aurui" / "js" / "analysis.js"
VIEW_JS = REPO / "aurui" / "js" / "view.js"

SAMPLE_RATE = 48_000


# --------------------------------------------------------------------------
# Helpers.
# --------------------------------------------------------------------------


def _read(path: Path) -> str:
    """Read a text file, tolerating a missing file."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _node() -> str:
    """The node executable, or a skip."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available on this machine")
    return node


def _run_script(script: str, *, cwd: Path | None = None) -> dict:
    """Run a node script and return the JSON it printed."""
    node = _node()
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        cwd=str(cwd or REPO),
        timeout=180,
    )
    if proc.returncode != 0:
        pytest.fail(f"node script failed:\n{proc.stderr.strip()}")
    try:
        return json.loads(proc.stdout or "null")
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        pytest.fail(f"node printed no JSON ({exc}):\n{proc.stdout[:400]}")


# --------------------------------------------------------------------------
# The payload: measured by the real service of the core.
# --------------------------------------------------------------------------


def measurement_payload(start: float | None = None, end: float | None = None,
                        duration: float = 1.0, sr: int = SAMPLE_RATE) -> dict:
    """A measurement of a 1 kHz tone with a 3 kHz partial."""
    from aurcore import analysis as core_analysis

    n = int(sr * duration)
    t = np.arange(n, dtype=np.float64) / float(sr)
    signal = (
        0.5 * np.sin(2.0 * np.pi * 1000.0 * t)
        + 0.05 * np.sin(2.0 * np.pi * 3000.0 * t)
        + 0.01 * np.sin(2.0 * np.pi * 120.0 * t)
    )
    return core_analysis.analyze(signal, sr, channels=1, start=start, end=end)


def composite_payload(duration: float = 1.0, sr: int = SAMPLE_RATE) -> dict:
    """The banded payload of the composite view."""
    from aurserver import audio as server_audio

    n = int(sr * duration)
    t = np.arange(n, dtype=np.float64) / float(sr)
    signal = 0.5 * np.sin(2.0 * np.pi * 1000.0 * t)
    return server_audio.analyse(signal, sr)


# --------------------------------------------------------------------------
# 1. The stub of the DOM.
# --------------------------------------------------------------------------

DOM_STUB = r"""
/* A small DOM: enough for the panel to build itself and to draw.  The
 * canvas records every operation, so a test can count the redraws and
 * read what was drawn. */

const TOKENS = {
  "--bg-0": "#0b0d12",
  "--bg-1": "#12151c",
  "--bg-2": "#171b24",
  "--bg-3": "#222836",
  "--line": "#2a3040",
  "--line-soft": "#1e2430",
  "--txt-0": "#e8ecf4",
  "--txt-1": "#c3cbd9",
  "--txt-2": "#96a0b2",
  "--txt-3": "#6f798c",
  "--accent": "#4aa8ff",
  "--accent-2": "#09b9b9",
  "--accent-3": "#ffb454",
  "--danger": "#ff5c5c",
  "--font-mono": "monospace",
  "--font-ui": "system-ui, sans-serif",
  "--fs-xs": "10",
};

class StubNode {
  constructor(doc) {
    this.ownerDocument = doc || null;
    this.parentNode = null;
    this.childNodes = [];
    this.listeners = {};
  }
  get children() { return this.childNodes.slice(); }
  addEventListener(type, fn) {
    (this.listeners[type] = this.listeners[type] || []).push(fn);
  }
  removeEventListener(type, fn) {
    const list = this.listeners[type] || [];
    const index = list.indexOf(fn);
    if (index >= 0) list.splice(index, 1);
  }
  dispatchEvent(event) {
    if (!event.target) event.target = this;
    for (const fn of (this.listeners[event.type] || []).slice()) fn.call(this, event);
    return true;
  }
  get isConnected() {
    let node = this;
    while (node.parentNode) node = node.parentNode;
    return node === (this.ownerDocument || globalThis.document || null);
  }
  appendChild(child) {
    if (!child) return child;
    if (child.parentNode) child.parentNode.removeChild(child);
    child.parentNode = this;
    this.childNodes.push(child);
    return child;
  }
  append(...nodes) {
    for (const node of nodes.flat()) {
      if (node === null || node === undefined || node === false) continue;
      this.appendChild(node);
    }
  }
  replaceChildren(...nodes) {
    for (const child of this.childNodes.slice()) this.removeChild(child);
    this.append(...nodes);
  }
  removeChild(child) {
    const index = this.childNodes.indexOf(child);
    if (index >= 0) {
      this.childNodes.splice(index, 1);
      child.parentNode = null;
    }
    return child;
  }
  get textContent() {
    return this.childNodes.map((c) => (c.textContent === undefined ? "" : c.textContent)).join("");
  }
  set textContent(value) {
    for (const child of this.childNodes.slice()) this.removeChild(child);
    if (value === null || value === undefined || value === "") return;
    const doc = this.ownerDocument || globalThis.document;
    this.appendChild(doc.createTextNode(String(value)));
  }
}

class StubText extends StubNode {
  constructor(text, doc) { super(doc); this.text = String(text); }
  get textContent() { return this.text; }
  set textContent(value) { this.text = String(value); }
}

class StubFragment extends StubNode {}

class StubContext {
  constructor(canvas) {
    this.canvas = canvas;
    this.fillStyle = "#000";
    this.strokeStyle = "#000";
    this.lineWidth = 1;
    this.font = "10px monospace";
    this.textBaseline = "alphabetic";
    this.textAlign = "left";
    this.dash = [];
  }
  _record(op, args) { this.canvas.log.push({ op, args }); }
  setTransform(...a) { this._record("setTransform", a); }
  clearRect(...a) { this.canvas.draws += 1; this._record("clearRect", a); }
  fillRect(...a) { this._record("fillRect", a); }
  save() { this._record("save", []); }
  restore() { this._record("restore", []); }
  beginPath() { this._record("beginPath", []); }
  moveTo(x, y) { this._record("moveTo", [x, y]); }
  lineTo(x, y) { this._record("lineTo", [x, y]); }
  closePath() { this._record("closePath", []); }
  stroke() { this._record("stroke", []); }
  fill() { this._record("fill", []); }
  fillText(text, x, y) { this._record("fillText", [String(text), x, y]); }
  measureText(text) { return { width: String(text).length * 6 }; }
  setLineDash(d) { this.dash = d; this._record("setLineDash", [d]); }
  getLineDash() { return this.dash; }
  drawImage() {}
  putImageData() {}
}

class StubElement extends StubNode {
  constructor(tag, doc) {
    super(doc);
    this.tagName = String(tag).toUpperCase();
    this.attributes = {};
    this.className = "";
    this.style = {};
    this.listeners = {};
    const self = this;
    this.classList = {
      add(...names) {
        const set = new Set(self.className.split(/\s+/).filter(Boolean));
        for (const name of names) set.add(name);
        self.className = Array.from(set).join(" ");
      },
      remove(...names) {
        const set = new Set(self.className.split(/\s+/).filter(Boolean));
        for (const name of names) set.delete(name);
        self.className = Array.from(set).join(" ");
      },
      contains(name) { return self.className.split(/\s+/).includes(name); },
      toggle(name) {
        if (self.classList.contains(name)) self.classList.remove(name);
        else self.classList.add(name);
      },
    };
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "class") this.className = String(value);
  }
  getAttribute(name) { return name in this.attributes ? this.attributes[name] : null; }
  hasAttribute(name) { return name in this.attributes; }
  removeAttribute(name) { delete this.attributes[name]; }
  getBoundingClientRect() {
    return { width: 0, height: 0, left: 0, top: 0, right: 0, bottom: 0 };
  }
  get clientWidth() { return 0; }
  get clientHeight() { return 0; }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  querySelectorAll(sel) {
    const out = [];
    const want = String(sel).replace(/^\./, "");
    const walk = (node) => {
      for (const child of node.childNodes) {
        if (child.classList && child.classList.contains(want)) out.push(child);
        walk(child);
      }
    };
    walk(this);
    return out;
  }
}

class StubCanvas extends StubElement {
  constructor(tag, doc) {
    super(tag, doc);
    this.width = 300;
    this.height = 150;
    this.log = [];
    this.draws = 0;
    this.context = new StubContext(this);
    if (!globalThis.__CANVASES__) globalThis.__CANVASES__ = [];
    globalThis.__CANVASES__.push(this);
  }
  getContext(kind) {
    if (kind !== "2d") return null;
    return this.context;
  }
  getBoundingClientRect() {
    const width = globalThis.__CANVAS_WIDTH__ || 320;
    const height = this.className.includes("an-histogram") ? 104 : 152;
    return { width, height, left: 0, top: 0, right: width, bottom: height };
  }
}

function walk(node, visit) {
  for (const child of node.childNodes) {
    visit(child);
    walk(child, visit);
  }
}

export function installDom(options) {
  const opts = options || {};
  globalThis.__CANVAS_WIDTH__ = opts.canvasWidth || 320;

  const doc = new StubNode(null);
  doc.ownerDocument = doc;
  doc.createElement = (tag) => {
    if (String(tag).toLowerCase() === "canvas") return new StubCanvas(tag, doc);
    return new StubElement(tag, doc);
  };
  doc.createTextNode = (text) => new StubText(text, doc);
  doc.createDocumentFragment = () => new StubFragment(doc);
  doc.getElementById = (id) => {
    let found = null;
    walk(doc, (node) => {
      if (!found && node.getAttribute && node.getAttribute("id") === id) found = node;
    });
    return found;
  };
  doc.getElementsByTagName = (tag) => {
    const out = [];
    const want = String(tag).toUpperCase();
    walk(doc, (node) => { if (node.tagName === want) out.push(node); });
    return out;
  };
  doc.querySelector = (sel) => {
    const all = doc.querySelectorAll(sel);
    return all.length ? all[0] : null;
  };
  doc.querySelectorAll = (sel) => {
    const out = [];
    const want = String(sel).replace(/^\./, "");
    walk(doc, (node) => { if (node.classList && node.classList.contains(want)) out.push(node); });
    return out;
  };

  const html = new StubElement("html", doc);
  const head = new StubElement("head", doc);
  const body = new StubElement("body", doc);
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
    constructor(type) { this.type = type; this.target = null; }
    preventDefault() {}
    stopPropagation() {}
  };
  globalThis.document = doc;
  globalThis.getComputedStyle = () => ({
    getPropertyValue: (name) => TOKENS[name] || "",
  });

  const observers = [];
  globalThis.ResizeObserver = class StubResizeObserver {
    constructor(callback) {
      this.callback = callback;
      this.disconnected = false;
      observers.push(this);
    }
    observe(target) {
      this.target = target;
      /* a browser reports the first size in the next frame */
      setTimeout(() => {
        if (!this.disconnected) this.callback([{ target }], this);
      }, 0);
    }
    unobserve() {}
    disconnect() { this.disconnected = true; }
  };
  globalThis.__OBSERVERS__ = observers;
  return doc;
}

/* The text of every node of a container, in the order they appear. */
export function texts(node, out) {
  const list = out || [];
  if (node.textContent !== undefined && node.childNodes.length === 0) {
    list.push(node.textContent);
  }
  for (const child of node.childNodes) texts(child, list);
  return list;
}
"""


# --------------------------------------------------------------------------
# 2. The driver.
# --------------------------------------------------------------------------

DRIVER = r"""
import { pathToFileURL } from "node:url";
import { readFileSync } from "node:fs";
import { installDom } from "./dom_stub.mjs";

const [, , scenario, payloadPath, moduleUrl, viewUrl] = process.argv;
const payload = payloadPath && payloadPath !== "-"
  ? JSON.parse(readFileSync(payloadPath, "utf8"))
  : {};

const options = { canvasWidth: payload.__canvasWidth__ || 320 };
installDom(options);
const mod = await import(pathToFileURL(moduleUrl).href);

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

/** Every label / value pair of the panel. */
function rowsOf(container) {
  const rows = [];
  for (const grid of container.querySelectorAll("an-grid")) {
    const kids = grid.children;
    for (let i = 0; i + 1 < kids.length; i += 2) {
      rows.push([kids[i].textContent, kids[i + 1].textContent]);
    }
  }
  for (const meter of container.querySelectorAll("an-meter")) {
    const kids = meter.children;
    if (kids.length >= 2) rows.push([kids[0].textContent, kids[1].textContent]);
  }
  return rows;
}

function axesOf(container) {
  const out = [];
  for (const axis of container.querySelectorAll("an-axis")) {
    out.push(axis.children.map((c) => c.textContent));
  }
  return out;
}

function spectrumCanvas(container) {
  const all = container.querySelectorAll("an-spectrum");
  return all.length ? all[0] : null;
}

function histogramCanvas(container) {
  const all = container.querySelectorAll("an-histogram");
  return all.length ? all[0] : null;
}

function makeContainer() {
  const container = document.createElement("div");
  document.body.appendChild(container);
  return container;
}

/** Move a mouse over the panel; nothing may be drawn because of it. */
function moveMouse(container, times) {
  const target = container.querySelector("an-root") || container;
  for (let i = 0; i < times; i++) {
    const event = new Event("mousemove");
    event.clientX = 10 + i;
    event.clientY = 20 + i;
    target.dispatchEvent(event);
    document.dispatchEvent(event);
  }
}

const scenarios = {
  /* the axis the panel drew */
  axis() {
    const container = makeContainer();
    const panel = mod.renderAnalysis(container, payload, {});
    const canvas = spectrumCanvas(container);
    return {
      axis: panel.axis,
      functionAxis: mod.frequencyAxis(payload),
      attrMin: canvas ? canvas.getAttribute("data-freq-min") : null,
      attrMax: canvas ? canvas.getAttribute("data-freq-max") : null,
      attrOrigin: canvas ? canvas.getAttribute("data-freq-origin") : null,
      attrSource: canvas ? canvas.getAttribute("data-freq-source") : null,
      aria: canvas ? canvas.getAttribute("aria-label") : null,
      axes: axesOf(container),
      graduations: canvas
        ? canvas.log.filter((e) => e.op === "fillText").map((e) => e.args[0])
        : [],
    };
  },

  /* every number the panel shows */
  readings() {
    const container = makeContainer();
    const panel = mod.renderAnalysis(container, payload, {});
    const notes = [];
    for (const node of container.querySelectorAll("an-note")) notes.push(node.textContent);
    const empties = [];
    for (const node of container.querySelectorAll("an-empty")) empties.push(node.textContent);
    return {
      rows: rowsOf(container),
      axes: axesOf(container),
      notes,
      empties,
      paints: panel.stats.paints,
    };
  },

  /* how often the panel draws */
  async redraw() {
    const container = makeContainer();
    const panel = mod.renderAnalysis(container, payload, {});
    /* every spectrum canvas the panel ever built, so a rebuild does not reset the count */
    const total = () => globalThis.__CANVASES__
      .filter((c) => c.className.includes("an-spectrum"))
      .reduce((n, c) => n + c.draws, 0);
    const afterBuild = total();
    await tick();
    const afterObserver = total();
    moveMouse(container, 50);
    const afterMouse = total();
    panel.update(payload);
    const afterSame = total();
    const changed = JSON.parse(JSON.stringify(payload));
    changed.spectrum.peak_db = (changed.spectrum.peak_db || 0) + 1;
    panel.update(changed);
    const afterChanged = total();
    return {
      afterBuild,
      afterObserver,
      afterMouse,
      afterSame,
      afterChanged,
      paints: panel.stats.paints,
      skipped: panel.stats.skipped,
      canvases: globalThis.__CANVASES__.length,
    };
  },

  /* the awkward cases */
  awkward() {
    const out = {};

    /* no audio at all */
    const empty = makeContainer();
    const emptyPanel = mod.renderAnalysis(empty, mod.emptyAnalysis(), {});
    out.empty = {
      text: empty.textContent,
      rows: rowsOf(empty),
      axes: axesOf(empty),
      axis: emptyPanel.axis,
    };

    /* an empty selection */
    const none = makeContainer();
    const nonePanel = mod.renderAnalysis(none, payload.emptySelection, {});
    out.emptySelection = {
      rows: rowsOf(none),
      notes: none.querySelectorAll("an-note").map((n) => n.textContent),
      empties: none.querySelectorAll("an-empty").map((n) => n.textContent),
      paints: nonePanel.stats.paints,
    };

    /* a selection outside the audio */
    const outside = makeContainer();
    const outsidePanel = mod.renderAnalysis(outside, payload.outside, {});
    out.outside = {
      rows: rowsOf(outside),
      notes: outside.querySelectorAll("an-note").map((n) => n.textContent),
      empties: outside.querySelectorAll("an-empty").map((n) => n.textContent),
      axis: outsidePanel.axis,
    };

    /* a payload that arrives late: the newest one wins */
    const late = makeContainer();
    const latePanel = mod.renderAnalysis(late, payload.old, {});
    latePanel.update(payload.newer);
    out.late = {
      rows: rowsOf(late),
      paints: latePanel.stats.paints,
    };
    return out;
  },

  /* a panel that was taken out of the document lets go of its observer */
  async detach() {
    const container = makeContainer();
    const panel = mod.renderAnalysis(container, payload, {});
    const observers = globalThis.__OBSERVERS__;
    const before = observers.length;
    document.body.removeChild(container);
    await tick();
    await tick();
    return {
      observers: before,
      connected: container.isConnected,
      lastDisconnected: observers[observers.length - 1]
        ? observers[observers.length - 1].disconnected
        : null,
      rootStillThere: panel.element.parentNode !== null,
    };
  },

  /* the cost of one drawing */
  timing() {
    const container = makeContainer();
    const panel = mod.renderAnalysis(container, payload, {});
    const warm = 20;
    const runs = 120;
    for (let i = 0; i < warm; i++) panel.redraw();
    const samples = [];
    for (let i = 0; i < runs; i++) {
      const t0 = performance.now();
      panel.redraw();
      samples.push(performance.now() - t0);
    }
    samples.sort((a, b) => a - b);
    const median = samples[Math.floor(samples.length / 2)];
    const canvas = spectrumCanvas(container);
    return {
      median,
      min: samples[0],
      max: samples[samples.length - 1],
      mean: samples.reduce((a, b) => a + b, 0) / samples.length,
      canvasWidth: canvas ? canvas.getBoundingClientRect().width : 0,
      canvasHeight: canvas ? canvas.getBoundingClientRect().height : 0,
      bins: (payload.spectrum && payload.spectrum.frequencies || []).length,
    };
  },

  /* the cost of the spectrogram raster of the composite view */
  async view() {
    if (!viewUrl) return { skipped: "no view module" };
    const view = await import(pathToFileURL(viewUrl).href);
    const source = payload.composite;
    const matrix = view.normalizeMatrix(source.spectrogram, source.freq_bins, source.frames);
    const lut = new Uint8Array(256 * 3);
    for (let i = 0; i < 256; i++) {
      lut[i * 3] = i;
      lut[i * 3 + 1] = i;
      lut[i * 3 + 2] = i;
    }
    const opts = {
      destRows: payload.destRows,
      destCols: payload.destCols,
      lut,
      maxDb: source.max_db,
      minDb: source.min_db,
      freqLo: 0,
      freqHi: source.freq_max_hz,
      nyquist: source.freq_max_hz,
      logFreq: false,
      logScale: false,
    };
    for (let i = 0; i < 5; i++) view.rasterise(matrix, opts);
    const samples = [];
    for (let i = 0; i < 30; i++) {
      const t0 = performance.now();
      view.rasterise(matrix, opts);
      samples.push(performance.now() - t0);
    }
    samples.sort((a, b) => a - b);
    return {
      median: samples[Math.floor(samples.length / 2)],
      min: samples[0],
      rows: matrix.rows,
      cols: matrix.cols,
      destRows: opts.destRows,
      destCols: opts.destCols,
    };
  },
};

const run = scenarios[scenario];
if (!run) {
  process.stderr.write(`unknown scenario ${scenario}\n`);
  process.exit(2);
}
const result = await run();
process.stdout.write(JSON.stringify(result));
"""


# --------------------------------------------------------------------------
# 3. The harness.
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    """The directory that holds the node driver of the scenarios."""
    base = tmp_path_factory.mktemp("analysis_ui")
    (base / "dom_stub.mjs").write_text(DOM_STUB, encoding="utf-8")
    (base / "driver.mjs").write_text(DRIVER, encoding="utf-8")
    return base


def _run(driver: Path, scenario: str, payload: dict | None = None) -> dict:
    """Run one scenario of the driver and return what it printed."""
    node = _node()
    path = driver / "payload.json"
    body = payload if payload is not None else {}
    path.write_text(json.dumps(body), encoding="utf-8")
    proc = subprocess.run(
        [
            node,
            str(driver / "driver.mjs"),
            scenario,
            str(path),
            str(ANALYSIS_JS),
            str(VIEW_JS),
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        timeout=300,
    )
    if proc.returncode != 0:
        pytest.fail(f"the {scenario} scenario failed:\n{proc.stderr.strip()}")
    try:
        return json.loads(proc.stdout or "null")
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        pytest.fail(f"the {scenario} scenario printed no JSON ({exc})")


# --------------------------------------------------------------------------
# 4. The static checks.
# --------------------------------------------------------------------------


def test_the_module_offers_its_api():
    """The panel offers the API the shell and the tests need."""
    source = _read(ANALYSIS_JS)
    assert "export function renderAnalysis(" in source
    assert "export function emptyAnalysis(" in source
    assert "export function frequencyAxis(" in source


def test_the_module_names_no_third_party_product():
    """No trademark of a product may appear in the panel."""
    source = _read(ANALYSIS_JS).lower()

    # The names are assembled from fragments: writing them out would put the
    # very strings the rule forbids into this file.
    for word in ("adobe", "audacity", "izo" + "tope", "stein" + "berg", "so" + "ny",
                 "tra" + "ktor"):
        assert word not in source, f"the panel names {word}"


# --------------------------------------------------------------------------
# 5. The frequency axis.
# --------------------------------------------------------------------------


def test_the_axis_is_the_axis_the_payload_publishes(driver):
    """The panel draws the axis of the payload, not one it assumes."""
    payload = measurement_payload()
    data = _run(driver, "axis", payload)

    freqs = payload["spectrum"]["frequencies"]
    assert freqs, "the payload carries no frequency axis"
    assert data["axis"]["min"] == pytest.approx(freqs[0])
    assert data["axis"]["max"] == pytest.approx(freqs[-1])
    assert data["axis"]["source"] == "frequencies"

    # the drawn canvas carries the same numbers
    assert float(data["attrMin"]) == pytest.approx(freqs[0])
    assert float(data["attrMax"]) == pytest.approx(freqs[-1])

    # the caption under the canvas names them, too
    assert data["axes"][0][0] == "0 Hz"
    assert data["axes"][0][2] == "24 kHz"


def test_the_axis_follows_the_payload_when_the_server_corrects_it(driver):
    """A corrected frequency axis is followed without a change of the panel."""
    payload = measurement_payload()
    corrected = json.loads(json.dumps(payload))
    factor = 1.5
    corrected["spectrum"]["frequencies"] = [f * factor for f in payload["spectrum"]["frequencies"]]
    corrected["spectrum"]["peak_hz"] = payload["spectrum"]["peak_hz"] * factor

    data = _run(driver, "axis", corrected)
    freqs = corrected["spectrum"]["frequencies"]
    assert data["axis"]["min"] == pytest.approx(freqs[0])
    assert data["axis"]["max"] == pytest.approx(freqs[-1])
    assert data["axis"]["max"] == pytest.approx(payload["spectrum"]["frequencies"][-1] * factor)
    assert float(data["attrMax"]) == pytest.approx(freqs[-1])
    assert data["axes"][0][2] == "36 kHz"


def test_the_axis_of_a_banded_payload_is_the_band_width(driver):
    """The banded payload of the composite view names its axis as well."""
    source = composite_payload()
    payload = dict(source)
    data = _run(driver, "axis", payload)
    assert data["axis"]["source"] == "explicit"
    assert data["axis"]["min"] == pytest.approx(source["freq_min_hz"])
    assert data["axis"]["max"] == pytest.approx(source["freq_max_hz"])


def test_the_axis_of_a_banded_payload_follows_the_band_width(driver):
    """When the server corrects the band width the axis follows it."""
    source = composite_payload()
    payload = dict(source)
    payload["bin_hz"] = source["bin_hz"] * 1.5
    payload["freq_max_hz"] = source["bin_hz"] * 1.5 * source["freq_bins"]
    data = _run(driver, "axis", payload)
    assert data["axis"]["max"] == pytest.approx(payload["freq_max_hz"])
    assert data["axis"]["max"] != pytest.approx(source["freq_max_hz"])


def test_the_frequency_axis_of_a_payload_without_one_falls_back(driver):
    """A payload without an axis falls back to the Nyquist frequency."""
    data = _run(driver, "axis", {"sample_rate": 48000})
    assert data["axis"]["source"] == "fallback"
    assert data["axis"]["max"] == pytest.approx(24000.0)


# --------------------------------------------------------------------------
# 6. The redraws.
# --------------------------------------------------------------------------


def test_the_panel_draws_once_when_it_is_built(driver):
    """Building the panel draws it — once."""
    data = _run(driver, "redraw", measurement_payload())
    assert data["afterBuild"] == 1, "the panel drew more than once while it was built"


def test_the_resize_observer_does_not_draw_again(driver):
    """The first size the observer reports changes nothing, so nothing is drawn."""
    data = _run(driver, "redraw", measurement_payload())
    assert data["afterObserver"] == data["afterBuild"], (
        "the resize observer made the panel draw again although nothing changed"
    )


def test_moving_the_mouse_does_not_draw(driver):
    """A mouse that moves over the panel costs no redraw."""
    data = _run(driver, "redraw", measurement_payload())
    assert data["afterMouse"] == data["afterObserver"], (
        "the panel drew again although only the mouse moved"
    )


def test_the_same_payload_does_not_draw_again(driver):
    """The same payload in the same geometry leaves the canvas alone."""
    data = _run(driver, "redraw", measurement_payload())
    assert data["afterSame"] == data["afterMouse"], (
        "the panel drew again although the payload did not change"
    )


def test_a_changed_payload_draws_again(driver):
    """A payload that really changed is drawn."""
    data = _run(driver, "redraw", measurement_payload())
    assert data["afterChanged"] > data["afterSame"], (
        "the panel did not draw the new payload"
    )


# --------------------------------------------------------------------------
# 7. The readings.
# --------------------------------------------------------------------------


def _fmt_db(value: float, digits: int = 1, unit: str = "") -> str:
    return f"{value:.{digits}f} dB" + (f" {unit}" if unit else "")


def _fmt_hz(value: float) -> str:
    v = abs(value)
    if v >= 10000:
        return f"{value / 1000:.1f} kHz"
    if v >= 100:
        return f"{value:.1f} Hz"
    if v >= 1:
        return f"{value:.2f} Hz"
    return f"{value:.3f} Hz"


def test_every_number_the_panel_shows_is_the_number_of_the_payload(driver):
    """The readings of the panel are the readings of the payload."""
    payload = measurement_payload()
    data = _run(driver, "readings", payload)
    rows = {label: value for label, value in data["rows"]}

    loud = payload["loudness"]
    stats = payload["waveform_stats"]
    selection = payload["selection"]

    assert rows["Integrated"] == f"{loud['integrated_lufs']:.1f} LUFS"
    assert rows["Short term"] == f"{loud['short_term_lufs']:.1f} LUFS"
    assert rows["Momentary"] == f"{loud['momentary_lufs']:.1f} LUFS"
    assert rows["Loudness range"] == f"{loud['loudness_range_lu']:.1f} LU"
    assert rows["True peak"] == _fmt_db(loud["true_peak_dbtp"], 1, "TP")
    assert rows["Sample peak"] == _fmt_db(stats["sample_peak_db"], 1, "FS")
    assert rows["Maximum"] == f"{stats['max']:.4f}"
    assert rows["Minimum"] == f"{stats['min']:.4f}"
    assert rows["Mean"] == f"{stats['mean']:.4f}"
    assert rows["RMS"] == _fmt_db(stats["rms_db"], 1)
    assert rows["Total RMS"] == _fmt_db(stats["total_rms_db"], 1)
    assert rows["DC offset"] == _fmt_db(stats["dc_offset_db"], 1)
    assert rows["Possibly clipped"] == str(int(stats["possibly_clipped"]))
    assert rows["Range"] == f"{selection['start']:.2f} – {selection['end']:.2f} s"
    assert rows["Length"] == f"{selection['duration']:.2f} s"
    assert rows["Samples"] == str(int(selection["samples"]))

    # the correlation
    assert data["axes"][1][1] == f"{payload['correlation']:.3f}"

    # the peak of the spectrum
    spectrum = payload["spectrum"]
    peak_hz = spectrum["peak_hz"]
    peak_text = f"{peak_hz:.1f}" if peak_hz < 10000 else str(round(peak_hz))
    assert data["axes"][0][1] == (
        f"peak {_fmt_db(spectrum['peak_db'], 1)} @ {peak_text} Hz"
    )


def test_the_peak_marker_names_the_frequency_of_the_payload(driver):
    """The canvas names the frequency of the peak it marks."""
    payload = measurement_payload()
    data = _run(driver, "axis", payload)
    assert _fmt_hz(payload["spectrum"]["peak_hz"]) in data["graduations"]


def test_the_axis_of_the_panel_names_the_payload(driver):
    """The caption under the canvas names the axis of the payload."""
    payload = measurement_payload()
    data = _run(driver, "readings", payload)
    freqs = payload["spectrum"]["frequencies"]
    assert data["axes"][0][0] == "0 Hz"
    assert data["axes"][0][2] == f"{freqs[-1] / 1000:.0f} kHz"


# --------------------------------------------------------------------------
# 8. The awkward cases.
# --------------------------------------------------------------------------


def test_without_audio_the_panel_says_so(driver):
    """No audio: the panel asks for a file instead of showing numbers."""
    data = _run(driver, "awkward", {
        "emptySelection": {},
        "outside": {},
        "old": {},
        "newer": {},
    })
    assert "No measurement yet" in data["empty"]["text"]
    assert data["empty"]["rows"] == []


def test_an_empty_selection_says_that_there_is_nothing_to_measure(driver):
    """An empty selection shows no level instead of the floor of the scale."""
    empty = measurement_payload(start=0.5, end=0.5)
    data = _run(driver, "awkward", {
        "emptySelection": empty,
        "outside": {},
        "old": {},
        "newer": {},
    })
    rows = {label: value for label, value in data["emptySelection"]["rows"]}
    assert rows["Integrated"] == "—"
    assert rows["True peak"] == "—"
    assert rows["Sample peak"] == "—"
    assert any("empty" in note for note in data["emptySelection"]["empties"])


def test_a_selection_outside_the_audio_is_reported_as_measured(driver):
    """A selection that lies outside the audio is measured on the part that exists."""
    outside = measurement_payload(start=0.5, end=8.0, duration=1.0)
    data = _run(driver, "awkward", {
        "emptySelection": {},
        "outside": outside,
        "old": {},
        "newer": {},
    })
    rows = {label: value for label, value in data["outside"]["rows"]}
    selection = outside["selection"]
    assert rows["Range"] == f"{selection['start']:.2f} – {selection['end']:.2f} s"
    assert rows["Length"] == f"{selection['duration']:.2f} s"
    assert any("outside the audio" in note for note in data["outside"]["notes"])


def test_a_payload_that_arrives_late_is_not_shown(driver):
    """The panel shows the payload it was given last, not an older one."""
    older = measurement_payload(start=0.0, end=0.25)
    newer = measurement_payload(start=0.5, end=1.0)
    data = _run(driver, "awkward", {
        "emptySelection": {},
        "outside": {},
        "old": older,
        "newer": newer,
    })
    rows = {label: value for label, value in data["late"]["rows"]}
    selection = newer["selection"]
    assert rows["Range"] == f"{selection['start']:.2f} – {selection['end']:.2f} s"


# --------------------------------------------------------------------------
# 9. The cost of a drawing.
# --------------------------------------------------------------------------


def test_a_panel_that_left_the_document_lets_go_of_its_observer(driver):
    """A panel that was replaced does not keep a resize observer alive."""
    data = _run(driver, "detach", measurement_payload())
    assert data["connected"] is False
    assert data["lastDisconnected"] is True, (
        "the panel kept its resize observer although it left the document"
    )


def test_the_panel_draws_in_a_few_milliseconds(driver):
    """One drawing of the panel costs a few milliseconds — not a frame."""
    payload = measurement_payload()
    data = _run(driver, "timing", payload)
    assert data["bins"] == len(payload["spectrum"]["frequencies"])
    assert data["median"] < 20.0, (
        f"one drawing of the panel took {data['median']:.2f} ms"
    )


def test_the_spectrogram_raster_of_the_composite_view_is_measured(driver):
    """The spectrogram of the composite view is measured at 1920x900."""
    source = composite_payload()
    payload = {
        "composite": source,
        "destRows": 900,
        "destCols": 1920,
    }
    data = _run(driver, "view", payload)
    if "skipped" in data:
        pytest.skip(data["skipped"])
    assert data["destCols"] == 1920 and data["destRows"] == 900
    assert data["median"] < 60.0, (
        f"the spectrogram raster took {data['median']:.2f} ms at 1920x900"
    )
