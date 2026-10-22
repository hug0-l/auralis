/* Auralis — composite view renderer.
 *
 * Owns the two canvases of the centre stage:
 *   • the spectrogram canvas (frequency × time, dB colour ramp)
 *   • the waveform canvas   (amplitude × time, min/max envelope)
 *
 * Design rules honoured here:
 *   • every colour comes from the CSS custom properties in css/tokens.css —
 *     there is not one colour literal in this file;
 *   • vanilla ES module, no dependencies, no build step;
 *   • the spectrogram raster is cached in an offscreen canvas so that a pan
 *     costs one drawImage instead of a full re-render.
 *
 * Assumptions about the wire format (see CONTRACT §7 /api/analyze):
 *   • spectrogram : a 2-D matrix, rows = frequency bins (linear in Hz over
 *                   [0, sample_rate/2]), columns = time frames, values in dB.
 *                   A transposed matrix is detected and swapped automatically.
 *   • waveform    : an array of [min, max] pairs (or a flat mono envelope).
 */

import { t } from "./i18n.js";



const SPEC_STOPS = 8;                       /* --spec-0 .. --spec-7            */
const SPEC_VARS = Array.from({ length: SPEC_STOPS }, (_, i) => `--spec-${i}`);
const MAX_CACHE_COLS = 4096;                /* bounds the raster build cost     */
const FREQ_TICKS = [50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000];
const FREQ_MAJORS = new Set([100, 1000, 10000]);
const TIME_STEPS = [0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5,
  1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];
const MEL_HZ = 700;
const MIN_ZOOM = 1;
const MAX_ZOOM = 128;

/* ==================================================================== *
 * colour helpers — tokens in, rgba() out
 * ==================================================================== */

/**
 * Parse a CSS colour string into `[r, g, b, a]`.
 * Understands #rgb, #rgba, #rrggbb, #rrggbbaa, rgb(), rgba().
 * Returns null when the value cannot be parsed.
 */
export function parseColor(input) {
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
    if (parts.length >= 3 && parts.every((n) => !Number.isNaN(n))) {
      return [parts[0], parts[1], parts[2], parts.length > 3 ? parts[3] : 1];
    }
  }
  return null;
}

/** Format a parsed colour as a CSS rgba() string, optionally overriding alpha. */
export function rgba(color, alpha) {
  if (!color) return null;
  const a = alpha === undefined || alpha === null ? color[3] : alpha;
  return `rgba(${color[0] | 0}, ${color[1] | 0}, ${color[2] | 0}, ${a})`;
}

/**
 * Read the spectrogram colour ramp (--spec-0 … --spec-7) from the computed
 * style of `el` (defaults to the document root).
 *
 * Returns an array of eight `[r, g, b, a]` entries; an entry is null when the
 * corresponding custom property is missing or unparsable.
 */
export function rampFromTokens(el) {
  const doc = typeof document !== "undefined" ? document : null;
  let node = el || (doc ? doc.documentElement : null);
  if (!node) return new Array(SPEC_STOPS).fill(null);

  let cs = null;
  try {
    cs = (typeof getComputedStyle === "function" ? getComputedStyle(node) : null);
  } catch (err) {
    cs = null;
  }
  const read = (name) => {
    if (!cs) return "";
    const v = cs.getPropertyValue(name);
    return v ? String(v).trim() : "";
  };

  let raw = SPEC_VARS.map((name) => parseColor(read(name)));

  /* A detached element has no resolved custom properties — fall back to the
     document root, where tokens.css declares :root. */
  if (raw.every((c) => c === null) && doc && doc.documentElement && node !== doc.documentElement) {
    try {
      const rootCs = getComputedStyle(doc.documentElement);
      raw = SPEC_VARS.map((name) => parseColor((rootCs.getPropertyValue(name) || "").trim()));
    } catch (err) { /* keep the empty ramp */ }
  }
  return raw;
}

/** Fill the gaps of a possibly incomplete ramp so it becomes a dense 8-stop ramp. */
export function resolveRamp(raw) {
  const list = Array.isArray(raw) ? raw.slice(0, SPEC_STOPS) : [];
  while (list.length < SPEC_STOPS) list.push(null);
  let first = -1;
  for (let i = 0; i < list.length; i++) {
    if (list[i]) { first = i; break; }
  }
  if (first < 0) return [];
  const out = [];
  for (let i = 0; i < list.length; i++) {
    if (list[i]) out.push(list[i].slice());
    else if (i > 0) out.push(out[i - 1].slice());
    else out.push(list[first].slice());
  }
  for (let i = 0; i < out.length; i++) {
    if (!out[i]) out[i] = out[out.length - 1].slice();
  }
  return out;
}

/** Sample a dense ramp at position u ∈ [0,1] → [r, g, b]. */
export function sampleRamp(ramp, u) {
  const n = ramp.length;
  if (n === 0) return [0, 0, 0];
  if (n === 1) return [ramp[0][0], ramp[0][1], ramp[0][2]];
  let p = u * (n - 1);
  if (p < 0) p = 0; else if (p > n - 1) p = n - 1;
  const i = Math.floor(p);
  if (i >= n - 1) return [ramp[n - 1][0], ramp[n - 1][1], ramp[n - 1][2]];
  const f = p - i;
  const a = ramp[i];
  const b = ramp[i + 1];
  return [
    Math.round(a[0] + (b[0] - a[0]) * f),
    Math.round(a[1] + (b[1] - a[1]) * f),
    Math.round(a[2] + (b[2] - a[2]) * f),
  ];
}

/** Build a 256-entry RGB lookup table from a dense ramp. */
export function buildLut(ramp) {
  const lut = new Uint8Array(256 * 3);
  for (let i = 0; i < 256; i++) {
    const c = sampleRamp(ramp, i / 255);
    lut[i * 3] = c[0];
    lut[i * 3 + 1] = c[1];
    lut[i * 3 + 2] = c[2];
  }
  return lut;
}

/**
 * The device pixel ratio used for every canvas in the app.
 * Clamped to [1, 3] so a 3× phone does not allocate a 9× buffer.
 */
export function dpr() {
  const raw = (typeof globalThis !== "undefined" && globalThis.devicePixelRatio) || 1;
  const v = Number(raw);
  if (!isFinite(v) || v <= 0) return 1;
  return Math.max(1, Math.min(3, v));
}

/* ==================================================================== *
 * small numeric helpers
 * ==================================================================== */

const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);
const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);
const lerp = (a, b, t) => a + (b - a) * t;
const isNum = (v) => typeof v === "number" && isFinite(v);

/** Frequency → mel. */
function toMel(hz) {
  return 2595 * Math.log10(1 + Math.max(0, hz) / MEL_HZ);
}

/** mel → frequency. */
function fromMel(m) {
  return MEL_HZ * (Math.pow(10, m / 2595) - 1);
}

/**
 * dB → ramp position ∈ [0,1].
 *
 * Linear mode maps the dB span linearly. Log mode maps it logarithmically:
 * u = log10(1 + u_linear) / log10(2), a curve that expands the quiet end of
 * the range (more colour resolution for faint material) while leaving the
 * loud end where it is, so the noise floor stays dark and signals stay bright.
 */
export function dbToUnit(db, minDb, maxDb, logScale) {
  const span = maxDb - minDb;
  if (!(span > 0)) return 0;
  let u = (db - minDb) / span;
  u = clamp01(u);
  if (logScale) u = Math.log10(1 + u) / Math.log10(2);
  return u;
}

/** Create a detached 2-D canvas (OffscreenCanvas when available). */
function makeCanvas(w, h) {
  if (typeof OffscreenCanvas === "function") {
    try {
      return new OffscreenCanvas(Math.max(1, w), Math.max(1, h));
    } catch (err) { /* fall through to a DOM canvas */ }
  }
  if (typeof document !== "undefined" && typeof document.createElement === "function") {
    const c = document.createElement("canvas");
    c.width = Math.max(1, w);
    c.height = Math.max(1, h);
    return c;
  }
  return null;
}

