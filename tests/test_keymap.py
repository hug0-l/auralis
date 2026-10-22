"""The audit of the keyboard map of the Auralis workbench.

``aurui/js/keymap.js`` is the only way a keyboard user can drive the
workbench, so the map is audited against the product instead of against a
list of wishes.  The audit answers five questions and every one of them is
measured here:

1.  **Does every command name an action that exists?**  Every command of the
    table carries the name of the action it drives (``action``).  The name
    must exist in the product: in ``aurui/js/app.js`` for the commands of the
    shell, in ``aurui/js/session.js`` for the commands of the session
    controller.
2.  **Is every command wired?**  The shell binds the commands in
    ``keymapCommands()``.  A command that is not bound does nothing, so the
    gap is written down in ``SHELL_BINDING_PENDING`` and may not grow.
3.  **Do the shortcuts collide?**  Two commands of the same context may not
    share a binding, and the keys the browser uses are listed in
    ``BROWSER_BINDINGS`` with the decision and the way the browser gets the
    key back.
4.  **Is the help overlay complete?**  The overlay renders ``describeRows()``;
    the rows must be exactly the commands of the table, with their keys, their
    context and their text.
5.  **Is the map reachable by keyboard only?**  The focus has to be visible,
    it must never get stuck, and a dialog has to trap the focus while it is
    open.  The focus rules are exercised against a small DOM built in the
    test, so they are measured and not assumed.

The behavioural checks need node; the static ones always run.
"""

from __future__ import annotations

import importlib.util
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
JS_DIR = REPO / "aurui" / "js"
KEYMAP_JS = JS_DIR / "keymap.js"
APP_JS = JS_DIR / "app.js"
SESSION_JS = JS_DIR / "session.js"
AUDIT_SCRIPT = REPO / "qa" / "a11y" / "audit_a11y.py"


def _audit_module():
    """Import the accessibility audit of qa/a11y/."""
    spec = importlib.util.spec_from_file_location("auralis_a11y_audit", AUDIT_SCRIPT)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        pytest.fail(f"{AUDIT_SCRIPT} could not be imported")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AUDIT = _audit_module()

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
    )
    if proc.returncode != 0:
        pytest.fail(f"node script failed:\n{proc.stderr.strip()}")
    try:
        return json.loads(proc.stdout or "null")
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        pytest.fail(f"node printed no JSON ({exc}):\n{proc.stdout[:400]}")


#: The command table, the browser table and the rows of the help overlay.
KEYMAP_TABLE = r"""
const mod = await import("__URL__");
const canon = (spec) => {
  const p = mod.parseBinding(spec);
  if (!p) return null;
  const parts = [];
  if (p.ctrl) parts.push("ctrl");
  if (p.alt) parts.push("alt");
  if (p.meta) parts.push("meta");
  if (p.shift) parts.push("shift");
  return parts.join("+") + "|" + p.key.toLowerCase();
};
const commands = mod.COMMANDS.map((c) => ({
  id: c.id,
  context: c.context,
  label: c.label,
  description: c.description,
  action: c.action === undefined ? null : c.action,
  provider: c.provider || "shell",
  modal: c.modal === true,
  keys: (Array.isArray(c.key) ? c.key : [c.key]).map(canon).filter((x) => x !== null),
  display: c.display === undefined ? null : String(c.display),
}));
process.stdout.write(JSON.stringify({
  commands: commands,
  contexts: mod.CONTEXTS,
  rows: mod.describeRows(),
  table: mod.describe(),
  browser: mod.BROWSER_BINDINGS.map((e) => ({
    key: String(e.key),
    canon: canon(e.key),
    browser: String(e.browser || ""),
    taken: e.taken === true,
    binding: e.binding === true,
    why: String(e.why || ""),
    back: String(e.back || ""),
  })),
  pending: mod.SHELL_BINDING_PENDING,
  session: mod.SESSION_COMMANDS,
  dialogs: mod.DIALOG_IDS,
}));
"""

#: The matcher of the keyboard map.
KEYMAP_BEHAVIOUR = r"""
const mod = await import("__URL__");
const body = { tagName: "BODY" };
const button = { tagName: "BUTTON" };
const input = { tagName: "INPUT", type: "text" };
const ev = (key, extra) => Object.assign({ key: key, target: body }, extra || {});
const id = (event, context) => {
  const found = mod.resolve(event, context);
  return found ? found.id : null;
};
process.stdout.write(JSON.stringify({
  plainB: mod.matches(ev("b"), "module.bypass"),
  ctrlB: mod.matches(ev("b", { ctrlKey: true }), "module.bypass"),
  typingB: mod.matches(ev("b", { target: input }), "module.bypass"),
  ctrlS: mod.matches(ev("s", { ctrlKey: true }), "preset.save"),
  plainS: mod.matches(ev("s"), "preset.save"),
  space: mod.matches(ev(" "), "transport.preview"),
  spaceOnButton: mod.matches(ev(" ", { target: button }), "transport.preview"),
  spaceOnInput: mod.matches(ev(" ", { target: input }), "transport.preview"),
  escape: mod.matches(ev("Escape"), "dialog.close"),
  deleteKey: mod.matches(ev("Delete"), "module.remove"),
  ctrlZ: mod.matches(ev("z", { ctrlKey: true }), "session.undo"),
  ctrlShiftZ: mod.matches(ev("z", { ctrlKey: true, shiftKey: true }), "session.redo"),
  ctrlShiftZIsNotUndo: mod.matches(ev("z", { ctrlKey: true, shiftKey: true }), "session.undo"),
  ctrlZIsNotRedo: mod.matches(ev("z", { ctrlKey: true }), "session.redo"),
  upperCtrlShiftZ: mod.matches(ev("Z", { ctrlKey: true, shiftKey: true }), "session.redo"),
  ctrlZAlone: mod.matches(ev("z"), "session.undo"),
  ctrlBracketLeft: mod.matches(ev("[", { ctrlKey: true }), "chain.movePrev"),
  ctrlBracketRight: mod.matches(ev("]", { ctrlKey: true }), "chain.moveNext"),
  plainBracketLeft: mod.matches(ev("["), "chain.selectPrev"),
  viewF: id(ev("f"), "view"),
  globalF: id(ev("f"), "global"),
  panelT: id(ev("t"), "panel"),
  viewT: id(ev("t"), "view"),
  globalT: id(ev("t"), "global"),
  typingResolve: id(ev("b", { target: input }), "global"),
}));
"""

