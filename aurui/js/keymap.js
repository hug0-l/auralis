/* Auralis — keyboard map.
 *
 * A declarative table of the keyboard commands of the workbench plus the
 * helpers that resolve an event against that table and print it.
 *
 * Why a table
 *  - The bindings are data, not code: the help overlay renders the very same
 *    table, so the documentation can never drift away from the behaviour.
 *  - A command declares *where* it applies (global / view / panel / browser);
 *    the resolver prefers the command of the focused region and falls back to
 *    the global commands.
 *
 * Rules honoured by matches()
 *  - Nothing is swallowed while the user types in a field (input, textarea,
 *    select or a contenteditable node).
 *  - A command only fires when exactly the modifiers it expects are held.
 *    Ctrl / Alt / Meta are always part of the comparison; Shift is part of it
 *    too, except for single letters, where the case of the key already
 *    carries that information.
 *  - Space never steals the activation of a focused button: the button keeps
 *    the key, the transport does not get it.
 *
 * Rules honoured by resolve()
 *  - While a dialog is open only the commands that carry the `modal` flag may
 *    fire.  A dialog that is open owns the keyboard: the chain does not react
 *    to "S" or "B" behind the dialog.
 *
 * The focus rules of the module
 *  - A dialog traps the focus: Tab and Shift+Tab cycle inside it.
 *  - The focus is given back when the dialog closes, so the user does not
 *    lose the place they were working at.
 *  - Escape leaves a field the user is typing in, so the focus can never get
 *    stuck.
 *
 * The browser keeps its own keys
 *  - Every binding of the map that a browser also uses is listed in
 *    BROWSER_BINDINGS, with the decision and the way the browser gets the
 *    key back.  The keys the map does not take are listed as well, so the
 *    table stays an audit and not a wish list.
 *
 * The module performs no DOM access at import time, so it can be imported by
 * a head-less test harness.
 */

import { el } from "./api.js";
import { t, onLocaleChange } from "./i18n.js";

/** The id of the help overlay of this module. */
const HELP_OVERLAY_ID = "help-overlay";

/* ------------------------------------------------------------------ keys */

/**
 * Canonical names for the keys that have no single character form.
 *
 * The table is looked up case-insensitively, so "esc", "Esc" and "ESC" all
 * resolve to "Escape".
 */
const KEY_ALIASES = {
  space: " ",
  spacebar: " ",
  esc: "Escape",
  escape: "Escape",
  enter: "Enter",
  return: "Enter",
  tab: "Tab",
  del: "Delete",
  delete: "Delete",
  backspace: "Backspace",
  plus: "+",
  equal: "=",
  equals: "=",
  minus: "-",
  underscore: "_",
  slash: "/",
  bracketleft: "[",
  bracketright: "]",
  comma: ",",
  period: ".",
  up: "ArrowUp",
  down: "ArrowDown",
  left: "ArrowLeft",
  right: "ArrowRight",
  pageup: "PageUp",
  pagedown: "PageDown",
  home: "Home",
  end: "End",
};

/** Modifier names accepted in a binding specification. */
const MODIFIER_NAMES = {
  ctrl: "ctrl",
  control: "ctrl",
  shift: "shift",
  alt: "alt",
  option: "alt",
  meta: "meta",
  cmd: "meta",
  command: "meta",
  super: "meta",
  win: "meta",
};

/** Friendly names used when a binding is printed. */
const DISPLAY_NAMES = {
  " ": "Space",
  Escape: "Esc",
  Delete: "Del",
  Backspace: "Backspace",
  Enter: "Enter",
  Tab: "Tab",
  ArrowUp: "Up",
  ArrowDown: "Down",
  ArrowLeft: "Left",
  ArrowRight: "Right",
  PageUp: "PageUp",
  PageDown: "PageDown",
  Home: "Home",
  End: "End",
};

/** True for a single alphabetic character. */
function isLetter(text) {
  return typeof text === "string" && text.length === 1 && /[a-z]/i.test(text);
}

/** Canonical form of a key token ("ESC" -> "Escape", "plus" -> "+"). */
function canonicalKey(token) {
  if (token === null || token === undefined) return "";
  const raw = String(token);
  /* A single character is taken as it is — that includes the space bar. */
  if (raw.length === 1) return raw;
  const trimmed = raw.trim();
  if (!trimmed) return "";
  const alias = KEY_ALIASES[trimmed.toLowerCase()];
  if (alias) return alias;
  return trimmed;
}

/**
 * Parse one binding specification such as "Ctrl+S", "Shift+Plus" or "F1".
 *
 * @param {string} spec
 * @returns {null|{ctrl:boolean, alt:boolean, meta:boolean, shift:boolean,
 *                 key:string, letter:boolean}}
 */
export function parseBinding(spec) {
  const raw = String(spec == null ? "" : spec).trim();
  if (!raw) return null;
  const parts = raw.split("+");
  const mods = { ctrl: false, alt: false, meta: false, shift: false };
  let token = "";
  for (const part of parts) {
    const piece = part.trim();
    if (!piece) continue;
    const modifier = MODIFIER_NAMES[piece.toLowerCase()];
    if (modifier) {
      mods[modifier] = true;
      continue;
    }
    token = piece;
  }
  if (!token) return null;
  const key = canonicalKey(token);
  if (!key) return null;
  return {
    ctrl: mods.ctrl,
    alt: mods.alt,
    meta: mods.meta,
    shift: mods.shift,
    key,
    letter: isLetter(key),
  };
}

/** Every specification of a command, as parsed bindings. */
function bindingsOf(command) {
  if (!command) return [];
  const raw = command.key === undefined ? command.binding : command.key;
  const list = Array.isArray(raw) ? raw : raw === undefined || raw === null ? [] : [raw];
  const out = [];
  for (const spec of list) {
    const parsed = parseBinding(spec);
    if (parsed) out.push(parsed);
  }
  return out;
}

