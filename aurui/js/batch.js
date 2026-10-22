/* Auralis — batch processor panel.
 *
 * The unattended workflow of the workbench: a list of files, one chain, one
 * delivery target and one output folder.  The server owns the job; this
 * module starts it, follows it and reports it.
 *
 * Contract notes
 *  - Vanilla ES module: no dependencies, no build step.
 *  - No DOM access at import time: every node is built inside renderBatch(),
 *    so the module can be driven by a head-less test harness.
 *  - Every colour, font and size comes from css/tokens.css through
 *    var(--token); the stylesheet below only references those properties.
 *  - The job is driven through the existing /api/batch endpoints only:
 *      POST /api/batch              start a job
 *      GET  /api/batch/{job_id}     poll a job
 *      POST /api/batch/{job_id}/cancel   stop a job
 *    No route is invented here; a capability the server does not expose is
 *    reported in the panel instead of being faked.
 *
 * Public API
 *   renderBatch(container, { files, chain, presets, onStart, onStop })
 *       -> { update(job), destroy() }
 *   batchViewModel(job) -> the view model of one job
 */

import { el, api } from "./api.js";
import { t } from "./i18n.js";

const STYLE_ID = "aur-batch-css";
const ROOTS = new WeakMap();

/** How often a running job is polled, in milliseconds. */
const POLL_MS = 700;

/** The word lengths the batch runner accepts (aurserver/batch.py). */
const BIT_DEPTHS = [16, 24, 32];

/** The containers the batch runner accepts (aurserver/batch.py). */
const FORMATS = ["wav", "flac"];

/** The word lengths the dither stage knows; 32 bit is written without dither. */
const DITHER_DEPTHS = [16, 24];

/**
 * The asset store writes every upload to <root>/var/assets/audio/<asset_id>.wav
 * (see aurserver/assets.py).  The batch runner resolves a relative path
 * against the server root, so this is how an uploaded file is addressed.
 */
const STAGED_PATH = "var/assets/audio/{asset_id}.wav";

/** The asset store keeps this many uploads alive at a time. */
const STAGED_LIMIT = 24;

/**
 * The name of a state of a file or of the job, in the active locale.
 *
 * The states themselves are the data of the server; the text the user
 * reads is a catalogue entry.
 */
function stateLabel(name) {
  const key = String(name || "");
  return t(`batch.state.${key}`, key);
}



/* ------------------------------------------------------------ panel styles */

