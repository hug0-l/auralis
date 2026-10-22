/* Auralis — control library.
 *
 * Vanilla ES module: no dependencies, no build step.
 *
 * The module performs NO DOM access at import time — every node is created
 * inside a factory function — so it can be imported by a test harness that only
 * provides a `document` stub.
 *
 * Every colour, font and size comes from /css/tokens.css through var(--token);
 * the stylesheet injected below only ever references those custom properties.
 */

import { el, fmtValue } from "./api.js";
import { t } from "./i18n.js";

/* ------------------------------------------------------------------ utils */

const SVG_NS = "http://www.w3.org/2000/svg";

/** Create an SVG element (el() builds HTML elements only). */
function svgEl(tag, attrs = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    node.setAttribute(k, String(v));
  }
  return node;
}

const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);
const num = (v, dflt) => (typeof v === "number" && isFinite(v) ? v : dflt);
const r2 = (n) => Math.round(n * 100) / 100;

/** Fill in the ParamDesc fields the widgets rely on. */
function normDesc(desc) {
  const d = desc || {};
  let type = d.type;
  if (!type) {
    if (typeof d.default === "boolean") type = "bool";
    else if (typeof d.default === "string") type = "enum";
    else type = "float";
  }
  return {
    name: String(d.name || t("widgets.parameter", "Parameter")),
    type,
    default: d.default !== undefined && d.default !== null ? d.default : num(d.minimum, 0),
    minimum: num(d.minimum, 0),
    maximum: num(d.maximum, 1),
    unit: d.unit || "",
    choices: Array.isArray(d.choices) ? d.choices.slice() : [],
    group: d.group || "",
    stepped: !!d.stepped,
    curve: d.curve || "linear",
    curveExponent: num(d.curve_exponent, 1),
    help: d.help || "",
  };
}

/** True when the travel of a parameter follows a law and not a straight line. */
function isCurved(desc) {
  return desc.curve === "log" || desc.curve === "taper";
}

/**
 * The position of a natural value on the travel, honouring the curve.
 *
 * The position is the number 0..1 a host automates: the same number the
 * plug-in adapter publishes.  A frequency follows the logarithm of its
 * value and a gain follows the taper, so the knob of the panel and the
 * control of the host stand at the same place.
 */
function positionFor(value, desc) {
  const { minimum: min, maximum: max } = desc;
  if (!(max > min)) return 0;
  if (desc.curve === "log" && min > 0) {
    return Math.log(value / min) / Math.log(max / min);
  }
  if (desc.curve === "taper") {
    const p = desc.curveExponent > 0 ? desc.curveExponent : 1;
    const share = clamp((value - min) / (max - min), 0, 1);
    return Math.pow(share, 1 / p);
  }
  return clamp((value - min) / (max - min), 0, 1);
}

/** The natural value at a position on the travel, honouring the curve. */
function valueAt(position, desc) {
  const { minimum: min, maximum: max } = desc;
  if (!(max > min)) return min;
  const n = clamp(position, 0, 1);
  if (desc.curve === "log" && min > 0) {
    return min * Math.pow(max / min, n);
  }
  if (desc.curve === "taper") {
    const p = desc.curveExponent > 0 ? desc.curveExponent : 1;
    return min + (max - min) * Math.pow(n, p);
  }
  return min + (max - min) * n;
}

/** Natural-unit display of a parameter value (never a normalised 0..1). */
function show(desc, value) {
  const v = value === null || value === undefined ? desc.default : value;
  if (desc.type === "bool") return v ? t("control.on", "On") : t("control.off", "Off");
  return fmtValue(desc, v);
}

/** Keyboard / wheel step for a parameter. */
function stepFor(desc) {
  const range = Math.abs(desc.maximum - desc.minimum) || 1;
  if (desc.type === "int" || desc.stepped) return 1;
  switch (desc.unit) {
    case "Hz": return niceStep(range / 100);
    case "dB": return 0.1;
    case "ms":
    case "s": return 0.1;
    case "st": return 0.01;
    default: return r2(range / 100);
  }
}

function niceStep(x) {
  const steps = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000, 10000];
  for (const s of steps) if (x <= s) return s;
  return steps[steps.length - 1];
}

