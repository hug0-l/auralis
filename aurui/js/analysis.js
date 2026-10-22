/* Auralis — Analysis tab (the third tab of the right hand inspector).
 *
 * A vanilla ES module: no dependencies, no build step.
 *
 * The module performs NO DOM access at import time — every node is created
 * inside renderAnalysis() — so it can be imported by a head-less harness
 * that only provides a `document` stub.
 *
 * Colours, fonts and sizes come from /css/tokens.css through var(--token);
 * the stylesheet injected below only ever references those custom
 * properties.  The two canvases read the same tokens at paint time.
 *
 * Public API
 *   renderAnalysis(container, data, opts) -> { update, destroy, element, axis, stats }
 *   emptyAnalysis()                        -> an empty measurement payload
 *   frequencyAxis(payload)                 -> the frequency axis of a payload
 *
 * The payload is the one produced by `aurcore.analysis` (the measurement
 * service of the DSP core):
 *
 *   {
 *     sample_rate, channels, frames, duration,
 *     selection: { start, end, requested_start, requested_end, samples,
 *                  duration, bounded },
 *     spectrum:      { frequencies: [...], magnitudes_db: [...], peaks_db: [...],
 *                      min_db, max_db, fft_size, frames, peak_hz, peak_db },
 *     waveform_stats:{ true_peak, true_peak_db, sample_peak, sample_peak_db,
 *                      max, min, mean, rms, rms_db, total_rms, total_rms_db,
 *                      dc_offset, dc_offset_db, possibly_clipped },
 *     loudness:      { integrated_lufs, short_term_lufs, momentary_lufs,
 *                      loudness_range_lu, true_peak_dbtp },
 *     correlation:   number,
 *     histogram:     { bin_edges, counts, bin_width, range, clip_threshold,
 *                      clip_bin_start, clip_bin_end, out_of_range },
 *   }
 *
 * The banded payload of the composite view names its axis `freq_min_hz`,
 * `freq_max_hz` and `bin_hz`; the panel reads that axis too.  Whatever the
 * payload publishes, the panel draws *that* axis — never one it assumes —
 * so a corrected server axis is followed without a change here.
 *
 * Every field is optional: a missing section renders as an empty block
 * instead of breaking the tab.
 */

import { el } from "./api.js";
import { t } from "./i18n.js";

/* ------------------------------------------------------------------ utils */

const STYLE_ID = "aur-analysis-css";
/** Root element per container, so a re-render replaces instead of stacking. */
const ROOTS = new WeakMap();

const CSS = `
.an-root { display: flex; flex-direction: column; gap: var(--sp-4); min-width: 0; }
.an-section { display: flex; flex-direction: column; gap: var(--sp-2); min-width: 0; }
.an-title {
  font: 600 var(--fs-xs)/1 var(--font-ui); text-transform: uppercase; letter-spacing: .08em;
  color: var(--txt-2); padding-bottom: 3px; border-bottom: 1px solid var(--line-soft);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.an-canvas { display: block; width: 100%; border: 1px solid var(--line); border-radius: var(--radius); background: var(--bg-2); }
.an-spectrum { height: 152px; }
.an-histogram { height: 104px; }
.an-axis { display: flex; justify-content: space-between; gap: var(--sp-2); font: 400 var(--fs-xs)/1 var(--font-mono); color: var(--txt-3); }
.an-axis > span { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.an-axis > span:nth-child(2) { flex: 1 1 auto; text-align: center; }
.an-meter { display: grid; grid-template-columns: 1fr auto; gap: 2px var(--sp-2); align-items: baseline; }
.an-label { font: 500 var(--fs-xs)/1.35 var(--font-ui); color: var(--txt-2); }
.an-value { font: 500 var(--fs-xs)/1.35 var(--font-mono); color: var(--txt-0); font-variant-numeric: tabular-nums; }
.an-bar { grid-column: 1 / -1; height: 4px; border-radius: 2px; background: var(--bg-3); overflow: hidden; }
.an-bar-fill { display: block; height: 100%; width: 0; background: var(--accent); }
.an-bar-fill.alt { background: var(--accent-2); }
.an-bar-fill.warn { background: var(--danger); }
.an-grid { display: grid; grid-template-columns: 1fr auto; gap: 1px var(--sp-3); }
.an-grid .an-value { text-align: right; }
.an-corr { position: relative; height: 16px; }
.an-corr-track { position: absolute; left: 0; right: 0; top: 6px; height: 4px; border-radius: 2px; background: var(--bg-3); }
.an-corr-fill { position: absolute; top: 6px; height: 4px; border-radius: 2px; background: var(--accent); }
.an-corr-fill.neg { background: var(--accent-2); }
.an-corr-fill.flat { background: var(--txt-2); }
.an-corr-centre { position: absolute; left: 50%; top: 1px; width: 1px; height: 14px; background: var(--txt-2); }
.an-note { font: 400 var(--fs-xs)/1.4 var(--font-ui); color: var(--txt-3); }
.an-empty { font: 400 var(--fs-sm)/1.5 var(--font-ui); color: var(--txt-2); margin: 0; }
.an-warn { color: var(--danger); }
.an-warnrow .an-value { color: var(--danger); }
`;

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
  style.textContent = CSS;
  head.appendChild(style);
}

const isNum = (v) => typeof v === "number" && isFinite(v);

/** A finite number, or the fallback. */
function num(v, fallback) {
  const n = Number(v);
  return isFinite(n) ? n : fallback;
}

const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);
const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);

/* ------------------------------------------------------------ colour tokens */

/**
 * Parse a CSS colour string into `[r, g, b, a]`.
 * Understands #rgb, #rgba, #rrggbb, #rrggbbaa, rgb() and rgba().
 */
function parseColor(input) {
  if (input === null || input === undefined) return null;
  const s = String(input).trim().toLowerCase();
  if (!s) return null;
  if (s === "transparent") return [0, 0, 0, 0];
  if (s[0] === "#") {
    const hex = s.slice(1);
    if (hex.length === 3 || hex.length === 4) {
      if (!/^[0-9a-f]+$/.test(hex)) return null;
      return [
        parseInt(hex[0] + hex[0], 16),
        parseInt(hex[1] + hex[1], 16),
        parseInt(hex[2] + hex[2], 16),
        hex.length === 4 ? parseInt(hex[3] + hex[3], 16) / 255 : 1,
      ];
    }
    if (hex.length === 6 || hex.length === 8) {
      if (!/^[0-9a-f]+$/.test(hex)) return null;
      return [
        parseInt(hex.slice(0, 2), 16),
        parseInt(hex.slice(2, 4), 16),
        parseInt(hex.slice(4, 6), 16),
        hex.length === 8 ? parseInt(hex.slice(6, 8), 16) / 255 : 1,
      ];
    }
    return null;
  }
  const m = s.match(/^rgba?\(([^)]+)\)$/);
  if (m) {
    const parts = m[1].split(/[,\s/]+/).filter(Boolean).map(Number);
    if (parts.length >= 3 && parts.every((n) => !isNaN(n))) {
      return [parts[0], parts[1], parts[2], parts.length > 3 ? parts[3] : 1];
    }
  }
  return null;
}