/** Printable form of a single specification ("Ctrl+S", "Shift + +"). */
function formatBinding(spec) {
  const parts = [];
  if (spec.ctrl) parts.push("Ctrl");
  if (spec.alt) parts.push("Alt");
  if (spec.meta) parts.push("Meta");
  if (spec.shift) parts.push("Shift");
  const key = DISPLAY_NAMES[spec.key] || spec.key;
  parts.push(key);
  return parts.join("+");
}

/* --------------------------------------------------------------- contexts */

/** The regions of the interface and the context they own. */
export const CONTEXTS = ["global", "view", "panel", "browser"];

/** The element id that identifies each context. */
const CONTEXT_ROOTS = {
  rail: "browser",
  stage: "view",
  inspector: "panel",
};

/* ------------------------------------------------------------ the commands */

/**
 * The command table.
 *
 * Every entry carries:
 *   id           unique, dot separated command name
 *   label        short name used in the help table
 *   description  one line, shown in the help table
 *   context      "global" | "view" | "panel" | "browser"
 *   key          the default binding, a specification or a list of them
 *   display      optional, the binding as it is printed
 *   action       the action the command drives: the name of the function of
 *                the shell (or of the session controller) that carries it, or
 *                a list of names when the command drives more than one
 *   provider     optional, "shell" (the default) or "session"
 *   modal        optional, true when the command may fire while a dialog is
 *                open
 */
export const COMMANDS = [
  /* ---- global ---------------------------------------------------- */
  {
    id: "transport.preview",
    label: "Preview",
    description: "Play a short preview of the chain output.",
    context: "global",
    key: "Space",
    action: "preview",
  },
  {
    id: "transport.stop",
    label: "Stop",
    description: "Stop the preview.",
    context: "global",
    key: "S",
    action: "stopPreview",
  },
  {
    id: "transport.previewPath",
    label: "Preview path",
    description: "Switch the preview between the fast path and the full path.",
    context: "global",
    key: "Shift+P",
    action: "togglePreviewPath",
  },
  {
    id: "module.bypass",
    label: "Bypass module",
    description: "Bypass or re-enable the selected module.",
    context: "global",
    key: "B",
    action: "bypassSelected",
  },
  {
    id: "module.remove",
    label: "Remove module",
    description: "Remove the selected module from the chain.",
    context: "global",
    key: "Delete",
    action: "removeModule",
  },
  {
    id: "chain.selectPrev",
    label: "Previous module",
    description: "Select the previous module in the chain.",
    context: "global",
    key: "BracketLeft",
    action: "selectRelativeModule",
  },
  {
    id: "chain.selectNext",
    label: "Next module",
    description: "Select the next module in the chain.",
    context: "global",
    key: "BracketRight",
    action: "selectRelativeModule",
  },
  {
    id: "chain.movePrev",
    label: "Move module back",
    description: "Move the selected module one place earlier in the chain.",
    context: "global",
    key: "Ctrl+BracketLeft",
    action: "reorderModule",
  },
  {
    id: "chain.moveNext",
    label: "Move module forward",
    description: "Move the selected module one place later in the chain.",
    context: "global",
    key: "Ctrl+BracketRight",
    action: "reorderModule",
  },
  {
    id: "session.undo",
    label: "Undo",
    description: "Undo the last change of the chain.",
    context: "global",
    key: "Ctrl+Z",
    action: "undo",
    provider: "session",
  },
  {
    id: "session.redo",
    label: "Redo",
    description: "Redo the change that was undone last.",
    context: "global",
    key: "Ctrl+Shift+Z",
    action: "redo",
    provider: "session",
  },
  {
    id: "preset.save",
    label: "Save preset",
    description: "Save the settings of the selected module as a preset.",
    context: "global",
    key: "Ctrl+S",
    action: "openPresetDialog",
  },
  {
    id: "dialog.close",
    label: "Close dialog",
    description: "Close the open dialog.",
    context: "global",
    key: "Escape",
    action: "closeDialogs",
    modal: true,
  },
  {
    id: "help.toggle",
    label: "Keyboard help",
    description: "Show or hide this list of shortcuts.",
    context: "global",
    key: "F1",
    action: "toggleHelpOverlay",
    modal: true,
  },
  {
    id: "browser.focusSearch",
    label: "Focus module search",
    description: "Move the cursor into the module search field.",
    context: "global",
    key: "Slash",
    action: "focusModuleSearch",
  },

  /* ---- view ------------------------------------------------------ */
  {
    id: "view.zoomIn",
    label: "Zoom in",
    description: "Zoom the composite view in.",
    context: "view",
    key: ["Shift+Plus", "Equal"],
    display: "+ / =",
    action: "setZoom",
  },
  {
    id: "view.zoomOut",
    label: "Zoom out",
    description: "Zoom the composite view out.",
    context: "view",
    key: ["Shift+Underscore", "Minus"],
    display: "- / _",
    action: "setZoom",
  },
  {
    id: "view.fit",
    label: "Fit to window",
    description: "Fit the whole file into the viewport.",
    context: "view",
    key: "0",
    action: ["setZoom", "setScroll"],
  },
  {
    id: "view.reset",
    label: "Reset view",
    description: "Reset the zoom, the scroll position and the selection.",
    context: "view",
    key: "R",
    action: ["setZoom", "setScroll", "clearSelection"],
  },
  {
    id: "view.toggleFreqSelection",
    label: "Frequency selection",
    description: "Turn the frequency selection on or off.",
    context: "view",
    key: "F",
    action: "toggleSelectionMode",
  },
  {
    id: "view.toggleTimeSelection",
    label: "Time selection",
    description: "Turn the time selection on or off.",
    context: "view",
    key: "T",
    action: "toggleSelectionMode",
  },

  /* ---- panel ----------------------------------------------------- */
  {
    id: "inspector.cycleTabs",
    label: "Cycle inspector tabs",
    description: "Step through the Module, Chain and Analysis tabs.",
    context: "panel",
    key: "T",
    action: "cycleInspectorTab",
  },
  {
    id: "panel.presetMenu",
    label: "Module menu",
    description: "Open the module menu (preset, reset, remove).",
    context: "panel",
    key: "M",
    action: "openModuleMenu",
  },

  /* ---- browser --------------------------------------------------- */
  {
    id: "browser.addModule",
    label: "Add module",
    description: "Add the highlighted module to the chain.",
    context: "browser",
    key: "Enter",
    action: "addModule",
  },
];