/* ==================================================================== *
 * wire-format normalisation
 * ==================================================================== */

/**
 * Turn the server payload into `{data: Float32Array, rows, cols}` where
 * `rows` are frequency bins and `cols` are time frames.
 */
export function normalizeMatrix(spectrogram, freqBins, frames) {
  const out = { data: null, rows: 0, cols: 0 };
  if (!spectrogram) return out;

  const fb = isNum(freqBins) ? freqBins | 0 : 0;
  const fr = isNum(frames) ? frames | 0 : 0;

  if (Array.isArray(spectrogram)) {
    if (spectrogram.length && Array.isArray(spectrogram[0])) {
      const rows = spectrogram.length;
      const cols = spectrogram[0].length;
      const data = new Float32Array(rows * cols);
      for (let r = 0; r < rows; r++) {
        const line = spectrogram[r] || [];
        for (let c = 0; c < cols; c++) {
          const v = line[c];
          data[r * cols + c] = typeof v === "number" && isFinite(v) ? v : -120;
        }
      }
      /* The server may send time-major data; detect and transpose. */
      if (fb > 0 && fr > 0 && rows === fr && cols === fb && !(rows === fb && cols === fr)) {
        const t = new Float32Array(rows * cols);
        for (let r = 0; r < rows; r++)
          for (let c = 0; c < cols; c++) t[c * rows + r] = data[r * cols + c];
        out.data = t; out.rows = cols; out.cols = rows;
        return out;
      }
      out.data = data; out.rows = rows; out.cols = cols;
      return out;
    }
    /* flat row-major matrix */
    const flat = spectrogram;
    const total = flat.length;
    if (!total) return out;
    let rows = fb || 0;
    let cols = fr || 0;
    if (!(rows > 0 && cols > 0 && rows * cols === total)) {
      rows = Math.max(1, Math.round(Math.sqrt(total)));
      cols = Math.max(1, Math.round(total / rows));
    }
    const data = new Float32Array(total);
    for (let i = 0; i < total; i++) {
      const v = flat[i];
      data[i] = typeof v === "number" && isFinite(v) ? v : -120;
    }
    out.data = data; out.rows = rows; out.cols = cols;
    return out;
  }
  return out;
}

/**
 * Turn the server payload into `{min: Float32Array, max: Float32Array, count}`.
 * Accepts [[min,max], …] pairs or a flat mono envelope.
 */
export function normalizeWaveform(waveform) {
  const out = { min: null, max: null, count: 0, peak: 0 };
  if (!waveform || !waveform.length) return out;
  const n = waveform.length;
  const min = new Float32Array(n);
  const max = new Float32Array(n);
  let peak = 0;
  if (Array.isArray(waveform[0])) {
    for (let i = 0; i < n; i++) {
      const pair = waveform[i] || [];
      const a = typeof pair[0] === "number" && isFinite(pair[0]) ? pair[0] : 0;
      const b = typeof pair[1] === "number" && isFinite(pair[1]) ? pair[1] : a;
      min[i] = Math.min(a, b);
      max[i] = Math.max(a, b);
      const m = Math.max(Math.abs(min[i]), Math.abs(max[i]));
      if (m > peak) peak = m;
    }
  } else {
    for (let i = 0; i < n; i++) {
      const v = typeof waveform[i] === "number" && isFinite(waveform[i]) ? waveform[i] : 0;
      min[i] = -Math.abs(v);
      max[i] = Math.abs(v);
      const m = Math.abs(v);
      if (m > peak) peak = m;
    }
  }
  out.min = min; out.max = max; out.count = n; out.peak = peak;
  return out;
}

/* ==================================================================== *
 * the rasteriser — pure, canvas-free, therefore testable in node
 * ==================================================================== */

/**
 * Rasterise a spectrogram matrix into an RGBA buffer.
 *
 * The vertical axis is the *display* frequency mapping (linear or mel-like);
 * the horizontal axis keeps the source time resolution (columns are
 * aggregated with a running maximum when the source is finer than the cache).
 *
 * Returns `{data: Uint8ClampedArray, width, height}`.
 */
export function rasterise(matrix, opts) {
  const destRows = Math.max(1, opts.destRows | 0);
  const destCols = Math.max(1, opts.destCols | 0);
  const data = matrix.data;
  const rows = matrix.rows;
  const cols = matrix.cols;
  const out = new Uint8ClampedArray(destRows * destCols * 4);
  if (!data || !rows || !cols) return { data: out, width: destCols, height: destRows };

  const lut = opts.lut;
  const invSpan = opts.maxDb > opts.minDb ? 1 / (opts.maxDb - opts.minDb) : 0;
  const logScale = !!opts.logScale;
  const logDen = Math.log10(2);
  const nyquist = opts.nyquist > 0 ? opts.nyquist : 1;

  /* vertical mapping: destination row → source bin */
  const rowI = new Int32Array(destRows);
  const rowF = new Float32Array(destRows);
  const mHi = toMel(opts.freqHi);
  const mLo = toMel(opts.freqLo);
  for (let y = 0; y < destRows; y++) {
    const v = destRows > 1 ? y / (destRows - 1) : 0;
    let f;
    if (opts.logFreq) f = fromMel(mHi - v * (mHi - mLo));
    else f = opts.freqHi - v * (opts.freqHi - opts.freqLo);
    let pos = (f / nyquist) * rows - 0.5;
    if (pos < 0) pos = 0;
    if (pos > rows - 1) pos = rows - 1;
    const i0 = Math.floor(pos);
    rowI[y] = i0;
    rowF[y] = pos - i0;
  }

  /* horizontal mapping: destination column → source column range */
  const step = cols / destCols;
  const s0 = new Int32Array(destCols);
  const s1 = new Int32Array(destCols);
  for (let x = 0; x < destCols; x++) {
    const a = Math.floor(x * step);
    let b = Math.ceil((x + 1) * step);
    if (b <= a) b = a + 1;
    if (a > cols - 1) a = cols - 1;
    if (b > cols) b = cols;
    if (b <= a) b = a + 1;
    s0[x] = a; s1[x] = b;
  }

  for (let y = 0; y < destRows; y++) {
    const r0 = rowI[y];
    const r1 = r0 + 1 < rows ? r0 + 1 : r0;
    const w = rowF[y];
    const blend = r1 !== r0 && w > 0;
    const b0 = r0 * cols;
    const b1 = r1 * cols;
    let o4 = y * destCols * 4;
    for (let x = 0; x < destCols; x++) {
      const from = s0[x];
      const to = s1[x];
      let db;
      if (to - from === 1) {
        const v = data[b0 + from];
        db = blend ? v + (data[b1 + from] - v) * w : v;
      } else {
        db = -Infinity;
        for (let s = from; s < to; s++) {
          let v = data[b0 + s];
          if (blend) {
            const v2 = data[b1 + s];
            v = v + (v2 - v) * w;
          }
          if (v > db) db = v;
        }
        if (db === -Infinity) db = opts.minDb;
      }
      let u = (db - opts.minDb) * invSpan;
      u = clamp01(u);
      if (logScale) u = Math.log10(1 + u) / logDen;
      const li = (u * 255 + 0.5) | 0;
      const l3 = li * 3;
      out[o4] = lut[l3];
      out[o4 + 1] = lut[l3 + 1];
      out[o4 + 2] = lut[l3 + 2];
      out[o4 + 3] = 255;
      o4 += 4;
    }
  }
  return { data: out, width: destCols, height: destRows };
}

