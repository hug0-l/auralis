# Auralis — Interface design

> Status: **alpha (`0.1.0a0`)**. The interface is implemented: `aurui/index.html`,
> `aurui/css/tokens.css`, `aurui/css/app.css`, `aurui/css/components.css` and
> `aurui/js/` (`app.js`, `api.js`, `browser.js`, `chain.js`, `panel.js`, `view.js`,
> `widgets.js`, `session.js`, `preset_manager.js`, `spectral_editor.js`, `analysis.js`,
> `batch.js`, `keymap.js`, `i18n.js`). This document describes what is there.
>
> The binding rules are in [`../CONTRACT.md`](../CONTRACT.md) §8. The normative source for
> colour and type is [`aurui/css/tokens.css`](../aurui/css/tokens.css) — no colour is
> hard-coded in HTML or JavaScript, and `tests/test_ui_contracts.py` enforces that.

---

## 1. Layout

One screen. The page itself never scrolls; the composite view and the inspector scroll
independently.

```
┌───────────────────────────────────────────────────────────────────────────────┐
│ TOPBAR  ◈ Auralis │ Open  file.wav 48 kHz 2ch │ ▶ ■ ↻ │ Composite│Wave│Spec │
│                                                     │ [lang] │Compare  Render│
├──────────────┬────────────────────────────────────────────┬───────────────────┤
│ LEFT RAIL    │  VIEW TOOLBAR                             │ RIGHT INSPECTOR   │
│              │  − 1.0× + Fit │ freq 0–22050 Hz │          │                   │
│  🔍 search   │  ☐ freq-sel ☐ time-sel │ [Spectral]       │ Module│Chain│     │
│              │                    0..−120 dB log ☐       │ Analysis│Batch│   │
│  ▾ Repair    ├────────────────────────────────────────────┤ Presets│         │
│    De-Click  │                                            │  De-Click      ⏻  │
│    De-Hum    │        SPECTROGRAM CANVAS                  │  preset*  Learn ⋯│
│    …         │        (frequency × time)                  │                   │
│  ▾ Utility   ├────────────────────────────────────────────┤  Algorithm ▾      │
│    EQ        │                                            │  Sensitivity ◯    │
│    Gain      │        WAVEFORM CANVAS                     │  Frequency Skew ◯ │
│  ▾ Measure.  │        (amplitude × time)                  │  Click Widening ◯ │
│  ▾ System    │                                            │  Output Clicks ⊙  │
│              │                                            │                   │
│              │                                            │  CHAIN STRIP      │
│              │                                            │  [De-Click][EQ]   │
├──────────────┴────────────────────────────────────────────┴───────────────────┤
│ STATUS BAR  00:12.480 │ no selection │ lat 128 smp │ −12.4 dBFS │ idle       │
└───────────────────────────────────────────────────────────────────────────────┘
```

Regions and their DOM owners. The ids are the ones in `aurui/index.html`:

| Region | Element | Owner | Behaviour |
|---|---|---|---|
| Top bar | `#topbar` | `app.js` | brand, file strip, transport, view mode, locale, compare, render |
| Session toolbar | `.session-host` inside the top bar's right-hand group | `session.js` | undo, redo, save session, open session and the dirty marker — created at run time and inserted at the front of that group |
| Left rail | `#rail` | `browser.js` | searchable, category-grouped module browser |
| View toolbar | `#view-toolbar` | `app.js` | zoom, frequency window, selection modes, spectral editor, dB range and log/linear |
| Composite view | `#canvas-wrap` | `view.js` | two stacked canvases: spectrogram over waveform |
| Inspector | `#inspector` | `panel.js`, `chain.js`, `analysis.js`, `batch.js`, `preset_manager.js` | five tabs — Module, Chain, Analysis, Batch and Presets |
| Chain strip | `#chain-strip` | `chain.js` | created at run time and appended to the inspector |
| Status bar | `#statusbar` | `app.js` | cursor, selection, latency, level, engine state |
| Overlays | `#modal`, `#toast` | `app.js` | dialogs and transient messages |

Four of the five inspector tabs are in the markup — `#inspector-tabs` holds a `data-tab`
button for `panel`, `chain`, `analysis` and `batch`. The **Presets** tab is created at run
time by `app.js` when the preset library has loaded. The keyboard command that cycles the
tabs walks all five, in that order (`INSPECTOR_TABS` in `app.js`).