/**
 * The commands whose action lives in the session controller.
 *
 * The controller is created by the shell; the map only names the action.
 */
export const SESSION_COMMANDS = COMMANDS.filter(
  (command) => command.provider === "session"
).map((command) => command.id);

/**
 * The commands the shell has to bind.
 *
 * The list is the audit trail of the wiring: a command that is not bound does
 * nothing, so the gap is written down instead of hiding in the code.  The
 * shell closes the list when it binds the command; the entries may go, they
 * may not come.
 */
export const SHELL_BINDING_PENDING = [
  "browser.addModule",
  "chain.movePrev",
  "chain.moveNext",
  "session.undo",
  "session.redo",
];

/* ------------------------------------------------------- the browser keys */

/**
 * The keys a browser uses and the decision of the map about them.
 *
 * Every entry names the key, what the browser does with it, whether the map
 * takes it (`taken`), whether the key is a binding of a command (`binding`),
 * why, and how the browser gets the key back.  The keys the map leaves alone
 * are listed as well: an audit that only names the keys it takes cannot show
 * that the rest was looked at.
 *
 * The `key` field is a binding specification, so the table can be compared
 * with the bindings of the command table mechanically.
 */
export const BROWSER_BINDINGS = [
  {
    key: "Ctrl+S",
    browser: "saves the page",
    taken: true,
    binding: true,
    why: "saving is what the user expects from this key; the product saves the preset of the selected module",
    back: "the browser keeps its own save in its menu, and Ctrl+Shift+S stays free",
  },
  {
    key: "Ctrl+O",
    browser: "opens a file",
    taken: false,
    binding: false,
    why: "the browser owns the key; the product opens its audio file from the Open button, which the keyboard reaches with Tab",
    back: "the map leaves the key alone",
  },
  {
    key: "Ctrl+F",
    browser: "finds in the page",
    taken: false,
    binding: false,
    why: "the product has no find action of its own, so the browser keeps the key",
    back: "the map leaves the key alone",
  },
  {
    key: "Ctrl+R",
    browser: "reloads the page",
    taken: false,
    binding: false,
    why: "the browser owns the key",
    back: "the map leaves the key alone",
  },
  {
    key: "Ctrl+T",
    browser: "opens a tab",
    taken: false,
    binding: false,
    why: "the browser owns the key",
    back: "the map leaves the key alone",
  },
  {
    key: "Ctrl+N",
    browser: "opens a window",
    taken: false,
    binding: false,
    why: "the browser owns the key",
    back: "the map leaves the key alone",
  },
  {
    key: "Ctrl+W",
    browser: "closes the tab",
    taken: false,
    binding: false,
    why: "the browser owns the key",
    back: "the map leaves the key alone",
  },
  {
    key: "Ctrl+P",
    browser: "prints the page",
    taken: false,
    binding: false,
    why: "the browser owns the key",
    back: "the map leaves the key alone",
  },
  {
    key: "Ctrl+D",
    browser: "bookmarks the page",
    taken: false,
    binding: false,
    why: "the browser owns the key",
    back: "the map leaves the key alone",
  },
  {
    key: "Ctrl+L",
    browser: "focuses the address bar",
    taken: false,
    binding: false,
    why: "the browser owns the key",
    back: "the map leaves the key alone",
  },
  {
    key: "Ctrl+Tab",
    browser: "switches the tab",
    taken: false,
    binding: false,
    why: "the browser owns the key",
    back: "the map leaves the key alone",
  },
  {
    key: "Ctrl+ArrowUp",
    browser: "scrolls the page",
    taken: false,
    binding: false,
    why: "the browser owns the key; the map never binds an arrow",
    back: "the map leaves the key alone",
  },
  {
    key: "Ctrl+ArrowDown",
    browser: "scrolls the page",
    taken: false,
    binding: false,
    why: "the browser owns the key; the map never binds an arrow",
    back: "the map leaves the key alone",
  },
  {
    key: "Ctrl+ArrowLeft",
    browser: "walks the history back",
    taken: false,
    binding: false,
    why: "the browser owns the key; the map never binds an arrow",
    back: "the map leaves the key alone",
  },
  {
    key: "Ctrl+ArrowRight",
    browser: "walks the history forward",
    taken: false,
    binding: false,
    why: "the browser owns the key; the map never binds an arrow",
    back: "the map leaves the key alone",
  },
  {
    key: "Ctrl+Plus",
    browser: "zooms the page in",
    taken: false,
    binding: false,
    why: "the browser owns the key; the product zooms its own view with the plain plus",
    back: "the map leaves the key alone",
  },
  {
    key: "Ctrl+Minus",
    browser: "zooms the page out",
    taken: false,
    binding: false,
    why: "the browser owns the key; the product zooms its own view with the plain minus",
    back: "the map leaves the key alone",
  },
  {
    key: "F1",
    browser: "opens the help of the browser",
    taken: true,
    binding: true,
    why: "the product carries a keyboard help of its own and this is the key the platform asks for",
    back: "the help of the browser stays reachable through the menu of the browser",
  },
  {
    key: "F5",
    browser: "reloads the page",
    taken: false,
    binding: false,
    why: "the browser owns the key",
    back: "the map leaves the key alone",
  },
  {
    key: "F11",
    browser: "switches the full screen",
    taken: false,
    binding: false,
    why: "the browser owns the key",
    back: "the map leaves the key alone",
  },
  {
    key: "F12",
    browser: "opens the developer tools",
    taken: false,
    binding: false,
    why: "the browser owns the key",
    back: "the map leaves the key alone",
  },
  {
    key: "Space",
    browser: "scrolls the page down and activates a focused button",
    taken: true,
    binding: true,
    why: "the transport is what the workbench expects from the key; a focused button keeps it, so the button is still activated",
    back: "the page is scrolled with the arrow keys, with PageUp and PageDown and with the scrollbar",
  },
  {
    key: "Escape",
    browser: "stops the loading and leaves the full screen",
    taken: true,
    binding: true,
    why: "the key closes the dialog and leaves a field the user is typing in",
    back: "the browser uses the key far more rarely than the product does",
  },
  {
    key: "Delete",
    browser: "deletes the selection",
    taken: true,
    binding: true,
    why: "the key removes the selected module",
    back: "a text field keeps the key: the map swallows nothing that comes from a field",
  },
  {
    key: "Backspace",
    browser: "walks the history back",
    taken: false,
    binding: false,
    why: "the browser owns the key; the map removes a module with Delete",
    back: "the map leaves the key alone",
  },
  {
    key: "Enter",
    browser: "activates the focused button or link",
    taken: true,
    binding: true,
    why: "the key adds the module the browser shows; the map cancels the activation of the row, so the module is added exactly once",
    back: "every other control of the interface keeps the key: the map binds it in the browser context only",
  },
  {
    key: "Slash",
    browser: "opens the quick find of the browser",
    taken: true,
    binding: true,
    why: "the key focuses the module search, as every editor in a browser does",
    back: "the find bar of the browser is still reachable with Ctrl+F, which the map does not bind",
  },
  {
    key: "Ctrl+Z",
    browser: "undoes the last action of the page and the last edit of a text field",
    taken: true,
    binding: true,
    why: "undo is the action a user expects from the key and the session controller carries it",
    back: "a text field keeps the key: the map swallows nothing that comes from a field",
  },
  {
    key: "Ctrl+Shift+Z",
    browser: "redoes the last edit of a text field",
    taken: true,
    binding: true,
    why: "redo belongs next to undo",
    back: "a text field keeps the key: the map swallows nothing that comes from a field",
  },
  {
    key: "Tab",
    browser: "moves the focus to the next control",
    taken: true,
    binding: true,
    binding: false,
    why: "a dialog that is open traps the focus, so the key cannot leave it",
    back: "outside a dialog the map does not touch the key",
  },
  {
    key: "Home",
    browser: "scrolls to the top of the page",
    taken: false,
    binding: false,
    why: "the browser owns the key; the widgets use it on a focused slider",
    back: "the map leaves the key alone",
  },
  {
    key: "End",
    browser: "scrolls to the end of the page",
    taken: false,
    binding: false,
    why: "the browser owns the key; the widgets use it on a focused slider",
    back: "the map leaves the key alone",
  },
  {
    key: "PageUp",
    browser: "scrolls one page up",
    taken: false,
    binding: false,
    why: "the browser owns the key; the widgets use it on a focused slider",
    back: "the map leaves the key alone",
  },
  {
    key: "PageDown",
    browser: "scrolls one page down",
    taken: false,
    binding: false,
    why: "the browser owns the key; the widgets use it on a focused slider",
    back: "the map leaves the key alone",
  },
];


