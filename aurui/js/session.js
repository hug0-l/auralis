/* Auralis — session control: the undo / redo history and the session document.
 *
 * The controller owns the edit history of the workbench.  Every user action
 * (adding a module, removing one, reordering the chain, switching a module in
 * or out, turning a knob, emptying the chain, opening a session) is recorded
 * as exactly one step of the history.  Consecutive changes of the same
 * parameter are folded into one step, so a knob drag is undone in one go
 * instead of one step per frame.  The folding window is one second, the same
 * window the core history uses.
 *
 * The history lives in the client, but every step is carried out on the
 * server: an undo replays the inverse operation through the ordinary chain
 * routes (POST /api/chain, DELETE /api/chain/{index}, POST /api/chain/reorder,
 * POST /api/chain/{index}/bypass, POST /api/chain/{index}/param).  The server
 * therefore stays the single owner of the chain and the client never has to
 * reverse a state on its own.
 *
 * A step of the history addresses its module by *code*, not by index: a
 * module that moved in the chain is found again by its code, and a module
 * that was removed is put back between the neighbours it had, so an undo
 * still works when the chain changed in the meantime.
 *
 * What the interface can do, and what the history makes of it:
 *
 *   action                     undoable   the step of the history
 *   -------------------------  ---------  ---------------------------
 *   add a module               yes        "Add <module>"
 *   remove a module            yes        "Remove <module>"
 *   move a module              yes        "Reorder <module>"
 *   switch a module out        yes        "Bypass <module>" / "Enable"
 *   turn a knob                yes        "Change <parameter>" (one
 *                                         step per gesture)
 *   empty the chain            yes        "Clear Chain"
 *   start a new session        yes        "New Session"
 *   open a session             yes        "Open <session>"
 *   save the session           no         it does not change the session
 *   read the chain             no         it does not change the session
 *   close the controller       no         it does not change the session
 *
 * The session document is the format of aurcore/document.py: the audio the
 * session works on, the chain, the view state, the markers and a summary of
 * the history.  It is built from the chain the server reports and is handed
 * to the session transport — the server route (POST /api/session/save and
 * POST /api/session/load), otherwise a local store.  The server writes the
 * chain into the document itself, so the module state (the learned profile,
 * the region of the spectral editor, ...) travels with the session; the
 * client adopts the state the server reports.
 *
 * Contract notes
 *  - Every colour comes from css/tokens.css (variable references only).
 *  - No top-level DOM access: the document is resolved inside the functions,
 *    so this module can be imported by a head-less test harness.
 *  - No third-party product or vendor names appear anywhere in this file.
 *
 * Public API
 *   createSessionController({ api, engine, onChange }) -> controller
 *     undo(), redo(), canUndo, canRedo, save(), load(), open(), newSession(),
 *     historyLabels, destroy()
 *     plus: addModule(), removeModule(), reorder(), setBypass(), setParam(),
 *     clearChain(), buildDocument(), refresh(), snapshot(), subscribe(),
 *     notifyError(), chain, dirty, lastLabel, redoLabels, mergeWindowMs,
 *     nextUndo, nextRedo, viewState, markers, setViewState(), setMarkers(),
 *     transportName
 *   renderSessionBar(container, controller) -> { update, destroy, element }
 */

import { t, onLocaleChange } from "./i18n.js";



export const MERGE_WINDOW_MS = 1000;
export const HISTORY_LIMIT = 200;
export const DOCUMENT_FORMAT = "auralis.document";
export const DOCUMENT_VERSION = 1;
export const CHAIN_FORMAT = "auralis.chain";
export const CHAIN_VERSION = 1;

/** The routes of the server that carry the session document. */
export const SESSION_ROUTES = Object.freeze({
  save: "/api/session/save",
  load: "/api/session/load",
  list: "/api/session/list",
});

/** The name a session gets when the caller does not give one. */
export const DEFAULT_SESSION_NAME = "session";

/** The id of the stylesheet this module injects into the document. */
const STYLE_ID = "aur-ui-session-style";

/** The key the local session store keeps the last document under. */
const LOCAL_STORE_KEY = "auralis.session";

/* ------------------------------------------------------------------ helpers */

/** The current time in milliseconds (monotonic enough for the merge window). */
function defaultClock() {
  return Date.now();
}

/**
 * Wrap a value in the typed-value envelope of the interchange format.
 *
 * A value that already carries an envelope is returned unchanged, so a chain
 * that came from the server keeps its types.
 */
export function wrapValue(value) {
  if (value && typeof value === "object" && typeof value.Type === "string" && "Value" in value) {
    return { Type: value.Type, Value: value.Value };
  }
  if (typeof value === "boolean") return { Type: "Bool", Value: value };
  if (typeof value === "number") {
    return Number.isInteger(value) ? { Type: "Int", Value: value } : { Type: "Float", Value: value };
  }
  if (typeof value === "string") return { Type: "String", Value: value };
  return { Type: "String", Value: value === null || value === undefined ? "" : String(value) };
}

/** Unwrap a typed value; a raw value is returned unchanged. */
export function unwrapValue(node, fallback) {
  if (node && typeof node === "object" && "Type" in node && "Value" in node) {
    if (node.Type === "Bool") return Boolean(node.Value);
    if (node.Type === "Int" || node.Type === "UInt") {
      const n = Number(node.Value);
      return Number.isFinite(n) ? Math.trunc(n) : fallback;
    }
    if (node.Type === "Float") {
      const n = Number(node.Value);
      return Number.isFinite(n) ? n : fallback;
    }
    if (node.Type === "Base64") return node.Value === undefined || node.Value === null ? "" : String(node.Value);
    return node.Value;
  }
  if (node === null || node === undefined) return fallback;
  return node;
}

/** The base64 state of a step, whether it is a raw string or an envelope. */
function stateOf(raw) {
  if (!raw) return "";
  if (typeof raw === "string") return raw;
  if (typeof raw === "object" && typeof raw.Value === "string") return raw.Value;
  return "";
}

/** Normalise one chain step into the fields the controller works with. */
function normaliseStep(raw, index) {
  const src = raw && typeof raw === "object" ? raw : {};
  const code = String(src.code || src.module_code || "");
  let bypass = false;
  if (typeof src.bypass === "boolean") bypass = src.bypass;
  else if (typeof src.bypassed === "boolean") bypass = src.bypassed;
  else if (typeof src.enabled === "boolean") bypass = !src.enabled;

  const params = {};
  if (src.params && typeof src.params === "object") {
    for (const [name, node] of Object.entries(src.params)) params[name] = wrapValue(node);
  }
  const values = {};
  for (const [name, node] of Object.entries(params)) values[name] = unwrapValue(node);

  return {
    index: Number.isFinite(src.index) ? Number(src.index) : index,
    code,
    display_name: String(src.display_name || src.name || code),
    category: String(src.category || ""),
    edition: String(src.edition || "standard"),
    bypass,
    params,
    values,
    state: stateOf(src.state),
  };
}

/** Normalise a chain (a list of steps) into the shape the controller uses. */
function normaliseChain(raw) {
  if (!Array.isArray(raw)) return [];
  return raw.map(normaliseStep);
}

/** The user-visible name of a step. */
function moduleName(step) {
  if (!step) return "module";
  return step.display_name || step.code || "module";
}

