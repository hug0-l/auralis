/* Auralis — the Spectral Editor overlay.
 *
 * The editor draws an interactive region rectangle over the spectrogram of
 * the composite view.  The rectangle is a *selection*: a rectangle in the
 * time/frequency plane that the Spectral Editor module (AUR_SPECTRALEDITOR)
 * processes.  The overlay never touches the audio itself — it reports the
 * selection to the application, which owns the engine.
 *
 * What the overlay provides
 *   • a draggable region rectangle (drag inside it to move it),
 *   • handles to resize the region in time (left / right) and in frequency
 *     (top / bottom), plus the four corners for both at once,
 *   • a mode selector (Attenuate, Replace, Gain, Pattern, Partials),
 *   • a strength slider,
 *   • a live preview readout with the size of the selection,
 *   • a status line that says whether the region really reached the module.
 *
 * The region is *module state*, not a parameter: the module keeps it in
 * `SpectralEditor.set_region()`, it travels in the chain document as a base64
 * block and no chain parameter route can carry it.  The overlay therefore
 * sends the region through a route of its own,
 *
 *     POST /api/chain/{index}/region
 *
 * and it tells the user the truth: while that route is missing the panel
 * says that the region is **not applied**, instead of showing a rectangle
 * that repairs nothing.  The route is specified in
 * `var/patches/e6_region_route.md`.
 *
 * How it talks to the application
 *   createSpectralEditor({ container, onChange, onPreview, moduleIndex,
 *                          transport }) returns
 *     { setData, setRegion, getRegion, destroy }
 *   and the application calls renderEditor(view, state) whenever the view
 *   changed (zoom, scroll, new data), so the overlay follows the spectrogram.
 *
 * House rules
 *   • vanilla ES module, no dependencies, no build step;
 *   • every colour comes from the CSS custom properties of css/tokens.css;
 *   • the pointer gestures that do not belong to the region are passed on to
 *     the composite view, so panning and the selection tools keep working.
 */

import { parseColor, rgba, dpr } from "./view.js";
import { t } from "./i18n.js";

/* The modes of the Spectral Editor, in the order of the module parameter. */
export const MODES = ["Attenuate", "Replace", "Gain", "Pattern", "Partials"];

/* The code of the module the editor drives. */
export const MODULE_CODE = "AUR_SPECTRALEDITOR";

/* The route that hands the region to the module (the region is its state). */
export const REGION_ROUTE = "/api/chain/{index}/region";

/* Half the size of a drawn handle, in pixels. */
const HANDLE = 4;
/* The radius an edge handle is grabbed with, in pixels. */
const HANDLE_HIT = 6;
/* The radius a corner handle is grabbed with, in pixels. */
const CORNER_HIT = 5;
/* The length of a freshly drawn region, in seconds. */
const DEFAULT_LENGTH_S = 0.25;
/* The shortest region the editor allows, in seconds. */
const MIN_LENGTH_S = 0.02;
/* The mel constant of the logarithmic frequency axis. */
const MEL_HZ = 700;

const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);

/**
 * The name of an editing mode, in the active locale.
 *
 * The mode is the value of a parameter of the module, so it is data;
 * the text the user reads is a catalogue entry.
 */
function modeLabel(mode) {
  const key = String(mode || "");
  return t(`spectral.mode.${key.toLowerCase()}`, key);
}



const isNum = (v) => typeof v === "number" && isFinite(v);

/** Frequency → mel (the logarithmic axis of the composite view). */
function toMel(hz) {
  return 2595 * Math.log10(1 + Math.max(0, hz) / MEL_HZ);
}

/** mel → frequency. */
function fromMel(m) {
  return MEL_HZ * (Math.pow(10, m / 2595) - 1);
}

/** Read a colour token from the computed style of an element. */
function token(node, name, fallback) {
  if (!node || typeof getComputedStyle !== "function") return fallback;
  try {
    const value = getComputedStyle(node).getPropertyValue(name);
    return value ? String(value).trim() : fallback;
  } catch (err) {
    return fallback;
  }
}

/* ==================================================================== *
 * the state of a module
 * ==================================================================== */

/* The alphabet of the base64 encoding (RFC 4648). */
const B64_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

/**
 * Decode a base64 string into its bytes.
 *
 * The decoder is written out here instead of using `atob`, so that the
 * interface reads the state block of a chain document the same way in every
 * browser and in the test driver.  Padding is optional: the chain document
 * pads the block itself, a hand written document may not.
 *
 * @param {string} text  the base64 text
 * @returns {number[]|null} the bytes, or null when there is nothing to read
 */
function decodeBase64Bytes(text) {
  const clean = String(text || "").replace(/[^A-Za-z0-9+/]/g, "");
  if (!clean) return null;
  const bytes = [];
  let buffer = 0;
  let bits = 0;
  for (const ch of clean) {
    const value = B64_ALPHABET.indexOf(ch);
    if (value < 0) continue;
    buffer = (buffer << 6) | value;
    bits += 6;
    if (bits >= 8) {
      bits -= 8;
      bytes.push((buffer >> bits) & 0xff);
      buffer &= bits ? (1 << bits) - 1 : 0;
    }
  }
  return bytes;
}