/** Format a parsed colour as a CSS rgba() string. */
function rgba(color, alpha) {
  if (!color) return null;
  const a = alpha === undefined || alpha === null ? color[3] : alpha;
  return `rgba(${color[0] | 0}, ${color[1] | 0}, ${color[2] | 0}, ${a})`;
}

/** Read the design tokens from the computed style of `node`. */
function readPalette(node) {
  const doc = typeof document !== "undefined" ? document : null;
  const root = node || (doc ? doc.documentElement : null);
  const palette = {
    bg0: null, bg1: null, bg2: null, bg3: null, line: null, lineSoft: null,
    txt0: null, txt1: null, txt2: null, txt3: null,
    accent: null, accent2: null, accent3: null, danger: null,
    fontMono: "monospace", fontUi: "system-ui, sans-serif", fontXs: 10,
  };
  if (!root || typeof getComputedStyle !== "function") return palette;
  let cs = null;
  try {
    cs = getComputedStyle(root);
  } catch (err) {
    return palette;
  }
  const g = (name) => {
    const v = cs.getPropertyValue(name);
    return v ? String(v).trim() : "";
  };
  palette.bg0 = parseColor(g("--bg-0"));
  palette.bg1 = parseColor(g("--bg-1"));
  palette.bg2 = parseColor(g("--bg-2"));
  palette.bg3 = parseColor(g("--bg-3"));
  palette.line = parseColor(g("--line"));
  palette.lineSoft = parseColor(g("--line-soft"));
  palette.txt0 = parseColor(g("--txt-0"));
  palette.txt1 = parseColor(g("--txt-1"));
  palette.txt2 = parseColor(g("--txt-2"));
  palette.txt3 = parseColor(g("--txt-3"));
  palette.accent = parseColor(g("--accent"));
  palette.accent2 = parseColor(g("--accent-2"));
  palette.accent3 = parseColor(g("--accent-3"));
  palette.danger = parseColor(g("--danger"));
  palette.fontMono = g("--font-mono") || palette.fontMono;
  palette.fontUi = g("--font-ui") || palette.fontUi;
  const fs = parseFloat(g("--fs-xs"));
  palette.fontXs = isFinite(fs) && fs > 0 ? fs : 10;
  return palette;
}

/* --------------------------------------------------------------- formatting */

/** Format a level in dB; `null` renders as a dash. */
function fmtDb(value, digits = 1, unit = "") {
  if (value === null || value === undefined) return "—";
  const n = Number(value);
  if (!isFinite(n)) return "—";
  return `${n > 0 ? "+" : ""}${n.toFixed(digits)} dB${unit ? ` ${unit}` : ""}`;
}

/** Format a plain number. */
function fmtNum(value, digits = 3) {
  if (value === null || value === undefined) return "—";
  const n = Number(value);
  if (!isFinite(n)) return "—";
  return n.toFixed(digits);
}

/** Format an integer count. */
function fmtInt(value) {
  if (value === null || value === undefined) return "—";
  const n = Number(value);
  if (!isFinite(n)) return "—";
  return String(Math.round(n));
}

/** Format a time in seconds. */
function fmtSeconds(value, digits = 2) {
  if (value === null || value === undefined) return "—";
  const n = Number(value);
  if (!isFinite(n)) return "—";
  return `${n.toFixed(digits)} s`;
}

/** Format a frequency for a readout: exact below a kilohertz, scaled above. */
function fmtHz(value) {
  if (!isNum(value)) return "—";
  const v = Math.abs(value);
  if (v >= 10000) return `${(value / 1000).toFixed(1)} kHz`;
  if (v >= 100) return `${value.toFixed(1)} Hz`;
  if (v >= 1) return `${value.toFixed(2)} Hz`;
  return `${value.toFixed(3)} Hz`;
}

/** Format a frequency for the axis: short enough to sit under the canvas. */
function fmtAxisHz(value) {
  if (!isNum(value)) return "—";
  const v = Math.abs(value);
  if (v >= 1000) return `${(value / 1000).toFixed(v >= 10000 ? 0 : 1)} kHz`;
  if (v >= 10) return `${Math.round(value)} Hz`;
  if (v > 0) return `${value.toFixed(1)} Hz`;
  return "0 Hz";
}

/* ------------------------------------------------------------------ canvas */

/** The device pixel ratio, clamped to [1, 3]. */
function dpr() {
  const raw = (typeof globalThis !== "undefined" && globalThis.devicePixelRatio) || 1;
  const v = Number(raw);
  if (!isFinite(v) || v <= 0) return 1;
  return Math.max(1, Math.min(3, v));
}

/**
 * The CSS box of a canvas, in CSS pixels.
 *
 * The box is read without touching the drawing buffer, so it can be used
 * to decide whether anything has to be drawn at all.
 */
function canvasBox(canvas, fallbackHeight) {
  let w = 0;
  let h = 0;
  if (canvas && typeof canvas.getBoundingClientRect === "function") {
    const rect = canvas.getBoundingClientRect();
    w = Math.floor(rect.width);
    h = Math.floor(rect.height);
  }
  if (!(w > 0)) w = Math.floor((canvas && canvas.clientWidth) || 0) || 280;
  if (!(h > 0)) h = Math.floor((canvas && canvas.clientHeight) || 0) || fallbackHeight || 120;
  return { w, h, ratio: dpr() };
}

/**
 * Size the drawing buffer of a canvas to its CSS box.
 * Returns `{ w, h, ratio }` in CSS pixels.
 */
function prepareCanvas(canvas, fallbackHeight) {
  const box = canvasBox(canvas, fallbackHeight);
  const pw = Math.max(1, Math.round(box.w * box.ratio));
  const ph = Math.max(1, Math.round(box.h * box.ratio));
  if (canvas.width !== pw) canvas.width = pw;
  if (canvas.height !== ph) canvas.height = ph;
  return box;
}

/* --------------------------------------------------------------- the axis */

/**
 * The fraction of the width the linear origin of the frequency axis takes.
 *
 * The origin covers the frequencies between 0 Hz and the first positive
 * frequency of the payload; the rest of the axis is logarithmic.  The
 * origin is a property of the *scale*, not of the axis: the axis itself
 * always starts and ends where the payload says.
 */
const ORIGIN_FRACTION = 0.08;

/** The round frequencies the axis is graduated with. */
const TICK_CANDIDATES = [10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 25000, 50000, 100000];

