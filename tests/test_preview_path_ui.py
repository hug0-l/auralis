"""tests/test_preview_path_ui.py — the path of the live preview in the interface.

The product has two render paths: the file render uses the *full* profile and
the live preview uses the *fast* profile, so the preview is not the file.  The
server says so — the ``ready`` and the ``profile`` message carry ``profile``,
``profile_label``, ``profile_report``, ``processing_mode``, ``latency_samples``
and ``block_size`` — and the interface has to say it too.

The module under test is ``aurui/js/path_report.js``: the chip of the status
bar and the disclosure that follows it.  The tests need no browser and no
server: the JavaScript is imported with node and driven with a stub of the
DOM, so the real data is checked instead of a re-implementation of it.

What is checked
  1. the status bar carries the chip of the path and the disclosure;
  2. the chip says which path plays — the words of the server, in the
     language of the moment;
  3. the disclosure says what the path changed: the module, the parameter and
     the value on both sides (the one the file gets and the one the preview
     gets);
  4. a latency that the server did not report is not shown: the browser
     computes nothing;
  5. the user can ask for the other path, and the client asks the server for
     it in the ``configure`` message;
  6. the interface never calls the preview the file;
  7. the three locales carry the new strings.

When node is not available the behavioural checks are skipped; the purely
static ones always run.
"""

from __future__ import annotations

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
UI_DIR = REPO / "aurui"
JS_DIR = UI_DIR / "js"
CSS_DIR = UI_DIR / "css"
INDEX_HTML = UI_DIR / "index.html"
PATH_JS = JS_DIR / "path_report.js"
APP_JS = JS_DIR / "app.js"
KEYMAP_JS = JS_DIR / "keymap.js"
I18N_JS = JS_DIR / "i18n.js"


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


def _run_script(script: str) -> dict:
    """Run a node script and return the JSON it printed."""
    node = _node()
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        timeout=180,
    )
    if proc.returncode != 0:
        pytest.fail(f"node script failed:\n{proc.stderr.strip()}")
    try:
        return json.loads(proc.stdout or "null")
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        pytest.fail(f"node printed no JSON ({exc}):\n{proc.stdout[:400]}")


# --------------------------------------------------------------------------
# The script that drives the module: a small DOM and the messages of the
# server.
# --------------------------------------------------------------------------
DRIVE = r"""
const mod = await import("__URL__");

/* A small DOM: enough for the report of the path to build itself. */

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
  get isConnected() { return true; }
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
    return this.childNodes
      .map((c) => (c.textContent === undefined ? "" : c.textContent))
      .join("");
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

class StubElement extends StubNode {
  constructor(tag, doc) {
    super(doc);
    this.tagName = String(tag).toUpperCase();
    this.attributes = {};
    this.className = "";
    this.style = {};
    this.focused = false;
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
      toggle(name, force) {
        const on = force === undefined ? !self.classList.contains(name) : !!force;
        if (on) self.classList.add(name);
        else self.classList.remove(name);
        return on;
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
    return { width: 120, height: 18, left: 640, top: 700, right: 760, bottom: 718 };
  }
  focus() { this.focused = true; globalThis.__FOCUSED__ = this; }
  matches(selector) {
    const want = String(selector);
    if (want.startsWith(".")) return this.classList.contains(want.slice(1));
    return this.tagName === want.toUpperCase();
  }
  querySelectorAll(selector) {
    const out = [];
    const want = String(selector);
    const walk = (node) => {
      for (const child of node.childNodes) {
        if (child.matches && child.matches(want)) out.push(child);
        walk(child);
      }
    };
    walk(this);
    return out;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

function installDom() {
  const doc = new StubNode(null);
  doc.ownerDocument = doc;
  doc.createElement = (tag) => new StubElement(tag, doc);
  doc.createTextNode = (text) => new StubText(text, doc);
  doc.getElementById = () => null;
  doc.addEventListener = (type, fn) => {
    (doc.listeners[type] = doc.listeners[type] || []).push(fn);
  };
  const body = new StubElement("body", doc);
  doc.appendChild(body);
  doc.body = body;
  globalThis.Node = StubNode;
  globalThis.Element = StubElement;
  globalThis.Text = StubText;
  globalThis.DocumentFragment = StubNode;
  globalThis.document = doc;
  globalThis.innerHeight = 720;
  globalThis.__FOCUSED__ = null;
  return doc;
}

const doc = installDom();

const chip = doc.createElement("button");
chip.className = "path-chip";
const label = doc.createElement("span");
label.className = "path-label";
chip.appendChild(label);
const panel = doc.createElement("div");
panel.className = "path-panel hidden";
doc.body.appendChild(chip);
doc.body.appendChild(panel);

const asked = [];
const report = mod.createPathReport({
  chip: chip,
  panel: panel,
  nameOf: (code) => (code === "AUR_SPECTRALDENOISE" ? "Spectral De-Noise" : code),
  onProfile: (profile) => asked.push(profile),
});

const ready = {
  type: "ready",
  path: "preview",
  profile: "fast",
  profile_label: "preview: fast path",
  profile_report: {
    profile: "fast",
    changed: 2,
    modules: [
      {
        index: 0,
        code: "AUR_SPECTRALDENOISE",
        parameters: [
          { name: "Quality", file: "B", preview: "A" },
          { name: "Multi Resolution", file: true, preview: false },
        ],
      },
    ],
    refused: [],
  },
  processing_mode: "realtime",
  latency_samples: 3168,
  latency_ms: 66.0,
  block_size: 512,
};

const seen = report.update(ready);
const chipText = label.textContent;
const chipClass = chip.className;

report.open();
const openText = panel.textContent;
const buttons = panel.querySelectorAll("button").map((b) => b.textContent);
for (const button of panel.querySelectorAll("button")) {
  if (button.textContent === "Full path") button.dispatchEvent({ type: "click" });
}
const askedAfter = asked.slice();
const focused = globalThis.__FOCUSED__ ? globalThis.__FOCUSED__.className : "";

report.close();
const closed = panel.classList.contains("hidden");

/* a message that carries no latency: the report must not invent one */
const bare = mod.createPathReport({
  chip: doc.createElement("button"),
  panel: doc.createElement("div"),
});
bare.update({ type: "ready", profile: "full", profile_label: "preview: full path" });
const bareLatency = bare.view.latency;
const bareLabel = bare.view.reported;

const full = mod.createPathReport({
  chip: doc.createElement("button"),
  panel: doc.createElement("div"),
});
full.update({
  type: "ready",
  profile: "full",
  profile_label: "preview: full path",
  profile_report: { profile: "full", changed: 0, modules: [], refused: [] },
  latency_samples: 8192,
  latency_ms: 170.7,
});
full.open();
const fullText = full.view.report ? JSON.stringify(full.view.report) : "";

/* before the server has said anything: the interface knows nothing */
const silentPanel = doc.createElement("div");
const silent = mod.createPathReport({
  chip: doc.createElement("button"),
  panel: silentPanel,
});
silent.open();
const silentText = silentPanel.textContent;

process.stdout.write(JSON.stringify({
  seen: seen,
  chipText: chipText,
  chipClass: chipClass,
  openText: openText,
  buttons: buttons,
  asked: askedAfter,
  focused: focused,
  closed: closed,
  bareLatency: bareLatency,
  bareLabel: bareLabel,
  silentText: silentText,
  fullText: fullText,
  fullProfile: full.view.reported,
}));
"""