#: A very small DOM, enough for the focus rules of the keyboard map.
FAKE_DOM = r"""
class FakeNode {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.childNodes = [];
    this.parentNode = null;
    this.attributes = {};
    this.className = "";
    this.textContent = "";
    this.style = {};
    this.hidden = false;
    this.disabled = false;
    this.isContentEditable = false;
    this.listeners = {};
    this.ownerDocument = null;
  }
  get children() { return this.childNodes.filter((n) => n instanceof FakeNode); }
  append(...nodes) {
    for (const node of nodes) {
      if (!node) continue;
      node.parentNode = this;
      this.childNodes.push(node);
    }
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "class") this.className = String(value);
  }
  getAttribute(name) {
    if (name === "class") return this.className ? this.className : null;
    return name in this.attributes ? this.attributes[name] : null;
  }
  hasAttribute(name) {
    if (name === "class") return !!this.className;
    return name in this.attributes;
  }
  get classList() {
    const node = this;
    const read = () => new Set(node.className.split(/\s+/).filter(Boolean));
    const write = (set) => { node.className = [...set].join(" "); };
    return {
      contains: (name) => read().has(name),
      add: (name) => { const set = read(); set.add(name); write(set); },
      remove: (name) => { const set = read(); set.delete(name); write(set); },
      toggle: (name, on) => {
        const set = read();
        if (on === undefined) { if (set.has(name)) set.delete(name); else set.add(name); }
        else if (on) set.add(name); else set.delete(name);
        write(set);
      },
    };
  }
  addEventListener(type, fn) {
    if (!this.listeners[type]) this.listeners[type] = [];
    this.listeners[type].push(fn);
  }
  removeEventListener(type, fn) {
    const list = this.listeners[type] || [];
    const at = list.indexOf(fn);
    if (at >= 0) list.splice(at, 1);
  }
  dispatch(type, event) {
    for (const fn of (this.listeners[type] || []).slice()) fn(event);
  }
  focus() { if (this.ownerDocument) this.ownerDocument.activeElement = this; }
  blur() {
    if (this.ownerDocument && this.ownerDocument.activeElement === this) {
      this.ownerDocument.activeElement = this.ownerDocument.body;
    }
  }
  contains(other) {
    let cur = other;
    while (cur) { if (cur === this) return true; cur = cur.parentNode; }
    return false;
  }
  closest(selector) {
    let cur = this;
    while (cur) { if (matchesSimple(cur, selector)) return cur; cur = cur.parentNode; }
    return null;
  }
  querySelectorAll(selector) { return collect(this, selector); }
  querySelector(selector) { return collect(this, selector)[0] || null; }
}

const parseSelector = (raw) => {
  const list = [];
  for (const part of String(raw).split(",")) {
    let text = part.trim();
    if (!text) continue;
    const negs = [];
    const notRe = /:not\(\s*\[([\w-]+)(?:=(?:"([^"]*)"|'([^']*)'))?\]\s*\)/g;
    let match;
    while ((match = notRe.exec(text)) !== null) {
      const value = match[2] !== undefined ? match[2] : (match[3] !== undefined ? match[3] : null);
      negs.push([match[1], value]);
    }
    text = text.replace(notRe, "");
    const classes = [];
    const classRe = /\.([A-Za-z][\w-]*)/g;
    let cls;
    while ((cls = classRe.exec(text)) !== null) classes.push(cls[1]);
    text = text.replace(classRe, "");
    const spec = { tag: null, attrs: [], classes: classes, negs: negs };
    const tagMatch = /^([a-zA-Z][\w-]*)/.exec(text);
    if (tagMatch) spec.tag = tagMatch[1].toUpperCase();
    const attrRe = /\[([\w-]+)(?:=(?:"([^"]*)"|'([^']*)'))?\]/g;
    let attr;
    while ((attr = attrRe.exec(text)) !== null) {
      const value = attr[2] !== undefined ? attr[2] : (attr[3] !== undefined ? attr[3] : null);
      spec.attrs.push([attr[1], value]);
    }
    list.push(spec);
  }
  return list;
};

const matchesSpec = (node, spec) => {
  if (spec.tag && node.tagName !== spec.tag) return false;
  for (const name of spec.classes) {
    if (!node.classList || !node.classList.contains(name)) return false;
  }
  for (const [name, value] of spec.attrs) {
    const got = node.getAttribute(name);
    if (value === null) { if (got === null) return false; }
    else if (String(got) !== value) return false;
  }
  for (const [name, value] of spec.negs) {
    const got = node.getAttribute(name);
    if (value === null) { if (got !== null) return false; }
    else if (String(got) === value) return false;
  }
  return true;
};

const matchesSimple = (node, selector) =>
  parseSelector(selector).some((spec) => matchesSpec(node, spec));

const collect = (root, selector) => {
  const specs = parseSelector(selector);
  const out = [];
  const walk = (node) => {
    for (const child of node.children) {
      if (specs.some((spec) => matchesSpec(child, spec))) out.push(child);
      walk(child);
    }
  };
  walk(root);
  return out;
};

class FakeObserver {
  constructor(callback) { this.callback = callback; FakeObserver.instances.push(this); }
  observe(target, options) { this.target = target; this.options = options; }
  disconnect() { this.disconnected = true; }
}
FakeObserver.instances = [];

const makeDocument = () => {
  const doc = {
    activeElement: null,
    listeners: {},
    defaultView: { MutationObserver: FakeObserver },
    createElement(tag) { const node = new FakeNode(tag); node.ownerDocument = doc; return node; },
    createTextNode(text) { const node = new FakeNode("#text");
      node.textContent = text; node.ownerDocument = doc; return node; },
    getElementById(id) {
      let found = null;
      const walk = (node) => {
        for (const child of node.children) {
          if (child.getAttribute("id") === id) { found = child; return; }
          walk(child);
        }
      };
      walk(doc.body);
      return found;
    },
    querySelectorAll(selector) { return collect(doc.body, selector); },
    querySelector(selector) { return collect(doc.body, selector)[0] || null; },
    addEventListener(type, fn) {
      if (!doc.listeners[type]) doc.listeners[type] = [];
      doc.listeners[type].push(fn);
    },
    removeEventListener(type, fn) {
      const list = doc.listeners[type] || [];
      const at = list.indexOf(fn);
      if (at >= 0) list.splice(at, 1);
    },
    dispatch(type, event) {
      for (const fn of (doc.listeners[type] || []).slice()) fn(event);
    },
  };
  doc.body = doc.createElement("body");
  doc.documentElement = doc.createElement("html");
  doc.documentElement.append(doc.body);
  return doc;
};
"""

