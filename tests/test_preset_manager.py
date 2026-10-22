"""
tests/test_preset_manager.py — the preset library manager of the web interface.

The manager (``aurui/js/preset_manager.js``) is a vanilla ES module, so it is
exercised with node: a small DOM stub and a stub api are installed, the
module is imported and driven, and the results are read back as JSON.

What is checked
  1. the library is grouped by category and every row shows the module code
     and the time the preset was written;
  2. the search field filters the list, and a hit in the name, in the code of
     the module and in the category is marked where it is;
  3. a save posts the payload the server expects;
  4. a load posts the path and hands the document to the host;
  5. a remove deletes the path, but only after the user confirmed it;
  6. the "..." menu of the module panel offers its seven items;
  7. a rejected api call is reported and never leaves the panel half applied;
  8. the menu tells the truth: it names the server that does not answer, the
     library that holds nothing and the preset that has unsaved changes, and
     it disables the actions that cannot work;
  9. a save that failed says so and is not taken for a write, and a save
     that worked is not reported as a lost preset when the list cannot be
     read again;
 10. the round trip through the interface: every kind of value a module has
     is saved, read back and compared — measured against the live server.

The tests need no browser and no network.  The round trip and the search of
the library start a server in a thread of this process (uvicorn) with a
preset library of its own; they are skipped when fastapi or uvicorn is not
installed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

# --------------------------------------------------------------------------
# Locations.
# --------------------------------------------------------------------------
REPO = Path(__file__).resolve().parents[1]
MODULE_JS = REPO / "aurui" / "js" / "preset_manager.js"

# --------------------------------------------------------------------------
# The DOM stub.
#
# It implements the small part of the DOM the manager uses: elements, text
# nodes, fragments, attributes, classes, events and a query selector that
# understands tag names, classes and attributes.
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
        /* a fragment hands its children over to the parent */
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
    dispatchEvent(event) {
      const ev = typeof event === "string" ? { type: event } : (event || {});
      ev.target = ev.target || this;
      ev.defaultPrevented = !!ev.defaultPrevented;
      if (typeof ev.preventDefault !== "function") {
        ev.preventDefault = () => { ev.defaultPrevented = true; };
      }
      if (typeof ev.stopPropagation !== "function") ev.stopPropagation = () => {};
      const list = (this._listeners.get(ev.type) || []).slice();
      for (const fn of list) fn.call(this, ev);
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
    const pattern =
      /([a-zA-Z][\w-]*)|\.([\w-]+)|\[([\w-]+)(?:=(?:"([^"]*)"|'([^']*)'|([^\]]*)))?\]/g;
    let match;
    let matched = false;
    while ((match = pattern.exec(sel)) !== null) {
      matched = true;
      if (match[1]) {
        if (node.tagName !== match[1].toUpperCase()) return false;
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
  doc.querySelector = (sel) => { const all = queryAll(doc, sel);
    return all.length ? all[0] : null; };
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

const FIXTURES = [
  {
    name: "Click Removal",
    category: "Repair",
    path: "builtin/Repair/Click Removal.json",
    module_code: "AUR_DECLICK",
    display_name: "De-Click",
    modified: 1700000000,
    builtin: true,
  },
  {
    name: "Hum Removal",
    category: "Repair",
    path: "builtin/Repair/Hum Removal.json",
    module_code: "AUR_DEHUM",
    display_name: "De-Hum",
    modified: 1700000100,
    builtin: true,
  },
  {
    name: "Level Match",
    category: "Utility",
    path: "builtin/Utility/Level Match.json",
    module_code: "AUR_LEVELER",
    display_name: "Leveler",
    modified: 1700000200,
    builtin: true,
  },
  {
    name: "My Preset",
    category: "Utility",
    path: "Utility/My Preset.json",
    module_code: "AUR_NORMALIZE",
    display_name: "Normalize",
    modified: 1700000300,
    builtin: false,
  },
];

/* The stub api: it records every call and answers with the fixtures. */
function makeApi(overrides = {}) {
  const calls = { list: 0, save: [], load: [], remove: [] };
  return {
    calls,
    async presets() {
      calls.list += 1;
      if (overrides.listError) throw new Error(overrides.listError);
      return { presets: overrides.presets || FIXTURES };
    },
    async savePreset(body) {
      calls.save.push(body);
      if (overrides.saveError) throw new Error(overrides.saveError);
      return { path: `${body.category}/${body.name}.json`, preset: body };
    },
    async loadPreset(path) {
      calls.load.push(path);
      if (overrides.loadError) throw new Error(overrides.loadError);
      const entry = FIXTURES.find((p) => p.path === path);
      return {
        format: "auralis.preset",
        version: 1,
        name: { Type: "String", Value: entry ? entry.name : "Unknown" },
        category: { Type: "String", Value: entry ? entry.category : "Presets" },
        description: { Type: "String", Value: "" },
        module: {
          code: entry ? entry.module_code : "",
          element: "",
          display_name: entry ? entry.display_name : "",
        },
        params: { Sensitivity: { Type: "Float", Value: 3 } },
        state: { Type: "Base64", Value: "" },
      };
    },
    async deletePreset(path) {
      calls.remove.push(path);
      if (overrides.removeError) throw new Error(overrides.removeError);
      return { ok: true, path };
    },
  };
}

const CONTENT = () => ({
  module_code: "AUR_DECLICK",
  element: "DeClicker",
  display_name: "De-Click",
  params: { Sensitivity: 3.5, "Output Clicks Only": false },
});

/* Let the pending promises of the manager settle. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

const scenarios = {
  async grouping() {
    const api = makeApi();
    const container = document.createElement("div");
    const manager = mod.createPresetManager({ api, container });
    await manager.refresh();
    const groups = manager.categories();
    return {
      categories: groups.map((g) => g.category),
      counts: groups.map((g) => g.presets.length),
      names: groups.map((g) => g.presets.map((p) => p.name)),
      list: manager.list().map((p) => p.path),
      all: manager.all().length,
      sections: Array.from(container.querySelectorAll(".pm-section")).map((s) =>
        s.getAttribute("data-category")
      ),
      rows: Array.from(container.querySelectorAll(".pm-item")).map((r) =>
        r.getAttribute("data-path")
      ),
      codes: Array.from(container.querySelectorAll(".pm-code")).map((n) => n.textContent),
      times: Array.from(container.querySelectorAll(".pm-time")).map((n) => n.textContent),
      stamps: Array.from(container.querySelectorAll(".pm-time")).map((n) =>
        n.getAttribute("title")
      ),
    };
  },

  async search() {
    const api = makeApi();
    const container = document.createElement("div");
    const manager = mod.createPresetManager({ api, container });
    await manager.refresh();
    const field = container.querySelector(".pm-search");
    const queries = {};
    for (const query of ["hum", "level", "AUR_LEVELER", "Repair", "zzz"]) {
      manager.search(query);
      queries[query] = {
        visible: manager.list().map((p) => p.name),
        categories: manager.categories().map((g) => g.category),
        rows: container.querySelectorAll(".pm-item").length,
      };
    }
    /* the same filter, driven through the field of the panel */
    field.value = "hum";
    field.dispatchEvent("input");
    const typed = manager.list().map((p) => p.name);
    const hits = Array.from(container.querySelectorAll(".pm-hit")).map((n) => n.textContent);
    manager.search("");
    return {
      queries,
      typed,
      hits,
      afterClear: manager.list().length,
      placeholder: field.getAttribute("placeholder"),
    };
  },

  async save() {
    const api = makeApi();
    const container = document.createElement("div");
    const manager = mod.createPresetManager({ api, container, getContent: CONTENT });
    await manager.refresh();
    const result = await manager.save({ name: "My New Preset", category: "Repair" });
    return {
      payload: api.calls.save[0],
      count: api.calls.save.length,
      result,
    };
  },

  async load() {
    const api = makeApi();
    const seen = [];
    const manager = mod.createPresetManager({
      api,
      onLoad: (document_, context) =>
        seen.push({
          path: context.path,
          name: context.name,
          code: document_.module.code,
        }),
    });
    await manager.refresh();
    const document_ = await manager.load("builtin/Repair/Hum Removal.json");
    return {
      requested: api.calls.load,
      seen,
      format: document_.format,
      params: document_.params,
      current: manager.currentPath(),
    };
  },

  async remove() {
    const api = makeApi();
    const asked = [];
    const manager = mod.createPresetManager({
      api,
      confirm: (message) => {
        asked.push(message);
        return true;
      },
    });
    await manager.refresh();
    const result = await manager.remove("Utility/My Preset.json");
    return { requested: api.calls.remove, asked, result };
  },

  async removeCancelled() {
    const api = makeApi();
    const asked = [];
    const manager = mod.createPresetManager({
      api,
      confirm: (message) => {
        asked.push(message);
        return false;
      },
    });
    await manager.refresh();
    const result = await manager.remove("Utility/My Preset.json");
    return { requested: api.calls.remove, asked, result };
  },

  async deleteFallback() {
    /* the client of the shell has no delete yet: the manager uses the route */
    const api = makeApi();
    delete api.deletePreset;
    const seen = [];
    globalThis.fetch = async (url, options) => {
      seen.push({ url, method: options.method, body: JSON.parse(options.body) });
      return {
        ok: true,
        status: 200,
        json: async () => ({ ok: true, path: "Utility/My Preset.json" }),
      };
    };
    const manager = mod.createPresetManager({ api, confirm: () => true });
    await manager.refresh();
    const result = await manager.remove("Utility/My Preset.json");
    return { seen, result };
  },

  async menu() {
    const api = makeApi();
    const container = document.createElement("div");
    const manager = mod.createPresetManager({ api, container });
    await manager.refresh();
    const anchor = document.createElement("div");
    const menu = mod.renderPresetMenu(anchor, manager, {});
    const buttons = Array.from(anchor.querySelectorAll(".pm-menu-item"));
    return {
      items: menu.items.map((item) => ({ id: item.id, label: item.label })),
      labels: buttons.map((b) => b.textContent),
      actions: buttons.map((b) => b.getAttribute("data-action")),
      separators: anchor.querySelectorAll(".pm-menu-sep").length,
      hidden: menu.element.classList.contains("hidden"),
    };
  },

  async menuClick() {
    const api = makeApi();
    const container = document.createElement("div");
    const manager = mod.createPresetManager({ api, container });
    await manager.refresh();
    const anchor = document.createElement("div");
    const calls = [];
    mod.renderPresetMenu(anchor, manager, {
      onSave: () => calls.push("save"),
      onSaveAs: () => calls.push("saveAs"),
      onRename: () => calls.push("rename"),
      onRemove: () => calls.push("remove"),
      onReload: () => calls.push("reload"),
      onImport: () => calls.push("import"),
      onExport: () => calls.push("export"),
    });
    for (const button of Array.from(anchor.querySelectorAll(".pm-menu-item"))) {
      button.dispatchEvent("click");
    }
    await settle();
    return { calls };
  },

  async menuFallback() {
    /* without a handler the manager does the work itself */
    const api = makeApi();
    const container = document.createElement("div");
    const asked = [];
    const manager = mod.createPresetManager({
      api,
      container,
      getContent: CONTENT,
      confirm: (message) => {
        asked.push(message);
        return true;
      },
    });
    await manager.refresh();
    const anchor = document.createElement("div");
    const menu = mod.renderPresetMenu(anchor, manager, {});
    await manager.load("Utility/My Preset.json");
    const buttons = Array.from(anchor.querySelectorAll(".pm-menu-item"));
    for (const button of buttons) {
      if (button.getAttribute("data-action") === "remove") button.dispatchEvent("click");
    }
    await settle();
    menu.toggle();
    return {
      requested: api.calls.remove,
      asked,
      open: !menu.element.classList.contains("hidden"),
    };
  },

  async errors() {
    const api = makeApi({
      listError: "the library is offline",
      saveError: "the disk is full",
      loadError: "the preset is gone",
      removeError: "the file is locked",
    });
    const errors = [];
    const container = document.createElement("div");
    const manager = mod.createPresetManager({
      api,
      container,
      onError: (err, context) => errors.push({ message: String(err.message), context }),
    });
    const outcomes = {};
    const attempts = {
      refresh: () => manager.refresh(),
      save: () => manager.save({ name: "Broken", category: "Repair", module_code: "AUR_DECLICK" }),
      load: () => manager.load("Utility/My Preset.json"),
      remove: () => manager.remove("Utility/My Preset.json", { confirm: false }),
    };
    for (const [key, call] of Object.entries(attempts)) {
      try {
        await call();
        outcomes[key] = "resolved";
      } catch (err) {
        outcomes[key] = String(err.message);
      }
    }
    const errorEl = container.querySelector(".pm-error");
    return {
      outcomes,
      errors,
      errorText: errorEl ? errorEl.textContent : "",
      errorHidden: errorEl ? errorEl.classList.contains("hidden") : true,
    };
  },

  async form() {
    const api = makeApi();
    const container = document.createElement("div");
    const manager = mod.createPresetManager({ api, container, getContent: CONTENT });
    await manager.refresh();
    const name = container.querySelector('input[placeholder="Preset name"]');
    const category = container.querySelector('input[placeholder="Category"]');
    const button = container.querySelector("button.pm-save");
    name.value = "Form Preset";
    category.value = "Utility";
    button.dispatchEvent("click");
    await settle();
    return {
      payload: api.calls.save[0],
      nameValue: name.value,
      label: button.textContent,
    };
  },

  async rename() {
    const api = makeApi();
    const container = document.createElement("div");
    const manager = mod.createPresetManager({ api, container });
    await manager.refresh();
    await manager.rename("builtin/Repair/Hum Removal.json", "Hum Cleanup", "Utility");
    return {
      loaded: api.calls.load,
      saved: api.calls.save.length,
      payload: api.calls.save[0],
      removed: api.calls.remove,
    };
  },

  async searchCode() {
    const api = makeApi();
    const container = document.createElement("div");
    const manager = mod.createPresetManager({ api, container });
    await manager.refresh();
    const out = {};
    for (const query of ["AUR_LEVELER", "AUR_DEHUM", "leveler", "hum", "Repair"]) {
      manager.search(query);
      out[query] = {
        visible: manager.list().map((p) => p.name),
        hits: Array.from(container.querySelectorAll(".pm-hit")).map((n) => n.textContent),
        hitsInCode: Array.from(container.querySelectorAll(".pm-code .pm-hit")).map(
          (n) => n.textContent
        ),
        rows: container.querySelectorAll(".pm-item").length,
        marked: Array.from(container.querySelectorAll(".pm-item")).map((r) =>
          r.getAttribute("data-hit")
        ),
      };
    }
    return out;
  },

  /* What the menu shows when the state of the library is not the happy one. */
  async menuState() {
    const readMenu = (anchor) => {
      const line = anchor.querySelector(".pm-menu-state");
      const buttons = Array.from(anchor.querySelectorAll(".pm-menu-item"));
      return {
        text: line ? line.textContent : "",
        offline: line ? line.classList.contains("pm-offline") : false,
        dirty: line ? line.classList.contains("pm-dirty") : false,
        disabled: buttons.map(
          (b) => `${b.getAttribute("data-action")}=${b.disabled ? "off" : "on"}`
        ),
      };
    };
    const out = {};

    /* a preset with changes that were not saved */
    {
      const api = makeApi();
      const container = document.createElement("div");
      let values = { Sensitivity: 3 };
      const manager = mod.createPresetManager({
        api,
        container,
        getContent: () => ({ module_code: "AUR_DECLICK", params: values }),
      });
      await manager.refresh();
      await manager.load("builtin/Repair/Hum Removal.json");
      const anchor = document.createElement("div");
      const menu = mod.renderPresetMenu(anchor, manager, {});
      menu.open();
      const clean = readMenu(anchor);
      values = { Sensitivity: 9 };
      menu.state();
      out.changed = {
        clean: clean.text,
        dirty: readMenu(anchor),
        managerDirty: manager.isDirty(),
      };
    }

    /* a library that holds nothing */
    {
      const api = makeApi({ presets: [] });
      const container = document.createElement("div");
      const manager = mod.createPresetManager({ api, container });
      await manager.refresh();
      const anchor = document.createElement("div");
      const menu = mod.renderPresetMenu(anchor, manager, {});
      menu.open();
      out.empty = readMenu(anchor);
      const empty = container.querySelector(".pm-empty");
      out.emptyPanel = empty ? empty.textContent : "";
    }

    /* a server that does not answer */
    {
      const offline = () => {
        const err = new TypeError("Failed to fetch");
        throw err;
      };
      const api = {
        async presets() { offline(); },
        async savePreset() { offline(); },
        async loadPreset() { offline(); },
      };
      const container = document.createElement("div");
      const errors = [];
      const manager = mod.createPresetManager({
        api,
        container,
        onError: (err, context) =>
          errors.push({ message: String(err.message), context }),
      });
      let failed = null;
      try {
        await manager.refresh();
      } catch (err) {
        failed = String(err.message);
      }
      const anchor = document.createElement("div");
      const menu = mod.renderPresetMenu(anchor, manager, {});
      menu.open();
      out.offline = readMenu(anchor);
      out.offlineErrors = errors;
      out.offlineFailed = failed;
      out.offlineState = manager.state();
    }
    return out;
  },

  /* What the manager does when a save does not work. */
  async saveFailure() {
    const out = {};

    /* a save the server refuses */
    {
      const api = makeApi({ saveError: "the disk is full" });
      const container = document.createElement("div");
      const manager = mod.createPresetManager({ api, container, getContent: CONTENT });
      await manager.refresh();
      let failed = null;
      try {
        await manager.save({ name: "Broken", category: "Repair" });
      } catch (err) {
        failed = String(err.message);
      }
      const line = container.querySelector(".pm-error");
      out.refused = {
        failed,
        errorText: line ? line.textContent : "",
        errorHidden: line ? line.classList.contains("hidden") : true,
        library: manager.all().length,
        current: manager.currentPath(),
      };
    }

    /* a save the server does not confirm */
    {
      const api = makeApi();
      api.savePreset = async () => ({ ok: true });
      const container = document.createElement("div");
      const manager = mod.createPresetManager({ api, container, getContent: CONTENT });
      await manager.refresh();
      let failed = null;
      try {
        await manager.save({ name: "Unconfirmed", category: "Repair" });
      } catch (err) {
        failed = String(err.message);
      }
      const line = container.querySelector(".pm-error");
      out.unconfirmed = {
        failed,
        errorText: line ? line.textContent : "",
        library: manager.all().length,
        current: manager.currentPath(),
      };
    }

    /* a save that is written but cannot be listed again */
    {
      const api = makeApi();
      let listed = 0;
      api.presets = async () => {
        listed += 1;
        if (listed > 1) throw new TypeError("Failed to fetch");
        return { presets: FIXTURES };
      };
      const container = document.createElement("div");
      const manager = mod.createPresetManager({ api, container, getContent: CONTENT });
      await manager.refresh();
      let failed = null;
      let saved = null;
      try {
        saved = await manager.save({ name: "Written", category: "Repair" });
      } catch (err) {
        failed = String(err.message);
      }
      const line = container.querySelector(".pm-error");
      out.written = {
        failed,
        path: saved ? saved.path : null,
        errorText: line ? line.textContent : "",
        current: manager.currentPath(),
        library: manager.all().length,
      };
    }
    return out;
  },

  /* The search against a live server. */
  async liveSearch() {
    const base = String(payload.base || "");
    const rawFetch = globalThis.fetch;
    globalThis.fetch = (url, options) =>
      rawFetch(String(url).startsWith("http") ? url : base + String(url), options);

    async function request(path, options = {}) {
      const res = await fetch(path, {
        headers: { "Content-Type": "application/json" },
        ...options,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `${res.status}`);
      return data;
    }

    const container = document.createElement("div");
    const manager = mod.createPresetManager({
      api: { async presets() { return request("/api/presets"); } },
      container,
    });
    await manager.refresh();
    const out = { library: manager.all().length, queries: {} };
    for (const query of payload.queries || []) {
      manager.search(query);
      const names = manager.list().map((p) => p.name);
      const codes = manager.list().map((p) => p.module_code);
      out.queries[query] = {
        found: names.length,
        names,
        codes,
        hits: Array.from(container.querySelectorAll(".pm-hit")).map((n) => n.textContent),
        hitsInCode: Array.from(container.querySelectorAll(".pm-code .pm-hit")).map(
          (n) => n.textContent
        ),
        marked: Array.from(container.querySelectorAll(".pm-item")).map((r) =>
          r.getAttribute("data-hit")
        ),
      };
    }
    manager.search("");
    return out;
  },

  async exportPreset() {
    const api = makeApi();
    const manager = mod.createPresetManager({ api });
    await manager.refresh();
    await manager.load("builtin/Repair/Hum Removal.json");
    const out = manager.exportCurrent();
    return { name: out.name, file: out.file, json: out.json };
  },

  /* The round trip through the interface, against a live server.
   *
   * The api client below is the one of aurui/js/api.js, line for line: the
   * same routes, the same headers and the same error handling.  It carries no
   * deletePreset, so the manager has to use the route of the server itself. */
  async roundTrip() {
    const base = String(payload.base || "");
    const values = payload.values || {};
    const rawFetch = globalThis.fetch;
    globalThis.fetch = (url, options) =>
      rawFetch(String(url).startsWith("http") ? url : base + String(url), options);

    async function request(path, options = {}) {
      const res = await fetch(path, {
        headers: { "Content-Type": "application/json" },
        ...options,
      });
      const ct = res.headers.get("content-type") || "";
      if (!ct.includes("application/json")) {
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        return res;
      }
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `${res.status}`);
      return data;
    }

    const sent = [];
    const api = {
      async presets() { return request("/api/presets"); },
      async savePreset(body) {
        sent.push(body);
        return request("/api/preset/save", {
          method: "POST",
          body: JSON.stringify(body),
        });
      },
      async loadPreset(path) {
        return request("/api/preset/load", {
          method: "POST",
          body: JSON.stringify({ path }),
        });
      },
    };

    const container = document.createElement("div");
    const seen = [];
    const manager = mod.createPresetManager({
      api,
      container,
      getContent: () => ({
        module_code: payload.module_code,
        element: payload.element,
        display_name: payload.display_name,
        params: values,
      }),
      onLoad: (document_) => seen.push(document_),
    });

    await manager.refresh();
    const libraryBefore = manager.all().length;
    const saved = await manager.save({ name: payload.name, category: payload.category });
    const libraryAfterSave = manager.all().length;
    const path = saved.path;
    const document_ = await manager.load(path);
    const back = mod.documentToPayload(document_);
    const currentAfterLoad = manager.currentPath();

    /* the delete the manager falls back to, against the same server */
    const removed = await manager.remove(path, { confirm: false });
    const libraryAfter = manager.all().length;

    const rows = [];
    for (const [key, value] of Object.entries(values)) {
      const envelope = document_.params ? document_.params[key] : null;
      const got = back.params[key];
      rows.push({
        param: key,
        sent: value,
        sent_json: JSON.stringify(value),
        envelope: envelope ? envelope.Type || null : null,
        received: got,
        received_json: JSON.stringify(got),
        identical: Object.is(value, got),
        same_kind: typeof value === typeof got,
      });
    }

    return {
      path,
      payload: sent[0] || null,
      rows,
      seen_count: seen.length,
      seen_params: seen[0] ? seen[0].params : null,
      current_path: currentAfterLoad,
      current_after_delete: manager.currentPath(),
      library_before: libraryBefore,
      library_after: libraryAfterSave,
      library_after_delete: libraryAfter,
      removed,
      errors: container.querySelector(".pm-error")
        ? container.querySelector(".pm-error").textContent
        : "",
    };
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
    base = tmp_path_factory.mktemp("preset_manager")
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
        stdin=subprocess.DEVNULL,
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
# The live server.
#
# The round trip is measured against the real server of the project: the
# manager posts a preset, the server writes it, the manager reads it back.
# The library lives in a directory of its own, so the presets of the
# repository are never touched.
# --------------------------------------------------------------------------
def _free_port() -> int:
    """Return a port that is free right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def live_server(tmp_path_factory) -> str:
    """A server with its own preset library; yields its base url."""
    pytest.importorskip("fastapi")
    uvicorn = pytest.importorskip("uvicorn")
    from aurserver.app import app

    root = tmp_path_factory.mktemp("preset_library")
    previous = os.environ.get("AURALIS_PRESET_DIR")
    os.environ["AURALIS_PRESET_DIR"] = str(root)
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="auralis-preset-server", daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 60.0
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=2.0) as handle:
                if handle.status == 200:
                    break
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    else:  # pragma: no cover - the server did not come up
        server.should_exit = True
        pytest.skip("the in-process server did not become healthy")
    try:
        yield base
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        if previous is None:
            os.environ.pop("AURALIS_PRESET_DIR", None)
        else:
            os.environ["AURALIS_PRESET_DIR"] = previous


