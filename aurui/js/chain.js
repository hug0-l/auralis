/* Auralis — chain strip (the horizontal module list in signal order).
 *
 * One chip per step: power toggle, display name, edition tag, drag handle and
 * a remove button.  Chips can be dragged to reorder the signal flow.
 *
 * Contract notes
 *  - Every colour comes from css/tokens.css (var(--token) references only).
 *  - No top-level DOM access: the document is resolved inside functions, so
 *    this module can be imported by a head-less test harness.
 *  - No third-party product or vendor names appear anywhere in this file.
 *
 * Public API
 *   renderChain(container, { chain, onSelect, onBypass, onReorder, onRemove }) -> { update, select }
 */

import { t } from "./i18n.js";

const STYLE_ID = "aur-ui-chain-style";
/** Root element per container, so a re-render replaces instead of stacking. */
const ROOTS = new WeakMap();

const CSS = `
.ch-root { display: flex; align-items: center; gap: var(--sp-2); height: 100%; min-height: 40px; box-sizing: border-box; padding: 0 var(--sp-2); overflow-x: auto; overflow-y: hidden; }
.ch-list { display: flex; align-items: center; gap: var(--sp-2); }
.ch-empty { color: var(--txt-2); font-family: var(--font-ui); font-size: var(--fs-sm); padding: var(--sp-2); white-space: nowrap; }
.ch-chip {
  display: flex; align-items: center; gap: var(--sp-2); box-sizing: border-box; white-space: nowrap;
  background: var(--bg-2); border: 1px solid var(--line); border-radius: var(--radius);
  padding: var(--sp-1) var(--sp-2); color: var(--txt-0); font-family: var(--font-ui); font-size: var(--fs-sm);
}
.ch-chip:hover { background: var(--bg-3); }
.ch-chip.ch-selected { border-color: var(--sel-edge); background: var(--bg-3); }
.ch-chip.ch-bypassed { opacity: 0.55; }
.ch-chip.ch-dragging { opacity: 0.35; }
.ch-chip.ch-drop-before { box-shadow: -2px 0 0 var(--accent); }
.ch-chip.ch-drop-after { box-shadow: 2px 0 0 var(--accent); }
.ch-power {
  flex: 0 0 auto; width: 12px; height: 12px; padding: 0; border-radius: 50%;
  background: transparent; border: 1px solid var(--txt-2); cursor: pointer;
}
.ch-power:hover { border-color: var(--txt-0); }
.ch-power.ch-on { background: var(--accent); border-color: var(--accent); box-shadow: 0 0 6px var(--accent-dim); }
.ch-power:focus-visible { outline: 1px solid var(--sel-edge); outline-offset: 1px; }
.ch-name { background: transparent; border: 0; color: var(--txt-0); font: inherit; padding: 0; cursor: pointer; }
.ch-name:focus-visible { outline: 1px solid var(--sel-edge); outline-offset: 2px; }
.ch-adv {
  flex: 0 0 auto; font-size: var(--fs-xs); font-family: var(--font-mono); letter-spacing: 0.06em;
  color: var(--accent-2); border: 1px solid var(--accent-2); border-radius: 2px; padding: 0 3px; opacity: 0.85;
}
.ch-handle {
  flex: 0 0 auto; width: 10px; height: 14px; color: var(--txt-2); cursor: grab; user-select: none;
  background-image: radial-gradient(currentColor 1px, transparent 1px); background-size: 4px 4px;
}
.ch-handle:active { cursor: grabbing; }
.ch-x { background: transparent; border: 0; color: var(--txt-2); font-family: var(--font-ui); font-size: var(--fs-sm); line-height: 1; padding: 0 2px; cursor: pointer; }
.ch-x:hover { color: var(--danger); }
.ch-x:focus-visible { outline: 1px solid var(--sel-edge); outline-offset: 1px; }
.ch-arrow { color: var(--txt-3); font-family: var(--font-ui); font-size: var(--fs-sm); user-select: none; }
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

/** Normalise one chain step into the fields the strip needs. */
function normalizeStep(raw) {
  const s = raw && typeof raw === "object" ? raw : {};
  const code = String(s.code || s.module_code || "");
  let bypass = false;
  if (typeof s.bypass === "boolean") bypass = s.bypass;
  else if (typeof s.bypassed === "boolean") bypass = s.bypassed;
  else if (typeof s.enabled === "boolean") bypass = !s.enabled;
  return {
    code,
    display_name: String(s.display_name || s.name || code),
    category: String(s.category || ""),
    edition: String(s.edition || "standard"),
    bypass,
  };
}

/** Normalise a chain into an array of steps. */
function normalizeChain(raw) {
  if (!Array.isArray(raw)) return [];
  return raw.map(normalizeStep);
}

/**
 * Resolve the options for renderChain.
 *
 * Two call shapes are supported:
 *   renderChain(container, { chain, onSelect, onBypass, onReorder, onRemove })
 *   renderChain(container, chain, handlers)      <- shell form
 *
 * `handlers` may be a bag of callbacks or a single callable; named callbacks
 * always win over the plain callable.  `onEnable` is deliberately ignored —
 * it carries the inverted (enabled) semantics of the shell, `onBypass` does not.
 */
function resolveChainOptions(second, third) {
  const opts = {};
  let handlerArg = null;
  if (Array.isArray(second)) {
    opts.chain = second;
    handlerArg = third;
  } else if (second && typeof second === "object") {
    Object.assign(opts, second);
    handlerArg = third;
  }
  if (handlerArg) {
    const pairs = [
      ["onSelect", "onSelect"],
      ["onBypass", "onBypass"],
      ["onBypass", "onPower"],
      ["onReorder", "onReorder"],
      ["onReorder", "onMove"],
      ["onRemove", "onRemove"],
    ];
    for (const [target, source] of pairs) {
      if (typeof handlerArg[source] === "function" && typeof opts[target] !== "function") {
        opts[target] = handlerArg[source];
      }
    }
    if (typeof handlerArg === "function" && typeof opts.onSelect !== "function") {
      opts.onSelect = handlerArg;
    }
  }
  return opts;
}

/* -------------------------------------------------------------- public API */

/**
 * Render the chain strip into `container`.
 *
 * @param {Element} container              element that receives the strip
 * @param {object}  options                { chain, onSelect, onBypass, onReorder, onRemove }
 * @param {Array}   options.chain          steps in signal order
 * @param {Function} options.onSelect      called with (index, step) when a chip is selected
 * @param {Function} options.onBypass      called with (index, bypass) — bypass === true mutes the step
 * @param {Function} options.onReorder     called with (fromIndex, toIndex) after a drag
 * @param {Function} options.onRemove      called with (index) when a step is removed
 * @param {object|Function} [handlers]     handler bag used by the positional form
 * @returns {{ update: Function, select: Function }}
 */
export function renderChain(container, options = {}, handlers = null) {
  const doc = ownerDoc(container);
  if (!doc || !container) {
    throw new Error("renderChain: a DOM container element is required");
  }
  ensureStyle(doc);

  const opts = resolveChainOptions(options, handlers);
  let steps = normalizeChain(opts.chain);
  let selectedIndex = -1;
  let selectedCode = null;
  let dragIndex = -1;
  let draggedEl = null;
  /** Chip elements of the current render, in signal order. */
  let chipEls = [];
  /** Name buttons of the current render, in signal order (focus targets). */
  let nameButtons = [];

  const rawOnSelect = typeof opts.onSelect === "function" ? opts.onSelect : null;
  const rawOnBypass = typeof opts.onBypass === "function" ? opts.onBypass : null;
  const rawOnReorder = typeof opts.onReorder === "function" ? opts.onReorder : null;
  const rawOnRemove = typeof opts.onRemove === "function" ? opts.onRemove : null;

  const root = make(doc, "div", "ch-root");
  const listEl = make(doc, "div", "ch-list");
  listEl.setAttribute("role", "list");
  listEl.setAttribute("aria-label", t("chain.label", "Signal chain"));
  root.appendChild(listEl);
  /* Re-rendering the strip replaces the previous instance instead of
     stacking a second copy inside the container. */
  const previous = ROOTS.get(container);
  if (previous && previous.parentNode) previous.parentNode.removeChild(previous);
  ROOTS.set(container, root);
  container.appendChild(root);

  /* ------------------------------------------------------------ selection */

  function applySelection() {
    for (let i = 0; i < chipEls.length; i++) {
      chipEls[i].classList.toggle("ch-selected", i === selectedIndex);
    }
  }

  function selectStep(index) {
    if (index < 0 || index >= steps.length) return;
    selectedIndex = index;
    selectedCode = steps[index].code || null;
    applySelection();
    if (rawOnSelect) {
      try {
        rawOnSelect(index, steps[index]);
      } catch (_err) {
        /* the host owns error reporting */
      }
    }
  }

  /* ------------------------------------------------------- step mutations */

  function togglePower(index) {
    if (index < 0 || index >= steps.length) return;
    const bypass = !steps[index].bypass;
    steps[index].bypass = bypass;
    if (rawOnBypass) {
      try {
        rawOnBypass(index, bypass);
      } catch (_err) {
        /* the host owns error reporting */
      }
    }
    render();
  }

  function removeStep(index) {
    if (index < 0 || index >= steps.length) return;
    if (rawOnRemove) {
      try {
        rawOnRemove(index);
      } catch (_err) {
        /* the host owns error reporting */
      }
    }
    steps.splice(index, 1);
    if (selectedIndex === index) selectedIndex = -1;
    else if (selectedIndex > index) selectedIndex -= 1;
    render();
  }

  /** Move a step locally, notify the host and re-render. */
  function moveStep(from, to) {
    if (from === to) return;
    if (from < 0 || from >= steps.length) return;
    const target = Math.max(0, Math.min(to, steps.length - 1));
    if (target === from) return;
    const moved = steps.splice(from, 1)[0];
    steps.splice(target, 0, moved);
    if (rawOnReorder) {
      try {
        rawOnReorder(from, target);
      } catch (_err) {
        /* the host owns error reporting */
      }
    }
    if (selectedIndex === from) selectedIndex = target;
    else if (target > from && selectedIndex > from && selectedIndex <= target) selectedIndex -= 1;
    else if (target < from && selectedIndex >= target && selectedIndex < from) selectedIndex += 1;
    selectedCode = selectedIndex >= 0 && steps[selectedIndex] ? steps[selectedIndex].code : selectedCode;
    render();
  }

  /* ------------------------------------------------------------ drag & drop */

  function clearDropHints() {
    for (const chip of chipEls) chip.classList.remove("ch-drop-before", "ch-drop-after");
  }

  /** Is the pointer over the trailing half of the chip? */
  function dropAfter(chip, event) {
    if (!chip || typeof chip.getBoundingClientRect !== "function") return false;
    const rect = chip.getBoundingClientRect();
    if (!rect) return false;
    const x = event && typeof event.clientX === "number" ? event.clientX : 0;
    return x > rect.left + rect.width / 2;
  }

  function transferIndex(event) {
    const dt = event && event.dataTransfer;
    if (!dt || typeof dt.getData !== "function") return -1;
    const raw = dt.getData("text/plain");
    const n = parseInt(raw, 10);
    return Number.isFinite(n) ? n : -1;
  }

  function endDrag() {
    dragIndex = -1;
    clearDropHints();
    if (draggedEl) {
      draggedEl.classList.remove("ch-dragging");
      draggedEl.draggable = false;
      draggedEl = null;
    }
    for (const chip of chipEls) chip.draggable = false;
  }

  /* ---------------------------------------------------------------- render */

  function buildChip(step, index) {
    /* The `chip` class lets the shell's own stylesheet theme the chip
       (and lets the shell mark the selected step with `.sel`). */
    const chip = make(doc, "div", "ch-chip chip");
    chip.setAttribute("role", "listitem");
    chip.setAttribute("data-code", step.code);
    chip.setAttribute("data-index", String(index));
    chip.draggable = false;
    if (step.bypass) chip.classList.add("ch-bypassed");

    const power = make(doc, "button", "ch-power");
    power.setAttribute("type", "button");
    power.setAttribute("data-chip-index", String(index));
    power.setAttribute("aria-pressed", step.bypass ? "false" : "true");
    power.setAttribute("title", step.bypass ? t("chain.enable", "Enable module") : t("chain.bypass", "Bypass module"));
    if (!step.bypass) power.classList.add("ch-on");
    power.addEventListener("click", (event) => {
      if (event && typeof event.stopPropagation === "function") event.stopPropagation();
      togglePower(index);
    });

    const name = make(doc, "button", "ch-name", step.display_name);
    name.setAttribute("type", "button");
    name.setAttribute("data-chip-index", String(index));
    name.setAttribute("title", step.display_name);
    name.addEventListener("click", (event) => {
      if (event && typeof event.stopPropagation === "function") event.stopPropagation();
      selectStep(index);
    });

    chip.appendChild(power);
    chip.appendChild(name);

    if (step.edition === "advanced") {
      const adv = make(doc, "span", "ch-adv", t("edition.advanced", "ADV"));
      adv.setAttribute("title", t("chain.advanced", "Advanced edition"));
      chip.appendChild(adv);
    }

    const handle = make(doc, "span", "ch-handle");
    handle.setAttribute("aria-hidden", "true");
    handle.setAttribute("title", t("chain.reorder", "Drag to reorder"));
    handle.addEventListener("mousedown", () => {
      chip.draggable = true;
    });

    const remove = make(doc, "button", "ch-x", "×");
    remove.setAttribute("type", "button");
    remove.setAttribute("data-chip-index", String(index));
    remove.setAttribute("title", t("chain.remove", "Remove from chain"));
    remove.addEventListener("click", (event) => {
      if (event && typeof event.stopPropagation === "function") event.stopPropagation();
      removeStep(index);
    });

    chip.appendChild(handle);
    chip.appendChild(remove);

    chip.addEventListener("click", (event) => {
      const target = event && event.target;
      if (target && typeof target.closest === "function" && target.closest(".ch-power, .ch-x")) return;
      selectStep(index);
    });

    chip.addEventListener("dragstart", (event) => {
      dragIndex = index;
      draggedEl = chip;
      chip.classList.add("ch-dragging");
      const dt = event && event.dataTransfer;
      if (dt) {
        try {
          dt.effectAllowed = "move";
          dt.setData("text/plain", String(index));
        } catch (_err) {
          /* some browsers restrict dataTransfer outside a real drag */
        }
      }
    });

    chip.addEventListener("dragend", () => endDrag());

    chip.addEventListener("dragover", (event) => {
      if (dragIndex < 0) return;
      if (event && typeof event.preventDefault === "function") event.preventDefault();
      const dt = event && event.dataTransfer;
      if (dt && dt.dropEffect !== undefined) dt.dropEffect = "move";
      clearDropHints();
      const after = dropAfter(chip, event);
      chip.classList.add(after ? "ch-drop-after" : "ch-drop-before");
    });

    chip.addEventListener("dragleave", () => {
      chip.classList.remove("ch-drop-before", "ch-drop-after");
    });

    chip.addEventListener("drop", (event) => {
      if (event && typeof event.preventDefault === "function") event.preventDefault();
      if (event && typeof event.stopPropagation === "function") event.stopPropagation();
      const from = dragIndex >= 0 ? dragIndex : transferIndex(event);
      const after = dropAfter(chip, event);
      endDrag();
      if (from < 0 || from >= steps.length) {
        render();
        return;
      }
      const insertion = after ? index + 1 : index;
      const to = insertion > from ? insertion - 1 : insertion;
      if (to === from) {
        render();
        return;
      }
      moveStep(from, to);
    });

    return { chip, name };
  }

  function render() {
    clear(listEl);
    chipEls = [];
    nameButtons = [];

    if (steps.length === 0) {
      const empty = make(doc, "span", "ch-empty empty", t("chain.empty", "No modules — add one from the browser"));
      empty.setAttribute("role", "note");
      listEl.appendChild(empty);
      return;
    }

    steps.forEach((step, index) => {
      if (index > 0) {
        const arrow = make(doc, "span", "ch-arrow", "→");
        arrow.setAttribute("aria-hidden", "true");
        listEl.appendChild(arrow);
      }
      const built = buildChip(step, index);
      chipEls.push(built.chip);
      nameButtons.push(built.name);
      listEl.appendChild(built.chip);
    });

    applySelection();
  }

  /* drop on the strip itself appends at the end */
  listEl.addEventListener("dragover", (event) => {
    if (dragIndex < 0) return;
    if (event && typeof event.preventDefault === "function") event.preventDefault();
  });
  listEl.addEventListener("drop", (event) => {
    if (dragIndex < 0) return;
    if (event && typeof event.preventDefault === "function") event.preventDefault();
    const from = dragIndex;
    endDrag();
    if (from < 0 || from >= steps.length) {
      render();
      return;
    }
    moveStep(from, steps.length - 1);
  });

  /* ------------------------------------------------------------- keyboard */

  /** Index of the chip that currently owns the keyboard focus. */
  function focusedIndex() {
    const active = doc.activeElement;
    if (!active || typeof active.getAttribute !== "function") return -1;
    const raw = active.getAttribute("data-chip-index") || active.getAttribute("data-index");
    const n = parseInt(raw, 10);
    return Number.isFinite(n) ? n : -1;
  }

  root.addEventListener("keydown", (event) => {
    if (!event) return;
    const key = event.key;
    const at = focusedIndex();
    if (at < 0) return;

    if (event.altKey && (key === "ArrowLeft" || key === "ArrowRight")) {
      if (event.preventDefault) event.preventDefault();
      const to = key === "ArrowLeft" ? at - 1 : at + 1;
      if (to < 0 || to >= steps.length) return;
      moveStep(at, to);
      const focusTarget = nameButtons[selectedIndex === at ? selectedIndex : to];
      if (focusTarget && typeof focusTarget.focus === "function") focusTarget.focus();
      return;
    }

    if (key === "ArrowLeft" || key === "ArrowRight") {
      if (event.preventDefault) event.preventDefault();
      const to = key === "ArrowLeft" ? at - 1 : at + 1;
      if (to < 0 || to >= nameButtons.length) return;
      if (nameButtons[to] && typeof nameButtons[to].focus === "function") nameButtons[to].focus();
    }
  });

  render();

  return {
    /** Replace the chain (the current selection is re-anchored by module code). */
    update(nextChain) {
      steps = normalizeChain(nextChain);
      if (selectedCode) {
        const found = steps.findIndex((s) => s.code && s.code === selectedCode);
        if (found >= 0) selectedIndex = found;
        else selectedIndex = steps.length ? Math.min(selectedIndex, steps.length - 1) : -1;
      } else if (selectedIndex >= steps.length) {
        selectedIndex = steps.length ? steps.length - 1 : -1;
      }
      render();
    },
    /** Highlight a step without notifying the host. */
    select(index) {
      const at = typeof index === "number" ? index : parseInt(index, 10);
      if (!Number.isFinite(at) || at < 0 || at >= steps.length) return;
      selectedIndex = at;
      selectedCode = steps[at].code || null;
      applySelection();
    },
  };
}