/** The command with the given id, or null. */
export function commandById(id) {
  const key = String(id == null ? "" : id);
  for (const command of COMMANDS) if (command.id === key) return command;
  return null;
}

/* -------------------------------------------------------------- matching */

/**
 * True when the event targets a field the user may be typing in.
 *
 * @param {EventTarget|null} target
 * @returns {boolean}
 */
export function isTypingTarget(target) {
  if (!target) return false;
  if (target.isContentEditable) return true;
  const tag = String(target.tagName || "").toUpperCase();
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  const role =
    typeof target.getAttribute === "function"
      ? String(target.getAttribute("role") || "").toLowerCase()
      : "";
  if (role === "textbox" || role === "searchbox" || role === "combobox" || role === "spinbutton") {
    return true;
  }
  return false;
}

/**
 * True when the event targets a control the browser activates with a key.
 *
 * A button is activated with Space or Enter, and a link is activated with
 * Enter.  The map does not take those keys away from the control: the
 * transport may not start while the user is on a button that is waiting for
 * a click.
 *
 * @param {EventTarget|null} target
 * @returns {boolean}
 */
export function isActivatableTarget(target) {
  if (!target) return false;
  const tag = String(target.tagName || "").toUpperCase();
  if (tag === "BUTTON" || tag === "A" || tag === "SUMMARY") return true;
  if (tag === "INPUT") {
    const type = String(
      (typeof target.getAttribute === "function" && target.getAttribute("type")) ||
        target.type ||
        ""
    ).toLowerCase();
    return type === "button" || type === "submit" || type === "reset" || type === "checkbox" || type === "radio";
  }
  const role =
    typeof target.getAttribute === "function"
      ? String(target.getAttribute("role") || "").toLowerCase()
      : "";
  return (
    role === "button" ||
    role === "link" ||
    role === "menuitem" ||
    role === "menuitemcheckbox" ||
    role === "menuitemradio" ||
    role === "option" ||
    role === "switch" ||
    role === "tab"
  );
}

/** The modifiers an event carries. */
function modifiersOf(event) {
  return {
    ctrl: Boolean(event.ctrlKey),
    alt: Boolean(event.altKey),
    meta: Boolean(event.metaKey),
    shift: Boolean(event.shiftKey),
  };
}

/** Compare the key of an event with the key of a binding. */
function keyMatches(binding, eventKey) {
  if (binding.letter) return binding.key.toLowerCase() === eventKey.toLowerCase();
  return binding.key === eventKey;
}

/**
 * True when the event triggers the command.
 *
 * The event must not come from a field the user is typing in, and it must
 * carry exactly the modifiers the command expects.
 *
 * Space is the one key that is left to a control that waits for a click: a
 * focused button keeps the key, so the transport does not start under the
 * finger of the user.
 *
 * @param {KeyboardEvent|object} event
 * @param {object|string} command  a command object or a command id
 * @returns {boolean}
 */