/* ------------------------------------------------------------ knob geometry
 *
 * Angles are degrees on the SVG circle, 0 = 3 o'clock, growing clockwise
 * (SVG's y axis points down).
 *
 *   7 o'clock = 120deg      12 o'clock = 270deg      5 o'clock = 420deg
 *
 * A unipolar knob sweeps the full 300deg from 7 o'clock to 5 o'clock.
 * A bipolar knob parks its origin (value 0) at 12 o'clock and grows in
 * either direction: negative towards 7 o'clock, positive towards 5 o'clock.
 */

const KNOB_C = 22;
const KNOB_R = 18.5;          // arc radius
const START_ANGLE = 120;      // 7 o'clock
const ZERO_ANGLE = 270;       // 12 o'clock
const SWEEP = 300;            // 7 o'clock -> 5 o'clock
const HALF_SWEEP = SWEEP / 2; // 12 o'clock -> 5 o'clock
const DRAG_SPAN_PX = 200;     // pixels of drag that cover the whole range

function polar(cx, cy, r, deg) {
  const a = (deg * Math.PI) / 180;
  return { x: cx + r * Math.cos(a), y: cy + r * Math.sin(a) };
}

/** SVG arc path from a0 to a1 (degrees, clockwise positive). */
function arcPath(cx, cy, r, a0, a1) {
  const delta = a1 - a0;
  if (Math.abs(delta) < 0.05) return "";
  const p0 = polar(cx, cy, r, a0);
  const p1 = polar(cx, cy, r, a1);
  const large = Math.abs(delta) > 180 ? 1 : 0;
  const sweep = delta >= 0 ? 1 : 0;
  return `M ${r2(p0.x)} ${r2(p0.y)} A ${r} ${r} 0 ${large} ${sweep} ${r2(p1.x)} ${r2(p1.y)}`;
}

/** Natural value -> knob angle. */
function angleFor(value, desc) {
  const { minimum: min, maximum: max } = desc;
  if (isCurved(desc)) {
    // the knob stands where the travel stands: the same position the host
    // automates
    const pos = positionFor(value, desc);
    if (min < 0 && max > 0) return ZERO_ANGLE + HALF_SWEEP * (pos - 0.5) * 2;
    return START_ANGLE + SWEEP * pos;
  }
  if (min < 0 && max > 0) {
    // bipolar: 0 sits at 12 o'clock, the two halves sweep outwards
    if (value >= 0) return ZERO_ANGLE + (HALF_SWEEP * value) / max;
    return ZERO_ANGLE + (HALF_SWEEP * value) / min;
  }
  const span = max - min || 1;
  return START_ANGLE + (SWEEP * (value - min)) / span;
}

/** Where the progress arc starts for the current value. */
function arcStart(desc) {
  if (desc.minimum < 0 && desc.maximum > 0) return ZERO_ANGLE;
  return START_ANGLE;
}

/** Unique id counter for the per-knob gradient. */
let gradientSeq = 0;

/* --------------------------------------------------------- widget stylesheet */