#: The focus rules, the dialog rules and the help overlay.
KEYMAP_FOCUS = FAKE_DOM + r"""
const mod = await import("__URL__");
const doc = makeDocument();
globalThis.Node = FakeNode;
globalThis.document = doc;

/* ---- the interface ---- */
const modal = doc.createElement("div");
modal.setAttribute("id", "modal");
modal.setAttribute("class", "modal hidden");
const modalClose = doc.createElement("button");
const modalOk = doc.createElement("button");
modal.append(modalClose, modalOk);
doc.body.append(modal);

const field = doc.createElement("input");
field.setAttribute("type", "text");
doc.body.append(field);

const plainButton = doc.createElement("button");
doc.body.append(plainButton);

const before = doc.createElement("button");
doc.body.append(before);

const ev = (key, extra) => Object.assign({ key: key, target: doc.body }, extra || {});
const id = (event, context) => {
  const found = mod.resolve(event, context);
  return found ? found.id : null;
};

/* ---- the keyboard map is installed on the window ---- */
const target = {
  ownerDocument: doc,
  handlers: {},
  addEventListener(type, fn) {
    if (!target.handlers[type]) target.handlers[type] = [];
    target.handlers[type].push(fn);
  },
  removeEventListener(type, fn) {
    const list = target.handlers[type] || [];
    const at = list.indexOf(fn);
    if (at >= 0) list.splice(at, 1);
  },
  dispatch(type, event) {
    for (const fn of (target.handlers[type] || []).slice()) fn(event);
  },
};

const calls = [];
const uninstall = mod.installKeymap(
  { "transport.preview": () => calls.push("preview") },
  { target: target }
);

/* ---- the dialog of the shell ---- */
const dialogHidden = mod.openDialog(doc) === null;
modal.classList.remove("hidden");
const dialogFound = mod.openDialog(doc) === modal;

/* ---- the focus trap ---- */
let prevented = false;
const tabEvent = (shift) => ({
  key: "Tab",
  shiftKey: Boolean(shift),
  preventDefault() { prevented = true; },
});
doc.activeElement = modalClose;
mod.trapFocus(tabEvent(false), modal, doc);
const tabForward = doc.activeElement === modalOk && prevented;
doc.activeElement = modalOk;
mod.trapFocus(tabEvent(false), modal, doc);
const tabWraps = doc.activeElement === modalClose;
doc.activeElement = modalClose;
mod.trapFocus(tabEvent(true), modal, doc);
const tabBack = doc.activeElement === modalOk;
const trapHandlesOtherKeys = mod.trapFocus({ key: "a", preventDefault() {} }, modal, doc) === false;

/* ---- the commands behind an open dialog ---- */
const behindDialog = id(ev("s"), "global");
const escapeInDialog = id(ev("Escape"), "global");
const helpInDialog = id(ev("F1"), "global");
const zoomInDialog = id(ev("="), "view");

/* ---- the focus is given back when the dialog closes ---- */
doc.activeElement = modalOk;
doc.dispatch("focusin", { target: modalOk, relatedTarget: before });
const remembered = doc.activeElement === modalOk;
modal.classList.add("hidden");
for (const observer of FakeObserver.instances) {
  if (!observer.disconnected) observer.callback([]);
}
const focusGivenBack = doc.activeElement === before;

/* ---- a field the user types in ---- */
field.focus();
let blurred = false;
field.blur = () => { blurred = true;
  field.ownerDocument.activeElement = field.ownerDocument.body; };
target.dispatch("keydown", {
  key: "Escape",
  target: field,
  preventDefault() {},
});
const escapeLeavesTheField = blurred && doc.activeElement !== field;

doc.activeElement = doc.body;
target.dispatch("keydown", {
  key: " ",
  target: doc.body,
  preventDefault() {},
});
const spaceStartsTheTransport = calls.length === 1;

doc.activeElement = plainButton;
target.dispatch("keydown", {
  key: " ",
  target: plainButton,
  preventDefault() {},
});
const spaceLeavesTheButton = calls.length === 1;

uninstall();

/* ---- the help overlay ---- */
const overlay = mod.buildHelpOverlay(doc);
doc.body.append(overlay);
const rows = overlay.querySelectorAll("tr.help-row").filter(
  (row) => !row.classList.contains("help-row-group")
);
const groups = overlay.querySelectorAll("tr.help-row-group");
const kbd = overlay.querySelectorAll("kbd");
const contextCells = overlay.querySelectorAll("td.help-context");
const dialogRole = overlay.getAttribute("role");
const modalRole = overlay.getAttribute("aria-modal");
const focusables = mod.focusablesIn(overlay).length;

const opener = doc.createElement("button");
doc.body.append(opener);
doc.activeElement = opener;
mod.toggleHelpOverlay(doc);
const overlayOpen = !overlay.classList.contains("hidden");
const focusInside = doc.activeElement !== opener && overlay.contains(doc.activeElement);
const focusTrapped = mod.trapFocus(tabEvent(false), overlay, doc);
const focusAfterTab = overlay.contains(doc.activeElement);
mod.hideHelpOverlay(doc);
const overlayClosed = overlay.classList.contains("hidden");
const focusRestored = doc.activeElement === opener;

process.stdout.write(JSON.stringify({
  commands: mod.COMMANDS.length,
  contexts: mod.CONTEXTS.length,
  dialogHidden: dialogHidden,
  dialogFound: dialogFound,
  tabForward: tabForward,
  tabWraps: tabWraps,
  tabBack: tabBack,
  trapHandlesOtherKeys: trapHandlesOtherKeys,
  behindDialog: behindDialog,
  escapeInDialog: escapeInDialog,
  helpInDialog: helpInDialog,
  zoomInDialog: zoomInDialog,
  remembered: remembered,
  focusGivenBack: focusGivenBack,
  escapeLeavesTheField: escapeLeavesTheField,
  spaceStartsTheTransport: spaceStartsTheTransport,
  spaceLeavesTheButton: spaceLeavesTheButton,
  overlayRows: rows.length,
  overlayGroups: groups.length,
  overlayKeys: kbd.length,
  overlayContexts: contextCells.length,
  dialogRole: dialogRole,
  modalRole: modalRole,
  overlayFocusables: focusables,
  overlayOpen: overlayOpen,
  focusInside: focusInside,
  focusTrapped: focusTrapped,
  focusAfterTab: focusAfterTab,
  overlayClosed: overlayClosed,
  focusRestored: focusRestored,
}));
"""