/** Is this object a plain (non array) object? */
function isPlainObject(value) {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

/** Read a value that may be given directly or through a function. */
function readOption(value) {
  if (typeof value === "function") {
    try {
      return value();
    } catch (_err) {
      return null;
    }
  }
  return value === undefined ? null : value;
}

/** The chain steps of a session document. */
function documentSteps(document) {
  if (!isPlainObject(document)) return [];
  const block = isPlainObject(document.chain) ? document.chain : document;
  return Array.isArray(block.steps) ? block.steps : [];
}

/**
 * Read an answer of a transport.
 *
 * A transport may answer with the document itself or with an envelope that
 * carries the document, the summary of the restored chain and the path of
 * the file.  Both shapes are accepted, so a transport written for an older
 * build keeps working.
 */
function asEnvelope(value, fallback) {
  if (!isPlainObject(value)) return { document: fallback || null, summary: null, path: "" };
  if (isPlainObject(value.document)) {
    return {
      document: value.document,
      summary: Array.isArray(value.summary) ? value.summary : null,
      path: String(value.path || ""),
    };
  }
  if (value.format || value.chain || Array.isArray(value.steps)) {
    return { document: value, summary: null, path: "" };
  }
  return { document: fallback || null, summary: null, path: "" };
}

/* ------------------------------------------------------------- the transport */

/**
 * A store that keeps the session document in the browser.
 *
 * It is the fallback for a host without a session route: the document is
 * written to the local storage of the browser (and to memory when the browser
 * has none, as in a test harness).
 */
function createLocalStore() {
  const memory = new Map();
  let backing = null;
  try {
    if (typeof globalThis !== "undefined" && globalThis.localStorage) backing = globalThis.localStorage;
  } catch (_err) {
    backing = null;
  }

  function read(key) {
    if (backing) {
      try {
        return backing.getItem(key);
      } catch (_err) {
        return null;
      }
    }
    return memory.has(key) ? memory.get(key) : null;
  }

  function write(key, text) {
    if (backing) {
      try {
        backing.setItem(key, text);
        return;
      } catch (_err) {
        /* fall through to the memory store */
      }
    }
    memory.set(key, text);
  }

  return {
    name: "local",
    async save(document) {
      write(LOCAL_STORE_KEY, JSON.stringify(document));
      return { document, path: LOCAL_STORE_KEY, summary: null };
    },
    async load(id) {
      const text = read(id || LOCAL_STORE_KEY);
      if (!text) throw new Error("no session document is stored");
      return { document: JSON.parse(text), summary: null, path: id || LOCAL_STORE_KEY };
    },
    async list() {
      const text = read(LOCAL_STORE_KEY);
      return text ? [{ id: LOCAL_STORE_KEY, name: "Last session", path: LOCAL_STORE_KEY }] : [];
    },
  };
}

/** The fetch of the host, or null when there is none. */
function resolveFetch(option) {
  if (typeof option === "function") return option;
  if (typeof globalThis !== "undefined" && typeof globalThis.fetch === "function") {
    return globalThis.fetch.bind(globalThis);
  }
  return null;
}

/**
 * A request against the server, in the shape of the api client.
 *
 * The answer is parsed as JSON; the error of the server becomes the message
 * of the thrown error, so the shell can show it.
 */
function createRequest(fetchOption) {
  return async function request(method, path, body) {
    const fetchFn = resolveFetch(fetchOption);
    if (!fetchFn) throw new Error("there is no fetch to talk to the server with");
    const init = { method, headers: { "Content-Type": "application/json" } };
    if (body !== undefined && body !== null) init.body = JSON.stringify(body);
    const res = await fetchFn(path, init);
    const text = typeof res.text === "function" ? await res.text() : "";
    let data = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch (_err) {
        data = null;
      }
    }
    if (!res.ok) {
      const message = (data && (data.error || data.detail)) || `${res.status} ${res.statusText || ""}`.trim();
      throw new Error(String(message));
    }
    return isPlainObject(data) ? data : {};
  };
}

/**
 * The transport of the session document over the routes of the server.
 *
 * The server builds the document from the chain it holds, so the module
 * state is part of the document; the answer carries the document back and
 * the controller adopts it.
 */
function createServerTransport(request, options = {}) {
  function nameOf(document, opts) {
    const wanted = (opts && opts.name) || document.name || options.name || DEFAULT_SESSION_NAME;
    return String(wanted || DEFAULT_SESSION_NAME);
  }

  return {
    name: "server",
    async save(document, opts = {}) {
      const body = {
        name: nameOf(document, opts),
        path: String((opts && opts.path) || ""),
        view: isPlainObject(document.view) ? document.view : {},
        markers: Array.isArray(document.markers) ? document.markers : [],
        audio: isPlainObject(document.audio) ? document.audio : {},
        history: document.history || null,
      };
      return asEnvelope(await request("POST", SESSION_ROUTES.save, body), document);
    },
    async load(id) {
      const wanted = isPlainObject(id) ? id : { name: String(id || "") };
      let body;
      if (wanted.path) {
        body = { path: String(wanted.path) };
      } else if (wanted.name && /[\\/]|\.json$/i.test(String(wanted.name))) {
        body = { path: String(wanted.name) };
      } else {
        body = { name: String(wanted.name || "") };
      }
      return asEnvelope(await request("POST", SESSION_ROUTES.load, body), null);
    },
    async list() {
      const answer = await request("GET", SESSION_ROUTES.list, null);
      const entries = Array.isArray(answer.sessions) ? answer.sessions : [];
      return entries.map((entry) => ({
        id: String(entry.name || entry.path || ""),
        name: String(entry.name || ""),
        path: String(entry.path || ""),
        saved: String(entry.saved || ""),
        steps: Number(entry.steps) || 0,
      }));
    },
  };
}

/**
 * The transport a session document travels through.
 *
 * The order is: an explicit transport, then the session methods of the api
 * client, then the routes of the server, then the local store of the
 * browser.
 */
function resolveTransport({ api, sessions, fetch: fetchOption }) {
  if (isPlainObject(sessions) && typeof sessions.save === "function" && typeof sessions.load === "function") {
    return {
      name: String(sessions.name || "custom"),
      async save(document, options) {
        return asEnvelope(await sessions.save(document, options), document);
      },
      async load(id) {
        return asEnvelope(await sessions.load(id), null);
      },
      async list() {
        return typeof sessions.list === "function" ? sessions.list() : [];
      },
    };
  }
  if (api && typeof api.saveSession === "function" && typeof api.loadSession === "function") {
    return {
      name: "server",
      async save(document, options) {
        return asEnvelope(await api.saveSession(document, options), document);
      },
      async load(id) {
        return asEnvelope(await api.loadSession(id), null);
      },
      async list() {
        return typeof api.sessions === "function" ? api.sessions() : [];
      },
    };
  }
  if (resolveFetch(fetchOption)) return createServerTransport(createRequest(fetchOption));
  return createLocalStore();
}

/* --------------------------------------------------------------- the history */

/**
 * One step of the undo history.
 *
 * `undo` and `redo` carry out the operation on the server, `mergeKey` names
 * the kind of step that may be folded into the step on top of the history and
 * `time` is the moment the step was recorded.
 */
function makeEntry({ label, mergeKey, undo, redo }) {
  return {
    label: String(label || t("session.actionDefault", "Action")),
    mergeKey: mergeKey || null,
    undo,
    redo,
    time: 0,
    merges: 0,
  };
}

/* ------------------------------------------------------------- the controller */