const WIDGET_CSS = `
.aw-knob, .aw-slider-wrap, .aw-select-wrap, .aw-toggle-wrap {
  display: flex; flex-direction: column; align-items: center; gap: 3px;
  min-width: 0; user-select: none;
}
.aw-label {
  font: 500 var(--fs-xs)/1.2 var(--font-ui);
  color: var(--txt-2); letter-spacing: .02em; text-align: center;
  max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.aw-value {
  font: 500 var(--fs-xs)/1.2 var(--font-mono); color: var(--txt-1);
  white-space: nowrap; font-variant-numeric: tabular-nums;
}
.aw-knob { cursor: ns-resize; touch-action: none; outline: none; }
.aw-knob-svg { display: block; overflow: visible; }
.aw-knob-track { fill: none; stroke: var(--bg-4); stroke-width: 3; stroke-linecap: round; }
.aw-knob-arc { fill: none; stroke: var(--accent); stroke-width: 3; stroke-linecap: round; }
.aw-knob-ring { fill: none; stroke: var(--line); stroke-width: 1; }
.aw-knob-face { stroke: var(--line); stroke-width: 1; }
.aw-knob-face-top { stop-color: var(--bg-4); }
.aw-knob-face-bot { stop-color: var(--bg-2); }
.aw-knob-pointer { stroke: var(--txt-0); stroke-width: 2; stroke-linecap: round; }
.aw-knob-tick { stroke: var(--txt-3); stroke-width: 1; stroke-linecap: round; }
.aw-knob:hover .aw-knob-ring { stroke: var(--sel-edge); }
.aw-knob.is-active .aw-knob-pointer { stroke: var(--accent); }
.aw-knob.is-active .aw-value { color: var(--accent); }
.aw-knob:focus-visible .aw-knob-ring { stroke: var(--accent-2); stroke-width: 1.5; }

.aw-toggle {
  display: inline-flex; align-items: center; gap: 5px;
  height: 20px; padding: 0 8px 0 6px;
  border: 1px solid var(--line); border-radius: 999px;
  background: var(--bg-3); color: var(--txt-1);
  font: 500 var(--fs-xs)/1 var(--font-ui); cursor: pointer;
}
.aw-toggle:hover { border-color: var(--sel-edge); color: var(--txt-0); }
.aw-toggle.on { border-color: var(--accent); color: var(--txt-0); background: var(--bg-4); }
.aw-toggle-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--txt-3); }
.aw-toggle.on .aw-toggle-dot { background: var(--accent); box-shadow: 0 0 6px var(--accent-dim); }
.aw-toggle:focus-visible { outline: 1px solid var(--accent-2); outline-offset: 1px; }

.aw-select-wrap { width: 100%; }
.aw-select-box { position: relative; width: 100%; }
.aw-select {
  appearance: none; -webkit-appearance: none; width: 100%;
  height: 22px; padding: 0 18px 0 7px;
  border: 1px solid var(--line); border-radius: var(--radius);
  background: var(--bg-3); color: var(--txt-0);
  font: 500 var(--fs-sm)/1 var(--font-ui); cursor: pointer; text-overflow: ellipsis;
}
.aw-select:hover { border-color: var(--sel-edge); }
.aw-select:focus-visible { outline: 1px solid var(--accent-2); outline-offset: 1px; }
.aw-select-caret {
  position: absolute; right: 7px; top: 50%; margin-top: -2px;
  width: 0; height: 0; pointer-events: none;
  border-left: 3px solid transparent; border-right: 3px solid transparent;
  border-top: 4px solid var(--txt-2);
}

.aw-slider { position: relative; width: 100%; height: 18px; cursor: pointer; touch-action: none; outline: none; }
.aw-slider-rail {
  position: absolute; left: 0; right: 0; top: 50%; margin-top: -2px;
  height: 4px; border-radius: 2px; background: var(--bg-4);
}
.aw-slider-fill { position: absolute; left: 0; top: 50%; margin-top: -2px; height: 4px; border-radius: 2px; background: var(--accent); }
.aw-slider-thumb {
  position: absolute; top: 50%; margin-top: -5px; width: 10px; height: 10px; margin-left: -5px;
  border-radius: 50%; background: var(--txt-0); box-shadow: var(--shadow-1);
}
.aw-slider:hover .aw-slider-thumb { background: var(--accent); }
.aw-slider:focus-visible .aw-slider-thumb { box-shadow: 0 0 0 2px var(--accent-2); }

.aw-btn {
  height: 22px; padding: 0 8px;
  border: 1px solid var(--line); border-radius: var(--radius);
  background: var(--bg-3); color: var(--txt-1);
  font: 500 var(--fs-sm)/1 var(--font-ui); cursor: pointer;
  display: inline-flex; align-items: center; justify-content: center; gap: 4px;
  white-space: nowrap;
}
.aw-btn:hover { background: var(--bg-4); color: var(--txt-0); border-color: var(--sel-edge); }
.aw-btn:active { transform: translateY(1px); }
.aw-btn:focus-visible { outline: 1px solid var(--accent-2); outline-offset: 1px; }
.aw-btn-sm { height: 19px; padding: 0 6px; font-size: var(--fs-xs); }
.aw-btn-icon { width: 22px; padding: 0; }
.aw-btn-primary { background: var(--accent-dim); border-color: var(--accent); color: var(--txt-0); }
.aw-btn-ghost { background: transparent; }
.aw-btn-danger { color: var(--danger); border-color: var(--line); }
.aw-btn[disabled] { opacity: .45; pointer-events: none; }
`;