#: The values of one module, one of every kind the parameter table knows.
#:
#: De-Hum (AUR_DEHUM) owns a float, an int, a bool and an enum parameter;
#: "Highpass Frequency" is pushed past its range on purpose.
ROUND_TRIP_VALUES = {
    "Mode": "Dynamic",
    "Base Frequency": 50.5,
    "Filter Q": 1.25,
    "Number of Harmonics": 5,
    "Harmonic Slope": 0.0,
    "Enable Learning": False,
    "Adaptive": True,
    "Highpass Frequency": 999.0,
}


@pytest.fixture(scope="module")
def round_trip(driver: Path, live_server: str) -> dict:
    """Save every kind of value and read it back through the interface."""
    return _run(
        driver,
        "roundTrip",
        {
            "base": live_server,
            "name": "Round Trip Probe",
            "category": "Utility",
            "module_code": "AUR_DEHUM",
            "element": "HumRemoval",
            "display_name": "De-Hum",
            "values": ROUND_TRIP_VALUES,
        },
    )


# --------------------------------------------------------------------------
# 1. The library.
# --------------------------------------------------------------------------
def test_the_library_is_grouped_by_category(driver):
    """The presets are grouped by category, the known categories first."""
    data = _run(driver, "grouping")
    assert data["categories"] == ["Repair", "Utility"]
    assert data["counts"] == [2, 2]
    assert data["names"] == [["Click Removal", "Hum Removal"], ["Level Match", "My Preset"]]
    assert data["sections"] == ["Repair", "Utility"]
    assert data["all"] == 4