const BATCH_CSS = `
.bt-root {
  display: flex; flex-direction: column; gap: var(--sp-3);
  min-width: 0; color: var(--txt-0); font-family: var(--font-ui); font-size: var(--fs-sm);
}
.bt-head { display: flex; align-items: center; gap: var(--sp-3); flex-wrap: wrap; }
.bt-title { font: 600 var(--fs-lg)/1.2 var(--font-ui); color: var(--txt-0); }
.bt-sub { color: var(--txt-2); font: 400 var(--fs-xs)/1.3 var(--font-ui); }
.bt-spacer { flex: 1 1 auto; }
.bt-section { display: flex; flex-direction: column; gap: var(--sp-2); min-width: 0; }
.bt-section-title {
  font: 600 var(--fs-xs)/1 var(--font-ui); text-transform: uppercase; letter-spacing: .08em;
  color: var(--txt-2); padding-bottom: 3px; border-bottom: 1px solid var(--line-soft);
}
.bt-row { display: flex; align-items: center; gap: var(--sp-3); flex-wrap: wrap; }
.bt-field { display: flex; align-items: center; gap: var(--sp-2); }
.bt-label { color: var(--txt-1); font: 500 var(--fs-xs)/1 var(--font-ui); text-transform: uppercase; letter-spacing: .06em; }
.bt-select, .bt-input {
  background: var(--bg-3); color: var(--txt-0); border: 1px solid var(--line);
  border-radius: var(--radius); padding: 3px 6px; font: 400 var(--fs-sm)/1.2 var(--font-ui);
  min-width: 0;
}
.bt-input { width: 100%; }
.bt-select:focus-visible, .bt-input:focus-visible { outline: 1px solid var(--sel-edge); outline-offset: 1px; }
.bt-facts { color: var(--txt-1); font: 400 var(--fs-xs)/1.4 var(--font-mono); }
.bt-chain { display: flex; align-items: center; gap: var(--sp-2); flex-wrap: wrap; }
.bt-chip {
  display: inline-flex; align-items: center; gap: var(--sp-2);
  background: var(--bg-2); border: 1px solid var(--line); border-radius: var(--radius);
  padding: 2px var(--sp-2); color: var(--txt-0); font: 400 var(--fs-xs)/1.4 var(--font-ui);
}
.bt-chip.bt-bypassed { opacity: 0.5; }
.bt-chip.bt-added { border-color: var(--accent); color: var(--accent); }
.bt-arrow { color: var(--txt-3); font: 400 var(--fs-xs)/1 var(--font-ui); }
.bt-empty { color: var(--txt-2); font: 400 var(--fs-sm)/1.4 var(--font-ui); }
.bt-drop {
  border: 1px dashed var(--line); border-radius: var(--radius); padding: var(--sp-4);
  color: var(--txt-2); font: 400 var(--fs-sm)/1.5 var(--font-ui); text-align: center;
  background: var(--bg-1);
}
.bt-drop.bt-over { border-color: var(--accent); color: var(--txt-1); background: var(--bg-2); }
.bt-list { display: flex; flex-direction: column; gap: 1px; max-height: 190px; overflow-y: auto; }
.bt-item {
  display: flex; align-items: center; gap: var(--sp-3);
  padding: 3px var(--sp-2); background: var(--bg-1); border: 1px solid var(--line-soft);
  border-radius: var(--radius); font: 400 var(--fs-sm)/1.4 var(--font-ui);
}
.bt-item-name { flex: 1 1 auto; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--txt-0); }
.bt-item-meta { color: var(--txt-2); font: 400 var(--fs-xs)/1.4 var(--font-mono); white-space: nowrap; }
.bt-item.bt-failed { border-color: var(--danger); }
.bt-item.bt-done .bt-item-meta { color: var(--accent); }
.bt-item.bt-running .bt-item-meta { color: var(--accent-2); }
.bt-status { color: var(--txt-2); font: 500 var(--fs-xs)/1 var(--font-ui); text-transform: uppercase; letter-spacing: .06em; }
.bt-status.bt-failed { color: var(--danger); }
.bt-status.bt-done { color: var(--accent); }
.bt-status.bt-running { color: var(--accent-2); }
.bt-summary { display: flex; align-items: center; gap: var(--sp-4); flex-wrap: wrap; }
.bt-count { color: var(--txt-0); font: 500 var(--fs-sm)/1.4 var(--font-ui); }
.bt-count b { font-weight: 600; }
.bt-count .bt-bad { color: var(--danger); }
.bt-note { color: var(--txt-2); font: 400 var(--fs-xs)/1.4 var(--font-ui); }
.bt-error { color: var(--danger); font: 400 var(--fs-xs)/1.4 var(--font-ui); }
.bt-foot { display: flex; align-items: center; gap: var(--sp-3); padding-top: var(--sp-3); border-top: 1px solid var(--line); }
.bt-remove { background: transparent; border: 0; color: var(--txt-2); cursor: pointer; font: 400 var(--fs-sm)/1 var(--font-ui); padding: 0 2px; }
.bt-remove:hover { color: var(--danger); }
`;

/* ------------------------------------------------------------------ utils */

/** Resolve the document that owns a node; falls back to the global document. */
function ownerDoc(node) {
  if (node && node.ownerDocument) return node.ownerDocument;
  return typeof document !== "undefined" ? document : null;
}

/** Inject the component stylesheet once per document. */
function ensureStyle(doc) {
  if (!doc) return;
  const head = doc.head || (doc.getElementsByTagName ? doc.getElementsByTagName("head")[0] : null);
  if (!head) return;
  if (doc.getElementById && doc.getElementById(STYLE_ID)) return;
  const style = doc.createElement("style");
  style.setAttribute("id", STYLE_ID);
  style.textContent = BATCH_CSS;
  head.appendChild(style);
}

/** Remove every child of a node. */
function clear(node) {
  if (!node) return;
  if (typeof node.replaceChildren === "function") {
    node.replaceChildren();
    return;
  }
  while (node.firstChild) node.removeChild(node.firstChild);
}

/** An integer, or a fallback when the value is missing. */
function toInt(value, fallback) {
  const n = Number(value);
  return Number.isFinite(n) ? Math.round(n) : fallback;
}

/** A finite number, or null. */
function toNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

/** The file name of a path, without the directory. */
function baseName(path) {
  const text = String(path || "");
  const cut = Math.max(text.lastIndexOf("/"), text.lastIndexOf("\\"));
  return cut >= 0 ? text.slice(cut + 1) : text;
}

/** Post or get JSON and raise the server message on failure. */
async function requestJSON(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await res.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch (_err) {
      data = null;
    }
  }
  if (!res.ok) {
    const message =
      (data && (data.error || data.detail)) || `${res.status} ${res.statusText || "request failed"}`;
    throw new Error(String(message));
  }
  return data && typeof data === "object" ? data : {};
}

/* ----------------------------------------------------------- normalisation */

