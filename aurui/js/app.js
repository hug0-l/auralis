/* Auralis — application controller.
 *
 * Owns the DOM skeleton declared in index.html and wires it to the server
 * API (js/api.js) and to the four presentation modules:
 *
 *   view.js     -> export class CompositeView(specCanvas, waveCanvas, wrapEl)
 *                  methods: setData, setZoom, setScroll, setViewMode,
 *                  setFrequencyRange, setSelection, clearSelection,
 *                  setLogScale, setLogFrequency, setSelectionModes, render
 *                  events:  onPointer(cb) -> { type, t, f, x, y, zoom, selection }
 *
 *   panel.js    -> export function renderPanel(container, options, handlers)
 *                  options: { moduleInfo, values, presetName, savedValues,
 *                              bypassed, dirty }
 *                  handlers:{ onParam, onBypass, onLearn, onCompare,
 *                              onRemove, onPreset("save"|"load"|"compare") }
 *                  returns: { update(values), destroy, setBypassed, setPreset }
 *
 *   browser.js  -> export function renderBrowser(container, options, handlers)
 *                  options: { modules, onAdd, searchQuery, searchInput }
 *                  returns: { update(modules), setQuery(text) }
 *
 *   chain.js    -> export function renderChain(container, chain, handlers)
 *                  chain:   [{ code, display_name, category, edition, bypass }]
 *                  handlers:{ onSelect, onBypass, onReorder, onRemove }
 *                  returns: { update(chain), select(index) }
 *
 * Every import of the shell is *optional*: they are all loaded with a
 * dynamic import, and a missing or broken file only costs the feature it
 * carries — the workbench still boots and the shell falls back to its own
 * rendering.
 *
 *   analysis.js        -> renderAnalysis(container, data, opts)
 *                         emptyAnalysis()
 *                        the measurement panel of the Analysis tab
 *   batch.js           -> renderBatch(container, {files, chain, presets,
 *                         onStart, onStop})
 *                        the batch processor of the Batch tab
 *   spectral_editor.js -> createSpectralEditor({container, onChange, onPreview})
 *                        renderEditor(view, state)
 *                        the region overlay of the composite view
 *   keymap.js          -> installKeymap(commands), applyAccessibility(root)
 *                        buildHelpOverlay(doc), toggleHelpOverlay(doc)
 *                        the declarative keyboard map
 *   i18n.js            -> t(key, fallback, vars), setLocale(code),
 *                        availableLocales(), localeName(code),
 *                        translateDocument(root)
 *                        the string catalogue of the shell
 *   session.js         -> createSessionController({api, engine, onChange})
 *                        renderSessionBar(container, controller)
 *                        the undo / redo history and the session document
 *   preset_manager.js  -> createPresetManager({api, container, onLoad})
 *                        renderPresetMenu(container, manager, handlers)
 *                        the preset library and the preset menu
 *
 * Every network call is wrapped in try/catch and surfaced in the toast; the
 * UI is never left in a half-applied state after a failed call.
 */

/* ------------------------------------------------------------------ setup */

/* The api client and its helpers.
 *
 * They are filled in when api.js has loaded; until then — and for ever when
 * api.js is missing — the fallbacks below are used, so that the skeleton of
 * the page can still be painted and the user can be told what is missing.
 */

/** Build an element — the fallback of the helper of api.js. */
function fallbackEl(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") {
      node.addEventListener(k.slice(2).toLowerCase(), v);
    } else if (v !== null && v !== undefined && v !== false) {
      node.setAttribute(k, v === true ? "" : v);
    }
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

/** Find one element — the fallback of the helper of api.js. */
function fallbackQuery(sel, root = document) {
  return root.querySelector(sel);
}

/** Format a level — the fallback of the helper of api.js. */
function fallbackFmtDb(v) {
  if (v === -Infinity || v === null || v === undefined) return "-inf";
  return `${v > 0 ? "+" : ""}${Number(v).toFixed(1)} dB`;
}

/** Format a time — the fallback of the helper of api.js. */
function fallbackFmtTime(seconds) {
  if (!isFinite(seconds)) return "--:--.--";
  const s = Math.max(0, seconds);
  const m = Math.floor(s / 60);
  const rest = s - m * 60;
  return `${String(m).padStart(2, "0")}:${rest.toFixed(2).padStart(5, "0")}`;
}

/* The api client and its helpers. */
let api = null;
let el = fallbackEl;
let $ = fallbackQuery;
let fmtDb = fallbackFmtDb;
let fmtTime = fallbackFmtTime;

/* The four presentation modules — they are optional as well. */
let CompositeView = null;
let renderPanel = null;
let renderBrowser = null;
let renderChain = null;

const PREVIEW_SECONDS = 0.5;
const ZOOM_MIN = 1;
const ZOOM_MAX = 128;
const POLL_MS = 120;

/** The code of the module the spectral editor drives. */
const SPECTRAL_EDITOR_CODE = "AUR_SPECTRALEDITOR";

/** The tabs of the inspector, in the order the cycle command walks them. */
const INSPECTOR_TABS = ["panel", "chain", "analysis", "batch", "presets"];

/** Where the chosen locale is remembered between two sessions. */
const LOCALE_STORAGE_KEY = "auralis.locale";

/** How long the shell waits before it measures a new selection. */
const ANALYSIS_DEBOUNCE_MS = 400;

/**
 * The elements of the page.
 *
 * The map is filled when the workbench boots: the ids are resolved after the
 * modules have loaded, so a missing api.js does not stop the shell from
 * finding the skeleton of the page.
 */
const dom = {};

/** Resolve every element of the workbench. */
function resolveDom() {
  dom.app = $("#app");
  dom.topbar = $("#topbar");
  dom.topbarRight = $(".topbar-right");
  dom.btnOpen = $("#btn-open");
  dom.fileInput = $("#file-input");
  dom.fileName = $("#file-name");
  dom.fileMeta = $("#file-meta");
  dom.btnPlay = $("#btn-play");
  dom.btnStop = $("#btn-stop");
  dom.btnLoop = $("#btn-loop");
  dom.viewMode = $("#view-mode");
  dom.btnCompare = $("#btn-compare");
  dom.btnRender = $("#btn-render");
  dom.learnedSelect = $("#learned-select");
  dom.localeSelect = $("#locale-select");
  dom.btnContrast = $("#btn-contrast");
  dom.rail = $("#rail");
  dom.moduleSearch = $("#module-search");
  dom.moduleTree = $("#module-tree");
  dom.stage = $("#stage");
  dom.viewToolbar = $("#view-toolbar");
  dom.btnZoomOut = $("#btn-zoom-out");
  dom.btnZoomIn = $("#btn-zoom-in");
  dom.btnZoomFit = $("#btn-zoom-fit");
  dom.btnSpectral = $("#btn-spectral");
  dom.zoomLabel = $("#zoom-label");
  dom.freqLo = $("#freq-lo");
  dom.freqHi = $("#freq-hi");
  dom.chkSelFreq = $("#chk-sel-freq");
  dom.chkSelTime = $("#chk-sel-time");
  dom.dbRange = $("#db-range");
  dom.chkLog = $("#chk-log");
  dom.canvasWrap = $("#canvas-wrap");
  dom.specCanvas = $("#spec-canvas");
  dom.waveCanvas = $("#wave-canvas");
  dom.hoverReadout = $("#hover-readout");
  dom.inspector = $("#inspector");
  dom.inspectorTabs = $("#inspector-tabs");
  dom.inspectorBody = $("#inspector-body");
  dom.statusbar = $("#statusbar");
  dom.stCursor = $("#st-cursor");
  dom.stSel = $("#st-sel");
  dom.stPath = $("#st-path");
  dom.pathPanel = $("#path-panel");
  dom.stLatency = $("#st-latency");
  dom.stLevel = $("#st-level");
  dom.stEngine = $("#st-engine");
  dom.modal = $("#modal");
  dom.modalTitle = $("#modal-title");
  dom.modalClose = $("#modal-close");
  dom.modalBody = $("#modal-body");
  dom.modalFoot = $("#modal-foot");
  dom.toast = $("#toast");
}

/**
 * Application state.  Everything the shell shows is derived from this object.
 */
const state = {
  modules: [],          // ModuleInfo records from GET /api/modules
  asset: null,          // { asset_id, sample_rate, channels, frames, duration, name }
  analysis: null,       // { waveform, spectrogram, freq_bins, frames, max_db, min_db }
  chain: [],            // server chain entries, in signal order
  selectedIndex: -1,    // index into state.chain, -1 = nothing selected
  viewMode: "composite",
  zoom: 1.0,
  scroll: 0.0,          // seconds at the left edge of the visible window
  frequencyRange: [0, 22050],
  selection: null,      // { t0, t1, f0, f1 } in seconds / hertz, or null
  presetName: "",
  dirty: false,         // the selected module differs from its saved preset
  // --- working state, not persisted -------------------------------------
  cursor: { time: 0, freq: 0 },
  selectionModes: { freq: false, time: false },
  logScale: true,
  searchQuery: "",
  tab: "panel",
  loop: false,
  playing: false,
  engine: "idle",
  levelText: "",        // the last level readout of the status bar
  latency: null,        // the latency of the path that plays, in samples
  previewProfile: "fast", // the path the next preview asks the server for
  spectral: false,      // the spectral editor overlay is on
  locale: "en",         // the active locale of the shell
  contrast: "normal",   // the high-contrast variant of the chrome
};

/** Saved parameter values per module code (the "clean" state of a preset). */
const savedValues = new Map();

/* Presentation module instances (set during boot). */
let view = null;
let browser = null;
let chainView = null;
let panelView = null;

/* The optional modules, loaded with a dynamic import.  A module that is
   missing stays null and the shell falls back to its own rendering. */
const optional = {
  analysis: null,
  batch: null,
  spectral: null,
  keymap: null,
  i18n: null,
  session: null,
  presets: null,
  path: null,
};

/* The modules the shell cannot work without — they are loaded with a dynamic
   import as well, so that a broken file costs only its own feature. */
const core = {
  api: null,
  view: null,
  panel: null,
  browser: null,
  chain: null,
};

/* The batch panel of the inspector (js/batch.js). */
let batchPanel = null;
let batchHost = null;

/* The session controller and its toolbar (js/session.js). */
let sessionController = null;
let sessionBar = null;
let sessionHost = null;

/* The preset library and the preset menu of the panel (js/preset_manager.js). */
let presetManager = null;
let presetHost = null;
let presetMenu = null;
let presetTab = null;

/* The spectral editor overlay (js/spectral_editor.js). */
let spectralEditor = null;
let spectralState = { region: null, mode: "Attenuate", strength: 0.5 };
const spectralPushed = {};
let spectralPushedIndex = -1;

/* Timers. */
let chainStrip = null;
let toastTimer = 0;
let inspectorToken = 0;
let audioCtx = null;
let previewSource = null;
let pollTimer = 0;
let analysisTimer = 0;
let spectralTimer = 0;

/* Removes the built-in keyboard handling once the keyboard map took over. */
let removeLegacyKeyboard = null;

/* ------------------------------------------------------------- utilities */

/** Call the first method that exists on `target`, ignoring failures. */
function callAny(target, names, ...args) {
  if (!target) return false;
  for (const name of names) {
    if (typeof target[name] === "function") {
      try {
        target[name](...args);
        return true;
      } catch (err) {
        console.warn(`[auralis] ${name}() failed`, err);
      }
    }
  }
  return false;
}

/* ------------------------------------------------------- optional modules */

/**
 * The few styles the shell needs for itself.
 *
 * The colours come from the design tokens of css/tokens.css; the rule is
 * injected once, when the workbench boots.
 */
const SHELL_CSS = `
.locale-select {
  height: 22px;
  max-width: 108px;
  padding: 0 var(--sp-2);
  background: var(--bg-3);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  color: var(--txt-0);
  font-family: var(--font-ui);
  font-size: var(--fs-xs);
}
.locale-select:hover { border-color: var(--txt-3); }
.locale-select:focus-visible { outline: 1px solid var(--accent); outline-offset: 1px; }
.session-host {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  padding-right: var(--sp-3);
  border-right: 1px solid var(--line);
}
.preset-host {
  display: flex;
  flex-direction: column;
  min-height: 320px;
}
`;

/** The style element of the shell, once it has been injected. */
let shellStyle = null;

/** Inject the stylesheet of the shell into the document. */
function injectShellStyle() {
  if (shellStyle || typeof document === "undefined") return;
  const style = document.createElement("style");
  style.setAttribute("id", "aur-shell-css");
  style.textContent = SHELL_CSS;
  const head = document.head || document.documentElement;
  if (!head) return;
  head.appendChild(style);
  shellStyle = style;
}

/**
 * Load one module of the shell.
 *
 * The module is fetched with a dynamic import, so a file that is missing (or
 * that fails to parse) only costs the feature it carries: the failure is
 * reported in the console and the shell keeps working with its own, simpler
 * rendering.
 *
 * @param {object} store       the map the module is put into (`core`, `optional`)
 * @param {string} name        the key in that map
 * @param {string} specifier   the module specifier, relative to this file
 * @param {Function} [onReady] called with the module once it has loaded
 * @returns {Promise<object|null>}
 */
async function loadModule(store, name, specifier, onReady) {
  try {
    const mod = await import(specifier);
    store[name] = mod;
    if (typeof onReady === "function") {
      try {
        onReady(mod);
      } catch (err) {
        console.warn(`[auralis] the ${name} module could not be applied`, err);
      }
    }
    return mod;
  } catch (err) {
    store[name] = null;
    const reason = err && err.message ? err.message : String(err);
    console.warn(`[auralis] the ${name} module is unavailable — ${reason}`);
    /* Without the catalogue the shell keeps its English texts; the locale
       selector still offers the language that is active. */
    if (name === "i18n") buildLocaleSelector();
    return null;
  }
}

/**
 * Load the api client and the four presentation modules.
 *
 * They are optional as well: a missing file only costs its own feature and
 * the shell falls back to its own rendering.
 *
 * @returns {Promise<void>}
 */
async function loadCoreModules() {
  await Promise.all([
    loadModule(core, "api", "./api.js", (mod) => {
      if (mod.api) api = mod.api;
      if (typeof mod.el === "function") el = mod.el;
      if (typeof mod.$ === "function") $ = mod.$;
      if (typeof mod.fmtDb === "function") fmtDb = mod.fmtDb;
      if (typeof mod.fmtTime === "function") fmtTime = mod.fmtTime;
    }),
    loadModule(core, "view", "./view.js", (mod) => {
      CompositeView = mod.CompositeView || null;
    }),
    loadModule(core, "panel", "./panel.js", (mod) => {
      renderPanel = mod.renderPanel || null;
    }),
    loadModule(core, "browser", "./browser.js", (mod) => {
      renderBrowser = mod.renderBrowser || null;
    }),
    loadModule(core, "chain", "./chain.js", (mod) => {
      renderChain = mod.renderChain || null;
    }),
  ]);
}

/** Load every optional module; the shell does not wait for them. */
function loadOptionalModules() {
  return Promise.all([
    loadModule(optional, "analysis", "./analysis.js", () => {
      if (state.tab === "analysis") renderInspector();
    }),
    loadModule(optional, "batch", "./batch.js", () => {
      if (state.tab === "batch") renderInspector();
    }),
    loadModule(optional, "spectral", "./spectral_editor.js", () => {
      if (state.spectral) enableSpectralEditor();
    }),
    loadModule(optional, "keymap", "./keymap.js", () => {
      /* The keyboard map takes over from the built-in shortcuts. */
      if (installKeymapBindings() && removeLegacyKeyboard) {
        removeLegacyKeyboard();
        removeLegacyKeyboard = null;
      }
    }),
    loadModule(optional, "i18n", "./i18n.js", () => {
      initLocale();
    }),
    loadModule(optional, "session", "./session.js", () => {
      initSession();
    }),
    loadModule(optional, "presets", "./preset_manager.js", () => {
      initPresetManager();
    }),
    loadModule(optional, "path", "./path_report.js", () => {
      initPathReport();
    }),
  ]);
}

/* ----------------------------------------------------------- localisation */

/**
 * Translate a catalogue key.
 *
 * The localisation module is optional: until it has loaded — and for ever
 * when it is missing — the English text of the shell is used, so the
 * interface never shows a raw key.
 *
 * @param {string} key      the catalogue key
 * @param {string} [fallback] the English text
 * @param {object} [vars]   values for the `{placeholder}` substitution
 * @returns {string}
 */
function t(key, fallback, vars) {
  const mod = optional.i18n;
  if (mod && typeof mod.t === "function") {
    try {
      return mod.t(key, fallback, vars);
    } catch (err) {
      /* the catalogue is broken — the plain text is better than nothing */
    }
  }
  if (fallback === undefined || fallback === null) return String(key);
  return String(fallback);
}

/** Read the stored locale, if the browser lets us. */
function storedLocale() {
  try {
    return window.localStorage.getItem(LOCALE_STORAGE_KEY) || "";
  } catch (err) {
    return "";
  }
}

/** Remember the chosen locale. */
function storeLocale(code) {
  try {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, code);
  } catch (err) {
    /* the storage may be blocked; the locale then lasts for this session */
  }
}

/** Where the chosen contrast is remembered between sessions. */
const CONTRAST_STORAGE_KEY = "auralis.contrast";

/** Read the stored contrast choice, if the browser lets us. */
function storedContrast() {
  try {
    return window.localStorage.getItem(CONTRAST_STORAGE_KEY) || "";
  } catch (err) {
    return "";
  }
}

/** Remember the chosen contrast. */
function storeContrast(value) {
  try {
    window.localStorage.setItem(CONTRAST_STORAGE_KEY, value);
  } catch (err) {
    /* the storage may be blocked; the choice then lasts for this session */
  }
}

/**
 * Apply the contrast variant.
 *
 * The stylesheets read `data-contrast` on <html> ("high" switches the
 * variant on), so that attribute is the whole state of the interface;
 * the button shows it through `aria-pressed`.
 */
function applyContrast(high) {
  const value = high ? "high" : "normal";
  document.documentElement.setAttribute("data-contrast", value);
  if (dom.btnContrast) {
    dom.btnContrast.setAttribute("aria-pressed", high ? "true" : "false");
    dom.btnContrast.classList.toggle("on", !!high);
    dom.btnContrast.setAttribute("aria-label", t("topbar.contrast", "High contrast"));
    dom.btnContrast.setAttribute("title", t("topbar.contrast.title", "High contrast"));
  }
  state.contrast = value;
}

/** Start the contrast up: the stored choice, else what the system asks for. */
function initContrast() {
  const stored = storedContrast();
  if (stored === "high" || stored === "normal") {
    applyContrast(stored === "high");
    return;
  }
  let high = false;
  if (window.matchMedia) {
    try {
      high = window.matchMedia("(prefers-contrast: more)").matches;
    } catch (err) {
      high = false;
    }
  }
  applyContrast(high);
}