def _keymap_data() -> dict:
    """The command table of the keyboard map."""
    data = _node_json(KEYMAP_TABLE.replace("__URL__", KEYMAP_JS.as_uri()))
    assert isinstance(data, dict) and "commands" in data
    return data


def _behaviour() -> dict:
    """The behaviour of the matcher."""
    data = _node_json(KEYMAP_BEHAVIOUR.replace("__URL__", KEYMAP_JS.as_uri()))
    assert isinstance(data, dict) and "plainB" in data
    return data


def _focus() -> dict:
    """The focus rules, the dialog rules and the help overlay."""
    data = _node_json(KEYMAP_FOCUS.replace("__URL__", KEYMAP_JS.as_uri()))
    assert isinstance(data, dict) and "dialogFound" in data
    return data


def _shell_functions() -> set[str]:
    """The names of the functions the shell declares."""
    text = _read(APP_JS)
    return set(re.findall(r"(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", text))


def _shell_bindings() -> dict[str, str]:
    """The commands the shell binds, and the action each one calls."""
    text = _read(APP_JS)
    match = re.search(
        r"function\s+keymapCommands\s*\(\)\s*\{\s*return\s*\{(.*?)\n\s*\};",
        text,
        re.S,
    )
    if not match:
        pytest.fail("aurui/js/app.js has no keymapCommands() bag")
    body = match.group(1)
    bindings: dict[str, str] = {}
    for entry in re.finditer(r'"([a-zA-Z0-9_.]+)"\s*:\s*(.*)', body):
        command_id = entry.group(1)
        handler = entry.group(2)
        calls = re.findall(r"\b([A-Za-z_$][\w$]*)\s*\(", handler)
        bindings[command_id] = calls[0] if calls else ""
    return bindings


def _session_methods() -> set[str]:
    """The names of the methods the session controller declares."""
    text = _read(SESSION_JS)
    return set(re.findall(r"(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", text))


def _catalogue() -> dict[str, dict[str, str]]:
    """The catalogues of the localisation module."""
    return AUDIT.load_catalogue()


# --------------------------------------------------------------------------
# 1. The command table.
# --------------------------------------------------------------------------


def test_every_command_carries_the_fields_the_audit_needs():
    """A command names its context, its keys and the action it drives."""
    data = _keymap_data()
    problems: dict[str, str] = {}
    for command in data["commands"]:
        if not command["id"]:
            problems[command["id"]] = "the command has no id"
        if command["context"] not in data["contexts"]:
            problems[command["id"]] = f"the context {command['context']!r} is unknown"
        if not command["keys"]:
            problems[command["id"]] = "the command has no binding"
        if not command["label"]:
            problems[command["id"]] = "the command has no label"
        if not command["description"]:
            problems[command["id"]] = "the command has no description"
        if not command["action"]:
            problems[command["id"]] = "the command names no action"
    assert not problems, f"these commands are incomplete: {problems}"