/** Normalise one chain step into the fields the summary needs. */
function normalizeStep(raw) {
  const step = raw && typeof raw === "object" ? raw : {};
  const code = String(step.code || step.module_code || "");
  let bypass = false;
  if (typeof step.bypass === "boolean") bypass = step.bypass;
  else if (typeof step.bypassed === "boolean") bypass = step.bypassed;
  else if (typeof step.enabled === "boolean") bypass = !step.enabled;
  return {
    code,
    display_name: String(step.display_name || step.name || code),
    bypass,
    params: step.params && typeof step.params === "object" ? step.params : {},
  };
}

/** Normalise a chain into an array of steps. */
function normalizeChain(raw) {
  if (!Array.isArray(raw)) return [];
  return raw.map(normalizeStep);
}

/** Normalise one delivery target record. */
function normalizePreset(raw) {
  const p = raw && typeof raw === "object" ? raw : {};
  const loudness = p.loudness && typeof p.loudness === "object" ? p.loudness : null;
  const dither = p.dither && typeof p.dither === "object" ? p.dither : null;
  return {
    key: String(p.key || p.id || p.name || ""),
    name: String(p.name || p.key || ""),
    integrated_lufs: toNumber(p.integrated_lufs),
    true_peak_dbtp: toNumber(p.true_peak_dbtp),
    codec: String(p.codec || ""),
    format: String(p.format || "wav"),
    bit_depth: toInt(p.bit_depth, 24),
    kind: String(p.kind || ""),
    description: String(p.description || ""),
    loudness: loudness ? { code: String(loudness.code || ""), params: loudness.params || {} } : null,
    dither: dither ? { code: String(dither.code || ""), params: dither.params || {} } : null,
  };
}

/** Normalise the delivery catalogue into an array of targets. */
function normalizePresets(raw) {
  let list = raw;
  if (list && !Array.isArray(list) && typeof list === "object") {
    list = list.targets || list.presets || list.delivery_targets || [];
  }
  if (!Array.isArray(list)) return [];
  return list.map(normalizePreset).filter((p) => p.key);
}

/** Normalise one input of the file list. */
function normalizeEntry(raw, index) {
  const id = `f${index}_${Math.random().toString(36).slice(2, 8)}`;
  if (typeof raw === "string") {
    return { id, name: baseName(raw) || raw, path: raw, file: null, status: "ready", error: "" };
  }
  if (raw && typeof raw === "object") {
    /* A File object carries a name and a size but no path. */
    const isFile =
      typeof raw.name === "string" && typeof raw.size === "number" && !raw.path && !raw.file;
    const path = String(raw.path || raw.server_path || "");
    const file = raw.file || (isFile ? raw : null);
    const name = String(raw.name || (file && file.name) || baseName(path) || "file");
    return {
      id: String(raw.id || id),
      name,
      path,
      file,
      assetId: String(raw.asset_id || ""),
      status: String(raw.status || (path ? "ready" : file ? "staging" : "ready")),
      error: String(raw.error || ""),
    };
  }
  return { id, name: "file", path: "", file: null, status: "ready", error: "" };
}

/* -------------------------------------------------------------- view model */

/** The loudness a result reports, or null when it reports none. */
function loudnessOf(result) {
  if (!result || typeof result !== "object") return null;
  const keys = ["loudness_lufs", "integrated_lufs", "measured_lufs", "loudness"];
  for (const key of keys) {
    const value = toNumber(result[key]);
    if (value !== null) return value;
  }
  return null;
}

/**
 * The view model of one batch job.
 *
 * The function is pure: it takes the job document the server returns and
 * returns everything the panel needs to draw.  Keeping it free of the DOM
 * makes the rendering testable.
 */
export function batchViewModel(job) {
  const src = job && typeof job === "object" ? job : {};
  const results = Array.isArray(src.results) ? src.results : [];
  const total = toInt(src.total, results.length);
  const done = toInt(src.done, results.filter((r) => r && !r.error).length);
  const failed = toInt(src.failed, results.filter((r) => r && r.error).length);

  const items = results.map((result, index) => {
    const r = result && typeof result === "object" ? result : {};
    const error = String(r.error || "");
    const input = String(r.input || "");
    return {
      index,
      name: baseName(input) || input || t("batch.fileName", "file {n}", { n: index + 1 }),
      input,
      output: String(r.output || ""),
      error,
      notes: Array.isArray(r.notes) ? r.notes.map(String) : [],
      duration: toNumber(r.duration) || 0,
      elapsed: toNumber(r.elapsed) || 0,
      gainDb: toNumber(r.gain_db),
      loudness: loudnessOf(r),
      status: error ? "failed" : "done",
    };
  });

  const measured = items.map((item) => item.loudness).filter((v) => v !== null);
  const mean = measured.length
    ? measured.reduce((sum, value) => sum + value, 0) / measured.length
    : null;

  const state = String(src.state || "idle");
  return {
    jobId: String(src.job_id || ""),
    state,
    total,
    done,
    failed,
    processed: items.length,
    pending: Math.max(0, total - items.length),
    running: state === "queued" || state === "running",
    complete: state === "done" || state === "error" || state === "cancelled",
    outDir: String(src.out_dir || ""),
    error: String(src.error || ""),
    items,
    measured,
    meanLoudness: mean,
    loudnessReported: measured.length > 0,
  };
}