@pytest.fixture(scope="module")
def driven() -> dict:
    """The report of the path, driven with the messages of the server."""
    return _run_script(DRIVE.replace("__URL__", PATH_JS.as_uri()))


# --------------------------------------------------------------------------
# 1. The markup.
# --------------------------------------------------------------------------


def test_the_status_bar_carries_the_path_of_the_preview():
    """The chip and the disclosure are part of the status bar."""
    markup = _read(INDEX_HTML)
    assert 'id="st-path"' in markup, "the status bar has no chip for the path"
    assert 'id="path-panel"' in markup, "the status bar has no disclosure"
    assert 'id="st-latency"' in markup, "the status bar has no latency readout"


def test_the_chip_is_a_button_with_a_name():
    """The chip is a control: it needs a name and the keyboard."""
    markup = _read(INDEX_HTML)
    chip = re.search(r"<button[^>]*id=\"st-path\".*?>", markup, re.S)
    assert chip, "the chip of the path is not a button"
    text = chip.group(0)
    assert "aria-label" in text, "the chip has no accessible name"
    assert "aria-expanded" in text, "the chip does not say whether it is open"
    assert 'aria-haspopup="dialog"' in text, "the chip does not point at the panel"
    assert 'data-i18n-aria="status.path.label"' in text, (
        "the name of the chip does not follow the locale"
    )


def test_the_disclosure_is_a_dialog_that_is_hidden():
    """The disclosure is a dialog of the interface, closed by default."""
    markup = _read(INDEX_HTML)
    panel = re.search(r"<div[^>]*id=\"path-panel\".*?>", markup, re.S)
    assert panel, "the disclosure of the path is not in the document"
    text = panel.group(0)
    assert 'role="dialog"' in text, "the disclosure is not a dialog"
    assert "hidden" in text, "the disclosure is open when the page loads"


def test_the_path_is_styled():
    """The chip and the disclosure have rules of their own."""
    css = "\n".join(_read(path) for path in sorted(CSS_DIR.glob("*.css")))
    for selector in ("#st-path", ".path-panel", ".path-value", ".path-module-name"):
        assert selector in css, f"no stylesheet has a rule for {selector}"


# --------------------------------------------------------------------------
# 2. The behaviour of the module.
# --------------------------------------------------------------------------


def test_the_chip_says_which_path_plays(driven: dict):
    """The chip carries the path the server reported."""
    assert driven["seen"] is True, "the module ignored the message of the server"
    assert driven["chipText"] == "preview: fast path", driven["chipText"]
    assert "on" in driven["chipClass"], "the chip does not show that a path plays"


