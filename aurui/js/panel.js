/* Auralis — module inspector panel.
 *
 * Vanilla ES module: no dependencies, no build step.
 *
 * The module performs NO DOM access at import time — everything is built
 * inside renderPanel() — so it can be imported by a test harness that only
 * provides a `document` stub.
 *
 * Colours, fonts and sizes come from /css/tokens.css through var(--token);
 * the stylesheet injected below only ever references those custom properties.
 */

import { el } from "./api.js";
import { t } from "./i18n.js";
import {
  knob,
  toggle,
  select,
  slider,
  button,
  truncationLadder,
  getTruncationLadder,
  pickLadderLabel,
} from "./widgets.js";

/* ------------------------------------------------------------------ utils */

/** First defined value among the given keys. */
function pick(obj, ...keys) {
  if (!obj) return undefined;
  for (const k of keys) {
    if (obj[k] !== undefined && obj[k] !== null) return obj[k];
  }
  return undefined;
}

/** Parameter names of the "Output ... Only" analysis switches. */
const ANALYSIS_PARAM = /^output\b.*\bonly$/i;

/** Approximate width of one character of the label font, in px. */
const CHAR_W = 5.4;

/* --------------------------------------------------------- panel stylesheet */

const PANEL_CSS = `
.aw-panel {
  display: flex; flex-direction: column; gap: var(--sp-3);
  position: relative; min-width: 0;
}
.aw-panel-head {
  display: flex; align-items: center; gap: var(--sp-3);
  padding-bottom: var(--sp-3); border-bottom: 1px solid var(--line);
  position: relative;
}
.aw-head-title { display: flex; flex-direction: column; gap: 1px; min-width: 0; flex: 1 1 auto; }
.aw-head-name {
  font: 600 var(--fs-lg)/1.15 var(--font-ui); color: var(--txt-0);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.aw-head-code { font: 400 var(--fs-xs)/1.1 var(--font-mono); color: var(--txt-3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.aw-head-actions { display: flex; align-items: center; gap: var(--sp-2); flex: 0 0 auto; }
.aw-preset {
  font: 500 var(--fs-xs)/1 var(--font-ui); color: var(--txt-1);
  background: var(--bg-2); border: 1px solid var(--line-soft);
  border-radius: var(--radius); padding: 4px 6px; white-space: nowrap;
  max-width: 110px; overflow: hidden; text-overflow: ellipsis;
}
.aw-preset.dirty { color: var(--accent-2); border-color: var(--accent-2); }
.aw-power { position: relative; color: var(--txt-2); }
.aw-power.on { color: var(--accent); border-color: var(--accent); }
.aw-power::before {
  content: ""; position: absolute; inset: 4px;
  border: 1.5px solid currentColor; border-radius: 50%;
  border-top-color: transparent;
}
.aw-power::after {
  content: ""; position: absolute; left: 50%; top: 3px; width: 1.5px; height: 6px;
  background: currentColor; transform: translateX(-50%); border-radius: 1px;
}
.aw-panel-body { display: flex; flex-direction: column; gap: var(--sp-4); }
.aw-group { display: flex; flex-direction: column; gap: var(--sp-2); min-width: 0; }
.aw-group-title {
  font: 600 var(--fs-xs)/1 var(--font-ui); text-transform: uppercase;
  letter-spacing: .08em; color: var(--txt-2);
  padding-bottom: 3px; border-bottom: 1px solid var(--line-soft);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.aw-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(76px, 1fr));
  gap: var(--sp-3) var(--sp-2);
}
.aw-panel-foot {
  display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap;
  border-top: 1px solid var(--line); padding-top: var(--sp-3);
}
.aw-foot-label {
  font: 600 var(--fs-xs)/1 var(--font-ui); text-transform: uppercase;
  letter-spacing: .08em; color: var(--txt-2);
}
.aw-analysis.on { color: var(--accent); border-color: var(--accent); }
.aw-menu {
  position: absolute; right: 0; top: 100%; z-index: 20;
  min-width: 168px; padding: var(--sp-1);
  background: var(--bg-2); border: 1px solid var(--line);
  border-radius: var(--radius); box-shadow: var(--shadow-2);
}
.aw-menu.hidden { display: none; }
.aw-menu-item {
  display: block; width: 100%; text-align: left;
  height: 24px; padding: 0 var(--sp-3);
  border: 0; border-radius: var(--radius); background: transparent;
  color: var(--txt-1); font: 500 var(--fs-sm)/1 var(--font-ui); cursor: pointer;
}
.aw-menu-item:hover { background: var(--sel); color: var(--txt-0); }
`;