/**
 * The frequency axis of a measurement payload.
 *
 * The panel never assumes an axis: the range is the one the payload
 * publishes.  Three shapes are understood.
 *
 * 1. the measurement payload carries the frequency of every bin in
 *    `spectrum.frequencies`; the axis reaches from its first entry to its
 *    last one;
 * 2. a payload may name the axis explicitly with `freq_min_hz` and
 *    `freq_max_hz`;
 * 3. a banded payload names the width of a band (`bin_hz`) and the number
 *    of bands (`freq_bins`); the axis then reaches from `freq_min_hz` to
 *    `bin_hz * freq_bins`.
 *
 * When the payload names none of them the axis falls back to the Nyquist
 * frequency of the file; `source` then says so.
 *
 * @param {object} data  a measurement payload
 * @returns {{min: number, max: number, origin: number, source: string,
 *            binHz: number|null, rows: number|null}}
 */
export function frequencyAxis(data) {
  const d = data && typeof data === "object" ? data : {};
  const spectrum = d.spectrum && typeof d.spectrum === "object" ? d.spectrum : {};
  const freqs = Array.isArray(spectrum.frequencies) ? spectrum.frequencies.filter(isNum) : [];

  const pick = (a, b) => (isNum(a) ? a : isNum(b) ? b : null);

  let min = pick(spectrum.freq_min_hz, d.freq_min_hz);
  let max = pick(spectrum.freq_max_hz, d.freq_max_hz);
  let source = min !== null || max !== null ? "explicit" : "";

  const binHz = pick(spectrum.bin_hz, d.bin_hz);
  const rows = pick(spectrum.freq_bins, d.freq_bins);
  if (binHz !== null && rows !== null && rows > 0) {
    if (min === null) min = 0;
    if (max === null) max = binHz * rows;
    if (!source) source = "bands";
  }

  if ((min === null || max === null) && freqs.length) {
    if (min === null) min = freqs[0];
    if (max === null) max = freqs[freqs.length - 1];
    if (!source) source = "frequencies";
  }

  if (min === null || max === null) {
    const sr = num(d.sample_rate, 0);
    min = 0;
    max = sr > 0 ? sr / 2 : 0;
    source = "fallback";
  }
  if (!(max > min)) max = min + 1;

  /* The scale needs a positive origin: a payload that starts at 0 Hz (the
   * DC bin) cannot be placed on a logarithmic scale, so the scale starts at
   * the lowest positive frequency the payload publishes. */
  let origin = null;
  for (const f of freqs) {
    if (f > 0) {
      origin = f;
      break;
    }
  }
  if (origin === null && binHz !== null && binHz > 0) origin = binHz;
  if (origin === null) origin = min > 0 ? min : max;
  if (!(origin > 0)) origin = 1;

  return { min, max, origin, source, binHz, rows };
}

/**
 * The mapping of a frequency onto the horizontal axis.
 *
 * The scale is logarithmic above the origin and linear below it, so the
 * axis can start at 0 Hz without losing the resolution of the low end.
 *
 * @param {object} axis     the axis of `frequencyAxis`
 * @param {number} width    the width of the canvas in CSS pixels
 * @returns {Function}      frequency → x in CSS pixels
 */
function axisMapper(axis, width) {
  const min = axis.min;
  const max = axis.max;
  const origin = axis.origin;
  const span = max - min;
  if (!(span > 0)) return () => 0;
  const logSpan = Math.log(max / origin);
  if (!(logSpan > 0)) {
    /* a single frequency: a linear axis */
    return (f) => (clamp(f, min, max) - min) / span * width;
  }
  const linear = ORIGIN_FRACTION;
  return (f) => {
    const v = clamp(f, min, max);
    if (v <= origin) return ((v - min) / (origin - min)) * linear * width;
    /* `t` is the name of the translation helper — this local one is `part` */
    const part = Math.log(v / origin) / logSpan;
    return (linear + (1 - linear) * part) * width;
  };
}

/** The graduations of the frequency axis: the round frequencies inside it. */
function axisTicks(axis) {
  const out = [];
  for (const f of TICK_CANDIDATES) {
    if (f < axis.min || f > axis.max) continue;
    out.push(f);
  }
  return out;
}

/* -------------------------------------------------------------- the model */

/**
 * Normalise a measurement payload into the fields the renderer needs.
 * Missing or malformed fields become empty arrays and `null` numbers.
 */
function normalise(data) {
  const d = data && typeof data === "object" ? data : {};
  const spectrum = d.spectrum && typeof d.spectrum === "object" ? d.spectrum : {};
  const stats = d.waveform_stats && typeof d.waveform_stats === "object" ? d.waveform_stats : {};
  const loud = d.loudness && typeof d.loudness === "object" ? d.loudness : {};
  const hist = d.histogram && typeof d.histogram === "object" ? d.histogram : {};
  const sel = d.selection && typeof d.selection === "object" ? d.selection : null;

  const freqs = Array.isArray(spectrum.frequencies) ? spectrum.frequencies : [];
  const mags = Array.isArray(spectrum.magnitudes_db) ? spectrum.magnitudes_db : [];
  const peaks = Array.isArray(spectrum.peaks_db) ? spectrum.peaks_db : [];

  const selection = sel
    ? {
        start: isNum(sel.start) ? sel.start : null,
        end: isNum(sel.end) ? sel.end : null,
        requestedStart: isNum(sel.requested_start) ? sel.requested_start : null,
        requestedEnd: isNum(sel.requested_end) ? sel.requested_end : null,
        samples: num(sel.samples, 0),
        duration: isNum(sel.duration) ? sel.duration : null,
        bounded: sel.bounded === true,
      }
    : null;

  /* An empty selection has nothing to measure: the payload then carries the
   * floor of every level, which would be read as a measurement. */
  const emptySelection = !!(selection && selection.bounded && selection.samples <= 0);
  const noSignal = emptySelection || (num(stats.samples, 0) <= 0 && mags.filter(isNum).length === 0);

  return {
    empty: d.empty === true,
    sampleRate: num(d.sample_rate, 0),
    channels: num(d.channels, 0),
    frames: num(d.frames, 0),
    duration: num(d.duration, 0),
    selection,
    emptySelection,
    hasSignal: !noSignal,
    axis: frequencyAxis(d),
    spectrum: {
      freqs: freqs.filter(isNum),
      mags: mags.filter(isNum),
      peaks: peaks.filter(isNum),
      minDb: isNum(spectrum.min_db) ? spectrum.min_db : -120,
      maxDb: isNum(spectrum.max_db) ? spectrum.max_db : 0,
      peakHz: isNum(spectrum.peak_hz) ? spectrum.peak_hz : 0,
      peakDb: isNum(spectrum.peak_db) ? spectrum.peak_db : null,
    },
    stats: {
      truePeak: isNum(stats.true_peak) ? stats.true_peak : null,
      truePeakDb: isNum(stats.true_peak_db) ? stats.true_peak_db : null,
      samplePeak: isNum(stats.sample_peak) ? stats.sample_peak : null,
      samplePeakDb: isNum(stats.sample_peak_db) ? stats.sample_peak_db : null,
      max: isNum(stats.max) ? stats.max : null,
      min: isNum(stats.min) ? stats.min : null,
      mean: isNum(stats.mean) ? stats.mean : null,
      rms: isNum(stats.rms) ? stats.rms : null,
      rmsDb: isNum(stats.rms_db) ? stats.rms_db : null,
      totalRms: isNum(stats.total_rms) ? stats.total_rms : null,
      totalRmsDb: isNum(stats.total_rms_db) ? stats.total_rms_db : null,
      dcOffset: isNum(stats.dc_offset) ? stats.dc_offset : null,
      dcOffsetDb: isNum(stats.dc_offset_db) ? stats.dc_offset_db : null,
      clipped: isNum(stats.possibly_clipped) ? Math.round(stats.possibly_clipped) : 0,
      samples: num(stats.samples, 0),
    },
    loudness: {
      integrated: isNum(loud.integrated_lufs) ? loud.integrated_lufs : null,
      shortTerm: isNum(loud.short_term_lufs) ? loud.short_term_lufs : null,
      momentary: isNum(loud.momentary_lufs) ? loud.momentary_lufs : null,
      range: isNum(loud.loudness_range_lu) ? loud.loudness_range_lu : null,
      truePeak: isNum(loud.true_peak_dbtp) ? loud.true_peak_dbtp : null,
    },
    correlation: isNum(d.correlation) ? d.correlation : null,
    histogram: {
      edges: Array.isArray(hist.bin_edges) ? hist.bin_edges.filter(isNum) : [],
      counts: Array.isArray(hist.counts) ? hist.counts.map((v) => num(v, 0)) : [],
      range: Array.isArray(hist.range) && hist.range.length === 2
        ? [num(hist.range[0], -1), num(hist.range[1], 1)]
        : [-1, 1],
      clipThreshold: num(hist.clip_threshold, 0.999),
      clipStart: num(hist.clip_bin_start, 0),
      clipEnd: num(hist.clip_bin_end, 0),
      outOfRange: num(hist.out_of_range, 0),
    },
  };
}

