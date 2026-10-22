/* Auralis — the path of the live preview.
 *
 * The product has two render paths and the preview is not the file.  The
 * server runs the preview in a *profile* (aurserver/audio.py: PROFILE_FAST
 * and PROFILE_FULL) and reports what the profile moved in the `profile_report`
 * of the `ready` and of the `profile` message.  The fast profile moves every
 * cost parameter of the chain onto its cheap setting; the full profile keeps
 * the values the user set, so the preview is the file configuration.
 *
 * The rule of this module: **nothing is computed here**.  Which path plays,
 * which parameters were moved, the value on each side and the latency are the
 * numbers the server sent.  The module chooses the words and puts them where
 * the user can reach them.
 *
 * The path is shown in the status bar, next to the readouts: a user who moves
 * a knob has to see which path is playing, and the disclosure that follows
 * the chip says what the path changed.  The report is chain wide, so it does
 * not belong to the panel of a single module — and the claim and its
 * evidence stay in the same place.
 *
 * Contract notes
 *  - Every colour comes from css/tokens.css (var(--token) references only).
 *  - No top-level DOM access: the document is resolved inside the functions,
 *    so this module can be imported by a head-less test harness.
 *  - No third-party product or vendor names appear anywhere in this file.
 *
 * Public API
 *   createPathReport(options) -> {
 *     update(payload),        // a message of the server
 *     setRequested(profile),  // the profile the next preview asks for
 *     setRender(),            // the file render is playing
 *     stop(),                 // nothing plays any more
 *     open(), close(), toggle(), refresh()
 *   }
 */

import { el } from "./api.js";
import { t } from "./i18n.js";

/**
 * The label of every profile the product knows.
 *
 * The key is looked up in the catalogue, so the label follows the locale; the
 * text is the English fallback and the word the server uses.
 */
const PROFILE_LABEL = {
  fast: { key: "status.path.fast", text: "preview: fast path" },
  full: { key: "status.path.full", text: "preview: full path" },
};

/** The profile the preview asks for when the user does not choose one. */
export const DEFAULT_PROFILE = "fast";

/** The profiles the user may choose between, in the order of the panel. */
export const PROFILES = ["fast", "full"];

/**
 * The label of a profile, in the language of the moment.
 *
 * A profile the catalogue does not know keeps the words of the server: the
 * server is the source of the path, so its name is shown as it is.
 *
 * @param {string} profile  the profile the server reported
 * @param {string} [fallback] the label the server sent
 * @returns {string} the label
 */
export function profileLabel(profile, fallback) {
  const name = String(profile == null ? "" : profile).trim().toLowerCase();
  const entry = PROFILE_LABEL[name];
  if (entry) return t(entry.key, entry.text);
  if (fallback) return String(fallback);
  return t("status.path.other", "preview: {name}", { name: name || "?" });
}

/** The name of a module, when the shell does not know it. */
function defaultName(code) {
  return String(code == null ? "" : code);
}

/**
 * The value of a parameter, as the report gives it.
 *
 * A boolean is shown as a word, a number as it is, everything else as text.
 *
 * @param {*} value the value of the server
 * @returns {string} the text
 */
export function formatValue(value) {
  if (value === true) return t("control.on", "On");
  if (value === false) return t("control.off", "Off");
  if (value === null || value === undefined) return "—";
  return String(value);
}

/**
 * Create the report of the path.
 *
 * @param {object} options
 * @param {Element} options.chip      the button of the status bar
 * @param {Element} options.panel     the container of the disclosure
 * @param {Function} [options.nameOf] (code) => the name of a module
 * @param {Function} [options.onProfile] (profile) => the user asks for a path
 * @returns {object} the report
 */