/**
 * Switch the interface to another locale.
 *
 * @param {string} code the locale code, e.g. "en" or "zh-CN"
 * @returns {boolean}  true when the locale was applied
 */
function setLocale(code) {
  const mod = optional.i18n;
  if (!mod || typeof mod.setLocale !== "function") {
    toast("Localisation is unavailable", "warn");
    return false;
  }
  if (!mod.setLocale(code)) return false;
  state.locale = typeof mod.getLocale === "function" ? mod.getLocale() : code;
  storeLocale(state.locale);
  applyLocale();
  return true;
}

/**
 * Fill the learned stage selector of the top bar with the models of the
 * library.
 *
 * The stage is an **offline** capability: the model runs over the finished
 * file and refines what the chain produced.  The first entry is "no stage",
 * which is what a render does when the user says nothing.
 */
async function buildLearnedSelector() {
  const select = dom.learnedSelect;
  if (!select) return;
  let models = [];
  try {
    const answer = await api.models();
    models = (answer && answer.models) || [];
  } catch (err) {
    console.warn("[auralis] the model library could not be read", err);
    models = [];
  }
  const previous = select.value;
  select.replaceChildren();
  select.append(
    el("option", { value: "", text: t("topbar.learned.none", "No learned stage") })
  );
  for (const model of models) {
    if (model.readable === false) continue;
    const variant = model.variant ? ` (${model.variant})` : "";
    select.append(el("option", { value: model.name || "", text: `${model.name || ""}${variant}` }));
  }
  if (previous && models.some((m) => m.name === previous)) select.value = previous;
}

/** The name of the learned stage the user chose, or "" for none. */
function learnedStageName() {
  const select = dom.learnedSelect;
  return select && select.value ? String(select.value) : "";
}

/** Fill the locale selector of the top bar with the shipped locales. */
function buildLocaleSelector() {
  const select = dom.localeSelect;
  if (!select) return;
  const mod = optional.i18n;
  const codes =
    mod && typeof mod.availableLocales === "function" ? mod.availableLocales() : ["en"];
  select.replaceChildren();
  for (const code of codes) {
    const name = mod && typeof mod.localeName === "function" ? mod.localeName(code) : code;
    select.append(el("option", { value: code, text: name }));
  }
  const active = mod && typeof mod.getLocale === "function" ? mod.getLocale() : "en";
  select.value = codes.indexOf(active) >= 0 ? active : codes[0];
}

/** Bring the selector in step with the active locale. */
function updateLocaleSelector() {
  const select = dom.localeSelect;
  if (!select) return;
  const mod = optional.i18n;
  const active = mod && typeof mod.getLocale === "function" ? mod.getLocale() : state.locale;
  if (select.value !== active) select.value = active;
}

/**
 * Apply the catalogue to the document and to the parts of the shell that are
 * built at runtime.
 */
function applyLocale() {
  const mod = optional.i18n;
  if (!mod) return;
  if (typeof mod.translateDocument === "function") {
    try {
      mod.translateDocument(document);
    } catch (err) {
      console.warn("[auralis] the document could not be translated", err);
    }
  }
  refreshLocaleText();
  updateLocaleSelector();
  renderChainStrip();
  renderInspector();
  /* the browser builds its own texts (the categories), so it has to be
     painted again in the language of the moment */
  if (browser && typeof browser.update === "function") {
    try {
      browser.update(state.modules);
    } catch (err) {
      console.warn("[auralis] the module browser could not be translated", err);
    }
  }
}

/** Re-apply the texts the document translation cannot know about. */
function refreshLocaleText() {
  if (dom.fileName) {
    dom.fileName.textContent = state.asset
      ? state.asset.name
      : t("topbar.noFile", "No file loaded");
    dom.fileName.classList.toggle("idle", !state.asset);
  }
  if (dom.fileMeta) {
    dom.fileMeta.textContent = state.asset
      ? `${state.asset.sample_rate} Hz · ${state.asset.channels} ch · ${state.asset.duration.toFixed(2)} s`
      : "";
  }
  if (presetTab) {
    presetTab.textContent = t("inspector.tab.presets", "Presets");
  }
  setSelectionReadout(state.selection);
  refreshCanvasSummary();
  if (dom.stLevel) dom.stLevel.textContent = state.levelText || t("status.level", "-inf dBFS");
  if (dom.stLatency) {
    dom.stLatency.textContent =
      state.latency === null || state.latency === undefined
        ? t("status.latency.unknown", "lat — smp")
        : t("status.latency", "lat {n} smp", {
            n: String(state.latency || 0),
          });
  }
  if (pathReport) pathReport.refresh();
  if (dom.stEngine) {
    dom.stEngine.textContent = t(`status.engine.${state.engine}`, state.engine);
  }
  if (dom.btnContrast) applyContrast(state.contrast === "high");
}

/**
 * Describe the two canvases in text.
 *
 * The pixels of a canvas cannot be read, so each canvas carries the
 * duration, the sample rate, the channel count and the peak level of
 * the signal it shows.
 */
function refreshCanvasSummary() {
  const asset = state.asset;
  /* the unit belongs to the text of the summary, not to the readout */
  const peak =
    String(state.levelText || "").replace(/\s*dBFS\s*$/i, "").trim() || "-inf";
  const describe = (view) =>
    t(
      "canvas.summary",
      "{name}, {duration} s, {rate} Hz, {channels} ch, peak {peak} dBFS",
      {
        name: view,
        duration: Number(asset ? asset.duration : 0).toFixed(2),
        rate: Number(asset ? asset.sample_rate : 0),
        channels: Number(asset ? asset.channels : 0),
        peak,
      }
    );
  if (dom.specCanvas) {
    dom.specCanvas.setAttribute(
      "aria-label",
      describe(t("topbar.viewMode.spectrogram", "Spectrogram"))
    );
  }
  if (dom.waveCanvas) {
    dom.waveCanvas.setAttribute(
      "aria-label",
      describe(t("topbar.viewMode.waveform", "Waveform"))
    );
  }
}

/**
 * Initialise the localisation.
 *
 * The locale is chosen in this order: the one the user chose before, the
 * language the browser asks for, the default.  The module owns the rule; the
 * shell only hands it the storage, the navigator and the document, so that
 * the `lang` attribute of the document follows the locale.
 */
function initLocale() {
  const mod = optional.i18n;
  if (!mod) return;
  let chosen = "";
  if (typeof mod.initLocale === "function") {
    try {
      chosen = mod.initLocale({
        storage: window.localStorage,
        navigator: window.navigator,
        root: document,
      });
    } catch (err) {
      console.warn("[auralis] the locale could not be selected", err);
    }
  }
  if (!chosen && typeof mod.setLocale === "function") {
    /* a module that does not know the rule: the stored choice, else English */
    try {
      mod.setLocale(storedLocale() || "en");
    } catch (err) {
      console.warn("[auralis] the locale could not be selected", err);
    }
  }
  state.locale = typeof mod.getLocale === "function" ? mod.getLocale() : state.locale;
  buildLocaleSelector();
  applyLocale();
}

/**
 * Unwrap a typed value of the interchange format.
 *
 * The server sends the parameters of a module as envelopes
 * (`{"Type": "Float", "Value": 3.0}`); the shell works with the plain
 * values.
 *
 * @param {*} node        the envelope, or a plain value
 * @param {*} [fallback]  the value of a broken envelope
 * @returns {*} the plain value
 */
function unwrapTypedValue(node, fallback = undefined) {
  if (node && typeof node === "object" && "Type" in node && "Value" in node) {
    if (node.Type === "Bool") return Boolean(node.Value);
    if (node.Type === "Int" || node.Type === "UInt") {
      const number = Number(node.Value);
      return Number.isFinite(number) ? Math.trunc(number) : fallback;
    }
    if (node.Type === "Float") {
      const number = Number(node.Value);
      return Number.isFinite(number) ? number : fallback;
    }
    if (node.Type === "Base64") {
      return node.Value === undefined || node.Value === null ? fallback : node.Value;
    }
    return node.Value;
  }
  if (node === null || node === undefined) return fallback;
  return node;
}

/**
 * The plain parameter values of a chain entry.
 *
 * The shell keeps the values it has sent in `values`; the server sends them
 * as envelopes in `params`.  Both are read, so that the entry always carries
 * the values the module holds.
 *
 * @param {object} entry the chain entry
 * @returns {object} the values, by parameter name
 */
function entryValues(entry) {
  const src = entry && typeof entry === "object" ? entry : {};
  const out = {};
  if (src.values && typeof src.values === "object") {
    for (const [name, value] of Object.entries(src.values)) {
      out[name] = value;
    }
  }
  if (src.params && typeof src.params === "object") {
    for (const [name, node] of Object.entries(src.params)) {
      if (out[name] === undefined) out[name] = unwrapTypedValue(node);
    }
  }
  return out;
}

/** Normalise a chain entry coming from the server. */
function normaliseEntry(entry, index) {
  const src = entry && typeof entry === "object" ? entry : {};
  const code = src.code || src.module_code || "";
  let bypass = false;
  if (typeof src.bypass === "boolean") bypass = src.bypass;
  else if (typeof src.bypassed === "boolean") bypass = src.bypassed;
  else if (typeof src.enabled === "boolean") bypass = !src.enabled;
  const info = moduleInfo(code) || {};
  return {
    index,
    code,
    display_name: src.display_name || src.name || info.display_name || code,
    category: src.category || info.category || "",
    edition: src.edition || info.edition || "standard",
    bypass,
    values: entryValues(src),
    learn: src.learn === true || info.learn === true,
  };
}

/** The chain array in the shape chain.js expects. */
function chainSteps() {
  return state.chain.map((entry, i) => {
    const e = normaliseEntry(entry, i);
    return {
      code: e.code,
      display_name: e.display_name,
      category: e.category,
      edition: e.edition,
      bypass: e.bypass,
    };
  });
}

/** Find the ModuleInfo record for a code. */
function moduleInfo(code) {
  if (!code) return null;
  return state.modules.find((m) => m.code === code) || null;
}

/** Human readable module name. */
function moduleName(code) {
  const info = moduleInfo(code);
  return (info && (info.display_name || info.name)) || code || "module";
}

/** Default parameter values of a module, used as the clean preset state. */
function defaultValues(code) {
  const info = moduleInfo(code);
  const out = {};
  for (const d of (info && info.params) || []) {
    if (d && d.name !== undefined) out[d.name] = d.default;
  }
  return out;
}

/** True when `values` differ from the saved preset values. */
function isDirtyValues(values, saved) {
  const keys = new Set([...Object.keys(values || {}), ...Object.keys(saved || {})]);
  for (const k of keys) {
    const a = values[k];
    const b = saved[k];
    if (typeof a === "number" && typeof b === "number") {
      if (Math.abs(a - b) > 1e-9 * Math.max(1, Math.abs(a))) return true;
    } else if (a !== b) {
      return true;
    }
  }
  return false;
}

/* --------------------------------------------------------------- toast */

function toast(message, kind = "info", ms = 2800) {
  if (!dom.toast) return;
  dom.toast.textContent = String(message);
  dom.toast.classList.remove("hidden", "err", "warn");
  if (kind === "err") dom.toast.classList.add("err");
  else if (kind === "warn") dom.toast.classList.add("warn");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => dom.toast.classList.add("hidden"), ms);
}

/* --------------------------------------------------------------- modal */

/* A callback that is run when the dialog is closed (the yes / no question). */
let modalOnClose = null;

function openModal({ title = "Dialog", body = null, foot = null } = {}) {
  if (!dom.modal) return;
  dom.modalTitle.textContent = title;
  dom.modalBody.replaceChildren();
  dom.modalFoot.replaceChildren();
  if (body) {
    const nodes = Array.isArray(body) ? body : [body];
    for (const node of nodes) if (node) dom.modalBody.append(node);
  }
  if (foot) {
    const nodes = Array.isArray(foot) ? foot : [foot];
    for (const node of nodes) if (node) dom.modalFoot.append(node);
  }
  dom.modal.classList.remove("hidden");
  const focusable = dom.modal.querySelector("button, a[href], input, select");
  if (focusable) focusable.focus();
}

function closeModal() {
  if (!dom.modal) return;
  dom.modal.classList.add("hidden");
  dom.modalBody.replaceChildren();
  dom.modalFoot.replaceChildren();
  if (modalOnClose) {
    const callback = modalOnClose;
    modalOnClose = null;
    try {
      callback();
    } catch (err) {
      console.warn("[auralis] the dialog callback failed", err);
    }
  }
}

/* --------------------------------------------------------- status bar */

function setEngine(status) {
  state.engine = status;
  if (!dom.stEngine) return;
  dom.stEngine.textContent = t(`status.engine.${status}`, status);
  dom.stEngine.classList.remove("ready", "busy", "error");
  if (status === "ready" || status === "idle") dom.stEngine.classList.add("ready");
  else if (status === "error") dom.stEngine.classList.add("error");
  else dom.stEngine.classList.add("busy");
}

function setCursorReadout(time, freq, visible) {
  if (visible === false) {
    if (dom.hoverReadout) dom.hoverReadout.classList.remove("show");
    return;
  }
  state.cursor.time = Number.isFinite(time) ? time : 0;
  state.cursor.freq = Number.isFinite(freq) ? freq : 0;
  if (dom.stCursor) {
    dom.stCursor.textContent = `${fmtTime(state.cursor.time)}  ${Math.round(state.cursor.freq)} Hz`;
  }
  if (dom.hoverReadout) {
    dom.hoverReadout.textContent = `${fmtTime(state.cursor.time)}  ${Math.round(state.cursor.freq)} Hz`;
    dom.hoverReadout.classList.add("show");
  }
}

function describeSelection(sel) {
  if (!sel) return t("status.noSelection", "no selection");
  const parts = [];
  const t0 = sel.t0;
  const t1 = sel.t1;
  const f0 = sel.f0;
  const f1 = sel.f1;
  if (t0 !== null && t1 !== null && t1 > t0) parts.push(`${fmtTime(t0)}–${fmtTime(t1)}`);
  else if (t0 !== null) parts.push(fmtTime(t0));
  if (f0 !== null && f1 !== null && f1 > f0) parts.push(`${Math.round(f0)}–${Math.round(f1)} Hz`);
  else if (f0 !== null) parts.push(`${Math.round(f0)} Hz`);
  return parts.length ? parts.join("  ·  ") : t("status.selection", "selection");
}

function setSelectionReadout(sel) {
  state.selection = sel || null;
  if (dom.stSel) dom.stSel.textContent = describeSelection(state.selection);
  scheduleAnalysis();
}

function setZoomLabel(zoom) {
  const z = Number(zoom);
  if (!Number.isFinite(z)) return;
  if (Math.abs(z - state.zoom) < 1e-9) return;
  state.zoom = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z));
  if (dom.zoomLabel) dom.zoomLabel.textContent = `${state.zoom.toFixed(1)}\u00d7`;
}

function setZoom(zoom) {
  const z = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Number(zoom) || ZOOM_MIN));
  state.zoom = z;
  if (dom.zoomLabel) dom.zoomLabel.textContent = `${state.zoom.toFixed(1)}\u00d7`;
  if (view && typeof view.setZoom === "function") {
    view.setZoom(z);
    state.zoom = Number(view.zoom) || z;
    if (dom.zoomLabel) dom.zoomLabel.textContent = `${state.zoom.toFixed(1)}\u00d7`;
  }
}

function setScroll(seconds) {
  const s = Number(seconds) || 0;
  state.scroll = s;
  if (view && typeof view.setScroll === "function") view.setScroll(s);
}

/* ------------------------------------------------- the path of the preview */

/** The report of the path (js/path_report.js): the chip and the disclosure. */
let pathReport = null;

/**
 * Build the report of the path of the preview.
 *
 * The chip of the status bar says which path plays and the disclosure that
 * follows it says what the path changed.  Both are filled from the report of
 * the server: the shell computes nothing about the path.
 */
function initPathReport() {
  const mod = optional.path;
  if (!mod || typeof mod.createPathReport !== "function") return;
  if (!dom.stPath || !dom.pathPanel) return;
  pathReport = mod.createPathReport({
    chip: dom.stPath,
    panel: dom.pathPanel,
    nameOf: (code) => moduleName(code),
    onProfile: (profile) => requestPreviewProfile(profile),
  });
  pathReport.setRequested(state.previewProfile);
  if (live.path) pathReport.update(live.path);
}

/**
 * Take the path of a message of the server.
 *
 * The path, the report of the profile and the latency are the server's; the
 * status bar shows them and the browser computes nothing.
 *
 * @param {object} payload the message
 * @returns {boolean} true when the message carried a path
 */
function applyServerPath(payload) {
  if (!payload || typeof payload !== "object") return false;
  live.path = payload;
  if (payload.latency_samples !== undefined && payload.latency_samples !== null) {
    setPreviewLatency(payload.latency_samples);
  }
  if (pathReport) return pathReport.update(payload);
  return false;
}

/**
 * Put the latency of the status bar in step with the path that plays.
 *
 * The number is the one the server reported for that path.  A path the server
 * did not report a latency for shows nothing: a stale number of another path
 * would be a lie.
 *
 * @param {number|null} samples the latency in samples, or null
 */
function setPreviewLatency(samples) {
  const value = samples === null || samples === undefined ? null : Number(samples);
  state.latency = value !== null && Number.isFinite(value) ? value : null;
  if (!dom.stLatency) return;
  dom.stLatency.textContent =
    state.latency === null
      ? t("status.latency.unknown", "lat — smp")
      : t("status.latency", "lat {n} smp", { n: String(state.latency) });
}

/**
 * Ask the server for another path in the preview.
 *
 * The stream starts again, so every number of the status bar belongs to the
 * path that plays: a message of a running preview carries the path but not
 * the latency, and the latency of the other path would be a lie.
 *
 * @param {string} profile the profile the preview shall use
 * @returns {boolean} true when the request was made
 */
function requestPreviewProfile(profile) {
  const name = String(profile || "").trim().toLowerCase();
  if (!name) return false;
  state.previewProfile = name;
  if (pathReport) pathReport.setRequested(name);
  if (live.state === "live" || live.state === "connecting") {
    liveLog(`live preview: the path is now "${name}" — the stream starts again`);
    const running = startLivePreview();
    if (running && typeof running.catch === "function") {
      running.catch((err) => {
        console.warn("[auralis] the preview could not start again", err);
      });
    }
    return true;
  }
  liveLog(`live preview: the next preview asks for the "${name}" path`);
  return true;
}

/**
 * Switch the preview between the fast path and the full path.
 */
function togglePreviewPath() {
  requestPreviewProfile(state.previewProfile === "full" ? "fast" : "full");
}

/* --------------------------------------------------------- browser tab */