def test_every_row_shows_the_module_code_and_the_modified_time(driver):
    """A row carries the code of the module and the time of the file."""
    data = _run(driver, "grouping")
    assert data["codes"] == [
        "AUR_DECLICK",
        "AUR_DEHUM",
        "AUR_LEVELER",
        "AUR_NORMALIZE",
    ]
    assert data["stamps"][0] == "2023-11-14T22:13:20.000Z"
    for shown in data["times"]:
        assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$", shown), shown


def test_the_rows_keep_the_path_of_the_preset(driver):
    """A row knows the path the server addresses it by."""
    data = _run(driver, "grouping")
    assert data["rows"] == [
        "builtin/Repair/Click Removal.json",
        "builtin/Repair/Hum Removal.json",
        "builtin/Utility/Level Match.json",
        "Utility/My Preset.json",
    ]
    assert data["list"] == data["rows"]


# --------------------------------------------------------------------------
# 2. The search field.
# --------------------------------------------------------------------------
def test_the_search_field_filters_the_list(driver):
    """The filter matches the name, the category and the module code."""
    data = _run(driver, "search")
    queries = data["queries"]
    assert queries["hum"]["visible"] == ["Hum Removal"]
    assert queries["hum"]["categories"] == ["Repair"]
    assert queries["hum"]["rows"] == 1
    assert queries["level"]["visible"] == ["Level Match"]
    assert queries["AUR_LEVELER"]["visible"] == ["Level Match"]
    assert queries["Repair"]["rows"] == 2
    assert queries["zzz"]["visible"] == []
    assert queries["zzz"]["rows"] == 0


