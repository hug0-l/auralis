/* Auralis — module browser (the left rail).
 *
 * Renders the module catalogue as collapsible, searchable category sections.
 *
 * Contract notes
 *  - Every colour comes from css/tokens.css (var(--token) references only).
 *  - No top-level DOM access: the document is resolved inside functions, so
 *    this module can be imported by a head-less test harness.
 *  - No third-party product or vendor names appear anywhere in this file.
 *
 * Public API
 *   renderBrowser(container, { modules, onAdd, searchQuery }) -> { update, setQuery }
 */

import { t } from "./i18n.js";



const CATEGORY_ORDER = ["Repair", "Utility", "Measurement", "System"];
const STYLE_ID = "aur-ui-browser-style";
/** Root element per container, so a re-render replaces instead of stacking. */
const ROOTS = new WeakMap();

/**
 * The catalogue key of every category the server reports.
 *
 * The category names come from the server, so they are data; the text
 * the user reads is a catalogue entry.
 */
const CATEGORY_KEYS = {
  Repair: "browser.category.repair",
  Utility: "browser.category.utility",
  Measurement: "browser.category.measurement",
  System: "browser.category.system",
};



const CSS = `
.mb-root { display: flex; flex-direction: column; gap: var(--sp-2); height: 100%; min-height: 0; }
.mb-search {
  width: 100%; box-sizing: border-box; background: var(--bg-3); border: 1px solid var(--line);
  border-radius: var(--radius); color: var(--txt-0); font-family: var(--font-ui);
  font-size: var(--fs-sm); padding: var(--sp-2) var(--sp-3); outline: none;
}
.mb-search:focus { border-color: var(--sel-edge); }
.mb-search::placeholder { color: var(--txt-2); }
.mb-list { flex: 1 1 auto; min-height: 0; overflow-y: auto; overflow-x: hidden; }
.mb-section { margin-bottom: var(--sp-3); }
.mb-section-head {
  display: flex; align-items: center; gap: var(--sp-2); width: 100%; box-sizing: border-box;
  background: transparent; border: 0; border-bottom: 1px solid var(--line-soft); border-radius: 0;
  color: var(--txt-1); font-family: var(--font-ui); font-size: var(--fs-xs); letter-spacing: 0.08em;
  text-transform: uppercase; padding: var(--sp-2) var(--sp-1); cursor: pointer; text-align: left;
}
.mb-section-head:hover { color: var(--txt-0); }
.mb-section-head:focus-visible { outline: 1px solid var(--sel-edge); outline-offset: -1px; }
.mb-caret { color: var(--txt-2); font-size: 9px; line-height: 1; transition: transform 120ms ease; }
.mb-section[data-open="false"] .mb-caret { transform: rotate(-90deg); }
.mb-section[data-open="false"] .mb-items { display: none; }
.mb-count { margin-left: auto; color: var(--txt-2); font-family: var(--font-mono); font-size: var(--fs-xs); }
.mb-items { list-style: none; margin: 0; padding: 0; }
.mb-row {
  display: flex; align-items: center; gap: var(--sp-2); width: 100%; box-sizing: border-box;
  background: transparent; border: 0; border-radius: var(--radius); color: var(--txt-0);
  font-family: var(--font-ui); font-size: var(--fs-sm); padding: var(--sp-2); cursor: pointer; text-align: left;
}
.mb-row:hover { background: var(--bg-2); }
.mb-row:focus-visible { outline: 1px solid var(--sel-edge); outline-offset: -1px; background: var(--bg-3); }
.mb-dot { flex: 0 0 auto; width: 6px; height: 6px; border-radius: 50%; background: var(--accent-2); }
.mb-dot-learn { background: var(--accent-3); }
.mb-name { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mb-hit { background: var(--sel); color: var(--txt-0); border-radius: 2px; padding: 0 1px; }
.mb-badge {
  flex: 0 0 auto; font-size: var(--fs-xs); font-family: var(--font-mono); color: var(--txt-2);
  border: 1px solid var(--line); border-radius: 2px; padding: 0 3px;
}
.mb-adv {
  flex: 0 0 auto; font-size: var(--fs-xs); font-family: var(--font-mono); letter-spacing: 0.06em;
  color: var(--accent-2); border: 1px solid var(--accent-2); border-radius: 2px; padding: 0 3px; opacity: 0.85;
}
.mb-add { flex: 0 0 auto; color: var(--txt-2); font-family: var(--font-mono); font-size: var(--fs-sm); padding: 0 var(--sp-1); }
.mb-row:hover .mb-add { color: var(--accent); }
.mb-empty { color: var(--txt-2); font-size: var(--fs-sm); padding: var(--sp-4) var(--sp-2); text-align: center; }
`;