function renderBrowserList() {
  const onAdd = (code) => addModule(code);
  const options = {
    modules: state.modules,
    onAdd,
    searchQuery: dom.moduleSearch ? dom.moduleSearch.value : "",
    searchInput: dom.moduleSearch || undefined,
  };
  if (typeof renderBrowser !== "function") {
    browser = null;
    fallbackBrowser();
    toast("The module browser is unavailable", "warn");
    return;
  }
  try {
    browser = renderBrowser(dom.moduleTree, options, { onAdd });
  } catch (err) {
    console.warn("[auralis] renderBrowser failed", err);
    browser = null;
    fallbackBrowser();
    toast(`Module browser unavailable — ${err.message}`, "err");
  }
}

/** Minimal browser used only if browser.js cannot render. */
function fallbackBrowser() {
  const groups = new Map();
  for (const m of state.modules) {
    const cat = m.category || "Modules";
    if (!groups.has(cat)) groups.set(cat, []);
    groups.get(cat).push(m);
  }
  const frag = document.createDocumentFragment();
  for (const [cat, mods] of groups) {
    const head = el(
      "div",
      { class: "cat-head" },
      cat,
      el("span", { class: "cat-count" }, String(mods.length))
    );
    const list = el("div", { class: "cat-body" });
    for (const m of mods) {
      const row = el(
        "button",
        { class: "mod", type: "button", onclick: () => addModule(m.code) },
        el("span", { class: "name" }, m.display_name || m.name || m.code)
      );
      if (m.edition === "advanced") row.append(el("span", { class: "adv" }, "ADV"));
      list.append(row);
    }
    frag.append(head, list);
  }
  dom.moduleTree.replaceChildren(frag);
}

function filterBrowser(query) {
  const q = query || "";
  state.searchQuery = q;
  if (browser && typeof browser.setQuery === "function") {
    try {
      browser.setQuery(q);
      return;
    } catch (err) {
      console.warn("[auralis] setQuery failed", err);
    }
  }
  const rows = dom.moduleTree.querySelectorAll(".mb-row, .mod");
  for (const row of rows) {
    const text = (row.textContent || "").toLowerCase();
    row.style.display = !q || text.includes(q.toLowerCase()) ? "" : "none";
  }
}

/* --------------------------------------------------------- chain strip */

function ensureChainStrip() {
  if (chainStrip && chainStrip.isConnected) return chainStrip;
  chainStrip = el("div", { id: "chain-strip", class: "chain-strip" });
  dom.inspector.append(chainStrip);
  return chainStrip;
}

function chainHandlers() {
  const handlers = {
    onSelect: (index) => selectModule(index),
    onBypass: (index, bypass) => toggleBypass(index, bypass),
    onReorder: (from, to) => reorderModule(from, to),
    onRemove: (index) => removeModule(index),
  };
  // A callable is accepted as well, so the bag works with both call shapes.
  return Object.assign((index) => selectModule(index), handlers);
}

function renderChainStrip() {
  const container = ensureChainStrip();
  const steps = chainSteps();
  if (typeof renderChain !== "function") {
    chainView = null;
    fallbackChainStrip(container, steps);
    highlightChain();
    return;
  }
  try {
    if (chainView && typeof chainView.update === "function") {
      chainView.update(steps);
    } else {
      chainView = renderChain(container, steps, chainHandlers());
    }
  } catch (err) {
    console.warn("[auralis] renderChain failed", err);
    chainView = null;
    fallbackChainStrip(container, steps);
  }
  highlightChain();
}

/** Minimal chain strip used only if chain.js cannot render. */
function fallbackChainStrip(container, steps) {
  if (!steps.length) {
    container.replaceChildren(el("span", { class: "empty" }, "Chain is empty"));
    return;
  }
  const frag = document.createDocumentFragment();
  steps.forEach((e, index) => {
    const chip = el(
      "div",
      { class: "chip", "data-index": String(index) },
      el("span", { class: "idx" }, String(index + 1)),
      el("span", { class: "name" }, e.display_name),
      el(
        "button",
        {
          class: "power" + (e.bypass ? "" : " on"),
          type: "button",
          title: e.bypass ? "Enable" : "Bypass",
          onclick: (ev) => {
            ev.stopPropagation();
            toggleBypass(index, !e.bypass);
          },
        },
        "P"
      )
    );
    chip.addEventListener("click", () => selectModule(index));
    frag.append(chip);
  });
  container.replaceChildren(frag);
}

function highlightChain() {
  if (chainView && typeof chainView.select === "function") {
    try {
      if (state.selectedIndex >= 0) chainView.select(state.selectedIndex);
      return;
    } catch (err) {
      console.warn("[auralis] chain.select failed", err);
    }
  }
  if (!chainStrip) return;
  for (const chip of chainStrip.querySelectorAll("[data-index]")) {
    chip.classList.toggle("sel", Number(chip.dataset.index) === state.selectedIndex);
  }
}

/* --------------------------------------------------------- inspector */

function setTab(tab) {
  state.tab = tab;
  for (const btn of dom.inspectorTabs.querySelectorAll("button")) {
    btn.classList.toggle("on", btn.dataset.tab === tab);
  }
  renderInspector();
}

function renderInspector() {
  const token = ++inspectorToken;
  const body = dom.inspectorBody;
  if (!body) return Promise.resolve();

  if (state.tab === "panel") {
    return renderModulePanel(body, token);
  }
  panelView = null;
  if (state.tab === "chain") {
    body.replaceChildren(renderChainDetail());
    return Promise.resolve();
  }
  if (state.tab === "analysis") {
    return renderAnalysisTab(body, token);
  }
  if (state.tab === "presets") {
    return renderPresetTab(body);
  }
  if (state.tab === "batch") {
    /* The batch panel keeps its file list: a chain edit only refreshes the
       chain summary instead of rebuilding the whole tab. */
    if (batchPanel && batchHost) {
      body.replaceChildren(batchHost);
      refreshBatchChain();
      return Promise.resolve();
    }
    return renderBatchTab(body, token);
  }
  body.replaceChildren(renderAssetFacts());
  return Promise.resolve();
}

async function renderModulePanel(body, token) {
  const entry = state.chain[state.selectedIndex];
  if (!entry) {
    panelView = null;
    body.replaceChildren(
      el(
        "p",
        { class: "empty" },
        "No module selected. Add one from the browser, or click a chip in the chain strip."
      )
    );
    return;
  }
  const norm = normaliseEntry(entry, state.selectedIndex);
  const info = moduleInfo(norm.code) || {};

  let descs = Array.isArray(info.params) ? info.params.slice() : [];
  let values = { ...norm.values };
  try {
    const res = await api.params(state.selectedIndex);
    if (token !== inspectorToken) return; // a newer render won
    if (Array.isArray(res && res.params) && res.params.length) descs = res.params;
    if (res && res.values && typeof res.values === "object") values = { ...res.values };
  } catch (err) {
    if (token !== inspectorToken) return;
    console.warn("[auralis] params unavailable", err);
    toast(`Parameter list unavailable — ${err.message}`, "warn");
  }
  if (token !== inspectorToken) return;

  if (!savedValues.has(norm.code)) savedValues.set(norm.code, defaultValues(norm.code));
  const saved = savedValues.get(norm.code);
  state.dirty = isDirtyValues(values, saved);

  const options = {
    moduleInfo: {
      code: norm.code,
      display_name: info.display_name || norm.display_name,
      element: info.element || "",
      category: info.category || norm.category,
      edition: info.edition || norm.edition,
      learn: info.learn === true || norm.learn === true,
      analysis: info.analysis === true,
      params: descs,
    },
    values,
    presetName: state.presetName || "Default",
    savedValues: saved,
    bypassed: norm.bypass,
    dirty: state.dirty,
  };

  body.replaceChildren();
  if (typeof renderPanel !== "function") {
    panelView = null;
    fallbackPanel(body, options);
    toast("The module panel is unavailable", "warn");
    return;
  }
  try {
    panelView = renderPanel(body, options, panelHandlers());
  } catch (err) {
    console.warn("[auralis] renderPanel failed", err);
    panelView = null;
    fallbackPanel(body, options);
    toast(`Module panel unavailable — ${err.message}`, "err");
    return;
  }
  attachPresetMenu(panelView);
}

function panelHandlers() {
  const handlers = {
    onParam: (name, value) => onParam(name, value),
    onBypass: (bypassed) => toggleBypass(state.selectedIndex, bypassed),
    onLearn: () => learnSelected(),
    onCompare: () => compareChains(),
    onRemove: () => removeModule(state.selectedIndex),
    onPreset: (action) => onPanelAction(action),
    /* The "..." button and the preset chip open the preset menu of the
       library (js/preset_manager.js).  Without that module the panel keeps
       its own menu. */
    onMenu: () => openPresetMenu(),
  };
  return Object.assign((name, value) => onParam(name, value), handlers);
}

/** Minimal panel used only if panel.js cannot render. */
function fallbackPanel(body, model) {
  const head = el(
    "div",
    { class: "panel-head" },
    el("span", { class: "mod-name" }, model.moduleInfo.display_name || model.moduleInfo.code),
    el("span", { class: "spacer" }),
    el("span", { class: "fb-preset" }, model.presetName)
  );
  body.append(head);

  const grid = el("div", { class: "ctrl-grid" });
  for (const desc of model.moduleInfo.params || []) {
    const value = model.values ? model.values[desc.name] : undefined;
    const label = el("span", { class: "ctrl-label" }, desc.name);
    if (desc.type === "bool") {
      const input = el("input", { type: "checkbox" });
      input.checked = Boolean(value);
      input.addEventListener("change", () => onParam(desc.name, input.checked));
      grid.append(el("div", { class: "ctrl inline" }, label, input));
    } else if (desc.type === "enum") {
      const sel = el("select");
      for (const choice of desc.choices || []) {
        const opt = el("option", { value: choice }, choice);
        if (choice === value) opt.selected = true;
        sel.append(opt);
      }
      sel.addEventListener("change", () => onParam(desc.name, sel.value));
      grid.append(el("div", { class: "ctrl inline" }, label, sel));
    } else {
      const min = Number(desc.minimum ?? 0);
      const max = Number(desc.maximum ?? 1);
      const range = el("input", { type: "range", min, max, step: (max - min) / 200 || 0.01 });
      range.value = Number(value ?? desc.default ?? min);
      const out = el("span", { class: "ctrl-value" }, String(range.value));
      range.addEventListener("input", () => {
        out.textContent = range.value;
        onParam(desc.name, Number(range.value));
      });
      grid.append(
        el("div", { class: "ctrl slider" }, el("span", { class: "ctrl-label" }, desc.name), range, out)
      );
    }
  }
  body.append(grid);
}

/** Chain tab: a detailed, vertical list of the chain. */
function renderChainDetail() {
  const wrap = el("div", { class: "insp-section" });
  if (!state.chain.length) {
    wrap.append(el("p", { class: "empty" }, t("inspector.chainEmpty", "The chain is empty.")));
    return wrap;
  }
  state.chain.forEach((raw, index) => {
    const e = normaliseEntry(raw, index);
    const row = el(
      "div",
      { class: "kv" },
      el("span", {}, `${index + 1}. ${e.display_name}`),
      el("span", {}, e.bypass ? t("inspector.bypassed", "bypassed") : e.code)
    );
    row.style.cursor = "pointer";
    row.addEventListener("click", () => selectModule(index));
    wrap.append(row);
  });
  const clear = el(
    "button",
    { class: "tbtn tbtn-sm", type: "button" },
    t("inspector.clearChain", "Clear chain")
  );
  clear.addEventListener("click", () => clearChain());
  wrap.append(el("div", { class: "insp-section" }, clear));
  return wrap;
}

/* ------------------------------------------------------ the analysis tab */

/** The time range the measurement is taken over. */
function analysisRange() {
  const sel = state.selection;
  if (sel && sel.t0 !== null && sel.t1 !== null && sel.t1 > sel.t0) {
    return { start: sel.t0, end: sel.t1 };
  }
  const duration = state.asset ? Number(state.asset.duration) || 0 : 0;
  return { start: 0, end: duration };
}

/**
 * Ask the server for a measurement of the loaded audio.
 *
 * @param {string} assetId the asset to measure
 * @param {number} start    the first second of the range
 * @param {number} end      the last second of the range
 * @returns {Promise<object>} the measurement payload
 */
async function fetchMeasurement(assetId, start, end) {
  const response = await fetch("/api/analysis", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ asset_id: assetId, start, end }),
  });
  const text = await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch (err) {
      data = null;
    }
  }
  if (!response.ok) {
    const message = (data && (data.error || data.detail)) || `${response.status} ${response.statusText}`;
    throw new Error(String(message));
  }
  return data && typeof data === "object" ? data : {};
}

/**
 * The Analysis tab: the facts about the asset and the measurement of the
 * material.
 *
 * The measurement panel comes from js/analysis.js; when that module is
 * missing the facts of the asset are shown on their own.
 */
async function renderAnalysisTab(body, token) {
  body.replaceChildren();
  body.append(renderAssetFacts());

  const host = el("div", { class: "insp-section" });
  body.append(host);

  const mod = optional.analysis;
  if (!mod || typeof mod.renderAnalysis !== "function") {
    host.append(
      el("p", { class: "empty" }, t("inspector.analysisEmpty", "No analysis yet. Open an audio file to populate this tab."))
    );
    return;
  }

  if (!state.asset) {
    try {
      mod.renderAnalysis(host, mod.emptyAnalysis(), {});
    } catch (err) {
      console.warn("[auralis] the empty measurement could not be rendered", err);
    }
    return;
  }

  const range = analysisRange();
  try {
    const payload = await fetchMeasurement(state.asset.asset_id, range.start, range.end);
    if (token !== inspectorToken) return;
    mod.renderAnalysis(host, payload, { sampleRate: state.asset.sample_rate });
  } catch (err) {
    if (token !== inspectorToken) return;
    console.warn("[auralis] the measurement service is unavailable", err);
    host.append(el("p", { class: "empty" }, `Measurement unavailable — ${err.message}`));
  }
}

/** Measure the audio again once the selection has settled. */
function scheduleAnalysis() {
  if (analysisTimer) window.clearTimeout(analysisTimer);
  analysisTimer = 0;
  if (state.tab !== "analysis") return;
  analysisTimer = window.setTimeout(() => {
    analysisTimer = 0;
    renderInspector();
  }, ANALYSIS_DEBOUNCE_MS);
}

/* ---------------------------------------------------------- the batch tab */

/**
 * The delivery catalogue of the server.
 *
 * @returns {Promise<Array<object>>} the targets; empty when the catalogue
 *          cannot be read
 */