/**
 * Decode a base64 string into text.
 *
 * @param {string} text  the base64 text
 * @returns {string|null} the text, or null when there is nothing to read
 */
function decodeBase64Text(text) {
  const bytes = decodeBase64Bytes(text);
  if (!bytes) return null;
  if (typeof TextDecoder === "function") {
    try {
      return new TextDecoder("utf-8").decode(new Uint8Array(bytes));
    } catch (err) {
      /* fall through to the plain decoding */
    }
  }
  let out = "";
  for (const byte of bytes) out += String.fromCharCode(byte);
  return out;
}

/**
 * Read the region out of the state block of a module.
 *
 * The block of the spectral editor is the JSON of the region, base64 encoded
 * by the chain document (`steps[i].state`).  Both shapes the module accepts
 * are read: the bare region (`{"start": …, "end": …, "low": …, "high": …}`)
 * and the region under a `region` key.
 *
 * @param {string|object} state  the base64 text or the state envelope
 * @returns {object|null} `{ start, end, low, high }`, or null
 */
export function regionFromState(state) {
  let text = "";
  if (typeof state === "string") text = state;
  else if (state && typeof state === "object") {
    text = typeof state.Value === "string" ? state.Value : "";
  }
  const json = decodeBase64Text(text);
  if (!json) return null;
  let payload = null;
  try {
    payload = JSON.parse(json);
  } catch (err) {
    return null;
  }
  if (!payload || typeof payload !== "object") return null;
  const region = payload.region && typeof payload.region === "object" ? payload.region : payload;
  const start = Number(region.start);
  const end = Number(region.end);
  const low = Number(region.low);
  const high = Number(region.high);
  if (!isNum(start) || !isNum(end) || !isNum(low) || !isNum(high)) return null;
  return { start, end, low, high };
}

/**
 * Read the region of the spectral editor out of a session document.
 *
 * The document of a session carries the chain, and every step of the chain
 * carries the state of its module.  This is the way the rectangle comes back
 * after a save and a load of a session.  A bare chain document (the `steps`
 * at the top level) is read as well.
 *
 * @param {object} document  the session document (or a chain document)
 * @param {string} [code]    the code of the module (the spectral editor)
 * @returns {object|null} `{ start, end, low, high }`, or null
 */
export function regionFromDocument(document, code = MODULE_CODE) {
  if (!document || typeof document !== "object") return null;
  const chain =
    document.chain && typeof document.chain === "object" ? document.chain : document;
  const steps = Array.isArray(chain.steps) ? chain.steps : [];
  for (const step of steps) {
    if (!step || typeof step !== "object") continue;
    if (String(step.code || "") !== String(code || "")) continue;
    const region = regionFromState(step.state);
    if (region) return region;
  }
  return null;
}

/* ==================================================================== *
 * the transport
 * ==================================================================== */

/**
 * Read the body of a response as JSON (never throws).
 *
 * @param {object} res  the response of fetch
 * @returns {Promise<object>}
 */
async function readBody(res) {
  if (res && typeof res.text === "function") {
    let text = "";
    try {
      text = await res.text();
    } catch (err) {
      text = "";
    }
    if (!text) return {};
    try {
      return JSON.parse(text);
    } catch (err) {
      return { error: text };
    }
  }
  if (res && typeof res.json === "function") {
    try {
      return await res.json();
    } catch (err) {
      return {};
    }
  }
  return {};
}

/**
 * The default transport: the region travels over HTTP.
 *
 * The three calls are the whole contract between the overlay and the server:
 *
 *   findIndex()              → the chain index of the spectral editor, or -1
 *   applyRegion(i, region)   → hand the region to the module
 *   readParams(i)            → what the module really holds
 *
 * @param {object} [options]
 * @param {string} [options.route]  the route of the region
 * @param {Function} [options.fetch] the fetch to use
 * @returns {object} the transport
 */
export function createHttpTransport(options = {}) {
  const route = options.route || REGION_ROUTE;
  const doFetch =
    typeof options.fetch === "function"
      ? options.fetch
      : (url, init) => {
          if (typeof fetch !== "function") {
            throw new Error("the browser has no fetch");
          }
          return fetch(url, init);
        };

  async function request(url, init) {
    const res = await doFetch(url, init);
    const data = await readBody(res);
    if (!res || !res.ok) {
      const message =
        (data && (data.error || data.detail)) || `${res ? res.status : 0}`;
      const error = new Error(String(message));
      error.status = res ? Number(res.status) || 0 : 0;
      throw error;
    }
    return data;
  }

  return {
    async findIndex() {
      const data = await request("/api/chain", { method: "GET" });
      const chain = data && Array.isArray(data.chain) ? data.chain : [];
      for (let index = 0; index < chain.length; index++) {
        const step = chain[index];
        if (step && String(step.code || "") === MODULE_CODE) return index;
      }
      return -1;
    },

    async applyRegion(index, region) {
      const url = String(route).replace("{index}", String(index));
      return request(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          start: region.start,
          end: region.end,
          low: region.low,
          high: region.high,
        }),
      });
    },

    async readParams(index) {
      return request(`/api/chain/${index}/params`, { method: "GET" });
    },
  };
}