/**
 * Create the controller of one session.
 *
 * @param {object}   config             the configuration of the controller
 * @param {object}   config.api         the api client (see aurui/js/api.js)
 * @param {object}   [config.engine]    a local mirror of the chain
 * @param {Function} [config.onChange]  called after every change
 * @param {Function} [config.onError]   called when an action fails
 * @param {Function} [config.onRestore] called when a document is restored
 * @param {object}   [config.sessions]  the transport of the session document
 * @param {Function} [config.fetch]     the fetch of the host (the server route)
 * @param {object|Function} [config.viewState]   the state of the view
 * @param {object|Function} [config.audio]       the audio the session works on
 * @param {object|Function} [config.markers]     the markers of the timeline
 * @param {Function} [config.now]       the clock of the merge window (ms)
 * @param {number}   [config.mergeWindowMs] the merge window in milliseconds
 * @returns {object} the controller
 */
export function createSessionController(config = {}) {
  const api = config.api || null;
  const engine = config.engine || null;
  const primary = typeof config.onChange === "function" ? config.onChange : null;
  const onError = typeof config.onError === "function" ? config.onError : null;
  const onRestore = typeof config.onRestore === "function" ? config.onRestore : null;
  const clock =
    typeof config.now === "function"
      ? config.now
      : typeof config.clock === "function"
        ? config.clock
        : defaultClock;
  const mergeWindowMs = Number.isFinite(config.mergeWindowMs)
    ? Math.max(0, config.mergeWindowMs)
    : MERGE_WINDOW_MS;
  const transport = resolveTransport({ api, sessions: config.sessions, fetch: config.fetch });

  /** The chain the server reports, mirrored for the labels and the document. */
  let chain = readEngineChain();
  /** The steps that can be undone, oldest first. */
  const undoStack = [];
  /** The steps that can be redone, most recently undone last. */
  const redoStack = [];
  /** The listeners of the controller. */
  const listeners = [];
  /** The label of the last action. */
  let lastLabel = "";
  /** The controller is closed. */
  let closed = false;
  /** The fingerprint of the document that was saved last (null when never). */
  let savedFingerprint = null;
  /** Did the session change since it was started? */
  let changedSinceStart = false;
  /** The view state the controller knows. */
  let currentView = null;
  /** The markers the controller knows. */
  let currentMarkers = [];
  /** Does the view state come from the shell (true) or from a document? */
  let viewFromShell = true;
  /** Do the markers come from the shell (true) or from a document? */
  let markersFromShell = true;

  /* ------------------------------------------------------------- the mirror */

  /** Read the chain out of the local mirror of the engine. */
  function readEngineChain() {
    if (!engine) return [];
    try {
      if (typeof engine.chainSummary === "function") return normaliseChain(engine.chainSummary());
      if (typeof engine.getChain === "function") return normaliseChain(engine.getChain());
      if (typeof engine.chain === "function") return normaliseChain(engine.chain());
      if (Array.isArray(engine.chain)) return normaliseChain(engine.chain);
    } catch (_err) {
      return [];
    }
    return [];
  }

  /** Take the chain out of a response of the server. */
  function applyChainResponse(res) {
    if (!res || typeof res !== "object") return;
    const raw = Array.isArray(res.chain) ? res.chain : Array.isArray(res.steps) ? res.steps : null;
    if (raw) chain = normaliseChain(raw);
  }

  /** Write a parameter value into the mirror. */
  function setMirrorParam(index, name, value) {
    const step = chain[index];
    if (!step) return;
    step.params[name] = wrapValue(value);
    step.values[name] = unwrapValue(step.params[name]);
  }

  /** The index of a module in the mirror, found by code when it moved. */
  function resolveIndex(code, index) {
    if (index >= 0 && index < chain.length && (!code || chain[index].code === code)) return index;
    if (code) {
      const found = indexOfCode(code, index >= 0 ? index : 0);
      if (found >= 0) return found;
    }
    return -1;
  }

  /** The index of the module with this code that lies nearest to `near`. */
  function indexOfCode(code, near) {
    if (!code) return -1;
    let best = -1;
    let distance = Infinity;
    for (let index = 0; index < chain.length; index += 1) {
      if (chain[index].code !== code) continue;
      const away = Math.abs(index - near);
      if (away < distance) {
        distance = away;
        best = index;
      }
    }
    return best;
  }

  /**
   * The slot a module takes back, found by the neighbours it had.
   *
   * The history addresses the modules by code, so a module that was removed
   * is put back between the neighbours it had rather than at the index it
   * had: the chain may have grown or shrunk in the meantime.
   */
  function slotBetween(neighbours, fallback) {
    const near = Number.isFinite(fallback) ? fallback : 0;
    if (neighbours.after) {
      const at = indexOfCode(neighbours.after, near);
      if (at >= 0) return at;
    }
    if (neighbours.before) {
      const at = indexOfCode(neighbours.before, near);
      if (at >= 0) return at + 1;
    }
    return Math.max(0, Math.min(near, chain.length));
  }

  /** The neighbours of the module at `index` (their codes). */
  function neighboursOf(index) {
    return {
      before: index > 0 ? chain[index - 1].code : "",
      after: index + 1 < chain.length ? chain[index + 1].code : "",
    };
  }

  /** One step of the chain, in the shape of a chain document. */
  function captureStep(step) {
    return {
      code: step.code,
      display_name: step.display_name,
      enabled: !step.bypass,
      bypass: step.bypass,
      params: { ...step.params },
      state: step.state,
    };
  }

  /** The whole chain, in the shape of a chain document. */
  function captureChain() {
    return chain.map((step) => ({
      code: step.code,
      display_name: step.display_name,
      enabled: !step.bypass,
      bypass: step.bypass,
      params: { ...step.params },
      state: step.state,
    }));
  }

  /**
   * Take the chain of a document into the mirror.
   *
   * The summary of the server carries the names and the categories of the
   * modules, the document carries their state; both are merged, so the
   * mirror keeps what the shell paints from.
   */
  function adoptDocumentChain(document, summary) {
    const steps = documentSteps(document);
    const live = Array.isArray(summary) ? summary.map(normaliseStep) : [];
    const previous = chain;
    const out = [];
    const count = Math.max(steps.length, live.length);
    for (let index = 0; index < count; index += 1) {
      const raw = isPlainObject(steps[index]) ? steps[index] : {};
      const meta = live[index] || null;
      const old = previous[index] || null;
      const code = String(raw.code || (meta ? meta.code : "") || (old ? old.code : ""));
      const params = {};
      if (isPlainObject(raw.params)) {
        for (const [name, node] of Object.entries(raw.params)) params[name] = wrapValue(node);
      } else if (meta) {
        Object.assign(params, meta.params);
      } else if (old) {
        Object.assign(params, old.params);
      }
      let bypass;
      if (raw.enabled !== undefined) bypass = !Boolean(raw.enabled);
      else if (meta) bypass = Boolean(meta.bypass);
      else if (old) bypass = Boolean(old.bypass);
      else bypass = false;
      out.push({
        index,
        code,
        display_name: String(
          (meta && meta.display_name) || (old && old.display_name) || code
        ),
        category: String((meta && meta.category) || (old && old.category) || ""),
        edition: String((meta && meta.edition) || (old && old.edition) || "standard"),
        bypass,
        params,
        values: Object.fromEntries(Object.entries(params).map(([name, node]) => [name, unwrapValue(node)])),
        state: isPlainObject(steps[index]) ? stateOf(raw.state) : old ? old.state : "",
      });
    }
    chain = out;
  }

  /** Take the module states of a document into the mirror. */
  function adoptStates(document) {
    const steps = documentSteps(document);
    for (let index = 0; index < chain.length; index += 1) {
      const raw = steps[index];
      if (!isPlainObject(raw)) continue;
      chain[index].state = stateOf(raw.state);
    }
  }

  /* ------------------------------------------------------------ the history */

  /** The fingerprint of the session: what the document would carry. */
  function fingerprint() {
    return JSON.stringify({
      chain: chain.map((step) => ({
        code: step.code,
        bypass: step.bypass,
        params: step.values,
        state: step.state,
      })),
      view: readView(),
      markers: readMarkers(),
    });
  }

  /** Does the session differ from the document that was saved last? */
  function isDirty() {
    if (savedFingerprint === null) return changedSinceStart;
    return fingerprint() !== savedFingerprint;
  }

  /** The state of the controller, as the interface sees it. */
  function snapshot() {
    return {
      chain: chain.slice(),
      canUndo: undoStack.length > 0,
      canRedo: redoStack.length > 0,
      dirty: isDirty(),
      labels: undoStack.map((entry) => entry.label),
      redoLabels: redoStack.map((entry) => entry.label),
      lastLabel,
      nextUndo: undoStack.length ? undoStack[undoStack.length - 1].label : "",
      nextRedo: redoStack.length ? redoStack[redoStack.length - 1].label : "",
    };
  }

  /** Tell the listeners that something changed. */
  function notify() {
    if (closed) return;
    const state = snapshot();
    for (const listener of listeners) {
      try {
        listener(state);
      } catch (err) {
        console.warn("[auralis] a session listener failed", err);
      }
    }
    if (primary) {
      try {
        primary(state);
      } catch (err) {
        console.warn("[auralis] onChange failed", err);
      }
    }
  }

  /**
   * Record a step of the history.
   *
   * A step that continues the one on top of the history (the same knob, the
   * same module) is folded into it as long as the merge window has not
   * passed.  The folded step keeps the state of the *first* change, so one
   * undo steps back over the whole gesture.
   */
  function pushEntry(entry) {
    const now = Number(clock()) || 0;
    const top = undoStack[undoStack.length - 1];
    if (entry.mergeKey && top && top.mergeKey === entry.mergeKey && now - top.time <= mergeWindowMs) {
      top.redo = entry.redo;
      top.label = entry.label;
      top.time = now;
      top.merges += 1;
      redoStack.length = 0;
      return top;
    }
    entry.time = now;
    undoStack.push(entry);
    while (undoStack.length > HISTORY_LIMIT) undoStack.shift();
    redoStack.length = 0;
    return entry;
  }

  /** Refuse the work of a closed controller. */
  function assertOpen() {
    if (closed) throw new Error("the session controller is closed");
    if (!api) throw new Error("the session controller needs an api client");
  }

  /** Note that the session changed. */
  function markChanged() {
    changedSinceStart = true;
    lastLabel = undoStack.length ? undoStack[undoStack.length - 1].label : lastLabel;
  }

  /* ------------------------------------------------------------ the actions */

  /**
   * Add a module to the chain.
   *
   * @param {string} code the code of the module
   * @returns {Promise<object>} the step that was added
   */
  async function addModule(code) {
    assertOpen();
    if (!code) throw new Error("addModule needs a module code");
    const res = await api.addModule(code);
    applyChainResponse(res);
    const index = chain.length - 1;
    const step = chain[index] || null;
    const addedCode = step ? step.code : String(code);
    pushEntry(
      makeEntry({
        label: t("session.actionAdd", "Add {name}", { name: moduleName(step) }),
        mergeKey: null,
        undo: async () => {
          const at = resolveIndex(addedCode, index);
          if (at < 0) throw new Error("the module is no longer in the chain");
          applyChainResponse(await api.removeModule(at));
        },
        redo: async () => {
          applyChainResponse(await api.addModule(addedCode));
          const at = chain.length - 1;
          if (index >= 0 && index < at) applyChainResponse(await api.reorder(at, index));
        },
      })
    );
    markChanged();
    notify();
    return step;
  }

  /**
   * Remove a module from the chain.
   *
   * @param {number} index the position of the module
   * @returns {Promise<object|null>} the step that was removed
   */
  async function removeModule(index) {
    assertOpen();
    const step = chain[index];
    if (!step) throw new Error("there is no module at this position");
    const removed = captureStep(step);
    const neighbours = neighboursOf(index);
    applyChainResponse(await api.removeModule(index));
    pushEntry(
      makeEntry({
        label: t("session.actionRemove", "Remove {name}", { name: moduleName(step) }),
        mergeKey: null,
        undo: async () => {
          await restoreStep(removed, neighbours, index);
        },
        redo: async () => {
          const at = resolveIndex(removed.code, index);
          if (at < 0) throw new Error("the module is no longer in the chain");
          applyChainResponse(await api.removeModule(at));
        },
      })
    );
    markChanged();
    notify();
    return step;
  }

  /**
   * Put a module back into the chain, between the neighbours it had.
   *
   * The parameters and the bypass flag are written back; the module state is
   * written back as well when the server offers a route for it.
   */
  async function restoreStep(step, neighbours, fallback) {
    applyChainResponse(await api.addModule(step.code));
    const at = chain.length - 1;
    const slot = slotBetween(neighbours, fallback);
    if (slot !== at) {
      applyChainResponse(await api.reorder(at, slot));
    }
    const where = resolveIndex(step.code, slot);
    await applyParams(where, step.params);
    applyChainResponse(await api.setBypass(where, Boolean(step.bypass)));
    await restoreStateAt(where, step.state);
  }

  /**
   * Write the state of a module back, when the server can take it.
   *
   * The chain routes of the server do not carry the state of a module yet;
   * the route is used when the api client offers it, so the undo of a
   * removal keeps the learned profile of the module.
   */
  async function restoreStateAt(index, state) {
    if (!state) return false;
    if (!api || typeof api.setState !== "function") return false;
    await api.setState(index, state);
    if (chain[index]) chain[index].state = state;
    return true;
  }

  /**
   * Move a module to another position in the chain.
   *
   * @param {number} from the position of the module
   * @param {number} to   the new position of the module
   * @returns {Promise<object>} the step that was moved
   */
  async function reorder(from, to) {
    assertOpen();
    if (from === to) return chain[from] || null;
    const step = chain[from] || null;
    const neighbours = neighboursOf(from);
    applyChainResponse(await api.reorder(from, to));
    const movedCode = step ? step.code : "";
    pushEntry(
      makeEntry({
        label: t("session.actionReorder", "Reorder {name}", { name: moduleName(step) }),
        mergeKey: null,
        undo: async () => {
          const at = resolveIndex(movedCode, to);
          if (at < 0) throw new Error("the module is no longer in the chain");
          const slot = slotBetween(neighbours, from);
          if (slot !== at) applyChainResponse(await api.reorder(at, slot));
        },
        redo: async () => {
          const at = resolveIndex(movedCode, from);
          if (at < 0) throw new Error("the module is no longer in the chain");
          applyChainResponse(await api.reorder(at, to));
        },
      })
    );
    markChanged();
    notify();
    return step;
  }

  /**
   * Switch a module in or out of the chain.
   *
   * @param {number}  index  the position of the module
   * @param {boolean} bypass true when the module is switched out
   * @returns {Promise<object>} the step that was changed
   */
  async function setBypass(index, bypass) {
    assertOpen();
    const step = chain[index] || null;
    const previous = step ? Boolean(step.bypass) : false;
    const wanted = Boolean(bypass);
    const code = step ? step.code : "";
    applyChainResponse(await api.setBypass(index, wanted));
    pushEntry(
      makeEntry({
        label: wanted
          ? t("session.actionBypass", "Bypass {name}", { name: moduleName(step) })
          : t("session.actionEnable", "Enable {name}", { name: moduleName(step) }),
        mergeKey: null,
        undo: async () => {
          const at = resolveIndex(code, index);
          if (at < 0) throw new Error("the module is no longer in the chain");
          applyChainResponse(await api.setBypass(at, previous));
        },
        redo: async () => {
          const at = resolveIndex(code, index);
          if (at < 0) throw new Error("the module is no longer in the chain");
          applyChainResponse(await api.setBypass(at, wanted));
        },
      })
    );
    markChanged();
    notify();
    return step;
  }

  /**
   * Change one parameter of one module.
   *
   * Consecutive changes of the same parameter are folded into one step of the
   * history, so a knob drag is one step and not one step per frame.
   *
   * @param {number} index the position of the module
   * @param {string} name  the name of the parameter
   * @param {*}      value the new value
   * @returns {Promise<object>} the step that was changed
   */
  async function setParam(index, name, value) {
    assertOpen();
    if (!name) throw new Error("setParam needs a parameter name");
    const step = chain[index] || null;
    const previous = await readParam(index, name);
    applyChainResponse(await api.setParam(index, name, value));
    setMirrorParam(index, name, value);
    const code = step ? step.code : "";
    pushEntry(
      makeEntry({
        label: t("session.actionChange", "Change {name}", { name }),
        mergeKey: `param:${code}:${occurrenceOf(index, code)}:${name}`,
        undo: async () => {
          if (previous === undefined) return;
          const at = resolveIndex(code, index);
          if (at < 0) throw new Error("the module is no longer in the chain");
          applyChainResponse(await api.setParam(at, name, previous));
          setMirrorParam(at, name, previous);
        },
        redo: async () => {
          const at = resolveIndex(code, index);
          if (at < 0) throw new Error("the module is no longer in the chain");
          applyChainResponse(await api.setParam(at, name, value));
          setMirrorParam(at, name, value);
        },
      })
    );
    markChanged();
    notify();
    return step;
  }

  /**
   * The number of modules with this code that stand before this one.
   *
   * Two modules of the same kind in one chain are told apart by it, so a
   * knob of the first one and a knob of the second one never merge.
   */
  function occurrenceOf(index, code) {
    let count = 0;
    for (let at = 0; at < index && at < chain.length; at += 1) {
      if (chain[at].code === code) count += 1;
    }
    return count;
  }

  /** The value a parameter has right now. */
  async function readParam(index, name) {
    const step = chain[index];
    if (step && step.params && Object.prototype.hasOwnProperty.call(step.params, name)) {
      return unwrapValue(step.params[name]);
    }
    if (api && typeof api.params === "function") {
      const res = await api.params(index);
      const values = (res && res.values) || {};
      if (Object.prototype.hasOwnProperty.call(values, name)) return values[name];
    }
    return undefined;
  }

  /** Write a set of parameters into the module at `index`. */
  async function applyParams(index, params) {
    if (!params) return;
    for (const [name, node] of Object.entries(params)) {
      const value = unwrapValue(node);
      if (value === undefined) continue;
      applyChainResponse(await api.setParam(index, name, value));
      setMirrorParam(index, name, value);
    }
  }

  /* ---------------------------------------------------------- the document */

  /** The view state, normalised into the document's view block. */
  function normaliseView(raw) {
    const view = {
      zoom: 1.0,
      scroll: 0.0,
      frequency_range: [0.0, 22050.0],
      selection: null,
      view_mode: "composite",
      spectrogram: true,
    };
    const aliases = {
      frequencyRange: "frequency_range",
      freqRange: "frequency_range",
      frequency_range_hz: "frequency_range",
      viewMode: "view_mode",
      spectro: "spectrogram",
      show_spectrogram: "spectrogram",
      spectrogram_enabled: "spectrogram",
    };
    if (isPlainObject(raw)) {
      for (const [key, value] of Object.entries(raw)) {
        const name = aliases[key] || key;
        view[name] = value;
      }
    }
    const zoom = Number(view.zoom);
    view.zoom = Number.isFinite(zoom) ? zoom : 1.0;
    const scroll = Number(view.scroll);
    view.scroll = Number.isFinite(scroll) ? scroll : 0.0;
    view.spectrogram = Boolean(view.spectrogram);
    view.view_mode = String(view.view_mode || "composite");
    if (Array.isArray(view.frequency_range) && view.frequency_range.length >= 2) {
      const low = Number(view.frequency_range[0]) || 0;
      const high = Number(view.frequency_range[1]) || 0;
      view.frequency_range = [Math.min(low, high), Math.max(low, high)];
    } else {
      view.frequency_range = [0.0, 22050.0];
    }
    view.selection = normaliseSelection(view.selection);
    return view;
  }

  /** A time / frequency selection, or null. */
  function normaliseSelection(value) {
    if (!isPlainObject(value)) return null;
    const out = {};
    for (const key of ["t0", "t1", "f0", "f1"]) {
      if (key in value) {
        const n = Number(value[key]);
        out[key] = Number.isFinite(n) ? n : 0;
      }
    }
    if (!Object.keys(out).length) return null;
    out.t0 = out.t0 === undefined ? 0 : out.t0;
    out.t1 = out.t1 === undefined ? 0 : out.t1;
    out.f0 = out.f0 === undefined ? 0 : out.f0;
    out.f1 = out.f1 === undefined ? 0 : out.f1;
    return out;
  }

  /** The state of the view, as the document carries it. */
  function readView() {
    if (viewFromShell) {
      const raw = readOption(config.viewState !== undefined ? config.viewState : config.view);
      if (isPlainObject(raw)) currentView = raw;
    }
    return normaliseView(currentView);
  }

  /** The audio the session works on, normalised into the document's block. */
  function readAudio() {
    const raw = readOption(config.audio) || {};
    const src = isPlainObject(raw) ? raw : {};
    const block = {
      path: String(src.path || src.file || src.filename || ""),
      sample_rate: Number(src.sample_rate) || 0,
      channels: Number(src.channels) || 0,
      frames: Number(src.frames) || 0,
      content_hash: String(src.content_hash || ""),
      size: Number(src.size) || 0,
      mtime: Number(src.mtime) || 0,
    };
    for (const [key, value] of Object.entries(src)) {
      if (key in block) continue;
      block[key] = value;
    }
    return block;
  }

  /** The markers of the timeline, normalised. */
  function normaliseMarkers(raw) {
    if (!raw) return [];
    const list = Array.isArray(raw) ? raw : isPlainObject(raw) && Array.isArray(raw.markers) ? raw.markers : [raw];
    return list
      .filter(isPlainObject)
      .map((entry, index) => {
        const marker = {
          id: String(entry.id || `m${index + 1}`),
          name: String(entry.name || `Marker ${index + 1}`),
          start: Number(entry.start !== undefined ? entry.start : entry.time) || 0,
        };
        const end = entry.end;
        if (end === undefined || end === null) {
          marker.end = null;
          marker.kind = String(entry.kind || "marker");
        } else {
          marker.end = Number(end) || 0;
          marker.kind = String(entry.kind || "region");
          marker.start = Math.min(marker.start, marker.end);
          marker.end = Math.max(marker.start, marker.end);
        }
        return marker;
      });
  }

  /** The markers of the timeline, as the document carries them. */
  function readMarkers() {
    if (markersFromShell) {
      const raw = readOption(config.markers);
      if (raw !== null && raw !== undefined) currentMarkers = normaliseMarkers(raw);
    }
    return currentMarkers.map((marker) => ({ ...marker }));
  }

  /** The chain of the session as a chain document. */
  function buildChainDocument() {
    return {
      format: CHAIN_FORMAT,
      version: CHAIN_VERSION,
      steps: chain.map((step) => ({
        code: step.code,
        enabled: !step.bypass,
        params: Object.fromEntries(Object.entries(step.params).map(([name, node]) => [name, wrapValue(node)])),
        state: { Type: "Base64", Value: step.state || "" },
      })),
    };
  }

  /**
   * Build the session document of the current session.
   *
   * The document follows the format of aurcore/document.py: the audio, the
   * chain, the view state, the markers and a summary of the history.
   *
   * @returns {object} the document
   */
  function buildDocument() {
    return {
      format: DOCUMENT_FORMAT,
      version: DOCUMENT_VERSION,
      saved: new Date().toISOString(),
      audio: readAudio(),
      chain: buildChainDocument(),
      view: readView(),
      markers: readMarkers(),
      history: {
        depth: undoStack.length,
        labels: undoStack.map((entry) => entry.label),
        redo_labels: redoStack.map((entry) => entry.label),
        merges: undoStack.reduce((sum, entry) => sum + (entry.merges || 0), 0),
        can_undo: undoStack.length > 0,
        can_redo: redoStack.length > 0,
        next_undo: undoStack.length ? undoStack[undoStack.length - 1].label : null,
        next_redo: redoStack.length ? redoStack[redoStack.length - 1].label : null,
        depth_limit: HISTORY_LIMIT,
      },
    };
  }

  /* ---------------------------------------------------------- the transport */

  /**
   * Save the session.
   *
   * The document is built from the chain the server reports and is handed to
   * the session transport.  The server writes the chain into the document
   * itself, so the document that comes back carries the state of the
   * modules; the controller adopts it.
   *
   * @param {object} [options] options of the transport (a name, a path, ...)
   * @returns {Promise<object>} the saved document
   */
  async function save(options = {}) {
    assertOpen();
    const document = buildDocument();
    const envelope = await transport.save(document, options);
    const saved = (envelope && envelope.document) || document;
    adoptStates(saved);
    savedFingerprint = fingerprint();
    changedSinceStart = false;
    notify();
    return saved;
  }

  /**
   * Load a session document and restore the chain it carries.
   *
   * @param {object|string} [source] the document, its JSON or the name of a
   *                                 stored document
   * @returns {Promise<object>} the restored document
   */
  async function load(source) {
    assertOpen();
    let envelope = null;
    if (isPlainObject(source)) {
      envelope = { document: source, summary: null, path: "" };
    } else if (typeof source === "string") {
      const text = source.trim();
      if (text.startsWith("{")) envelope = { document: JSON.parse(text), summary: null, path: "" };
      else envelope = await transport.load(source);
    } else if (source === undefined || source === null) {
      envelope = await transport.load("");
    } else {
      envelope = await transport.load(source);
    }
    const document = envelope && envelope.document;
    if (!isPlainObject(document)) throw new Error("the session document is empty");
    await restoreDocument(document, envelope);
    return document;
  }

  /**
   * Open a session.
   *
   * Without an argument the most recent stored session is opened; with an
   * argument the source is handed to load().
   *
   * @param {object|string} [source] the document or the name of a session
   * @returns {Promise<object>} the restored document
   */
  async function open(source) {
    assertOpen();
    if (source !== undefined && source !== null) return load(source);
    const listed = await transport.list();
    const entries = Array.isArray(listed) ? listed : [];
    if (!entries.length) throw new Error("no session is stored");
    const entry = entries[0];
    const id = typeof entry === "string" ? entry : entry.id || entry.path || entry.name;
    return load(id);
  }

  /**
   * Restore a session document.
   *
   * The server restores the chain itself when it reads a document; a local
   * store leaves that to the client, which rebuilds the chain through the
   * chain routes.  Either way the load is one step of the history: an undo
   * brings the chain that was there before back.
   */
  async function restoreDocument(document, envelope) {
    const previous = captureChain();
    const previousView = readView();
    const previousMarkers = readMarkers();
    const steps = documentSteps(document);
    const summary = envelope && envelope.summary;

    if (summary) {
      adoptDocumentChain(document, summary);
    } else {
      await rebuildChain(steps);
      adoptDocumentChain(document, null);
    }

    const view = normaliseView(document.view);
    const markers = normaliseMarkers(document.markers);
    applyView(view, markers);

    pushEntry(
      makeEntry({
        label: t("session.actionOpen", "Open {name}", { name: sessionLabel(document) }),
        mergeKey: null,
        undo: async () => {
          await rebuildChain(previous);
          applyView(previousView, previousMarkers);
        },
        redo: async () => {
          await rebuildChain(steps);
          applyView(view, markers);
        },
      })
    );
    savedFingerprint = fingerprint();
    changedSinceStart = false;
    notify();
  }

  /** The name of a document, for the label of the step. */
  function sessionLabel(document) {
    const name = document && (document.name || document.title);
    if (name) return String(name);
    const path = document && document.path;
    if (path) return String(path).split(/[\\/]/).pop();
    return "session";
  }

  /**
   * Rebuild a chain on the server.
   *
   * The chain is emptied and the steps are written again, with their
   * parameters, their bypass flag and (when the server can take it) their
   * state.
   */
  async function rebuildChain(steps) {
    for (let index = chain.length - 1; index >= 0; index -= 1) {
      applyChainResponse(await api.removeModule(index));
    }
    for (const raw of steps || []) {
      if (!isPlainObject(raw)) continue;
      const code = String(raw.code || raw.module_code || "");
      if (!code) continue;
      applyChainResponse(await api.addModule(code));
      const at = chain.length - 1;
      const params = isPlainObject(raw.params) ? raw.params : {};
      await applyParams(at, params);
      const enabled = raw.enabled === undefined ? !Boolean(raw.bypass) : Boolean(raw.enabled);
      applyChainResponse(await api.setBypass(at, !enabled));
      await restoreStateAt(at, stateOf(raw.state));
    }
  }

  /**
   * Start a new session: the chain is emptied and the view is reset.
   *
   * The step is one step of the history: an undo brings the chain, the view
   * and the markers that were there before back.
   *
   * @returns {Promise<boolean>} true when the session is empty
   */
  async function newSession() {
    assertOpen();
    await syncChain();
    const previous = captureChain();
    const previousView = readView();
    const previousMarkers = readMarkers();
    for (let index = chain.length - 1; index >= 0; index -= 1) {
      applyChainResponse(await api.removeModule(index));
    }
    chain = [];
    applyView(null, []);
    pushEntry(
      makeEntry({
        label: t("session.actionNew", "New Session"),
        mergeKey: null,
        undo: async () => {
          await rebuildChain(previous);
          applyView(previousView, previousMarkers);
        },
        redo: async () => {
          for (let index = chain.length - 1; index >= 0; index -= 1) {
            applyChainResponse(await api.removeModule(index));
          }
          chain = [];
          applyView(null, []);
        },
      })
    );
    markChanged();
    notify();
    return true;
  }

  /**
   * Empty the chain.
   *
   * The whole chain is one step of the history: one undo brings the modules
   * back with their parameters.
   *
   * @returns {Promise<object[]>} the steps that were removed
   */
  async function clearChain() {
    assertOpen();
    await syncChain();
    if (!chain.length) return [];
    const removed = captureChain();
    for (let index = chain.length - 1; index >= 0; index -= 1) {
      applyChainResponse(await api.removeModule(index));
    }
    pushEntry(
      makeEntry({
        label: t("session.actionClearChain", "Clear Chain"),
        mergeKey: null,
        undo: async () => {
          await rebuildChain(removed);
        },
        redo: async () => {
          for (let index = chain.length - 1; index >= 0; index -= 1) {
            applyChainResponse(await api.removeModule(index));
          }
        },
      })
    );
    markChanged();
    notify();
    return removed;
  }

  /**
   * Hand the view state and the markers to the shell.
   *
   * The shell owns the view; after a document was restored it has to show
   * the view of that document, so the controller hands it over.
   */
  function applyView(view, markers) {
    currentView = isPlainObject(view) ? { ...view } : null;
    currentMarkers = normaliseMarkers(markers);
    viewFromShell = false;
    markersFromShell = false;
    if (onRestore) {
      try {
        onRestore({
          view: currentView ? { ...currentView } : null,
          markers: currentMarkers.map((marker) => ({ ...marker })),
          chain: chain.slice(),
        });
      } catch (err) {
        console.warn("[auralis] onRestore failed", err);
      }
    }
  }

  /**
   * Read the chain from the server and update the mirror.
   *
   * @returns {Promise<object[]>} the chain
   */
  async function refresh() {
    if (!api || typeof api.chain !== "function") return chain;
    applyChainResponse(await api.chain());
    notify();
    return chain;
  }

  /**
   * Read the chain of the server into the mirror, without telling anybody.
   *
   * The mirror of a fresh controller is empty, while the server may already
   * hold a chain; an action that empties the chain has to know what is on
   * the server, so it reads it first.
   */
  async function syncChain() {
    if (!api || typeof api.chain !== "function") return chain;
    try {
      applyChainResponse(await api.chain());
    } catch (_err) {
      /* the mirror stays as it is */
    }
    return chain;
  }

  /**
   * Undo the last step of the history.
   *
   * @returns {Promise<string|null>} the label of the step that was undone
   */
  async function undo() {
    assertOpen();
    const entry = undoStack.pop();
    if (!entry) return null;
    try {
      await entry.undo();
    } catch (err) {
      undoStack.push(entry);
      throw err;
    }
    redoStack.push(entry);
    changedSinceStart = true;
    lastLabel = entry.label;
    notify();
    return entry.label;
  }

  /**
   * Redo the step that was undone last.
   *
   * @returns {Promise<string|null>} the label of the step that was redone
   */
  async function redo() {
    assertOpen();
    const entry = redoStack.pop();
    if (!entry) return null;
    try {
      await entry.redo();
    } catch (err) {
      redoStack.push(entry);
      throw err;
    }
    undoStack.push(entry);
    changedSinceStart = true;
    lastLabel = entry.label;
    notify();
    return entry.label;
  }

  /**
   * Register a listener of the controller.
   *
   * @param {Function} listener called with the state of the session
   * @returns {Function} a function that removes the listener
   */
  function subscribe(listener) {
    if (typeof listener !== "function") return () => {};
    listeners.push(listener);
    return () => {
      const at = listeners.indexOf(listener);
      if (at >= 0) listeners.splice(at, 1);
    };
  }

  /**
   * Report a failed action.
   *
   * The shell owns the reporting (a toast, a message in the status bar); the
   * controller only forwards the error.
   *
   * @param {Error} err the error of the action
   * @returns {boolean} true when the error was reported
   */
  function notifyError(err) {
    if (onError) {
      try {
        onError(err);
        return true;
      } catch (_err) {
        /* the shell owns the reporting */
      }
    }
    console.warn("[auralis] a session action failed", err);
    return false;
  }

  /** Close the controller: the listeners are forgotten. */
  function destroy() {
    listeners.length = 0;
    closed = true;
    return true;
  }

  return {
    /* -- the history ------------------------------------------------------ */
    undo,
    redo,
    get canUndo() {
      return undoStack.length > 0;
    },
    get canRedo() {
      return redoStack.length > 0;
    },
    get historyLabels() {
      return undoStack.map((entry) => entry.label);
    },
    get redoLabels() {
      return redoStack.map((entry) => entry.label);
    },
    get lastLabel() {
      return lastLabel;
    },
    /** The label of the step an undo would revert. */
    get nextUndo() {
      return undoStack.length ? undoStack[undoStack.length - 1].label : "";
    },
    /** The label of the step a redo would replay. */
    get nextRedo() {
      return redoStack.length ? redoStack[redoStack.length - 1].label : "";
    },
    get dirty() {
      return isDirty();
    },
    get chain() {
      return chain.slice();
    },
    get mergeWindowMs() {
      return mergeWindowMs;
    },
    /** The name of the transport the document travels through. */
    get transportName() {
      return String(transport.name || "");
    },
    /** The view state the controller knows. */
    get viewState() {
      return readView();
    },
    /** The markers the controller knows. */
    get markers() {
      return readMarkers();
    },

    /* -- the session ------------------------------------------------------ */
    save,
    load,
    open,
    newSession,
    buildDocument,
    refresh,
    snapshot,
    subscribe,
    notifyError,
    destroy,

    /* -- the state of the shell ------------------------------------------- */
    /** Hand the view state of the shell to the controller. */
    setViewState(view) {
      currentView = isPlainObject(view) ? view : null;
      viewFromShell = true;
      return currentView;
    },
    /** Hand the markers of the shell to the controller. */
    setMarkers(markers) {
      currentMarkers = normaliseMarkers(markers);
      markersFromShell = true;
      return currentMarkers;
    },

    /* -- the actions ------------------------------------------------------ */
    addModule,
    removeModule,
    reorder,
    setBypass,
    setParam,
    clearChain,
  };
}