/* ------------------------------------------------------------------ helpers */

/** Resolve the document that owns a node; falls back to the global document. */
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

/** Normalise a raw module record into the fields the browser needs. */
function normalizeModule(raw) {
  const m = raw && typeof raw === "object" ? raw : {};
  return {
    code: String(m.code || ""),
    display_name: String(m.display_name || m.name || m.code || ""),
    category: String(m.category || "Utility"),
    edition: String(m.edition || "standard"),
    analysis: Boolean(m.analysis),
    learn: Boolean(m.learn),
    ml: Boolean(m.ml),
    description: String(m.description || ""),
  };
}

/** The name of a category in the active locale. */
function categoryLabel(category) {
  const key = CATEGORY_KEYS[category] || "browser.category.other";
  return t(key, String(category || ""));
}



/** Group normalised modules by category, in the canonical category order. */
function groupByCategory(modules) {
  const buckets = new Map();
  for (const m of modules) {
    const key = m.category || "Utility";
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(m);
  }
  /* Inside a section the modules are listed by display name. */
  for (const items of buckets.values()) {
    items.sort((a, b) => {
      const x = a.display_name.toLowerCase();
      const y = b.display_name.toLowerCase();
      if (x < y) return -1;
      if (x > y) return 1;
      if (a.display_name < b.display_name) return -1;
      if (a.display_name > b.display_name) return 1;
      return 0;
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
  return known.concat(extra).map((key) => [key, buckets.get(key)]);
}

/**
 * Resolve the options for renderBrowser.
 *
 * Two call shapes are supported:
 *   renderBrowser(container, { modules, onAdd, searchQuery })
 *   renderBrowser(container, modules, handlers)      <- shell form
 *
 * `handlers` may be a bag of callbacks or a single callable; named callbacks
 * always win over the plain callable.
 */
function resolveBrowserOptions(second, third) {
  const opts = {};
  let handlerArg = null;
  if (Array.isArray(second)) {
    opts.modules = second;
    handlerArg = third;
  } else if (second && typeof second === "object") {
    Object.assign(opts, second);
    handlerArg = third;
  }
  if (handlerArg) {
    for (const key of ["onAdd", "onPick", "onInsert", "onModuleClick"]) {
      if (typeof handlerArg[key] === "function" && typeof opts[key] !== "function") {
        opts[key] = handlerArg[key];
      }
    }
    if (typeof handlerArg === "function" && typeof opts.onAdd !== "function") {
      opts.onAdd = handlerArg;
    }
  }
  return opts;
}

/** True when `node` is `ancestor` or lives inside it. */
function containsNode(ancestor, node) {
  if (!ancestor || !node) return false;
  if (typeof ancestor.contains === "function") return ancestor.contains(node);
  let cur = node;
  while (cur) {
    if (cur === ancestor) return true;
    cur = cur.parentNode;
  }
  return false;
}

/**
 * Look for a search field that already belongs to the panel holding the
 * container (the shell keeps one in the rail header).  When one is found it
 * is used instead of rendering a second field.
 */
function findSearchField(container) {
  const panel = container.parentNode;
  if (!panel || typeof panel.getElementsByTagName !== "function") return null;
  const inputs = panel.getElementsByTagName("input");
  for (const input of inputs) {
    if (!input || input === container) continue;
    const type = (typeof input.getAttribute === "function" && input.getAttribute("type")) || input.type || "";
    if (String(type).toLowerCase() !== "search") continue;
    if (containsNode(container, input)) continue;
    return input;
  }
  return null;
}

/** Build a text fragment with the matched substring wrapped in <mark>. */
function highlightedName(doc, name, query) {
  const frag = doc.createDocumentFragment();
  const needle = query.trim();
  if (!needle) {
    frag.appendChild(doc.createTextNode(name));
    return frag;
  }
  const lower = name.toLowerCase();
  const target = needle.toLowerCase();
  let cursor = 0;
  for (;;) {
    const at = lower.indexOf(target, cursor);
    if (at < 0) {
      if (cursor < name.length) frag.appendChild(doc.createTextNode(name.slice(cursor)));
      break;
    }
    if (at > cursor) frag.appendChild(doc.createTextNode(name.slice(cursor, at)));
    const mark = doc.createElement("mark");
    mark.className = "mb-hit";
    mark.textContent = name.slice(at, at + target.length);
    frag.appendChild(mark);
    cursor = at + target.length;
  }
  return frag;
}

/* -------------------------------------------------------------- public API */

/**
 * Render the module browser into `container`.
 *
 * @param {Element} container          element that receives the browser
 * @param {object}  options            { modules, onAdd, searchQuery, searchInput }
 * @param {Array}   options.modules    ModuleInfo records ({ code, display_name, category, edition, analysis, learn })
 * @param {Function} options.onAdd     called with the module code when a row is activated
 * @param {string}  options.searchQuery initial filter text (case-insensitive substring of the display name)
 * @param {Element} [options.searchInput] an existing <input> to bind instead of the built-in search field
 * @param {object|Function} [handlers] handler bag used by the positional form
 * @returns {{ update: Function, setQuery: Function }}
 */
export function renderBrowser(container, options = {}, handlers = null) {
  const doc = ownerDoc(container);
  if (!doc || !container) {
    throw new Error("renderBrowser: a DOM container element is required");
  }
  ensureStyle(doc);

  const opts = resolveBrowserOptions(options, handlers);
  let modules = (Array.isArray(opts.modules) ? opts.modules : []).map(normalizeModule);
  let query = String(opts.searchQuery == null ? "" : opts.searchQuery);
  const collapsed = new Set();
  /** Rows currently reachable by the keyboard (open sections only). */
  let rowButtons = [];

  const rawOnAdd =
    (typeof opts.onAdd === "function" && opts.onAdd) ||
    (typeof opts.onPick === "function" && opts.onPick) ||
    (typeof opts.onInsert === "function" && opts.onInsert) ||
    (typeof opts.onModuleClick === "function" && opts.onModuleClick) ||
    null;
  /** Invoke the host callback without letting a rejection escape. */
  const addModule = (code) => {
    if (!rawOnAdd) return;
    try {
      const result = rawOnAdd(code);
      if (result && typeof result.catch === "function") result.catch(() => {});
    } catch (_err) {
      /* the host owns error reporting */
    }
  };

  const root = make(doc, "div", "mb-root");
  const listEl = make(doc, "div", "mb-list");
  /* ---- search field (rendered above the list) ---- */
  const externalInput = opts.searchInput || findSearchField(container);
  let searchField = null;
  if (externalInput) {
    if (typeof externalInput.value === "string") query = externalInput.value;
    externalInput.addEventListener("input", () => {
      query = typeof externalInput.value === "string" ? externalInput.value : "";
      render();
    });
  } else {
    searchField = make(doc, "input", "mb-search");
    searchField.setAttribute("type", "search");
    searchField.setAttribute("placeholder", t("browser.search", "Search modules"));
    searchField.setAttribute("aria-label", t("browser.search", "Search modules"));
    searchField.value = query;
    searchField.addEventListener("input", () => {
      query = typeof searchField.value === "string" ? searchField.value : "";
      render();
    });
    root.appendChild(searchField);
  }
  root.appendChild(listEl);
  /* Re-rendering the browser replaces the previous instance instead of
     stacking a second copy inside the container. */
  const previous = ROOTS.get(container);
  if (previous && previous.parentNode) previous.parentNode.removeChild(previous);
  ROOTS.set(container, root);
  container.appendChild(root);

  /** Build one module row. */
  function buildRow(mod, sectionCategory, searching) {
    const item = make(doc, "li", "mb-item");
    const row = make(doc, "button", "mb-row");
    row.setAttribute("type", "button");
    row.setAttribute("data-code", mod.code);
    row.setAttribute("data-category", mod.category);

    const hints = [];
    if (mod.analysis) hints.push(t("browser.badge.analysis", "Analysis output"));
    if (mod.learn) hints.push(t("browser.badge.learn", "Supports Learn"));
    if (mod.ml) hints.push(t("browser.badge.ml", "Uses a model"));
    row.setAttribute("title", hints.length ? `${mod.display_name} — ${hints.join(", ")}` : mod.display_name);

    if (mod.analysis || mod.learn) {
      const dot = make(doc, "span", mod.analysis ? "mb-dot" : "mb-dot mb-dot-learn");
      dot.setAttribute("aria-hidden", "true");
      dot.setAttribute("title", hints[0] || t("browser.badge.analysis", "Analysis output"));
      row.appendChild(dot);
    }

    const name = make(doc, "span", "mb-name");
    name.appendChild(highlightedName(doc, mod.display_name, searching ? query : ""));
    row.appendChild(name);

    if (mod.category !== sectionCategory || searching) {
      const badge = make(doc, "span", "mb-badge", categoryLabel(mod.category));
      badge.setAttribute("title", categoryLabel(mod.category));
      row.appendChild(badge);
    }

    if (mod.edition === "advanced") {
      const adv = make(doc, "span", "mb-adv", t("edition.advanced", "ADV"));
      adv.setAttribute("title", t("browser.badge.advanced", "Advanced"));
      row.appendChild(adv);
    }

    const plus = make(doc, "span", "mb-add", "+");
    plus.setAttribute("aria-hidden", "true");
    plus.setAttribute("title", t("browser.add", "Add to chain"));
    row.appendChild(plus);

    row.addEventListener("click", () => addModule(mod.code));
    row.addEventListener("contextmenu", (event) => {
      if (event && typeof event.preventDefault === "function") event.preventDefault();
      addModule(mod.code);
    });

    item.appendChild(row);
    return item;
  }

  /** Rebuild the section list for the current module list and query. */
  function render() {
    clear(listEl);
    rowButtons = [];
    const searching = query.trim().length > 0;
    const needle = query.trim().toLowerCase();
    const visible = searching
      ? modules.filter((m) => m.display_name.toLowerCase().includes(needle))
      : modules.slice();

    if (visible.length === 0) {
      const empty = make(doc, "div", "mb-empty");
      empty.textContent = searching
        ? t("browser.noMatch", "No modules match “{query}”", { query: query.trim() })
        : t("browser.empty", "No modules available");
      listEl.appendChild(empty);
      return;
    }

    for (const [category, items] of groupByCategory(visible)) {
      const open = searching ? true : !collapsed.has(category);
      const section = make(doc, "section", "mb-section");
      section.setAttribute("data-category", category);
      section.setAttribute("data-open", open ? "true" : "false");

      const head = make(doc, "button", "mb-section-head");
      head.setAttribute("type", "button");
      head.setAttribute("aria-expanded", open ? "true" : "false");
      const caret = make(doc, "span", "mb-caret", "▾");
      caret.setAttribute("aria-hidden", "true");
      head.appendChild(caret);
      head.appendChild(make(doc, "span", "mb-section-name", categoryLabel(category)));
      head.appendChild(make(doc, "span", "mb-count", String(items.length)));
      head.addEventListener("click", () => {
        if (collapsed.has(category)) collapsed.delete(category);
        else collapsed.add(category);
        render();
      });
      section.appendChild(head);

      const list = make(doc, "ul", "mb-items");
      for (const mod of items) {
        const item = buildRow(mod, category, searching);
        list.appendChild(item);
        if (open) rowButtons.push(item.firstChild);
      }
      section.appendChild(list);
      listEl.appendChild(section);
    }
  }

  /* ---- keyboard navigation between rows ---- */
  root.addEventListener("keydown", (event) => {
    if (!event || rowButtons.length === 0) return;
    const key = event.key;
    if (key !== "ArrowDown" && key !== "ArrowUp" && key !== "Home" && key !== "End") return;
    const active = doc.activeElement || null;
    let at = rowButtons.indexOf(active);
    if (at < 0) return;
    event.preventDefault();
    let next = at;
    if (key === "ArrowDown") next = Math.min(rowButtons.length - 1, at + 1);
    else if (key === "ArrowUp") next = Math.max(0, at - 1);
    else if (key === "Home") next = 0;
    else next = rowButtons.length - 1;
    const target = rowButtons[next];
    if (target && typeof target.focus === "function") target.focus();
  });

  render();

  return {
    /** Replace the module list (the current filter and collapsed state are kept). */
    update(nextModules) {
      modules = (Array.isArray(nextModules) ? nextModules : []).map(normalizeModule);
      render();
    },
    /** Set the filter text (case-insensitive substring match on the display name). */
    setQuery(nextQuery) {
      query = String(nextQuery == null ? "" : nextQuery);
      /* Only the field we own is written back to: an external field belongs to
         the shell and writing to it while the user types would move the caret. */
      if (searchField && searchField.value !== query) searchField.value = query;
      render();
    },
  };
}