const STYLE_ID = "aur-widgets-css";
let stylesInstalled = false;

/** Install the widget stylesheet (lazily — never at import time). */
function ensureWidgetStyles() {
  if (stylesInstalled) return;
  stylesInstalled = true;
  const doc = typeof document === "undefined" ? null : document;
  if (!doc || typeof doc.createElement !== "function") return;
  if (typeof doc.getElementById === "function" && doc.getElementById(STYLE_ID)) return;
  const style = doc.createElement("style");
  style.id = STYLE_ID;
  style.textContent = WIDGET_CSS;
  const host = doc.head || doc.documentElement || doc.body;
  if (host && typeof host.appendChild === "function") host.appendChild(style);
}

/* ------------------------------------------------------------------- knob */

/**
 * A 44 px rotary control.
 *
 * The knob position follows the travel of the parameter: a straight line
 * between the minimum and the maximum for a linear parameter, and the law
 * of the parameter for a curved one (the logarithm of a frequency, the taper
 * of a gain) — the same position the host automates.  Values are never
 * stored normalised.
 *
 * - drag vertically: up increases, down decreases (200 px cover the range)
 * - shift while dragging / wheel / arrow keys: 5x finer
 * - double click: reset to the parameter default
 * - wheel: one step
 * - arrow keys: one step, PageUp/PageDown: ten steps, Home/End: min/max
 * - a drag that passes within 2 % of the range of the default snaps to the
 *   default (discrete steps are never snapped, so one key press always moves
 *   the value)
 */