async function fetchDeliveryTargets() {
  try {
    const response = await fetch("/api/delivery-targets", {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    const data = await response.json();
    const list = data && (data.targets || data.presets || data.delivery_targets);
    return Array.isArray(list) ? list : [];
  } catch (err) {
    console.warn("[auralis] the delivery catalogue is unavailable", err);
    return [];
  }
}

/**
 * The files the batch starts with: the audio the server already holds.
 *
 * The server hands out the path the batch runner reads every file from; when
 * that route is not there, the asset that is loaded is used with its
 * conventional path.
 *
 * @returns {Promise<Array<object>>} the files of the batch
 */
async function fetchBatchFiles() {
  try {
    const response = await fetch("/api/assets", { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    const data = await response.json();
    const list = data && Array.isArray(data.assets) ? data.assets : [];
    const files = [];
    for (const asset of list) {
      if (!asset || !asset.path) continue;
      files.push({
        name: asset.name || asset.asset_id,
        path: asset.path,
        asset_id: asset.asset_id,
        current: asset.current === true,
      });
    }
    if (files.length) {
      files.sort((a, b) => Number(Boolean(b.current)) - Number(Boolean(a.current)));
      return files;
    }
  } catch (err) {
    console.warn("[auralis] the asset list is unavailable", err);
  }
  if (!state.asset || !state.asset.asset_id) return [];
  return [
    {
      name: state.asset.name || state.asset.asset_id,
      path: `var/assets/audio/${state.asset.asset_id}.wav`,
      asset_id: state.asset.asset_id,
    },
  ];
}

/** Drop the batch panel of the inspector, if there is one. */
function destroyBatchPanel() {
  if (!batchPanel) return;
  try {
    if (typeof batchPanel.destroy === "function") batchPanel.destroy();
  } catch (err) {
    console.warn("[auralis] the batch panel could not be closed", err);
  }
  batchPanel = null;
  batchHost = null;
}

/**
 * The Batch tab: the unattended processor.
 *
 * The panel comes from js/batch.js; the delivery catalogue is read from the
 * server and handed over as the list of presets.
 */
async function renderBatchTab(body, token) {
  body.replaceChildren();
  const host = el("div", { class: "insp-section" });
  body.append(host);

  const mod = optional.batch;
  if (!mod || typeof mod.renderBatch !== "function") {
    host.append(el("p", { class: "empty" }, "Batch processing is unavailable."));
    return;
  }

  destroyBatchPanel();
  const presets = await fetchDeliveryTargets();
  if (token !== inspectorToken) return;
  const files = await fetchBatchFiles();
  if (token !== inspectorToken) return;

  batchHost = host;

  try {
    batchPanel = mod.renderBatch(host, {
      files,
      chain: chainSteps(),
      presets,
      onStart: (payload) => {
        const count = payload && Array.isArray(payload.paths) ? payload.paths.length : 0;
        toast(`Batch started — ${count} file(s)`, "info");
        return true;
      },
      onStop: (jobId) => {
        toast(`Batch ${jobId || ""} stopped`, "warn");
      },
    });
  } catch (err) {
    console.warn("[auralis] renderBatch failed", err);
    batchPanel = null;
    host.append(el("p", { class: "empty" }, `Batch panel unavailable — ${err.message}`));
  }
}

/** Keep the chain summary of the batch panel in step with the chain. */
function refreshBatchChain() {
  if (!batchPanel) return;
  try {
    if (typeof batchPanel.update === "function") {
      batchPanel.update(undefined, { chain: chainSteps() });
    }
  } catch (err) {
    console.warn("[auralis] the batch panel could not be updated", err);
  }
}

/* ----------------------------------------------------- the spectral editor */

/** The chain index of the spectral editor module, or -1. */
function spectralIndex() {
  for (let i = 0; i < state.chain.length; i++) {
    if (normaliseEntry(state.chain[i], i).code === SPECTRAL_EDITOR_CODE) return i;
  }
  return -1;
}

/**
 * Tell the overlay which module of the chain it drives.
 *
 * The index changes when a module is added or removed; the overlay sends the
 * region to that module.
 */
function updateSpectralIndex() {
  if (!spectralEditor || typeof spectralEditor.setModuleIndex !== "function") return;
  try {
    spectralEditor.setModuleIndex(spectralIndex());
  } catch (err) {
    console.warn("[auralis] the spectral editor index could not be set", err);
  }
}

/** Turn the spectral editor overlay on. */
function enableSpectralEditor() {
  if (spectralEditor) return;
  const mod = optional.spectral;
  if (!mod || typeof mod.createSpectralEditor !== "function") {
    toast("The spectral editor is unavailable", "err");
    state.spectral = false;
    updateSpectralButton();
    return;
  }
  try {
    spectralEditor = mod.createSpectralEditor({
      container: dom.canvasWrap,
      moduleIndex: spectralIndex(),
      onChange: (payload) => onSpectralChange(payload),
      onPreview: (payload) => onSpectralPreview(payload),
    });
  } catch (err) {
    console.warn("[auralis] the spectral editor could not be created", err);
    spectralEditor = null;
    toast(`Spectral editor unavailable — ${err.message}`, "err");
    state.spectral = false;
    updateSpectralButton();
    return;
  }
  state.spectral = true;
  updateSpectralButton();
  /* the index of the module may have changed while the overlay was off */
  updateSpectralIndex();
  if (state.asset) {
    try {
      spectralEditor.setData({
        duration: state.asset.duration,
        sample_rate: state.asset.sample_rate,
      });
    } catch (err) {
      /* the overlay works without the asset information */
    }
  }
  renderSpectralEditor();
}

/** Turn the spectral editor overlay off. */
function disableSpectralEditor() {
  if (spectralEditor) {
    try {
      spectralEditor.destroy();
    } catch (err) {
      console.warn("[auralis] the spectral editor could not be closed", err);
    }
    spectralEditor = null;
  }
  if (spectralTimer) {
    window.clearTimeout(spectralTimer);
    spectralTimer = 0;
  }
  state.spectral = false;
  updateSpectralButton();
  setSelectionReadout(state.selection);
}

/** The toggle of the view toolbar. */
function toggleSpectralEditor() {
  if (state.spectral) {
    disableSpectralEditor();
    return;
  }
  enableSpectralEditor();
}

/** Bring the toggle of the view toolbar in step with the state. */
function updateSpectralButton() {
  const btn = dom.btnSpectral;
  if (!btn) return;
  btn.classList.toggle("on", !!state.spectral);
  btn.setAttribute("aria-pressed", state.spectral ? "true" : "false");
}

/** The editor changed: remember the region and push the parameters. */
function onSpectralChange(payload) {
  const p = payload && typeof payload === "object" ? payload : {};
  spectralState = {
    region: p.region || null,
    mode: p.mode || spectralState.mode,
    strength: Number.isFinite(Number(p.strength)) ? Number(p.strength) : spectralState.strength,
  };
  scheduleSpectralPush();
}

/** The editor moves: the readout of the status bar follows the region. */
function onSpectralPreview(payload) {
  const label = payload && payload.label ? String(payload.label) : "";
  if (label && dom.stSel) dom.stSel.textContent = label;
}

/** Push the parameters of the editor to the chain, but not too often. */
function scheduleSpectralPush() {
  if (spectralTimer) window.clearTimeout(spectralTimer);
  spectralTimer = window.setTimeout(() => {
    spectralTimer = 0;
    pushSpectralParams();
  }, 120);
}

/**
 * Send the settings of the overlay to the spectral editor module.
 *
 * The region rectangle itself is the state of the module, not a parameter,
 * so only the parameters the module owns are sent — through the ordinary
 * chain parameter route.
 */
async function pushSpectralParams() {
  const index = spectralIndex();
  /* the editor sends the region to that module, so it has to know which one */
  updateSpectralIndex();
  if (index < 0) {
    if (dom.stSel) {
      dom.stSel.textContent = "add the Spectral Editor module to the chain";
    }
    return;
  }
  const region = spectralState.region;
  const values = {
    Mode: spectralState.mode,
    Strength: Number(spectralState.strength) || 0,
  };
  if (region) {
    values["Region Length"] = Math.max(0.02, Math.min(10, region.end - region.start));
  }
  /* Another module took the place of the editor: everything is sent again. */
  if (spectralPushedIndex !== index) {
    spectralPushedIndex = index;
    for (const key of Object.keys(spectralPushed)) delete spectralPushed[key];
  }
  for (const [name, value] of Object.entries(values)) {
    if (spectralPushed[name] === value) continue;
    try {
      await api.setParam(index, name, value);
      spectralPushed[name] = value;
    } catch (err) {
      console.warn(`[auralis] the parameter ${name} could not be applied`, err);
    }
  }
}

/** Keep the overlay aligned with the spectrogram of the composite view. */
function renderSpectralEditor() {
  if (!spectralEditor) return;
  const mod = optional.spectral;
  try {
    if (mod && typeof mod.renderEditor === "function") {
      mod.renderEditor(view, spectralState);
    } else if (typeof spectralEditor.render === "function") {
      spectralEditor.render(view, spectralState);
    }
  } catch (err) {
    console.warn("[auralis] the spectral editor could not be drawn", err);
  }
}

/** Asset tab: read-only facts about the loaded asset. */
function renderAssetFacts() {
  const wrap = el("div", { class: "insp-section" });
  const rows = [];
  if (state.asset) {
    rows.push(["File", state.asset.name || state.asset.asset_id]);
    rows.push(["Sample rate", `${state.asset.sample_rate} Hz`]);
    rows.push(["Channels", String(state.asset.channels)]);
    rows.push(["Frames", String(state.asset.frames)]);
    rows.push(["Duration", `${Number(state.asset.duration || 0).toFixed(2)} s`]);
  }
  if (state.analysis) {
    rows.push(["Frequency bins", String(state.analysis.freq_bins)]);
    rows.push(["Spectrogram frames", String(state.analysis.frames)]);
    rows.push(["Level range", `${state.analysis.max_db} .. ${state.analysis.min_db} dB`]);
  }
  if (!rows.length) {
    wrap.append(
      el("p", { class: "empty" }, t("inspector.analysisEmpty", "No analysis yet. Open an audio file to populate this tab."))
    );
    return wrap;
  }
  wrap.append(el("div", { class: "insp-title" }, t("inspector.asset", "Asset")));
  for (const [k, v] of rows) {
    wrap.append(el("div", { class: "kv" }, el("span", {}, k), el("span", {}, v)));
  }
  return wrap;
}

/* --------------------------------------------------------- chain edits */

/* ---------------------------------------------------------- the session */

/**
 * True when the server client is there.
 *
 * Every call to the server goes through it, so that a missing api.js costs
 * the features of the server and not the workbench itself.
 *
 * @returns {boolean}
 */
function hasApi() {
  return !!(api && typeof api.chain === "function");
}

/**
 * Read a JSON answer of the server.
 *
 * @param {Response} response the answer
 * @returns {Promise<object|null>} the answer, or null when it is not JSON
 */
async function readJson(response) {
  let text = "";
  try {
    text = await response.text();
  } catch (err) {
    return null;
  }
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch (err) {
    return null;
  }
}

/**
 * Send a JSON body to the server.
 *
 * @param {string} path the route
 * @param {object} body the body
 * @returns {Promise<object>} the answer
 */
async function postJson(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const data = await readJson(response);
  if (!response.ok) {
    const message = (data && (data.error || data.detail)) || `${response.status} ${response.statusText}`;
    throw new Error(String(message));
  }
  return data || {};
}

/**
 * Read a JSON answer from the server.
 *
 * @param {string} path the route
 * @returns {Promise<object>} the answer
 */
async function getJson(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  const data = await readJson(response);
  if (!response.ok) {
    const message = (data && (data.error || data.detail)) || `${response.status} ${response.statusText}`;
    throw new Error(String(message));
  }
  return data || {};
}

/**
 * The transport of the session document.
 *
 * The server owns the session files (POST /api/session/save, POST
 * /api/session/load, GET /api/session/list), so the document travels over
 * those routes.  When the server does not offer them the controller of the
 * session falls back to the local store of the browser.
 *
 * @returns {object} the transport
 */
function sessionTransport() {
  return {
    name: "server",
    async save(document, options = {}) {
      const name = String((options && options.name) || sessionName());
      const res = await postJson("/api/session/save", {
        name,
        audio: (document && document.audio) || {},
        view: (document && document.view) || {},
        markers: (document && document.markers) || [],
        history: (document && document.history) || {},
      });
      toast(`Session saved — ${(res && res.path) || name}`, "info", 3600);
      return res || { path: name };
    },
    async load(id) {
      /* The server restores the chain itself and answers with the document,
         the summary of the chain and the path of the file. */
      return await postJson("/api/session/load", { path: String(id || "") });
    },
    async list() {
      const res = await getJson("/api/session/list");
      const entries = Array.isArray(res && res.sessions) ? res.sessions : [];
      /* The newest session first: "open" without a name opens the session
         that was written last. */
      return entries.slice().sort((a, b) => {
        const left = String((a && a.saved) || "");
        const right = String((b && b.saved) || "");
        if (left === right) return 0;
        return left < right ? 1 : -1;
      });
    },
  };
}

/**
 * The name of the session file: the name of the audio, or "session".
 *
 * @returns {string}
 */
function sessionName() {
  const name = state.asset && state.asset.name ? String(state.asset.name) : "";
  const base = name
    .replace(/\.[^.]+$/, "")
    .replace(/[^\w -]+/g, " ")
    .trim();
  return base || "session";
}

/**
 * The state of the view, as the session document carries it.
 *
 * @returns {object} the view block
 */
function sessionViewState() {
  return {
    zoom: state.zoom,
    scroll: state.scroll,
    frequencyRange: state.frequencyRange,
    selection: state.selection,
    viewMode: state.viewMode,
    spectrogram: state.viewMode !== "waveform",
  };
}

/**
 * The audio the session works on.
 *
 * @returns {object} the audio block
 */
function sessionAudio() {
  const asset = state.asset;
  if (!asset) return {};
  return {
    path: `var/assets/audio/${asset.asset_id}.wav`,
    name: asset.name,
    asset_id: asset.asset_id,
    sample_rate: asset.sample_rate,
    channels: asset.channels,
    frames: asset.frames,
  };
}

/**
 * Create the session controller and its toolbar.
 *
 * The controller owns the undo / redo history: every chain edit of the shell
 * goes through it, so that it can be undone.  Without the module the shell
 * keeps working — the chain is then simply not undoable.
 *
 * @returns {boolean} true when the controller was created
 */
function initSession() {
  const mod = optional.session;
  if (!mod || typeof mod.createSessionController !== "function") {
    console.warn("[auralis] the session controller is unavailable — the chain cannot be undone");
    return false;
  }
  if (!hasApi()) return false;
  try {
    sessionController = mod.createSessionController({
      api,
      /* The mirror of the chain: the controller reads the parameters of a
         module out of it, so that an undo restores the right value. */
      engine: { chainSummary: () => state.chain },
      onChange: (snapshot) => onSessionChange(snapshot),
      onError: (err) => toast(`Session — ${err.message}`, "err"),
      onRestore: (restored) => onSessionRestore(restored),
      viewState: () => sessionViewState(),
      audio: () => sessionAudio(),
      sessions: sessionTransport(),
    });
  } catch (err) {
    console.warn("[auralis] the session controller could not be created", err);
    sessionController = null;
    return false;
  }
  wrapSessionActions();
  mountSessionBar(mod);
  return true;
}

/**
 * A session document was restored.
 *
 * The controller hands the view of that document over; the shell shows it.
 *
 * @param {object} restored the view, the markers and the chain
 */
function onSessionRestore(restored) {
  if (!restored) return;
  const restoredView = restored.view;
  if (restoredView && typeof restoredView === "object") {
    if (Number.isFinite(Number(restoredView.zoom))) setZoom(Number(restoredView.zoom));
    if (Number.isFinite(Number(restoredView.scroll))) setScroll(Number(restoredView.scroll));
    const range = Array.isArray(restoredView.frequency_range)
      ? restoredView.frequency_range
      : null;
    if (range && range.length >= 2) {
      state.frequencyRange = [Number(range[0]) || 0, Number(range[1]) || 0];
      if (dom.freqLo) dom.freqLo.value = String(state.frequencyRange[0]);
      if (dom.freqHi) dom.freqHi.value = String(state.frequencyRange[1]);
      if (view && typeof view.setFrequencyRange === "function") {
        view.setFrequencyRange(state.frequencyRange[0], state.frequencyRange[1]);
      }
    }
    if (restoredView.selection !== undefined) {
      setSelectionReadout(restoredView.selection || null);
    }
  }
  syncChain({ repaint: true });
}

/**
 * The toolbar of the session: undo, redo, save, open and the dirty marker.
 *
 * @param {object} mod the session module
 */
function mountSessionBar(mod) {
  const host = dom.topbarRight || dom.topbar;
  if (!host) return;
  sessionHost = el("div", { class: "session-host" });
  host.insertBefore(sessionHost, host.firstChild);
  try {
    sessionBar = mod.renderSessionBar(sessionHost, sessionController);
  } catch (err) {
    console.warn("[auralis] the session toolbar could not be rendered", err);
    sessionBar = null;
    if (sessionHost.parentNode) sessionHost.parentNode.removeChild(sessionHost);
    sessionHost = null;
  }
}

/**
 * The controller told the shell that something changed.
 *
 * @param {object} snapshot the state of the session
 */
function onSessionChange(snapshot) {
  if (sessionBar && typeof sessionBar.update === "function") {
    try {
      sessionBar.update();
    } catch (err) {
      console.warn("[auralis] the session toolbar could not be updated", err);
    }
  }
}

/**
 * Let the shell follow the actions that the session toolbar starts.
 *
 * Undo, redo and the session commands change the chain on the server; the
 * shell has to read it back, otherwise the panels would show a chain that
 * does not exist any more.
 */
function wrapSessionActions() {
  const names = ["undo", "redo", "load", "open", "newSession"];
  for (const name of names) {
    const original = sessionController[name];
    if (typeof original !== "function") continue;
    sessionController[name] = async (...args) => {
      const result = await original.apply(sessionController, args);
      await syncChain({ repaint: true });
      /* a loaded session carries the region of the spectral editor */
      if (name === "load" || name === "open") applySpectralRegion(result);
      return result;
    };
  }
}

/**
 * Hand the region of a restored session to the spectral editor.
 *
 * The rectangle is the state of the module, not a parameter: it travels in
 * the chain document of the session (`chain.steps[i].state`) and comes back
 * with it.  The overlay has to be told, otherwise it would show an empty
 * region although the module holds one.
 *
 * @param {object} document  the session document that was restored
 */
function applySpectralRegion(document) {
  const mod = optional.spectral;
  if (!mod || typeof mod.regionFromDocument !== "function") return;
  let region = null;
  try {
    region = mod.regionFromDocument(document, SPECTRAL_EDITOR_CODE);
  } catch (err) {
    console.warn("[auralis] the region of the session could not be read", err);
    return;
  }
  if (!region) return;
  spectralState = { ...spectralState, region };
  if (spectralEditor && typeof spectralEditor.setState === "function") {
    try {
      /* the module already holds it — the server restored the chain */
      spectralEditor.setState({
        region,
        regionApplied: true,
        moduleIndex: spectralIndex(),
      });
    } catch (err) {
      console.warn("[auralis] the region could not be given to the editor", err);
    }
  }
  renderSpectralEditor();
}

/**
 * Read the chain back from the server and show it.
 *
 * @param {object} [options]
 * @param {boolean} [options.repaint] repaint the inspector as well (the
 *        values of the modules changed, not only the chain)
 * @returns {Promise<void>}
 */
async function syncChain(options = {}) {
  if (!hasApi()) return;
  try {
    const res = await api.chain();
    if (!res || !Array.isArray(res.chain)) return;
    state.chain = res.chain;
    if (state.selectedIndex >= state.chain.length) {
      state.selectedIndex = state.chain.length - 1;
    }
    renderChainStrip();
    updateSpectralIndex();
    /* The preview runs on a chain of its own: it follows the server chain. */
    pushLiveChain();
    if (options.repaint) renderInspector();
  } catch (err) {
    console.warn("[auralis] the chain could not be read", err);
  }
}

/**
 * Add a module to the chain.
 *
 * The edit goes through the session controller, so that it can be undone.
 *
 * @param {string} code the code of the module
 * @returns {Promise<void>}
 */
async function addModule(code) {
  if (!code) return;
  if (!hasApi()) {
    toast("The server client is unavailable", "err");
    return;
  }
  setEngine("busy");
  try {
    if (sessionController) await sessionController.addModule(code);
    else await api.addModule(code);
    await syncChain({ repaint: true });
    const idx = findLastIndex(state.chain, (e) => normaliseEntry(e, 0).code === code);
    await selectModule(idx >= 0 ? idx : state.chain.length - 1);
    toast(`${moduleName(code)} added to the chain`, "info");
    setEngine(state.asset ? "ready" : "idle");
  } catch (err) {
    toast(`Could not add ${moduleName(code)} — ${err.message}`, "err");
    setEngine("error");
  }
}

/**
 * Remove a module from the chain.
 *
 * @param {number} index the position of the module
 * @returns {Promise<void>}
 */
async function removeModule(index) {
  if (index < 0 || index >= state.chain.length) return;
  if (!hasApi()) {
    toast("The server client is unavailable", "err");
    return;
  }
  const name = normaliseEntry(state.chain[index], index).display_name;
  setEngine("busy");
  try {
    if (sessionController) await sessionController.removeModule(index);
    else await api.removeModule(index);
    await syncChain({ repaint: true });
    const next = Math.min(index, state.chain.length - 1);
    await selectModule(next >= 0 ? next : -1);
    toast(`${name} removed`, "info");
    setEngine(state.asset ? "ready" : "idle");
  } catch (err) {
    toast(`Could not remove ${name} — ${err.message}`, "err");
    setEngine("error");
  }
}

/**
 * Switch a module in or out.
 *
 * @param {number} index  the position of the module
 * @param {boolean} bypass true when the module is switched out
 * @returns {Promise<void>}
 */
async function toggleBypass(index, bypass) {
  if (index < 0 || index >= state.chain.length) return;
  if (!hasApi()) {
    toast("The server client is unavailable", "err");
    return;
  }
  const name = normaliseEntry(state.chain[index], index).display_name;
  try {
    if (sessionController) await sessionController.setBypass(index, Boolean(bypass));
    else await api.setBypass(index, Boolean(bypass));
    await syncChain({ repaint: true });
    toast(`${name} ${bypass ? "bypassed" : "enabled"}`, "info");
  } catch (err) {
    toast(`Could not change bypass on ${name} — ${err.message}`, "err");
  }
}

/**
 * Move a module to another position.
 *
 * @param {number} from the position it has now
 * @param {number} to   the position it should have
 * @returns {Promise<void>}
 */
async function reorderModule(from, to) {
  if (from === to || from < 0 || to < 0) return;
  if (from >= state.chain.length || to >= state.chain.length) return;
  if (!hasApi()) {
    toast("The server client is unavailable", "err");
    return;
  }
  try {
    if (sessionController) await sessionController.reorder(from, to);
    else await api.reorder(from, to);
    await syncChain({ repaint: true });
    await selectModule(to);
    toast(t("toast.reordered", "Chain reordered"), "info");
  } catch (err) {
    toast(`Could not reorder the chain — ${err.message}`, "err");
  }
}

/**
 * Empty the chain.
 *
 * The whole chain is one step of the history: one undo brings the modules
 * back with their parameters.
 *
 * @returns {Promise<void>}
 */
async function clearChain() {
  if (!state.chain.length) return;
  if (!hasApi()) {
    toast("The server client is unavailable", "err");
    return;
  }
  try {
    if (sessionController) {
      await sessionController.clearChain();
    } else {
      for (let i = state.chain.length - 1; i >= 0; i--) {
        const res = await api.removeModule(i);
        if (res && Array.isArray(res.chain)) state.chain = res.chain;
      }
    }
    state.chain = [];
    state.selectedIndex = -1;
    renderChainStrip();
    renderInspector();
    toast(t("toast.cleared", "Chain cleared"), "info");
  } catch (err) {
    toast(`Could not clear the chain — ${err.message}`, "err");
  }
}

function findLastIndex(arr, predicate) {
  for (let i = arr.length - 1; i >= 0; i--) if (predicate(arr[i], i)) return i;
  return -1;
}

/* --------------------------------------------------------- selection */

async function selectModule(index) {
  state.selectedIndex = index;
  state.presetName = "";
  state.dirty = false;
  highlightChain();
  if (state.tab !== "panel") {
    state.tab = "panel";
    for (const btn of dom.inspectorTabs.querySelectorAll("button")) {
      btn.classList.toggle("on", btn.dataset.tab === "panel");
    }
  }
  await renderInspector();
}

/* -------------------------------------------------------------- params */

let lastParamError = "";

/* The parameter writes are queued: a knob drag sends one request per frame
   and the server has to see them in the order the user made them — otherwise
   the last frame of the drag would not be the value that is left. */
let paramQueue = Promise.resolve();

/**
 * Run a task after every task that is already queued.
 *
 * @param {Function} task the work
 * @returns {Promise<*>} the result of the task
 */
function queueParam(task) {
  const next = paramQueue.then(
    () => task(),
    () => task()
  );
  paramQueue = next.then(
    () => undefined,
    () => undefined
  );
  return next;
}

/**
 * A parameter of the selected module changed.
 *
 * The change goes through the session controller, so that it is one step of
 * the history — a knob drag is folded into a single step by the controller.
 *
 * @param {string} name  the name of the parameter
 * @param {*}      value the new value
 * @returns {Promise<void>}
 */
async function onParam(name, value) {
  const index = state.selectedIndex;
  if (index < 0 || !name) return;
  if (!hasApi()) return;
  const entry = state.chain[index];
  if (entry) {
    if (!entry.values || typeof entry.values !== "object") entry.values = {};
    entry.values[name] = value;
  }
  /* The live preview hears the change first: the socket is the short way to
     the audio.  The server chain follows over HTTP. */
  pushLiveParam(index, name, value);
  try {
    await queueParam(async () => {
      if (sessionController) await sessionController.setParam(index, name, value);
      else await api.setParam(index, name, value);
    });
    if (lastParamError) {
      lastParamError = "";
      toast("Parameter update recovered", "info");
    }
    refreshDirtyState();
  } catch (err) {
    const key = `${index}:${name}`;
    if (key !== lastParamError) {
      lastParamError = key;
      toast(`Parameter update failed — ${err.message}`, "err");
    }
    /* The server refused the change: the preview follows the server chain. */
    pushLiveChain();
  }
}

/** Recompute the dirty flag and push it into the panel header. */
function refreshDirtyState() {
  const entry = state.chain[state.selectedIndex];
  if (!entry) {
    state.dirty = false;
    return;
  }
  const code = normaliseEntry(entry, state.selectedIndex).code;
  const saved = savedValues.get(code) || {};
  const values = entryValues(entry);
  state.dirty = isDirtyValues(values, saved);
  if (panelView && typeof panelView.update === "function") {
    try {
      panelView.update({ dirty: state.dirty });
    } catch (err) {
      console.warn("[auralis] panel.update failed", err);
    }
  }
  highlightChain();
}

/**
 * Set the name of the preset of the selected module.
 *
 * @param {string} name   the name of the preset
 * @param {object} [values] the values the preset stands for
 */
function setPresetName(name, values) {
  state.presetName = String(name || "");
  if (panelView && typeof panelView.setPreset === "function") {
    try {
      panelView.setPreset(state.presetName, values);
    } catch (err) {
      console.warn("[auralis] panel.setPreset failed", err);
    }
  }
  state.dirty = false;
}

/* Panel menu actions: "save" | "load" | "compare" | "menu". */
function onPanelAction(action) {
  if (action === "save") openPresetDialog("save");
  else if (action === "load") openPresetDialog("load");
  else if (action === "compare") compareChains();
  else if (action === "menu") openModuleMenu();
}

async function learnSelected() {
  const index = state.selectedIndex;
  if (index < 0 || index >= state.chain.length) {
    toast(t("toast.selectFirst", "Select a module first"), "warn");
    return;
  }
  const name = normaliseEntry(state.chain[index], index).display_name;
  setEngine("busy");
  try {
    const res = await api.learn(index);
    const message = res && (res.message || res.ok);
    const text = message === true || !message ? "profile learned" : String(message);
    toast(text.startsWith(name) ? text : `${name}: ${text}`, "info", 3600);
    setEngine(state.asset ? "ready" : "idle");
  } catch (err) {
    toast(`Learn failed on ${name} — ${err.message}`, "err");
    setEngine("error");
  }
}

/* ------------------------------------------------------------- presets */

function typedValue(desc, value) {
  const type = desc && desc.type ? desc.type : typeof value === "number" ? "float" : typeof value;
  switch (type) {
    case "bool":
      return { Type: "Bool", Value: Boolean(value) };
    case "int":
      return { Type: "Int", Value: Math.round(Number(value) || 0) };
    case "enum":
      return { Type: "String", Value: String(value) };
    default:
      return { Type: "Float", Value: Number(value) || 0 };
  }
}

/* ------------------------------------------------- the preset library */

/**
 * Create the manager of the preset library.
 *
 * The library lives on the server (GET /api/presets, POST /api/preset/save,
 * POST /api/preset/load, DELETE /api/preset); the manager renders it in the
 * Presets tab of the inspector and hands the presets it loads back to the
 * shell.
 *
 * @returns {boolean} true when the manager was created
 */
function initPresetManager() {
  const mod = optional.presets;
  if (!mod || typeof mod.createPresetManager !== "function") {
    console.warn("[auralis] the preset library is unavailable");
    return false;
  }
  if (!hasApi()) return false;
  presetHost = el("div", { class: "preset-host" });
  try {
    presetManager = mod.createPresetManager({
      api,
      container: presetHost,
      onLoad: (document, context) => applyPreset(document, context),
      onError: (err) => toast(`Preset library — ${err.message}`, "err"),
      onChange: () => syncCurrentPreset(),
      getContent: () => currentPresetContent(),
      confirm: (message) => confirmDialog(message),
    });
  } catch (err) {
    console.warn("[auralis] the preset manager could not be created", err);
    presetManager = null;
    presetHost = null;
    return false;
  }
  /* The tab may already be open: it shows the library now. */
  if (state.tab === "presets") renderInspector();
  return true;
}

/**
 * The content of the preset a save would write: the parameters of the
 * selected module.
 *
 * @returns {object|null} the content, or null when no module is selected
 */
function currentPresetContent() {
  const index = state.selectedIndex;
  if (index < 0 || index >= state.chain.length) return null;
  const entry = normaliseEntry(state.chain[index], index);
  const info = moduleInfo(entry.code) || {};
  const values = entryValues(state.chain[index]);
  const params = {};
  for (const [name, value] of Object.entries(values)) params[name] = value;
  return {
    module_code: entry.code,
    element: info.element || entry.element || "",
    display_name: entry.display_name,
    params,
  };
}

/**
 * Apply a preset of the library to the selected module.
 *
 * The parameters go through the session controller, so that a preset can be
 * undone like every other change of the chain.
 *
 * @param {object} document the preset document of the server
 * @param {object} context  what the manager knows about the preset
 * @returns {Promise<void>}
 */
async function applyPreset(document, context) {
  const index = state.selectedIndex;
  if (index < 0 || index >= state.chain.length) {
    toast(t("toast.selectFirst", "Select a module first"), "warn");
    return;
  }
  const entry = normaliseEntry(state.chain[index], index);
  const params = {};
  for (const [name, value] of Object.entries((document && document.params) || {})) {
    params[name] = unwrapTypedValue(value);
  }
  for (const [name, value] of Object.entries(params)) {
    await onParam(name, value);
  }
  const name = String(
    unwrapTypedValue((context && context.name) || (document && document.name) || "") || ""
  );
  if (name) {
    savedValues.set(entry.code, { ...params });
    setPresetName(name, params);
  }
  if (panelView && typeof panelView.update === "function") {
    try {
      panelView.update(params);
    } catch (err) {
      console.warn("[auralis] panel.update failed", err);
    }
  }
  toast(`Preset "${name || "loaded"}" loaded`, "info");
}

/**
 * Show the preset the manager holds as the preset of the selected module.
 *
 * The name is only adopted when the preset belongs to the module that is
 * selected, and the dirty marker is left alone: reading the library does not
 * change the module.
 */
function syncCurrentPreset() {
  if (!presetManager || typeof presetManager.current !== "function") return;
  const preset = presetManager.current();
  if (!preset || !preset.name) return;
  const index = state.selectedIndex;
  if (index < 0 || index >= state.chain.length) return;
  const code = normaliseEntry(state.chain[index], index).code;
  if (preset.module_code && preset.module_code !== code) return;
  if (preset.name === state.presetName) return;
  state.presetName = String(preset.name);
  if (panelView && typeof panelView.setPreset === "function") {
    try {
      panelView.setPreset(state.presetName);
    } catch (err) {
      console.warn("[auralis] panel.setPreset failed", err);
    }
  }
}

/**
 * Add the tab of the preset library to the inspector.
 */
function addPresetTab() {
  if (!dom.inspectorTabs) return;
  const existing = dom.inspectorTabs.querySelector('[data-tab="presets"]');
  if (existing) {
    presetTab = existing;
    return;
  }
  presetTab = el(
    "button",
    { "data-tab": "presets", type: "button" },
    t("inspector.tab.presets", "Presets")
  );
  dom.inspectorTabs.appendChild(presetTab);
}

/**
 * The Presets tab: the library of the server.
 *
 * @param {Element} body the body of the inspector
 * @returns {Promise<void>}
 */
async function renderPresetTab(body) {
  body.replaceChildren();
  if (!presetManager) {
    body.replaceChildren(
      el("p", { class: "empty" }, t("inspector.presetsEmpty", "The preset library is unavailable."))
    );
    return;
  }
  body.replaceChildren(presetHost);
  try {
    await presetManager.refresh();
  } catch (err) {
    /* the manager shows the error in its own panel */
  }
}

/**
 * Attach the preset menu of the library to the head of the module panel.
 *
 * The panel positions its own menu against its head, so the menu of the
 * library is rendered into the same place: the chip of the preset and the
 * "..." button open it.
 *
 * @param {object} panel the panel of the selected module
 */
function attachPresetMenu(panel) {
  presetMenu = null;
  const mod = optional.presets;
  if (!panel || !panel.root || !mod || typeof mod.renderPresetMenu !== "function") return;
  if (!presetManager) return;
  const head = panel.root.querySelector(".aw-panel-head");
  if (!head) return;
  try {
    presetMenu = mod.renderPresetMenu(head, presetManager, presetMenuHandlers());
    appendMenuItem(t("control.reset", "Reset to Defaults"), () => resetSelectedToDefaults());
    appendMenuItem(
      t("control.removeModule", "Remove Module"),
      () => removeModule(state.selectedIndex),
      true
    );
  } catch (err) {
    console.warn("[auralis] the preset menu could not be rendered", err);
    presetMenu = null;
  }
}

/**
 * Add one entry to the preset menu of the panel.
 *
 * @param {string}   label the text of the entry
 * @param {Function} run   what the entry does
 * @param {boolean}  [danger] the entry is destructive
 */
function appendMenuItem(label, run, danger = false) {
  if (!presetMenu || !presetMenu.element) return;
  const button = el(
    "button",
    {
      class: danger ? "pm-menu-item pm-danger" : "pm-menu-item",
      type: "button",
      role: "menuitem",
    },
    label
  );
  button.addEventListener("click", () => {
    if (typeof presetMenu.close === "function") presetMenu.close();
    run();
  });
  presetMenu.element.appendChild(button);
}

/**
 * The handlers of the preset menu of the panel.
 *
 * The ones that are not given here are carried out by the manager itself.
 *
 * @returns {object} the handlers
 */
function presetMenuHandlers() {
  return {
    onSave: () => saveCurrentPreset(),
    onSaveAs: () => {
      setTab("presets");
      if (presetManager && typeof presetManager.focusSaveForm === "function") {
        presetManager.focusSaveForm();
      }
    },
    onLoad: () => setTab("presets"),
    onRename: (preset) => {
      setTab("presets");
      if (presetManager && preset && typeof presetManager.beginRename === "function") {
        presetManager.beginRename(preset.path);
      }
    },
    onRemove: (preset) => {
      if (presetManager && preset && typeof presetManager.remove === "function") {
        return presetManager.remove(preset.path);
      }
      return undefined;
    },
    onReload: () => (presetManager ? presetManager.refresh() : undefined),
  };
}

/**
 * Open the preset menu of the module panel.
 */
function openPresetMenu() {
  if (presetMenu && typeof presetMenu.toggle === "function") {
    presetMenu.toggle();
    return;
  }
  /* Without the library the panel keeps its own menu. */
  openModuleMenu();
}

/**
 * Write the current preset over itself.
 *
 * @returns {Promise<void>}
 */
async function saveCurrentPreset() {
  if (!presetManager) return;
  const preset = presetManager.current();
  if (!preset || !preset.name) {
    setTab("presets");
    if (typeof presetManager.focusSaveForm === "function") presetManager.focusSaveForm();
    return;
  }
  try {
    await presetManager.save({ name: preset.name, category: preset.category });
    toast(`Preset "${preset.name}" saved`, "info");
  } catch (err) {
    toast(`Preset save failed — ${err.message}`, "err");
  }
}

/**
 * Set every parameter of the selected module back to its default.
 */
function resetSelectedToDefaults() {
  const index = state.selectedIndex;
  if (index < 0 || index >= state.chain.length) {
    toast(t("toast.selectFirst", "Select a module first"), "warn");
    return;
  }
  const info = moduleInfo(normaliseEntry(state.chain[index], index).code) || {};
  const descs = Array.isArray(info.params) ? info.params : [];
  const values = {};
  for (const desc of descs) {
    if (!desc || desc.name === undefined) continue;
    values[desc.name] = desc.default;
    onParam(desc.name, desc.default);
  }
  if (panelView && typeof panelView.update === "function") {
    try {
      panelView.update(values);
    } catch (err) {
      console.warn("[auralis] panel.update failed", err);
    }
  }
}

/**
 * Ask the user a yes / no question in the dialog of the shell.
 *
 * @param {string} message the question
 * @returns {Promise<boolean>} the answer
 */
function confirmDialog(message) {
  return new Promise((resolve) => {
    const yes = el("button", { class: "tbtn tbtn-primary", type: "button" }, t("control.yes", "Yes"));
    const no = el("button", { class: "tbtn", type: "button" }, t("control.no", "No"));
    let answered = false;
    const finish = (value) => {
      if (answered) return;
      answered = true;
      if (modalOnClose === cancel) modalOnClose = null;
      closeModal();
      resolve(value);
    };
    const cancel = () => finish(false);
    yes.addEventListener("click", () => finish(true));
    no.addEventListener("click", () => finish(false));
    modalOnClose = cancel;
    openModal({
      title: t("control.confirm", "Confirm"),
      body: [el("p", { class: "modal-note" }, String(message || ""))],
      foot: [yes, no],
    });
  });
}

/**
 * Preset dialog.  mode = "save" stores the current parameters under a name,
 * mode = "load" restores a stored preset into the selected module.
 *
 * With the preset library the dialog is only a front end for the manager:
 * the library stays the one place the presets live in.
 */
function openPresetDialog(mode) {
  const index = state.selectedIndex;
  if (index < 0 || index >= state.chain.length) {
    toast("Select a module first", "warn");
    return;
  }
  const entry = normaliseEntry(state.chain[index], index);
  const info = moduleInfo(entry.code) || {};

  if (presetManager) {
    openPresetDialogWithManager(mode, entry);
    return;
  }

  if (mode === "load") {
    const list = el("div", { class: "insp-section" }, el("p", { class: "modal-note" }, t("dialog.loading", "Loading…")));
    openModal({
      title: t("dialog.loadPreset", "Load preset"),
      body: list,
      foot: [el("button", { class: "tbtn", type: "button", onclick: closeModal }, t("control.close", "Close"))],
    });
    api
      .presets()
      .then((res) => {
        const presets = (res && res.presets) || [];
        list.replaceChildren();
        if (!presets.length) {
          list.append(el("p", { class: "modal-note" }, t("dialog.noPresets", "No presets stored yet.")));
          return;
        }
        for (const p of presets) {
          const name = typeof p === "string" ? p : p.path || p.name || "preset";
          const btn = el("button", { class: "tbtn tbtn-sm", type: "button" }, name);
          btn.addEventListener("click", async () => {
            try {
              const preset = await api.loadPreset(typeof p === "string" ? p : p.path || name);
              const params = (preset && preset.params) || {};
              const values = {};
              for (const [k, v] of Object.entries(params)) {
                values[k] = v && typeof v === "object" && "Value" in v ? v.Value : v;
              }
              for (const [k, v] of Object.entries(values)) {
                await api.setParam(index, k, v);
              }
              const e = state.chain[index];
              if (e) {
                e.values = { ...(e.values || {}), ...values };
              }
              savedValues.set(entry.code, { ...values });
              setPresetName(name);
              closeModal();
              toast(`Preset "${name}" loaded`, "info");
              renderInspector();
            } catch (err) {
              toast(`Could not load the preset — ${err.message}`, "err");
            }
          });
          list.append(el("div", { class: "insp-section" }, btn));
        }
      })
      .catch((err) => {
        list.replaceChildren(el("p", { class: "modal-note" }, `Could not list presets — ${err.message}`));
      });
    return;
  }

  const nameInput = el("input", { type: "text", value: state.presetName || "", placeholder: t("dialog.presetName", "Preset name") });
  const saveBtn = el("button", { class: "tbtn tbtn-primary", type: "button" }, t("control.save", "Save"));
  saveBtn.addEventListener("click", async () => {
    try {
      const descs = (info.params || []).slice();
      const values = {};
      const e = state.chain[index] || {};
      for (const d of descs) {
        const v = e.values && e.values[d.name] !== undefined ? e.values[d.name] : d.default;
        values[d.name] = typedValue(d, v);
      }
      const preset = {
        format: "auralis.preset",
        version: 1,
        module: { code: entry.code, element: info.element || "" },
        params: values,
        state: { Type: "Base64", Value: "" },
      };
      const res = await api.savePreset(preset);
      savedValues.set(entry.code, defaultValues(entry.code));
      setPresetName(nameInput.value);
      closeModal();
      toast(`${t("toast.saved", "Preset saved")}${res && res.path ? ` — ${res.path}` : ""}`, "info", 3600);
    } catch (err) {
      toast(`Preset save failed — ${err.message}`, "err");
    }
  });

  openModal({
    title: t("dialog.savePreset", "Save preset — {name}", { name: entry.display_name }),
    body: [
      el("p", { class: "modal-note" }, `Code ${entry.code}`),
      el(
        "div",
        { class: "ctrl inline" },
        el("span", { class: "ctrl-label" }, t("dialog.presetName", "Preset name")),
        nameInput
      ),
    ],
    foot: [
      saveBtn,
      el("button", { class: "tbtn", type: "button", onclick: closeModal }, t("control.cancel", "Cancel")),
    ],
  });
}

/**
 * The preset dialog, driven by the preset library.
 *
 * @param {string} mode  "save" or "load"
 * @param {object} entry the chain entry of the selected module
 */
function openPresetDialogWithManager(mode, entry) {
  if (mode === "load") {
    const list = el("div", { class: "insp-section" });
    openModal({
      title: t("dialog.loadPreset", "Load preset"),
      body: list,
      foot: [el("button", { class: "tbtn", type: "button", onclick: closeModal }, t("control.close", "Close"))],
    });
    presetManager
      .refresh()
      .then(() => {
        const presets = presetManager.all();
        list.replaceChildren();
        if (!presets.length) {
          list.append(el("p", { class: "modal-note" }, t("dialog.noPresets", "No presets stored yet.")));
          return;
        }
        for (const preset of presets) {
          const btn = el("button", { class: "tbtn tbtn-sm", type: "button" }, preset.name);
          btn.addEventListener("click", async () => {
            try {
              await presetManager.load(preset.path);
              closeModal();
            } catch (err) {
              toast(`Could not load the preset — ${err.message}`, "err");
            }
          });
          list.append(el("div", { class: "insp-section" }, btn));
        }
      })
      .catch((err) => {
        list.replaceChildren(el("p", { class: "modal-note" }, `Could not list presets — ${err.message}`));
      });
    return;
  }

  const nameInput = el("input", {
    type: "text",
    value: state.presetName || "",
    placeholder: t("dialog.presetName", "Preset name"),
  });
  const categoryInput = el("input", {
    type: "text",
    value: "Presets",
    placeholder: t("dialog.presetCategory", "Category"),
  });
  const saveBtn = el("button", { class: "tbtn tbtn-primary", type: "button" }, t("control.save", "Save"));
  saveBtn.addEventListener("click", async () => {
    try {
      const res = await presetManager.save({
        name: nameInput.value,
        category: categoryInput.value || "Presets",
      });
      savedValues.set(entry.code, defaultValues(entry.code));
      closeModal();
      toast(
        `${t("toast.saved", "Preset saved")}${res && res.path ? ` — ${res.path}` : ""}`,
        "info",
        3600
      );
    } catch (err) {
      toast(`Preset save failed — ${err.message}`, "err");
    }
  });

  openModal({
    title: t("dialog.savePreset", "Save preset — {name}", { name: entry.display_name }),
    body: [
      el("p", { class: "modal-note" }, `Code ${entry.code}`),
      el(
        "div",
        { class: "ctrl inline" },
        el("span", { class: "ctrl-label" }, t("dialog.presetName", "Preset name")),
        nameInput
      ),
      el(
        "div",
        { class: "ctrl inline" },
        el("span", { class: "ctrl-label" }, t("dialog.presetCategory", "Category")),
        categoryInput
      ),
    ],
    foot: [
      saveBtn,
      el("button", { class: "tbtn", type: "button", onclick: closeModal }, t("control.cancel", "Cancel")),
    ],
  });
}

/* ------------------------------------------------------------ file I/O */

async function openFile(file) {
  if (!file) return;
  setEngine("busy");
  toast(`Uploading ${file.name}…`, "info", 1600);
  try {
    const up = await api.upload(file);
    state.asset = {
      asset_id: up.asset_id,
      sample_rate: up.sample_rate || 48000,
      channels: up.channels || 2,
      frames: up.frames || 0,
      duration: up.duration || 0,
      name: file.name,
    };
    dom.fileName.textContent = file.name;
    dom.fileName.classList.remove("idle");
    dom.fileMeta.textContent = `${state.asset.sample_rate} Hz · ${state.asset.channels} ch · ${state.asset.duration.toFixed(2)} s`;
    state.levelText = "";
    if (dom.stLevel) dom.stLevel.textContent = t("status.level", "-inf dBFS");
    refreshCanvasSummary();

    const analysis = await api.analyze(state.asset.asset_id);
    state.analysis = analysis || null;
    if (state.analysis) {
      if (view && typeof view.setData === "function") {
        view.setData({
          ...state.analysis,
          sample_rate: state.asset.sample_rate,
          duration: state.asset.duration,
        });
      }
      if (dom.dbRange && state.analysis.max_db !== undefined && state.analysis.min_db !== undefined) {
        dom.dbRange.textContent = `${state.analysis.max_db} .. ${state.analysis.min_db} dB`;
      }
      const nyquist = Math.floor(state.asset.sample_rate / 2);
      if (dom.freqHi) dom.freqHi.value = String(nyquist);
      state.frequencyRange = [Number(dom.freqLo.value) || 0, nyquist];
      if (view && typeof view.setFrequencyRange === "function") {
        view.setFrequencyRange(state.frequencyRange[0], state.frequencyRange[1]);
      }
    }
    if (spectralEditor) {
      try {
        spectralEditor.setData({
          duration: state.asset.duration,
          sample_rate: state.asset.sample_rate,
        });
      } catch (err) {
        /* the overlay works without the asset information */
      }
    }
    setZoom(1);
    setScroll(0);
    setEngine("ready");
    toast(`Loaded ${file.name}`, "info");
  } catch (err) {
    toast(`Could not load ${file.name} — ${err.message}`, "err");
    setEngine("error");
    dom.fileName.textContent = "No file loaded";
    dom.fileName.classList.add("idle");
    dom.fileMeta.textContent = "";
  }
}

/* --------------------------------------------------------- WAV decoding */

/**
 * Minimal RIFF/WAVE decoder.
 * Supports PCM (8/16/24/32 bit) and IEEE float (32/64 bit), mono or multi
 * channel.  Returns { sampleRate, channels: Float32Array[], frames }.
 */
function decodeWav(arrayBuffer) {
  const view = new DataView(arrayBuffer);
  const tag = (off) =>
    String.fromCharCode(
      view.getUint8(off),
      view.getUint8(off + 1),
      view.getUint8(off + 2),
      view.getUint8(off + 3)
    );

  if (view.byteLength < 44 || tag(0) !== "RIFF") throw new Error("preview payload is not RIFF data");
  if (tag(8) !== "WAVE") throw new Error("preview payload is not a WAVE file");

  let fmt = null;
  let dataOffset = -1;
  let dataLength = 0;

  for (let pos = 12; pos + 8 <= view.byteLength; ) {
    const id = tag(pos);
    const size = view.getUint32(pos + 4, true);
    const body = pos + 8;
    if (id === "fmt " && size >= 16) {
      fmt = {
        format: view.getUint16(body, true),
        channels: view.getUint16(body + 2, true),
        sampleRate: view.getUint32(body + 4, true),
        bits: view.getUint16(body + 14, true),
      };
      if (fmt.format === 0xfffe && size >= 40) fmt.format = view.getUint16(body + 24, true);
    } else if (id === "data") {
      dataOffset = body;
      dataLength = Math.min(size, view.byteLength - body);
    }
    pos = body + size + (size & 1);
  }

  if (!fmt) throw new Error("preview payload has no fmt chunk");
  if (dataOffset < 0) throw new Error("preview payload has no data chunk");

  const channels = Math.max(1, fmt.channels | 0);
  const bytes = Math.max(1, fmt.bits >> 3);
  const frames = Math.max(0, Math.floor(dataLength / (bytes * channels)));
  const out = [];
  for (let c = 0; c < channels; c++) out.push(new Float32Array(frames));

  let offset = dataOffset;
  for (let i = 0; i < frames; i++) {
    for (let c = 0; c < channels; c++) {
      let v = 0;
      if (fmt.format === 3 && fmt.bits === 32) v = view.getFloat32(offset, true);
      else if (fmt.format === 3 && fmt.bits === 64) v = view.getFloat64(offset, true);
      else if (fmt.bits === 8) v = (view.getUint8(offset) - 128) / 128;
      else if (fmt.bits === 16) v = view.getInt16(offset, true) / 32768;
      else if (fmt.bits === 24) {
        const b0 = view.getUint8(offset);
        const b1 = view.getUint8(offset + 1);
        const b2 = view.getUint8(offset + 2);
        let s = (b2 << 16) | (b1 << 8) | b0;
        if (s & 0x800000) s -= 0x1000000;
        v = s / 8388608;
      } else if (fmt.bits === 32) v = view.getInt32(offset, true) / 2147483648;
      out[c][i] = v;
      offset += bytes;
    }
  }
  return { sampleRate: fmt.sampleRate || 48000, channels: out, frames };
}

function peakDb(channels) {
  let peak = 0;
  for (const ch of channels) {
    for (let i = 0; i < ch.length; i++) {
      const a = Math.abs(ch[i]);
      if (a > peak) peak = a;
    }
  }
  return peak > 0 ? 20 * Math.log10(peak) : -Infinity;
}

/* ---------------------------------------------------------- the live preview */

/**
 * The live preview: the chain is streamed over the WebSocket while the
 * parameters change, so the user hears the knob they are turning and not the
 * result of the last render.
 *
 * The server owns an engine of its own for the preview (aurserver/ws.py), so
 * a preview can never disturb the chain the user is working on.  The messages
 * the client sends are
 *
 *   {"type": "configure", "asset_id": "...", "chain": [...],
 *    "start": 0.0, "end": 30.0, "realtime": true}
 *   {"type": "param", "index": 0, "name": "Gain", "value": -6.0}
 *   {"type": "configure", "chain": [...]}   the chain alone
 *   {"type": "stop"}
 *
 * and the messages the server sends are
 *
 *   {"type": "ready", "sample_rate": 48000, "channels": 2, "block_size": 1024}
 *   {"type": "applied", "kind": "param"|"bypass"|"configure", ...}
 *   {"type": "dropout", "frames": 1024}
 *   {"type": "warning", "message": "..."}
 *   {"type": "end", "frames": N, "blocks": N, "dropped_frames": 0}
 *   {"type": "error", "message": "..."}
 *
 * The audio arrives as binary frames of interleaved little-endian int16 PCM:
 * one block of `block_size` frames per channel.
 */

/**
 * How far the playback runs ahead of the audio that has arrived, in seconds.
 *
 * The buffer has to be larger than the jitter of the delivery: measured on
 * this machine, 10 % of the blocks arrive 38 ms after the previous one and
 * 1 % arrive 75 ms after it, so a buffer of 30 ms leaves a hole in the audio
 * a few times a minute.  60 ms covers the jitter up to the 99th percentile.
 */
const LIVE_HEADROOM = 0.06;

/** The window the level of the output is measured with, in samples. */
const LIVE_FFT = 512;

/** How often the level of the output is measured, in milliseconds. */
const LIVE_POLL_MS = 5;

/** The smallest level change the measurement of the preview accepts, in dB. */
const LIVE_LEVEL_STEP_DB = 3;

/** The longest preview the server is asked for, in seconds. */
const LIVE_SECONDS = 30;

/** The readyState of a socket that is open (the constant of the platform). */
const LIVE_SOCKET_OPEN = 1;

/** The state of the live preview. */
const live = {
  socket: null,        // the WebSocket of the preview
  mode: "live",        // "live" (the socket) or "file" (the rendered window)
  state: "off",        // "off", "connecting", "live", "failed"
  graph: null,         // the audio context the preview is played in
  output: null,        // the node the blocks are played through
  analyser: null,      // the node that measures the level of the output
  sources: new Set(),  // the blocks that are scheduled
  nextTime: 0,         // the audio time the next block is scheduled for
  audibleAt: 0,        // when the last scheduled block becomes audible
  appliedAt: 0,        // when the server answered the last change
  sampleRate: 48000,
  channels: 2,
  blockSize: 1024,
  frames: 0,           // the frames the client received
  blocks: 0,           // the blocks the client received
  totalFrames: 0,      // the frames of every stream of this session
  totalBlocks: 0,
  serverFrames: 0,     // the frames the server says it sent
  dropped: 0,          // the frames the server says it dropped
  dropouts: 0,         // the dropout notices the client received
  gaps: 0,             // the holes the client had to skip
  pending: null,       // the change that is being measured
  levelTimer: 0,
  levelDb: -Infinity,  // the level of the output, in dBFS
  failures: 0,         // how often the socket could not be opened
  path: null,          // the last message of the server that carried a path
};

/**
 * Write one line to the console and to the diagnostics of the page.
 *
 * The diagnostics are what the measurement harness reads: `window.auralis.log`
 * holds the lines, `window.auralis.preview` the counters of the preview.
 *
 * @param {string} message the line
 * @returns {string} the line
 */
function liveLog(message) {
  const line = `[auralis] ${message}`;
  console.log(line);
  try {
    const bag = (window.auralis = window.auralis || {});
    if (!Array.isArray(bag.log)) bag.log = [];
    bag.log.push(line);
    if (bag.log.length > 400) bag.log.shift();
  } catch (err) {
    /* the diagnostics are a nicety, never a requirement */
  }
  return line;
}

/**
 * The address of the streaming endpoint of the server.
 *
 * @returns {string} the WebSocket address
 */
function liveSocketUrl() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws/stream`;
}

/**
 * The chain in the shape the preview server expects.
 *
 * Every step carries the code of the module, whether it is engaged and the
 * values of its parameters; the server builds an engine of its own out of it.
 *
 * @returns {Array<object>} the chain
 */
function liveChainPayload() {
  return state.chain.map((entry, index) => {
    const norm = normaliseEntry(entry, index);
    const params = {};
    for (const [name, value] of Object.entries(entryValues(entry))) {
      params[name] = value;
    }
    return { code: norm.code, enabled: !norm.bypass, params };
  });
}

/**
 * Send one message to the server.
 *
 * @param {object} message the message
 * @returns {boolean} true when the message was handed to the socket
 */
function liveSend(message) {
  const socket = live.socket;
  if (!socket) return false;
  try {
    if (socket.readyState !== LIVE_SOCKET_OPEN) return false;
    socket.send(JSON.stringify(message));
    return true;
  } catch (err) {
    console.warn("[auralis] the live preview could not send a message", err);
    return false;
  }
}

/**
 * The output graph of the preview.
 *
 * The blocks are played through a gain node; an analyser behind it measures
 * the level of the output, so the shell can tell when the sound changed.
 *
 * @returns {AudioContext|null} the context, or null when WebAudio is missing
 */
function liveGraph() {
  if (live.graph) return live.graph;
  let ctx = null;
  try {
    ctx = getAudioContext();
  } catch (err) {
    liveLog(`live preview: WebAudio is not available — ${err.message}`);
    return null;
  }
  const output = ctx.createGain();
  const analyser = ctx.createAnalyser();
  analyser.fftSize = LIVE_FFT;
  analyser.smoothingTimeConstant = 0;
  output.connect(analyser);
  analyser.connect(ctx.destination);
  live.graph = ctx;
  live.output = output;
  live.analyser = analyser;
  return ctx;
}

/**
 * Play one block of the stream.
 *
 * The blocks are scheduled one after the other, so the audio has no hole in
 * it; the block that arrives late is played late only when the client was
 * busy for longer than the headroom, and the hole is counted.
 *
 * @param {ArrayBuffer} arrayBuffer the block (interleaved int16)
 * @returns {boolean} true when the block was scheduled
 */
function liveOnAudio(arrayBuffer) {
  const ctx = liveGraph();
  if (!ctx) return false;
  const channels = live.channels;
  const frames = Math.floor((arrayBuffer.byteLength || 0) / (2 * channels));
  if (!frames) return false;
  const view = new DataView(arrayBuffer);
  const buffer = ctx.createBuffer(channels, frames, live.sampleRate);
  for (let c = 0; c < channels; c++) {
    const out = buffer.getChannelData(c);
    let offset = c * 2;
    for (let i = 0; i < frames; i++) {
      out[i] = view.getInt16(offset, true) / 32768;
      offset += channels * 2;
    }
  }
  const source = ctx.createBufferSource();
  source.buffer = buffer;
  source.connect(live.output);
  const now = ctx.currentTime;
  if (!live.nextTime) live.nextTime = now + LIVE_HEADROOM;
  if (live.nextTime < now) {
    /* the client was busy for longer than the headroom: the hole is counted */
    live.nextTime = now + LIVE_HEADROOM;
    live.gaps += 1;
  }
  source.start(live.nextTime);
  /* the moment the block becomes audible, in the clock of the page */
  live.audibleAt = performance.now() + (live.nextTime - now) * 1000;
  live.nextTime += frames / live.sampleRate;
  live.sources.add(source);
  source.onended = () => {
    live.sources.delete(source);
  };
  live.frames += frames;
  live.blocks += 1;
  live.totalFrames += frames;
  live.totalBlocks += 1;
  return true;
}

/**
 * Handle one control message of the server.
 *
 * @param {object} payload the message
 */
function liveOnControl(payload) {
  const kind = String(payload.type || "");
  if (kind === "ready") {
    live.sampleRate = Number(payload.sample_rate) || 48000;
    live.channels = Math.max(1, Number(payload.channels) || 2);
    live.blockSize = Math.max(1, Number(payload.block_size) || 1024);
    live.state = "live";
    live.nextTime = 0;
    /* the counters belong to one stream */
    live.frames = 0;
    live.blocks = 0;
    live.serverFrames = 0;
    live.dropped = 0;
    live.dropouts = 0;
    live.gaps = 0;
    state.playing = true;
    if (dom.btnPlay) dom.btnPlay.classList.add("on");
    setEngine("playing");
    liveStartLevelWatch();
    /* the path of the stream: the server says which one it is on, and the
       status bar shows it */
    applyServerPath(payload);
    liveLog(
      `live preview: ready — ${live.sampleRate} Hz, ${live.channels} ch, ` +
        `${payload.frames || 0} frames, ${live.blockSize} frames per block`
    );
    return;
  }
  if (kind === "profile") {
    /* the server repeats the path in a message of its own: the label of the
       path, what it changed and the latency of that path */
    applyServerPath(payload);
    liveLog(
      `live preview: the path is "${payload.profile_label || payload.profile || "?"}" — ` +
        `${payload.latency_samples !== undefined ? payload.latency_samples : "?"} samples of latency`
    );
    return;
  }
  if (kind === "applied") {
    live.appliedAt = performance.now();
    /* the path may have changed with the chain */
    applyServerPath(payload);
    const pending = live.pending;
    if (pending && payload.kind === "param") {
      const server = live.appliedAt - pending.at;
      const audible = live.audibleAt ? live.audibleAt - pending.at : 0;
      pending.serverMs = server;
      pending.audibleMs = audible;
      liveLog(
        `live preview: "${pending.name}" -> ${pending.value} — the server applied it ` +
          `${server.toFixed(1)} ms after the change, the changed audio is audible ` +
          `${audible.toFixed(1)} ms after it`
      );
    }
    return;
  }
  if (kind === "dropout") {
    live.dropouts += 1;
    live.dropped += Number(payload.frames) || 0;
    liveLog(`live preview: the server dropped ${payload.frames} frames`);
    return;
  }
  if (kind === "warning") {
    toast(String(payload.message || ""), "warn");
    return;
  }
  if (kind === "error") {
    const message = String(payload.message || "the server refused the preview");
    if (live.state !== "live") {
      liveFailed(message);
      return;
    }
    toast(`Live preview — ${message}`, "err");
    return;
  }
  if (kind === "end") {
    live.serverFrames = Number(payload.frames) || 0;
    live.serverBlocks = Number(payload.blocks) || 0;
    const lost = Number(payload.dropped_frames) || 0;
    live.dropped += lost;
    liveLog(
      `live preview: the stream ended — the client received ${live.frames} frames ` +
        `in ${live.blocks} blocks, the server sent ${live.serverFrames} frames in ` +
        `${live.serverBlocks} blocks, ${lost} frames were dropped by the server ` +
        `(${live.dropouts} dropout notice(s), ${live.gaps} hole(s) in the client)`
    );
    liveEnd();
    return;
  }
  if (kind === "stopped") {
    liveLog("live preview: the server stopped the stream");
  }
}

/**
 * Handle one message of the server.
 *
 * @param {MessageEvent} event the message
 */
function liveOnMessage(event) {
  const data = event && event.data;
  if (typeof data === "string") {
    let payload = null;
    try {
      payload = JSON.parse(data);
    } catch (err) {
      return;
    }
    if (payload && typeof payload === "object") liveOnControl(payload);
    return;
  }
  if (data && typeof ArrayBuffer !== "undefined" && data instanceof ArrayBuffer) {
    liveOnAudio(data);
  }
}

/**
 * Open the socket and start the stream.
 *
 * @returns {Promise<void>}
 */
async function startLivePreview() {
  if (!state.asset) {
    toast("Open an audio file before previewing", "warn");
    return;
  }
  /* The audio context is opened here, in the gesture of the user: a context
     that is created later stays suspended, and a suspended context plays
     nothing. */
  if (!liveGraph()) {
    liveFailed("WebAudio is not available in this browser");
    return;
  }
  stopPreview();
  live.state = "connecting";
  setEngine("busy");
  const url = liveSocketUrl();
  let socket = null;
  try {
    socket = new WebSocket(url);
  } catch (err) {
    liveFailed(err && err.message ? err.message : String(err));
    return;
  }
  socket.binaryType = "arraybuffer";
  live.socket = socket;
  socket.onopen = () => {
    const start = Math.max(0, state.cursor.time || 0);
    const chain = liveChainPayload();
    const sent = liveSend({
      type: "configure",
      asset_id: state.asset.asset_id,
      chain,
      start,
      end: start + LIVE_SECONDS,
      realtime: true,
      /* the path the preview shall use: the fast one, or the full one when
         the user asked for it */
      profile: state.previewProfile,
    });
    if (!sent) {
      liveFailed("the socket closed before the chain was sent");
      return;
    }
    liveLog(
      `live preview: the chain (${chain.length} module(s)) was sent to ${url}, ` +
        `the stream starts at ${start.toFixed(2)} s on the "${state.previewProfile}" path`
    );
  };
  socket.onmessage = (event) => liveOnMessage(event);
  socket.onerror = () => {
    if (live.state === "connecting" || live.state === "live") {
      liveFailed("the socket reported an error");
    }
  };
  socket.onclose = () => {
    if (live.state === "connecting") {
      liveFailed("the server closed the socket before the stream started");
    }
  };
}

/**
 * The live preview could not be started.
 *
 * The user is told that the live preview is not available and the old path
 * takes over: a window of the chain is rendered over HTTP and played.  The
 * transport keeps working either way.
 *
 * @param {string} reason why the preview could not be started
 */
function liveFailed(reason) {
  const detail = String(reason || "");
  live.failures += 1;
  liveCloseSocket();
  liveStopLevelWatch();
  const message = t("transport.live.unavailable", "The live preview is not available");
  toast(detail ? `${message} — ${detail}` : message, "warn", 4200);
  liveLog(`live preview: not available — ${detail}`);
  setEngine(state.asset ? "ready" : "idle");
  /* the old path takes over */
  previewFile();
  live.state = "failed";
}

/**
 * Close the socket of the preview.
 */
function liveCloseSocket() {
  const socket = live.socket;
  live.socket = null;
  if (!socket) return;
  socket.onopen = null;
  socket.onmessage = null;
  socket.onerror = null;
  socket.onclose = null;
  try {
    socket.close(1000, "the preview was stopped");
  } catch (err) {
    /* the socket is gone anyway */
  }
}

/**
 * Stop the live preview: the socket is closed and the playback is stopped.
 */
function stopLivePreview() {
  if (live.socket) liveSend({ type: "stop" });
  liveCloseSocket();
  liveStopLevelWatch();
  for (const source of live.sources) {
    try {
      source.onended = null;
      source.stop();
    } catch (err) {
      /* already stopped */
    }
  }
  live.sources.clear();
  live.nextTime = 0;
  live.audibleAt = 0;
  live.pending = null;
  live.state = "off";
}

/**
 * The stream is over: close the socket and let the audio run out.
 */
function liveEnd() {
  const again = state.loop;
  liveCloseSocket();
  liveStopLevelWatch();
  live.nextTime = 0;
  live.pending = null;
  live.state = "off";
  if (again) {
    liveLog("live preview: the loop is on — the stream starts again");
    window.setTimeout(() => {
      if (!state.asset) return;
      startLivePreview();
    }, 150);
    return;
  }
  state.playing = false;
  if (dom.btnPlay) dom.btnPlay.classList.remove("on");
  setEngine(state.asset ? "ready" : "idle");
  if (pathReport) pathReport.stop();
}

/**
 * Send the chain to the running preview.
 *
 * The chain is sent as a whole: the server builds an engine of its own out
 * of it and cross-fades the new chain in, so the change cannot click.
 */
function pushLiveChain() {
  if (!live.socket) return;
  const chain = liveChainPayload();
  if (!liveSend({ type: "configure", chain })) return;
  liveLog(`live preview: the chain (${chain.length} module(s)) was sent`);
}

/**
 * Send one parameter change to the running preview.
 *
 * The change goes over the socket first: it is the short way to the audio.
 *
 * @param {number} index the position of the module in the chain
 * @param {string} name  the name of the parameter
 * @param {*} value      the new value
 * @returns {boolean} true when the change was sent
 */
function pushLiveParam(index, name, value) {
  if (!live.socket) return false;
  const at = performance.now();
  if (!liveSend({ type: "param", index, name, value })) return false;
  live.pending = {
    at,
    name: String(name || ""),
    value,
    baseDb: live.levelDb,
    serverMs: 0,
    audibleMs: 0,
    measuredMs: 0,
  };
  return true;
}

/**
 * Watch the level of the output.
 *
 * The watch tells when the sound changed: the first level that differs from
 * the level before the change by more than the threshold is the moment the
 * new setting was heard.  The resolution of the measurement is the window of
 * the analyser plus the period of the watch.
 */
function liveStartLevelWatch() {
  if (live.levelTimer) return;
  live.levelTimer = window.setInterval(liveMeasureLevel, LIVE_POLL_MS);
}

/** Stop the watch of the level. */
function liveStopLevelWatch() {
  if (!live.levelTimer) return;
  window.clearInterval(live.levelTimer);
  live.levelTimer = 0;
}

/**
 * Measure the level of the output and report the moment the sound changed.
 */
function liveMeasureLevel() {
  const analyser = live.analyser;
  if (!analyser) return;
  const data = new Float32Array(analyser.fftSize);
  try {
    analyser.getFloatTimeDomainData(data);
  } catch (err) {
    return;
  }
  let sum = 0;
  for (let i = 0; i < data.length; i++) sum += data[i] * data[i];
  const rms = Math.sqrt(sum / data.length);
  const db = rms > 0 ? 20 * Math.log10(rms) : -Infinity;
  live.levelDb = db;
  const pending = live.pending;
  if (!pending) return;
  if (!isFinite(pending.baseDb) || !isFinite(db)) {
    pending.baseDb = db;
    return;
  }
  if (Math.abs(db - pending.baseDb) < LIVE_LEVEL_STEP_DB) return;
  const measured = performance.now() - pending.at;
  pending.measuredMs = measured;
  liveLog(
    `live preview: the output moved from ${pending.baseDb.toFixed(1)} dBFS to ` +
      `${db.toFixed(1)} dBFS ${measured.toFixed(1)} ms after the change ` +
      `(the window of the meter is ${((LIVE_FFT / live.sampleRate) * 1000).toFixed(1)} ms)`
  );
  live.pending = null;
}

/**
 * The page is going away: the socket of the preview is closed.
 *
 * A reload or a closed tab must not leave a stream running on the server.
 */
function liveOnUnload() {
  if (!live.socket) return;
  liveLog("live preview: the socket is closed because the page is unloaded");
  liveSend({ type: "stop" });
  liveCloseSocket();
}

/**
 * Create the control that switches the live preview on and off.
 *
 * The shell builds the control, so it does not depend on the markup of the
 * document; it is a button of the transport and it carries a name of its own.
 *
 * @returns {Element|null} the button
 */
function ensureLiveButton() {
  if (dom.btnLive) return dom.btnLive;
  const transport = document.querySelector(".transport");
  if (!transport) return null;
  const button = el("button", {
    class: "tbtn tbtn-icon",
    type: "button",
    title: t("transport.live.title", "Live preview over the streaming socket"),
    "aria-label": t("transport.live", "Live preview"),
    "aria-pressed": live.mode === "live" ? "true" : "false",
    "data-i18n-title": "transport.live.title",
    "data-i18n-aria": "transport.live",
    text: "●",
  });
  button.classList.toggle("on", live.mode === "live");
  button.addEventListener("click", () => {
    live.mode = live.mode === "live" ? "file" : "live";
    const on = live.mode === "live";
    button.setAttribute("aria-pressed", on ? "true" : "false");
    button.classList.toggle("on", on);
    toast(
      on
        ? t("transport.live.on", "Live preview on")
        : t("transport.live.off", "Live preview off"),
      "info",
      1600
    );
    if (!on) stopLivePreview();
  });
  if (dom.btnPlay && dom.btnPlay.parentNode === transport) {
    transport.insertBefore(button, dom.btnPlay.nextSibling);
  } else {
    transport.appendChild(button);
  }
  dom.btnLive = button;
  return button;
}

/* ------------------------------------------------------------- preview */

function getAudioContext() {
  const Ctor = window.AudioContext || window.webkitAudioContext;
  if (!Ctor) throw new Error("WebAudio is not available in this browser");
  if (!audioCtx) audioCtx = new Ctor();
  if (audioCtx.state === "suspended") audioCtx.resume().catch(() => {});
  return audioCtx;
}

function stopPreview() {
  stopLivePreview();
  if (previewSource) {
    const src = previewSource;
    previewSource = null;
    try {
      src.onended = null;
      src.stop();
    } catch (err) {
      /* already stopped */
    }
    try {
      src.disconnect();
    } catch (err) {
      /* ignore */
    }
  }
  state.playing = false;
  if (dom.btnPlay) dom.btnPlay.classList.remove("on");
  setEngine(state.asset ? "ready" : "idle");
  if (pathReport) pathReport.stop();
}

function playWav(wav) {
  const ctx = getAudioContext();
  const buffer = ctx.createBuffer(wav.channels.length, wav.frames, wav.sampleRate);
  for (let c = 0; c < wav.channels.length; c++) buffer.copyToChannel(wav.channels[c], c);

  const src = ctx.createBufferSource();
  src.buffer = buffer;
  src.connect(ctx.destination);
  previewSource = src;
  state.playing = true;
  dom.btnPlay.classList.add("on");

  src.onended = () => {
    if (previewSource !== src) return;
    previewSource = null;
    state.playing = false;
    dom.btnPlay.classList.remove("on");
    if (state.loop) {
      try {
        playWav(wav);
      } catch (err) {
        setEngine("error");
      }
    } else {
      setEngine(state.asset ? "ready" : "idle");
      if (pathReport) pathReport.stop();
    }
  };
  src.start(0, 0, PREVIEW_SECONDS);
}

/**
 * Start the preview.
 *
 * The live preview is the default: the chain is streamed while the parameters
 * change.  When the live preview is switched off — or when the socket cannot
 * be opened — the old path takes over: a window of the chain is rendered over
 * HTTP and played.
 *
 * @returns {Promise<void>}
 */
async function preview() {
  liveLog(`preview: the play button was pressed (playing=${state.playing}, mode=${live.mode})`);
  if (state.playing) {
    stopPreview();
    return;
  }
  if (!state.asset) {
    toast("Open an audio file before previewing", "warn");
    return;
  }
  /* The audio context is opened in the gesture of the user: a context that is
     created after the render stays suspended, and a suspended context plays
     nothing. */
  try {
    getAudioContext();
  } catch (err) {
    toast(`Preview failed — ${err.message}`, "err");
    setEngine("error");
    return;
  }
  if (live.mode === "live") {
    await startLivePreview();
    return;
  }
  await previewFile();
}

/**
 * The old path: a window of the chain is rendered over HTTP and played.
 *
 * @returns {Promise<void>}
 */
async function previewFile() {
  stopPreview();
  setEngine("rendering");
  const start = Math.max(0, state.cursor.time || 0);
  const end = start + PREVIEW_SECONDS;
  try {
    let res;
    try {
      res = await api.render({
        asset_id: state.asset.asset_id,
        chain: state.chain,
        start,
        end,
      });
    } catch (err) {
      // The server may not accept a client-side chain; fall back to the
      // chain that is already loaded in the engine.
      res = await api.render({ asset_id: state.asset.asset_id, start, end });
    }
    const assetId = (res && res.asset_id) || "";
    const url = (res && res.url) || api.audioUrl(assetId);
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    const wav = decodeWav(await response.arrayBuffer());
    if (!wav.frames) throw new Error("preview buffer is empty");
    state.levelText = `${fmtDb(peakDb(wav.channels))}FS`;
    dom.stLevel.textContent = state.levelText;
    /* the old path plays a window of the file render: the audio is the file
       configuration, so the status bar says so — and the server reports no
       latency for that path, so the readout stays empty */
    if (pathReport) pathReport.setRender();
    setPreviewLatency(null);
    playWav(wav);
    setEngine("playing");
  } catch (err) {
    toast(`Preview failed — ${err.message}`, "err");
    setEngine("error");
  }
}

/* -------------------------------------------------------------- render */

async function renderFull() {
  if (!state.asset) {
    toast("Open an audio file before rendering", "warn");
    return;
  }
  setEngine("rendering");
  try {
    let res;
    const learned = learnedStageName();
    try {
      res = await api.render({
        asset_id: state.asset.asset_id,
        chain: state.chain,
        start: 0,
        end: 0,
        learned,
      });
    } catch (err) {
      res = await api.render({ asset_id: state.asset.asset_id, start: 0, end: 0 });
    }
    const assetId = (res && res.asset_id) || "";
    const url = (res && res.url) || api.audioUrl(assetId);
    const filename = `${(state.asset.name || "output").replace(/\.[^.]+$/, "")}-auralis.wav`;
    const audio = el("audio", { src: url, controls: true, preload: "metadata" });
    const download = el("a", { class: "tbtn tbtn-primary", href: url, download: filename }, "Download WAV");
    openModal({
      title: t("dialog.renderComplete", "Render complete"),
      body: [
        audio,
        el(
          "p",
          { class: "modal-note" },
          "Rendered from ",
          el("code", {}, state.chain.length ? `${state.chain.length} module(s)` : "an empty chain"),
          " — ",
          el("code", {}, filename)
        ),
      ],
      foot: [
        download,
        el("button", { class: "tbtn", type: "button", onclick: closeModal }, t("control.close", "Close")),
      ],
    });
    setEngine("ready");
    toast("Render complete", "info");
  } catch (err) {
    toast(`Render failed — ${err.message}`, "err");
    setEngine("error");
  }
}

async function compareChains() {
  if (!state.asset) {
    toast("Open an audio file before comparing", "warn");
    return;
  }
  setEngine("busy");
  try {
    const res = await api.compare({
      asset_id: state.asset.asset_id,
      chain_a: state.chain,
      chain_b: [],
    });
    const a = (res && res.a) || "";
    const b = (res && res.b) || "";
    openModal({
      title: t("dialog.compareChains", "Compare chains"),
      body: [
        el("div", { class: "insp-title" }, t("dialog.processed", "Processed (current chain)")),
        el("audio", { src: a, controls: true, preload: "metadata" }),
        el("div", { class: "insp-title" }, t("dialog.original", "Original (dry)")),
        el("audio", { src: b, controls: true, preload: "metadata" }),
      ],
      foot: [
        el("button", { class: "tbtn", type: "button", onclick: closeModal }, t("control.close", "Close")),
      ],
    });
    setEngine("ready");
  } catch (err) {
    toast(`Compare failed — ${err.message}`, "err");
    setEngine("error");
  }
}

/* ----------------------------------------------------------- view wiring */

function createView() {
  if (typeof CompositeView !== "function") {
    console.warn("[auralis] the composite view is unavailable");
    return null;
  }
  try {
    return new CompositeView(dom.specCanvas, dom.waveCanvas, dom.canvasWrap);
  } catch (err) {
    console.warn("[auralis] CompositeView construction failed", err);
    return null;
  }
}

/**
 * The view reports every pointer gesture through a single callback.
 * payload = { type: "hover"|"drag"|"click", t, f, x, y, zoom, selection }
 */
function bindView(v) {
  if (!v) return;
  const onPointer = (payload) => {
    const p = payload || {};
    if (p.zoom !== undefined && p.zoom !== null) setZoomLabel(p.zoom);
    if (p.selection !== undefined) setSelectionReadout(p.selection);
    if (p.t === null || p.t === undefined) {
      if (p.type === "hover") setCursorReadout(0, 0, false);
      return;
    }
    setCursorReadout(Number(p.t) || 0, Number(p.f) || 0, true);
  };
  if (typeof v.onPointer === "function") {
    v.onPointer(onPointer);
  } else {
    v.onCursor = onPointer;
  }

  if (dom.btnContrast) {
    dom.btnContrast.addEventListener("click", () => {
      const high = document.documentElement.getAttribute("data-contrast") !== "high";
      applyContrast(high);
      storeContrast(high ? "high" : "normal");
    });
  }
}

/** Keep the zoom / scroll readouts in sync with the view (it zooms on wheel). */
function startViewPolling() {
  if (pollTimer) window.clearInterval(pollTimer);
  pollTimer = window.setInterval(() => {
    if (!view) return;
    if (typeof view.zoom === "number") setZoomLabel(view.zoom);
    if (typeof view.scroll === "number" && Math.abs(view.scroll - state.scroll) > 1e-6) {
      state.scroll = view.scroll;
    }
    /* The spectral editor follows the spectrogram. */
    if (spectralEditor) renderSpectralEditor();
  }, POLL_MS);
}

/* --------------------------------------------------------- static wiring */

function wireToolbar() {
  dom.btnZoomIn.addEventListener("click", () => setZoom(state.zoom * 1.25));
  dom.btnZoomOut.addEventListener("click", () => setZoom(state.zoom / 1.25));
  dom.btnZoomFit.addEventListener("click", () => {
    setZoom(1);
    setScroll(0);
  });

  const applyFreq = () => {
    let lo = Number(dom.freqLo.value) || 0;
    let hi = Number(dom.freqHi.value) || 0;
    if (hi < lo) {
      const swap = lo;
      lo = hi;
      hi = swap;
    }
    state.frequencyRange = [lo, hi];
    if (view && typeof view.setFrequencyRange === "function") {
      view.setFrequencyRange(lo, hi);
    }
  };
  dom.freqLo.addEventListener("change", applyFreq);
  dom.freqHi.addEventListener("change", applyFreq);

  /* The view binds these checkboxes itself, so the DOM stays the single
     source of truth; we only mirror the values into the state. */
  dom.chkSelFreq.addEventListener("change", () => {
    state.selectionModes.freq = dom.chkSelFreq.checked;
  });
  dom.chkSelTime.addEventListener("change", () => {
    state.selectionModes.time = dom.chkSelTime.checked;
  });
  dom.chkLog.addEventListener("change", () => {
    state.logScale = dom.chkLog.checked;
  });

  for (const btn of dom.viewMode.querySelectorAll("button")) {
    btn.addEventListener("click", () => {
      const mode = btn.dataset.mode || "composite";
      state.viewMode = mode;
      for (const other of dom.viewMode.querySelectorAll("button")) {
        other.classList.toggle("on", other === btn);
      }
      if (view && typeof view.setViewMode === "function") view.setViewMode(mode);
    });
  }

  if (dom.btnSpectral) {
    dom.btnSpectral.addEventListener("click", () => toggleSpectralEditor());
    updateSpectralButton();
  }

  if (dom.localeSelect) {
    dom.localeSelect.addEventListener("change", (ev) => {
      const code = ev && ev.target ? ev.target.value : "";
      if (code) setLocale(code);
    });
  }
}

function wireTransport() {
  dom.btnOpen.addEventListener("click", () => dom.fileInput.click());
  dom.fileInput.addEventListener("change", () => {
    const file = dom.fileInput.files && dom.fileInput.files[0];
    dom.fileInput.value = "";
    if (file) openFile(file);
  });

  dom.btnPlay.addEventListener("click", () => preview());
  dom.btnStop.addEventListener("click", () => stopPreview());
  dom.btnLoop.addEventListener("click", () => {
    state.loop = !state.loop;
    dom.btnLoop.classList.toggle("on", state.loop);
    toast(
      state.loop
        ? t("transport.loop.on", "Loop preview on")
        : t("transport.loop.off", "Loop preview off"),
      "info",
      1400
    );
  });

  dom.btnRender.addEventListener("click", () => renderFull());
  dom.btnCompare.addEventListener("click", () => compareChains());

  dom.moduleSearch.addEventListener("input", (ev) => filterBrowser(ev.target.value));

  for (const btn of dom.inspectorTabs.querySelectorAll("button")) {
    btn.addEventListener("click", () => setTab(btn.dataset.tab || "panel"));
  }

  dom.modalClose.addEventListener("click", closeModal);
  dom.modal.addEventListener("mousedown", (ev) => {
    if (ev.target === dom.modal) closeModal();
  });
}

/* --------------------------------------------------------- the keyboard map */

/** The tabs the cycle command walks through, in the order of the tab bar. */
function cycleInspectorTab(delta = 1) {
  const index = INSPECTOR_TABS.indexOf(state.tab);
  const start = index < 0 ? 0 : index;
  const next = INSPECTOR_TABS[(start + delta + INSPECTOR_TABS.length) % INSPECTOR_TABS.length];
  setTab(next);
}

/** Select the module before or after the selected one. */
function selectRelativeModule(delta) {
  if (!state.chain.length) {
    toast(t("toast.selectFirst", "Select a module first"), "warn");
    return;
  }
  const current = state.selectedIndex;
  let next = current + delta;
  if (current < 0) next = delta > 0 ? 0 : state.chain.length - 1;
  next = Math.max(0, Math.min(state.chain.length - 1, next));
  selectModule(next);
}

/** Move the cursor into the search field of the module browser. */
function focusModuleSearch() {
  if (!dom.moduleSearch) return;
  dom.moduleSearch.focus();
  dom.moduleSearch.select();
}

/** Turn the frequency or the time selection on and off. */
function toggleSelectionMode(which) {
  const box = which === "freq" ? dom.chkSelFreq : dom.chkSelTime;
  if (!box) return;
  box.checked = !box.checked;
  try {
    box.dispatchEvent(new Event("change", { bubbles: true }));
  } catch (err) {
    /* the view reads the flag itself */
  }
  state.selectionModes[which] = box.checked;
}

/** Close the dialog and the help overlay. */
function closeDialogs() {
  closeModal();
  if (pathReport) pathReport.close();
  const mod = optional.keymap;
  if (mod && typeof mod.hideHelpOverlay === "function") {
    try {
      mod.hideHelpOverlay(document);
    } catch (err) {
      /* the overlay may not exist */
    }
  }
}

/** The shortcuts of the shell, shown when the keyboard map is missing. */
const LEGACY_SHORTCUTS = [
  ["Space", "Preview"],
  ["S", "Stop"],
  ["B", "Bypass the selected module"],
  ["Delete", "Remove the selected module"],
  ["+ / -", "Zoom in and out"],
  ["0", "Fit the file into the view"],
  ["[ / ]", "Previous and next module"],
  ["F / T", "Frequency and time selection"],
  ["/", "Focus the module search"],
  ["Ctrl+S", "Save the preset of the selected module"],
  ["Escape", "Close the dialog"],
];

/** Show or hide the list of the keyboard shortcuts. */
function toggleHelpOverlay() {
  const mod = optional.keymap;
  if (mod && typeof mod.toggleHelpOverlay === "function") {
    try {
      mod.toggleHelpOverlay(document);
      return;
    } catch (err) {
      console.warn("[auralis] the help overlay could not be shown", err);
    }
  }
  const list = el("div", { class: "insp-section" });
  for (const [key, description] of LEGACY_SHORTCUTS) {
    list.append(
      el(
        "div",
        { class: "kv" },
        el("span", {}, key),
        el("span", {}, description)
      )
    );
  }
  openModal({
    title: "Keyboard shortcuts",
    body: [el("p", { class: "modal-note" }, "The full keyboard map is unavailable."), list],
    foot: [el("button", { class: "tbtn", type: "button", onclick: closeModal }, t("control.close", "Close"))],
  });
}

/**
 * Move the selected module one place in the chain.
 *
 * The order of the chain is the signal flow, so the keyboard has to be able
 * to change it: the drag of a chip is not the only way.
 *
 * @param {number} delta  -1 for one place back, +1 for one place forward
 */
function moveSelectedModule(delta) {
  const index = state.selectedIndex;
  if (index < 0 || index >= state.chain.length) {
    toast(t("toast.selectFirst", "Select a module first"), "warn");
    return;
  }
  const to = index + delta;
  if (to < 0 || to >= state.chain.length) return;
  reorderModule(index, to);
}

/**
 * Add the module the browser shows to the chain.
 *
 * The row of the browser is a button, so the browser itself adds the module
 * when the key is pressed.  The command of the map cancels that activation
 * and does the work here, so the module is added exactly once.
 */
function addHighlightedModule() {
  const row = document.activeElement;
  const code = row && row.dataset ? String(row.dataset.code || "") : "";
  if (!code) {
    toast(t("toast.selectFirst", "Select a module first"), "warn");
    return;
  }
  addModule(code);
}

/**
 * Run an action of the session controller and report a failure.
 *
 * @param {string} name  the name of the action ("undo", "redo")
 */
function runSessionAction(name) {
  if (!sessionController || typeof sessionController[name] !== "function") return;
  const failed = (err) => {
    const message = err && err.message ? err.message : String(err);
    if (sessionController && typeof sessionController.notifyError === "function") {
      sessionController.notifyError(err);
    } else {
      console.warn("[auralis] the session action failed", err);
    }
    toast(t("toast.actionFailed", "The action failed") + " \u2014 " + message, "err");
  };
  try {
    const result = sessionController[name]();
    if (result && typeof result.catch === "function") result.catch(failed);
  } catch (err) {
    failed(err);
  }
}

/**
 * The commands of the keyboard map.
 *
 * The ids are the ones of js/keymap.js; every command is bound to the action
 * of the shell that carries the same meaning.
 *
 * @returns {object} a map of command id -> function
 */
function keymapCommands() {
  return {
    "transport.preview": () => preview(),
    "transport.stop": () => stopPreview(),
    "transport.previewPath": () => togglePreviewPath(),
    "module.bypass": () => bypassSelected(),
    "module.remove": () => removeModule(state.selectedIndex),
    "chain.selectPrev": () => selectRelativeModule(-1),
    "chain.selectNext": () => selectRelativeModule(1),
    "chain.movePrev": () => moveSelectedModule(-1),
    "chain.moveNext": () => moveSelectedModule(1),
    "preset.save": () => openPresetDialog("save"),
    "dialog.close": () => closeDialogs(),
    "help.toggle": () => toggleHelpOverlay(),
    "browser.focusSearch": () => focusModuleSearch(),
    "view.zoomIn": () => setZoom(state.zoom * 1.25),
    "view.zoomOut": () => setZoom(state.zoom / 1.25),
    "view.fit": () => {
      setZoom(1);
      setScroll(0);
    },
    "view.reset": () => {
      setZoom(1);
      setScroll(0);
      clearSelection();
    },
    "view.toggleFreqSelection": () => toggleSelectionMode("freq"),
    "view.toggleTimeSelection": () => toggleSelectionMode("time"),
    "inspector.cycleTabs": () => cycleInspectorTab(1),
    "panel.presetMenu": () => openPresetMenu(),
    "browser.addModule": () => addHighlightedModule(),
    "session.undo": () => runSessionAction("undo"),
    "session.redo": () => runSessionAction("redo"),
  };
}

/**
 * Install the keyboard map of js/keymap.js.
 *
 * @returns {boolean} true when the map was installed
 */
function installKeymapBindings() {
  const mod = optional.keymap;
  if (!mod || typeof mod.installKeymap !== "function") {
    console.warn("[auralis] the keyboard map is unavailable — the built-in shortcuts are used");
    return false;
  }
  try {
    mod.installKeymap(keymapCommands());
  } catch (err) {
    console.warn("[auralis] the keyboard map could not be installed", err);
    return false;
  }
  if (typeof mod.applyAccessibility === "function") {
    try {
      mod.applyAccessibility(document);
    } catch (err) {
      console.warn("[auralis] the accessibility names could not be applied", err);
    }
  }
  return true;
}

/**
 * The built-in keyboard handling.
 *
 * It is only used while the keyboard map has not loaded yet — and for ever
 * when js/keymap.js is missing.
 *
 * @returns {Function} a function that removes the handler again
 */
function wireKeyboard() {
  const onKeyDown = (ev) => {
    const target = ev.target;
    const typing =
      target &&
      (target.tagName === "INPUT" ||
        target.tagName === "TEXTAREA" ||
        target.tagName === "SELECT" ||
        target.isContentEditable);

    if (ev.key === "Escape") {
      if (!dom.modal.classList.contains("hidden")) {
        ev.preventDefault();
        closeDialogs();
      } else if (typing) {
        target.blur();
      }
      return;
    }
    if (typing || ev.ctrlKey || ev.metaKey || ev.altKey) return;

    switch (ev.key) {
      case " ":
      case "Spacebar":
        ev.preventDefault();
        preview();
        break;
      case "b":
      case "B":
        ev.preventDefault();
        bypassSelected();
        break;
      case "Delete":
      case "Backspace":
        ev.preventDefault();
        removeModule(state.selectedIndex);
        break;
      case "+":
      case "=":
        ev.preventDefault();
        setZoom(state.zoom * 1.25);
        break;
      case "-":
      case "_":
        ev.preventDefault();
        setZoom(state.zoom / 1.25);
        break;
      case "/":
        ev.preventDefault();
        focusModuleSearch();
        break;
      default:
        break;
    }
  };
  window.addEventListener("keydown", onKeyDown);
  return () => window.removeEventListener("keydown", onKeyDown);
}

function bypassSelected() {
  const index = state.selectedIndex;
  if (index < 0 || index >= state.chain.length) {
    toast(t("toast.selectFirst", "Select a module first"), "warn");
    return;
  }
  const entry = normaliseEntry(state.chain[index], index);
  toggleBypass(index, !entry.bypass);
}

/** Remove the selection of the composite view. */
function clearSelection() {
  state.selection = null;
  if (view && typeof view.clearSelection === "function") {
    try {
      view.clearSelection();
    } catch (err) {
      console.warn("[auralis] the selection could not be cleared", err);
    }
  }
  setSelectionReadout(null);
}

/**
 * The menu of the selected module.
 *
 * It carries the actions of the "⋯" button of the module panel: the presets
 * of the module, the comparison with the original and its removal.
 */
function openModuleMenu() {
  const index = state.selectedIndex;
  if (index < 0 || index >= state.chain.length) {
    toast(t("toast.selectFirst", "Select a module first"), "warn");
    return;
  }
  const entry = normaliseEntry(state.chain[index], index);
  const actions = [
    { label: t("control.savePreset", "Save Preset…"), run: () => openPresetDialog("save") },
    { label: t("control.loadPreset", "Load Preset…"), run: () => openPresetDialog("load") },
    { label: t("control.compare", "Compare"), run: () => compareChains() },
    { label: t("control.removeModule", "Remove Module"), run: () => removeModule(index) },
  ];
  const list = el("div", { class: "insp-section" });
  for (const action of actions) {
    const button = el("button", { class: "tbtn tbtn-sm", type: "button" }, action.label);
    button.addEventListener("click", () => {
      closeModal();
      action.run();
    });
    list.append(el("div", { class: "insp-section" }, button));
  }
  openModal({
    title: entry.display_name,
    body: [el("p", { class: "modal-note" }, entry.code), list],
    foot: [
      el("button", { class: "tbtn", type: "button", onclick: closeModal }, t("control.close", "Close")),
    ],
  });
}

/* ----------------------------------------------------------------- boot */

/**
 * Start the workbench.
 *
 * Every import is optional, so the shell first loads the modules it needs
 * and then paints what it has: a missing file costs its own feature and is
 * reported in the console and in a message, never the page.
 *
 * @returns {Promise<void>}
 */
async function boot() {
  injectShellStyle();

  /* The api client and the presentation modules come first: the rest of the
     shell is built on top of them. */
  await loadCoreModules();
  resolveDom();

  if (!dom.app || !dom.inspector || !dom.topbar) {
    console.warn("[auralis] the page does not carry the workbench — nothing to wire");
    return;
  }
  if (!hasApi()) {
    toast("The server client is unavailable — the workbench is read only", "err");
  }

  /* The tab of the preset library is added before the wiring, so that it is
     bound like the tabs of the markup. */
  addPresetTab();

  wireToolbar();
  wireTransport();
  /* The control of the live preview belongs to the transport; the shell
     builds it, so it does not depend on the markup of the document. */
  ensureLiveButton();
  /* The built-in shortcuts work from the first moment; the keyboard map
     takes over as soon as js/keymap.js has loaded. */
  removeLegacyKeyboard = wireKeyboard();
  ensureChainStrip();
  initContrast();
  /* The learned stage of a render: the models of the library are read once,
     and the control is filled from them.  A library that cannot be read
     leaves the control empty, which means "no stage". */
  buildLearnedSelector();

  /* The diagnostics of the preview: the console lines and the counters of
     the stream.  The measurement harness reads them from the page. */
  try {
    window.auralis = window.auralis || {};
    window.auralis.preview = live;
    window.auralis.state = state;
  } catch (err) {
    /* the diagnostics are a nicety, never a requirement */
  }
  /* A reload or a closed tab must not leave a stream running on the server. */
  window.addEventListener("pagehide", liveOnUnload);
  window.addEventListener("beforeunload", liveOnUnload);

  state.selectionModes = {
    freq: !!(dom.chkSelFreq && dom.chkSelFreq.checked),
    time: !!(dom.chkSelTime && dom.chkSelTime.checked),
  };
  state.logScale = !!(dom.chkLog && dom.chkLog.checked);

  view = createView();
  bindView(view);
  startViewPolling();
  if (!view) toast("Spectrogram view unavailable", "err");

  /* The optional modules load in the background: the workbench is usable
     while they are on their way. */
  loadOptionalModules();

  setEngine("busy");
  if (!hasApi()) {
    setEngine("error");
    renderChainStrip();
    renderInspector();
    return;
  }
  try {
    const res = await api.modules();
    state.modules = Array.isArray(res && res.modules) ? res.modules : [];
    renderBrowserList();
  } catch (err) {
    toast(`Module list unavailable — ${err.message}`, "err");
    setEngine("error");
  }

  try {
    const res = await api.chain();
    state.chain = Array.isArray(res && res.chain) ? res.chain : [];
  } catch (err) {
    state.chain = [];
  }
  renderChainStrip();
  renderInspector();
  setEngine("idle");
  setZoom(1);
  setScroll(0);
  setCursorReadout(0, 0, false);
  setSelectionReadout(null);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot, { once: true });
} else {
  boot();
}