/* -------------------------------------------------------------- the toolbar */

const CSS = `
.ss-root { display: flex; align-items: center; gap: var(--sp-2); font-family: var(--font-ui); font-size: var(--fs-sm); color: var(--txt-1); }
.ss-btn { background: var(--bg-2); border: 1px solid var(--line); border-radius: var(--radius); color: var(--txt-0); font: inherit; line-height: 1.4; padding: var(--sp-1) var(--sp-3); cursor: pointer; }
.ss-btn:hover:not(:disabled) { background: var(--bg-3); }
.ss-btn:disabled { color: var(--txt-3); cursor: default; opacity: 0.55; }
.ss-btn:focus-visible { outline: 1px solid var(--sel-edge); outline-offset: 1px; }
.ss-undo:hover:not(:disabled) { border-color: var(--accent); }
.ss-redo:hover:not(:disabled) { border-color: var(--accent); }
.ss-save:hover:not(:disabled) { border-color: var(--accent-2); }
.ss-open:hover:not(:disabled) { border-color: var(--accent-2); }
.ss-label { color: var(--txt-2); max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ss-hint { color: var(--txt-3); max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ss-dirty { color: var(--accent-3); opacity: 0; }
.ss-dirty.ss-dirty-on { opacity: 1; }
.ss-sep { width: 1px; align-self: stretch; background: var(--line-soft); }
`;

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