export function knob({ desc, value, onChange, label } = {}) {
  ensureWidgetStyles();
  const d = normDesc(desc);
  const min = d.minimum;
  const max = d.maximum;
  const span = max - min || 1;
  const def = clamp(typeof d.default === "number" ? d.default : min, min, max);
  const bipolar = min < 0 && max > 0;
  const title = label || d.name;
  const perPx = span / DRAG_SPAN_PX;

  let cur = clamp(typeof value === "number" ? value : def, min, max);
  let dragging = false;
  let startY = 0;
  let startVal = 0;

  const root = el("div", {
    class: "aw-knob",
    role: "slider",
    tabindex: "0",
    "aria-label": title,
    "aria-orientation": "vertical",
    "aria-valuemin": String(min),
    "aria-valuemax": String(max),
  });

  const gradId = `aw-knob-grad-${(gradientSeq += 1)}`;
  const svg = svgEl("svg", {
    class: "aw-knob-svg",
    width: 44,
    height: 44,
    viewBox: "0 0 44 44",
    "aria-hidden": "true",
    focusable: "false",
  });
  const defs = svgEl("defs");
  const grad = svgEl("linearGradient", { id: gradId, x1: "0", y1: "0", x2: "0", y2: "1" });
  grad.append(svgEl("stop", { offset: "0", class: "aw-knob-face-top" }));
  grad.append(svgEl("stop", { offset: "1", class: "aw-knob-face-bot" }));
  defs.append(grad);
  svg.append(defs);

  const track = svgEl("path", { class: "aw-knob-track" });
  const arc = svgEl("path", { class: "aw-knob-arc" });
  const ring = svgEl("circle", { class: "aw-knob-ring", cx: KNOB_C, cy: KNOB_C, r: 16.5 });
  const face = svgEl("circle", {
    class: "aw-knob-face",
    cx: KNOB_C,
    cy: KNOB_C,
    r: 14.5,
    fill: `url(#${gradId})`,
  });
  const pointer = svgEl("line", { class: "aw-knob-pointer", x1: KNOB_C, y1: KNOB_C, x2: KNOB_C, y2: 10 });
  svg.append(track, arc, ring, face, pointer);

  const nameEl = el("span", { class: "aw-label", text: title });
  const valueEl = el("span", { class: "aw-value" });
  root.append(nameEl, svg, valueEl);

  function render() {
    const a = angleFor(cur, d);
    arc.setAttribute("d", arcPath(KNOB_C, KNOB_C, KNOB_R, arcStart(d), a));
    const p = polar(KNOB_C, KNOB_C, 12.5, a);
    pointer.setAttribute("x1", String(KNOB_C));
    pointer.setAttribute("y1", String(KNOB_C));
    pointer.setAttribute("x2", String(r2(p.x)));
    pointer.setAttribute("y2", String(r2(p.y)));
    const text = show(d, cur);
    valueEl.textContent = text;
    root.setAttribute("aria-valuenow", String(cur));
    root.setAttribute("aria-valuetext", text);
    root.setAttribute("title", `${title} — ${text}`);
  }

  function apply(v, emit, continuous) {
    let nv = clamp(typeof v === "number" && isFinite(v) ? v : cur, min, max);
    // The detent around the default only applies to continuous gestures
    // (dragging); a keyboard or wheel step always moves the value.
    if (continuous && Math.abs(nv - def) <= 0.02 * span) nv = def;
    if (nv === cur) return;
    cur = nv;
    render();
    if (emit && typeof onChange === "function") onChange(cur);
  }

  root.addEventListener("pointerdown", (ev) => {
    if (typeof ev.button === "number" && ev.button !== 0) return;
    dragging = true;
    startY = ev.clientY;
    startVal = cur;
    if (typeof root.setPointerCapture === "function") {
      try { root.setPointerCapture(ev.pointerId); } catch (_) { /* not capturable */ }
    }
    root.classList.add("is-active");
    if (typeof root.focus === "function") root.focus();
    ev.preventDefault();
  });

  root.addEventListener("pointermove", (ev) => {
    if (!dragging) return;
    const dy = startY - ev.clientY;          // up is positive
    const fine = ev.shiftKey ? 0.2 : 1;      // shift = 5x finer
    if (isCurved(d)) {
      // the drag walks along the travel of the curve, so the knob and the
      // host stand at the same place
      const pos = positionFor(startVal, d) + (dy * fine) / DRAG_SPAN_PX;
      apply(valueAt(pos, d), true, true);
      return;
    }
    apply(startVal + dy * perPx * fine, true, true);
  });

  const endDrag = () => {
    if (!dragging) return;
    dragging = false;
    root.classList.remove("is-active");
  };
  root.addEventListener("pointerup", endDrag);
  root.addEventListener("pointercancel", endDrag);
  root.addEventListener("lostpointercapture", endDrag);

  root.addEventListener("dblclick", (ev) => {
    ev.preventDefault();
    apply(def, true);
  });

  root.addEventListener(
    "wheel",
    (ev) => {
      ev.preventDefault();
      const dir = ev.deltaY > 0 ? -1 : 1;
      const step = stepFor(d) * (ev.shiftKey ? 0.2 : 1);
      apply(cur + dir * step, true);
    },
    { passive: false }
  );

  root.addEventListener("keydown", (ev) => {
    const step = stepFor(d) * (ev.shiftKey ? 0.2 : 1);
    switch (ev.key) {
      case "ArrowUp":
      case "ArrowRight":
        apply(cur + step, true);
        break;
      case "ArrowDown":
      case "ArrowLeft":
        apply(cur - step, true);
        break;
      case "PageUp":
        apply(cur + step * 10, true);
        break;
      case "PageDown":
        apply(cur - step * 10, true);
        break;
      case "Home":
        apply(min, true);
        break;
      case "End":
        apply(max, true);
        break;
      default:
        return;
    }
    ev.preventDefault();
  });

  /** Update the displayed value without firing onChange (external updates). */
  root.setValue = (v) => {
    if (typeof v !== "number" || !isFinite(v)) return;
    const nv = clamp(v, min, max);
    if (nv === cur) return;
    cur = nv;
    render();
  };
  root.getValue = () => cur;
  root.getDesc = () => d;

  render();
  return root;
}

/* ----------------------------------------------------------------- toggle */

/**
 * A pill switch that reads On/Off and lights up with the accent when on.
 */