export function matches(event, command) {
  if (!event) return false;
  const cmd = typeof command === "string" ? commandById(command) : command;
  if (!cmd) return false;
  if (isTypingTarget(event.target)) return false;
  const held = modifiersOf(event);
  const eventKey = canonicalKey(event.key);
  if (!eventKey) return false;
  const activatable = isActivatableTarget(event.target);
  for (const binding of bindingsOf(cmd)) {
    if (held.ctrl !== binding.ctrl) continue;
    if (held.alt !== binding.alt) continue;
    if (held.meta !== binding.meta) continue;
    /* The shift is part of the comparison for every key, letters included.
       The case of the key carries the information for a letter, but not
       for the MODIFIER: without this, "Ctrl+Shift+Z" would match the
       "Ctrl+Z" binding (both are letters), the undo would fire and the
       redo could never be reached from the keyboard. */
    if (held.shift !== binding.shift) continue;
    if (binding.key === " " && activatable) continue;
    if (keyMatches(binding, eventKey)) return true;
  }
  return false;
}

/**
 * The context of a node: the region of the interface it belongs to.
 *
 * @param {Node|null} node
 * @returns {"global"|"view"|"panel"|"browser"}
 */
export function contextOf(node) {
  let current = node;
  let guard = 0;
  while (current && guard < 64) {
    guard += 1;
    const id = current.id || (typeof current.getAttribute === "function" ? current.getAttribute("id") : "");
    if (id && CONTEXT_ROOTS[id]) return CONTEXT_ROOTS[id];
    current = current.parentNode;
  }
  return "global";
}

/* -------------------------------------------------- the dialogs and focus */

/** The ids of the dialogs of the interface. */
export const DIALOG_IDS = ["modal", HELP_OVERLAY_ID];

/** The elements the keyboard can reach. */
const FOCUSABLE_SELECTOR =
  'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])';

/**
 * True when the node is hidden from the user.
 *
 * @param {Element|null} node
 * @returns {boolean}
 */
export function isHidden(node) {
  if (!node) return true;
  if (node.hidden === true) return true;
  if (typeof node.getAttribute === "function") {
    if (node.getAttribute("hidden") !== null) return true;
    if (String(node.getAttribute("aria-hidden") || "").toLowerCase() === "true") return true;
  }
  if (node.classList && typeof node.classList.contains === "function") {
    if (node.classList.contains("hidden")) return true;
  }
  return false;
}

/**
 * The dialog that is open, or null.
 *
 * The dialog of the shell (`#modal`) and the help overlay of this module
 * (`#help-overlay`) are the two the interface knows; any other element that
 * declares itself a dialog counts as well.
 *
 * @param {Document|null} [doc]
 * @returns {Element|null}
 */
export function openDialog(doc) {
  const target = doc || (typeof document !== "undefined" ? document : null);
  if (!target) return null;
  if (typeof target.getElementById === "function") {
    for (const id of DIALOG_IDS) {
      const node = target.getElementById(id);
      if (node && !isHidden(node)) return node;
    }
  }
  if (typeof target.querySelectorAll === "function") {
    let list = [];
    try {
      list = target.querySelectorAll('[role="dialog"], [aria-modal="true"]') || [];
    } catch (_err) {
      return null;
    }
    for (const node of list) {
      if (node && !isHidden(node)) return node;
    }
  }
  return null;
}

/**
 * The elements of a container the keyboard can reach.
 *
 * @param {Element|null} container
 * @returns {Element[]}
 */
export function focusablesIn(container) {
  if (!container || typeof container.querySelectorAll !== "function") return [];
  let nodes = [];
  try {
    nodes = container.querySelectorAll(FOCUSABLE_SELECTOR) || [];
  } catch (_err) {
    return [];
  }
  const out = [];
  for (const node of nodes) {
    if (!node) continue;
    if (node.disabled === true) continue;
    if (isHidden(node)) continue;
    const tabindex =
      typeof node.getAttribute === "function" ? node.getAttribute("tabindex") : null;
    if (tabindex !== null && tabindex !== undefined && Number(tabindex) < 0) continue;
    out.push(node);
  }
  return out;
}

/**
 * Keep the focus inside the dialog that is open.
 *
 * A dialog that is open owns the keyboard: Tab and Shift+Tab cycle inside it
 * and can never leave it.
 *
 * @param {KeyboardEvent|object} event
 * @param {Element|null} dialog
 * @param {Document|null} [doc]
 * @returns {boolean} true when the event was handled
 */
export function trapFocus(event, dialog, doc) {
  if (!event || !dialog) return false;
  if (event.key !== "Tab") return false;
  const items = focusablesIn(dialog);
  if (typeof event.preventDefault === "function") event.preventDefault();
  if (!items.length) {
    if (typeof dialog.focus === "function") {
      try {
        dialog.focus();
      } catch (_err) {
        /* the dialog cannot take the focus — the key is still swallowed */
      }
    }
    return true;
  }
  const active = doc && doc.activeElement ? doc.activeElement : null;
  const at = items.indexOf(active);
  let next;
  if (at < 0) next = event.shiftKey ? items.length - 1 : 0;
  else next = (at + (event.shiftKey ? -1 : 1) + items.length) % items.length;
  const target = items[next];
  if (target && typeof target.focus === "function") {
    try {
      target.focus();
    } catch (_err) {
      /* the control cannot take the focus — the key is still swallowed */
    }
  }
  return true;
}

/** The element that had the focus before the current dialog took it. */
let focusBeforeDialog = null;

/**
 * Remember the element that has the focus now.
 *
 * @param {Element|null} node
 * @returns {Element|null} the remembered element
 */
export function rememberFocus(node) {
  focusBeforeDialog = node || null;
  return focusBeforeDialog;
}

/**
 * Give the focus back to the element it came from.
 *
 * @param {Document|null} [doc]
 * @returns {boolean} true when the focus was given back
 */
export function restoreFocus(doc) {
  const target = focusBeforeDialog;
  focusBeforeDialog = null;
  if (!target) return false;
  if (target.isConnected === false) return false;
  if (typeof target.focus !== "function") return false;
  try {
    target.focus();
  } catch (_err) {
    return false;
  }
  return true;
}