Geometry comes from the tokens: rail 232 px (`--rail-w`), inspector 320 px
(`--inspector-w`), top bar 44 px (`--topbar-h`), status bar 26 px (`--status-h`). The
layout must work at 1280×800 and stay correct at 2560×1440.

### How the shell is put together

`app.js` owns the DOM skeleton. **Every** other module is loaded with a dynamic
`import()`, so a missing or broken file only costs the feature it carries: the workbench
still boots and the shell falls back to its own rendering.

Five modules are loaded **first**, because the shell cannot start without them:

| Core module | What it contributes | Loaded as |
|---|---|---|
| `api.js` | the HTTP client and the element helpers | `./api.js` |
| `view.js` | the composite view | `./view.js` |
| `panel.js` | the module inspector panel | `./panel.js` |
| `browser.js` | the module browser | `./browser.js` |
| `chain.js` | the chain strip | `./chain.js` |

Seven more are **optional**, and the shell does not wait for them:

| Optional module | What it contributes | Loaded as |
|---|---|---|
| `analysis.js` | the measurement panel of the Analysis tab | `./analysis.js` |
| `batch.js` | the batch processor of the Batch tab | `./batch.js` |
| `spectral_editor.js` | the region overlay of the composite view | `./spectral_editor.js` |
| `keymap.js` | the declarative keyboard map, the help overlay and the accessibility furniture | `./keymap.js` |
| `i18n.js` | the string catalogue of the shell | `./i18n.js` |
| `session.js` | the undo/redo controller and the session toolbar | `./session.js` |
| `preset_manager.js` | the preset library panel | `./preset_manager.js` |

---

## 2. Component rules

Every control is generated from a `ParamDesc` (`widgets.js`). There is no hand-drawn
control in the module panel: the panel factory reads `type`, `default`, `minimum`,
`maximum`, `unit`, `choices` and `group` and picks the control from the parameter type.

| Param type | Control | Rules as implemented |
|---|---|---|
| `float` | **Knob** | 44 px SVG (`--knob-size`). Drag **vertically** (up increases); the full range spans a fixed pixel travel. `Shift` = 0.2× (five times finer). Double-click = reset to the default. Wheel = one step. Arrows = one step, `PageUp`/`PageDown` = ten steps, `Home`/`End` = min/max. A drag that passes within 2 % of the range of the default snaps to the default; a key press never snaps. |
| `int` | **Knob**, stepped | Same gestures, snapped to integers. |
| `bool` | **Toggle** | A `role="switch"` button with a dot and a text label that reads `On`/`Off`. The on state also carries the accent colour, so the state is never colour-only. |
| `enum` | **Select** | A native `<select>` listing `desc.choices` in table order — never sorted alphabetically. |
| `float` with `%` | **Slider** | Horizontal track. Drag, wheel, arrows, `PageUp`/`PageDown`, `Home`/`End`, `Shift` = 0.2×. Used for 0–100 % style parameters and for mixer channel faders. |

### Module panel

A module panel is a header row plus a grid of grouped controls (`panel.js`):

* **Header** — display name, power button (bypass), preset name with a `*` dirty flag, a
  Learn button (only when the module declares `supports_learn = True`), and a `⋯`
  overflow menu.
* **Body** — controls grouped by `ParamDesc.group`, in the order the groups first appear
  in the module's parameter list. A parameter with no group lands in the default group.
* **Footer** — the analysis switches: a module with an `Output … Only` parameter gets a
  button per such parameter, so the removed component can be rendered on its own.
* The dirty flag is computed by comparing the live values against the saved preset.

### Chain strip

A horizontal list of module chips in **signal order** (`chain.js`). Each chip carries the
module name, an `ADV` badge when the module is an `advanced` edition, a power toggle, a
drag handle and a remove button (`×`). Chips are reordered by HTML5 drag and drop, with a
drop indicator on the edge the chip would land on. The strip is the only place where chain
order is visible at a glance.

The inspector's **Chain** tab shows the same chain as a numbered list — display name, and
either the module code or its bypassed state — with a *Clear chain* button underneath.

### Composite view

Two stacked canvases sharing one horizontal (time) axis (`view.js`):

* **Spectrogram** — frequency on the vertical axis, time on the horizontal, coloured with
  the `--spec-*` ramp (`--spec-0` at the floor, `--spec-7` at the top of the range). The
  raster is cached in an offscreen canvas so a pan does not re-render it.