/** True when the payload holds nothing worth drawing. */
function isEmptyModel(model) {
  if (model.empty) return true;
  if (model.spectrum.mags.length) return false;
  if (model.stats.samples > 0) return false;
  if (model.loudness.integrated !== null) return false;
  if (model.correlation !== null) return false;
  if (model.histogram.counts.length) return false;
  return true;
}

/**
 * A fingerprint of everything a drawing depends on.
 *
 * Two payloads with the same fingerprint draw the same picture, so the
 * panel can leave the canvas alone instead of drawing it again.
 */
function fingerprint(model) {
  return [
    model.empty ? 1 : 0,
    model.sampleRate,
    model.channels,
    model.frames,
    model.spectrum.freqs.length,
    model.spectrum.mags.length,
    model.spectrum.peaks.length,
    hashNumbers(model.spectrum.mags),
    hashNumbers(model.spectrum.peaks),
    model.spectrum.peakHz,
    model.spectrum.peakDb,
    model.spectrum.minDb,
    model.spectrum.maxDb,
    model.axis.min,
    model.axis.max,
    model.axis.origin,
    model.axis.source,
    model.correlation,
    model.loudness.integrated,
    model.loudness.shortTerm,
    model.loudness.momentary,
    model.loudness.range,
    model.loudness.truePeak,
    model.stats.truePeak,
    model.stats.truePeakDb,
    model.stats.samplePeak,
    model.stats.samplePeakDb,
    model.stats.max,
    model.stats.min,
    model.stats.mean,
    model.stats.rms,
    model.stats.rmsDb,
    model.stats.totalRms,
    model.stats.totalRmsDb,
    model.stats.dcOffset,
    model.stats.dcOffsetDb,
    model.stats.clipped,
    model.histogram.counts.length,
    hashNumbers(model.histogram.counts),
    model.histogram.clipThreshold,
    model.histogram.clipStart,
    model.histogram.clipEnd,
    model.histogram.outOfRange,
    model.emptySelection ? 1 : 0,
    model.selection
      ? `${model.selection.start}:${model.selection.end}:${model.selection.samples}:${model.selection.bounded}`
      : "none",
  ].join("|");
}

/** A cheap hash of a list of numbers. */
function hashNumbers(values) {
  let h = 2166136261;
  for (let i = 0; i < values.length; i++) {
    const q = Math.round(values[i] * 1000) | 0;
    h = Math.imul(h ^ (q & 0xffff), 16777619);
    h = Math.imul(h ^ ((q >>> 16) & 0xffff), 16777619);
  }
  return h >>> 0;
}

/* --------------------------------------------------------------- spectrum */

/** The graduations of the decibel axis, derived from the floor of the payload. */
function dbTicks(floorDb) {
  const out = [];
  const bottom = Math.min(0, floorDb);
  for (let db = 0; db >= bottom - 1e-9; db -= 20) out.push(Math.round(db * 100) / 100);
  const last = out[out.length - 1];
  if (last !== bottom) out.push(bottom);
  return out;
}

/**
 * Draw the spectrum analyser.
 *
 * The horizontal axis is the frequency axis of the payload — the range the
 * payload publishes, mapped logarithmically above its origin — and the
 * vertical axis is linear in decibels, from the floor the payload reports
 * up to full scale.  A peak hold line remembers the highest level every
 * frequency bin has reached since the last data update.
 */