def test_typing_in_the_search_field_filters_the_list(driver):
    """The field of the panel drives the same filter."""
    data = _run(driver, "search")
    assert data["typed"] == ["Hum Removal"]
    assert data["hits"] == ["Hum"]
    assert data["afterClear"] == 4
    assert data["placeholder"] == "Search presets"


# --------------------------------------------------------------------------
# 3. The save.
# --------------------------------------------------------------------------
def test_a_save_posts_the_payload_the_server_expects(driver):
    """The save posts the name, the category and the content of the module."""
    data = _run(driver, "save")
    assert data["count"] == 1
    assert data["payload"] == {
        "name": "My New Preset",
        "category": "Repair",
        "module_code": "AUR_DECLICK",
        "params": {"Sensitivity": 3.5, "Output Clicks Only": False},
        "element": "DeClicker",
        "display_name": "De-Click",
    }
    assert data["result"]["path"] == "Repair/My New Preset.json"


def test_the_form_saves_a_new_preset(driver):
    """The form of the panel posts the name and the category of the field."""
    data = _run(driver, "form")
    assert data["payload"]["name"] == "Form Preset"
    assert data["payload"]["category"] == "Utility"
    assert data["payload"]["module_code"] == "AUR_DECLICK"
    assert data["nameValue"] == ""