/* ==================================================================== *
 * CompositeView
 * ==================================================================== */

/**
 * The composite stage renderer.
 *
 * @param {HTMLCanvasElement} specCanvas  the spectrogram canvas
 * @param {HTMLCanvasElement} waveCanvas  the waveform canvas
 * @param {HTMLElement}       wrapEl      the positioned container (both canvases live in it)
 */
export class CompositeView {
  constructor(specCanvas, waveCanvas, wrapEl) {
    if (!specCanvas || !waveCanvas) {
      throw new Error("CompositeView needs both a spectrogram and a waveform canvas");
    }
    this.specCanvas = specCanvas;
    this.waveCanvas = waveCanvas;
    this.wrapEl = wrapEl || specCanvas.parentElement || null;

    /* --- data ------------------------------------------------------- */
    this._matrix = null;          /* {data, rows, cols} — rows = freq bins */
    this._wave = null;            /* {min, max, count, peak}             */
    this.frames = 0;
    this.freqBins = 0;
    this.maxDb = 0;
    this.minDb = -120;
    this.sampleRate = 48000;
    this.duration = 0;

    /* --- view state ------------------------------------------------- */
    this._zoom = 1;
    this._scroll = 0;             /* seconds at the left edge              */
    this._mode = "composite";
    this._freqLo = 0;
    this._freqHi = 22050;
    this._freqExplicit = false;
    this._selection = null;       /* {t0, t1, f0, f1}                     */
    this._cursor = null;          /* {t, f}                              */
    this._logScale = false;       /* logarithmic dB → colour ramp         */
    this._logFreq = false;        /* mel-like vertical frequency axis     */
    this._logOverride = false;
    this._logFreqOverride = false;
    this._selFreq = false;        /* drag vertically → frequency selection */
    this._selTime = false;        /* drag horizontally → time selection   */
    this._selFreqOverride = false;
    this._selTimeOverride = false;

    /* --- caches ----------------------------------------------------- */
    this._ramp = [];
    this._lut = buildLut([]);
    this._palette = null;
    this._specCache = null;       /* {canvas, width, height}              */
    this._specKey = "";
    this._dataVersion = 0;

    /* --- interaction ------------------------------------------------ */
    this._pointerCb = null;
    this._drag = null;
    this._raf = 0;
    this._dirty = true;
    this._layout = { w: 0, h: 0, specH: 0, waveTop: 0, waveH: 0 };
    this._waveScale = 1;

    this._refreshTokens();
    this._bindDom();
    this._readToggles();
    this._layoutCanvases();
    this._observeResize();
  }

  /* ---------------------------------------------------------------- *
   * public API
   * ---------------------------------------------------------------- */

  /**
   * Install the analysis payload.
   * @param {object} data {waveform, spectrogram, freq_bins, frames, max_db, min_db, sample_rate, duration}
   */
  setData(data) {
    const d = data || {};
    const prevDuration = this.duration;

    this.maxDb = isNum(d.max_db) ? d.max_db : 0;
    this.minDb = isNum(d.min_db) ? d.min_db : -120;

    // The server may quantise the spectrogram to uint8 and publish the
    // scale alongside it.  Decode it back to dB here so the rest of the
    // renderer only ever deals with decibels.
    let matrix = d.spectrogram;
    const encoding = String(d.spectrogram_encoding || "").toLowerCase();
    if (encoding === "uint8" || encoding === "u8") {
      const span = this.maxDb - this.minDb;
      const decode = (v) => this.minDb + (v / 255) * span;
      matrix = Array.isArray(matrix)
        ? matrix.map((row) => row.map((v) => decode(v)))
        : matrix;
    }

    this._matrix = normalizeMatrix(matrix, d.freq_bins, d.frames);
    this._wave = normalizeWaveform(d.waveform);
    this.frames = isNum(d.frames) ? d.frames | 0 : this._matrix.cols;
    this.freqBins = isNum(d.freq_bins) ? d.freq_bins | 0 : this._matrix.rows;
    this.sampleRate = isNum(d.sample_rate) && d.sample_rate > 0 ? d.sample_rate : 48000;
    this.duration = isNum(d.duration) && d.duration > 0 ? d.duration : 0;
    this._waveScale = this._wave && this._wave.peak > 0.02 ? 1 / this._wave.peak : 1;

    if (!this._freqExplicit) {
      const nyq = this.sampleRate / 2;
      this._freqLo = 0;
      this._freqHi = Math.min(nyq || 22050, 22050);
    }

    if (Math.abs(prevDuration - this.duration) > 1e-6) {
      this._zoom = 1;
      this._scroll = 0;
      this._selection = null;
      this._cursor = null;
    }
    this._dataVersion++;
    this._specKey = "";
    this._clampScroll();
    this._schedule();
  }

  /** Zoom factor; 1 = the whole file fits the view. */
  get zoom() {
    return this._zoom;
  }

  /** @param {number} z zoom factor, clamped to [1, 128] */
  setZoom(z) {
    const v = Number(z);
    if (!isFinite(v) || v <= 0) return;
    const nz = clamp(v, MIN_ZOOM, MAX_ZOOM);
    if (Math.abs(nz - this._zoom) < 1e-9) return;
    this._zoom = nz;
    this._clampScroll();
    this._schedule();
  }

  /** Left edge of the visible time window, in seconds. */
  get scroll() {
    return this._scroll;
  }

  /** @param {number} t0 seconds at the left edge */
  setScroll(t0) {
    const v = Number(t0);
    if (!isFinite(v)) return;
    this._scroll = v;
    this._clampScroll();
    this._schedule();
  }

  /** @param {"composite"|"waveform"|"spectrogram"} mode */
  setViewMode(mode) {
    const m = String(mode || "composite");
    if (m !== "composite" && m !== "waveform" && m !== "spectrogram") return;
    if (m === this._mode) return;
    this._mode = m;
    this._layoutCanvases();
    this._schedule();
  }

  /** Current view mode. */
  get viewMode() {
    return this._mode;
  }

  /**
   * Restrict the vertical axis to a frequency band.
   * @param {number} loHz lower edge
   * @param {number} hiHz upper edge
   */
  setFrequencyRange(loHz, hiHz) {
    const nyq = this.sampleRate / 2 || 22050;
    let lo = isNum(loHz) ? loHz : this._freqLo;
    let hi = isNum(hiHz) ? hiHz : this._freqHi;
    lo = clamp(lo, 0, nyq);
    hi = clamp(hi, 0, nyq);
    if (hi < lo) { const t = lo; lo = hi; hi = t; }
    if (hi - lo < 1) hi = Math.min(nyq, lo + 1);
    if (lo === this._freqLo && hi === this._freqHi) return;
    this._freqLo = lo;
    this._freqHi = hi;
    this._freqExplicit = true;
    this._schedule();
  }

  /** Currently displayed frequency band. */
  get frequencyRange() {
    return { lo: this._freqLo, hi: this._freqHi };
  }

  /**
   * Set the selection. `t0/t1` are seconds, `f0/f1` are hertz; either pair may
   * be null (time-only or frequency-only selection).
   */
  setSelection(sel) {
    if (!sel) return this.clearSelection();
    const pick = (a, b) => {
      const x = isNum(a) ? a : null;
      const y = isNum(b) ? b : null;
      if (x === null && y === null) return null;
      if (x === null) return [y, y];
      if (y === null) return [x, x];
      return x <= y ? [x, y] : [y, x];
    };
    const t = pick(sel.t0, sel.t1);
    const f = pick(sel.f0, sel.f1);
    this._selection = {
      t0: t ? t[0] : null,
      t1: t ? t[1] : null,
      f0: f ? f[0] : null,
      f1: f ? f[1] : null,
    };
    this._schedule();
  }