def test_the_command_ids_are_unique():
    """Two commands may not share an id."""
    data = _keymap_data()
    ids = [command["id"] for command in data["commands"]]
    duplicates = sorted({name for name in ids if ids.count(name) > 1})
    assert not duplicates, f"these command ids are used twice: {duplicates}"


def test_the_map_covers_the_four_contexts():
    """The map has a command for every region of the interface."""
    data = _keymap_data()
    used = {command["context"] for command in data["commands"]}
    missing = sorted(set(data["contexts"]) - used)
    assert not missing, f"these contexts carry no command: {missing}"


# --------------------------------------------------------------------------
# 2. The audit against the product.
# --------------------------------------------------------------------------


def test_every_command_names_an_action_that_exists():
    """The action of a command has to exist in the product."""
    data = _keymap_data()
    shell = _shell_functions()
    session = _session_methods()
    missing: dict[str, list[str]] = {}
    for command in data["commands"]:
        actions = command["action"]
        if not isinstance(actions, list):
            actions = [actions]
        source = session if command["provider"] == "session" else shell
        gap = [name for name in actions if name not in source]
        if gap:
            missing[command["id"]] = gap
    assert not missing, (
        "these commands name an action the product does not have: "
        f"{missing}"
    )


def test_the_undo_and_the_redo_reach_the_session_controller():
    """The session controller has the history; the map has to reach it."""
    data = _keymap_data()
    ids = {command["id"]: command for command in data["commands"]}
    for command_id, action in (("session.undo", "undo"), ("session.redo", "redo")):
        assert command_id in ids, f"the map has no {command_id} command"
        command = ids[command_id]
        assert command["provider"] == "session", (
            f"{command_id} does not come from the session controller"
        )
        assert action in command["action"], (
            f"{command_id} does not drive the {action}() of the controller"
        )
    assert "session.undo" in data["session"]
    assert "session.redo" in data["session"]
    session = _session_methods()
    assert "undo" in session and "redo" in session, (
        "the session controller does not carry the history any more"
    )


def test_every_command_is_bound_by_the_shell_or_declared_as_pending():
    """A command that is not bound does nothing: the gap has to be written down."""
    data = _keymap_data()
    ids = {command["id"] for command in data["commands"]}
    bindings = _shell_bindings()
    pending = set(data["pending"])

    orphans = sorted(set(bindings) - ids)
    assert not orphans, (
        f"the shell binds commands the map does not have: {orphans}"
    )

    unbound = sorted(ids - set(bindings))
    undeclared = sorted(set(unbound) - pending)
    assert not undeclared, (
        "these commands are not bound by the shell and are not declared as "
        f"pending: {undeclared} — they do nothing"
    )

    stale = sorted(pending - ids)
    assert not stale, (
        f"these pending commands are not part of the map any more: {stale}"
    )


def test_the_pending_list_is_the_audit_trail_of_the_wiring():
    """The list of the pending commands may shrink, but it may not grow."""
    data = _keymap_data()
    bindings = _shell_bindings()
    pending = set(data["pending"])
    ids = {command["id"] for command in data["commands"]}

    unbound = ids - set(bindings)
    assert not (unbound - pending), (
        "these commands are not bound by the shell and are not on the list of "
        f"the pending commands: {sorted(unbound - pending)}"
    )
    assert not (pending - ids), (
        "these entries of the pending list are not commands of the map: "
        f"{sorted(pending - ids)}"
    )


def test_every_action_a_keyboard_user_needs_is_reachable():
    """An action is reachable by a key or by a control of the interface."""
    data = _keymap_data()
    commands = data["commands"]
    named: set[str] = set()
    for command in commands:
        actions = command["action"]
        if not isinstance(actions, list):
            actions = [actions]
        named.update(actions)

    #: The actions a keyboard user needs, and the way they are reached.
    required = {
        "preview": "the transport",
        "stopPreview": "the transport",
        "bypassSelected": "the chain",
        "removeModule": "the chain",
        "selectRelativeModule": "the chain",
        "reorderModule": "the chain",
        "undo": "the session history",
        "redo": "the session history",
        "openPresetDialog": "the preset library",
        "closeDialogs": "the dialogs",
        "toggleHelpOverlay": "the keyboard help",
        "focusModuleSearch": "the module browser",
        "setZoom": "the view",
        "setScroll": "the view",
        "clearSelection": "the view",
        "toggleSelectionMode": "the view",
        "cycleInspectorTab": "the inspector",
        "openModuleMenu": "the inspector",
        "addModule": "the module browser",
    }
    missing = sorted(name for name in required if name not in named)
    assert not missing, (
        f"these actions are named by no command: {missing} — a keyboard user "
        "cannot reach them"
    )


def test_the_actions_of_the_shell_are_reachable_by_a_key_or_by_a_control():
    """An action of the shell is reached by a command or by a button."""
    data = _keymap_data()
    named: set[str] = set()
    for command in data["commands"]:
        actions = command["action"]
        if not isinstance(actions, list):
            actions = [actions]
        named.update(actions)

    #: The actions of the shell that a keyboard user needs.
    shell_actions = (
        "preview",
        "stopPreview",
        "bypassSelected",
        "removeModule",
        "selectRelativeModule",
        "reorderModule",
        "openPresetDialog",
        "closeDialogs",
        "toggleHelpOverlay",
        "focusModuleSearch",
        "setZoom",
        "setScroll",
        "clearSelection",
        "toggleSelectionMode",
        "cycleInspectorTab",
        "openModuleMenu",
        "addModule",
        "renderFull",
        "compareChains",
        "clearChain",
        "toggleSpectralEditor",
        "learnSelected",
    )
    buttons = _click_handlers()
    unreachable = sorted(
        name
        for name in shell_actions
        if name not in named and name not in buttons
    )
    assert not unreachable, (
        f"these actions are neither a command nor a button: {unreachable} — "
        "the keyboard cannot reach them"
    )