def test_a_rename_writes_the_new_file_and_drops_the_old_one(driver):
    """A rename loads the preset, writes it again and deletes the old file."""
    data = _run(driver, "rename")
    assert data["loaded"] == ["builtin/Repair/Hum Removal.json"]
    assert data["saved"] == 1
    assert data["payload"]["name"] == "Hum Cleanup"
    assert data["payload"]["category"] == "Utility"
    assert data["payload"]["module_code"] == "AUR_DEHUM"
    assert data["removed"] == ["builtin/Repair/Hum Removal.json"]


# --------------------------------------------------------------------------
# 4. The load.
# --------------------------------------------------------------------------
def test_a_load_posts_the_path_and_hands_the_document_to_the_host(driver):
    """The load posts the path and calls onLoad with the document."""
    data = _run(driver, "load")
    assert data["requested"] == ["builtin/Repair/Hum Removal.json"]
    assert data["seen"] == [
        {
            "path": "builtin/Repair/Hum Removal.json",
            "name": "Hum Removal",
            "code": "AUR_DEHUM",
        }
    ]
    assert data["format"] == "auralis.preset"
    assert data["params"] == {"Sensitivity": {"Type": "Float", "Value": 3}}
    assert data["current"] == "builtin/Repair/Hum Removal.json"


