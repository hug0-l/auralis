/* Auralis — preset library manager.
 *
 * The manager owns the preset library of the server
 *
 *   GET    /api/presets        every stored preset
 *   POST   /api/preset/save    store one preset
 *   POST   /api/preset/load    read one preset
 *   DELETE /api/preset         delete one preset
 *
 * and renders it: a search field, a "save as new" form and the presets
 * grouped by category.  Every row shows the name of the preset, the code of
 * the module it belongs to and the time the file was last written.
 *
 * The search finds a preset by its name and by the code of its module; the
 * hit is marked in the row, in the code and in the head of the group.
 *
 * The "..." menu of the module panel shows the state of the library: whether
 * the server answers, whether the library holds anything and whether the
 * preset on the panel has changes that were not saved.
 *
 * The module is a vanilla ES module without a build step.  It touches no DOM
 * at import time, so a head-less harness can import it and drive it with a
 * stub api.
 *
 * Contract notes
 *  - Every colour, size and spacing value comes from css/tokens.css; this
 *    file never hard-codes a colour.
 *  - No third-party product or vendor names appear anywhere in this file.
 *
 * Public API
 *   createPresetManager({ api, container, onLoad }) -> manager
 *   renderPresetMenu(container, manager, handlers)   -> menu
 */

import { el } from "./api.js";
import { t, onLocaleChange } from "./i18n.js";

/* ------------------------------------------------------------------ setup */

/** Id of the stylesheet this module injects into the document. */
const STYLE_ID = "aur-preset-manager-style";

/** Id of the datalist that offers the categories of the library. */
const CATEGORY_LIST_ID = "aur-preset-categories";

/** The category a preset lands in when the user does not name one. */
const DEFAULT_CATEGORY = "Presets";

/** The categories of the library that keep their place in the list. */
const CATEGORY_ORDER = ["Repair", "Utility", "Measurement", "System"];

/** The names the file system refuses; the server rejects them as well. */
const RESERVED_NAMES = new Set([
  "CON", "PRN", "AUX", "NUL",
  ...Array.from({ length: 9 }, (_, i) => `COM${i + 1}`),
  ...Array.from({ length: 9 }, (_, i) => `LPT${i + 1}`),
]);

/* ------------------------------------------------------------------ styles */

const CSS = `
.pm-root { display: flex; flex-direction: column; gap: var(--sp-2); height: 100%; min-height: 0; }
.pm-search {
  width: 100%; box-sizing: border-box; background: var(--bg-3); border: 1px solid var(--line);
  border-radius: var(--radius); color: var(--txt-0); font-family: var(--font-ui);
  font-size: var(--fs-sm); padding: var(--sp-2) var(--sp-3); outline: none;
}
.pm-search:focus { border-color: var(--sel-edge); }
.pm-search::placeholder { color: var(--txt-2); }
.pm-form { display: flex; align-items: center; gap: var(--sp-2); }
.pm-input {
  flex: 1 1 auto; min-width: 0; box-sizing: border-box; background: var(--bg-3);
  border: 1px solid var(--line); border-radius: var(--radius); color: var(--txt-0);
  font-family: var(--font-ui); font-size: var(--fs-sm); padding: var(--sp-2) var(--sp-3); outline: none;
}
.pm-input:focus { border-color: var(--sel-edge); }
.pm-input::placeholder { color: var(--txt-2); }
.pm-save {
  flex: 0 0 auto; height: 24px; padding: 0 var(--sp-3); background: var(--bg-3);
  border: 1px solid var(--line); border-radius: var(--radius); color: var(--txt-0);
  font-family: var(--font-ui); font-size: var(--fs-sm); cursor: pointer;
}
.pm-save:hover { background: var(--bg-4); }
.pm-save.armed { border-color: var(--accent); color: var(--accent); }
.pm-list { flex: 1 1 auto; min-height: 0; overflow-y: auto; overflow-x: hidden; }
.pm-section { margin-bottom: var(--sp-3); }
.pm-section-head {
  display: flex; align-items: center; gap: var(--sp-2); width: 100%; box-sizing: border-box;
  background: transparent; border: 0; border-bottom: 1px solid var(--line-soft); border-radius: 0;
  color: var(--txt-1); font-family: var(--font-ui); font-size: var(--fs-xs); letter-spacing: 0.08em;
  text-transform: uppercase; padding: var(--sp-2) var(--sp-1); cursor: pointer; text-align: left;
}
.pm-section-head:hover { color: var(--txt-0); }
.pm-section-head:focus-visible { outline: 1px solid var(--sel-edge); outline-offset: -1px; }
.pm-section[data-open="false"] .pm-items { display: none; }
.pm-section-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pm-count { margin-left: auto; color: var(--txt-2); font-family: var(--font-mono); font-size: var(--fs-xs); }
.pm-items { list-style: none; margin: 0; padding: 0; }
.pm-item {
  display: flex; align-items: center; gap: var(--sp-2); width: 100%; box-sizing: border-box;
  background: transparent; border: 0; border-radius: var(--radius); color: var(--txt-0);
  font-family: var(--font-ui); font-size: var(--fs-sm); padding: var(--sp-2); cursor: pointer; text-align: left;
}
.pm-item:hover { background: var(--bg-2); }
.pm-item:focus-visible { outline: 1px solid var(--sel-edge); outline-offset: -1px; background: var(--bg-3); }
.pm-name { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pm-code { flex: 0 0 auto; color: var(--txt-2); font-family: var(--font-mono); font-size: var(--fs-xs); }
.pm-time { flex: 0 0 auto; color: var(--txt-3); font-family: var(--font-mono); font-size: var(--fs-xs); }
.pm-hit { background: var(--sel); color: var(--txt-0); border-radius: 2px; padding: 0 1px; }
.pm-item[data-hit] { box-shadow: inset 2px 0 0 var(--sel-edge); }
.pm-empty { color: var(--txt-2); font-size: var(--fs-sm); padding: var(--sp-4) var(--sp-2); text-align: center; }
.pm-error { color: var(--danger); font-size: var(--fs-xs); padding: var(--sp-1) var(--sp-2); }
.pm-error.hidden { display: none; }
.pm-menu {
  position: absolute; right: 0; top: 100%; z-index: 20;
  min-width: 176px; padding: var(--sp-1);
  background: var(--bg-2); border: 1px solid var(--line);
  border-radius: var(--radius); box-shadow: var(--shadow-2);
}
.pm-menu.hidden { display: none; }
.pm-menu-state {
  color: var(--txt-2); font-family: var(--font-ui); font-size: var(--fs-xs);
  padding: var(--sp-1) var(--sp-3) var(--sp-2); white-space: normal;
}
.pm-menu-state:empty { display: none; }
.pm-menu-state.pm-offline { color: var(--danger); }
.pm-menu-state.pm-dirty { color: var(--accent-2); }
.pm-menu-item {
  display: block; width: 100%; text-align: left;
  height: 24px; padding: 0 var(--sp-3);
  border: 0; border-radius: var(--radius); background: transparent;
  color: var(--txt-1); font: 500 var(--fs-sm)/1 var(--font-ui); cursor: pointer;
}
.pm-menu-item:hover { background: var(--sel); color: var(--txt-0); }
.pm-menu-item.pm-danger { color: var(--danger); }
.pm-menu-item.pm-danger:hover { background: var(--danger); color: var(--txt-0); }
.pm-menu-item[disabled] { color: var(--txt-3); cursor: default; }
.pm-menu-item[disabled]:hover { background: transparent; color: var(--txt-3); }
.pm-menu-item.pm-dirty { color: var(--accent-2); }
.pm-menu-sep { height: 1px; margin: var(--sp-1) 0; background: var(--line); border: 0; }
`;