/**
 * Render the session toolbar into `container`.
 *
 * The toolbar carries the undo and the redo button, the save and the open
 * button, the label of the last action, the hint that names what an undo
 * and a redo would do and the dirty marker.  The buttons follow the
 * history: undo and redo are disabled when there is nothing to undo or to
 * redo.
 *
 * @param {Element} container    the element that receives the toolbar
 * @param {object}  controller   the controller of the session
 * @returns {{ update: Function, destroy: Function }} the toolbar
 */
export function renderSessionBar(container, controller) {
  const doc = ownerDoc(container);
  if (!doc || !container) {
    throw new Error("renderSessionBar: a DOM container element is required");
  }
  if (!controller) {
    throw new Error("renderSessionBar: a session controller is required");
  }
  ensureStyle(doc);

  const root = make(doc, "div", "ss-root");
  root.setAttribute("role", "toolbar");

  const undoBtn = make(doc, "button", "ss-btn ss-undo", "");
  undoBtn.setAttribute("type", "button");
  const redoBtn = make(doc, "button", "ss-btn ss-redo", "");
  redoBtn.setAttribute("type", "button");
  const saveBtn = make(doc, "button", "ss-btn ss-save", "");
  saveBtn.setAttribute("type", "button");
  const openBtn = make(doc, "button", "ss-btn ss-open", "");
  openBtn.setAttribute("type", "button");

  const sep = make(doc, "span", "ss-sep");
  sep.setAttribute("aria-hidden", "true");

  const label = make(doc, "span", "ss-label", "");
  label.setAttribute("role", "status");
  label.setAttribute("aria-live", "polite");

  const hint = make(doc, "span", "ss-hint", "");
  hint.setAttribute("aria-live", "polite");

  const dirty = make(doc, "span", "ss-dirty", "*");
  dirty.setAttribute("aria-hidden", "true");

  root.append(undoBtn, redoBtn, saveBtn, openBtn, sep, label, hint, dirty);
  clear(container);
  container.appendChild(root);

  /**
   * The texts of the toolbar, in the language of the moment.
   *
   * The toolbar is built once and lives in the top bar, so the texts have to
   * follow the locale when it changes — otherwise the user reads half of the
   * interface in one language and half of it in another.
   */
  function translate() {
    root.setAttribute("aria-label", t("session.label", "Session"));
    undoBtn.textContent = t("session.undo", "Undo");
    undoBtn.setAttribute("title", t("session.undoTitle", "Undo the last change"));
    redoBtn.textContent = t("session.redo", "Redo");
    redoBtn.setAttribute("title", t("session.redoTitle", "Redo the change that was undone"));
    saveBtn.textContent = t("control.save", "Save");
    saveBtn.setAttribute("title", t("session.saveTitle", "Save the session"));
    openBtn.textContent = t("session.open", "Open");
    openBtn.setAttribute("title", t("session.openTitle", "Open a session"));
    dirty.setAttribute("title", t("session.dirtyTitle", "The session has unsaved changes"));
    update();
  }

  /* ------------------------------------------------------------- the wiring */

  /** Report a failed action to the shell. */
  function reportError(err) {
    if (typeof controller.notifyError === "function") controller.notifyError(err);
    else console.warn("[auralis] a session action failed", err);
  }

  /** Run an action of the controller and keep its failure from escaping. */
  function run(action) {
    try {
      const result = action();
      if (result && typeof result.catch === "function") result.catch(reportError);
    } catch (err) {
      reportError(err);
    }
  }

  undoBtn.addEventListener("click", () => {
    if (typeof controller.undo === "function") run(() => controller.undo());
  });
  redoBtn.addEventListener("click", () => {
    if (typeof controller.redo === "function") run(() => controller.redo());
  });
  saveBtn.addEventListener("click", () => {
    if (typeof controller.save === "function") run(() => controller.save());
  });
  openBtn.addEventListener("click", () => {
    if (typeof controller.open === "function") run(() => controller.open());
  });

  /* -------------------------------------------------------------- the state */

  /** The state of the controller, read the way the shell reads it. */
  function state() {
    if (typeof controller.snapshot === "function") {
      const snap = controller.snapshot();
      if (snap && typeof snap === "object") return snap;
    }
    return {
      canUndo: Boolean(controller.canUndo),
      canRedo: Boolean(controller.canRedo),
      dirty: Boolean(controller.dirty),
      lastLabel: String(controller.lastLabel || ""),
      nextUndo: String(controller.nextUndo || ""),
      nextRedo: String(controller.nextRedo || ""),
    };
  }

  /** The text that names what an undo and a redo would do. */
  function hintText(snap) {
    const parts = [];
    if (snap.canUndo) {
      parts.push(
        t("session.hintUndo", "undo: {name}", {
          name: snap.nextUndo || t("session.hintLastChange", "the last change"),
        })
      );
    }
    if (snap.canRedo) {
      parts.push(
        t("session.hintRedo", "redo: {name}", {
          name: snap.nextRedo || t("session.hintLastChange", "the last change"),
        })
      );
    }
    return parts.length ? parts.join(" · ") : t("session.hintNoHistory", "no history");
  }

  /** Show the state of the controller in the toolbar. */
  function update() {
    const snap = state();
    undoBtn.disabled = !snap.canUndo;
    redoBtn.disabled = !snap.canRedo;
    undoBtn.setAttribute(
      "title",
      snap.canUndo
        ? t("session.titleUndo", "Undo {name}", {
            name: snap.nextUndo || t("session.hintLastChange", "the last change"),
          })
        : t("session.titleNothingUndo", "Nothing to undo")
    );
    redoBtn.setAttribute(
      "title",
      snap.canRedo
        ? t("session.titleRedo", "Redo {name}", {
            name: snap.nextRedo || t("session.hintLastChange", "the last change"),
          })
        : t("session.titleNothingRedo", "Nothing to redo")
    );
    const text = String(snap.lastLabel || "");
    label.textContent = text;
    label.setAttribute(
      "title",
      text
        ? t("session.titleLastAction", "Last action: {name}", { name: text })
        : t("session.titleNoAction", "No action yet")
    );
    const words = hintText(snap);
    hint.textContent = words;
    hint.setAttribute("title", words);
    dirty.classList.toggle("ss-dirty-on", Boolean(snap.dirty));
    dirty.setAttribute(
      "title",
      snap.dirty
        ? t("session.dirtyTitle", "The session has unsaved changes")
        : t("session.savedTitle", "The session is saved")
    );
    return toolbar;
  }

  const toolbar = {
    /** Show the current state of the controller. */
    update,
    /** The element of the toolbar. */
    element: root,
    /** The buttons of the toolbar. */
    buttons: { undo: undoBtn, redo: redoBtn, save: saveBtn, open: openBtn },
    /** The label of the last action. */
    label,
    /** The hint that names the next undo and the next redo. */
    hint,
    /** The dirty marker. */
    dirty,
    /** The label of the step an undo would revert. */
    get undoLabel() {
      return String(state().nextUndo || "");
    },
    /** The label of the step a redo would replay. */
    get redoLabel() {
      return String(state().nextRedo || "");
    },
    /** Remove the toolbar from the document. */
    destroy() {
      if (unsubscribe) unsubscribe();
      if (unsubscribeLocale) unsubscribeLocale();
      if (root.parentNode) root.parentNode.removeChild(root);
      return true;
    },
  };

  // The toolbar follows the controller on its own; the shell may still call
  // update() when it repaints.
  const unsubscribe =
    typeof controller.subscribe === "function" ? controller.subscribe(() => update()) : null;

  /* The toolbar follows the locale as well: it lives in the top bar and is
     built only once. */
  const unsubscribeLocale =
    typeof onLocaleChange === "function" ? onLocaleChange(() => translate()) : null;

  translate();
  update();
  return toolbar;
}