/**
 * The command an event triggers, honouring the context.
 *
 * The command of the focused region wins; the global commands are the
 * fallback.  While a dialog is open only the commands that carry the
 * `modal` flag may fire: the chain does not react behind a dialog.
 *
 * @param {KeyboardEvent|object} event
 * @param {string} [context]  the context to resolve against
 * @returns {object|null}     the command, or null
 */
export function resolve(event, context) {
  if (!event) return null;
  const ctx = context || contextOf(event.target);
  const doc =
    event.target && event.target.ownerDocument
      ? event.target.ownerDocument
      : typeof document !== "undefined"
        ? document
        : null;
  const dialog = openDialog(doc);
  for (const command of COMMANDS) {
    if (command.context !== ctx) continue;
    if (dialog && !command.modal) continue;
    if (matches(event, command)) return command;
  }
  for (const command of COMMANDS) {
    if (command.context !== "global") continue;
    if (dialog && !command.modal) continue;
    if (matches(event, command)) return command;
  }
  return null;
}

/* --------------------------------------------------------------- describe */

/**
 * The rows of the keyboard table.
 *
 * The label and the description come from the catalogue, so the help overlay
 * follows the active locale; the English text of the command table is the
 * fallback.
 *
 * @returns {Array<{context:string, contextLabel:string, id:string,
 *                  label:string, description:string, keys:string[]}>}
 */
export function describeRows() {
  const rows = [];
  for (const command of COMMANDS) {
    const bindings = bindingsOf(command);
    const keys =
      command.display !== undefined && command.display !== null
        ? [String(command.display)]
        : bindings.map(formatBinding);
    rows.push({
      context: command.context,
      contextLabel: t(`help.context.${command.context}`, command.context),
      id: command.id,
      label: t(`key.${command.id}.label`, command.label),
      description: t(`key.${command.id}.description`, command.description),
      keys,
    });
  }
  return rows;
}

/**
 * A printable table of every keyboard command.
 *
 * This is what the help overlay shows.
 *
 * @param {object} [options]
 * @param {string} [options.title]  heading of the table
 * @returns {string}
 */
export function describe(options = {}) {
  const opts = options && typeof options === "object" ? options : {};
  const title = opts.title || t("help.title", "Keyboard shortcuts");
  const rows = describeRows();
  const keyWidth = rows.reduce((max, row) => {
    const width = row.keys.join(" / ").length;
    return width > max ? width : max;
  }, 4);
  const lines = [];
  lines.push(title);
  lines.push("=".repeat(Math.max(title.length, 12)));
  let current = null;
  for (const row of rows) {
    if (row.context !== current) {
      current = row.context;
      lines.push("");
      lines.push(row.contextLabel.toUpperCase());
    }
    const keys = row.keys.join(" / ");
    lines.push(`  ${keys.padEnd(keyWidth)}  ${row.description}`);
  }
  return lines.join("\n");
}

/* ---------------------------------------------------------------- install */

/**
 * Install the keyboard map.
 *
 * Besides the commands the installer also keeps the focus honest:
 *  - a dialog that is open traps the focus;
 *  - the focus is given back when the dialog closes;
 *  - Escape leaves a field the user is typing in.
 *
 * @param {object} handlers  a map of command id -> function (event, command)
 * @param {object} [options]
 * @param {EventTarget} [options.target]  where to listen (default: window)
 * @param {string} [options.context]      the context to resolve against
 * @returns {Function} an uninstall function
 */
export function installKeymap(handlers = {}, options = {}) {
  const opts = options && typeof options === "object" ? options : {};
  const bag = handlers && typeof handlers === "object" ? handlers : {};
  const target =
    opts.target || (typeof window !== "undefined" && window ? window : null);
  if (!target || typeof target.addEventListener !== "function") return () => {};

  const doc =
    (target.ownerDocument && target.ownerDocument) ||
    (typeof document !== "undefined" ? document : null);

  const onKeyDown = (event) => {
    if (!event || event.defaultPrevented) return;

    /* A dialog that is open owns the keyboard: the focus may not leave it. */
    const dialog = openDialog(doc);
    if (dialog && trapFocus(event, dialog, doc)) return;

    /* Escape leaves the field the user is typing in, so the focus can never
       get stuck in a field the user cannot leave. */
    if (!dialog && event.key === "Escape" && isTypingTarget(event.target)) {
      if (typeof event.preventDefault === "function") event.preventDefault();
      if (event.target && typeof event.target.blur === "function") {
        try {
          event.target.blur();
        } catch (_err) {
          /* the field keeps the focus */
        }
      }
      return;
    }

    const command = resolve(event, opts.context);
    if (!command) return;
    let handler = bag[command.id];
    if (typeof handler !== "function" && command.id === "help.toggle") {
      handler = () => toggleHelpOverlay(doc);
    }
    if (typeof handler !== "function") return;
    if (typeof event.preventDefault === "function") event.preventDefault();
    handler(event, command);
  };

  /* The focus a dialog takes is given back when the dialog closes. */
  let tracked = null;

  const onFocusIn = (event) => {
    const node = event && event.target;
    if (!node) return;
    const dialog = openDialog(doc);
    if (!dialog) return;
    if (tracked !== dialog) {
      tracked = dialog;
      if (event.relatedTarget) rememberFocus(event.relatedTarget);
    }
  };

  const syncFocus = () => {
    if (!tracked) return;
    const dialog = openDialog(doc);
    if (dialog) {
      /* Another dialog took over — the focus belongs to it now. */
      tracked = dialog;
      return;
    }
    tracked = null;
    restoreFocus(doc);
  };

  let observer = null;
  const view = doc && doc.defaultView ? doc.defaultView : null;
  if (view && typeof view.MutationObserver === "function" && doc.body) {
    observer = new view.MutationObserver(() => {
      syncFocus();
    });
    observer.observe(doc.body, {
      attributes: true,
      attributeFilter: ["class", "hidden"],
      subtree: true,
    });
  }

  target.addEventListener("keydown", onKeyDown);
  if (doc && typeof doc.addEventListener === "function") {
    doc.addEventListener("focusin", onFocusIn);
  }

  return () => {
    if (typeof target.removeEventListener === "function") {
      target.removeEventListener("keydown", onKeyDown);
    }
    if (doc && typeof doc.removeEventListener === "function") {
      doc.removeEventListener("focusin", onFocusIn);
    }
    if (observer && typeof observer.disconnect === "function") observer.disconnect();
  };
}