/* ----------------------------------------------------------------- helpers */

/** Resolve the document that owns a node; falls back to the global one. */
function ownerDoc(node) {
  if (node && node.ownerDocument) return node.ownerDocument;
  return typeof document !== "undefined" ? document : null;
}

/** Create an element in `doc`. */
function make(doc, tag, className, text) {
  const node = doc.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
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

/** Inject the component stylesheet once per document. */
function ensureStyle(doc) {
  if (!doc) return;
  const head = doc.head || (doc.getElementsByTagName ? doc.getElementsByTagName("head")[0] : null);
  if (!head) return;
  if (doc.getElementById && doc.getElementById(STYLE_ID)) return;
  const style = doc.createElement("style");
  style.setAttribute("id", STYLE_ID);
  style.textContent = CSS;
  head.appendChild(style);
}

/** The first of `names` that the api offers as a function. */
function pickMethod(api, names) {
  if (!api) return null;
  for (const name of names) {
    if (typeof api[name] === "function") return api[name].bind(api);
  }
  return null;
}

/**
 * Delete a preset when the api client offers no delete of its own.
 *
 * The server answers a DELETE of /api/preset with the body {path}; the
 * client of the shell does not offer that call yet, so the manager falls
 * back to the route itself.
 */
async function defaultDelete(path) {
  if (typeof fetch !== "function") {
    throw new Error(t("preset.errorUnavailable", "The preset library is not available."));
  }
  const res = await fetch("/api/preset", {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  let data = {};
  try {
    data = await res.json();
  } catch (_ignored) {
    /* the answer was not JSON */
  }
  if (!res.ok) throw new Error((data && data.error) || `${res.status}`);
  return data;
}

/** The first of the given values that is a function, or null. */
function pickFn(...values) {
  for (const value of values) if (typeof value === "function") return value;
  return null;
}

/**
 * The confirmation the manager asks before it deletes a preset.
 *
 * A host may pass its own function; otherwise the browser dialog is used
 * when the page offers one.
 */
function resolveConfirm(custom) {
  if (typeof custom === "function") return custom;
  if (typeof globalThis !== "undefined" && typeof globalThis.confirm === "function") {
    return (message) => globalThis.confirm(message);
  }
  return () => true;
}

/** Read a string out of a raw value or a typed-value envelope. */
function unwrapTyped(value, fallback = "") {
  if (value && typeof value === "object" && "Value" in value) return value.Value;
  if (value === undefined || value === null) return fallback;
  return value;
}

/**
 * True when a failure means the server could not be reached.
 *
 * The client of the shell throws a plain Error for an answer of the server
 * and the runtime throws a TypeError when the request never left the
 * browser, so the two cases are told apart by the kind of the failure and by
 * the words of its message.
 */
function isOfflineError(err) {
  if (!err) return false;
  if (String(err.name || "") === "TypeError") return true;
  const message = String(err.message || err).toLowerCase();
  return /failed to fetch|fetch failed|networkerror|network error|econnrefused|connection refused|load failed|unreachable/.test(
    message
  );
}

/** The parameters of a document, with the typed envelopes taken off. */
function unwrapParams(params) {
  const out = {};
  for (const [key, value] of Object.entries(params || {})) {
    out[key] = unwrapTyped(value, value);
  }
  return out;
}

/** True when two sets of parameters hold the same values. */
function sameParams(left, right) {
  const a = left || {};
  const b = right || {};
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  for (const key of keys) {
    if (a[key] !== b[key]) return false;
  }
  return true;
}

/** Normalise one record of the server listing into the fields the UI needs. */
function normalizePreset(raw) {
  const p = raw && typeof raw === "object" ? raw : {};
  const module = p.module && typeof p.module === "object" ? p.module : {};
  return {
    name: String(p.name || ""),
    category: String(p.category || DEFAULT_CATEGORY),
    path: String(p.path || ""),
    module_code: String(p.module_code || module.code || ""),
    display_name: String(p.display_name || module.display_name || ""),
    modified: Number(p.modified || 0),
    builtin: Boolean(p.builtin),
  };
}

/** The text a preset is searched by: its name, category and module code. */
function searchText(preset) {
  return [preset.name, preset.category, preset.module_code, preset.display_name]
    .join(" ")
    .toLowerCase();
}

/** The terms of a search text; every term has to match. */
function searchTerms(text) {
  return String(text || "")
    .trim()
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean);
}

/** The presets of `list` the search text leaves visible. */
function filterPresets(list, text) {
  const terms = searchTerms(text);
  if (!terms.length) return list.slice();
  return list.filter((preset) => {
    const haystack = searchText(preset);
    return terms.every((term) => haystack.includes(term));
  });
}

/**
 * Group presets by category.
 *
 * The categories of the specification keep their order, every other category
 * follows in alphabetical order; inside a group the presets are listed by
 * their name.
 */
function groupPresets(presets) {
  const buckets = new Map();
  for (const preset of presets) {
    const key = preset.category || DEFAULT_CATEGORY;
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(preset);
  }
  for (const items of buckets.values()) {
    items.sort((a, b) => {
      const x = a.name.toLowerCase();
      const y = b.name.toLowerCase();
      if (x < y) return -1;
      if (x > y) return 1;
      return a.name < b.name ? -1 : a.name > b.name ? 1 : 0;
    });
  }
  const known = [];
  const extra = [];
  for (const key of buckets.keys()) {
    if (CATEGORY_ORDER.includes(key)) known.push(key);
    else extra.push(key);
  }
  known.sort((a, b) => CATEGORY_ORDER.indexOf(a) - CATEGORY_ORDER.indexOf(b));
  extra.sort((a, b) => a.localeCompare(b));
  return known.concat(extra).map((category) => ({ category, presets: buckets.get(category) }));
}

/** The time a preset was written, as a short local date and time. */
export function formatModified(stamp) {
  const seconds = Number(stamp);
  if (!Number.isFinite(seconds) || seconds <= 0) return "—";
  const date = new Date(seconds * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

/** The time a preset was written, as an ISO stamp (the tooltip of a row). */
export function modifiedStamp(stamp) {
  const seconds = Number(stamp);
  if (!Number.isFinite(seconds) || seconds <= 0) return "";
  return new Date(seconds * 1000).toISOString();
}

/** The payload the server expects when a preset is written. */
export function buildSavePayload(input = {}, content = {}) {
  const src = Object.assign({}, content || {}, input || {});
  const payload = {
    name: String(src.name || "").trim(),
    category: String(src.category || DEFAULT_CATEGORY).trim() || DEFAULT_CATEGORY,
    module_code: String(src.module_code || "").trim(),
    params: src.params && typeof src.params === "object" ? src.params : {},
  };
  if (src.element) payload.element = String(src.element);
  if (src.display_name) payload.display_name = String(src.display_name);
  if (src.description) payload.description = String(src.description);
  if (src.state !== undefined && src.state !== null) payload.state = src.state;
  return payload;
}

/** Turn a preset document of the server into the payload of a save. */
export function documentToPayload(doc) {
  const d = doc && typeof doc === "object" ? doc : {};
  const module = d.module && typeof d.module === "object" ? d.module : {};
  const params = {};
  for (const [key, value] of Object.entries(d.params || {})) {
    params[key] = unwrapTyped(value, value);
  }
  const payload = {
    name: String(unwrapTyped(d.name, "")),
    category: String(unwrapTyped(d.category, DEFAULT_CATEGORY)) || DEFAULT_CATEGORY,
    module_code: String(module.code || d.module_code || ""),
    params,
  };
  if (module.element || d.element) payload.element = String(module.element || d.element);
  if (module.display_name || d.display_name) {
    payload.display_name = String(module.display_name || d.display_name);
  }
  const description = unwrapTyped(d.description, "");
  if (description) payload.description = String(description);
  if (d.state !== undefined && d.state !== null) payload.state = d.state;
  return payload;
}

/**
 * Check a payload against the rules of the library.
 *
 * The server enforces the same rules; the check here keeps a broken preset
 * from ever reaching the disk.
 */
export function validateSavePayload(payload) {
  const name = String((payload && payload.name) || "");
  const category = String((payload && payload.category) || "");
  if (!name) throw new Error(t("preset.errorName", "The preset needs a name."));
  if (!String((payload && payload.module_code) || "")) {
    throw new Error(t("preset.errorModule", "The preset needs a module — select one first."));
  }
  for (const [value, field] of [[name, "name"], [category, "category"]]) {
    if (/[\\/]/.test(value) || value.includes("..")) {
      throw new Error(
        t("preset.errorPath", "The preset {field} must not contain a path.", { field })
      );
    }
    if (value.startsWith(".")) {
      throw new Error(t("preset.errorDot", "The preset {field} must not start with a dot.", { field }));
    }
    if (/[<>:"*?|]/.test(value)) {
      throw new Error(
        t("preset.errorChars", "The preset {field} contains characters that are not allowed.", { field })
      );
    }
  }
  if (RESERVED_NAMES.has(name.toUpperCase())) {
    throw new Error(
      t("preset.errorReserved", "The preset name “{name}” is a reserved device name.", { name })
    );
  }
  return true;
}

/* ------------------------------------------------------------------ manager */

/**
 * Create the manager of the preset library.
 *
 * options
 *   api        the server client; it must offer `presets()`, `savePreset()`,
 *              `loadPreset()` and — for the delete — `deletePreset()`
 *   container  the element the library is rendered into (optional: without
 *              one the manager works head-less)
 *   onLoad     called with the document a load returned; the shell applies it
 *              to the chain
 *   onError    called with (error, context) when a call fails
 *   onChange   called with the list of presets whenever the library changes
 *   getContent returns the parameters of the current module for a save
 *   confirm    the confirmation shown before a preset is deleted
 *
 * The returned manager offers
 *   refresh()      re-read the library from the server
 *   list()         the presets the search field leaves visible
 *   categories()   the same presets grouped by category
 *   save(options)  "save as new" -> POST /api/preset/save
 *   load(path)     POST /api/preset/load -> onLoad(document)
 *   remove(path)   DELETE /api/preset, after a confirmation
 *   destroy()      release the DOM and the listeners
 * and, beyond that contract
 *   all()          every preset the server returned
 *   search(text)   set the filter of the search field
 *   current()      the preset the panel shows
 *   setCurrent(p)  tell the manager which preset the panel shows
 *   beginRename(p) fill the form with a preset so that saving renames it
 *   importDocument(d) store a preset document that came from a file
 *   exportCurrent()  the document of the current preset as JSON text
 *   state()          the state of the library, as the menu shows it
 *   isDirty()        the preset has changes that were not saved
 *   report(err)      show a failure of the host in the panel
 */
export function createPresetManager(options = {}) {
  const opts = options && typeof options === "object" ? options : {};
  const api = opts.api || null;
  const container = opts.container || null;
  const onLoad = pickFn(opts.onLoad);
  const onError = pickFn(opts.onError);
  const onChange = pickFn(opts.onChange);
  const getContent = pickFn(opts.getContent);
  const confirmFn = resolveConfirm(opts.confirm);

  const listFn = pickMethod(api, ["presets", "listPresets", "list"]);
  const saveFn = pickMethod(api, ["savePreset", "save"]);
  const loadFn = pickMethod(api, ["loadPreset", "load"]);
  const deleteFn =
    pickMethod(api, ["deletePreset", "removePreset", "delete"]) || (api ? defaultDelete : null);

  /** Every preset the server returned. */
  let presets = [];
  /** The text of the search field. */
  let query = "";
  /** The preset the panel shows. */
  let current = null;
  /** The document of the last preset that was loaded or written. */
  let currentDocument = null;
  /** The path the form renames, or null while the form creates a preset. */
  let renameFrom = null;
  /** The categories the user folded away. */
  const collapsed = new Set();
  /** The message of the last failure, or null while nothing failed. */
  let lastError = null;
  /** True while the server answers. */
  let online = true;

  const doc = ownerDoc(container);
  let root = null;
  let searchField = null;
  let nameField = null;
  let categoryField = null;
  let saveButton = null;
  let categoryList = null;
  let listEl = null;
  let errorEl = null;

  /* ---- the DOM -------------------------------------------------------- */

  if (container && doc && typeof doc.createElement === "function") {
    ensureStyle(doc);
    root = make(doc, "div", "pm-root");

    searchField = make(doc, "input", "pm-search");
    searchField.setAttribute("type", "search");
    searchField.setAttribute("placeholder", t("preset.search", "Search presets"));
    searchField.setAttribute("aria-label", t("preset.search", "Search presets"));
    searchField.value = query;
    searchField.addEventListener("input", () => {
      query = typeof searchField.value === "string" ? searchField.value : "";
      render();
    });
    root.appendChild(searchField);

    const form = make(doc, "div", "pm-form");
    nameField = make(doc, "input", "pm-input");
    nameField.setAttribute("type", "text");
    nameField.setAttribute("placeholder", t("dialog.presetName", "Preset name"));
    nameField.setAttribute("aria-label", t("control.preset", "Preset"));
    categoryField = make(doc, "input", "pm-input");
    categoryField.setAttribute("type", "text");
    categoryField.setAttribute("placeholder", t("preset.category", "Category"));
    categoryField.setAttribute("aria-label", t("preset.category", "Category"));
    categoryField.setAttribute("list", CATEGORY_LIST_ID);
    categoryList = make(doc, "datalist");
    categoryList.setAttribute("id", CATEGORY_LIST_ID);
    saveButton = make(doc, "button", "pm-save", t("control.save", "Save"));
    saveButton.setAttribute("type", "button");
    saveButton.setAttribute("title", t("preset.saveTitle", "Save the preset under this name"));
    saveButton.addEventListener("click", () => {
      submitForm();
    });
    form.appendChild(nameField);
    form.appendChild(categoryField);
    form.appendChild(saveButton);
    root.appendChild(form);
    root.appendChild(categoryList);

    listEl = make(doc, "div", "pm-list");
    root.appendChild(listEl);

    errorEl = make(doc, "div", "pm-error hidden");
    root.appendChild(errorEl);

    container.appendChild(root);
  }

  /* ---- painting ------------------------------------------------------- */

  /**
   * The texts of the panel, in the language of the moment.
   *
   * The panel is built once and lives in the inspector, so the texts have to
   * follow the locale when it changes — otherwise the user reads half of the
   * interface in one language and half of it in another.
   */
  function translate() {
    if (searchField) {
      searchField.setAttribute("placeholder", t("preset.search", "Search presets"));
      searchField.setAttribute("aria-label", t("preset.search", "Search presets"));
    }
    if (nameField) {
      nameField.setAttribute("placeholder", t("dialog.presetName", "Preset name"));
      nameField.setAttribute("aria-label", t("control.preset", "Preset"));
    }
    if (categoryField) {
      categoryField.setAttribute("placeholder", t("preset.category", "Category"));
      categoryField.setAttribute("aria-label", t("preset.category", "Category"));
    }
    if (saveButton) {
      saveButton.textContent = t("control.save", "Save");
      saveButton.setAttribute("title", t("preset.saveTitle", "Save the preset under this name"));
    }
    render();
  }

  /** Show an error in the panel and hand it to the host. */
  function reportError(err, context) {
    const message = err && err.message ? String(err.message) : String(err);
    lastError = message;
    if (isOfflineError(err)) online = false;
    if (errorEl) {
      errorEl.textContent = message;
      errorEl.classList.remove("hidden");
    }
    if (onError) {
      try {
        onError(err, context);
      } catch (_ignored) {
        /* the host owns its own reporting */
      }
    }
    return message;
  }

  /** Hide the last error. */
  function clearError() {
    lastError = null;
    if (errorEl) {
      errorEl.textContent = "";
      errorEl.classList.add("hidden");
    }
  }

  /** Tell the host that the library changed. */
  function notifyChange() {
    if (!onChange) return;
    try {
      onChange(presets.slice());
    } catch (_ignored) {
      /* the host owns its own reporting */
    }
  }

  /** Build the fragment that marks the search hit inside a name. */
  function highlighted(doc2, text, terms) {
    const frag = doc2.createDocumentFragment();
    const lower = text.toLowerCase();
    const wanted = Array.isArray(terms) ? terms : [terms];
    const hits = [];
    for (const term of wanted) {
      if (!term) continue;
      let at = lower.indexOf(term);
      while (at >= 0) {
        hits.push([at, at + term.length]);
        at = lower.indexOf(term, at + term.length);
      }
    }
    if (!hits.length) {
      frag.appendChild(doc2.createTextNode(text));
      return frag;
    }
    hits.sort((a, b) => a[0] - b[0] || b[1] - a[1]);
    const merged = [];
    for (const [start, end] of hits) {
      const last = merged[merged.length - 1];
      if (last && start <= last[1]) {
        if (end > last[1]) last[1] = end;
        continue;
      }
      merged.push([start, end]);
    }
    let cursor = 0;
    for (const [start, end] of merged) {
      if (start > cursor) frag.appendChild(doc2.createTextNode(text.slice(cursor, start)));
      const mark = doc2.createElement("mark");
      mark.className = "pm-hit";
      mark.textContent = text.slice(start, end);
      frag.appendChild(mark);
      cursor = end;
    }
    if (cursor < text.length) frag.appendChild(doc2.createTextNode(text.slice(cursor)));
    return frag;
  }

  /**
   * The fields of a preset the search marks.
   *
   * The name comes first, then the code of the module, then the name the
   * module is shown under and finally the category.  The first field that
   * holds every term wins; when no field does, every term is marked in the
   * first field that holds it.
   */
  function hitFields(preset, terms) {
    if (!terms.length) return [];
    const fields = [
      { key: "name", text: preset.name },
      { key: "code", text: preset.module_code },
      { key: "display", text: preset.display_name },
      { key: "category", text: preset.category },
    ].filter((field) => field.text);
    for (const field of fields) {
      const lower = field.text.toLowerCase();
      if (terms.every((term) => lower.includes(term))) {
        return [{ key: field.key, text: field.text, terms: terms.slice() }];
      }
    }
    const out = [];
    for (const term of terms) {
      const field = fields.find((entry) => entry.text.toLowerCase().includes(term));
      if (!field) continue;
      const hit = out.find((entry) => entry.key === field.key);
      if (hit) hit.terms.push(term);
      else out.push({ key: field.key, text: field.text, terms: [term] });
    }
    return out;
  }

  /** Build the row of one preset. */
  function buildRow(preset) {
    const item = make(doc, "li");
    const row = make(doc, "button", "pm-item");
    row.setAttribute("type", "button");
    row.setAttribute("data-path", preset.path);
    row.setAttribute("data-category", preset.category);
    row.setAttribute("title", `${preset.name} — ${preset.module_code}`);
    row.setAttribute("aria-label", t("preset.loadLabel", "Load the preset {name}", { name: preset.name }));

    const terms = searchTerms(query);
    const hits = hitFields(preset, terms);
    const name = make(doc, "span", "pm-name");
    const code = make(doc, "span", "pm-code", preset.module_code || "—");
    if (hits.length) {
      row.setAttribute("data-hit", hits[0].key);
      const nameHit = hits.find((hit) => hit.key === "name");
      if (nameHit) name.appendChild(highlighted(doc, preset.name, nameHit.terms));
      else name.textContent = preset.name;
      const codeHit = hits.find((hit) => hit.key === "code");
      if (codeHit) {
        clear(code);
        code.appendChild(highlighted(doc, preset.module_code, codeHit.terms));
      }
    } else {
      name.textContent = preset.name;
    }
    row.appendChild(name);

    row.appendChild(code);
    const time = make(doc, "span", "pm-time", formatModified(preset.modified));
    const stamp = modifiedStamp(preset.modified);
    if (stamp) time.setAttribute("title", stamp);
    row.appendChild(time);

    row.addEventListener("click", () => {
      select(preset);
    });
    item.appendChild(row);
    return item;
  }

  /** Rebuild the list of the visible presets. */
  function render() {
    if (!listEl) return;
    clear(listEl);
    const visible = filterPresets(presets, query);
    const terms = searchTerms(query);
    if (!visible.length) {
      const empty = make(doc, "div", "pm-empty");
      empty.textContent = query.trim()
        ? t("preset.noMatch", "No presets match “{query}”", { query: query.trim() })
        : t("dialog.noPresets", "No presets stored yet.");
      listEl.appendChild(empty);
    } else {
      for (const group of groupPresets(visible)) {
        const open = !collapsed.has(group.category);
        const section = make(doc, "section", "pm-section");
        section.setAttribute("data-category", group.category);
        section.setAttribute("data-open", open ? "true" : "false");

        const head = make(doc, "button", "pm-section-head");
        head.setAttribute("type", "button");
        head.setAttribute("aria-expanded", open ? "true" : "false");
        const headName = make(doc, "span", "pm-section-name");
        const lower = group.category.toLowerCase();
        if (terms.length && terms.every((term) => lower.includes(term))) {
          headName.appendChild(highlighted(doc, group.category, terms));
        } else {
          headName.textContent = group.category;
        }
        head.appendChild(headName);
        head.appendChild(make(doc, "span", "pm-count", String(group.presets.length)));
        head.addEventListener("click", () => {
          if (collapsed.has(group.category)) collapsed.delete(group.category);
          else collapsed.add(group.category);
          render();
        });
        section.appendChild(head);

        const items = make(doc, "ul", "pm-items");
        for (const preset of group.presets) items.appendChild(buildRow(preset));
        section.appendChild(items);
        listEl.appendChild(section);
      }
    }
    renderCategoryOptions();
  }

  /** Offer the categories of the library in the form. */
  function renderCategoryOptions() {
    if (!categoryList) return;
    clear(categoryList);
    const names = new Set();
    for (const preset of presets) names.add(preset.category);
    for (const name of CATEGORY_ORDER) names.add(name);
    for (const name of names) {
      const option = make(doc, "option");
      option.setAttribute("value", name);
      categoryList.appendChild(option);
    }
  }

  /* ---- the actions ---------------------------------------------------- */

  /** Re-read the library from the server. */
  async function refresh() {
    try {
      if (!listFn) throw new Error(t("preset.errorUnavailable", "The preset library is not available."));
      const res = await listFn();
      const raw =
        res && Array.isArray(res.presets)
          ? res.presets
          : Array.isArray(res)
            ? res
            : [];
      presets = raw.map(normalizePreset);
      online = true;
      clearError();
      render();
      notifyChange();
      return presets.slice();
    } catch (err) {
      reportError(err, "list");
      throw err;
    }
  }

  /** The presets the search field leaves visible. */
  function list() {
    return filterPresets(presets, query);
  }

  /** The visible presets grouped by category. */
  function categories() {
    return groupPresets(list());
  }

  /** Every preset the server returned. */
  function all() {
    return presets.slice();
  }

  /** Set the filter of the search field. */
  function search(text) {
    query = String(text == null ? "" : text);
    if (searchField && searchField.value !== query) searchField.value = query;
    render();
    return list();
  }

  /** The text of the search field. */
  function filter() {
    return query;
  }

  /**
   * Store a preset under a new name.
   *
   * The answer of the server has to name the file that was written: when it
   * does not, the manager says the save failed instead of pretending that the
   * preset is stored.  A failure of the list that follows the write is
   * reported as a failure of the list, so the user is not told that a preset
   * was lost when it was in fact written.
   */
  async function save(input = {}) {
    const content = getContent ? getContent() || {} : {};
    const payload = buildSavePayload(input, content);
    try {
      validateSavePayload(payload);
    } catch (err) {
      reportError(err, "save");
      throw err;
    }
    let res;
    try {
      if (!saveFn) throw new Error(t("preset.errorUnavailable", "The preset library is not available."));
      res = await saveFn(payload);
    } catch (err) {
      reportError(err, "save");
      throw err;
    }
    const written = res && typeof res === "object" ? res : {};
    const path = String(written.path || "");
    if (!path) {
      const err = new Error(
        t(
          "preset.errorSaveNotConfirmed",
          "The server did not confirm the save — the preset was not written."
        )
      );
      reportError(err, "save");
      throw err;
    }
    online = true;
    current = {
      name: payload.name,
      category: payload.category,
      path,
      module_code: payload.module_code,
      display_name: payload.display_name || "",
      modified: 0,
      builtin: false,
    };
    currentDocument = written.preset || currentDocument;
    clearError();
    try {
      await refresh();
    } catch (err) {
      const message = err && err.message ? String(err.message) : String(err);
      reportError(
        new Error(
          t(
            "preset.errorSavedListFailed",
            "The preset was saved, but the library could not be read again — {message}",
            { message }
          )
        ),
        "list"
      );
    }
    notifyChange();
    return { path, preset: written.preset || payload };
  }

  /** Read one preset and hand it to the host. */
  async function load(path) {
    const target = String(path || "");
    if (!target) throw new Error(t("preset.errorPathRequired", "A preset path is required."));
    try {
      if (!loadFn) throw new Error(t("preset.errorUnavailable", "The preset library is not available."));
      // the module that is open: the server refuses a preset that belongs to
      // another module, and it says so in one clear line
      const content = getContent ? getContent() || {} : {};
      const moduleCode = String(content.module_code || "").trim();
      const document_ = await loadFn(target, moduleCode);
      currentDocument = document_;
      const entry = presets.find((preset) => preset.path === target);
      if (entry) current = entry;
      online = true;
      clearError();
      if (onLoad) {
        onLoad(document_, {
          path: target,
          name: String(unwrapTyped(document_ && document_.name, (entry && entry.name) || "")),
        });
      }
      return document_;
    } catch (err) {
      reportError(err, "load");
      throw err;
    }
  }

  /** Delete one preset, after the user confirmed it. */
  async function remove(path, removeOptions = {}) {
    const target = String(path || "");
    if (!target) throw new Error(t("preset.errorPathRequired", "A preset path is required."));
    const entry = presets.find((preset) => preset.path === target);
    const label = (entry && entry.name) || target;
    const ask = removeOptions.confirm === undefined ? true : !!removeOptions.confirm;
    if (ask) {
      const ok = await confirmFn(t("preset.removeConfirm", "Remove the preset “{name}”?", { name: label }));
      if (!ok) return null;
    }
    try {
      if (!deleteFn) throw new Error(t("preset.errorUnavailable", "The preset library is not available."));
      const res = await deleteFn(target);
      online = true;
      clearError();
      if (current && current.path === target) {
        current = null;
        currentDocument = null;
      }
      await refresh();
      notifyChange();
      return res || { ok: true, path: target };
    } catch (err) {
      reportError(err, "remove");
      throw err;
    }
  }

  /** Store the preset of `path` under a new name and drop the old file. */
  async function rename(path, name, category) {
    const source = String(path || "");
    const target = String(name || "").trim();
    if (!source) throw new Error(t("preset.errorPathRequired", "A preset path is required."));
    if (!target) throw new Error(t("preset.errorName", "The preset needs a name."));
    try {
      if (!loadFn || !saveFn) throw new Error(t("preset.errorUnavailable", "The preset library is not available."));
      const document_ = await loadFn(source);
      const payload = documentToPayload(document_);
      payload.name = target;
      if (category) payload.category = String(category);
      validateSavePayload(payload);
      const saved = await saveFn(payload);
      if (!saved || !saved.path) {
        throw new Error(
          t(
            "preset.errorRenameNotConfirmed",
            "The server did not confirm the rename — the preset was not written."
          )
        );
      }
      if (deleteFn) {
        try {
          await deleteFn(source);
        } catch (err) {
          const message = err && err.message ? String(err.message) : String(err);
          throw new Error(
            t(
              "preset.errorRenamedNotRemoved",
              "The preset was renamed to “{name}”, but the old file could not be " +
                "removed — {message}",
              { name: target, message }
            )
          );
        }
      }
      online = true;
      clearError();
      await refresh();
      notifyChange();
      return saved;
    } catch (err) {
      reportError(err, "rename");
      throw err;
    }
  }

  /** Store a preset document that came from a file. */
  async function importDocument(document_) {
    return save(documentToPayload(document_));
  }

  /** The document of the current preset as JSON text. */
  function exportCurrent() {
    if (!currentDocument) throw new Error(t("preset.errorNoExport", "There is no preset to export."));
    const text = JSON.stringify(currentDocument, null, 2);
    const name = String(unwrapTyped(currentDocument.name, current ? current.name : "preset"));
    const file = `${name.replace(/[^\w.-]+/g, "_") || "preset"}.json`;
    downloadJson(text, file);
    return { name, file, json: text };
  }

  /** Offer a JSON document as a download, when the browser allows it. */
  function downloadJson(text, filename) {
    try {
      if (typeof Blob === "undefined") return false;
      if (typeof URL === "undefined" || typeof URL.createObjectURL !== "function") return false;
      const host = doc || (typeof document !== "undefined" ? document : null);
      if (!host || !host.body) return false;
      const blob = new Blob([text], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = el("a", { href: url, download: filename });
      host.body.appendChild(link);
      link.click();
      if (typeof link.remove === "function") link.remove();
      else if (link.parentNode) link.parentNode.removeChild(link);
      URL.revokeObjectURL(url);
      return true;
    } catch (_ignored) {
      return false;
    }
  }

  /* ---- the form ------------------------------------------------------- */

  /** Fill the form with a preset, so that saving renames it. */
  function beginRename(path) {
    const target = String(path || "");
    const entry = presets.find((preset) => preset.path === target) || current;
    if (!target) return false;
    renameFrom = target;
    if (nameField && entry) nameField.value = entry.name || "";
    if (categoryField && entry) categoryField.value = entry.category || "";
    if (saveButton) {
      saveButton.textContent = t("preset.menu.rename", "Rename");
      saveButton.classList.add("armed");
    }
    if (nameField && typeof nameField.focus === "function") nameField.focus();
    return true;
  }

  /** Turn the form back into a "save as new" form. */
  function cancelRename() {
    renameFrom = null;
    if (nameField) nameField.value = "";
    if (saveButton) {
      saveButton.textContent = t("control.save", "Save");
      saveButton.classList.remove("armed");
    }
    return true;
  }

  /** Put the caret into the name field of the form. */
  function focusSaveForm() {
    if (!nameField) return false;
    if (typeof nameField.focus === "function") nameField.focus();
    return true;
  }

  /** Store the content of the form, as a new preset or as a rename. */
  async function submitForm() {
    const name = nameField ? String(nameField.value || "").trim() : "";
    const category = categoryField ? String(categoryField.value || "").trim() : "";
    try {
      if (renameFrom) {
        await rename(renameFrom, name, category || undefined);
        cancelRename();
      } else {
        await save({ name, category: category || DEFAULT_CATEGORY });
      }
      if (nameField) nameField.value = "";
      clearError();
      return true;
    } catch (_err) {
      /* the error is already shown in the panel */
      return false;
    }
  }

  /* ---- the current preset --------------------------------------------- */

  /**
   * True when the module changed since the preset was read.
   *
   * The manager compares the parameters of the document it holds with the
   * parameters the host reports for the module of the panel.  Without a
   * document or without live parameters the answer is "no": the manager does
   * not guess.
   */
  function isDirty() {
    if (!currentDocument) return false;
    const saved = unwrapParams(currentDocument.params);
    const content = getContent ? getContent() || {} : null;
    if (!content) return false;
    const live = content.params && typeof content.params === "object" ? content.params : null;
    if (!live) return false;
    return !sameParams(saved, live);
  }

  /**
   * The state of the library, as the menu of the panel shows it.
   *
   *   ok          the last call to the server succeeded
   *   offline     the server could not be reached
   *   error       the message of the last failure, or null
   *   count       the number of presets in the library
   *   empty       the library holds no preset at all
   *   current     the preset the panel shows
   *   hasCurrent  the panel shows a preset that can be renamed or removed
   *   dirty       the preset has changes that were not saved
   *   document    the manager holds the document of the preset
   */
  function state() {
    const dirty = isDirty();
    return {
      ok: online && !lastError,
      offline: !online,
      error: lastError,
      count: presets.length,
      empty: presets.length === 0,
      current,
      hasCurrent: Boolean(current && current.path),
      hasDocument: Boolean(currentDocument),
      dirty,
      name: current ? String(current.name || "") : "",
      path: currentPath(),
      query,
    };
  }

  /** Show an error of the host in the panel of the manager. */
  function report(err, context) {
    return reportError(err, context || "menu");
  }

  /** Make a preset the current one and read it from the server. */
  async function select(preset) {
    current = preset;
    if (!preset || !preset.path) return null;
    return load(preset.path);
  }

  /** The preset the panel shows. */
  function currentPreset() {
    return current;
  }

  /** The path of the preset the panel shows. */
  function currentPath() {
    return current ? current.path : "";
  }

  /** Tell the manager which preset the panel shows. */
  function setCurrent(preset) {
    current = preset || null;
    return current;
  }

  /** Release the DOM and the listeners of the manager. */
  function destroy() {
    if (unsubscribeLocale) unsubscribeLocale();
    if (container && root && root.parentNode) root.parentNode.removeChild(root);
    root = null;
    searchField = null;
    nameField = null;
    categoryField = null;
    saveButton = null;
    categoryList = null;
    listEl = null;
    errorEl = null;
    presets = [];
    current = null;
    currentDocument = null;
    renameFrom = null;
    lastError = null;
    online = true;
  }

  /* The panel follows the locale: it is built once and lives in the
     inspector. */
  const unsubscribeLocale =
    typeof onLocaleChange === "function" ? onLocaleChange(() => translate()) : null;

  render();

  return {
    refresh,
    list,
    categories,
    save,
    load,
    remove,
    destroy,
    /* beyond the contract */
    all,
    search,
    filter,
    rename,
    importDocument,
    exportCurrent,
    beginRename,
    cancelRename,
    focusSaveForm,
    current: currentPreset,
    currentPath,
    setCurrent,
    state,
    isDirty,
    report,
    element: root,
  };
}

/* -------------------------------------------------------------------- menu */

/** The items of the preset menu, in the order they are shown. */
export const PRESET_MENU_ITEMS = Object.freeze([
  Object.freeze({ id: "save", label: "Save", key: "preset.menu.save" }),
  Object.freeze({ id: "saveAs", label: "Save As", key: "preset.menu.saveAs" }),
  Object.freeze({ id: "rename", label: "Rename", key: "preset.menu.rename" }),
  Object.freeze({ id: "remove", label: "Remove", key: "preset.menu.remove" }),
  Object.freeze({ id: "reload", label: "Reload", key: "preset.menu.reload" }),
  Object.freeze({ id: "import", label: "Import", key: "preset.menu.import" }),
  Object.freeze({ id: "export", label: "Export", key: "preset.menu.export" }),
]);

/**
 * Render the "..." menu of the module panel.
 *
 * The menu belongs to the panel, so `container` has to be the element the
 * panel positions its own menu against — the head of the panel.  The menu
 * plugs into the preset chip of the panel: the shell opens it when the chip
 * or the "..." button is pressed.
 *
 * The menu shows the state of the library honestly: the line at the top says
 * whether the server answers, whether the library holds anything and whether
 * the preset on the panel has changes that were not saved.  An action that
 * cannot work in that state is disabled instead of failing silently.
 *
 * handlers
 *   onSave     the current preset is written over
 *   onLoad     the preset list is opened
 *   onRemove   the current preset is deleted
 *   onRename   the current preset is renamed
 *   onSaveAs   a new preset is written
 *   onReload   the library is read again
 *   onImport   a preset file is read
 *   onExport   the current preset is written to a file
 *   state      the state of the library, when the manager does not report it
 *   dirty      true when the preset on the panel has unsaved changes
 *
 * Every handler is optional: without one the manager does the work itself.
 *
 * Returns { open, close, toggle, items, element, state, destroy }.
 */
export function renderPresetMenu(container, manager, handlers = {}) {
  const doc = ownerDoc(container);
  if (!doc || !container || typeof doc.createElement !== "function") {
    throw new Error("renderPresetMenu: a DOM container element is required");
  }
  ensureStyle(doc);
  const hnd = handlers && typeof handlers === "object" ? handlers : {};
  const onSave = pickFn(hnd.onSave);
  const onLoad = pickFn(hnd.onLoad);
  const onRemove = pickFn(hnd.onRemove);
  const onRename = pickFn(hnd.onRename);
  const onSaveAs = pickFn(hnd.onSaveAs);
  const onReload = pickFn(hnd.onReload);
  const onImport = pickFn(hnd.onImport);
  const onExport = pickFn(hnd.onExport);
  const stateFn = pickFn(hnd.state);
  const dirtyFn = pickFn(hnd.dirty);

  const menu = make(doc, "div", "pm-menu hidden");
  menu.setAttribute("role", "menu");
  const stateLine = make(doc, "div", "pm-menu-state");
  stateLine.setAttribute("role", "status");
  menu.appendChild(stateLine);
  const items = [];
  const buttons = new Map();

  for (const spec of PRESET_MENU_ITEMS) {
    if (spec.id === "reload") {
      const sep = make(doc, "div", "pm-menu-sep");
      sep.setAttribute("role", "separator");
      menu.appendChild(sep);
    }
    const button = make(doc, "button", "pm-menu-item");
    if (spec.id === "remove") button.className = "pm-menu-item pm-danger";
    button.setAttribute("type", "button");
    button.setAttribute("role", "menuitem");
    button.setAttribute("data-action", spec.id);
    button.textContent = t(spec.key, spec.label);
    button.addEventListener("click", () => {
      close();
      run(spec.id);
    });
    menu.appendChild(button);
    buttons.set(spec.id, button);
    items.push({ id: spec.id, label: spec.label, element: button });
  }

  container.appendChild(menu);

  /* ---- the state ------------------------------------------------------ */

  let isOpen = false;
  let outsideHandler = null;

  /**
   * The state of the library.
   *
   * The manager knows it; a host that keeps the state itself may hand it
   * over with the `state` handler.
   */
  function libraryState() {
    let value = null;
    if (manager && typeof manager.state === "function") {
      try {
        value = manager.state();
      } catch (_ignored) {
        value = null;
      }
    }
    if ((!value || typeof value !== "object") && stateFn) {
      try {
        value = stateFn();
      } catch (_ignored) {
        value = null;
      }
    }
    return value && typeof value === "object" ? value : {};
  }

  /** True when the preset on the panel has changes that were not saved. */
  function isDirty(state) {
    if (dirtyFn) {
      try {
        return Boolean(dirtyFn(state));
      } catch (_ignored) {
        return false;
      }
    }
    return state.dirty === true;
  }

  /**
   * Show the state of the library and disable the actions that cannot work.
   *
   * The text is written for the user: it says what is true, not what the
   * manager happens to know.
   */
  function updateState() {
    const state = libraryState();
    const known = Object.keys(state).length > 0;
    const offline = state.offline === true;
    const empty = state.empty === true;
    const count = Number(state.count || 0);
    const dirty = isDirty(state);
    const hasCurrent =
      state.hasCurrent !== undefined
        ? Boolean(state.hasCurrent)
        : known
          ? Boolean(state.current)
          : true;
    const hasDocument = state.hasDocument !== undefined ? Boolean(state.hasDocument) : hasCurrent;
    const name = String(state.name || "");

    let text = "";
    if (offline) text = t("preset.errorUnreachable", "The preset library is not reachable.");
    else if (state.error) text = String(state.error);
    else if (empty) text = t("dialog.noPresets", "No presets stored yet.");
    else if (dirty) {
      text = name
        ? t("preset.unsavedIn", "Unsaved changes in “{name}”.", { name })
        : t("preset.unsavedChanges", "There are unsaved changes.");
    }
    else if (count) text = t("preset.count", "{n} preset(s).", { n: count });
    stateLine.textContent = text;
    stateLine.classList.toggle("pm-offline", offline);
    stateLine.classList.toggle("pm-dirty", dirty && !offline);
    stateLine.classList.toggle("pm-empty", empty && !offline);

    const usable = {
      save: !offline,
      saveAs: !offline,
      rename: !offline && hasCurrent,
      remove: !offline && hasCurrent,
      reload: true,
      import: !offline,
      export: hasDocument,
    };
    for (const [id, button] of buttons) {
      const ok = usable[id] !== false;
      button.disabled = !ok;
      button.setAttribute("aria-disabled", String(!ok));
      button.classList.toggle("pm-dirty", id === "save" && dirty);
      if (id === "save" && dirty) {
        button.setAttribute(
          "title",
          name
            ? t("preset.unsavedTitle", "Save “{name}” — there are unsaved changes", { name })
            : t("preset.unsavedTitleNoName", "There are unsaved changes")
        );
      } else {
        button.removeAttribute("title");
      }
    }
    return state;
  }

  function open() {
    if (isOpen) return;
    isOpen = true;
    updateState();
    menu.classList.remove("hidden");
    if (doc.addEventListener) {
      outsideHandler = (event) => {
        if (menu.contains && event && event.target && menu.contains(event.target)) return;
        close();
      };
      doc.addEventListener("pointerdown", outsideHandler, true);
    }
  }

  function close() {
    if (!isOpen) return;
    isOpen = false;
    menu.classList.add("hidden");
    if (outsideHandler && doc.removeEventListener) {
      doc.removeEventListener("pointerdown", outsideHandler, true);
      outsideHandler = null;
    }
  }

  function toggle() {
    if (isOpen) close();
    else open();
    return isOpen;
  }

  if (doc.addEventListener) {
    doc.addEventListener("keydown", (event) => {
      if (!isOpen) return;
      if (event && event.key === "Escape") close();
    });
  }

  /* ---- the actions ---------------------------------------------------- */

  /** The preset the panel shows. */
  function currentPreset() {
    if (!manager) return null;
    if (typeof manager.current === "function") return manager.current();
    return null;
  }

  /** Write the current preset over itself. */
  async function doSave() {
    const preset = currentPreset();
    if (onSave) return onSave(preset);
    if (!manager) return undefined;
    if (!preset || !preset.name) {
      if (typeof manager.focusSaveForm === "function") manager.focusSaveForm();
      reportToManager(
        new Error(t("preset.errorNoSave", "There is no preset to save yet — give it a name in the form."))
      );
      return undefined;
    }
    return manager.save({ name: preset.name, category: preset.category });
  }

  /** Open the form that writes a new preset. */
  async function doSaveAs() {
    if (onSaveAs) return onSaveAs();
    if (!manager) return undefined;
    if (typeof manager.focusSaveForm === "function") return manager.focusSaveForm();
    return undefined;
  }

  /** Rename the current preset. */
  async function doRename() {
    const preset = currentPreset();
    if (onRename) return onRename(preset);
    if (!manager) return undefined;
    if (!preset || !preset.path) {
      if (typeof manager.focusSaveForm === "function") manager.focusSaveForm();
      reportToManager(new Error(t("preset.errorNoRename", "There is no preset to rename.")));
      return undefined;
    }
    return manager.beginRename(preset.path);
  }

  /** Delete the current preset. */
  async function doRemove() {
    const preset = currentPreset();
    if (onRemove) return onRemove(preset);
    if (!manager) return undefined;
    if (!preset || !preset.path) {
      reportToManager(new Error(t("preset.errorNoRemove", "There is no preset to remove.")));
      return undefined;
    }
    return manager.remove(preset.path);
  }

  /** Read the library again and read the current preset once more. */
  async function doReload() {
    if (onReload) return onReload();
    if (!manager) return undefined;
    await manager.refresh();
    const preset = currentPreset();
    if (preset && preset.path) await manager.load(preset.path);
    return undefined;
  }

  /** Read a preset file and store it in the library. */
  async function doImport() {
    if (onImport) return onImport();
    if (!manager) return undefined;
    const file = await pickFile();
    if (!file) return undefined;
    const text = await readFile(file);
    const document_ = JSON.parse(text);
    return manager.importDocument(document_);
  }

  /** Write the current preset to a file. */
  async function doExport() {
    if (onExport) return onExport();
    if (!manager) return undefined;
    return manager.exportCurrent();
  }

  /** Run one item of the menu. */
  async function run(id) {
    try {
      const result = await dispatch(id);
      updateState();
      return result;
    } catch (err) {
      /* the failure is shown in the menu and in the panel of the manager */
      showError(err);
      return undefined;
    }
  }

  /** Do the work of one item of the menu. */
  async function dispatch(id) {
    switch (id) {
      case "save":
        return await doSave();
      case "saveAs":
        return await doSaveAs();
      case "rename":
        return await doRename();
      case "remove":
        return await doRemove();
      case "reload":
        return await doReload();
      case "import":
        return await doImport();
      case "export":
        return await doExport();
      default:
        return undefined;
    }
  }

  /** Show the failure of an action in the state line of the menu. */
  function showError(err) {
    const message = err && err.message ? String(err.message) : String(err);
    reportToManager(err);
    updateState();
    stateLine.textContent = message;
    stateLine.classList.add("pm-offline");
  }

  /** Hand a failure to the manager, so that the panel shows it as well. */
  function reportToManager(err) {
    if (!manager || typeof manager.report !== "function") return;
    try {
      manager.report(err, "menu");
    } catch (_ignored) {
      /* the host owns its own reporting */
    }
  }

  /** Ask the user for a JSON file. */
  function pickFile() {
    return new Promise((resolve) => {
      const input = make(doc, "input");
      input.setAttribute("type", "file");
      input.setAttribute("accept", "application/json,.json");
      input.setAttribute("hidden", "");
      input.addEventListener("change", () => {
        const files = input.files;
        const file = files && files[0] ? files[0] : null;
        if (input.parentNode) input.parentNode.removeChild(input);
        resolve(file);
      });
      const host = doc.body || doc.documentElement;
      if (!host) {
        resolve(null);
        return;
      }
      host.appendChild(input);
      input.click();
    });
  }

  /** Read the text of a file the user picked. */
  function readFile(file) {
    if (file && typeof file.text === "function") return file.text();
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ""));
      reader.onerror = () => reject(new Error(t("preset.errorUnreadable", "The file could not be read.")));
      reader.readAsText(file);
    });
  }

  /** The texts of the menu, in the language of the moment. */
  function translate() {
    for (const spec of PRESET_MENU_ITEMS) {
      const button = buttons.get(spec.id);
      if (button) button.textContent = t(spec.key, spec.label);
    }
    updateState();
  }

  /** Remove the menu from the document. */
  function destroy() {
    close();
    if (unsubscribeLocale) unsubscribeLocale();
    if (container && menu.parentNode) container.removeChild(menu);
  }

  /* The menu follows the locale: it is built once and lives in the panel. */
  const unsubscribeLocale =
    typeof onLocaleChange === "function" ? onLocaleChange(() => translate()) : null;

  return { open, close, toggle, items, element: menu, state: updateState, destroy };
}