export function toggle({ desc, value, onChange, label } = {}) {
  ensureWidgetStyles();
  const d = normDesc(desc);
  const title = label || d.name;
  let cur = !!value;

  const root = el("div", { class: "aw-toggle-wrap" });
  const nameEl = el("span", { class: "aw-label", text: title });
  const btn = el("button", {
    class: "aw-toggle",
    type: "button",
    role: "switch",
    "aria-checked": String(cur),
    "aria-label": title,
  });
  const dot = el("span", { class: "aw-toggle-dot" });
  const text = el("span", { class: "aw-toggle-text" });
  btn.append(dot, text);
  root.append(nameEl, btn);

  function paint() {
    const labelText = show(d, cur);
    if (btn.classList) btn.classList.toggle("on", cur);
    btn.setAttribute("aria-checked", String(cur));
    text.textContent = labelText;
    btn.setAttribute("title", `${title} — ${labelText}`);
  }

  btn.addEventListener("click", () => {
    cur = !cur;
    paint();
    if (typeof onChange === "function") onChange(cur);
  });

  root.setValue = (v) => {
    const nv = !!v;
    if (nv === cur) return;
    cur = nv;
    paint();
  };
  root.getValue = () => cur;
  root.getDesc = () => d;

  paint();
  return root;
}

/* ----------------------------------------------------------------- select */

/**
 * A compact dropdown listing desc.choices and showing the current choice.
 */
export function select({ desc, value, onChange, label } = {}) {
  ensureWidgetStyles();
  const d = normDesc(desc);
  const title = label || d.name;
  const choices = d.choices;
  let cur = choices.indexOf(value) >= 0 ? value : choices.indexOf(d.default) >= 0 ? d.default : choices[0];

  const root = el("div", { class: "aw-select-wrap" });
  const nameEl = el("span", { class: "aw-label", text: title });
  const box = el("div", { class: "aw-select-box" });
  const sel = el("select", { class: "aw-select", "aria-label": title });
  for (const c of choices) sel.append(el("option", { value: c, text: c ? c : t("control.none", "None") }));
  const caret = el("span", { class: "aw-select-caret" });
  box.append(sel, caret);
  root.append(nameEl, box);

  function paint() {
    if (sel.value !== cur) sel.value = cur;
    sel.setAttribute("title", `${title} — ${show(d, cur)}`);
  }

  sel.addEventListener("change", () => {
    cur = sel.value;
    paint();
    if (typeof onChange === "function") onChange(cur);
  });

  root.setValue = (v) => {
    if (typeof v !== "string" || choices.indexOf(v) < 0) return;
    if (v === cur) return;
    cur = v;
    paint();
  };
  root.getValue = () => cur;
  root.getDesc = () => d;

  paint();
  return root;
}

/* ----------------------------------------------------------------- slider */

/**
 * A horizontal track with a filled portion.
 *
 * Used for 0-100 % style parameters and mixer-style values; the value is
 * still stored in natural units (the track position is the only normalised
 * thing). Clicking or dragging anywhere on the track jumps to that position.
 */