def _click_handlers() -> set[str]:
    """The actions a control of the interface calls.

    A control is either a button of the markup or a button a component
    creates at run time; the components hand their actions to the shell
    through a handler bag, so the bags are read as well.
    """
    found: set[str] = set()
    for path in sorted(JS_DIR.glob("*.js")):
        text = _read(path)
        for match in re.finditer(r'addEventListener\(\s*"click"', text):
            window = text[match.start(): match.start() + 400]
            found.update(re.findall(r"\b([A-Za-z_$][\w$]*)\s*\(", window))
        # the handler bags of the components: onLearn: () => learnSelected()
        for match in re.finditer(r"\bon[A-Z][A-Za-z]*\s*:\s*\(\)\s*=>\s*([A-Za-z_$][\w$]*)", text):
            found.add(match.group(1))
    found.discard("addEventListener")
    found.discard("el")
    return found


# --------------------------------------------------------------------------
# 3. The collisions.
# --------------------------------------------------------------------------


def test_no_two_commands_of_a_context_share_a_binding():
    """Two commands of the same context may not take the same key."""
    data = _keymap_data()
    clashes: list[str] = []
    for context in data["contexts"]:
        taken: dict[str, str] = {}
        for command in data["commands"]:
            if command["context"] != context:
                continue
            for binding in command["keys"]:
                if binding in taken:
                    clashes.append(
                        f"{context}: {taken[binding]} and {command['id']} both "
                        f"use {binding}"
                    )
                else:
                    taken[binding] = command["id"]
    assert not clashes, "duplicate keyboard bindings: " + "; ".join(clashes)


def test_a_key_of_a_context_does_not_shadow_a_global_command():
    """A context command and a global command may not take the same key."""
    data = _keymap_data()
    globals_ = {
        binding: command["id"]
        for command in data["commands"]
        if command["context"] == "global"
        for binding in command["keys"]
    }
    clashes: list[str] = []
    for command in data["commands"]:
        if command["context"] == "global":
            continue
        for binding in command["keys"]:
            if binding in globals_:
                clashes.append(
                    f"{command['context']}: {command['id']} takes {binding}, "
                    f"which the global command {globals_[binding]} takes as well"
                )
    assert not clashes, "a key of a context shadows a global command: " + "; ".join(clashes)


def test_the_same_key_in_two_contexts_is_documented():
    """A key that means two things needs a note in the map."""
    data = _keymap_data()
    owners: dict[str, list[str]] = {}
    for command in data["commands"]:
        for binding in command["keys"]:
            owners.setdefault(binding, []).append(command["id"])
    shared = {key: ids for key, ids in owners.items() if len(ids) > 1}
    for key, ids in shared.items():
        contexts = {
            command["context"]
            for command in data["commands"]
            if command["id"] in ids
        }
        assert len(contexts) == len(ids), (
            f"the key {key} is used by {ids} without a context of its own"
        )
    assert "t|" in shared or any(key.endswith("|t") for key in shared), (
        "the map no longer binds the same key in two contexts — the note in "
        "the documentation has to follow"
    )


# --------------------------------------------------------------------------
# 4. The keys of the browser.
# --------------------------------------------------------------------------

#: The keys a browser uses, in the canonical form of the map.
#:
#: The list is the audit's own: the table of the map has to cover every key
#: of it that the map takes, and to leave the rest alone.
BROWSER_KEYS = {
    "ctrl+s",
    "ctrl+o",
    "ctrl+f",
    "ctrl+r",
    "ctrl+t",
    "ctrl+n",
    "ctrl+w",
    "ctrl+p",
    "ctrl+d",
    "ctrl+l",
    "ctrl+tab",
    "ctrl+arrowup",
    "ctrl+arrowdown",
    "ctrl+arrowleft",
    "ctrl+arrowright",
    "ctrl+plus",
    "ctrl+minus",
    "f1",
    "f5",
    "f11",
    "f12",
    "space",
    "escape",
    "delete",
    "backspace",
    "enter",
    "slash",
    "ctrl+z",
    "ctrl+shift+z",
    "tab",
    "home",
    "end",
    "pageup",
    "pagedown",
}


def test_every_browser_key_the_map_takes_is_documented():
    """A key the browser uses is listed with the decision and the way back."""
    data = _keymap_data()
    documented = {entry["canon"]: entry for entry in data["browser"]}
    taken: dict[str, str] = {}
    for command in data["commands"]:
        for binding in command["keys"]:
            taken[binding] = command["id"]

    suspects = sorted(set(taken) & BROWSER_KEYS)
    undocumented = [key for key in suspects if key not in documented]
    assert not undocumented, (
        "these bindings are keys a browser uses and they are not documented "
        f"in BROWSER_BINDINGS: {undocumented}"
    )

    for binding in suspects:
        entry = documented[binding]
        assert entry["taken"] is True, (
            f"{binding} is bound by {taken[binding]} but the browser table "
            "says the map does not take it"
        )
        assert entry["binding"] is True, (
            f"{binding} is bound by {taken[binding]} but the browser table "
            "does not know it as a binding"
        )
        assert entry["why"].strip(), f"{entry['key']} has no reason"
        assert entry["back"].strip(), f"{entry['key']} has no way back"