export function createPathReport(options) {
  const opts = options && typeof options === "object" ? options : {};
  const chip = opts.chip || null;
  const panel = opts.panel || null;
  const nameOf = typeof opts.nameOf === "function" ? opts.nameOf : defaultName;
  const onProfile = typeof opts.onProfile === "function" ? opts.onProfile : () => {};

  /** What the interface knows about the path. */
  const view = {
    requested: DEFAULT_PROFILE, // the profile the next preview asks for
    reported: "",               // the profile the server reported
    playing: false,             // a stream is running
    render: false,              // the file render is playing
    mode: "",                   // the processing mode of the chain
    block: 0,                   // the block the preview renders in
    latency: null,              // the latency the server reported, in samples
    latencyMs: null,            // the latency the server reported, in ms
    report: null,               // the report of the profile
  };

  /** True when the disclosure is open. */
  function isOpen() {
    return !!panel && !panel.classList.contains("hidden");
  }

  /** The text of the chip: the path that plays, or the one that is asked for. */
  function chipText() {
    if (view.render) return t("status.path.render", "render: file path");
    if (view.playing && view.reported) {
      return profileLabel(view.reported, view.reportedLabel || "");
    }
    return profileLabel(view.requested, "");
  }

  /** Put the text of the chip and its state in step with the view. */
  function paint() {
    if (!chip) return;
    const text = chip.querySelector(".path-label") || chip;
    text.textContent = chipText();
    chip.classList.toggle("on", view.playing);
    chip.classList.toggle("off", !view.playing);
    chip.setAttribute("aria-expanded", isOpen() ? "true" : "false");
    chip.setAttribute(
      "title",
      view.playing
        ? t("status.path.title", "What the preview plays and what the path changed")
        : t("status.path.titleIdle", "The path the next preview will use")
    );
  }

  /** One fact of the path: a name and the value the server sent. */
  function fact(name, value) {
    return el(
      "span",
      { class: "path-fact" },
      el("span", { class: "path-fact-name" }, name),
      el("span", { class: "path-fact-value" }, value)
    );
  }

  /** The rows of the report: the parameters the profile moved. */
  function reportRows() {
    const section = el("div", { class: "path-report" });
    if (!view.report) {
      /* No message of the server yet: the interface knows nothing about the
         path and says so.  It must not claim that the path moves nothing —
         that is a claim of the server, and the server has not made it. */
      section.append(
        el(
          "p",
          { class: "path-note" },
          t("status.path.unknown", "The server has not reported a path yet.")
        )
      );
      return section;
    }
    const report = view.report;
    const modules = Array.isArray(report.modules) ? report.modules : [];
    const refused = Array.isArray(report.refused) ? report.refused : [];
    if (!modules.length) {
      section.append(
        el(
          "p",
          { class: "path-note" },
          t(
            "status.path.unchanged",
            "The path moved no parameter — the preview uses the values you set."
          )
        )
      );
    } else {
      const changed = Number(report.changed) || 0;
      section.append(
        el(
          "p",
          { class: "path-note" },
          t("status.path.changed", "The path moved {n} parameter(s):", { n: String(changed) })
        )
      );
      section.append(
        el(
          "div",
          { class: "path-columns" },
          el("span", { class: "path-column" }, t("status.path.file", "file")),
          el("span", { class: "path-column" }, t("status.path.preview", "preview"))
        )
      );
      for (const entry of modules) {
        const block = el("div", { class: "path-module" });
        block.append(el("div", { class: "path-module-name" }, nameOf(entry.code)));
        for (const parameter of entry.parameters || []) {
          block.append(
            el(
              "div",
              { class: "path-row" },
              el("span", { class: "path-param" }, String(parameter.name)),
              el("span", { class: "path-value" }, formatValue(parameter.file)),
              el("span", { class: "path-arrow" }, "→"),
              el("span", { class: "path-value path-value-now" }, formatValue(parameter.preview))
            )
          );
        }
        section.append(block);
      }
    }
    for (const entry of refused) {
      section.append(
        el(
          "p",
          { class: "path-note path-note-warn" },
          t(
            "status.path.refused",
            "The cheap configuration does not run in {name} — the preview keeps your value.",
            { name: nameOf(entry.code) }
          )
        )
      );
    }
    return section;
  }

  /** The buttons that ask for a path. */
  function actions() {
    const group = el("div", {
      class: "path-actions",
      role: "group",
      "aria-label": t("status.path.choose", "The path of the preview"),
    });
    for (const profile of PROFILES) {
      const on = view.requested === profile;
      const label =
        profile === "full"
          ? t("status.path.useFull", "Full path")
          : t("status.path.useFast", "Fast path");
      group.append(
        el("button", {
          class: on ? "tbtn tbtn-sm on" : "tbtn tbtn-sm",
          type: "button",
          "aria-pressed": on ? "true" : "false",
          text: label,
          onclick: () => {
            onProfile(profile);
          },
        })
      );
    }
    group.append(
      el(
        "p",
        { class: "path-note" },
        t("status.path.restart", "The preview starts again on the path you choose.")
      )
    );
    return group;
  }

  /** Fill the disclosure with what the server reported. */
  function fill() {
    if (!panel) return;
    panel.replaceChildren();
    panel.append(
      el(
        "div",
        { class: "path-head" },
        el("span", { class: "path-title" }, chipText()),
        el(
          "span",
          { class: "path-state" },
          view.playing
            ? t("status.path.playing", "playing")
            : t("status.path.idle", "no preview is playing")
        )
      )
    );
    const facts = el("div", { class: "path-facts" });
    if (view.mode) facts.append(fact(t("status.path.mode", "mode"), view.mode));
    if (view.block) facts.append(fact(t("status.path.block", "block"), String(view.block)));
    if (view.latency !== null) {
      facts.append(
        fact(
          t("status.path.latency", "latency"),
          t("status.path.latencyValue", "{n} smp ({ms} ms)", {
            n: String(view.latency),
            ms: Number(view.latencyMs || 0).toFixed(1),
          })
        )
      );
    }
    if (facts.childNodes.length) panel.append(facts);
    panel.append(reportRows());
    panel.append(actions());
  }

  /** Put the panel where the chip is. */
  function place() {
    if (!panel || !chip || typeof chip.getBoundingClientRect !== "function") return;
    if (!panel.style) return;
    const rect = chip.getBoundingClientRect();
    const height =
      typeof globalThis !== "undefined" && globalThis.innerHeight
        ? globalThis.innerHeight
        : rect.bottom;
    panel.style.left = `${Math.round(rect.left)}px`;
    panel.style.bottom = `${Math.round(height - rect.top + 4)}px`;
  }

  /** Open the disclosure. */
  function open() {
    if (!panel) return;
    fill();
    panel.classList.remove("hidden");
    place();
    paint();
    const first = panel.querySelector("button");
    if (first && typeof first.focus === "function") {
      try {
        first.focus();
      } catch (err) {
        /* the button cannot take the focus — the panel is still open */
      }
    }
  }

  /** Close the disclosure. */
  function close() {
    if (!panel) return;
    panel.classList.add("hidden");
    paint();
  }

  /** Open the disclosure, or close it. */
  function toggle() {
    if (isOpen()) {
      close();
      return;
    }
    open();
  }

  /**
   * Take the path of a message of the server.
   *
   * @param {object} payload the message
   * @returns {boolean} true when the message carried a path
   */
  function update(payload) {
    if (!payload || typeof payload !== "object") return false;
    const profile = String(payload.profile == null ? "" : payload.profile).trim().toLowerCase();
    let seen = false;
    if (profile) {
      view.reported = profile;
      view.reportedLabel = String(payload.profile_label || "");
      seen = true;
    }
    if (payload.profile_report && typeof payload.profile_report === "object") {
      view.report = payload.profile_report;
      seen = true;
    }
    if (payload.processing_mode) {
      view.mode = String(payload.processing_mode);
      seen = true;
    }
    if (payload.block_size !== undefined && payload.block_size !== null) {
      const block = Number(payload.block_size);
      if (Number.isFinite(block)) view.block = block;
    }
    if (payload.latency_samples !== undefined && payload.latency_samples !== null) {
      const latency = Number(payload.latency_samples);
      if (Number.isFinite(latency)) {
        view.latency = latency;
        seen = true;
      }
    }
    if (payload.latency_ms !== undefined && payload.latency_ms !== null) {
      const latencyMs = Number(payload.latency_ms);
      if (Number.isFinite(latencyMs)) view.latencyMs = latencyMs;
    }
    if (seen) {
      view.playing = true;
      view.render = false;
      paint();
      if (isOpen()) fill();
    }
    return seen;
  }

  /**
   * The user asks for another path.
   *
   * @param {string} profile the profile the next preview asks for
   */
  function setRequested(profile) {
    const name = String(profile == null ? "" : profile).trim().toLowerCase();
    if (!name) return;
    view.requested = name;
    paint();
    if (isOpen()) fill();
  }

  /** The file render is playing: the audio is the file configuration. */
  function setRender() {
    view.render = true;
    view.playing = true;
    paint();
    if (isOpen()) fill();
  }

  /** Nothing plays any more. */
  function stop() {
    view.playing = false;
    view.render = false;
    paint();
  }

  /** The locale changed: the texts of the chip and of the panel follow. */
  function refresh() {
    paint();
    if (isOpen()) fill();
  }

  if (chip) {
    chip.addEventListener("click", (event) => {
      if (event && typeof event.stopPropagation === "function") event.stopPropagation();
      toggle();
    });
  }
  if (panel) {
    panel.addEventListener("click", (event) => {
      if (event && typeof event.stopPropagation === "function") event.stopPropagation();
    });
  }
  if (typeof document !== "undefined" && typeof document.addEventListener === "function") {
    document.addEventListener("click", () => {
      if (isOpen()) close();
    });
    document.addEventListener("keydown", (event) => {
      if (!isOpen()) return;
      if (String(event.key || "") !== "Escape") return;
      close();
      if (chip && typeof chip.focus === "function") {
        try {
          chip.focus();
        } catch (err) {
          /* the chip cannot take the focus */
        }
      }
    });
  }

  paint();
  return {
    update,
    setRequested,
    setRender,
    stop,
    open,
    close,
    toggle,
    refresh,
    isOpen,
    view,
  };
}