  /** Remove the selection. */
  clearSelection() {
    if (!this._selection) return;
    this._selection = null;
    this._schedule();
  }

  /** The current selection (a copy) or null. */
  get selection() {
    return this._selection ? { ...this._selection } : null;
  }

  /**
   * Register the pointer callback.
   * @param {(e:{type:"click"|"drag"|"hover",t:number,f:number|null,x:number,y:number})=>void} cb
   */
  onPointer(cb) {
    this._pointerCb = typeof cb === "function" ? cb : null;
    return this;
  }

  /** Logarithmic dB → colour ramp mapping. */
  setLogScale(on) {
    const v = !!on;
    this._logOverride = true;
    if (v === this._logScale) return;
    this._logScale = v;
    this._schedule();
  }

  /** Mel-like (logarithmic) vertical frequency axis. */
  setLogFrequency(on) {
    const v = !!on;
    this._logFreqOverride = true;
    if (v === this._logFreq) return;
    this._logFreq = v;
    this._schedule();
  }

  /** Enable / disable the drag-to-select behaviours. */
  setSelectionModes(modes) {
    const m = modes || {};
    if ("freq" in m) { this._selFreqOverride = true; this._selFreq = !!m.freq; }
    if ("time" in m) { this._selTimeOverride = true; this._selTime = !!m.time; }
    this._schedule();
  }

  /** Re-read the colour ramp and the palette from the CSS tokens. */
  refreshTokens() {
    this._refreshTokens();
    this._schedule();
  }

  /** Draw immediately (the scheduled path coalesces into one animation frame). */
  render() {
    this._dirty = false;
    this._draw();
  }

  /* ---------------------------------------------------------------- *
   * tokens / palette
   * ---------------------------------------------------------------- */

  _refreshTokens() {
    const doc = typeof document !== "undefined" ? document : null;
    const root = doc ? doc.documentElement : null;
    const source = root || this.wrapEl;
    this._ramp = resolveRamp(rampFromTokens(source));
    this._lut = buildLut(this._ramp);
    this._palette = this._readPalette(source);
  }

  _readPalette(node) {
    if (!node || typeof getComputedStyle !== "function") return null;
    let cs = null;
    try { cs = getComputedStyle(node); } catch (err) { return null; }
    const g = (name) => {
      const v = cs.getPropertyValue(name);
      return v ? String(v).trim() : "";
    };
    const mono = g("--font-mono");
    const fsXs = parseFloat(g("--fs-xs"));
    return {
      bg0: parseColor(g("--bg-0")),
      bg1: parseColor(g("--bg-1")),
      bg2: parseColor(g("--bg-2")),
      line: parseColor(g("--line")),
      lineSoft: parseColor(g("--line-soft")),
      txt0: parseColor(g("--txt-0")),
      txt1: parseColor(g("--txt-1")),
      txt2: parseColor(g("--txt-2")),
      accent: parseColor(g("--accent")),
      accentDim: parseColor(g("--accent-dim")),
      accent2: parseColor(g("--accent-2")),
      accent3: parseColor(g("--accent-3")),
      sel: parseColor(g("--sel")),
      selEdge: parseColor(g("--sel-edge")),
      fontMono: mono || "monospace",
      fontUi: g("--font-ui") || "system-ui, sans-serif",
      fontXs: isFinite(fsXs) && fsXs > 0 ? fsXs : 10,
    };
  }

  /* ---------------------------------------------------------------- *
   * DOM wiring
   * ---------------------------------------------------------------- */

  _bindDom() {
    const wrap = this.wrapEl;
    if (!wrap) return;
    this._onWheel = (ev) => this._handleWheel(ev);
    this._onPointerDown = (ev) => this._handlePointerDown(ev);
    this._onPointerMove = (ev) => this._handlePointerMove(ev);
    this._onPointerUp = (ev) => this._handlePointerUp(ev);
    this._onPointerLeave = () => { if (!this._drag) this._emit("hover", null, null, -1, -1); };

    wrap.addEventListener("wheel", this._onWheel, { passive: false });
    wrap.addEventListener("pointerdown", this._onPointerDown);
    wrap.addEventListener("pointermove", this._onPointerMove);
    wrap.addEventListener("pointerup", this._onPointerUp);
    wrap.addEventListener("pointercancel", this._onPointerUp);
    wrap.addEventListener("pointerleave", this._onPointerLeave);
    wrap.addEventListener("contextmenu", (ev) => { if (this._drag) ev.preventDefault(); });

    if (typeof window !== "undefined") {
      this._onResize = () => { this._layoutCanvases(); this._schedule(); };
      window.addEventListener("resize", this._onResize);
    }
  }

  _observeResize() {
    if (typeof ResizeObserver !== "function" || !this.wrapEl) return;
    this._ro = new ResizeObserver(() => {
      this._layoutCanvases();
      this._schedule();
    });
    this._ro.observe(this.wrapEl);
  }

  /** Read the toolbar checkboxes that drive the log axis and the selection tools. */
  _readToggles() {
    const doc = typeof document !== "undefined" ? document : null;
    if (!doc) return;
    const bind = (box, apply) => {
      if (!box || box._aurBound) return;
      box._aurBound = true;
      box.addEventListener("change", () => {
        apply();
        this._schedule();
      });
    };
    const logBox = doc.getElementById("chk-log");
    if (logBox) {
      const on = !!logBox.checked;
      if (!this._logOverride) this._logScale = on;
      if (!this._logFreqOverride) this._logFreq = on;
      bind(logBox, () => this._readToggles());
    }
    const fBox = doc.getElementById("chk-sel-freq");
    if (fBox) {
      if (!this._selFreqOverride) this._selFreq = !!fBox.checked;
      bind(fBox, () => this._readToggles());
    }
    const tBox = doc.getElementById("chk-sel-time");
    if (tBox) {
      if (!this._selTimeOverride) this._selTime = !!tBox.checked;
      bind(tBox, () => this._readToggles());
    }
  }

  /* ---------------------------------------------------------------- *
   * layout
   * ---------------------------------------------------------------- */

  _layoutCanvases() {
    const wrap = this.wrapEl;
    const mode = this._mode;

    let w = 0;
    let h = 0;
    if (wrap) {
      const r = wrap.getBoundingClientRect();
      w = Math.floor(r.width);
      h = Math.floor(r.height);
      if (wrap.style && !wrap.style.position) {
        let pos = null;
        try { pos = getComputedStyle(wrap).position; } catch (err) { pos = null; }
        if (!pos || pos === "static") wrap.style.position = "relative";
      }
    }
    if (!(w > 0)) w = Math.floor(this.specCanvas.getBoundingClientRect().width) || 960;
    if (!(h > 0)) {
      if (wrap && wrap.style) wrap.style.minHeight = "320px";
      h = 480;
    }

    let specH = 0;
    let waveH = 0;
    let waveTop = 0;
    if (mode === "waveform") {
      waveH = h;
    } else if (mode === "spectrogram") {
      specH = h;
    } else {
      specH = Math.max(1, Math.round(h * 0.64));
      waveTop = specH + 1;
      waveH = Math.max(0, h - waveTop);
    }

    this._layout = { w, h, specH, waveTop, waveH };

    this._placeCanvas(this.specCanvas, 0, specH, mode === "waveform");
    this._placeCanvas(this.waveCanvas, waveTop, waveH, mode === "spectrogram");
  }