* **Waveform** — amplitude on the vertical axis, peak envelope, centred.
* A hover readout follows the pointer and reports time, frequency and the dB value under
  the cursor.
* The dB range can be shown linearly or logarithmically; the log curve expands the quiet
  end of the range.
* The **spectral editor overlay** (`spectral_editor.js`) draws a draggable, resizable
  rectangle over the spectrogram: a selection in the time/frequency plane that the
  Spectral Editor module (`AUR_SPECTRALEDITOR`) processes. The overlay never touches the
  audio itself — it reports the selection, and the application owns the engine.

---

## 3. Interaction model

### Zoom and pan

| Action | Result |
|---|---|
| wheel over the view | zoom the time axis, anchored on the pointer (the sample under the cursor stays under the cursor) |
| `+` / `-`, or the toolbar buttons | zoom in / out |
| `Fit` | the whole asset is visible; the frequency axis shows 0 … Nyquist |
| drag with no selection tool armed | pan the time axis |
| `freq-lo` / `freq-hi` inputs | set the displayed frequency window |

Zoom is clamped to the implementation's minimum and maximum (1× to 128×) and the scroll
position is clamped to the asset bounds, so the view can never be dragged into empty
space.

### Selection

Two independent selection modes, each toggled from the view toolbar:

* **Frequency selection** — restricts which frequencies the chain processes.
* **Time selection** — restricts which samples the chain processes, and bounds the preview
  and the render region.

When neither mode is armed a drag pans. When exactly one is armed a drag selects. When
both are armed, the dominant axis decides once the pointer leaves a 3-pixel dead zone, so
the gesture starts unambiguously.

Selections are drawn with the `--sel` fill and a `--sel-edge` outline, so they are legible
without relying on hue alone. The status bar always reports the current selection, or
`no selection`.

### Preview

* The **preview** button and the **space bar** render a short window of the chain output
  starting at the cursor, decode the returned WAV and play it in the browser.
* Preview and full render share one server path (`POST /api/render`), so what you hear is
  what you export.
* The server also offers a live preview stream over `/ws/stream`, which sends binary
  frames of interleaved int16 PCM and owns its own engine, so it can never disturb the
  chain the user is working on.
* Loop mode (the ↻ button) repeats the buffer until stopped.
* A preview failure is reported as a toast and leaves the engine state at `error`.

### Undo and redo

The history is a command stack of parameter edits and chain mutations. It is bounded at
200 entries and two edits of the same kind merge when they are less than a second apart,
so a knob drag is one entry rather than one per frame.

The stack lives in the **client** (`aurui/js/session.js` over `aurcore/undo.py`), and
every step is carried out on the **server** through the ordinary chain routes. The server
therefore stays the single owner of the chain, and the client never tries to reverse a
state on its own.

The shell mounts the session toolbar at run time — undo, redo, save session, open session
and the dirty marker — at the front of the top bar's right-hand group, and binds
`Ctrl+Z` for undo and `Ctrl+Shift+Z` for redo. The same controller talks to the session
routes (`POST /api/session/save`, `POST /api/session/load`, `GET /api/session/list`), so
the session document on disk and the undo history are one thing the user can see.

---

## 4. The keyboard map

**[`aurui/js/keymap.js`](../aurui/js/keymap.js) is the source of truth.** The bindings are
data, not code: the help overlay (`F1`) renders the very same table, so the documentation
cannot drift away from the behaviour.

A command declares *where* it applies. The resolver prefers the command of the focused
region and falls back to the global commands:

| Context | Root element | Meaning |
|---|---|---|
| `global` | — | applies everywhere |
| `view` | `#stage` | the composite view |
| `panel` | `#inspector` | the inspector |
| `browser` | `#rail` | the module browser |

### The commands

Twenty-three commands. The table is `COMMANDS` in `aurui/js/keymap.js`; the help overlay
(`F1`) renders the very same entries, so this table and the behaviour cannot drift.

