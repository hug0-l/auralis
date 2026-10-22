# Visual verification log

Each entry: what was checked, what it looked like, and what was changed as a result.

## 01-boot.png — first boot, no file
Shell renders as a three-row grid: 44 px top bar, 232 px module rail, 728 px stage,
320 px inspector, 26 px status bar. Zero page scroll (only the rail, inspector and
chain strip scroll). Palette is the studio set (`#161a21` app, `#2f87ff` accent,
`#09b9b9` secondary, `#ebebeb` text, Source Sans Pro / Roboto Mono).
**Fixed:** nothing. Matches the layout spec.

## 02-loaded.png — a probe tone, module panel open
The right inspector showed the De-Click panel: name + code, power button, preset chip
`Default*`, Learn, Compare, `…`, then the grouped controls (Algorithm select, Sensitivity
knob at 3.1, Frequency Skew, Click Widening) and an Analysis footer toggle.
**Defect found:** the spectrogram area rendered as a flat near-white field.
**Cause:** the server publishes the spectrogram as `uint8` with a scale
(`dB = min_db + v*(max_db-min_db)/255`), but the view was reading the values as
decibels directly, so every sample read >= 0 dB and saturated the colour ramp.
**Fixed:** `aurui/js/view.js` `setData()` now honours `spectrogram_encoding` and decodes
uint8 to dB before rasterising. 652 distinct colours afterwards.

## 03-spectro.png — after the uint8 fix
The spectrogram floor is dark again; the module list had not been fetched yet (BUSY).

## 04-spectro-fixed.png — 1 kHz tone + 50 Hz hum + a click
The 1 kHz line reads orange, the 50 Hz hum a second orange line near the bottom, the
click at 1.0 s a full-height vertical line, the noise floor green, the waveform a
dense blue envelope underneath. This is a working spectral view.
**Defect found:** the frequency tick labels (10k / 1k / 100) were drawn straight onto
the spectrogram and were unreadable over bright content.
**Fixed:** `_label()` now paints a translucent panel behind the text, and the decibel
legend got its own bordered panel so it reads as an overlay widget.

## 05-labels.png — after the label fix
Frequency ticks readable; decibel legend sits in a panel in the top right.

## 06-final.png / 07-working.png — full working state
20 repair modules + 17 utility modules in the browser with ADV badges, the De-Click
panel with a live power button and preset chip, the chain strip chip, and the status bar
reporting latency and engine state.

### Status bar
The cursor readout shows `--:--.--` before a file is loaded; that is the intended
placeholder, not a rendering fault (the earlier screenshot was just a small font at
1280 px wide).