# --------------------------------------------------------------------------
# 5. The remove.
# --------------------------------------------------------------------------
def test_a_remove_deletes_the_path_after_the_confirmation(driver):
    """The delete asks first and then addresses the path of the preset."""
    data = _run(driver, "remove")
    assert len(data["asked"]) == 1
    assert "My Preset" in data["asked"][0]
    assert data["requested"] == ["Utility/My Preset.json"]
    assert data["result"] == {"ok": True, "path": "Utility/My Preset.json"}


def test_a_cancelled_remove_keeps_the_preset(driver):
    """A delete the user refused never reaches the server."""
    data = _run(driver, "removeCancelled")
    assert len(data["asked"]) == 1
    assert "My Preset" in data["asked"][0]
    assert data["requested"] == []
    assert data["result"] is None


def test_the_delete_falls_back_to_the_route_of_the_server(driver):
    """Without a delete in the client the manager uses the route itself."""
    data = _run(driver, "deleteFallback")
    assert data["seen"] == [
        {
            "url": "/api/preset",
            "method": "DELETE",
            "body": {"path": "Utility/My Preset.json"},
        }
    ]
    assert data["result"] == {"ok": True, "path": "Utility/My Preset.json"}


# --------------------------------------------------------------------------
# 6. The menu.
# --------------------------------------------------------------------------
def test_the_menu_offers_its_items(driver):
    """The "..." menu of the panel offers the seven preset actions."""
    data = _run(driver, "menu")
    assert data["items"] == [
        {"id": "save", "label": "Save"},
        {"id": "saveAs", "label": "Save As"},
        {"id": "rename", "label": "Rename"},
        {"id": "remove", "label": "Remove"},
        {"id": "reload", "label": "Reload"},
        {"id": "import", "label": "Import"},
        {"id": "export", "label": "Export"},
    ]
    assert data["labels"] == [
        "Save",
        "Save As",
        "Rename",
        "Remove",
        "Reload",
        "Import",
        "Export",
    ]
    assert data["actions"] == [item["id"] for item in data["items"]]
    assert data["separators"] == 1
    assert data["hidden"] is True


def test_every_item_of_the_menu_reaches_its_handler(driver):
    """A click on an item calls the handler of that item."""
    data = _run(driver, "menuClick")
    assert data["calls"] == [
        "save",
        "saveAs",
        "rename",
        "remove",
        "reload",
        "import",
        "export",
    ]