function drawSpectrum(canvas, model, palette, hold) {
  if (!canvas) return;
  const { w, h, ratio } = prepareCanvas(canvas, 152);
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, w, h);
  if (palette.bg2) {
    ctx.fillStyle = rgba(palette.bg2, 1);
    ctx.fillRect(0, 0, w, h);
  }

  const axis = model.axis;
  const xOf = axisMapper(axis, w);
  const dbTop = 0;
  const dbBottom = Math.min(0, model.spectrum.minDb);
  const dbSpan = dbTop - dbBottom || 1;
  const yOf = (db) => ((dbTop - clamp(db, dbBottom, dbTop)) / dbSpan) * h;

  ctx.save();
  ctx.font = `${palette.fontXs}px ${palette.fontMono}`;
  ctx.textBaseline = "middle";

  /* --- horizontal (decibel) grid --- */
  for (const db of dbTicks(dbBottom)) {
    const yExact = yOf(db);
    const y = Math.round(yExact) + 0.5;
    if (y >= -0.5 && y <= h + 0.5) {
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(w, y);
      ctx.strokeStyle = db === 0
        ? (palette.line ? rgba(palette.line, 0.95) : null)
        : (palette.lineSoft ? rgba(palette.lineSoft, 0.85) : null);
      ctx.lineWidth = 1;
      if (ctx.strokeStyle) ctx.stroke();
    }
    const label = `${db}`;
    ctx.fillStyle = palette.txt3 ? rgba(palette.txt3, 1) : null;
    if (ctx.fillStyle) {
      const tw = ctx.measureText(label).width;
      /* the label of the lowest graduation sits above the frequency labels */
      const ly = clamp(yExact - palette.fontXs * 0.6, palette.fontXs * 0.5, h - palette.fontXs * 2.2);
      ctx.fillText(label, w - tw - 3, ly);
    }
  }

  /* --- vertical (frequency) grid --- */
  ctx.textBaseline = "alphabetic";
  let lastLabelEnd = -1e9;
  for (const f of axisTicks(axis)) {
    const x = Math.round(xOf(f)) + 0.5;
    if (x < 0 || x > w) continue;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, h);
    ctx.strokeStyle = palette.lineSoft ? rgba(palette.lineSoft, 0.8) : null;
    ctx.lineWidth = 1;
    if (ctx.strokeStyle) ctx.stroke();
    const label = f >= 1000 ? `${f / 1000}k` : `${f}`;
    /* skip a label that would collide with the previous one */
    const tw = ctx.measureText ? ctx.measureText(label).width : label.length * 6;
    if (x - lastLabelEnd < 6) continue;
    lastLabelEnd = x + tw;
    ctx.fillStyle = palette.txt3 ? rgba(palette.txt3, 1) : null;
    if (ctx.fillStyle) ctx.fillText(label, Math.min(x + 2, w - tw - 2), h - 3);
  }

  const freqs = model.spectrum.freqs;
  const mags = model.spectrum.mags;
  if (freqs.length && mags.length) {
    const n = Math.min(freqs.length, mags.length);
    /* --- the spectrum itself --- */
    ctx.beginPath();
    let started = false;
    for (let i = 0; i < n; i++) {
      const x = xOf(freqs[i]);
      const y = yOf(clamp(mags[i], dbBottom, dbTop));
      if (!started) {
        ctx.moveTo(x, y);
        started = true;
      } else {
        ctx.lineTo(x, y);
      }
    }
    if (started) {
      ctx.strokeStyle = palette.accent ? rgba(palette.accent, 0.95) : null;
      ctx.lineWidth = 1.25;
      if (ctx.strokeStyle) ctx.stroke();

      ctx.lineTo(xOf(freqs[n - 1]), h);
      ctx.lineTo(xOf(freqs[0]), h);
      ctx.closePath();
      ctx.fillStyle = palette.accent ? rgba(palette.accent, 0.22) : null;
      if (ctx.fillStyle) ctx.fill();
    }

    /* --- the peak hold line --- */
    if (hold && hold.values && hold.values.length >= n) {
      ctx.beginPath();
      started = false;
      for (let i = 0; i < n; i++) {
        const x = xOf(freqs[i]);
        const y = yOf(clamp(hold.values[i], dbBottom, dbTop));
        if (!started) {
          ctx.moveTo(x, y);
          started = true;
        } else {
          ctx.lineTo(x, y);
        }
      }
      ctx.strokeStyle = palette.accent3 ? rgba(palette.accent3, 0.9) : null;
      ctx.lineWidth = 1;
      if (ctx.strokeStyle) ctx.stroke();
    }

    /* --- the peak marker --- */
    const peakHz = model.spectrum.peakHz;
    if (peakHz > 0 && model.spectrum.peakDb !== null) {
      const px = xOf(peakHz);
      const py = yOf(clamp(model.spectrum.peakDb, dbBottom, dbTop));
      ctx.beginPath();
      ctx.moveTo(px, 0);
      ctx.lineTo(px, h);
      ctx.strokeStyle = palette.accent3 ? rgba(palette.accent3, 0.55) : null;
      ctx.lineWidth = 1;
      ctx.setLineDash([2, 3]);
      if (ctx.strokeStyle) ctx.stroke();
      ctx.setLineDash([]);
      const label = fmtHz(peakHz);
      const tw = ctx.measureText ? ctx.measureText(label).width : label.length * 6;
      const lx = px + 3 + tw > w ? px - 3 - tw : px + 3;
      ctx.fillStyle = palette.txt3 ? rgba(palette.txt3, 1) : null;
      if (ctx.fillStyle) ctx.fillText(label, lx, clamp(py - 4, palette.fontXs, h - 2));
    }
  } else {
    ctx.fillStyle = palette.txt2 ? rgba(palette.txt2, 0.9) : null;
    if (ctx.fillStyle) {
      ctx.textAlign = "center";
      ctx.fillText(t("analysis.noSpectrum", "No spectrum"), w / 2, h / 2);
      ctx.textAlign = "left";
    }
  }
  ctx.restore();
}

/**
 * Update the peak hold buffer.
 *
 * The hold starts at the highest level every bin reached in any analysis
 * frame (the ``peaks_db`` trace) and then keeps the highest level it has
 * seen since the last data update.  A slow decay (in dB per redraw) lets
 * it fall back when the material changes.
 */
function updateHold(hold, mags, peaks) {
  if (!hold) return;
  const source = peaks && peaks.length === mags.length ? peaks : mags;
  const n = mags.length;
  if (!hold.values || hold.values.length !== n) {
    hold.values = new Float64Array(n);
    for (let i = 0; i < n; i++) hold.values[i] = Math.max(source[i], mags[i]);
    return;
  }
  const decay = 0.6;
  for (let i = 0; i < n; i++) {
    const decayed = hold.values[i] - decay;
    hold.values[i] = Math.max(mags[i], decayed);
  }
}

/* -------------------------------------------------------------- histogram */