  _placeCanvas(canvas, top, height, hidden) {
    if (hidden) {
      if (canvas.style.display !== "none") {
        canvas._aurPrevDisplay = canvas.style.display;
        canvas.style.display = "none";
      }
      return;
    }
    if (canvas._aurPrevDisplay !== undefined) {
      canvas.style.display = canvas._aurPrevDisplay;
      canvas._aurPrevDisplay = undefined;
    } else if (canvas.style.display === "none") {
      canvas.style.display = "";
    }
    const ratio = dpr();
    const pw = Math.max(1, Math.round(this._layout.w * ratio));
    const ph = Math.max(1, Math.round(height * ratio));
    if (canvas.width !== pw) canvas.width = pw;
    if (canvas.height !== ph) canvas.height = ph;
    canvas.style.position = "absolute";
    canvas.style.left = "0px";
    canvas.style.top = `${top}px`;
    canvas.style.width = `${this._layout.w}px`;
    canvas.style.height = `${height}px`;
  }

  /* ---------------------------------------------------------------- *
   * scheduling
   * ---------------------------------------------------------------- */

  _schedule() {
    this._dirty = true;
    if (this._raf) return;
    const raf = typeof requestAnimationFrame === "function"
      ? (cb) => requestAnimationFrame(cb)
      : (cb) => setTimeout(cb, 16);
    this._raf = raf(() => {
      this._raf = 0;
      if (this._dirty) this.render();
    });
  }

  /* ---------------------------------------------------------------- *
   * time / frequency <-> pixel mapping
   * ---------------------------------------------------------------- */

  get _visible() {
    return this._span / this._zoom;
  }

  /**
   * Length of the time axis. Normally the file duration in seconds; when the
   * server did not report one the frame count is used so that the view still
   * renders (the pointer then reports time in frames).
   */
  get _span() {
    return this.duration > 0 ? this.duration : Math.max(1, this.frames);
  }

  _clampScroll() {
    const max = Math.max(0, this._span - this._visible);
    this._scroll = clamp(this._scroll, 0, max);
  }

  _timeToX(t) {
    const vis = this._visible;
    if (!(vis > 0)) return 0;
    return ((t - this._scroll) / vis) * this._layout.w;
  }

  _xToTime(x) {
    const vis = this._visible;
    if (!(vis > 0)) return 0;
    return this._scroll + (clamp(x, 0, this._layout.w) / this._layout.w) * vis;
  }

  /** Vertical fraction (0 = top) → frequency, honouring the log/linear axis. */
  _displayFreq(v) {
    const lo = this._freqLo;
    const hi = this._freqHi;
    if (this._logFreq) {
      const mHi = toMel(hi);
      const mLo = toMel(lo);
      return fromMel(mHi - clamp01(v) * (mHi - mLo));
    }
    return hi - clamp01(v) * (hi - lo);
  }

  /** Frequency → vertical fraction (0 = top). */
  _displayV(f) {
    const lo = this._freqLo;
    const hi = this._freqHi;
    if (!(hi > lo)) return 0;
    if (f >= hi) return 0;
    if (f <= lo) return 1;
    if (this._logFreq) {
      const mHi = toMel(hi);
      const mLo = toMel(lo);
      return (mHi - toMel(f)) / (mHi - mLo);
    }
    return (hi - f) / (hi - lo);
  }

  _freqToY(f) {
    return this._displayV(f) * this._layout.specH;
  }

  /* ---------------------------------------------------------------- *
   * drawing
   * ---------------------------------------------------------------- */

  _draw() {
    const pal = this._palette;
    if (!pal) return;
    const specCtx = this.specCanvas.getContext("2d");
    const waveCtx = this.waveCanvas.getContext("2d");
    if (!specCtx || !waveCtx) return;

    const ratio = dpr();
    const L = this._layout;

    /* ---------------- spectrogram ---------------- */
    if (L.specH > 0) {
      specCtx.setTransform(ratio, 0, 0, ratio, 0, 0);
      specCtx.clearRect(0, 0, L.w, L.specH);
      if (pal.bg0) {
        specCtx.fillStyle = rgba(pal.bg0, 1);
        specCtx.fillRect(0, 0, L.w, L.specH);
      }
      const drew = this._drawSpectrogram(specCtx);
      this._drawFreqGrid(specCtx);
      this._drawTimeGrid(specCtx, L.specH, false);
      this._drawSelection(specCtx, L.specH, true);
      this._drawCursor(specCtx, L.specH, true);
      this._drawDbScale(specCtx);
      if (!drew) this._drawEmptyState(specCtx, L.specH);
    }

    /* ---------------- waveform ---------------- */
    if (L.waveH > 0) {
      waveCtx.setTransform(ratio, 0, 0, ratio, 0, 0);
      waveCtx.clearRect(0, 0, L.w, L.waveH);
      if (pal.bg1) {
        waveCtx.fillStyle = rgba(pal.bg1, 1);
        waveCtx.fillRect(0, 0, L.w, L.waveH);
      }
      const drew = this._drawWaveform(waveCtx);
      this._drawTimeGrid(waveCtx, L.waveH, true);
      this._drawSelection(waveCtx, L.waveH, false);
      this._drawCursor(waveCtx, L.waveH, false);
      /* the hint is shown once, on the upper canvas when both are visible */
      if (!drew && L.specH <= 0) this._drawEmptyState(waveCtx, L.waveH);
    }
  }

  /** Blit the cached raster; returns false when there is nothing to show. */
  _drawSpectrogram(ctx) {
    const m = this._matrix;
    const L = this._layout;
    if (!m || !m.data || !m.cols || !this._span) return false;

    const destRows = Math.max(1, Math.round(L.specH * dpr()));
    const key = [
      this._dataVersion, this._freqLo.toFixed(2), this._freqHi.toFixed(2),
      this._logFreq ? 1 : 0, this._logScale ? 1 : 0, this.minDb, this.maxDb,
      destRows, m.cols, m.rows,
    ].join("|");

    if (!this._specCache || this._specKey !== key) {
      const destCols = Math.min(MAX_CACHE_COLS, m.cols);
      const raster = rasterise(m, {
        destRows,
        destCols,
        freqLo: this._freqLo,
        freqHi: this._freqHi,
        nyquist: this.sampleRate / 2,
        logFreq: this._logFreq,
        minDb: this.minDb,
        maxDb: this.maxDb,
        logScale: this._logScale,
        lut: this._lut,
      });
      const canvas = makeCanvas(raster.width, raster.height);
      if (!canvas) return false;
      const cctx = canvas.getContext("2d");
      if (!cctx) return false;
      const img = this._toImageData(cctx, raster.data, raster.width, raster.height);
      if (!img) return false;
      cctx.putImageData(img, 0, 0);
      this._specCache = { canvas, width: raster.width, height: raster.height };
      this._specKey = key;
    }

    const cache = this._specCache;
    const vis = this._visible;
    if (!(vis > 0)) return false;
    const c0 = (this._scroll / this._span) * cache.width;
    const c1 = ((this._scroll + vis) / this._span) * cache.width;
    const sw = Math.max(1, c1 - c0);
    ctx.imageSmoothingEnabled = true;
    if ("imageSmoothingQuality" in ctx) ctx.imageSmoothingQuality = "high";
    ctx.drawImage(cache.canvas, c0, 0, sw, cache.height, 0, 0, L.w, L.specH);
    return true;
  }

  _toImageData(ctx, data, w, h) {
    if (typeof ImageData === "function") {
      try { return new ImageData(data, w, h); } catch (err) { /* fall through */ }
    }
    if (ctx && typeof ctx.createImageData === "function") {
      const img = ctx.createImageData(w, h);
      img.data.set(data);
      return img;
    }
    return null;
  }