| Context | Command | Key | Action |
|---|---|---|---|
| Global | `transport.preview` | `Space` | Play a short preview of the chain output. |
| Global | `transport.stop` | `S` | Stop the preview. |
| Global | `module.bypass` | `B` | Bypass or re-enable the selected module. |
| Global | `module.remove` | `Delete` | Remove the selected module from the chain. |
| Global | `chain.selectPrev` | `[` | Select the previous module in the chain. |
| Global | `chain.selectNext` | `]` | Select the next module in the chain. |
| Global | `chain.movePrev` | `Ctrl+[` | Move the selected module one place earlier in the chain. |
| Global | `chain.moveNext` | `Ctrl+]` | Move the selected module one place later in the chain. |
| Global | `session.undo` | `Ctrl+Z` | Undo the last change of the chain. |
| Global | `session.redo` | `Ctrl+Shift+Z` | Redo the change that was undone last. |
| Global | `preset.save` | `Ctrl+S` | Save the settings of the selected module as a preset. |
| Global | `dialog.close` | `Escape` | Close the open dialog. |
| Global | `help.toggle` | `F1` | Show or hide the list of shortcuts. |
| Global | `browser.focusSearch` | `/` | Move the cursor into the module search field. |
| View | `view.zoomIn` | `+` / `=` | Zoom the composite view in. |
| View | `view.zoomOut` | `-` / `_` | Zoom the composite view out. |
| View | `view.fit` | `0` | Fit the whole file into the viewport. |
| View | `view.reset` | `R` | Reset the zoom, the scroll position and the selection. |
| View | `view.toggleFreqSelection` | `F` | Turn the frequency selection on or off. |
| View | `view.toggleTimeSelection` | `T` | Turn the time selection on or off. |
| Panel | `inspector.cycleTabs` | `T` | Step through the Module, Chain, Analysis, Batch and Presets tabs. |
| Panel | `panel.presetMenu` | `M` | Open the module menu (preset, reset, remove). |
| Browser | `browser.addModule` | `Enter` | Add the highlighted module to the chain. |

`T` is bound twice on purpose: in the view context it toggles the time selection, in the
panel context it cycles the inspector tabs. The focused region decides.

### The rules the matcher honours

* **Nothing is swallowed while the user types.** An event whose target is an `input`, a
  `textarea`, a `select`, a `contenteditable` node or an element with a textbox-like role
  never triggers a command — not even `Space`.
* **A command fires only when exactly the modifiers it expects are held.** `Ctrl`, `Alt`
  and `Meta` are always part of the comparison. `Shift` is part of it too, except for
  single letters, where the case of the key already carries that information.
* **No two commands of the same context share a binding.** `tests/test_ui_contracts.py`
  asserts it.

### The focused control

Outside the command table, a focused widget handles its own keys (`widgets.js`):

| Key | Action |
|---|---|
| `↑` `→` | increase by one step |
| `↓` `←` | decrease by one step |
| `PageUp` / `PageDown` | ten steps |
| `Home` / `End` | minimum / maximum |
| `Shift` + any of the above | 0.2× step |
| wheel | one step |

---

## 5. Accessibility commitments

The shell owns the markup, so the accessibility furniture is applied at start-up by
`keymap.js`'s `applyAccessibility(root)` rather than being scattered through the HTML.
It sets:

* the toast to `role="status"`, `aria-live="polite"`, `aria-atomic="true"`;
* the status-bar readouts (`#st-cursor`, `#st-sel`, `#st-level`, `#st-latency`) to
  `aria-live="polite"`, and `#st-engine` to `role="status"`;
* `#canvas-wrap` to `tabindex="0"` and `role="group"`, so the view shortcuts have a focus
  home;
* the two canvases to `role="img"` with an accessible name;
* `#view-mode` to `role="radiogroup"` and its buttons to `role="radio"` with
  `aria-checked`;
* `#inspector-tabs` to `role="tablist"` and its buttons to `role="tab"` with
  `aria-selected`;
* the chain strip to `role="list"`;
* an `aria-label` on every control that carries only a symbol and no text.

The commitments:

1. **Everything is reachable by keyboard.** Every control is a real focusable element with
   a visible focus ring (`:focus-visible` in `app.css`). No control is reachable only by
   pointer.
2. **No information by colour alone.** Toggle state is text plus fill. Selection is fill
   plus outline. Bypass is a power glyph plus a muted chip. The spectrogram is the one
   place where colour carries meaning, and it is always accompanied by a numeric dB
   readout.
3. **Semantic roles and names.** A knob is `role="slider"` with `aria-valuenow`,
   `aria-valuemin`, `aria-valuemax`, `aria-valuetext` (the natural-unit string, e.g.
   `"−12.4 dB"`) and `aria-label` (the parameter name). A toggle is `role="switch"` with
   `aria-checked`. A select is a native `<select>`. The knob's decorative SVG is
   `aria-hidden`.