/** Draw the peak histogram with the clipping region highlighted. */
function drawHistogram(canvas, model, palette) {
  if (!canvas) return;
  const { w, h, ratio } = prepareCanvas(canvas, 104);
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, w, h);
  if (palette.bg2) {
    ctx.fillStyle = rgba(palette.bg2, 1);
    ctx.fillRect(0, 0, w, h);
  }

  const hist = model.histogram;
  const counts = hist.counts;
  const edges = hist.edges;
  const [lo, hi] = hist.range;
  const span = hi - lo || 1;
  const xOf = (a) => ((a - lo) / span) * w;

  ctx.save();
  ctx.font = `${palette.fontXs}px ${palette.fontMono}`;
  ctx.textBaseline = "middle";

  /* the clipping region, drawn behind the bars */
  const clipLow = Math.max(0, Math.min(hist.clipStart, counts.length));
  const clipHigh = Math.max(clipLow, Math.min(hist.clipEnd, counts.length));
  if (clipHigh > clipLow && palette.danger) {
    const x0 = edges.length ? xOf(edges[clipLow]) : 0;
    const x1 = edges.length ? xOf(edges[clipHigh]) : w;
    ctx.fillStyle = rgba(palette.danger, 0.12);
    ctx.fillRect(x0, 0, Math.max(1, x1 - x0), h);
    const mx = counts.length - clipHigh;
    if (mx > 0 && edges.length && edges[mx + (clipHigh - clipLow)] !== undefined) {
      const y0 = xOf(edges[mx]);
      const y1 = xOf(edges[mx + (clipHigh - clipLow)]);
      ctx.fillRect(y0, 0, Math.max(1, y1 - y0), h);
    }
  }

  let maxCount = 0;
  for (const c of counts) if (c > maxCount) maxCount = c;

  if (maxCount > 0 && counts.length) {
    const barWidth = w / counts.length;
    for (let i = 0; i < counts.length; i++) {
      const count = counts[i];
      if (!(count > 0)) continue;
      const bh = Math.max(1, (count / maxCount) * (h - 2));
      const inClip = (i >= clipLow && i < clipHigh) || (i >= counts.length - clipHigh && i < counts.length - clipLow);
      ctx.fillStyle = inClip
        ? (palette.danger ? rgba(palette.danger, 0.9) : null)
        : (palette.accent ? rgba(palette.accent, 0.7) : null);
      if (!ctx.fillStyle) continue;
      const x = edges.length ? xOf(edges[i]) : i * barWidth;
      const width = edges.length > i + 1
        ? Math.max(1, xOf(edges[i + 1]) - x - 0.5)
        : Math.max(1, barWidth - 0.5);
      ctx.fillRect(x, h - bh, width, bh);
    }
  } else {
    ctx.fillStyle = palette.txt2 ? rgba(palette.txt2, 0.9) : null;
    if (ctx.fillStyle) {
      ctx.textAlign = "center";
      ctx.fillText(t("analysis.noHistogram", "No histogram"), w / 2, h / 2);
      ctx.textAlign = "left";
    }
  }

  /* the clipping threshold markers */
  if (palette.danger && hist.clipThreshold > 0 && hist.clipThreshold < 1) {
    ctx.setLineDash([2, 2]);
    for (const sign of [1, -1]) {
      const x = Math.round(xOf(sign * hist.clipThreshold)) + 0.5;
      if (x < 0 || x > w) continue;
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, h);
      ctx.strokeStyle = rgba(palette.danger, 0.85);
      ctx.lineWidth = 1;
      ctx.stroke();
    }
    ctx.setLineDash([]);
  }
  ctx.restore();
}

/* -------------------------------------------------------------- sections */

const LEVEL_FLOOR = -60;
const LEVEL_CEIL = 0;

/** One meter row: a label, a value and a small bar. */
function meterRow(label, value, text, options = {}) {
  const row = el("div", { class: "an-meter" + (options.warn ? " an-warnrow" : "") });
  row.append(el("span", { class: "an-label" }, label));
  row.append(el("span", { class: "an-value" }, text));
  const bar = el("div", { class: "an-bar" });
  const fill = el("span", { class: "an-bar-fill" + (options.warn ? " warn" : options.alt ? " alt" : "") });
  if (isNum(value)) {
    const low = isNum(options.min) ? options.min : LEVEL_FLOOR;
    const high = isNum(options.max) ? options.max : LEVEL_CEIL;
    const span = high - low || 1;
    const pct = clamp01((value - low) / span) * 100;
    fill.style.width = `${pct.toFixed(2)}%`;
  }
  bar.append(fill);
  row.append(bar);
  return row;
}

/**
 * A label / value pair.
 * Returns the two nodes so the caller can place them in a two column grid.
 */
function statRow(label, text, options = {}) {
  const labelNode = el("span", { class: "an-label" }, label);
  const valueNode = el("span", { class: "an-value" + (options.warn ? " an-warn" : "") }, text);
  if (options.title) valueNode.setAttribute("title", options.title);
  return [labelNode, valueNode];
}

/* ------------------------------------------------------------ public API */

/**
 * An empty measurement payload — the placeholder the inspector shows
 * before an audio file has been measured.
 *
 * @returns {object} a payload with the same shape as a real measurement
 */
export function emptyAnalysis() {
  return {
    empty: true,
    sample_rate: 0,
    channels: 0,
    frames: 0,
    duration: 0,
    selection: null,
    spectrum: {
      frequencies: [],
      magnitudes_db: [],
      peaks_db: [],
      min_db: -120,
      max_db: -120,
      fft_size: 0,
      frames: 0,
      peak_hz: 0,
      peak_db: -120,
    },
    waveform_stats: {
      true_peak: 0,
      true_peak_db: null,
      sample_peak: 0,
      sample_peak_db: null,
      max: 0,
      min: 0,
      mean: 0,
      rms: 0,
      rms_db: null,
      total_rms: 0,
      total_rms_db: null,
      dc_offset: 0,
      dc_offset_db: null,
      possibly_clipped: 0,
      samples: 0,
    },
    loudness: {
      integrated_lufs: null,
      short_term_lufs: null,
      momentary_lufs: null,
      loudness_range_lu: null,
      true_peak_dbtp: null,
    },
    correlation: null,
    histogram: {
      bin_edges: [],
      counts: [],
      bin_width: 0,
      range: [-1, 1],
      clip_threshold: 0.999,
      clip_bin_start: 0,
      clip_bin_end: 0,
      out_of_range: 0,
    },
  };
}

/**
 * Render the Analysis tab into `container`.
 *
 * @param {Element} container              the element that receives the tab
 * @param {object} data                    a measurement payload (see the header)
 * @param {object} [opts]                  optional settings
 * @param {number} [opts.sampleRate]       fallback sample rate
 * @param {object} [opts.selection]        fallback selection {start, end}
 * @returns {{update: Function, destroy: Function, element: Element,
 *            axis: object, stats: object}}
 */