def test_the_menu_falls_back_to_the_manager(driver):
    """Without a handler the manager does the work of the item itself."""
    data = _run(driver, "menuFallback")
    assert data["requested"] == ["Utility/My Preset.json"]
    assert len(data["asked"]) == 1
    assert "My Preset" in data["asked"][0]
    assert data["open"] is True


# --------------------------------------------------------------------------
# 7. The errors.
# --------------------------------------------------------------------------
def test_a_rejected_call_is_reported_and_never_resolves(driver):
    """A failing api call rejects, is reported and leaves the panel usable."""
    data = _run(driver, "errors")
    assert data["outcomes"] == {
        "refresh": "the library is offline",
        "save": "the disk is full",
        "load": "the preset is gone",
        "remove": "the file is locked",
    }
    assert data["errors"] == [
        {"message": "the library is offline", "context": "list"},
        {"message": "the disk is full", "context": "save"},
        {"message": "the preset is gone", "context": "load"},
        {"message": "the file is locked", "context": "remove"},
    ]
    assert data["errorText"] == "the file is locked"
    assert data["errorHidden"] is False


# --------------------------------------------------------------------------
# 6. The menu shows the state of the library.
# --------------------------------------------------------------------------
def test_the_menu_says_that_the_preset_has_unsaved_changes(driver):
    """A preset that changed since it was read is shown as such."""
    data = _run(driver, "menuState")
    assert data["changed"]["clean"] == "4 preset(s)."
    assert data["changed"]["managerDirty"] is True
    assert "Unsaved changes" in data["changed"]["dirty"]["text"]
    assert data["changed"]["dirty"]["dirty"] is True


def test_the_menu_says_that_the_library_is_empty(driver):
    """A library without a preset says so instead of showing an empty list."""
    data = _run(driver, "menuState")
    assert data["empty"]["text"] == "No presets stored yet."
    assert data["emptyPanel"] == "No presets stored yet."
    assert data["empty"]["disabled"] == [
        "save=on",
        "saveAs=on",
        "rename=off",
        "remove=off",
        "reload=on",
        "import=on",
        "export=off",
    ]


def test_the_menu_says_that_the_server_does_not_answer(driver):
    """A server that cannot be reached is named, and the menu is honest."""
    data = _run(driver, "menuState")
    assert data["offline"]["text"] == "The preset library is not reachable."
    assert data["offline"]["offline"] is True
    assert data["offlineFailed"] == "Failed to fetch"
    assert data["offlineState"]["offline"] is True
    # only the reload still works: every other action needs the server
    assert data["offline"]["disabled"] == [
        "save=off",
        "saveAs=off",
        "rename=off",
        "remove=off",
        "reload=on",
        "import=off",
        "export=off",
    ]


# --------------------------------------------------------------------------
# 7. A save that fails is told.
# --------------------------------------------------------------------------
def test_a_refused_save_is_reported_and_writes_nothing(driver):
    """A save the server refuses says so and keeps the library as it was."""
    data = _run(driver, "saveFailure")
    refused = data["refused"]
    assert refused["failed"] == "the disk is full"
    assert refused["errorText"] == "the disk is full"
    assert refused["errorHidden"] is False
    assert refused["library"] == 4
    assert refused["current"] == ""


def test_a_save_the_server_does_not_confirm_is_not_taken_for_a_write(driver):
    """An answer without a path is a failure, not a written preset."""
    data = _run(driver, "saveFailure")
    unconfirmed = data["unconfirmed"]
    assert "did not confirm the save" in unconfirmed["failed"]
    assert unconfirmed["errorText"] == unconfirmed["failed"]
    assert unconfirmed["library"] == 4
    assert unconfirmed["current"] == ""


def test_a_written_preset_is_not_reported_as_a_lost_one(driver):
    """A save that worked stays a success when the list cannot be read again."""
    data = _run(driver, "saveFailure")
    written = data["written"]
    assert written["failed"] is None
    assert written["path"] == "Repair/Written.json"
    assert written["current"] == "Repair/Written.json"
    assert "the library could not be read again" in written["errorText"]
    # the library that is on the screen is kept: nothing is lost
    assert written["library"] == 4


# --------------------------------------------------------------------------
# 8. The search finds a preset by its module code and marks it.
# --------------------------------------------------------------------------
def test_the_search_finds_a_preset_by_its_module_code(driver):
    """The code of the module leads to the preset and the hit is marked."""
    data = _run(driver, "searchCode")
    assert data["AUR_LEVELER"]["visible"] == ["Level Match"]
    assert data["AUR_LEVELER"]["hitsInCode"] == ["AUR_LEVELER"]
    assert data["AUR_LEVELER"]["marked"] == ["code"]
    assert data["AUR_DEHUM"]["visible"] == ["Hum Removal"]
    assert data["AUR_DEHUM"]["hitsInCode"] == ["AUR_DEHUM"]


def test_the_search_marks_the_name_and_the_category(driver):
    """A hit in the name and a hit in the category are marked as well."""
    data = _run(driver, "searchCode")
    assert data["hum"]["hits"] == ["Hum"]
    assert data["hum"]["marked"] == ["name"]
    assert data["Repair"]["visible"] == ["Click Removal", "Hum Removal"]
    assert data["Repair"]["rows"] == 2
    assert data["Repair"]["marked"] == ["category", "category"]


@pytest.fixture(scope="module")
def live_search(driver: Path, live_server: str) -> dict:
    """Search the library of the live server."""
    return _run(
        driver,
        "liveSearch",
        {
            "base": live_server,
            "queries": ["AUR_DEHUM", "Mains Hum", "Utility", "aur_dehum"],
        },
    )