/* -------------------------------------------------------- accessibility */

/** Accessible names for the controls that carry none. */
const KNOWN_LABELS = [
  ["#btn-open", "topbar.open.title"],
  ["#btn-play", "transport.preview.title"],
  ["#btn-stop", "transport.stop.title"],
  ["#btn-loop", "transport.loop"],
  ["#btn-compare", "topbar.compare.title"],
  ["#btn-render", "topbar.render.title"],
  ["#btn-zoom-in", "control.zoomIn"],
  ["#btn-zoom-out", "control.zoomOut"],
  ["#btn-zoom-fit", "control.fit"],
  ["#modal-close", "control.closeDialog"],
  ["#module-search", "browser.search"],
  ["#freq-lo", "control.frequency"],
  ["#freq-hi", "control.frequency"],
];

/** True when the node already carries an accessible name. */
function hasAccessibleName(node) {
  if (!node) return false;
  if (typeof node.getAttribute === "function") {
    const label = node.getAttribute("aria-label");
    if (label && label.trim()) return true;
    if (node.getAttribute("aria-labelledby")) return true;
    const title = node.getAttribute("title");
    if (title && title.trim()) return true;
  }
  const tag = String(node.tagName || "").toUpperCase();
  if (tag === "BUTTON" || tag === "A") {
    const text = String(node.textContent || "").trim();
    if (text) return true;
  }
  if (typeof node.closest === "function" && node.closest("label")) return true;
  return false;
}

/**
 * Give the interface the accessibility furniture it needs.
 *
 * The shell owns the markup, so the live regions, the roles and the missing
 * accessible names are applied here at start-up rather than in the HTML.
 *
 * @param {Element|Document} [root]
 * @returns {number} the number of nodes that were touched
 */
export function applyAccessibility(root) {
  const scope =
    root || (typeof document !== "undefined" ? document : null);
  if (!scope || typeof scope.querySelector !== "function") return 0;
  const query = (selector) => scope.querySelector(selector);
  const queryAll = (selector) =>
    typeof scope.querySelectorAll === "function"
      ? Array.prototype.slice.call(scope.querySelectorAll(selector))
      : [];
  let touched = 0;

  /* The toast announces what happened. */
  const toast = query("#toast");
  if (toast) {
    toast.setAttribute("role", "status");
    toast.setAttribute("aria-live", "polite");
    toast.setAttribute("aria-atomic", "true");
    touched += 1;
  }

  /* The status bar readouts announce their new value. */
  for (const selector of ["#st-cursor", "#st-sel", "#st-level", "#st-latency"]) {
    const node = query(selector);
    if (!node) continue;
    node.setAttribute("aria-live", "polite");
    node.setAttribute("aria-atomic", "true");
    touched += 1;
  }
  const engine = query("#st-engine");
  if (engine) {
    engine.setAttribute("role", "status");
    engine.setAttribute("aria-live", "polite");
    touched += 1;
  }

  /* The viewport is a focus target, so the view shortcuts have a home. */
  const wrap = query("#canvas-wrap");
  if (wrap) {
    wrap.setAttribute("tabindex", "0");
    wrap.setAttribute("role", "group");
    wrap.setAttribute("aria-label", t("app.tagline", "Audio restoration workbench"));
    touched += 1;
  }
  for (const selector of ["#spec-canvas", "#wave-canvas"]) {
    const canvas = query(selector);
    if (!canvas) continue;
    canvas.setAttribute("role", "img");
    if (!canvas.getAttribute("aria-label")) {
      canvas.setAttribute("aria-label", t("topbar.viewMode.spectrogram", "Spectrogram"));
    }
    touched += 1;
  }

  /* Accessible names for the controls that only carry a symbol. */
  for (const [selector, key] of KNOWN_LABELS) {
    const node = query(selector);
    if (!node || hasAccessibleName(node)) continue;
    node.setAttribute("aria-label", t(key, key));
    touched += 1;
  }
  for (const node of queryAll("button, input, select, textarea, a[href]")) {
    if (hasAccessibleName(node)) continue;
    const title =
      typeof node.getAttribute === "function" ? node.getAttribute("title") || "" : "";
    if (!title.trim()) continue;
    node.setAttribute("aria-label", title.trim());
    touched += 1;
  }

  /* The segmented view switch behaves like a radio group. */
  const viewMode = query("#view-mode");
  if (viewMode) {
    viewMode.setAttribute("role", "radiogroup");
    viewMode.setAttribute("aria-label", t("topbar.viewMode", "View mode"));
    for (const button of queryAll("#view-mode button")) {
      const on = button.classList && button.classList.contains("on");
      button.setAttribute("role", "radio");
      button.setAttribute("aria-checked", on ? "true" : "false");
      touched += 1;
    }
  }

  /* The inspector tabs behave like a tab list. */
  const tabs = query("#inspector-tabs");
  if (tabs) {
    tabs.setAttribute("role", "tablist");
    tabs.setAttribute("aria-label", t("inspector.tabs", "Inspector tabs"));
    for (const button of queryAll("#inspector-tabs button")) {
      const on = button.classList && button.classList.contains("on");
      button.setAttribute("role", "tab");
      button.setAttribute("aria-selected", on ? "true" : "false");
      touched += 1;
    }
  }

  /* The chain strip is a list of the modules in signal order. */
  const chain = query("#chain-strip");
  if (chain) {
    chain.setAttribute("role", "list");
    chain.setAttribute("aria-label", t("chain.label", "Signal chain"));
    touched += 1;
  }

  return touched;
}

/* ---------------------------------------------------------- the help overlay */

/**
 * Build the keyboard help overlay.
 *
 * @param {Document} [doc]
 * @returns {Element} the overlay element
 */