const STYLE_ID = "aur-panel-css";
let stylesInstalled = false;

/** Install the panel stylesheet (lazily — never at import time). */
function ensurePanelStyles() {
  if (stylesInstalled) return;
  stylesInstalled = true;
  const doc = typeof document === "undefined" ? null : document;
  if (!doc || typeof doc.createElement !== "function") return;
  if (typeof doc.getElementById === "function" && doc.getElementById(STYLE_ID)) return;
  const style = doc.createElement("style");
  style.id = STYLE_ID;
  style.textContent = PANEL_CSS;
  const host = doc.head || doc.documentElement || doc.body;
  if (host && typeof host.appendChild === "function") host.appendChild(style);
}

/* ------------------------------------------------------------------ panel */

/**
 * Render the module inspector panel into `container`.
 *
 * options:
 *   moduleInfo  ModuleInfo record (code, display_name, params, learn, ...)
 *   values      { paramName: value } — natural units
 *   onParam     (name, value)  — a parameter changed
 *   onBypass    (bypassed)     — the power button was toggled
 *   onLearn     ()             — the Learn button was pressed
 *   onPreset    (action)       — "save" | "load" | "reset" | "compare" | "menu"
 *   onCompare   ()             — the Compare button was pressed
 *   onMenu      ()             — the "..." button was pressed (host menu)
 *   ladder      truncation ladder override (array or generator)
 *   presetName  name shown in the header
 *   savedValues values of the saved preset (for the dirty marker)
 *   dirty       explicit dirty flag (the host owns the preset state)
 *   bypassed    initial bypass state (the power button lights when active)
 *
 * `handlers` may be passed as a third argument; it is merged over the
 * options and may also carry a host model under `module`. A host that also
 * exposes `onPresetName` uses its own `onPreset` for renaming, so that one
 * is never used for menu actions.
 *
 * Returns { update(values), destroy() } — update() refreshes the controls in
 * place, it never re-renders the panel, so a knob cannot jump mid-drag.
 * update() also accepts { presetName, dirty } to refresh the header.
 */