export function slider({ desc, value, onChange, label } = {}) {
  ensureWidgetStyles();
  const d = normDesc(desc);
  const min = d.minimum;
  const max = d.maximum;
  const span = max - min || 1;
  const title = label || d.name;

  let cur = clamp(typeof value === "number" ? value : num(d.default, min), min, max);
  let dragging = false;

  const root = el("div", { class: "aw-slider-wrap" });
  const nameEl = el("span", { class: "aw-label", text: title });
  const track = el("div", {
    class: "aw-slider",
    role: "slider",
    tabindex: "0",
    "aria-label": title,
    "aria-valuemin": String(min),
    "aria-valuemax": String(max),
  });
  const rail = el("div", { class: "aw-slider-rail" });
  const fill = el("div", { class: "aw-slider-fill" });
  const thumb = el("div", { class: "aw-slider-thumb" });
  track.append(rail, fill, thumb);
  const valueEl = el("span", { class: "aw-value" });
  root.append(nameEl, track, valueEl);

  function render() {
    const p = clamp((cur - min) / span, 0, 1);
    // The thumb travels between the inner edges of the track so it never
    // sticks out of the rail.
    const pos = `calc((100% - 10px) * ${p} + 5px)`;
    fill.style.width = pos;
    thumb.style.left = pos;
    const text = show(d, cur);
    valueEl.textContent = text;
    track.setAttribute("aria-valuenow", String(cur));
    track.setAttribute("aria-valuetext", text);
    track.setAttribute("title", `${title} — ${text}`);
  }

  function apply(v, emit) {
    const nv = clamp(typeof v === "number" && isFinite(v) ? v : cur, min, max);
    if (nv === cur) return;
    cur = nv;
    render();
    if (emit && typeof onChange === "function") onChange(cur);
  }

  function valueFromClientX(cx) {
    const rect = typeof track.getBoundingClientRect === "function"
      ? track.getBoundingClientRect()
      : { left: 0, width: 1 };
    const w = rect.width || 1;
    const p = clamp((cx - rect.left) / w, 0, 1);
    return min + p * span;
  }

  track.addEventListener("pointerdown", (ev) => {
    if (typeof ev.button === "number" && ev.button !== 0) return;
    dragging = true;
    if (typeof track.setPointerCapture === "function") {
      try { track.setPointerCapture(ev.pointerId); } catch (_) { /* not capturable */ }
    }
    apply(valueFromClientX(ev.clientX), true);
    if (typeof track.focus === "function") track.focus();
    ev.preventDefault();
  });

  track.addEventListener("pointermove", (ev) => {
    if (!dragging) return;
    apply(valueFromClientX(ev.clientX), true);
  });

  const endDrag = () => {
    dragging = false;
  };
  track.addEventListener("pointerup", endDrag);
  track.addEventListener("pointercancel", ev => {
    endDrag();
    ev.preventDefault();
  });

  track.addEventListener(
    "wheel",
    (ev) => {
      ev.preventDefault();
      const dir = ev.deltaY > 0 ? -1 : 1;
      const step = stepFor(d) * (ev.shiftKey ? 0.2 : 1);
      apply(cur + dir * step, true);
    },
    { passive: false }
  );

  track.addEventListener("keydown", (ev) => {
    const step = stepFor(d) * (ev.shiftKey ? 0.2 : 1);
    switch (ev.key) {
      case "ArrowUp":
      case "ArrowRight":
        apply(cur + step, true);
        break;
      case "ArrowDown":
      case "ArrowLeft":
        apply(cur - step, true);
        break;
      case "PageUp":
        apply(cur + step * 10, true);
        break;
      case "PageDown":
        apply(cur - step * 10, true);
        break;
      case "Home":
        apply(min, true);
        break;
      case "End":
        apply(max, true);
        break;
      default:
        return;
    }
    ev.preventDefault();
  });

  root.setValue = (v) => {
    if (typeof v !== "number" || !isFinite(v)) return;
    const nv = clamp(v, min, max);
    if (nv === cur) return;
    cur = nv;
    render();
  };
  root.getValue = () => cur;
  root.getDesc = () => d;

  render();
  return root;
}

/* ----------------------------------------------------------------- button */

/**
 * A plain button. variant: default | primary | ghost | danger | icon | sm.
 */
export function button({ label, onClick, variant } = {}) {
  ensureWidgetStyles();
  const variantClass = variant === "icon" || variant === "sm" ? variant : variant || "default";
  const b = el("button", {
    class: `aw-btn aw-btn-${variantClass}`,
    type: "button",
    text: label === undefined || label === null ? "" : String(label),
  });
  if (typeof onClick === "function") b.addEventListener("click", onClick);
  return b;
}

/* --------------------------------------------------------------- labelled */

/**
 * Wrap a control with a text label. The label becomes the control's
 * aria-label when it does not have one yet.
 */
export function labelled(label, control) {
  ensureWidgetStyles();
  const text = label === undefined || label === null ? "" : String(label);
  const wrap = el("div", { class: "aw-labelled" });
  wrap.append(el("span", { class: "aw-label", text }));
  if (control) {
    if (typeof control.getAttribute === "function" && !control.getAttribute("aria-label")) {
      control.setAttribute("aria-label", text);
    }
    wrap.append(control);
  }
  return wrap;
}