export function buildHelpOverlay(doc) {
  const target = doc || (typeof document !== "undefined" ? document : null);
  if (!target) return null;

  const overlay = el("div", {
    id: HELP_OVERLAY_ID,
    class: "help-overlay hidden",
    role: "dialog",
    "aria-modal": "true",
    "aria-labelledby": "help-overlay-title",
  });

  const box = el("div", { class: "help-overlay-box" });
  const head = el("div", { class: "help-overlay-head" });
  const title = el("h2", { id: "help-overlay-title", class: "help-overlay-title" });
  title.textContent = t("help.title", "Keyboard shortcuts");
  const close = el("button", {
    type: "button",
    class: "help-overlay-close",
    "aria-label": t("help.close", "Close keyboard shortcuts"),
  });
  close.textContent = "×";
  close.addEventListener("click", () => hideHelpOverlay(target));
  head.append(title, close);

  const body = el("div", { class: "help-overlay-body" });
  const table = el("table", { class: "help-table" });
  const thead = el("thead");
  const headRow = el("tr");
  const heads = [];
  for (const key of ["help.column.key", "help.column.action", "help.column.context"]) {
    const th = el("th", { class: "help-th", scope: "col" });
    th.setAttribute("data-i18n-key", key);
    headRow.append(th);
    heads.push(th);
  }
  thead.append(headRow);
  table.append(thead);

  const tbody = el("tbody");
  table.append(tbody);

  /* The rows of the table, and the cells that carry the texts of the locale.
     The table is built once; the texts are written again when the locale
     changes, so the help follows the language of the interface. */
  const cells = [];
  let current = null;
  for (const row of describeRows()) {
    let group = null;
    if (row.context !== current) {
      current = row.context;
      const groupRow = el("tr", { class: "help-row help-row-group" });
      const cell = el("th", { class: "help-th", colspan: "3", scope: "colgroup" });
      cell.textContent = row.contextLabel;
      groupRow.append(cell);
      tbody.append(groupRow);
      group = cell;
    }
    const tr = el("tr", { class: "help-row" });
    const keys = el("td", { class: "help-td help-keys" });
    const keyNodes = [];
    for (const key of row.keys) {
      const kbd = el("kbd", { class: "kbd" });
      kbd.textContent = key;
      keyNodes.push(kbd);
      keys.append(kbd);
    }
    const action = el("td", { class: "help-td help-action" });
    action.textContent = row.description;
    action.setAttribute("title", row.label);
    const context = el("td", { class: "help-td help-context" });
    context.textContent = row.contextLabel;
    tr.append(keys, action, context);
    tbody.append(tr);
    cells.push({ group, keyNodes, action, context });
  }
  body.append(table);

  const foot = el("div", { class: "help-overlay-foot" });
  const done = el("button", { type: "button", class: "tbtn tbtn-primary" });
  done.textContent = t("control.close", "Close");
  done.addEventListener("click", () => hideHelpOverlay(target));
  foot.append(done);

  box.append(head, body, foot);
  overlay.append(box);
  overlay.addEventListener("mousedown", (event) => {
    if (event && event.target === overlay) hideHelpOverlay(target);
  });

  /**
   * The texts of the overlay, in the language of the moment.
   *
   * The overlay is built once and shown again and again, so it has to
   * follow the locale — otherwise the help of the keyboard map stays in the
   * language of the first visit.
   */
  function translate() {
    title.textContent = t("help.title", "Keyboard shortcuts");
    close.setAttribute("aria-label", t("help.close", "Close keyboard shortcuts"));
    for (const th of heads) {
      const key = th.getAttribute("data-i18n-key") || "";
      th.textContent = t(key, key);
    }
    done.textContent = t("control.close", "Close");
    const rows = describeRows();
    for (let index = 0; index < cells.length; index += 1) {
      const cell = cells[index];
      const row = rows[index];
      if (!row) continue;
      if (cell.group) cell.group.textContent = row.contextLabel;
      for (let k = 0; k < cell.keyNodes.length; k += 1) {
        if (row.keys[k] !== undefined) cell.keyNodes[k].textContent = row.keys[k];
      }
      cell.action.textContent = row.description;
      cell.action.setAttribute("title", row.label);
      cell.context.textContent = row.contextLabel;
    }
  }

  translate();
  if (typeof onLocaleChange === "function") onLocaleChange(translate);
  return overlay;
}

/**
 * Show (or hide) the keyboard help overlay.
 *
 * The overlay takes the focus when it opens and gives it back when it
 * closes, so the user does not lose the place they were working at.
 *
 * @param {Document} [doc]
 * @returns {Element|null} the overlay
 */
export function toggleHelpOverlay(doc) {
  const target = doc || (typeof document !== "undefined" ? document : null);
  if (!target) return null;
  let overlay = target.getElementById ? target.getElementById(HELP_OVERLAY_ID) : null;
  if (!overlay) {
    overlay = buildHelpOverlay(target);
    if (!overlay) return null;
    const host = target.body || target.documentElement;
    if (host && typeof host.appendChild === "function") host.appendChild(overlay);
  }
  const hidden = overlay.classList && overlay.classList.contains("hidden");
  if (hidden) {
    overlay.classList.remove("hidden");
    rememberFocus(target.activeElement);
    const focusable = overlay.querySelector("button");
    if (focusable && typeof focusable.focus === "function") focusable.focus();
  } else {
    overlay.classList.add("hidden");
    restoreFocus(target);
  }
  return overlay;
}

/**
 * Hide the keyboard help overlay and give the focus back.
 *
 * @param {Document} [doc]
 * @returns {Element|null} the overlay
 */
export function hideHelpOverlay(doc) {
  const target = doc || (typeof document !== "undefined" ? document : null);
  if (!target || !target.getElementById) return null;
  const overlay = target.getElementById(HELP_OVERLAY_ID);
  const wasOpen = overlay ? !isHidden(overlay) : false;
  if (overlay && overlay.classList) overlay.classList.add("hidden");
  if (wasOpen) restoreFocus(target);
  return overlay;
}