export function renderAnalysis(container, data, opts = {}) {
  const doc = ownerDoc(container);
  if (!doc || !container) {
    throw new Error("renderAnalysis: a DOM container element is required");
  }
  ensureStyle(doc);

  const options = opts && typeof opts === "object" ? opts : {};

  /** Apply the settings of the caller to a payload. */
  function withFallbacks(source) {
    const next = normalise(source);
    if (!next.sampleRate && isNum(options.sampleRate)) {
      next.sampleRate = options.sampleRate;
    }
    if (!next.selection && options.selection && typeof options.selection === "object") {
      next.selection = {
        start: isNum(options.selection.start) ? options.selection.start : null,
        end: isNum(options.selection.end) ? options.selection.end : null,
        requestedStart: null,
        requestedEnd: null,
        samples: null,
        duration: null,
        bounded: true,
      };
    }
    return next;
  }

  let model = withFallbacks(data);

  /** The peak hold state of the spectrum analyser. */
  const hold = { values: null };

  const root = el("div", { class: "an-root" });
  const previous = ROOTS.get(container);
  if (previous && previous.parentNode) previous.parentNode.removeChild(previous);
  ROOTS.set(container, root);
  container.appendChild(root);

  let spectrumCanvas = null;
  let histogramCanvas = null;
  let palette = readPalette(root);
  let resizeObserver = null;

  /* the state of the last drawing: everything a picture depends on */
  let lastFingerprint = null;
  let canvasGeneration = 0;
  const counters = { paints: 0, spectrum: 0, histogram: 0, skipped: 0 };

  /** The geometry of the two canvases, as a comparable string. */
  function geometry() {
    const spec = canvasBox(spectrumCanvas, 152);
    const hist = canvasBox(histogramCanvas, 104);
    return `${canvasGeneration}|${spec.w}x${spec.h}@${spec.ratio}|${hist.w}x${hist.h}@${hist.ratio}`;
  }

  /**
   * Draw the panel.
   *
   * Nothing is drawn when nothing changed: the same payload in the same
   * geometry leaves the canvas alone.
   *
   * @param {boolean} [force]  draw even when nothing changed
   * @returns {boolean}        true when the canvases were drawn
   */
  function paint(force) {
    const key = `${fingerprint(model)}|${geometry()}`;
    if (!force && key === lastFingerprint) {
      counters.skipped += 1;
      return false;
    }
    palette = readPalette(root);
    updateHold(hold, model.spectrum.mags, model.spectrum.peaks);
    drawSpectrum(spectrumCanvas, model, palette, hold);
    drawHistogram(histogramCanvas, model, palette);
    lastFingerprint = `${fingerprint(model)}|${geometry()}`;
    counters.paints += 1;
    counters.spectrum += 1;
    counters.histogram += 1;
    return true;
  }

  function build() {
    root.replaceChildren();
    spectrumCanvas = null;
    histogramCanvas = null;
    canvasGeneration += 1;

    if (isEmptyModel(model)) {
      root.append(
        el(
          "p",
          { class: "an-empty" },
          t("analysis.empty", "No measurement yet. Open an audio file to populate this tab.")
        )
      );
      return;
    }

    /* --- context line ------------------------------------------------ */
    const context = el("div", { class: "an-section" });
    const bits = [];
    if (model.sampleRate > 0) bits.push(`${model.sampleRate} Hz`);
    if (model.channels > 0) bits.push(t("analysis.channels", "{n} ch", { n: model.channels }));
    if (model.duration > 0) bits.push(`${model.duration.toFixed(2)} s`);
    if (bits.length) context.append(el("div", { class: "an-note" }, bits.join("  ·  ")));
    root.append(context);

    /* --- selection ---------------------------------------------------- */
    const sel = model.selection;
    const selSection = el("div", { class: "an-section" });
    selSection.append(el("div", { class: "an-title" }, t("analysis.selectionTitle", "Selection")));
    const selGrid = el("div", { class: "an-grid" });
    if (sel && sel.start !== null && sel.end !== null) {
      selGrid.append(
        ...statRow(t("analysis.range", "Range"), `${sel.start.toFixed(2)} – ${sel.end.toFixed(2)} s`)
      );
      const length = sel.duration !== null ? sel.duration : Math.max(0, sel.end - sel.start);
      selGrid.append(...statRow(t("analysis.length", "Length"), fmtSeconds(length)));
      selGrid.append(...statRow(t("analysis.samples", "Samples"), fmtInt(sel.samples)));
    } else if (sel && sel.bounded) {
      selGrid.append(...statRow(t("analysis.range", "Range"), "—"));
    } else {
      selGrid.append(...statRow(t("analysis.range", "Range"), t("analysis.wholeFile", "the whole file")));
      selGrid.append(...statRow(t("analysis.length", "Length"), fmtSeconds(model.duration)));
    }
    selSection.append(selGrid);
    if (model.emptySelection) {
      selSection.append(
        el(
          "p",
          { class: "an-empty" },
          t("analysis.selectionEmpty", "The selection is empty — there is nothing to measure.")
        )
      );
    } else if (sel && sel.bounded) {
      const wanted = [];
      if (sel.requestedStart !== null) wanted.push(`${sel.requestedStart.toFixed(2)} s`);
      if (sel.requestedEnd !== null) wanted.push(`${sel.requestedEnd.toFixed(2)} s`);
      const clamped =
        (sel.requestedStart !== null && sel.start !== null && sel.requestedStart !== sel.start) ||
        (sel.requestedEnd !== null && sel.end !== null && sel.requestedEnd !== sel.end);
      if (clamped && wanted.length) {
        selSection.append(
          el(
            "p",
            { class: "an-note" },
            t(
              "analysis.selectionClamped",
              "The request {list} lies outside the audio; the measured range is the one above.",
              { list: wanted.join(" – ") }
            )
          )
        );
      }
    }
    root.append(selSection);

    /* --- spectrum ---------------------------------------------------- */
    const specSection = el("div", { class: "an-section" });
    specSection.append(el("div", { class: "an-title" }, t("analysis.spectrum", "Spectrum")));
    spectrumCanvas = el("canvas", { class: "an-canvas an-spectrum" });
    spectrumCanvas.setAttribute("role", "img");
    spectrumCanvas.setAttribute(
      "aria-label",
      t("analysis.spectrumRange", "Magnitude spectrum, {min} to {max}", {
        min: fmtAxisHz(model.axis.min),
        max: fmtAxisHz(model.axis.max),
      })
    );
    spectrumCanvas.setAttribute("data-freq-min", String(model.axis.min));
    spectrumCanvas.setAttribute("data-freq-max", String(model.axis.max));
    spectrumCanvas.setAttribute("data-freq-origin", String(model.axis.origin));
    spectrumCanvas.setAttribute("data-freq-source", model.axis.source);
    specSection.append(spectrumCanvas);
    const specAxis = el("div", { class: "an-axis" });
    specAxis.append(el("span", {}, fmtAxisHz(model.axis.min)));
    const peakText = model.spectrum.peakDb !== null
      ? t("analysis.peak", "peak {value} @ {hz} Hz", {
          value: fmtDb(model.spectrum.peakDb, 1),
          /* the catalogue carries the unit — the value is a plain number */
          hz: model.spectrum.peakHz >= 10000
            ? String(Math.round(model.spectrum.peakHz))
            : model.spectrum.peakHz.toFixed(1),
        })
      : t("analysis.peakNone", "peak —");
    specAxis.append(el("span", {}, peakText));
    specAxis.append(el("span", {}, fmtAxisHz(model.axis.max)));
    specSection.append(specAxis);
    root.append(specSection);

    /* --- loudness ---------------------------------------------------- */
    const loudSection = el("div", { class: "an-section" });
    loudSection.append(el("div", { class: "an-title" }, t("analysis.loudness", "Loudness")));
    const loud = model.loudness;
    const show = (v) => (model.hasSignal ? v : null);
    loudSection.append(
      meterRow(t("analysis.integrated", "Integrated"), show(loud.integrated), show(loud.integrated) === null ? "—" : `${loud.integrated.toFixed(1)} LUFS`)
    );
    loudSection.append(
      meterRow(t("analysis.shortTerm", "Short term"), show(loud.shortTerm), show(loud.shortTerm) === null ? "—" : `${loud.shortTerm.toFixed(1)} LUFS`, { alt: true })
    );
    loudSection.append(
      meterRow(t("analysis.momentary", "Momentary"), show(loud.momentary), show(loud.momentary) === null ? "—" : `${loud.momentary.toFixed(1)} LUFS`)
    );
    loudSection.append(
      meterRow(t("analysis.loudnessRange", "Loudness range"), show(loud.range), show(loud.range) === null ? "—" : `${loud.range.toFixed(1)} LU`, {
        alt: true,
        min: 0,
        max: 60,
      })
    );
    const truePeak = show(loud.truePeak);
    const tpWarn = truePeak !== null && truePeak > -0.1;
    loudSection.append(
      meterRow(t("analysis.truePeak", "True peak"), truePeak, truePeak === null ? "—" : fmtDb(truePeak, 1, "TP"), {
        warn: tpWarn,
      })
    );
    root.append(loudSection);

    /* --- waveform statistics ----------------------------------------- */
    const statSection = el("div", { class: "an-section" });
    statSection.append(
      el("div", { class: "an-title" }, t("analysis.waveformStats", "Waveform statistics"))
    );
    const stats = model.stats;
    const level = (v) => (model.hasSignal ? v : null);
    const grid = el("div", { class: "an-grid" });
    grid.append(...statRow(t("analysis.samplePeak", "Sample peak"), fmtDb(level(stats.samplePeakDb), 1, "FS")));
    grid.append(
      ...statRow(t("analysis.truePeak", "True peak"), fmtDb(level(stats.truePeakDb), 1, "TP"), { warn: tpWarn })
    );
    grid.append(
      ...statRow(t("analysis.maximum", "Maximum"), fmtNum(level(stats.max), 4), {
        title: t("analysis.linearAmplitude", "linear amplitude"),
      })
    );
    grid.append(
      ...statRow(t("analysis.minimum", "Minimum"), fmtNum(level(stats.min), 4), {
        title: t("analysis.linearAmplitude", "linear amplitude"),
      })
    );
    grid.append(
      ...statRow(t("analysis.mean", "Mean"), fmtNum(level(stats.mean), 4), {
        title: t("analysis.linearAmplitude", "linear amplitude"),
      })
    );
    grid.append(
      ...statRow(t("analysis.rms", "RMS"), fmtDb(level(stats.rmsDb), 1), {
        title:
          stats.rms === null
            ? ""
            : t("analysis.linearValue", "linear {value}", { value: fmtNum(stats.rms, 6) }),
      })
    );
    grid.append(...statRow(t("analysis.totalRms", "Total RMS"), fmtDb(level(stats.totalRmsDb), 1)));
    grid.append(...statRow(t("analysis.dcOffset", "DC offset"), fmtDb(level(stats.dcOffsetDb), 1)));
    grid.append(
      ...statRow(t("analysis.possiblyClipped", "Possibly clipped"), fmtInt(stats.clipped), {
        warn: stats.clipped > 0,
        title: t("analysis.clipThresholdTitle", "samples at or above the clipping threshold"),
      })
    );
    statSection.append(grid);
    root.append(statSection);

    /* --- correlation -------------------------------------------------- */
    const corrSection = el("div", { class: "an-section" });
    corrSection.append(el("div", { class: "an-title" }, t("analysis.correlation", "Correlation")));
    const value = model.correlation;
    const corr = el("div", { class: "an-corr" });
    corr.append(el("div", { class: "an-corr-track" }));
    const fill = el("div", { class: "an-corr-fill" });
    if (value !== null) {
      const v = clamp(value, -1, 1);
      const half = 50;
      const centre = 50;
      const end = centre + v * half;
      fill.style.left = `${Math.min(centre, end)}%`;
      fill.style.width = `${Math.abs(end - centre)}%`;
      if (Math.abs(v) < 0.05) fill.classList.add("flat");
      else if (v < 0) fill.classList.add("neg");
    }
    corr.append(fill);
    corr.append(el("div", { class: "an-corr-centre" }));
    corrSection.append(corr);
    const corrAxis = el("div", { class: "an-axis" });
    corrAxis.append(el("span", {}, t("analysis.correlationInverted", "−1 (inverted)")));
    corrAxis.append(el("span", {}, value === null ? "—" : value.toFixed(3)));
    corrAxis.append(el("span", {}, t("analysis.correlationIdentical", "+1 (identical)")));
    corrSection.append(corrAxis);
    root.append(corrSection);

    /* --- histogram ---------------------------------------------------- */
    const histSection = el("div", { class: "an-section" });
    histSection.append(el("div", { class: "an-title" }, t("analysis.peakHistogram", "Peak histogram")));
    histogramCanvas = el("canvas", { class: "an-canvas an-histogram" });
    histogramCanvas.setAttribute("role", "img");
    histogramCanvas.setAttribute("aria-label", t("analysis.peakHistogram", "Peak histogram"));
    histSection.append(histogramCanvas);
    const histAxis = el("div", { class: "an-axis" });
    const [lo, hi] = model.histogram.range;
    histAxis.append(el("span", {}, fmtNum(lo, 2)));
    histAxis.append(
      el(
        "span",
        { class: model.stats.clipped > 0 ? "an-warn" : "" },
        model.stats.clipped > 0
          ? t("analysis.clippedCount", "{n} clipped", { n: fmtInt(model.stats.clipped) })
          : t("analysis.clipThreshold", "clip ≥ {value}", {
              value: model.histogram.clipThreshold.toFixed(3),
            })
      )
    );
    histAxis.append(el("span", {}, fmtNum(hi, 2)));
    histSection.append(histAxis);
    root.append(histSection);

    paint(true);
  }

  build();

  /* keep the canvases in step with the inspector width */
  if (typeof ResizeObserver === "function" && root) {
    resizeObserver = new ResizeObserver(() => {
      /* a panel that was taken out of the document has nothing to draw */
      if (root.isConnected === false) {
        try {
          resizeObserver.disconnect();
        } catch (err) {
          /* already gone */
        }
        resizeObserver = null;
        return;
      }
      paint();
    });
    try {
      resizeObserver.observe(root);
    } catch (err) {
      resizeObserver = null;
    }
  }

  return {
    /** Replace the payload and redraw — but only when it really changed. */
    update(next) {
      const candidate = withFallbacks(next);
      if (fingerprint(candidate) === fingerprint(model)) {
        /* the payload did not change: nothing to draw */
        counters.skipped += 1;
        return this;
      }
      model = candidate;
      hold.values = null;
      build();
      return this;
    },
    /** Draw the canvases again, even when nothing changed. */
    redraw() {
      paint(true);
      return this;
    },
    /** Remove the tab from the DOM. */
    destroy() {
      if (resizeObserver) {
        try {
          resizeObserver.disconnect();
        } catch (err) {
          /* already gone */
        }
        resizeObserver = null;
      }
      if (root.parentNode) root.parentNode.removeChild(root);
      if (ROOTS.get(container) === root) ROOTS.delete(container);
      return this;
    },
    /** The root element of the tab. */
    element: root,
    /** The frequency axis the panel drew. */
    axis: model.axis,
    /** How often the panel drew. */
    stats: counters,
  };
}