export function renderPanel(container, options = {}, handlers = {}) {
  ensurePanelStyles();

  const opts = options && typeof options === "object" ? options : {};
  // The host may pass its callbacks as an object or as a function that carries
  // them as properties.
  const hnd =
    handlers && (typeof handlers === "object" || typeof handlers === "function")
      ? handlers
      : {};

  // The module record is either moduleInfo (documented contract) or a host
  // model that carries it under `module`.
  const moduleInfo = opts.moduleInfo || opts.module || hnd.module || {};
  const params = Array.isArray(moduleInfo.params)
    ? moduleInfo.params
    : Array.isArray(opts.params)
      ? opts.params
      : [];

  const displayName = String(
    pick(moduleInfo, "display_name", "displayName", "name") ||
      pick(opts, "display_name", "displayName") ||
      t("panel.module", "Module")
  );
  const code = String(pick(moduleInfo, "code") || pick(opts, "code") || "");
  const canLearn = !!pick(moduleInfo, "learn");

  const cb = {
    onParam: hnd.onParam || hnd.onChange || opts.onParam || opts.onChange,
    onBypass: hnd.onBypass || hnd.onEnable || opts.onBypass,
    onLearn: hnd.onLearn || opts.onLearn,
    onCompare: hnd.onCompare || opts.onCompare,
    onMenu: hnd.onMenu || opts.onMenu,
    onRemove: hnd.onRemove || opts.onRemove,
  };
  // Menu actions. A host that exposes `onPresetName` uses its own `onPreset`
  // for renaming — that one is never used for actions.
  const action =
    opts.onPreset || opts.onAction || (hnd.onPresetName ? null : hnd.onPreset) || null;

  let current = Object.assign({}, opts.values || {});
  let bypassed =
    opts.bypassed === undefined
      ? !!(pick(moduleInfo, "bypassed") ?? pick(opts, "bypassed") ?? false)
      : !!opts.bypassed;

  const presetInfo = pick(moduleInfo, "preset") || null;
  let presetName = String(
    opts.presetName !== undefined && opts.presetName !== null
      ? opts.presetName
      : pick(presetInfo, "name") || pick(opts, "preset_name") || t("control.default", "Default")
  );
  const savedValues =
    opts.savedValues ||
    pick(presetInfo, "values") ||
    pick(moduleInfo, "saved_values", "savedValues") ||
    null;
  // An explicit dirty flag from the host wins over the computed one.
  let explicitDirty = typeof opts.dirty === "boolean" ? opts.dirty : null;

  const teardown = [];

  /* ---- helpers ------------------------------------------------------- */

  /** Fallback "saved" state: the parameter defaults. */
  function fallbackSaved() {
    const out = {};
    for (const p of params) {
      if (p && p.name !== undefined) out[p.name] = p.default;
    }
    return out;
  }

  const savedState = savedValues || fallbackSaved();

  function isDirty() {
    if (typeof explicitDirty === "boolean") return explicitDirty;
    const keys = new Set([...Object.keys(current), ...Object.keys(savedState)]);
    for (const k of keys) {
      const a = current[k];
      const b = savedState[k];
      if (typeof a === "number" && typeof b === "number") {
        if (Math.abs(a - b) > 1e-9 * Math.max(1, Math.abs(a))) return true;
      } else if (a !== b) {
        return true;
      }
    }
    return false;
  }

  /** How many characters fit into one control cell. */
  function budget() {
    const w = container && typeof container.clientWidth === "number" ? container.clientWidth : 0;
    if (!w) return 14;
    const inner = Math.max(120, w - 24);
    const cols = inner > 260 ? 3 : inner > 168 ? 2 : 1;
    const cell = (inner - (cols - 1) * 8) / cols;
    return Math.max(8, Math.min(20, Math.floor(cell / CHAR_W)));
  }

  /** Shorten a parameter name with the active ladder. */
  function labelFor(name) {
    const text = String(name || "");
    const max = budget();
    if (text.length <= max) return text;
    const custom = opts.ladder || getTruncationLadder(code) || getTruncationLadder(text);
    const ladder = custom || truncationLadder(text, max);
    return pickLadderLabel(ladder, text, max) || text.slice(0, max);
  }

  /* ---- header -------------------------------------------------------- */

  const root = el("div", { class: "aw-panel" });

  const head = el("div", { class: "aw-panel-head" });
  const titleBox = el("div", { class: "aw-head-title" });
  titleBox.append(el("span", { class: "aw-head-name", text: displayName }));
  if (code) titleBox.append(el("span", { class: "aw-head-code", text: code }));

  const actions = el("div", { class: "aw-head-actions" });

  const powerBtn = el("button", {
    class: "aw-btn aw-btn-icon aw-power",
    type: "button",
    title: t("control.bypass", "Bypass"),
    "aria-label": t("panel.bypassModule", "Bypass {name}", { name: displayName }),
    "aria-pressed": String(!bypassed),
  });

  // The preset name carries the app.css class as well, so the host can find
  // and update it directly.
  const presetEl = el("button", {
    class: "aw-preset preset-name",
    type: "button",
    title: t("panel.presetTitle", "Preset: {name}", { name: presetName }),
    "aria-haspopup": "true",
  });
  presetEl.addEventListener("click", () => {
    if (typeof cb.onMenu === "function") {
      cb.onMenu();
      return;
    }
    toggleMenu();
  });

  actions.append(powerBtn, presetEl);

  if (canLearn) {
    const learnBtn = button({
      label: t("control.learn", "Learn"),
      variant: "sm",
      onClick: () => {
        if (typeof cb.onLearn === "function") cb.onLearn();
      },
    });
    learnBtn.setAttribute("title", t("control.learn.title", "Learn from the current signal"));
    actions.append(learnBtn);
  }

  const compareBtn = button({
    label: t("control.compare", "Compare"),
    variant: "sm",
    onClick: () => {
      if (typeof cb.onCompare === "function") cb.onCompare();
      else if (typeof action === "function") action("compare");
    },
  });
  compareBtn.setAttribute("title", t("control.compare.title", "Compare with the original signal"));
  actions.append(compareBtn);

  const menuBtn = button({
    label: "…",
    variant: "icon",
    onClick: () => {
      if (typeof cb.onMenu === "function") {
        cb.onMenu();
        return;
      }
      toggleMenu();
    },
  });
  menuBtn.setAttribute("title", t("control.moduleMenu", "Module menu"));
  menuBtn.setAttribute("aria-haspopup", "true");
  menuBtn.setAttribute("aria-expanded", "false");
  actions.append(menuBtn);

  head.append(titleBox, actions);

  /* ---- the "..." menu ------------------------------------------------ */

  const menu = el("div", { class: "aw-menu hidden" });
  const menuItems = [];

  // Reset always works: it is driven by the parameter defaults.
  menuItems.push({
    label: t("control.reset", "Reset to Defaults"),
    run: () => {
      for (const p of params) {
        if (!p || p.name === undefined || p.default === undefined) continue;
        const name = String(p.name);
        current[name] = p.default;
        const ctrl = widgets.get(name);
        if (ctrl && typeof ctrl.setValue === "function") ctrl.setValue(p.default);
        if (typeof cb.onParam === "function") cb.onParam(name, p.default);
      }
      paintAnalysis();
      paintPreset();
    },
  });
  if (typeof action === "function") {
    menuItems.push({ label: t("control.savePreset", "Save Preset…"), run: () => action("save") });
    menuItems.push({ label: t("control.loadPreset", "Load Preset…"), run: () => action("load") });
  }
  if (typeof cb.onRemove === "function") {
    menuItems.push({ label: t("control.removeModule", "Remove Module"), run: () => cb.onRemove() });
  }

  for (const item of menuItems) {
    const mi = el("button", { class: "aw-menu-item", type: "button", text: item.label });
    mi.addEventListener("click", () => {
      closeMenu();
      item.run();
    });
    menu.append(mi);
  }
  head.append(menu);

  let menuOpen = false;
  let outsideHandler = null;

  function closeMenu() {
    if (!menuOpen) return;
    menuOpen = false;
    if (menu.classList) menu.classList.add("hidden");
    menuBtn.setAttribute("aria-expanded", "false");
    if (outsideHandler && typeof document !== "undefined" && document.removeEventListener) {
      document.removeEventListener("pointerdown", outsideHandler, true);
      outsideHandler = null;
    }
  }

  function openMenu() {
    menuOpen = true;
    if (menu.classList) menu.classList.remove("hidden");
    menuBtn.setAttribute("aria-expanded", "true");
    if (typeof document !== "undefined" && document.addEventListener) {
      outsideHandler = (ev) => {
        if (menu.contains && ev.target && menu.contains(ev.target)) return;
        closeMenu();
      };
      document.addEventListener("pointerdown", outsideHandler, true);
      teardown.push(() => {
        if (document.removeEventListener) document.removeEventListener("pointerdown", outsideHandler, true);
        outsideHandler = null;
      });
    }
  }

  function toggleMenu() {
    if (menuOpen) closeMenu();
    else openMenu();
    if (typeof action === "function") action("menu", menuOpen);
  }

  /* ---- parameter groups --------------------------------------------- */

  const body = el("div", { class: "aw-panel-body" });
  const widgets = new Map();         // param name -> control element
  const analysisButtons = new Map(); // param name -> toggle button

  const groups = [];
  const groupIndex = new Map();
  const analysisParams = [];

  for (const p of params) {
    if (!p || p.name === undefined || p.name === null) continue;
    const name = String(p.name);
    if (ANALYSIS_PARAM.test(name)) {
      analysisParams.push(p);
      continue;
    }
    const groupName = String(p.group || "") || t("panel.groupGeneral", "General");
    let group = groupIndex.get(groupName);
    if (!group) {
      group = { name: groupName, params: [] };
      groupIndex.set(groupName, group);
      groups.push(group);
    }
    group.params.push(p);
  }

  for (const group of groups) {
    const section = el("section", { class: "aw-group" });
    section.append(el("div", { class: "aw-group-title", text: group.name, title: group.name }));
    const grid = el("div", { class: "aw-grid" });

    for (const p of group.params) {
      const name = String(p.name);
      const ctrl = makeControl(p, name);
      widgets.set(name, ctrl);
      grid.append(ctrl);
    }
    section.append(grid);
    body.append(section);
  }

  /** Build the control that suits a parameter. */
  function makeControl(p, name) {
    const opts2 = {
      desc: p,
      value: current[name],
      label: labelFor(name),
      onChange: (v) => {
        current[name] = v;
        paintPreset();
        if (typeof cb.onParam === "function") cb.onParam(name, v);
      },
    };
    if (p.type === "bool") return toggle(opts2);
    if (p.type === "enum") return select(opts2);
    if (p.unit === "%") return slider(opts2);
    return knob(opts2);
  }

  /* ---- footer: analysis switches ------------------------------------- */

  let foot = null;
  if (analysisParams.length) {
    foot = el("div", { class: "aw-panel-foot" });
    foot.append(el("span", { class: "aw-foot-label", text: t("panel.analysis", "Analysis") }));
    for (const p of analysisParams) {
      const name = String(p.name);
      const b = el("button", {
        class: "aw-btn aw-btn-sm aw-analysis",
        type: "button",
        "aria-pressed": "false",
        text: labelFor(name),
        title: name,
      });
      b.addEventListener("click", () => {
        const nv = !current[name];
        current[name] = nv;
        paintAnalysis();
        if (typeof cb.onParam === "function") cb.onParam(name, nv);
      });
      analysisButtons.set(name, b);
      foot.append(b);
    }
  }

  /* ---- painting ------------------------------------------------------ */

  function paintPreset() {
    const dirty = isDirty();
    const shown = presetName && String(presetName).trim() ? String(presetName) : t("control.default", "Default");
    presetEl.textContent = dirty ? `${shown} *` : shown;
    presetEl.setAttribute(
      "title",
      dirty
        ? t("control.modified", "{name} (modified)", { name: shown })
        : t("panel.presetTitle", "Preset: {name}", { name: shown })
    );
    if (presetEl.classList) presetEl.classList.toggle("dirty", dirty);
  }

  function paintPower() {
    const active = !bypassed;
    if (powerBtn.classList) powerBtn.classList.toggle("on", active);
    powerBtn.setAttribute("aria-pressed", String(active));
    powerBtn.setAttribute("title", active ? t("control.bypass", "Bypass") : t("control.enable", "Enable"));
    powerBtn.setAttribute(
      "aria-label",
      active
        ? t("panel.bypassModule", "Bypass {name}", { name: displayName })
        : t("panel.enableModule", "Enable {name}", { name: displayName })
    );
  }

  function paintAnalysis() {
    for (const [name, b] of analysisButtons) {
      const on = !!current[name];
      if (b.classList) b.classList.toggle("on", on);
      b.setAttribute("aria-pressed", String(on));
    }
  }

  powerBtn.addEventListener("click", () => {
    bypassed = !bypassed;
    paintPower();
    if (typeof cb.onBypass === "function") cb.onBypass(bypassed);
  });

  /* ---- mount --------------------------------------------------------- */

  root.append(head);
  if (groups.length) root.append(body);
  if (foot) root.append(foot);

  if (container) {
    if (typeof container.innerHTML === "string") container.innerHTML = "";
    container.append(root);
  }

  paintPreset();
  paintPower();
  paintAnalysis();

  /* ---- public interface ---------------------------------------------- */

  /**
   * Push new values into the controls without re-rendering.
   * A control that already shows the value is left untouched, so dragging a
   * knob never makes it jump. The object may also carry `presetName` and
   * `dirty` to refresh the header.
   */
  function update(values) {
    const next = values && typeof values === "object" ? values : {};
    if (next.presetName !== undefined && next.presetName !== null) {
      presetName = String(next.presetName);
    }
    if (typeof next.dirty === "boolean") explicitDirty = next.dirty;
    for (const [name, ctrl] of widgets) {
      if (!Object.prototype.hasOwnProperty.call(next, name)) continue;
      const v = next[name];
      current[name] = v;
      if (ctrl && typeof ctrl.setValue === "function") ctrl.setValue(v);
    }
    for (const name of analysisButtons.keys()) {
      if (!Object.prototype.hasOwnProperty.call(next, name)) continue;
      current[name] = next[name];
    }
    paintAnalysis();
    paintPreset();
  }

  /** Remove the panel and release every listener it owns. */
  function destroy() {
    closeMenu();
    for (const fn of teardown.splice(0, teardown.length)) {
      try { fn(); } catch (_) { /* ignore */ }
    }
    widgets.clear();
    analysisButtons.clear();
    if (container) {
      while (container.firstChild) container.removeChild(container.firstChild);
    }
  }

  /** Update the bypass / power state from outside the panel. */
  function setBypassed(state) {
    bypassed = !!state;
    paintPower();
  }

  /** Update the preset name (and the saved state behind the dirty marker). */
  function setPreset(name, values) {
    if (name !== undefined && name !== null) {
      presetName = String(name);
      presetEl.setAttribute("title", t("panel.presetTitle", "Preset: {name}", { name: presetName }));
    }
    if (values && typeof values === "object") {
      for (const k of Object.keys(savedState)) delete savedState[k];
      Object.assign(savedState, values);
    }
    explicitDirty = null;
    paintPreset();
  }

  return { update, destroy, setBypassed, setPreset, root };
}