def test_the_browser_table_is_honest():
    """A key the table claims is taken really is taken."""
    data = _keymap_data()
    taken = {
        binding
        for command in data["commands"]
        for binding in command["keys"]
    }
    for entry in data["browser"]:
        assert entry["why"].strip(), f"{entry['key']} has no reason"
        assert entry["back"].strip(), f"{entry['key']} has no way back"
        if entry["binding"]:
            assert entry["canon"] in taken, (
                f"{entry['key']} is documented as a binding of the map, but "
                "no command takes it"
            )
        else:
            assert entry["canon"] not in taken, (
                f"{entry['key']} is documented as a key the map leaves alone, "
                "but a command takes it"
            )
            if entry["taken"]:
                assert entry["key"] == "Tab", (
                    f"{entry['key']} is taken by the map but is no binding of "
                    "a command; only the focus trap may do that"
                )


def test_the_map_does_not_take_the_keys_the_browser_needs():
    """The keys the browser owns are left alone."""
    data = _keymap_data()
    left_alone = {
        entry["key"]
        for entry in data["browser"]
        if not entry["taken"]
    }
    for key in ("Ctrl+O", "Ctrl+F", "Ctrl+R", "Ctrl+T", "Ctrl+W", "F5", "F11", "F12"):
        assert key in left_alone, (
            f"{key} belongs to the browser and has to be listed as a key the "
            "map leaves alone"
        )
    taken = {
        entry["key"]
        for entry in data["browser"]
        if entry["taken"] and entry["binding"]
    }
    for key in ("Ctrl+O", "Ctrl+F", "Ctrl+R", "F5", "F11", "F12"):
        assert key not in taken, f"the map must not take {key} from the browser"


def test_the_map_does_not_bind_an_arrow_key():
    """The arrows belong to the widgets and to the browser."""
    data = _keymap_data()
    arrows = {
        binding
        for command in data["commands"]
        for binding in command["keys"]
        if binding.split("|")[-1] in {"arrowup", "arrowdown", "arrowleft", "arrowright"}
    }
    assert not arrows, f"the map binds the arrow keys: {sorted(arrows)}"


# --------------------------------------------------------------------------
# 5. The help overlay.
# --------------------------------------------------------------------------


def test_the_help_overlay_lists_every_command():
    """The overlay renders the table of the map, so it is always complete."""
    data = _keymap_data()
    rows = data["rows"]
    assert len(rows) == len(data["commands"]), (
        f"the help overlay lists {len(rows)} commands, the map has "
        f"{len(data['commands'])}"
    )
    assert [row["id"] for row in rows] == [c["id"] for c in data["commands"]]
    for row in rows:
        assert row["keys"], f"{row['id']} prints no key"
        assert row["description"], f"{row['id']} prints no description"
        assert row["label"], f"{row['id']} has no short name"
        assert row["context"] in data["contexts"]


def test_the_printed_table_carries_every_command():
    """The printable table names every command of the map."""
    data = _keymap_data()
    table = data["table"]
    for command in data["commands"]:
        assert command["description"] in table, (
            f"{command['id']} is missing from the printed table"
        )


def test_the_overlay_renders_a_row_for_every_command():
    """The overlay of the interface lists every command of the map."""
    data = _focus()
    assert data["overlayRows"] == data["commands"], (
        f"the overlay shows {data['overlayRows']} rows, the map has "
        f"{data['commands']} commands"
    )
    assert data["overlayGroups"] == data["contexts"], (
        "the overlay does not have a group per context"
    )
    assert data["overlayKeys"] >= data["commands"], (
        "the overlay prints fewer keys than the map has commands"
    )
    assert data["overlayContexts"] == data["commands"], (
        "the overlay does not name the context of every command"
    )
    assert data["dialogRole"] == "dialog", "the overlay is not a dialog"
    assert data["modalRole"] == "true", "the overlay is not modal"
    assert data["overlayFocusables"] >= 2, (
        "the overlay has no control the keyboard can reach"
    )


def test_every_command_has_a_translated_label_and_description():
    """The help overlay follows the locale, so every command needs its text."""
    data = _keymap_data()
    catalogue = _catalogue()
    reference = catalogue.get("en", {})
    assert reference, "the English catalogue is empty"
    missing: dict[str, list[str]] = {}
    for command in data["commands"]:
        gap = [
            key
            for key in (
                f"key.{command['id']}.label",
                f"key.{command['id']}.description",
            )
            if key not in reference
        ]
        if gap:
            missing[command["id"]] = gap
    assert not missing, (
        "these keyboard commands have no text in the catalogue: "
        f"{missing} — the help overlay would show the English fallback only"
    )


# --------------------------------------------------------------------------
# 6. The focus.
# --------------------------------------------------------------------------


def test_the_focus_ring_is_visible():
    """The stylesheets draw a ring around the focused control."""
    css_dir = REPO / "aurui" / "css"
    css = "\n".join(_read(path) for path in sorted(css_dir.glob("*.css")))
    assert css, "the interface has no stylesheet"
    rules = re.findall(r"([^{}]+)\{([^{}]*)\}", css)
    visible = False
    for selector, body in rules:
        if ":focus" not in selector:
            continue
        for value in re.findall(r"outline(?:-[a-z]+)?\s*:\s*([^;]+);", body):
            if value.strip().lower() in {"none", "0"}:
                continue
            visible = True
    assert visible, "no stylesheet draws a visible focus ring"

    # The overlay must not turn the ring off for its own controls.
    for selector, body in rules:
        if "help-" not in selector and "tbtn" not in selector:
            continue
        for value in re.findall(r"outline(?:-[a-z]+)?\s*:\s*([^;]+);", body):
            assert value.strip().lower() not in {"none", "0"}, (
                f"the rule {selector.strip()!r} hides the focus ring"
            )