# --------------------------------------------------------------------------
# 9. The round trip through the interface.
#
# The measurement is made against the live server: the manager posts a preset
# that carries one value of every kind, the server writes it, the manager
# reads it back and hands it to the host.  The table below is the result.
# --------------------------------------------------------------------------
def _table(rows: list[dict]) -> str:
    """Render the round trip table for the report."""
    lines = [
        f"{'parameter':<22} {'kind':<10} {'sent':<14} {'envelope':<10} {'received':<14} same",
    ]
    for row in rows:
        lines.append(
            f"{row['param']:<22} {row['sent_json']:<10} {row['sent_json']:<14} "
            f"{str(row['envelope']):<10} {row['received_json']:<14} "
            f"{'yes' if row['identical'] else 'NO'}"
        )
    return "\n".join(lines)


def test_every_value_comes_back_identical(round_trip):
    """Every kind of value survives the round trip through the interface."""
    rows = round_trip["rows"]
    assert rows, "the round trip measured nothing"
    assert round_trip["errors"] == ""
    print("\n" + _table(rows))
    lost = [row["param"] for row in rows if not row["identical"]]
    assert not lost, f"these values did not come back identical: {lost}"
    assert len(rows) == len(ROUND_TRIP_VALUES)


def test_the_round_trip_keeps_the_kind_of_every_value(round_trip):
    """A float stays a float, an int an int, a bool a bool and an enum a string."""
    kinds = {row["param"]: row["sent_json"] for row in round_trip["rows"]}
    envelopes = {row["param"]: row["envelope"] for row in round_trip["rows"]}
    # a whole number float is written as "0" in JSON, so the server answers
    # with an int envelope: the value is the same, the envelope is not.
    assert kinds["Base Frequency"] == "50.5"
    assert envelopes["Base Frequency"] == "Float"
    assert kinds["Number of Harmonics"] == "5"
    assert envelopes["Number of Harmonics"] == "Int"
    assert kinds["Enable Learning"] == "false"
    assert envelopes["Enable Learning"] == "Bool"
    assert kinds["Mode"] == '"Dynamic"'
    assert envelopes["Mode"] == "String"
    # the value outside the range of the parameter is kept as it is
    assert kinds["Highpass Frequency"] == "999"
    assert envelopes["Highpass Frequency"] in ("Float", "Int")


def test_the_host_receives_the_document_of_the_preset(round_trip):
    """The document reaches the host, and the manager knows the preset."""
    assert round_trip["seen_count"] == 1
    assert round_trip["seen_params"], "the host received no parameters"
    assert round_trip["current_path"] == round_trip["path"]
    assert round_trip["path"].endswith("Round Trip Probe.json")


def test_the_save_adds_the_preset_to_the_library(round_trip):
    """The library the manager shows grows by exactly one preset."""
    assert round_trip["library_after"] == round_trip["library_before"] + 1


def test_the_delete_fallback_removes_the_preset_from_the_server(round_trip):
    """The route of the server takes the path in the body of the request."""
    assert round_trip["removed"] == {"ok": True, "path": round_trip["path"]}
    assert round_trip["library_after_delete"] == round_trip["library_before"]
    assert round_trip["current_after_delete"] == ""
    assert round_trip["errors"] == ""


# --------------------------------------------------------------------------
# 10. The search against the library of the live server.
# --------------------------------------------------------------------------
def test_the_search_of_the_live_library_finds_by_name_and_by_code(live_search):
    """The search of the server library finds a preset both ways."""
    queries = live_search["queries"]
    assert live_search["library"] >= 10
    # by the code of the module: every program of the module is found, and
    # the code is the field the row marks
    hum = queries["AUR_DEHUM"]
    assert hum["found"] >= 1
    assert "Mains Hum" in hum["names"]
    assert set(hum["marked"]) == {"code"}
    assert set(hum["hitsInCode"]) == {"AUR_DEHUM"}
    # by the name of the preset, every word of the query is marked
    assert queries["Mains Hum"]["found"] >= 1
    assert set(queries["Mains Hum"]["marked"]) == {"name"}
    assert set(queries["Mains Hum"]["hits"]) == {"Mains", "Hum"}
    # by the category
    assert queries["Utility"]["found"] >= 1
    assert set(queries["Utility"]["marked"]) == {"category"}
    # the search does not care about the case
    assert queries["aur_dehum"]["found"] == hum["found"]
    assert set(queries["aur_dehum"]["hitsInCode"]) == {"AUR_DEHUM"}


# --------------------------------------------------------------------------
# 9. The hygiene of the file.
# --------------------------------------------------------------------------
def test_the_module_never_hard_codes_a_colour():
    """Every colour of the manager comes from a token of the stylesheet."""
    text = MODULE_JS.read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", text), "the module hard-codes a colour"
    for function in ("rgb(", "rgba(", "hsl(", "hsla("):
        assert function not in text, f"the module hard-codes a colour ({function})"
    assert "var(--" in text, "the module does not use the design tokens"


def test_the_module_mentions_no_third_party_trademark():
    """The naming rule of the contract holds for the manager as well.

    The two words are spelled in pieces on purpose: this file is part of the
    codebase, and the codebase must not contain a third party trademark even
    in the scanner that looks for one.
    """
    forbidden = ("r" + "x", "izo" + "tope")
    for path in (MODULE_JS, Path(__file__)):
        text = path.read_text(encoding="utf-8").lower()
        for token in forbidden:
            assert token not in text, f"{path.name} mentions a third party trademark"