  /** Draw the waveform envelope; returns false when there is nothing to show. */
  _drawWaveform(ctx) {
    const w = this._wave;
    const L = this._layout;
    if (!w || !w.count || !this._span || L.waveH <= 0) return false;
    const pal = this._palette;

    const width = L.w;
    const height = L.waveH;
    const mid = height / 2;
    const scale = (height / 2) * 0.92 * this._waveScale;

    const top = new Float32Array(width);
    const bot = new Float32Array(width);
    const vis = this._visible;
    const perSec = w.count / this._span;

    for (let x = 0; x < width; x++) {
      const ta = (this._scroll + (x / width) * vis) * perSec;
      const tb = (this._scroll + ((x + 1) / width) * vis) * perSec;
      let i0 = Math.floor(ta);
      let i1 = Math.ceil(tb);
      if (i1 <= i0) i1 = i0 + 1;
      if (i0 < 0) i0 = 0;
      if (i1 > w.count) i1 = w.count;
      if (i1 <= i0) { i0 = Math.min(i0, w.count - 1); i1 = i0 + 1; }
      let mn = Infinity;
      let mx = -Infinity;
      for (let i = i0; i < i1; i++) {
        const a = w.min[i];
        const b = w.max[i];
        if (a < mn) mn = a;
        if (b > mx) mx = b;
      }
      if (i1 - i0 === 1 && i1 < w.count && tb > ta) {
        const f = tb - ta;
        const a = w.min[i1];
        const b = w.max[i1];
        mn = mn + (a - mn) * f;
        mx = mx + (b - mx) * f;
      }
      if (!isFinite(mn)) mn = 0;
      if (!isFinite(mx)) mx = 0;
      top[x] = mid - mx * scale;
      bot[x] = mid - mn * scale;
    }

    /* envelope */
    ctx.beginPath();
    ctx.moveTo(0, top[0]);
    for (let x = 1; x < width; x++) ctx.lineTo(x, top[x]);
    for (let x = width - 1; x >= 0; x--) ctx.lineTo(x, bot[x]);
    ctx.closePath();
    if (pal.accent) {
      ctx.fillStyle = rgba(pal.accent, 0.52);
      ctx.fill();
    }

    /* centre line */
    ctx.beginPath();
    ctx.moveTo(0, Math.round(mid) + 0.5);
    ctx.lineTo(width, Math.round(mid) + 0.5);
    ctx.strokeStyle = pal.txt0 ? rgba(pal.txt0, 0.32) : (pal.accent ? rgba(pal.accent, 0.9) : null);
    if (ctx.strokeStyle) {
      ctx.lineWidth = 1;
      ctx.stroke();
    }

    /* top divider between the two canvases */
    if (this._mode === "composite" && pal.line) {
      ctx.beginPath();
      ctx.moveTo(0, 0.5);
      ctx.lineTo(width, 0.5);
      ctx.strokeStyle = rgba(pal.line, 1);
      ctx.lineWidth = 1;
      ctx.stroke();
    }
    return true;
  }

  /**
   * Draw a small axis label.
   *
   * The labels sit on top of the spectrogram, which can be anything from the
   * black floor to the bright top of the colour ramp, so the text gets a
   * translucent panel behind it rather than a one pixel shadow.  That keeps
   * the frequency ticks and the decibel legend readable over any content.
   */
  _label(ctx, text, x, y, colour) {
    const pal = this._palette;
    if (pal.bg0) {
      const w = ctx.measureText(text).width;
      const h = pal.fontXs + 3;
      const top = y - h * 0.5;
      ctx.fillStyle = rgba(pal.bg0, 0.72);
      ctx.fillRect(x - 2, top, w + 4, h);
    }
    ctx.fillStyle = colour ? rgba(colour, 1) : null;
    if (ctx.fillStyle) ctx.fillText(text, x, y);
  }

  _drawFreqGrid(ctx) {
    const L = this._layout;
    const pal = this._palette;
    if (L.specH <= 0) return;
    ctx.save();
    ctx.font = `${pal.fontXs}px ${pal.fontMono}`;
    ctx.textBaseline = "middle";
    let lastY = -99;
    /* the axis is inverted (high frequencies on top), so walk the ticks from
       the top down — otherwise the label de-clutter guard hides every label
       below the first one. */
    for (let i = FREQ_TICKS.length - 1; i >= 0; i--) {
      const f = FREQ_TICKS[i];
      if (f < this._freqLo || f > this._freqHi) continue;
      const y = Math.round(this._freqToY(f)) + 0.5;
      if (y < 0 || y > L.specH) continue;
      const major = FREQ_MAJORS.has(f);
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(L.w, y);
      ctx.strokeStyle = major
        ? (pal.line ? rgba(pal.line, 0.72) : null)
        : (pal.lineSoft ? rgba(pal.lineSoft, 0.5) : null);
      ctx.lineWidth = 1;
      if (ctx.strokeStyle) ctx.stroke();

      if (major && y - lastY > pal.fontXs + 3) {
        lastY = y;
        const label = f >= 1000 ? `${f / 1000}k` : `${f}`;
        this._label(ctx, label, 4, y - pal.fontXs * 0.75, pal.txt1);
      }
    }
    ctx.restore();
  }