def test_a_dialog_is_found_and_the_focus_is_trapped_in_it():
    """A dialog that is open owns the keyboard."""
    data = _focus()
    assert data["dialogHidden"] is True, "the closed dialog is taken for open"
    assert data["dialogFound"] is True, "the open dialog is not found"
    assert data["tabForward"] is True, "Tab does not move the focus forward"
    assert data["tabWraps"] is True, "Tab does not wrap around the dialog"
    assert data["tabBack"] is True, "Shift+Tab does not move the focus back"
    assert data["trapHandlesOtherKeys"] is True, (
        "the trap must not swallow the other keys"
    )


def test_only_the_dialog_commands_fire_while_a_dialog_is_open():
    """The chain does not react behind a dialog."""
    data = _focus()
    assert data["behindDialog"] is None, (
        "a command of the chain fires while a dialog is open"
    )
    assert data["zoomInDialog"] is None, (
        "a command of the view fires while a dialog is open"
    )
    assert data["escapeInDialog"] == "dialog.close", (
        "Escape does not close the dialog"
    )
    assert data["helpInDialog"] == "help.toggle", (
        "the keyboard help cannot be reached while a dialog is open"
    )


def test_the_focus_is_given_back_when_the_dialog_closes():
    """The user does not lose the place they were working at."""
    data = _focus()
    assert data["remembered"] is True, (
        "the focus the dialog took is not remembered"
    )
    assert data["focusGivenBack"] is True, (
        "the focus is not given back when the dialog closes"
    )


def test_the_focus_never_gets_stuck_in_a_field():
    """Escape leaves the field the user is typing in."""
    data = _focus()
    assert data["escapeLeavesTheField"] is True, (
        "Escape does not leave a field, so the focus gets stuck in it"
    )


def test_a_focused_button_keeps_the_space():
    """The transport does not start under the finger of the user."""
    data = _focus()
    assert data["spaceStartsTheTransport"] is True, (
        "Space does not start the transport"
    )
    assert data["spaceLeavesTheButton"] is True, (
        "Space is taken away from a focused button"
    )


def test_the_help_overlay_takes_the_focus_and_gives_it_back():
    """The overlay is a dialog: it takes the focus and gives it back."""
    data = _focus()
    assert data["overlayOpen"] is True, "the overlay does not open"
    assert data["focusInside"] is True, "the overlay does not take the focus"
    assert data["focusTrapped"] is True, "the overlay does not trap the focus"
    assert data["focusAfterTab"] is True, "Tab leaves the overlay"
    assert data["overlayClosed"] is True, "the overlay does not close"
    assert data["focusRestored"] is True, (
        "the focus is not given back when the overlay closes"
    )


# --------------------------------------------------------------------------
# 7. The matcher.
# --------------------------------------------------------------------------


def test_the_matcher_respects_the_modifiers_and_the_typing():
    """A command fires only when it is meant to fire."""
    data = _behaviour()
    assert data["plainB"] is True, "a plain letter must fire its command"
    assert data["ctrlB"] is False, "ctrl must not fire an unmodified command"
    assert data["typingB"] is False, "a key typed in a field must be ignored"
    assert data["ctrlS"] is True, "ctrl+s must reach its command"
    assert data["plainS"] is False, "s alone must not save a preset"
    assert data["space"] is True
    assert data["spaceOnButton"] is False, (
        "a focused button must keep the space"
    )
    assert data["spaceOnInput"] is False, (
        "a field must keep the space"
    )
    assert data["escape"] is True
    assert data["deleteKey"] is True
    assert data["ctrlZ"] is True, "ctrl+z must reach the undo"
    assert data["ctrlShiftZ"] is True, "ctrl+shift+z must reach the redo"
    assert data["ctrlShiftZIsNotUndo"] is False, (
        "ctrl+shift+z must not reach the undo as well — otherwise the redo "
        "can never be reached from the keyboard"
    )
    assert data["ctrlZIsNotRedo"] is False, "ctrl+z must not reach the redo"
    assert data["upperCtrlShiftZ"] is True, (
        "a browser reports the shifted key in upper case, and the redo must "
        "still be reached"
    )
    assert data["ctrlZAlone"] is False, "z alone must not undo"
    assert data["ctrlBracketLeft"] is True, "ctrl+[ must move the module"
    assert data["ctrlBracketRight"] is True, "ctrl+] must move the module"
    assert data["plainBracketLeft"] is True, "[ must select the module"
    assert data["viewF"] == "view.toggleFreqSelection"
    assert data["globalF"] is None
    assert data["panelT"] == "inspector.cycleTabs"
    assert data["viewT"] == "view.toggleTimeSelection"
    assert data["globalT"] is None
    assert data["typingResolve"] is None, "a typed key must not resolve"


def test_the_map_is_installed_without_a_document():
    """The module works head-less: the harness imports it."""
    data = _node_json(
        (
            "const mod = await import(process.argv[1]);"
            "process.stdout.write(JSON.stringify({"
            "  install: typeof mod.installKeymap, "
            "  commands: mod.COMMANDS.length,"
            "}));"
        ).replace("process.argv[1]", json.dumps(KEYMAP_JS.as_uri()))
    )
    assert data["install"] == "function"
    assert data["commands"] > 0