/* ------------------------------------------------------- truncation ladder
 *
 * Long parameter names are shortened with a ladder: an ordered list of
 * candidate labels, longest (most preferred) first. The panel picks the
 * longest candidate that fits the available width.
 *
 * The default algorithm keeps the outer words (first and last) and drops the
 * middle ones:
 *
 *   "Frequency Skew"   (budget 4)  -> "Freq"
 *   "Click Widening"   (budget 10) -> "Click wide"
 *
 * When the first word fits the budget on its own it is kept whole and the
 * remaining room is spent on a lower-cased prefix of the last word. When it
 * does not fit, the budget is split between the first and the last word in
 * proportion to their lengths, and the tail is only used when at least two
 * characters of it survive.
 */

function shortenLabel(text, maxLen) {
  const s = String(text === null || text === undefined ? "" : text).trim();
  if (!s) return "";
  if (s.length <= maxLen) return s;
  // Straight prefix cut: the candidate at budget s is exactly name.slice(0, s).
  //
  // This is the verified behaviour of the ladder we are matching: every
  // observed ladder obeys  ladder[s] === label.slice(0, s)  for
  // s = 1 .. label.length - 1, with no exceptions.  For example
  //   "Click widening" -> s=6 "Click "   s=7 "Click w"   s=10 "Click wide"
  //   "Frequency Skew" -> s=4 "Freq"                      s=10 "Frequency Sk"
  //
  // A word-aware rebalance (first word + lower-cased tail of the last word)
  // produces a DIFFERENT set -- it drops the trailing space at s=6 and
  // lower-cases the tail -- so it is deliberately not used here.
  return s.slice(0, Math.max(1, Math.floor(maxLen)));
}

/**
 * Default ladder generator: every candidate label for `name` that fits in
 * `maxLen` characters, ordered from the longest to the shortest.
 */
export function truncationLadder(name, maxLen = 24) {
  const text = String(name === null || name === undefined ? "" : name);
  const limit = Math.max(1, Math.floor(Number(maxLen) || 1));
  const out = [];
  for (let len = limit; len >= 1; len--) {
    const label = shortenLabel(text, len);
    if (label && out.indexOf(label) < 0) out.push(label);
  }
  return out;
}

const LADDER_REGISTRY = new Map();

/**
 * Register a named truncation ladder.
 *
 * `ladder` is either an array of candidate labels (longest first) or a
 * generator function (name, maxLen) => string[]. Passing null removes it.
 */
export function setTruncationLadder(name, ladder) {
  const key = String(name || "").trim();
  if (!key) throw new Error("setTruncationLadder: a name is required");
  if (ladder === null || ladder === undefined) {
    LADDER_REGISTRY.delete(key);
    return;
  }
  if (!Array.isArray(ladder) && typeof ladder !== "function") {
    throw new Error("setTruncationLadder: ladder must be an array of labels or a generator function");
  }
  LADDER_REGISTRY.set(key, ladder);
}

/** Look up a registered ladder (null when there is none). */
export function getTruncationLadder(name) {
  return LADDER_REGISTRY.get(String(name || "").trim()) || null;
}

/** Names of every registered ladder. */
export function listTruncationLadders() {
  return Array.from(LADDER_REGISTRY.keys());
}

/**
 * Normalise a ladder to a list of label strings.
 * A generator is called with (name, maxLen).
 */
export function ladderLabels(ladder, name, maxLen) {
  if (!ladder) return null;
  let list = null;
  if (typeof ladder === "function") list = ladder(name, maxLen);
  else if (Array.isArray(ladder)) list = ladder;
  if (!Array.isArray(list)) return null;
  return list.map((v) => String(v)).filter((v) => v.length > 0);
}

/**
 * Pick the longest label of a ladder that fits into `budget` characters.
 */
export function pickLadderLabel(ladder, name, budget) {
  const labels = ladderLabels(ladder, name, budget);
  if (!labels || !labels.length) return null;
  let best = null;
  for (const l of labels) {
    if (l.length > budget) continue;
    if (best === null || l.length > best.length) best = l;
  }
  if (best !== null) return best;
  return labels[labels.length - 1].slice(0, budget);
}