/* -------------------------------------------------------------- rendering */

/**
 * Render the batch processor into `container`.
 *
 * @param {Element} container                  element that receives the panel
 * @param {object}  options                    { files, chain, presets, onStart, onStop }
 * @param {Array}   options.files              inputs: server paths, File objects or records
 * @param {Array}   options.chain              the current chain, for the summary
 * @param {Array}   options.presets            the delivery catalogue
 * @param {Function} options.onStart           called with the payload before the job starts
 * @param {Function} options.onStop            called with the job id after a stop request
 * @returns {{ update: Function, destroy: Function }}
 */
export function renderBatch(container, options = {}) {
  const doc = ownerDoc(container);
  if (!doc || !container) {
    throw new Error("renderBatch: a DOM container element is required");
  }
  ensureStyle(doc);

  const opts = options && typeof options === "object" ? options : {};
  const state = {
    mode: "chain", // "chain" (the chain of the session) or "preset" (a delivery target)
    targetKey: "",
    format: "wav",
    bitDepth: 24,
    outDir: "",
    entries: [],
    chain: normalizeChain(opts.chain),
    presets: normalizePresets(opts.presets),
    job: null,
    jobId: "",
    running: false,
    message: "",
  };

  let pollTimer = 0;
  let destroyed = false;
  const cleanups = [];

  /* ------------------------------------------------------------- helpers */

  function findPreset(key) {
    return state.presets.find((p) => p.key === key) || null;
  }

  function currentPreset() {
    return state.mode === "preset" ? findPreset(state.targetKey) : null;
  }

  function make(tag, className, text) {
    const node = doc.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function on(node, event, handler) {
    node.addEventListener(event, handler);
    cleanups.push(() => node.removeEventListener(event, handler));
  }

  /* --------------------------------------------------------------- layout */

  const root = make("div", "bt-root");
  const previous = ROOTS.get(container);
  if (previous && previous.parentNode) previous.parentNode.removeChild(previous);
  ROOTS.set(container, root);
  container.appendChild(root);

  /* -- head -- */
  const head = make("div", "bt-head");
  head.append(make("span", "bt-title", t("batch.title", "Batch Processor")));
  const modeNote = make("span", "bt-sub", "");
  head.append(modeNote);
  head.append(make("span", "bt-spacer"));

  const modeButtons = make("div", "bt-row");
  const modeChain = make("button", "tbtn tbtn-sm", t("batch.modeChain", "Current chain"));
  const modePreset = make("button", "tbtn tbtn-sm", t("batch.modePreset", "Delivery preset"));
  modeButtons.append(modeChain, modePreset);
  head.append(modeButtons);
  root.append(head);

  /* -- delivery section -- */
  const deliverySection = make("div", "bt-section");
  deliverySection.append(make("div", "bt-section-title", t("batch.sectionDelivery", "Delivery")));
  const targetRow = make("div", "bt-row");
  targetRow.append(make("span", "bt-label", t("batch.labelTarget", "Target")));
  const targetSelect = make("select", "bt-select");
  targetSelect.setAttribute("aria-label", t("batch.ariaTarget", "Delivery target"));
  targetRow.append(targetSelect);
  const facts = make("span", "bt-facts", "");
  targetRow.append(facts);
  deliverySection.append(targetRow);
  root.append(deliverySection);

  /* -- chain section -- */
  const chainSection = make("div", "bt-section");
  chainSection.append(make("div", "bt-section-title", t("batch.sectionChain", "Chain")));
  const chainRow = make("div", "bt-chain");
  chainSection.append(chainRow);
  root.append(chainSection);

  /* -- output section -- */
  const outputSection = make("div", "bt-section");
  outputSection.append(make("div", "bt-section-title", t("batch.sectionOutput", "Output")));
  const outputRow = make("div", "bt-row");
  const formatField = make("div", "bt-field");
  formatField.append(make("span", "bt-label", t("batch.labelFormat", "Format")));
  const formatSelect = make("select", "bt-select");
  formatSelect.setAttribute("aria-label", t("batch.ariaFormat", "Output format"));
  for (const fmt of FORMATS) formatSelect.append(el("option", { value: fmt, text: fmt }));
  formatField.append(formatSelect);
  const depthField = make("div", "bt-field");
  depthField.append(make("span", "bt-label", t("batch.labelBitDepth", "Bit depth")));
  const depthSelect = make("select", "bt-select");
  depthSelect.setAttribute("aria-label", t("batch.ariaBitDepth", "Output bit depth"));
  for (const depth of BIT_DEPTHS) {
    depthSelect.append(el("option", { value: String(depth), text: t("batch.bit", "{n} bit", { n: depth }) }));
  }
  depthField.append(depthSelect);
  const outField = make("div", "bt-field");
  outField.append(make("span", "bt-label", t("batch.labelFolder", "Folder")));
  const outInput = make("input", "bt-input");
  outInput.type = "text";
  outInput.placeholder = t("batch.placeholderFolder", "server default");
  outInput.setAttribute("aria-label", t("batch.ariaFolder", "Output folder"));
  outField.append(outInput);
  outputRow.append(formatField, depthField, outField);
  outputSection.append(outputRow);
  root.append(outputSection);

  /* -- files section -- */
  const filesSection = make("div", "bt-section");
  filesSection.append(make("div", "bt-section-title", t("batch.sectionFiles", "Files")));
  const drop = make(
    "div",
    "bt-drop",
    t("batch.dropHint", "Drop audio files here, or drop a list of server paths")
  );
  filesSection.append(drop);
  const fileActions = make("div", "bt-row");
  const pickerButton = make("button", "tbtn tbtn-sm", t("batch.addFiles", "Add files"));
  const pathInput = make("input", "bt-input");
  pathInput.type = "text";
  pathInput.placeholder = t("batch.placeholderPath", "server path of one file");
  pathInput.setAttribute("aria-label", t("batch.ariaPath", "Server path"));
  const pathButton = make("button", "tbtn tbtn-sm", t("batch.addPath", "Add path"));
  const clearButton = make("button", "tbtn tbtn-sm", t("batch.clearList", "Clear list"));
  fileActions.append(pickerButton, pathInput, pathButton, clearButton);
  filesSection.append(fileActions);
  const fileInput = make("input", "bt-input");
  fileInput.type = "file";
  fileInput.multiple = true;
  fileInput.style.display = "none";
  fileInput.setAttribute("aria-label", t("batch.ariaFiles", "Choose audio files"));
  filesSection.append(fileInput);
  const fileList = make("div", "bt-list");
  filesSection.append(fileList);
  const fileNote = make("div", "bt-note", "");
  filesSection.append(fileNote);
  root.append(filesSection);

  /* -- progress section -- */
  const progressSection = make("div", "bt-section");
  progressSection.append(make("div", "bt-section-title", t("batch.sectionProgress", "Progress")));
  const progressList = make("div", "bt-list");
  progressSection.append(progressList);
  root.append(progressSection);

  /* -- summary and foot -- */
  const summaryRow = make("div", "bt-summary");
  const summaryCount = make("span", "bt-count", "");
  const summaryLoudness = make("span", "bt-note", "");
  summaryRow.append(summaryCount, summaryLoudness);
  const errorNote = make("div", "bt-error", "");
  root.append(summaryRow, errorNote);

  const foot = make("div", "bt-foot");
  const startButton = make("button", "tbtn tbtn-primary", t("batch.start", "Start batch"));
  const stopButton = make("button", "tbtn tbtn-sm", t("batch.stop", "Stop"));
  stopButton.disabled = true;
  const footNote = make("span", "bt-note", "");
  foot.append(startButton, stopButton, footNote);
  root.append(foot);

  /* ------------------------------------------------------------ behaviour */

  function setMessage(text) {
    state.message = String(text || "");
    errorNote.textContent = state.message;
  }

  function addEntry(entry) {
    state.entries.push(entry);
    render();
    return entry;
  }

  function addServerPath(path) {
    const text = String(path || "").trim();
    if (!text) return;
    addEntry(normalizeEntry(text, state.entries.length));
  }

  function addLocalFile(file) {
    const entry = normalizeEntry({ name: file.name, file }, state.entries.length);
    entry.status = "staging";
    addEntry(entry);
    stageFile(entry);
  }

  /** Upload a local file and remember the path the batch runner can read. */
  async function stageFile(entry) {
    try {
      const res = await api.upload(entry.file);
      const assetId = (res && res.asset_id) || "";
      if (!assetId) {
        throw new Error(t("batch.errorNoAssetId", "the upload did not return an asset id"));
      }
      entry.assetId = assetId;
      entry.path = STAGED_PATH.replace("{asset_id}", assetId);
      entry.status = "staged";
      entry.error = "";
    } catch (err) {
      entry.status = "error";
      entry.error = err.message || String(err);
    }
    render();
  }

  /** The chain that is sent to the server: the session chain plus the delivery stages. */
  function buildChain() {
    const steps = state.chain.map((step) => ({
      code: step.code,
      enabled: !step.bypass,
      params: step.params || {},
    }));
    const preset = currentPreset();
    if (!preset) return steps;
    if (preset.loudness && preset.loudness.code) {
      steps.push({
        code: preset.loudness.code,
        enabled: true,
        params: { ...preset.loudness.params },
      });
    }
    if (preset.dither && preset.dither.code && DITHER_DEPTHS.indexOf(state.bitDepth) >= 0) {
      steps.push({
        code: preset.dither.code,
        enabled: true,
        params: { ...preset.dither.params, "Bit Depth": String(state.bitDepth) },
      });
    }
    return steps;
  }

  function isTerminal(job) {
    const view = batchViewModel(job);
    return view.complete;
  }

  async function start() {
    if (state.running) return;
    const paths = state.entries.map((entry) => entry.path).filter(Boolean);
    if (!paths.length) {
      setMessage(t("batch.errorNoFiles", "Add at least one file to the batch."));
      return;
    }
    const payload = {
      paths,
      chain: buildChain(),
      format: state.format,
      bit_depth: state.bitDepth,
      loudness_normalise: false,
    };
    const outDir = state.outDir.trim();
    if (outDir) payload.out_dir = outDir;

    if (typeof opts.onStart === "function") {
      let verdict;
      try {
        verdict = await opts.onStart(payload);
      } catch (err) {
        setMessage(err.message || String(err));
        return;
      }
      if (verdict === false) return; // the host refused the job
    }

    setMessage("");
    state.running = true;
    state.job = null;
    render();
    try {
      const res = await requestJSON("/api/batch", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      state.jobId = (res && res.job_id) || "";
      state.job = res || null;
      render();
      poll();
    } catch (err) {
      state.running = false;
      setMessage(err.message || String(err));
      render();
    }
  }

  async function stop() {
    if (!state.jobId) return;
    try {
      await requestJSON(`/api/batch/${state.jobId}/cancel`, { method: "POST", body: "{}" });
    } catch (err) {
      setMessage(err.message || String(err));
    }
    if (typeof opts.onStop === "function") {
      try {
        opts.onStop(state.jobId);
      } catch (_err) {
        /* the host owns error reporting */
      }
    }
  }

  async function poll() {
    if (destroyed || !state.jobId) return;
    try {
      const job = await requestJSON(`/api/batch/${state.jobId}`);
      update(job);
      if (!isTerminal(job)) {
        pollTimer = setTimeout(poll, POLL_MS);
      } else {
        state.running = false;
        render();
      }
    } catch (err) {
      state.running = false;
      setMessage(err.message || String(err));
      render();
    }
  }

  /* --------------------------------------------------------------- render */

  function renderTargets() {
    clear(targetSelect);
    if (!state.presets.length) {
      const option = el("option", { value: "", text: t("batch.noTargets", "no delivery targets") });
      targetSelect.append(option);
      targetSelect.disabled = true;
      return;
    }
    targetSelect.disabled = false;
    for (const preset of state.presets) {
      targetSelect.append(el("option", { value: preset.key, text: preset.name }));
    }
    if (!findPreset(state.targetKey)) state.targetKey = state.presets[0].key;
    targetSelect.value = state.targetKey;
  }

  function renderFacts() {
    const preset = currentPreset();
    if (!preset) {
      facts.textContent = t("batch.factsChainOnly", "the chain of the session is sent unchanged");
      return;
    }
    const parts = [];
    if (preset.integrated_lufs !== null) parts.push(`${preset.integrated_lufs.toFixed(1)} LUFS`);
    if (preset.true_peak_dbtp !== null) parts.push(`${preset.true_peak_dbtp.toFixed(1)} dBTP`);
    if (preset.codec) parts.push(preset.codec.toUpperCase());
    parts.push(`${state.bitDepth} bit`);
    facts.textContent = parts.join(" · ");
  }

  function renderChainSummary() {
    clear(chainRow);
    const steps = state.chain;
    if (!steps.length) {
      chainRow.append(
        make(
          "span",
          "bt-empty",
          t("batch.chainEmpty", "The chain is empty — the delivery stages only are sent.")
        )
      );
    }
    steps.forEach((step, index) => {
      if (index > 0) chainRow.append(make("span", "bt-arrow", "→"));
      const chip = make("span", "bt-chip");
      if (step.bypass) chip.classList.add("bt-bypassed");
      chip.append(make("span", "", step.display_name || step.code));
      chip.setAttribute("title", step.code);
      chainRow.append(chip);
    });
    const preset = currentPreset();
    if (preset) {
      if (steps.length) chainRow.append(make("span", "bt-arrow", "→"));
      if (preset.loudness && preset.loudness.code) {
        const chip = make("span", "bt-chip bt-added");
        chip.append(make("span", "", t("batch.stageLoudness", "Loudness Control")));
        chip.setAttribute("title", `${preset.loudness.code} — ${preset.integrated_lufs} LUFS`);
        chainRow.append(chip);
      }
      if (preset.dither && preset.dither.code && DITHER_DEPTHS.indexOf(state.bitDepth) >= 0) {
        chainRow.append(make("span", "bt-arrow", "→"));
        const chip = make("span", "bt-chip bt-added");
        chip.append(make("span", "", t("batch.stageDither", "Dither")));
        chip.setAttribute(
          "title",
          `${preset.dither.code} — ${t("batch.bit", "{n} bit", { n: state.bitDepth })}`
        );
        chainRow.append(chip);
      }
    }
  }

  function renderFiles() {
    clear(fileList);
    if (!state.entries.length) {
      fileList.append(make("span", "bt-empty", t("batch.noFiles", "No files yet.")));
      fileNote.textContent = "";
      return;
    }
    for (const entry of state.entries) {
      const row = make("div", "bt-item");
      row.append(make("span", "bt-item-name", entry.name));
      const status = make("span", "bt-status", stateLabel(entry.status));
      if (entry.status === "error") status.classList.add("bt-failed");
      row.append(status);
      row.append(make("span", "bt-item-meta", entry.error || entry.path || ""));
      const remove = make("button", "bt-remove", "×");
      remove.setAttribute("type", "button");
      remove.setAttribute("title", t("batch.removeTitle", "Remove from the batch"));
      remove.addEventListener("click", () => {
        state.entries = state.entries.filter((item) => item.id !== entry.id);
        render();
      });
      row.append(remove);
      fileList.append(row);
    }
    const staged = state.entries.filter((entry) => entry.status === "staged").length;
    const pending = state.entries.filter((entry) => entry.status === "staging").length;
    const notes = [];
    if (pending) {
      notes.push(t("batch.uploadingFiles", "{n} file(s) are still being uploaded", { n: pending }));
    }
    if (staged > STAGED_LIMIT) {
      notes.push(
        t(
          "batch.stagedLimit",
          "the server keeps {n} uploaded files alive — run the batch in smaller parts",
          { n: STAGED_LIMIT }
        )
      );
    }
    fileNote.textContent = notes.join(" · ");
  }

  function renderProgress() {
    clear(progressList);
    const view = batchViewModel(state.job);
    if (!view.items.length) {
      progressList.append(
        make("span", "bt-empty", t("batch.noProgress", "No file has been processed yet."))
      );
      return;
    }
    for (const item of view.items) {
      const row = make("div", `bt-item bt-${item.status}`);
      row.append(make("span", "bt-item-name", item.name));
      const status = make("span", "bt-status", stateLabel(item.status));
      status.classList.add(`bt-${item.status}`);
      row.append(status);
      const bits = [];
      if (item.loudness !== null) bits.push(`${item.loudness.toFixed(1)} LUFS`);
      else if (item.gainDb !== null) bits.push(`${item.gainDb >= 0 ? "+" : ""}${item.gainDb.toFixed(1)} dB`);
      if (item.error) bits.push(item.error);
      row.append(make("span", "bt-item-meta", bits.join(" · ")));
      progressList.append(row);
    }
  }

  function renderSummary() {
    const view = batchViewModel(state.job);
    const done = document.createElement("span");
    done.append(el("b", {}, String(view.done)), document.createTextNode(` ${t("batch.countDone", "done")}`));
    const failed = document.createElement("span");
    if (view.failed) failed.classList.add("bt-bad");
    failed.append(
      el("b", {}, String(view.failed)),
      document.createTextNode(` ${t("batch.countFailed", "failed")}`)
    );
    const total = document.createElement("span");
    total.append(
      el("b", {}, String(view.total)),
      document.createTextNode(` ${t("batch.countTotal", "total")}`)
    );
    clear(summaryCount);
    summaryCount.append(done, document.createTextNode(" · "), failed, document.createTextNode(" · "), total);
    if (view.loudnessReported) {
      summaryLoudness.textContent = t(
        "batch.measuredLoudness",
        "measured loudness {value} LUFS ({n} file(s))",
        { value: view.meanLoudness.toFixed(1), n: view.measured.length }
      );
    } else if (view.items.length) {
      summaryLoudness.textContent = t(
        "batch.noLoudness",
        "the server did not report a loudness measurement"
      );
    } else {
      summaryLoudness.textContent = "";
    }
  }

  function render() {
    if (destroyed) return;
    modeChain.classList.toggle("on", state.mode === "chain");
    modePreset.classList.toggle("on", state.mode === "preset");
    modeChain.disabled = !state.presets.length;
    modePreset.disabled = !state.presets.length;
    modeNote.textContent = state.presets.length
      ? t("batch.targetCount", "{n} delivery target(s)", { n: state.presets.length })
      : t("batch.noCatalogue", "no delivery catalogue was supplied");

    targetRow.style.display = state.mode === "preset" ? "" : "none";
    renderTargets();
    renderFacts();
    renderChainSummary();
    renderFiles();
    renderProgress();
    renderSummary();

    const preset = currentPreset();
    const ready = state.entries.some((entry) => entry.path);
    startButton.disabled = state.running || !ready;
    stopButton.disabled = !state.running;
    footNote.textContent = state.running
      ? t("batch.jobRunning", "job {id} is running", { id: state.jobId || "" })
      : t("batch.fileCount", "{n} file(s) in the list", { n: state.entries.length });
  }

  /* ------------------------------------------------------------- listeners */

  on(modeChain, "click", () => {
    state.mode = "chain";
    render();
  });
  on(modePreset, "click", () => {
    state.mode = "preset";
    render();
  });
  on(targetSelect, "change", () => {
    state.targetKey = targetSelect.value;
    const preset = findPreset(state.targetKey);
    if (preset) {
      state.format = preset.format || state.format;
      state.bitDepth = preset.bit_depth || state.bitDepth;
      formatSelect.value = state.format;
      depthSelect.value = String(state.bitDepth);
    }
    render();
  });
  on(formatSelect, "change", () => {
    state.format = formatSelect.value;
    render();
  });
  on(depthSelect, "change", () => {
    state.bitDepth = toInt(depthSelect.value, state.bitDepth);
    render();
  });
  on(outInput, "input", () => {
    state.outDir = outInput.value;
  });
  on(pickerButton, "click", () => fileInput.click());
  on(fileInput, "change", () => {
    const list = fileInput.files ? Array.from(fileInput.files) : [];
    for (const file of list) addLocalFile(file);
    fileInput.value = "";
  });
  on(pathButton, "click", () => {
    const value = pathInput.value.trim();
    if (!value) return;
    addServerPath(value);
    pathInput.value = "";
  });
  on(pathInput, "keydown", (event) => {
    if (event && event.key === "Enter") pathButton.click();
  });
  on(clearButton, "click", () => {
    state.entries = [];
    render();
  });
  on(drop, "dragover", (event) => {
    if (event && typeof event.preventDefault === "function") event.preventDefault();
    drop.classList.add("bt-over");
  });
  on(drop, "dragleave", () => drop.classList.remove("bt-over"));
  on(drop, "drop", (event) => {
    if (event && typeof event.preventDefault === "function") event.preventDefault();
    drop.classList.remove("bt-over");
    const transfer = event && event.dataTransfer;
    if (!transfer) return;
    let files = [];
    try {
      files = transfer.files ? Array.from(transfer.files) : [];
    } catch (_err) {
      files = [];
    }
    if (files.length) {
      for (const file of files) addLocalFile(file);
      return;
    }
    let text = "";
    try {
      text = transfer.getData("text/plain") || "";
    } catch (_err) {
      text = "";
    }
    for (const line of String(text).split(/\r?\n/)) {
      const value = line.trim();
      if (value) addServerPath(value);
    }
  });
  on(startButton, "click", () => start());
  on(stopButton, "click", () => stop());

  /* ------------------------------------------------------------- lifecycle */

  state.entries = (Array.isArray(opts.files) ? opts.files : []).map((raw, index) =>
    normalizeEntry(raw, index)
  );
  for (const entry of state.entries) {
    /* A local file the host handed over is uploaded and staged right away. */
    if (entry.file && !entry.path && entry.status !== "error") stageFile(entry);
  }
  if (state.presets.length) {
    state.targetKey = state.presets[0].key;
    const preset = findPreset(state.targetKey);
    if (preset) {
      state.format = preset.format || state.format;
      state.bitDepth = preset.bit_depth || state.bitDepth;
    }
  }
  formatSelect.value = state.format;
  depthSelect.value = String(state.bitDepth);
  render();

  /**
   * Show one job document.
   *
   * The second argument is optional and may carry a fresh chain, catalogue
   * or file list when the host changes them.
   */
  function update(job, extra) {
    if (destroyed) return;
    state.job = job || null;
    if (extra && typeof extra === "object") {
      if (extra.chain !== undefined) state.chain = normalizeChain(extra.chain);
      if (extra.presets !== undefined) state.presets = normalizePresets(extra.presets);
      if (extra.files !== undefined) {
        state.entries = (Array.isArray(extra.files) ? extra.files : []).map((raw, index) =>
          normalizeEntry(raw, index)
        );
      }
    }
    if (state.job && state.job.state) {
      const view = batchViewModel(state.job);
      state.running = view.running;
      if (view.jobId) state.jobId = view.jobId;
    }
    render();
  }

  /** Stop the polling, drop every listener and remove the panel. */
  function destroy() {
    if (destroyed) return;
    destroyed = true;
    if (pollTimer) {
      clearTimeout(pollTimer);
      pollTimer = 0;
    }
    for (const off of cleanups.splice(0)) {
      try {
        off();
      } catch (_err) {
        /* the node may already be gone */
      }
    }
    if (root.parentNode) root.parentNode.removeChild(root);
    if (ROOTS.get(container) === root) ROOTS.delete(container);
  }

  return { update, destroy };
}