def test_the_disclosure_says_what_the_path_changed(driven: dict):
    """The disclosure names the module, the parameter and both values."""
    text = driven["openText"]
    assert "Spectral De-Noise" in text, text
    assert "Quality" in text, text
    assert "Multi Resolution" in text, text
    assert "B" in text and "A" in text, "the values of both sides are missing"
    assert "On" in text and "Off" in text, "the boolean values are missing"
    assert "file" in text and "preview" in text, "the two sides are not named"


def test_the_disclosure_offers_the_other_path(driven: dict):
    """The user can ask for the full path in the preview."""
    assert "Fast path" in driven["buttons"], driven["buttons"]
    assert "Full path" in driven["buttons"], driven["buttons"]
    assert driven["asked"] == ["full"], driven["asked"]


def test_the_disclosure_closes_again(driven: dict):
    """The chip opens and closes the disclosure."""
    assert driven["closed"] is True, "the disclosure did not close"


def test_a_latency_the_server_did_not_report_is_not_shown(driven: dict):
    """The browser computes no latency of its own."""
    assert driven["bareLatency"] is None, (
        "the module invented a latency the server did not report"
    )


def test_the_interface_claims_nothing_before_the_server_spoke(driven: dict):
    """Without a message of the server the interface says that it knows nothing."""
    text = driven["silentText"]
    assert "has not reported a path" in text, text
    assert "moved no parameter" not in text, (
        "the interface claims what the server has not said yet"
    )


def test_the_full_path_is_reported_as_the_full_path(driven: dict):
    """The full profile is named as it is."""
    assert driven["bareLabel"] == "full", driven["bareLabel"]
    assert driven["fullProfile"] == "full", driven["fullProfile"]
    assert '"changed":0' in driven["fullText"], driven["fullText"]


# --------------------------------------------------------------------------
# 3. The shell.
# --------------------------------------------------------------------------


def test_the_client_asks_the_server_for_the_path():
    """The configure message carries the path the user chose."""
    shell = _read(APP_JS)
    assert "profile: state.previewProfile" in shell, (
        "the client does not tell the server which path it wants"
    )
    assert "requestPreviewProfile" in shell, "the shell has no way to ask for a path"


def test_the_shell_binds_the_command_of_the_keyboard_map():
    """The shortcut of the keymap reaches the shell."""
    shell = _read(APP_JS)
    keymap = _read(KEYMAP_JS)
    assert "transport.previewPath" in keymap, "the map has no command for the path"
    assert "togglePreviewPath" in keymap, "the command names no action"
    assert '"transport.previewPath": () => togglePreviewPath()' in shell, (
        "the shell does not bind the command of the path"
    )
    assert "function togglePreviewPath" in shell, "the shell has no such action"


def test_the_latency_of_the_status_bar_is_the_one_of_the_server():
    """The latency comes from the server and is never computed here."""
    shell = _read(APP_JS)
    assert "latency_samples" in shell, "the shell does not read the latency"
    assert "setPreviewLatency" in shell, "the shell does not put the latency in the bar"
    assert not re.search(r"\blatency\s*[*/]", shell), (
        "the shell computes a latency of its own"
    )


def test_the_path_never_claims_to_be_the_file():
    """The path of the preview is never called the file."""
    catalogue = _read(I18N_JS)
    for key in (
        "status.path.fast",
        "status.path.full",
        "status.path.other",
        "status.path.render",
    ):
        labels = re.findall(rf"\"{re.escape(key)}\":\s*\"([^\"]*)\"", catalogue)
        assert len(labels) == 3, f"the key {key} is not in the three locales"
        # the first label is the reference locale: the path of the preview is
        # named after the preview, never after the file
        assert labels[0].startswith("preview:") or labels[0].startswith("render:"), (
            f"the path is labelled {labels[0]!r} — the preview is not the file"
        )
        for label in labels[1:]:
            assert label.strip(), f"the key {key} has an empty translation"


# --------------------------------------------------------------------------
# 4. The localisation.
# --------------------------------------------------------------------------


def test_the_new_strings_are_in_the_three_locales():
    """Every locale carries the strings of the path."""
    catalogue = _read(I18N_JS)
    keys = (
        "status.path.label",
        "status.path.title",
        "status.path.titleIdle",
        "status.path.playing",
        "status.path.idle",
        "status.path.unknown",
        "status.path.fast",
        "status.path.full",
        "status.path.other",
        "status.path.render",
        "status.path.mode",
        "status.path.block",
        "status.path.latency",
        "status.path.latencyValue",
        "status.path.changed",
        "status.path.unchanged",
        "status.path.refused",
        "status.path.file",
        "status.path.preview",
        "status.path.choose",
        "status.path.useFast",
        "status.path.useFull",
        "status.path.restart",
        "status.latency.unknown",
        "key.transport.previewPath.label",
        "key.transport.previewPath.description",
    )
    for key in keys:
        assert catalogue.count(f'"{key}"') == 3, (
            f"the key {key} is not in the three locales"
        )


def test_the_module_does_not_touch_the_document_when_it_is_imported():
    """The module may be imported by a head-less harness."""
    node = _node()
    proc = subprocess.run(
        [node, "--input-type=module", "-e", f'await import("{PATH_JS.as_uri()}");'],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"the module does not load without a document:\n{proc.stderr.strip()}"
    )