  _drawTimeGrid(ctx, height, withLabels) {
    const L = this._layout;
    const pal = this._palette;
    if (height <= 0) return;
    const step = this._timeStep();
    const vis = this._visible;
    const first = Math.ceil(this._scroll / step) * step;
    ctx.save();
    ctx.font = `${pal.fontXs}px ${pal.fontMono}`;
    ctx.textBaseline = "alphabetic";
    for (let t = first; t <= this._scroll + vis + 1e-9; t += step) {
      const x = Math.round(this._timeToX(t)) + 0.5;
      if (x < 0 || x > L.w) continue;
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, height);
      ctx.strokeStyle = pal.lineSoft ? rgba(pal.lineSoft, 0.5) : null;
      ctx.lineWidth = 1;
      if (ctx.strokeStyle) ctx.stroke();
      if (withLabels) {
        this._label(ctx, this._formatTick(t, step), x + 3, height - 4, pal.txt2);
      }
    }
    ctx.restore();
  }

  _timeStep() {
    const vis = this._visible;
    if (!(vis > 0)) return 1;
    const raw = vis / 9;
    for (const s of TIME_STEPS) {
      if (s >= raw) return s;
    }
    return TIME_STEPS[TIME_STEPS.length - 1];
  }

  _formatTick(t, step) {
    if (step >= 1) {
      const m = Math.floor(t / 60);
      const s = Math.floor(t - m * 60);
      return `${m}:${String(s).padStart(2, "0")}`;
    }
    if (step >= 0.1) return `${t.toFixed(2)} s`;
    return `${t.toFixed(3)} s`;
  }

  _drawSelection(ctx, height, withFreq) {
    const sel = this._selection;
    const L = this._layout;
    const pal = this._palette;
    if (!sel) return;

    if (sel.t0 !== null && sel.t1 !== null && L.w > 0) {
      const x0 = this._timeToX(sel.t0);
      const x1 = this._timeToX(sel.t1);
      const w = Math.max(1, x1 - x0);
      ctx.fillStyle = pal.sel ? rgba(pal.sel, 0.35) : null;
      if (ctx.fillStyle) ctx.fillRect(x0, 0, w, height);
      ctx.strokeStyle = pal.selEdge ? rgba(pal.selEdge, 0.9) : null;
      if (ctx.strokeStyle) {
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(Math.round(x0) + 0.5, 0); ctx.lineTo(Math.round(x0) + 0.5, height);
        ctx.moveTo(Math.round(x1) + 0.5, 0); ctx.lineTo(Math.round(x1) + 0.5, height);
        ctx.stroke();
      }
    }

    if (withFreq && sel.f0 !== null && sel.f1 !== null && height > 0) {
      const y0 = this._freqToY(sel.f1);
      const y1 = this._freqToY(sel.f0);
      const h = Math.max(1, y1 - y0);
      ctx.fillStyle = pal.sel ? rgba(pal.sel, 0.28) : null;
      if (ctx.fillStyle) ctx.fillRect(0, y0, L.w, h);
      ctx.strokeStyle = pal.selEdge ? rgba(pal.selEdge, 0.85) : null;
      if (ctx.strokeStyle) {
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(0, Math.round(y0) + 0.5); ctx.lineTo(L.w, Math.round(y0) + 0.5);
        ctx.moveTo(0, Math.round(y1) + 0.5); ctx.lineTo(L.w, Math.round(y1) + 0.5);
        ctx.stroke();
      }
    }
  }

  _drawCursor(ctx, height, withFreq) {
    const cur = this._cursor;
    const L = this._layout;
    const pal = this._palette;
    if (!cur) return;
    const colour = pal.accent2 || pal.accent;
    if (!colour) return;
    ctx.strokeStyle = rgba(colour, 0.85);
    ctx.lineWidth = 1;
    if (isNum(cur.t)) {
      const x = Math.round(this._timeToX(cur.t)) + 0.5;
      if (x >= 0 && x <= L.w) {
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, height);
        ctx.stroke();
      }
    }
    if (withFreq && isNum(cur.f) && this._layout.specH > 0) {
      const y = Math.round(this._freqToY(cur.f)) + 0.5;
      if (y >= 0 && y <= this._layout.specH) {
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(L.w, y);
        ctx.stroke();
      }
    }
  }

  /** The little dB scale bar in the top-right corner of the spectrogram. */
  _drawDbScale(ctx) {
    const L = this._layout;
    const pal = this._palette;
    if (!this._ramp.length || L.specH <= 0) return;
    const w = Math.min(132, Math.max(64, Math.round(L.w * 0.12)));
    const h = 6;
    const x = L.w - w - 8;
    const y = 8;

    /* a panel behind the whole legend so it reads as an overlay widget
       instead of fighting with the spectrogram underneath */
    ctx.save();
    if (pal.bg1) {
      ctx.fillStyle = rgba(pal.bg1, 0.82);
      ctx.fillRect(x - 8, y - 6, w + 16, h + 20);
    }
    if (pal.line) {
      ctx.strokeStyle = rgba(pal.line, 0.55);
      ctx.lineWidth = 1;
      ctx.strokeRect(x - 7.5, y - 5.5, w + 15, h + 19);
    }

    for (let i = 0; i < w; i++) {
      const c = sampleRamp(this._ramp, i / (w - 1));
      ctx.fillStyle = `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
      ctx.fillRect(x + i, y, 1, h);
    }
    ctx.strokeStyle = pal.line ? rgba(pal.line, 0.9) : null;
    if (ctx.strokeStyle) {
      ctx.lineWidth = 1;
      ctx.strokeRect(x + 0.5, y + 0.5, w - 1, h - 1);
    }
    ctx.font = `${pal.fontXs}px ${pal.fontMono}`;
    ctx.textBaseline = "alphabetic";
    this._label(ctx, `${this.minDb.toFixed(0)} dB`, x, y + h + 9, pal.txt2);
    const maxLabel = `${this.maxDb.toFixed(0)} dB`;
    this._label(ctx, maxLabel, x + w - ctx.measureText(maxLabel).width, y + h + 9, pal.txt2);
    ctx.restore();
  }

  _drawEmptyState(ctx, height) {
    const L = this._layout;
    const pal = this._palette;
    ctx.save();
    ctx.font = `${pal.fontXs + 2}px ${pal.fontUi}`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillStyle = pal.txt2 ? rgba(pal.txt2, 0.9) : null;
    if (ctx.fillStyle) ctx.fillText(t("view.empty", "Load an audio file to begin"), L.w / 2, height / 2);
    ctx.restore();
  }

  /* ---------------------------------------------------------------- *
   * interaction
   * ---------------------------------------------------------------- */

  _handleWheel(ev) {
    if (!this._span) return;
    if (typeof ev.preventDefault === "function") ev.preventDefault();
    const dir = ev.deltaMode === 1 ? ev.deltaY * 16 : ev.deltaY;
    const factor = Math.pow(1.0016, -dir);
    const nz = clamp(this._zoom * factor, MIN_ZOOM, MAX_ZOOM);
    if (Math.abs(nz - this._zoom) < 1e-9) return;

    const hit = this._hitTest(ev.clientX, ev.clientY);
    const frac = clamp01(this._layout.w > 0 ? hit.x / this._layout.w : 0.5);
    const tAt = this._scroll + frac * this._visible;
    this._zoom = nz;
    this._scroll = tAt - frac * this._visible;
    this._clampScroll();
    this._schedule();
  }

  _hitTest(clientX, clientY) {
    const L = this._layout;
    const wrap = this.wrapEl;
    let ox = 0;
    let oy = 0;
    if (wrap) {
      const r = wrap.getBoundingClientRect();
      ox = r.left + (wrap.clientLeft || 0);
      oy = r.top + (wrap.clientTop || 0);
    }
    const x = clientX - ox;
    const y = clientY - oy;
    let region = "gap";
    if (L.specH > 0 && y >= 0 && y < L.specH) region = "spectrogram";
    else if (L.waveH > 0 && y >= L.waveTop && y < L.waveTop + L.waveH) region = "waveform";
    return { x, y, region };
  }

  _handlePointerDown(ev) {
    if (ev.button !== 0 && ev.button !== 1) return;
    const hit = this._hitTest(ev.clientX, ev.clientY);
    if (hit.region === "gap") return;
    const wrap = this.wrapEl;
    if (wrap && wrap.setPointerCapture && ev.pointerId !== undefined) {
      try { wrap.setPointerCapture(ev.pointerId); } catch (err) { /* not fatal */ }
    }
    const t = this._xToTime(hit.x);
    const f = hit.region === "spectrogram" ? this._displayFreq(hit.y / Math.max(1, this._layout.specH)) : null;

    /* Which gesture? A selection tool wins; if both are armed the dominant
       axis decides once the drag leaves the dead zone; otherwise the drag
       pans the view. */
    let mode = "pan";
    if (this._selFreq && this._selTime) mode = "auto";
    else if (this._selFreq) mode = "freq";
    else if (this._selTime) mode = "time";

    this._drag = {
      mode,
      x0: hit.x,
      y0: hit.y,
      t0: t,
      f0: f,
      moved: false,
      pointerId: ev.pointerId,
      shift: !!ev.shiftKey,
      alt: !!ev.altKey,
    };
    if (typeof ev.preventDefault === "function") ev.preventDefault();
  }

  _handlePointerMove(ev) {
    const hit = this._hitTest(ev.clientX, ev.clientY);
    const drag = this._drag;
    if (!drag) {
      if (hit.region !== "gap") {
        const t = this._xToTime(hit.x);
        const f = hit.region === "spectrogram"
          ? this._displayFreq(hit.y / Math.max(1, this._layout.specH))
          : null;
        this._emit("hover", t, f, hit.x, hit.y);
      }
      return;
    }

    const dx = hit.x - drag.x0;
    const dy = hit.y - drag.y0;
    if (!drag.moved) {
      if (Math.abs(dx) < 3 && Math.abs(dy) < 3) return;   /* still a click */
      drag.moved = true;
      if (drag.mode === "auto") {
        drag.mode = Math.abs(dx) >= Math.abs(dy) ? "time" : "freq";
      }
    }

    const t = this._xToTime(hit.x);
    const f = hit.region === "spectrogram"
      ? this._displayFreq(hit.y / Math.max(1, this._layout.specH))
      : (drag.f0 !== null ? drag.f0 : null);

    if (drag.mode === "pan") {
      const vis = this._visible;
      if (vis > 0 && this._layout.w > 0) {
        this._scroll = drag.t0 - (hit.x / this._layout.w) * vis;
        this._clampScroll();
      }
    } else if (drag.mode === "time") {
      this.setSelection({ t0: drag.t0, t1: t });
    } else if (drag.mode === "freq") {
      this.setSelection({ f0: drag.f0, f1: f });
    }

    this._emit("drag", t, f, hit.x, hit.y);
  }

  _handlePointerUp(ev) {
    const drag = this._drag;
    if (!drag) return;
    this._drag = null;
    const wrap = this.wrapEl;
    if (wrap && wrap.releasePointerCapture && drag.pointerId !== undefined) {
      try { wrap.releasePointerCapture(drag.pointerId); } catch (err) { /* not fatal */ }
    }
    const hit = this._hitTest(ev.clientX, ev.clientY);
    const t = this._xToTime(hit.x);
    const f = hit.region === "spectrogram"
      ? this._displayFreq(hit.y / Math.max(1, this._layout.specH))
      : null;

    if (!drag.moved) {
      this._cursor = { t, f };
      this._emit("click", t, f, hit.x, hit.y);
      this._schedule();
    } else {
      this._emit("drag", t, f, hit.x, hit.y);
    }
  }

  _emit(type, t, f, x, y) {
    if (!this._pointerCb) return;
    const payload = {
      type,
      t: isNum(t) ? t : null,
      f: isNum(f) ? f : null,
      x: isNum(x) ? x : null,
      y: isNum(y) ? y : null,
      zoom: this._zoom,
      selection: this._selection ? { ...this._selection } : null,
    };
    try {
      this._pointerCb(payload);
    } catch (err) {
      if (typeof console !== "undefined") console.error("CompositeView pointer callback failed", err);
    }
  }
}

/* ==================================================================== *
 * self test
 * ==================================================================== */

/**
 * Build a synthetic 64×64 spectrogram, rasterise it with the production code
 * path and verify the dB → colour mapping.
 *
 * Run it from the browser console:
 *     const { selfTest } = await import('/js/view.js'); selfTest();
 *
 * The function is DOM-free when no canvas is available (node), in which case
 * `canvas.supported` is false and the numeric checks still run.
 */
export function selfTest() {
  const out = {
    ok: false,
    rampLength: 0,
    ramp: [],
    pixels: 0,
    nonZeroPixels: 0,
    checks: [],
    errors: [],
    canvas: { supported: false, roundTrip: false },
  };
  const check = (name, pass, detail) => {
    out.checks.push({ name, pass: !!pass, detail: detail === undefined ? null : detail });
    if (!pass) out.errors.push(`${name}: ${detail === undefined ? "failed" : detail}`);
    return pass;
  };

  try {
    /* 1 — the ramp comes from the CSS tokens */
    const raw = rampFromTokens();
    const ramp = resolveRamp(raw);
    out.rampLength = ramp.length;
    out.ramp = ramp.map((c) => [c[0], c[1], c[2]]);
    check("ramp has 8 stops", ramp.length === 8, `got ${ramp.length}`);

    /* 2 — synthetic 64×64 matrix, dB ramps left → right */
    const N = 64;
    const minDb = -120;
    const maxDb = 0;
    const data = new Float32Array(N * N);
    for (let y = 0; y < N; y++) {
      for (let x = 0; x < N; x++) {
        data[y * N + x] = minDb + ((maxDb - minDb) * x) / (N - 1);
      }
    }
    const matrix = { data, rows: N, cols: N };
    const lut = buildLut(ramp);

    const raster = rasterise(matrix, {
      destRows: N,
      destCols: N,
      freqLo: 0,
      freqHi: 24000,
      nyquist: 24000,
      logFreq: false,
      minDb,
      maxDb,
      logScale: false,
      lut,
    });
    out.pixels = raster.width * raster.height;
    check("raster is 64x64", raster.width === 64 && raster.height === 64,
      `${raster.width}x${raster.height}`);

    let nonZero = 0;
    for (let i = 3; i < raster.data.length; i += 4) if (raster.data[i] !== 0) nonZero++;
    out.nonZeroPixels = nonZero;
    check("all pixels are opaque", nonZero === out.pixels, `${nonZero}/${out.pixels}`);

    /* 3 — dB mapping: column 0 → ramp stop 0, column 63 → ramp stop 7 */
    const row = 32 * N;
    const at = (x) => [raster.data[row + x * 4], raster.data[row + x * 4 + 1], raster.data[row + x * 4 + 2]];
    const expect = (u) => sampleRamp(ramp, u);
    /* the raster quantises the ramp position to 256 steps, so the reference
       colour is looked up through the same quantisation */
    const quant = (u) => expect(Math.round(clamp01(u) * 255) / 255);
    const near = (a, b, tol) => {
      const t = tol === undefined ? 1 : tol;
      return Math.abs(a[0] - b[0]) <= t && Math.abs(a[1] - b[1]) <= t && Math.abs(a[2] - b[2]) <= t;
    };

    const lo = at(0);
    const hi = at(N - 1);
    const mid = at(31);
    check("min dB maps to ramp[0]", near(lo, expect(0)), `${lo} vs ${expect(0)}`);
    check("max dB maps to ramp[7]", near(hi, expect(1)), `${hi} vs ${expect(1)}`);
    check("mid dB maps to the ramp middle", near(mid, quant(31 / (N - 1)), 2),
      `${mid} vs ${quant(31 / (N - 1))}`);

    /* every column must land on the ramp colour of its own dB value */
    let allMatch = true;
    let worst = "";
    for (let x = 0; x < N; x++) {
      const want = quant(x / (N - 1));
      const got = at(x);
      if (!near(got, want, 2)) {
        allMatch = false;
        worst = `x=${x} ${got} vs ${want}`;
        break;
      }
    }
    check("every column matches its dB ramp position", allMatch, worst);

    /* 4 — the dB → unit mapping itself */
    const uLin = dbToUnit(-60, minDb, maxDb, false);
    const uLog = dbToUnit(-60, minDb, maxDb, true);
    check("linear dB mapping", Math.abs(uLin - 0.5) < 1e-6, `${uLin}`);
    check("log dB mapping is monotonic and bounded",
      uLog > 0 && uLog < 1 && dbToUnit(-30, minDb, maxDb, true) > uLog &&
        dbToUnit(-90, minDb, maxDb, true) < uLog, `${uLog}`);
    check("log dB mapping spans the whole range",
      dbToUnit(minDb, minDb, maxDb, true) === 0 && dbToUnit(maxDb, minDb, maxDb, true) === 1,
      `${dbToUnit(minDb, minDb, maxDb, true)} .. ${dbToUnit(maxDb, minDb, maxDb, true)}`);

    /* 5 — optional canvas round trip */
    const canvas = makeCanvas(N, N);
    if (canvas) {
      const ctx = canvas.getContext("2d");
      if (ctx && typeof ctx.putImageData === "function") {
        let img = null;
        if (typeof ImageData === "function") {
          try { img = new ImageData(raster.data, raster.width, raster.height); } catch (err) { img = null; }
        }
        if (!img && typeof ctx.createImageData === "function") {
          img = ctx.createImageData(raster.width, raster.height);
          img.data.set(raster.data);
        }
        if (img) {
          ctx.putImageData(img, 0, 0);
          out.canvas.supported = true;
          if (typeof ctx.getImageData === "function") {
            const back = ctx.getImageData(0, 0, N, N);
            let same = true;
            for (let i = 0; i < back.data.length; i++) {
              if (back.data[i] !== raster.data[i]) { same = false; break; }
            }
            out.canvas.roundTrip = same;
            check("canvas round trip preserves pixels", same, "pixel mismatch");
          }
        }
      }
    }
    if (!out.canvas.supported) {
      out.canvas.reason = "no canvas implementation in this runtime";
    }
  } catch (err) {
    out.errors.push(String((err && err.stack) || err));
  }

  out.ok = out.errors.length === 0;
  return out;
}
