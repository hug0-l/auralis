# QA — Visual & Functional Acceptance Runbook

The alpha is judged on two axes: **does it work** (`verify_app.py`) and **does it feel like a
professional audio tool** (this runbook). Both are required.

---

## 1. Automated functional pass

```powershell
cd <the repository root>
python qa\verify_app.py --port 8741
```
22 checks: server health, module catalogue, param descriptors, upload, analysis (spectrogram +
waveform), chain add/reorder/bypass/param set, render (WAV header + length preserved), compare,
presets, static UI assets.
**Gate: 22/22.**

```powershell
python -m pytest tests -q
```
**Gate: green, no failures** (skips allowed for not-yet-implemented modules, and the skip list
must be empty by the end of the alpha).

```powershell
python tools\validate_schema.py
```
**Gate: no schema drift, every module matches the canonical parameter table.**

---

## 2. Visual pass — browser drive

The UI is served at `http://127.0.0.1:8741/`. Drive it with the Firefox DevTools bridge.

### 2.1 Start
```powershell
cd <the repository root>
$env:PYTHONPATH="."
python -m uvicorn aurserver.app:app --port 8741
```

### 2.2 Drive sequence

| # | Action | Tool | Expected |
|---|---|---|---|
| 1 | open the app | `navigate_page` -> `http://127.0.0.1:8741/` | 3-row shell renders, no console errors |
| 2 | full-page shot | `screenshot_page` (fullPage) | topbar / rail / stage / inspector / statusbar all present |
| 3 | DOM sanity | `evaluate_script` | `document.querySelectorAll('#topbar,#rail,#stage,#inspector,#statusbar').length === 5` |
| 4 | console errors | `evaluate_script` (patch `console.error` early) or `get_firefox_output` | zero errors |
| 5 | load a test file | `evaluate_script` -> build a File and POST it to `/api/upload`, then `/api/analyze`, then call the view's `setData` | spectrogram + waveform visible |
| 6 | zoom / pan | `evaluate_script` dispatching wheel + drag events on the canvas | the view redraws, no error |
| 7 | add a module | `evaluate_script` -> `fetch('/api/chain',{method:'POST',body:'{"code":"AUR_DEHUM"}'})` then refresh the UI | a chip appears in the chain strip, the panel shows De-Hum |
| 8 | panel controls | `screenshot_by_uid` on the inspector | knobs render with labels, values and units |
| 9 | drag a knob | `evaluate_script` dispatching pointer events | the value changes, the readout updates |
| 10 | toggle a switch | same | state flips, the accent lights up |
| 11 | render | `evaluate_script` -> POST `/api/render` | a modal appears with an audio preview and a download link |
| 12 | narrow window | resize the viewport to 1280x800 | no overflow, no clipped controls |
| 13 | 2x display | `screenshot_page` | canvas is crisp (devicePixelRatio handled) |

### 2.3 Visual bar (judge these, do not skip)

- The shell reads as a **dense professional tool**, not a web page: no rounded cards, no
  oversized type, no drop shadows on flat surfaces.
- The spectrogram is the visual centre of gravity and fills the stage.
- The right inspector shows a control grid that is **aligned**, not a ragged list.
- Type is small and consistent (10-12 px for labels, 14 px for the module name).
- Colour is used only for state (accent = active/on, amber = learning, red = destructive).
- Nothing scrolls except the rail, the inspector and the chain strip.
- At 1280x800 the module panel of De-Hum (19 params) is reachable without hunting.

### 2.4 Screenshot archive
Save each shot to `qa/shots/<NN>-<name>.png` and keep a one-line note in
`qa/shots/notes.md` describing what was fixed as a result.

---

## 3. Acceptance gates for the alpha

| Gate | Criterion |
|---|---|
| G1 core | `aurcore` imports clean; every module in the canonical table is registered and passes the schema check |
| G2 dsp | FFT / COLA / resampler / biquad / loudness numeric tests pass |
| G3 modules | every module processes a signal without raising; the 7 flagship behaviours hit their measured targets |
| G4 chain | add / remove / reorder / bypass / param-edit all round-trip through the server |
| G5 render | a rendered file is a valid WAV of the right length, and the processed audio is measurably cleaner |
| G6 ui | the 13-step visual drive passes; the visual bar is met |
| G7 plugins | the identity record is stamped and read back for every module; the adapter contract is documented |
| G8 docs | README, ARCHITECTURE, UI, MODULES, ROADMAP, DESIGN_DECISIONS exist and are accurate |
| G9 hygiene | no third-party trademark anywhere in the tree (grep the whole repo) |

---

## 4. Regression discipline

- Any change to `aurcore/schema/params.tsv` MUST be accompanied by a re-run of
  `tools/validate_schema.py` and the module agents' implementers.
- Any change to `aurcore/params.py`, `registry.py`, `api.py`, `modules/base.py`,
  `serial/typed_value.py` or `aurui/css/tokens.css` is a **contract change**: re-run the full
  suite and the visual pass before shipping it.

