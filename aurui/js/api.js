/* Auralis frontend API client — thin wrapper over fetch.
   Every call returns parsed JSON and throws an Error with the server message. */

const BASE = "";

async function request(path, options = {}) {
  const res = await fetch(BASE + path, {
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

export const api = {
  modules: () => request("/api/modules"),

  chain: () => request("/api/chain"),
  addModule: (code) =>
    request("/api/chain", { method: "POST", body: JSON.stringify({ code }) }),
  removeModule: (index) =>
    request(`/api/chain/${index}`, { method: "DELETE" }),
  reorder: (from, to) =>
    request("/api/chain/reorder", {
      method: "POST",
      body: JSON.stringify({ from, to }),
    }),
  setBypass: (index, bypass) =>
    request(`/api/chain/${index}/bypass`, {
      method: "POST",
      body: JSON.stringify({ bypass }),
    }),
  params: (index) => request(`/api/chain/${index}/params`),
  setParam: (index, name, value) =>
    request(`/api/chain/${index}/param`, {
      method: "POST",
      body: JSON.stringify({ name, value }),
    }),
  learn: (index) => request(`/api/chain/${index}/learn`, { method: "POST" }),

  upload: async (file) => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch("/api/upload", { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "upload failed");
    return data;
  },
  analyze: (assetId, opts = {}) =>
    request("/api/analyze", {
      method: "POST",
      body: JSON.stringify({ asset_id: assetId, ...opts }),
    }),
  render: (payload) =>
    request("/api/render", { method: "POST", body: JSON.stringify(payload) }),
  audioUrl: (assetId) => `/api/audio/${assetId}`,
  compare: (payload) =>
    request("/api/compare", { method: "POST", body: JSON.stringify(payload) }),

  presets: () => request("/api/presets"),
  models: () => request("/api/models"),
  savePreset: (preset) =>
    request("/api/preset/save", { method: "POST", body: JSON.stringify(preset) }),
  loadPreset: (path, moduleCode) =>
    request("/api/preset/load", {
      method: "POST",
      body: JSON.stringify({ path, module_code: moduleCode || "" }),
    }),
  deletePreset: (path) =>
    request("/api/preset", { method: "DELETE", body: JSON.stringify({ path }) }),

  /* The state of a module in the chain: the learned profile of a de-hummer,
     the region of the spectral editor.  It travels as the base64 text the
     chain document carries. */
  setState: (index, state) =>
    request(`/api/chain/${index}/state`, {
      method: "POST",
      body: JSON.stringify({ state: state || "" }),
    }),
  region: (index, region) =>
    request(`/api/chain/${index}/region`, {
      method: "POST",
      body: JSON.stringify(region || {}),
    }),
};

/* ---- shared tiny helpers ---- */

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function")
      node.addEventListener(k.slice(2).toLowerCase(), v);
    else if (v !== null && v !== undefined && v !== false)
      node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

export function fmtDb(v) {
  if (v === -Infinity || v === null || v === undefined) return "-inf";
  return `${v > 0 ? "+" : ""}${v.toFixed(1)} dB`;
}

export function fmtTime(seconds) {
  if (!isFinite(seconds)) return "--:--.--";
  const s = Math.max(0, seconds);
  const m = Math.floor(s / 60);
  const rest = s - m * 60;
  return `${String(m).padStart(2, "0")}:${rest.toFixed(2).padStart(5, "0")}`;
}

export function fmtValue(desc, value) {
  if (!desc) return String(value);
  if (desc.type === "bool") return value ? "On" : "Off";
  if (desc.type === "enum") return String(value);
  if (desc.unit === "%") return `${Number(value).toFixed(0)} %`;
  const unit = desc.unit ? ` ${desc.unit}` : "";
  const digits = Math.abs(Number(value)) >= 100 ? 0 : Math.abs(Number(value)) >= 1 ? 1 : 2;
  return `${Number(value).toFixed(digits)}${unit}`;
}