/* ==================================================================== *
 * the editor
 * ==================================================================== */

/**
 * Create a Spectral Editor overlay.
 *
 * @param {object}   options
 * @param {Element}  options.container  the element the overlay lives in
 *                                       (default: #canvas-wrap)
 * @param {Function} options.onChange    called with the editor state after
 *                                       every change the user makes
 * @param {Function} options.onPreview   called with the same state plus a
 *                                       human readable label (live readout)
 * @param {number}   [options.moduleIndex] the chain index of the module; the
 *                                       editor looks it up when it is missing
 * @param {object}   [options.transport] the way the region reaches the
 *                                       module (the HTTP route by default)
 * @returns {{setData:Function, setRegion:Function, getRegion:Function,
 *            destroy:Function, render:Function, setState:Function,
 *            getState:Function, applyRegion:Function, setModuleIndex:Function,
 *            element:Element}}
 */
export function createSpectralEditor(options = {}) {
  const doc = typeof document !== "undefined" ? document : null;
  if (!doc) throw new Error("createSpectralEditor needs a DOM");

  const host = options.container || doc.getElementById("canvas-wrap");
  if (!host) throw new Error("createSpectralEditor needs a container element");

  const onChange = typeof options.onChange === "function" ? options.onChange : null;
  const onPreview = typeof options.onPreview === "function" ? options.onPreview : null;
  const transport =
    options.transport && typeof options.transport.applyRegion === "function"
      ? options.transport
      : createHttpTransport({ fetch: options.fetch });

  /* ------------------------------------------------------------- state */
  const state = {
    region: null,          /* { start, end, low, high } — seconds / hertz */
    mode: "Attenuate",
    strength: 0.5,
    data: null,            /* the analysis payload (duration, sample rate) */
    view: null,            /* the composite view the overlay draws on */
    moduleIndex: Number.isInteger(options.moduleIndex) ? options.moduleIndex : -1,
    status: { kind: "idle", detail: "" },  /* the truth about the region */
    module: null,          /* what the module holds, as the server reports it */
    destroyed: false,
  };

  /* ----------------------------------------------------------- overlay */
  const overlay = doc.createElement("canvas");
  overlay.id = "spectral-editor-overlay";
  overlay.className = "spectral-editor-overlay";
  overlay.style.position = "absolute";
  overlay.style.left = "0px";
  overlay.style.top = "0px";
  overlay.style.pointerEvents = "auto";
  overlay.style.zIndex = "2";
  overlay.setAttribute("aria-label", t("spectral.regionLabel", "Spectral editor region"));

  const panel = doc.createElement("div");
  panel.id = "spectral-editor-panel";
  panel.className = "spectral-editor-panel";
  panel.style.cssText = [
    "position:absolute",
    "left:8px",
    "top:8px",
    "z-index:3",
    "display:flex",
    "flex-direction:column",
    "gap:4px",
    "padding:6px 8px",
    "background:var(--bg-2)",
    "border:1px solid var(--line)",
    "border-radius:var(--radius)",
    "box-shadow:var(--shadow-1)",
    "font-family:var(--font-ui)",
    "font-size:var(--fs-sm)",
    "color:var(--txt-1)",
    "user-select:none",
  ].join(";");

  const modeSelect = doc.createElement("select");
  modeSelect.className = "se-select";
  modeSelect.style.cssText = [
    "background:var(--bg-3)",
    "color:var(--txt-0)",
    "border:1px solid var(--line)",
    "border-radius:var(--radius)",
    "font-family:var(--font-ui)",
    "font-size:var(--fs-sm)",
    "padding:1px 4px",
  ].join(";");
  for (const mode of MODES) {
    const option = doc.createElement("option");
    option.value = mode;
    option.textContent = modeLabel(mode);
    modeSelect.append(option);
  }
  modeSelect.value = state.mode;

  const strengthSlider = doc.createElement("input");
  strengthSlider.type = "range";
  strengthSlider.className = "se-slider";
  strengthSlider.min = "0";
  strengthSlider.max = "1";
  strengthSlider.step = "0.01";
  strengthSlider.value = String(state.strength);
  strengthSlider.style.cssText = "width:110px;accent-color:var(--accent);";

  const strengthValue = doc.createElement("span");
  strengthValue.className = "se-value mono-label";
  strengthValue.style.cssText = "min-width:34px;text-align:right;color:var(--txt-0);";

  const newButton = doc.createElement("button");
  newButton.type = "button";
  newButton.className = "se-btn tbtn tbtn-sm";
  newButton.textContent = t("spectral.newRegion", "New region");
  newButton.style.cssText = "align-self:flex-start;";

  const readout = doc.createElement("div");
  readout.className = "se-readout mono-label";
  readout.style.cssText = "color:var(--txt-2);white-space:nowrap;";

  /* The status line: it says whether the region really reached the module.
     A rectangle that repairs nothing must not look like a repair. */
  const status = doc.createElement("div");
  status.id = "spectral-editor-status";
  status.className = "se-readout mono-label";
  status.style.cssText = "color:var(--txt-2);white-space:nowrap;";
  status.setAttribute("role", "status");

  const row = (label, control) => {
    const line = doc.createElement("div");
    line.style.cssText = "display:flex;align-items:center;gap:6px;";
    const tag = doc.createElement("span");
    tag.className = "mono-label";
    tag.style.cssText = "min-width:52px;color:var(--txt-2);";
    tag.textContent = label;
    line.append(tag, control);
    return line;
  };

  panel.append(
    row(t("spectral.mode", "Mode"), modeSelect),
    row(t("spectral.strength", "Strength"), strengthSlider),
    row("", strengthValue),
    newButton,
    readout,
    status
  );

  host.append(overlay, panel);

  /* ------------------------------------------------------- geometry */
  const geometry = { left: 0, top: 0, width: 0, height: 0 };

  /** Match the overlay to the spectrogram canvas of the composite view. */
  function measure() {
    const spec = host.querySelector("#spec-canvas");
    const hostRect = host.getBoundingClientRect();
    let left = 0;
    let top = 0;
    let width = hostRect.width;
    let height = hostRect.height;
    if (spec) {
      const rect = spec.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        left = rect.left - hostRect.left;
        top = rect.top - hostRect.top;
        width = rect.width;
        height = rect.height;
      }
    }
    geometry.left = left;
    geometry.top = top;
    geometry.width = width;
    geometry.height = height;

    const ratio = dpr();
    overlay.style.left = `${left}px`;
    overlay.style.top = `${top}px`;
    overlay.style.width = `${width}px`;
    overlay.style.height = `${height}px`;
    const pw = Math.max(1, Math.round(width * ratio));
    const ph = Math.max(1, Math.round(height * ratio));
    if (overlay.width !== pw) overlay.width = pw;
    if (overlay.height !== ph) overlay.height = ph;
  }

  /* ------------------------------------------------------- mapping */
  /** The mapping between the view coordinates and the overlay pixels. */
  function mapping() {
    const view = state.view;
    const w = geometry.width;
    const h = geometry.height;
    if (view && typeof view._timeToX === "function" && typeof view._freqToY === "function") {
      const specH = (view._layout && view._layout.specH) || h;
      /* the view works in the coordinate system of the container, the overlay
         in its own — shift by the offset of the spectrogram canvas */
      return {
        timeToX: (t) => view._timeToX(t) - geometry.left,
        xToTime: (x) => view._xToTime(x + geometry.left),
        freqToY: (f) => view._freqToY(f) - geometry.top,
        yToFreq: (y) => view._displayFreq((y + geometry.top) / Math.max(1, specH)),
      };
    }
    /* Fallback: derive the mapping from the public getters of the view. */
    const duration = (view && view.duration) || (state.data && state.data.duration) || 1;
    const zoom = (view && view.zoom) || 1;
    const scroll = (view && view.scroll) || 0;
    const visible = Math.max(1e-6, duration / zoom);
    const range = (view && view.frequencyRange) || { lo: 0, hi: 22050 };
    const lo = range.lo || 0;
    const hi = Math.max(range.hi || 22050, lo + 1);
    const logFreq = !!(view && view._logFreq);
    return {
      timeToX: (t) => ((t - scroll) / visible) * w,
      xToTime: (x) => scroll + (clamp(x, 0, w) / Math.max(1, w)) * visible,
      freqToY: (f) => {
        if (logFreq) {
          const mHi = toMel(hi);
          const mLo = toMel(lo);
          return ((mHi - toMel(f)) / Math.max(1e-6, mHi - mLo)) * h;
        }
        return ((hi - f) / (hi - lo)) * h;
      },
      yToFreq: (y) => {
        const v = clamp(y / Math.max(1, h), 0, 1);
        if (logFreq) {
          const mHi = toMel(hi);
          const mLo = toMel(lo);
          return fromMel(mHi - v * (mHi - mLo));
        }
        return hi - v * (hi - lo);
      },
    };
  }

  /* -------------------------------------------------------- painting */
  function draw() {
    const ctx = overlay.getContext("2d");
    if (!ctx) return;
    const ratio = dpr();
    const w = geometry.width;
    const h = geometry.height;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, w, h);

    const region = state.region;
    if (!region) return;
    const map = mapping();
    const x0 = map.timeToX(region.start);
    const x1 = map.timeToX(region.end);
    const y0 = map.freqToY(region.high);
    const y1 = map.freqToY(region.low);
    const left = Math.min(x0, x1);
    const width = Math.max(1, Math.abs(x1 - x0));
    const top = Math.min(y0, y1);
    const height = Math.max(1, Math.abs(y1 - y0));

    const fill = parseColor(token(host, "--sel", "#24405c"));
    const edge = parseColor(token(host, "--sel-edge", "#3d6ea3"));
    const accent = parseColor(token(host, "--accent", "#2f87ff"));

    ctx.save();
    if (fill) {
      ctx.fillStyle = rgba(fill, 0.32);
      ctx.fillRect(left, top, width, height);
    }
    if (edge) {
      ctx.strokeStyle = rgba(edge, 0.95);
      ctx.lineWidth = 1;
      ctx.strokeRect(
        Math.round(left) + 0.5,
        Math.round(top) + 0.5,
        Math.round(width),
        Math.round(height)
      );
    }

    /* the handles */
    if (accent) {
      ctx.fillStyle = rgba(accent, 0.95);
      const hx = [left, left + width];
      const hy = [top, top + height];
      for (const x of hx) {
        for (const y of hy) {
          ctx.fillRect(Math.round(x - HANDLE), Math.round(y - HANDLE), HANDLE * 2, HANDLE * 2);
        }
      }
    }
    ctx.restore();
  }

  /* ------------------------------------------------------- the label */
  /** The one line the status bar of the interface shows. */
  function label() {
    const region = state.region;
    if (!region) return t("spectral.noRegion", "no region — drag a selection in the view");
    const seconds = Math.max(0, region.end - region.start);
    const hz = Math.max(0, region.high - region.low);
    const applied =
      state.status.kind === "applied"
        ? t("spectral.applied", "applied")
        : t("spectral.notApplied", "not applied");
    return `${modeLabel(state.mode)} · ${Math.round(state.strength * 100)} % · ${seconds.toFixed(3)} s · ${Math.round(region.low)}–${Math.round(region.high)} Hz (${Math.round(hz)} Hz) · ${applied}`;
  }

  /* ------------------------------------------------------- the status */
  /** What the module holds, as the server reported it. */
  function confirmedText() {
    const held = state.module;
    if (!held) return "";
    const parts = [];
    if (held.mode) parts.push(modeLabel(held.mode));
    if (isNum(held.strength)) parts.push(`${Math.round(held.strength * 100)} %`);
    if (isNum(held.regionLength)) parts.push(`${held.regionLength.toFixed(3)} s`);
    if (!parts.length) return "";
    const text = parts.join(" · ");
    return held.stale
      ? t("spectral.stale", "{value} (the module still holds the old value)", { value: text })
      : text;
  }

  /** The truth about the region: did it reach the module? */
  function statusText() {
    const kind = state.status.kind;
    let text;
    if (kind === "applied") {
      text =
        state.moduleIndex >= 0
          ? t("spectral.status.applied", "the region is applied to module {index}", {
              index: state.moduleIndex,
            })
          : t("spectral.status.appliedToModule", "the region is applied to the module");
    } else if (kind === "pending") {
      text = t("spectral.status.pending", "the region is being sent to the module…");
    } else if (kind === "missing") {
      text = t(
        "spectral.status.missing",
        "the region is NOT applied — the server has no route for the region yet"
      );
    } else if (kind === "no-module") {
      text = t(
        "spectral.status.noModule",
        "the region is NOT applied — there is no Spectral Editor in the chain"
      );
    } else if (kind === "failed") {
      text = t("spectral.status.failed", "the region is NOT applied — {reason}", {
        reason: state.status.detail || t("spectral.status.refused", "the server refused it"),
      });
    } else {
      text = state.region
        ? t("spectral.status.notSent", "the region has not been sent to the module yet")
        : t("spectral.noRegion", "no region — drag a selection in the view");
    }
    const held = confirmedText();
    return held
      ? `${text} — ${t("spectral.status.holds", "the module holds {value}", { value: held })}`
      : text;
  }

  /** The colour of the status line, from the tokens of the stylesheet. */
  function statusColour() {
    const kind = state.status.kind;
    if (kind === "applied") return "var(--accent-2)";
    if (kind === "missing" || kind === "failed" || kind === "no-module") {
      return "var(--danger)";
    }
    return "var(--txt-2)";
  }

  /** Remember what happened to the region. */
  function setStatus(kind, detail = "") {
    state.status = { kind, detail: String(detail || "") };
    refreshReadout();
    /* the status bar of the host shows whether the region reached the
       module, so it has to learn about the new state as well */
    publishLabel();
  }

  /**
   * Hand the one line of the status bar to the host.
   *
   * The line carries the truth about the region ("applied" or "not applied"),
   * so it is published whenever that truth changes — and not only when the
   * region itself changes.
   */
  function publishLabel() {
    if (!onPreview) return;
    try {
      onPreview({ ...getState(), label: label() });
    } catch (err) {
      console.error("[auralis] spectral editor onPreview failed", err);
    }
  }

  function refreshReadout() {
    readout.textContent = label();
    strengthValue.textContent = `${Math.round(state.strength * 100)} %`;
    status.textContent = statusText();
    status.style.color = statusColour();
  }

  /** Report a change to the application. */
  function notify() {
    refreshReadout();
    draw();
    const payload = getState();
    if (onChange) {
      try {
        onChange(payload);
      } catch (err) {
        console.error("[auralis] spectral editor onChange failed", err);
      }
    }
    if (onPreview) {
      try {
        onPreview({ ...payload, label: label() });
      } catch (err) {
        console.error("[auralis] spectral editor onPreview failed", err);
      }
    }
  }

  /* ------------------------------------------------------ the region */
  function normaliseRegion(region) {
    if (!region) return null;
    const start = Number(region.start ?? region.t0);
    const end = Number(region.end ?? region.t1);
    const low = Number(region.low ?? region.f0);
    const high = Number(region.high ?? region.f1);
    if (!isNum(start) || !isNum(end) || !isNum(low) || !isNum(high)) return null;
    const duration = (state.data && state.data.duration) || (state.view && state.view.duration) || 0;
    let s = Math.min(start, end);
    let e = Math.max(start, end);
    if (duration > 0) {
      s = clamp(s, 0, duration);
      e = clamp(e, 0, duration);
    }
    if (e - s < MIN_LENGTH_S) e = s + MIN_LENGTH_S;
    let lo = Math.min(low, high);
    let hi = Math.max(low, high);
    const nyquist = ((state.data && state.data.sample_rate) || 48000) / 2;
    lo = clamp(lo, 0, nyquist);
    hi = clamp(hi, 0, nyquist);
    if (hi - lo < 1) hi = Math.min(nyquist, lo + 1);
    return { start: s, end: e, low: lo, high: hi };
  }

  /** Are two regions the same rectangle? */
  function sameRegion(a, b) {
    if (!a || !b) return a === b;
    return (
      Math.abs(a.start - b.start) < 1e-9 &&
      Math.abs(a.end - b.end) < 1e-9 &&
      Math.abs(a.low - b.low) < 1e-9 &&
      Math.abs(a.high - b.high) < 1e-9
    );
  }

  function setRegion(region, silent = true) {
    state.region = normaliseRegion(region);
    refreshReadout();
    draw();
    if (!silent) notify();
  }

  function getRegion() {
    return state.region ? { ...state.region } : null;
  }

  function getState() {
    return {
      region: getRegion(),
      mode: state.mode,
      strength: state.strength,
      moduleIndex: state.moduleIndex,
      status: { ...state.status },
      label: label(),
    };
  }

  /** The chain index of the module the editor drives. */
  function setModuleIndex(index) {
    const value = Number(index);
    state.moduleIndex = Number.isInteger(value) ? value : -1;
    refreshReadout();
    return state.moduleIndex;
  }

  function setState(next, silent = true) {
    if (!next || typeof next !== "object") return;
    if (next.region !== undefined) state.region = normaliseRegion(next.region);
    if (next.mode && MODES.indexOf(next.mode) >= 0) {
      state.mode = next.mode;
      modeSelect.value = state.mode;
    }
    if (isNum(next.strength)) {
      state.strength = clamp(next.strength, 0, 1);
      strengthSlider.value = String(state.strength);
    }
    if (Number.isInteger(next.moduleIndex)) setModuleIndex(next.moduleIndex);
    /* The host knows the region came from the module itself (a session that
       was just loaded), so the panel may say so. */
    if (next.regionApplied === true) setStatus("applied", "");
    else if (next.regionApplied === false) setStatus("idle", "");
    refreshReadout();
    draw();
    /* the host shows the truth about the region in its status bar, so it has
       to learn about the new state as well */
    publishLabel();
    if (!silent) notify();
  }

  function setData(data) {
    state.data = data || null;
    if (state.region) setRegion(state.region);
  }

  /* ------------------------------------------------- the region route */
  /** Look the spectral editor up in the chain of the server. */
  async function discoverIndex() {
    if (typeof transport.findIndex !== "function") {
      return { index: -1, error: null };
    }
    try {
      const index = await transport.findIndex();
      return { index: Number.isInteger(index) ? index : -1, error: null };
    } catch (err) {
      return { index: -1, error: err };
    }
  }

  /** Read back what the module holds (the mode, the strength, the length). */
  async function refreshModuleValues(index) {
    if (typeof transport.readParams !== "function") return;
    try {
      const data = await transport.readParams(index);
      const values = (data && data.values) || {};
      const strength = Number(values.Strength);
      const length = Number(values["Region Length"]);
      state.module = {
        mode: values.Mode != null ? String(values.Mode) : "",
        strength: isNum(strength) ? strength : null,
        regionLength: isNum(length) ? length : null,
        stale: false,
      };
    } catch (err) {
      state.module = null;
    }
    refreshReadout();
  }

  /**
   * Send the region to the module.
   *
   * The region is the state of the module, so it needs a route of its own
   * (`POST /api/chain/{index}/region`).  While that route is missing the
   * panel says that the region is **not** applied — the user must never see a
   * rectangle that repairs nothing without being told.
   *
   * @param {boolean} [retried]  the call already looked the index up again
   * @returns {Promise<object|null>} the answer of the server, or null
   */
  async function applyRegion(retried = false) {
    if (state.destroyed) return null;
    const region = state.region;
    if (!region) {
      setStatus("idle", "");
      return null;
    }
    let index = state.moduleIndex;
    if (!(index >= 0)) {
      const found = await discoverIndex();
      if (found.error) {
        setStatus("failed", found.error && found.error.message ? found.error.message : "the server could not be reached");
        return null;
      }
      index = found.index;
      state.moduleIndex = index;
    }
    if (!(index >= 0)) {
      setStatus("no-module", "");
      return null;
    }

    setStatus("pending", "");
    try {
      const answer = await transport.applyRegion(index, region);
      if (state.destroyed) return answer;
      setStatus("applied", "");
      await refreshModuleValues(index);
      return answer;
    } catch (err) {
      if (state.destroyed) return null;
      const code = Number(err && err.status) || 0;
      if (code === 404 && !retried) {
        /* The route may be missing, or the index may be stale: look again. */
        const found = await discoverIndex();
        if (found.error) {
          setStatus("failed", found.error.message || "the server could not be reached");
          return null;
        }
        state.moduleIndex = found.index;
        if (found.index >= 0 && found.index !== index) {
          return applyRegion(true);
        }
        setStatus(found.index >= 0 ? "missing" : "no-module", "");
        console.warn("[auralis] the region was not applied", err);
        return null;
      }
      setStatus(
        "failed",
        err && err.message ? err.message : "the server refused the region"
      );
      console.warn("[auralis] the region was not applied", err);
      return null;
    }
  }

  /** The local mode or strength no longer matches the module. */
  function markStale() {
    const held = state.module;
    if (!held) return;
    if (held.mode && held.mode !== state.mode) held.stale = true;
    if (isNum(held.strength) && Math.abs(held.strength - state.strength) > 1e-6) {
      held.stale = true;
    }
  }

  /* ------------------------------------------------------ the pointer */
  let drag = null;

  /** Which part of the region is under the pointer? */
  function hitTest(x, y) {
    const region = state.region;
    if (!region) return null;
    const map = mapping();
    const x0 = Math.min(map.timeToX(region.start), map.timeToX(region.end));
    const x1 = Math.max(map.timeToX(region.start), map.timeToX(region.end));
    const y0 = Math.min(map.freqToY(region.high), map.freqToY(region.low));
    const y1 = Math.max(map.freqToY(region.high), map.freqToY(region.low));

    const nearLeft = Math.abs(x - x0) <= HANDLE_HIT;
    const nearRight = Math.abs(x - x1) <= HANDLE_HIT;
    const nearTop = Math.abs(y - y0) <= HANDLE_HIT;
    const nearBottom = Math.abs(y - y1) <= HANDLE_HIT;
    const cornerLeft = Math.abs(x - x0) <= CORNER_HIT;
    const cornerRight = Math.abs(x - x1) <= CORNER_HIT;
    const cornerTop = Math.abs(y - y0) <= CORNER_HIT;
    const cornerBottom = Math.abs(y - y1) <= CORNER_HIT;
    const inX = x >= x0 - HANDLE_HIT && x <= x1 + HANDLE_HIT;
    const inY = y >= y0 - HANDLE_HIT && y <= y1 + HANDLE_HIT;

    if (cornerLeft && cornerTop && inX && inY) return "resize-tl";
    if (cornerLeft && cornerBottom && inX && inY) return "resize-bl";
    if (cornerRight && cornerTop && inX && inY) return "resize-tr";
    if (cornerRight && cornerBottom && inX && inY) return "resize-br";
    if (nearLeft && inY) return "resize-l";
    if (nearRight && inY) return "resize-r";
    if (nearTop && inX) return "resize-t";
    if (nearBottom && inX) return "resize-b";
    if (inX && inY) return "move";
    return null;
  }

  function localPoint(ev) {
    const rect = overlay.getBoundingClientRect();
    return { x: ev.clientX - rect.left, y: ev.clientY - rect.top };
  }

  function onPointerDown(ev) {
    if (ev.button !== 0 && ev.button !== 1) return;
    const point = localPoint(ev);
    const mode = hitTest(point.x, point.y);
    if (!mode) return;   /* let the composite view have the gesture */
    const map = mapping();
    drag = {
      mode,
      x0: point.x,
      y0: point.y,
      region: { ...state.region },
      time0: map.xToTime(point.x),
      freq0: map.yToFreq(point.y),
      pointerId: ev.pointerId,
      changed: false,
    };
    if (overlay.setPointerCapture && ev.pointerId !== undefined) {
      try {
        overlay.setPointerCapture(ev.pointerId);
      } catch (err) {
        /* not fatal */
      }
    }
    if (typeof ev.preventDefault === "function") ev.preventDefault();
    if (typeof ev.stopPropagation === "function") ev.stopPropagation();
  }

  function onPointerMove(ev) {
    if (!drag) return;
    const point = localPoint(ev);
    const map = mapping();
    const t = map.xToTime(point.x);
    const f = map.yToFreq(point.y);
    const base = drag.region;
    let region = { ...base };

    switch (drag.mode) {
      case "move": {
        const dt = t - drag.time0;
        const df = f - drag.freq0;
        region.start = base.start + dt;
        region.end = base.end + dt;
        region.low = base.low + df;
        region.high = base.high + df;
        break;
      }
      case "resize-l":
        region.start = t;
        break;
      case "resize-r":
        region.end = t;
        break;
      case "resize-t":
        region.high = f;
        break;
      case "resize-b":
        region.low = f;
        break;
      case "resize-tl":
        region.start = t;
        region.high = f;
        break;
      case "resize-tr":
        region.end = t;
        region.high = f;
        break;
      case "resize-bl":
        region.start = t;
        region.low = f;
        break;
      case "resize-br":
        region.end = t;
        region.low = f;
        break;
      default:
        break;
    }
    const next = normaliseRegion(region);
    if (!sameRegion(next, drag.region)) drag.changed = true;
    state.region = next;
    notify();
    if (typeof ev.preventDefault === "function") ev.preventDefault();
    if (typeof ev.stopPropagation === "function") ev.stopPropagation();
  }

  function onPointerUp(ev) {
    if (!drag) return;
    const changed = drag.changed;
    drag = null;
    if (overlay.releasePointerCapture && ev.pointerId !== undefined) {
      try {
        overlay.releasePointerCapture(ev.pointerId);
      } catch (err) {
        /* not fatal */
      }
    }
    if (typeof ev.preventDefault === "function") ev.preventDefault();
    if (typeof ev.stopPropagation === "function") ev.stopPropagation();
    /* The rectangle changed: the module has to learn about it. */
    if (changed) applyRegion();
  }

  overlay.addEventListener("pointerdown", onPointerDown);
  overlay.addEventListener("pointermove", onPointerMove);
  overlay.addEventListener("pointerup", onPointerUp);
  overlay.addEventListener("pointercancel", onPointerUp);

  /* --------------------------------------------------------- controls */
  modeSelect.addEventListener("change", () => {
    state.mode = modeSelect.value;
    markStale();
    notify();
  });

  strengthSlider.addEventListener("input", () => {
    state.strength = clamp(Number(strengthSlider.value) || 0, 0, 1);
    markStale();
    notify();
  });

  newButton.addEventListener("click", () => {
    const map = mapping();
    const duration = (state.data && state.data.duration) || (state.view && state.view.duration) || 0;
    /* adopt the selection of the composite view when there is one */
    const selection = state.view && typeof state.view.selection === "object" ? state.view.selection : null;
    let start = null;
    let end = null;
    let low = null;
    let high = null;
    if (selection && isNum(selection.t0) && isNum(selection.t1) && selection.t1 > selection.t0) {
      start = selection.t0;
      end = selection.t1;
    }
    if (selection && isNum(selection.f0) && isNum(selection.f1) && selection.f1 > selection.f0) {
      low = selection.f0;
      high = selection.f1;
    }
    if (start === null || end === null) {
      const centre = map.xToTime(geometry.width * 0.5);
      start = Math.max(0, centre - DEFAULT_LENGTH_S * 0.5);
      end = start + DEFAULT_LENGTH_S;
    }
    if (low === null || high === null) {
      const range = (state.view && state.view.frequencyRange) || { lo: 0, hi: 22050 };
      const centre = (range.lo + range.hi) * 0.5;
      const half = Math.max(200, (range.hi - range.lo) * 0.08);
      low = Math.max(range.lo, centre - half);
      high = Math.min(range.hi, centre + half);
    }
    if (duration > 0) {
      start = clamp(start, 0, Math.max(0, duration - MIN_LENGTH_S));
      end = clamp(end, start + MIN_LENGTH_S, duration);
    }
    setRegion({ start, end, low, high }, false);
    applyRegion();
  });

  /* ---------------------------------------------------------- render */
  function render(view, next) {
    state.view = view || null;
    measure();
    if (next && typeof next === "object") {
      if (next.region !== undefined) state.region = normaliseRegion(next.region);
      if (next.mode && MODES.indexOf(next.mode) >= 0) {
        state.mode = next.mode;
        modeSelect.value = state.mode;
      }
      if (isNum(next.strength)) {
        state.strength = clamp(next.strength, 0, 1);
        strengthSlider.value = String(state.strength);
      }
      if (Number.isInteger(next.moduleIndex)) setModuleIndex(next.moduleIndex);
    }
    refreshReadout();
    draw();
    return getState();
  }

  function destroy() {
    state.destroyed = true;
    overlay.removeEventListener("pointerdown", onPointerDown);
    overlay.removeEventListener("pointermove", onPointerMove);
    overlay.removeEventListener("pointerup", onPointerUp);
    overlay.removeEventListener("pointercancel", onPointerUp);
    if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
    if (panel.parentNode) panel.parentNode.removeChild(panel);
    state.view = null;
    state.region = null;
    if (active === api) active = null;
  }

  const api = {
    setData,
    setRegion,
    getRegion,
    destroy,
    render,
    setState,
    getState,
    setModuleIndex,
    applyRegion,
    element: overlay,
    panel,
  };

  active = api;
  measure();
  refreshReadout();
  return api;
}

/* ==================================================================== *
 * the module level entry point
 * ==================================================================== */

/** The editor that was created last; `renderEditor` drives it. */
let active = null;

/**
 * Draw the editor over a composite view.
 *
 * The application calls this whenever the view changed — after a zoom, a
 * scroll, a new file or a new selection — so that the region follows the
 * spectrogram.
 *
 * @param {object} view  the composite view (js/view.js)
 * @param {object} state { region, mode, strength }
 * @returns {object|null} the state of the editor, or null when there is none
 */
export function renderEditor(view, state) {
  if (!active) return null;
  try {
    return active.render(view, state);
  } catch (err) {
    console.error("[auralis] renderEditor failed", err);
    return null;
  }
}

/**
 * The editor that is currently active, or null.
 * @returns {object|null}
 */
export function currentEditor() {
  return active;
}