4. **Live regions.** The status bar and the toast announce their new value without
   stealing focus.
5. **Respects `prefers-reduced-motion`.** Transitions and animations are disabled when the
   user asks for reduced motion (`app.css`, `components.css`); the audio preview still
   works.
6. **Ships a high-contrast variant.** A `[data-contrast="high"]` block in
   `components.css` raises the surface and text tokens against each other, thickens the
   focus ring and brightens the headings. It is opt-in through the attribute, not
   switched by `prefers-contrast`, because the two are not the same thing.
7. **Zoom-safe layout.** The layout is built on CSS grid with token-based sizing, so the
   browser's font-size zoom reflows the UI without clipping. Pixel-perfect screenshots at
   100 % are the reference; 200 % zoom is the accessibility floor.
8. **Canvas content is described in text.** The pixels of a canvas cannot be read, so
   each canvas carries the duration, the sample rate, the channel count and the peak
   level of the signal it shows, in the active locale
   (`refreshCanvasSummary()` in `app.js`, the `canvas.summary` catalogue key).
9. **No flashing.** Nothing in the UI flashes more than three times per second; the clip
   indicator is a steady fill, not a strobe.
10. **Latency and gain are always visible.** The status bar shows the engine latency in
    samples and the current level in dBFS, so a user working without audio can still see
    what the tool is doing.

### The audit

`python qa/a11y/audit_a11y.py` builds the *effective* interface — the markup, plus the
`data-i18n*` translation, plus the attributes `applyAccessibility()` adds — and audits it
without a browser, a server or a network. It checks **eight error classes** —
`missing-translation`, `unlabelled-control`, `not-keyboard-reachable`, `missing-live-region`,
`no-focus-ring`, `no-high-contrast-variant`, `runtime-drift` and `duplicate-declaration`
— and **seven warning
classes** — `symbol-name`, `placeholder-name`, `missing-element`, `no-canvas-summary`,
`no-locale-switch`, `unused-catalogue-keys` and `module-without-catalogue`.

Today it reports **0 errors and 0 warnings** over 26 controls and the three locales
(`en`, `zh-CN`, `de`, 370 catalogue keys each).

### Known accessibility gaps

None that the audit can see. The audit reports **0 errors and 0 warnings**; the ten
commitments above are met, and every interface module renders its text through the
catalogue.

---

## 6. Colour and type

All colour lives in [`aurui/css/tokens.css`](../aurui/css/tokens.css). No colour is
hard-coded in HTML or JavaScript — the UI reads the CSS custom properties, and
`tests/test_ui_contracts.py` fails the build if a stylesheet or an injected stylesheet
contains a colour literal.

| Token | Value | Use |
|---|---|---|
| `--bg-0` | `#161a21` | app background |
| `--bg-1` | `#1b2028` | panel background |
| `--bg-2` | `#232a33` | raised surface |
| `--bg-3` | `#313a42` | control surface |
| `--bg-4` | `#3b444d` | hover / active surface |
| `--line` | `#39424c` | hairline border |
| `--line-soft` | `#262d36` | subtle separator |
| `--txt-0` | `#ebebeb` | primary text, values |
| `--txt-1` | `#a5acb2` | secondary text, labels |
| `--txt-2` | `#7c858f` | tertiary text |
| `--txt-3` | `#566068` | decoration only |
| `--accent` | `#2f87ff` | primary accent, active state, focus |
| `--accent-dim` | `#1c5fb0` | accent, pressed / recessed |
| `--accent-2` | `#09b9b9` | second accent, analysis and spectrum overlays |
| `--accent-3` | `#e0a13a` | learn, attention, dirty marker |
| `--danger` | `#d1544f` | destructive, clipping |
| `--sel` | `#24405c` | selection fill |
| `--sel-edge` | `#3d6ea3` | selection outline |

The spectrogram ramp is a separate eight-step scale (`--spec-0` … `--spec-7`) from near-black
to near-white. It is the only place where a large continuous colour range is used; every
other surface is one of five greys plus three accents.

### Measured contrast

Ratios computed from the token values with the WCAG 2.1 relative-luminance formula:

| Foreground | On | Ratio | Verdict |
|---|---|---|---|
| `--txt-0` `#ebebeb` | `--bg-0` | 14.6 : 1 | AAA |
| `--txt-0` `#ebebeb` | `--bg-1` | 13.7 : 1 | AAA |
| `--txt-0` `#ebebeb` | `--bg-2` | 12.1 : 1 | AAA |
| `--txt-1` `#a5acb2` | `--bg-1` | 7.1 : 1 | AAA |
| `--txt-1` `#a5acb2` | `--bg-2` | 6.3 : 1 | AA |
| `--accent` `#2f87ff` | `--bg-0` | 5.0 : 1 | AA |
| `--accent` `#2f87ff` | `--bg-1` | 4.7 : 1 | AA |
| `--accent-2` `#09b9b9` | `--bg-1` | 6.7 : 1 | AA |
| `--accent-3` `#e0a13a` | `--bg-1` | 7.3 : 1 | AAA |
| `--txt-2` `#7c858f` | `--bg-1` | 4.4 : 1 | AA for large text only |
| `--txt-3` `#566068` | `--bg-1` | 2.5 : 1 | **fails — decoration only** |
| `--danger` `#d1544f` | `--bg-1` | 4.0 : 1 | AA for large text and icons |
| `--sel-edge` `#3d6ea3` | `--bg-1` | 3.1 : 1 | AA for non-text UI components |
| `--line` `#39424c` | `--bg-1` | 1.6 : 1 | decorative border only |

Consequences, and they are binding on the UI code:

* `--txt-2` is for tertiary labels and decoration. It is never used for a parameter value,
  an error message, or a state that has to be read.
* `--txt-3` fails the 3 : 1 threshold entirely. It is used for decorative separators and
  nothing else.
* `--danger` clears the threshold for large text and for icons but not for small body
  text. Every destructive or clipping indicator therefore carries a glyph and a text
  label, so the state is never colour-only.
* `--line` is a decorative hairline; it is not required to carry information.

### Type

| Token | Value | Use |
|---|---|---|
| `--font-ui` | Source Sans Pro, Roboto, Segoe UI, system-ui | labels, buttons, module names |
| `--font-mono` | Roboto Mono, JetBrains Mono, SF Mono, Cascadia Mono, Consolas | every number: values, time, frequency, dB |
| `--fs-xs` … `--fs-xl` | 10 / 11 / 12 / 14 / 17 px | density ramp |

Rule: **numbers are always monospaced.** A value that changes width as it changes (a
level, a frequency) shifts its neighbours and destroys the reading of a column of
parameters. The font stacks are fallbacks, not bundled fonts — the UI ships no web fonts
and therefore triggers no network request.

---

## 7. Localisation

The string catalogue is [`aurui/js/i18n.js`](../aurui/js/i18n.js). It is a plain,
dependency-free dictionary per locale:

* **Three locales ship:** `en` (the reference), `zh-CN` and `de` — **370 keys each**.
* **Every locale carries exactly the same key set.** A missing key falls back to English —
  never to the raw key — so a half-finished translation still produces a usable interface.
  `tests/test_ui_contracts.py` asserts the key sets match and that no key ever resolves to
  its own name.
* **The catalogue is data.** A translator can be handed a copy of one locale object and
  fill it in; nothing else in the code base has to change.
* **The keys are grouped by the part of the interface they belong to**, with a dotted
  prefix: `app.`, `topbar.`, `transport.`, `browser.`, `inspector.`, `chain.`, `status.`,
  `control.`, `dialog.`, `help.`, `key.`, `toast.`.
* **Placeholders** are written `{name}` and filled by `t(key, fallback, vars)`.
* **Markup is translated by attribute**: `data-i18n` (text content), `data-i18n-title`,
  `data-i18n-aria` (accessible name) and `data-i18n-placeholder`.
* **The chosen locale is remembered** in `localStorage` under `auralis.locale` and can be
  changed from the top-bar language selector.

The keyboard help table follows the locale too: the label and the description of every
command are looked up as `key.<command id>.label` and `key.<command id>.description`, so
the help overlay never shows an English string inside a translated interface.

The catalogue now covers the **chrome and the modules** — the top bar, the transport, the
browser, the inspector, the chain strip, the status bar, the common control labels, the
dialogs, the help, and the session and preset panels. The audit no longer reports a
`module-without-catalogue` warning: every interface module goes through `i18n.js`.
