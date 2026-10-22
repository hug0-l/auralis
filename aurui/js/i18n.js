/* Auralis — localisation.
 *
 * A tiny, dependency-free string catalogue for the interface chrome.
 *
 * Design rules
 *  - The catalogue is a plain dictionary (`key -> translation`).  A translator
 *    can be handed a copy of one locale object and fill it in; nothing else in
 *    the code base has to change.
 *  - Every locale carries exactly the same key set.  A missing key falls back
 *    to English (never to the raw key), so a half-finished translation still
 *    produces a usable interface.
 *  - The module performs no DOM access at import time, so it can be imported
 *    by a head-less test harness.
 *  - There is **no per-locale file loader**: the catalogues are plain objects
 *    inside this file.  The module is imported synchronously and must not
 *    reach the network, so a locale is added by copying one object and
 *    filling it in here — `aurui/locales/*.json` is deliberately not used.
 *
 * Public API
 *   t(key, fallback, vars)   look up a string in the active locale
 *   setLocale(code)          switch the active locale
 *   getLocale()              the active locale code
 *   availableLocales()       every locale code that ships with the build
 *   localeName(code)         the human readable name of a locale
 *   onLocaleChange(cb)       subscribe to locale changes
 *   translateDocument(root)  apply the catalogue to `data-i18n*` attributes
 *   detectLocale(nav)        the locale the browser asks for
 *   initLocale(options)      start the locale up (stored -> detected -> default)
 */

/* ------------------------------------------------------------------ locales */

/** The locale used when nothing else has been selected. */
export const DEFAULT_LOCALE = "en";

/** Human readable names of the shipped locales. */
export const LOCALE_NAMES = {
  en: "English",
  "zh-CN": "简体中文",
  de: "Deutsch",
};

/** Where the chosen locale is remembered between sessions. */
export const LOCALE_STORAGE_KEY = "auralis.locale";

/**
 * The string catalogues.
 *
 * `en` is the reference catalogue: every other locale is expected to carry
 * exactly the same keys.  Keys are grouped by the part of the interface they
 * belong to, using a dotted prefix.
 */
export const CATALOGUES = {
  en: {
    /* ---- application ----------------------------------------------- */
    "app.name": "Auralis",
    "app.tagline": "Audio restoration workbench",
    /* ---- top bar --------------------------------------------------- */
    "topbar.open": "Open",
    "topbar.open.title": "Open an audio file",
    "topbar.noFile": "No file loaded",
    "topbar.compare": "Compare",
    "topbar.compare.title": "Compare two chains",
    "topbar.render": "Render",
    "topbar.render.title": "Render and export",
    "topbar.learned.title": "Learned stage of the render",
    "topbar.learned": "Learned stage",
    "topbar.learned.none": "No learned stage",
    "topbar.viewMode": "View mode",
    "topbar.viewMode.composite": "Composite",
    "topbar.viewMode.waveform": "Waveform",
    "topbar.viewMode.spectrogram": "Spectrogram",
    "topbar.language": "Language",
    "topbar.contrast": "High contrast",
    "topbar.contrast.title": "Switch the interface to the high-contrast variant",
    /* ---- transport ------------------------------------------------- */
    "transport.preview": "Preview",
    "transport.preview.title": "Preview (Space)",
    "transport.stop": "Stop",
    "transport.stop.title": "Stop the preview",
    "transport.loop": "Loop preview",
    "transport.loop.on": "Loop preview on",
    "transport.loop.off": "Loop preview off",
    "transport.live": "Live preview",
    "transport.live.title": "Live preview over the streaming socket",
    "transport.live.on": "Live preview on",
    "transport.live.off": "Live preview off",
    "transport.live.unavailable": "The live preview is not available",
    /* ---- module browser -------------------------------------------- */
    "browser.search": "Search modules",
    "browser.search.title": "Filter the module list by name",
    "browser.category.repair": "Repair",
    "browser.category.utility": "Utility",
    "browser.category.measurement": "Measurement",
    "browser.category.system": "System",
    "browser.category.other": "Modules",
    "browser.empty": "No modules available",
    "browser.noMatch": "No modules match “{query}”",
    "browser.add": "Add to chain",
    "browser.badge.advanced": "Advanced",
    "browser.badge.analysis": "Analysis output",
    "browser.badge.learn": "Supports Learn",
    "browser.badge.ml": "Uses a model",
    /* ---- inspector ------------------------------------------------- */
    "inspector.tabs": "Inspector tabs",
    "inspector.tab.module": "Module",
    "inspector.tab.chain": "Chain",
    "inspector.tab.analysis": "Analysis",
    "inspector.tab.batch": "Batch",
    "inspector.noModule": "No module selected. Add one from the browser, or click a chip in the chain strip.",
    "inspector.chainEmpty": "The chain is empty.",
    "inspector.analysisEmpty": "No analysis yet. Open an audio file to populate this tab.",
    "inspector.asset": "Asset",
    "inspector.clearChain": "Clear chain",
    "inspector.bypassed": "bypassed",
    "inspector.active": "active",
    "inspector.tab.presets": "Presets",
    "inspector.presetsEmpty": "The preset library is unavailable.",
    /* ---- chain strip ----------------------------------------------- */
    "chain.label": "Signal chain",
    "chain.empty": "No modules — add one from the browser",
    "chain.bypass": "Bypass module",
    "chain.enable": "Enable module",
    "chain.remove": "Remove from chain",
    "chain.reorder": "Drag to reorder",
    "chain.advanced": "Advanced edition",
    /* ---- status bar ------------------------------------------------ */
    "status.cursor": "Cursor",
    "status.noSelection": "no selection",
    "status.selection": "selection",
    "status.latency": "lat {n} smp",
    "status.level": "-inf dBFS",
    "status.engine.idle": "idle",
    "status.engine.ready": "ready",
    "status.engine.busy": "busy",
    "status.engine.error": "error",
    "status.engine.rendering": "rendering",
    "status.engine.playing": "playing",
    /* ---- the path of the preview ----------------------------------- */
    "status.path.label": "The path of the preview",
    "status.path.title": "What the preview plays and what the path changed",
    "status.path.titleIdle": "The path the next preview will use",
    "status.path.playing": "playing",
    "status.path.idle": "no preview is playing",
    "status.path.unknown": "The server has not reported a path yet.",
    "status.path.fast": "preview: fast path",
    "status.path.full": "preview: full path",
    "status.path.other": "preview: {name}",
    "status.path.render": "render: file path",
    "status.path.mode": "mode",
    "status.path.block": "block",
    "status.path.latency": "latency",
    "status.path.latencyValue": "{n} smp ({ms} ms)",
    "status.path.changed": "The path moved {n} parameter(s):",
    "status.path.unchanged": "The path moved no parameter — the preview uses the values you set.",
    "status.path.refused": "The cheap configuration does not run in {name} — the preview keeps your value.",
    "status.path.file": "file",
    "status.path.preview": "preview",
    "status.path.choose": "The path of the preview",
    "status.path.useFast": "Fast path",
    "status.path.useFull": "Full path",
    "status.path.restart": "The preview starts again on the path you choose.",
    "status.latency.unknown": "lat — smp",
    /* ---- the canvases ---------------------------------------------- */
    "canvas.summary": "{name}, {duration} s, {rate} Hz, {channels} ch, peak {peak} dBFS",
    /* ---- common control labels ------------------------------------- */
    "control.bypass": "Bypass",
    "control.enable": "Enable",
    "control.learn": "Learn",
    "control.learn.title": "Learn from the current signal",
    "control.compare": "Compare",
    "control.compare.title": "Compare with the original signal",
    "control.reset": "Reset to Defaults",
    "control.savePreset": "Save Preset…",
    "control.loadPreset": "Load Preset…",
    "control.removeModule": "Remove Module",
    "control.moduleMenu": "Module menu",
    "control.preset": "Preset",
    "control.default": "Default",
    "control.modified": "{name} (modified)",
    "control.close": "Close",
    "control.closeDialog": "Close dialog",
    "control.cancel": "Cancel",
    "control.confirm": "Confirm",
    "control.yes": "Yes",
    "control.no": "No",
    "control.save": "Save",
    "control.download": "Download WAV",
    "control.zoomIn": "Zoom in",
    "control.zoomOut": "Zoom out",
    "control.spectralEditor": "Spectral editor",
    "control.fit": "Fit",
    "control.frequencySelection": "frequency selection",
    "control.timeSelection": "time selection",
    "control.logScale": "log",
    "control.frequency": "freq",
    "control.hertz": "Hz",
    "control.on": "On",
    "control.off": "Off",
    "control.none": "None",
    "control.freqLo": "Low frequency cut",
    "control.freqHi": "High frequency cut",
    /* ---- dialogs --------------------------------------------------- */
    "dialog.loadPreset": "Load preset",
    "dialog.savePreset": "Save preset — {name}",
    "dialog.presetName": "Preset name",
    "dialog.presetCategory": "Category",
    "dialog.noPresets": "No presets stored yet.",
    "dialog.loading": "Loading…",
    "dialog.renderComplete": "Render complete",
    "dialog.compareChains": "Compare chains",
    "dialog.processed": "Processed (current chain)",
    "dialog.original": "Original (dry)",
    /* ---- keyboard help --------------------------------------------- */
    "help.title": "Keyboard shortcuts",
    "help.close": "Close keyboard shortcuts",
    "help.column.key": "Key",
    "help.column.action": "Action",
    "help.column.context": "Where",
    "help.context.global": "Global",
    "help.context.view": "View",
    "help.context.panel": "Panel",
    "help.context.browser": "Browser",
    /* ---- keyboard commands ----------------------------------------- */
    "key.transport.preview.label": "Preview",
    "key.transport.preview.description": "Play a short preview of the chain output.",
    "key.transport.stop.label": "Stop",
    "key.transport.stop.description": "Stop the preview.",
    "key.transport.previewPath.label": "Preview path",
    "key.transport.previewPath.description": "Switch the preview between the fast path and the full path.",
    "key.module.bypass.label": "Bypass module",
    "key.module.bypass.description": "Bypass or re-enable the selected module.",
    "key.module.remove.label": "Remove module",
    "key.module.remove.description": "Remove the selected module from the chain.",
    "key.chain.selectPrev.label": "Previous module",
    "key.chain.selectPrev.description": "Select the previous module in the chain.",
    "key.chain.selectNext.label": "Next module",
    "key.chain.selectNext.description": "Select the next module in the chain.",
    "key.preset.save.label": "Save preset",
    "key.preset.save.description": "Save the settings of the selected module as a preset.",
    "key.dialog.close.label": "Close dialog",
    "key.dialog.close.description": "Close the open dialog.",
    "key.help.toggle.label": "Keyboard help",
    "key.help.toggle.description": "Show or hide this list of shortcuts.",
    "key.browser.focusSearch.label": "Focus module search",
    "key.browser.focusSearch.description": "Move the cursor into the module search field.",
    "key.view.zoomIn.label": "Zoom in",
    "key.view.zoomIn.description": "Zoom the composite view in.",
    "key.view.zoomOut.label": "Zoom out",
    "key.view.zoomOut.description": "Zoom the composite view out.",
    "key.view.fit.label": "Fit to window",
    "key.view.fit.description": "Fit the whole file into the viewport.",
    "key.view.reset.label": "Reset view",
    "key.view.reset.description": "Reset the zoom, the scroll position and the selection.",
    "key.view.toggleFreqSelection.label": "Frequency selection",
    "key.view.toggleFreqSelection.description": "Turn the frequency selection on or off.",
    "key.view.toggleTimeSelection.label": "Time selection",
    "key.view.toggleTimeSelection.description": "Turn the time selection on or off.",
    "key.inspector.cycleTabs.label": "Cycle inspector tabs",
    "key.inspector.cycleTabs.description": "Step through the Module, Chain and Analysis tabs.",
    "key.panel.presetMenu.label": "Module menu",
    "key.panel.presetMenu.description": "Open the module menu (preset, reset, remove).",
    "key.browser.addModule.label": "Add module",
    "key.browser.addModule.description": "Add the highlighted module to the chain.",
    "key.chain.movePrev.label": "Move module back",
    "key.chain.movePrev.description": "Move the selected module one place earlier in the chain.",
    "key.chain.moveNext.label": "Move module forward",
    "key.chain.moveNext.description": "Move the selected module one place later in the chain.",
    "key.session.undo.label": "Undo",
    "key.session.undo.description": "Undo the last change of the chain.",
    "key.session.redo.label": "Redo",
    "key.session.redo.description": "Redo the change that was undone last.",
    /* ---- messages -------------------------------------------------- */
    "toast.saved": "Preset saved",
    "toast.cleared": "Chain cleared",
    "toast.reordered": "Chain reordered",
    "toast.selectFirst": "Select a module first",
    "toast.actionFailed": "The action failed",
    /* ---- edition tags ---------------------------------------------- */
    "edition.advanced": "ADV",
    /* ---- module inspector panel ------------------------------------ */
    "panel.module": "Module",
    "panel.bypassModule": "Bypass {name}",
    "panel.enableModule": "Enable {name}",
    "panel.presetTitle": "Preset: {name}",
    "panel.analysis": "Analysis",
    "panel.groupGeneral": "General",
    /* ---- control library ------------------------------------------- */
    "widgets.parameter": "Parameter",
    /* ---- batch processor ------------------------------------------- */
    "batch.title": "Batch Processor",
    "batch.modeChain": "Current chain",
    "batch.modePreset": "Delivery preset",
    "batch.sectionDelivery": "Delivery",
    "batch.labelTarget": "Target",
    "batch.ariaTarget": "Delivery target",
    "batch.sectionChain": "Chain",
    "batch.sectionOutput": "Output",
    "batch.labelFormat": "Format",
    "batch.ariaFormat": "Output format",
    "batch.labelBitDepth": "Bit depth",
    "batch.ariaBitDepth": "Output bit depth",
    "batch.labelFolder": "Folder",
    "batch.placeholderFolder": "server default",
    "batch.ariaFolder": "Output folder",
    "batch.sectionFiles": "Files",
    "batch.dropHint": "Drop audio files here, or drop a list of server paths",
    "batch.addFiles": "Add files",
    "batch.placeholderPath": "server path of one file",
    "batch.ariaPath": "Server path",
    "batch.addPath": "Add path",
    "batch.clearList": "Clear list",
    "batch.ariaFiles": "Choose audio files",
    "batch.sectionProgress": "Progress",
    "batch.start": "Start batch",
    "batch.stop": "Stop",
    "batch.errorNoFiles": "Add at least one file to the batch.",
    "batch.noTargets": "no delivery targets",
    "batch.factsChainOnly": "the chain of the session is sent unchanged",
    "batch.chainEmpty": "The chain is empty — the delivery stages only are sent.",
    "batch.stageLoudness": "Loudness Control",
    "batch.stageDither": "Dither",
    "batch.noFiles": "No files yet.",
    "batch.removeTitle": "Remove from the batch",
    "batch.uploadingFiles": "{n} file(s) are still being uploaded",
    "batch.stagedLimit": "the server keeps {n} uploaded files alive — run the batch in smaller parts",
    "batch.noProgress": "No file has been processed yet.",
    "batch.countDone": "done",
    "batch.countFailed": "failed",
    "batch.countTotal": "total",
    "batch.measuredLoudness": "measured loudness {value} LUFS ({n} file(s))",
    "batch.noLoudness": "the server did not report a loudness measurement",
    "batch.targetCount": "{n} delivery target(s)",
    "batch.noCatalogue": "no delivery catalogue was supplied",
    "batch.jobRunning": "job {id} is running",
    "batch.fileCount": "{n} file(s) in the list",
    "batch.bit": "{n} bit",
    "batch.fileName": "file {n}",
    "batch.errorNoAssetId": "the upload did not return an asset id",
    "batch.state.idle": "idle",
    "batch.state.queued": "queued",
    "batch.state.running": "running",
    "batch.state.staging": "staging",
    "batch.state.staged": "staged",
    "batch.state.done": "done",
    "batch.state.failed": "failed",
    "batch.state.error": "error",
    "batch.state.cancelled": "cancelled",
    /* ---- analysis tab ---------------------------------------------- */
    "analysis.empty": "No measurement yet. Open an audio file to populate this tab.",
    "analysis.channels": "{n} ch",
    "analysis.selectionTitle": "Selection",
    "analysis.range": "Range",
    "analysis.length": "Length",
    "analysis.samples": "Samples",
    "analysis.wholeFile": "the whole file",
    "analysis.selectionEmpty": "The selection is empty — there is nothing to measure.",
    "analysis.selectionClamped": "The request {list} lies outside the audio; the measured range is the one above.",
    "analysis.noSpectrum": "No spectrum",
    "analysis.noHistogram": "No histogram",
    "analysis.spectrumRange": "Magnitude spectrum, {min} to {max}",
    "analysis.spectrum": "Spectrum",
    "analysis.peak": "peak {value} @ {hz} Hz",
    "analysis.peakNone": "peak —",
    "analysis.loudness": "Loudness",
    "analysis.integrated": "Integrated",
    "analysis.shortTerm": "Short term",
    "analysis.momentary": "Momentary",
    "analysis.loudnessRange": "Loudness range",
    "analysis.truePeak": "True peak",
    "analysis.waveformStats": "Waveform statistics",
    "analysis.samplePeak": "Sample peak",
    "analysis.maximum": "Maximum",
    "analysis.minimum": "Minimum",
    "analysis.mean": "Mean",
    "analysis.rms": "RMS",
    "analysis.totalRms": "Total RMS",
    "analysis.dcOffset": "DC offset",
    "analysis.possiblyClipped": "Possibly clipped",
    "analysis.linearAmplitude": "linear amplitude",
    "analysis.linearValue": "linear {value}",
    "analysis.clipThresholdTitle": "samples at or above the clipping threshold",
    "analysis.correlation": "Correlation",
    "analysis.correlationInverted": "−1 (inverted)",
    "analysis.correlationIdentical": "+1 (identical)",
    "analysis.peakHistogram": "Peak histogram",
    "analysis.clippedCount": "{n} clipped",
    "analysis.clipThreshold": "clip ≥ {value}",
    /* ---- spectral editor ------------------------------------------- */
    "spectral.regionLabel": "Spectral editor region",
    "spectral.mode": "Mode",
    "spectral.strength": "Strength",
    "spectral.newRegion": "New region",
    "spectral.noRegion": "no region — drag a selection in the view",
    "spectral.applied": "applied",
    "spectral.notApplied": "not applied",
    "spectral.mode.attenuate": "Attenuate",
    "spectral.mode.replace": "Replace",
    "spectral.mode.gain": "Gain",
    "spectral.mode.pattern": "Pattern",
    "spectral.mode.partials": "Partials",
    "spectral.status.applied": "the region is applied to module {index}",
    "spectral.status.appliedToModule": "the region is applied to the module",
    "spectral.status.pending": "the region is being sent to the module…",
    "spectral.status.missing": "the region is NOT applied — the server has no route for the region yet",
    "spectral.status.noModule": "the region is NOT applied — there is no Spectral Editor in the chain",
    "spectral.status.failed": "the region is NOT applied — {reason}",
    "spectral.status.refused": "the server refused it",
    "spectral.status.notSent": "the region has not been sent to the module yet",
    "spectral.status.holds": "the module holds {value}",
    "spectral.stale": "{value} (the module still holds the old value)",
    /* ---- preset library -------------------------------------------- */
    "preset.search": "Search presets",
    "preset.category": "Category",
    "preset.saveTitle": "Save the preset under this name",
    "preset.noMatch": "No presets match “{query}”",
    "preset.loadLabel": "Load the preset {name}",
    "preset.menu.save": "Save",
    "preset.menu.saveAs": "Save As",
    "preset.menu.rename": "Rename",
    "preset.menu.remove": "Remove",
    "preset.menu.reload": "Reload",
    "preset.menu.import": "Import",
    "preset.menu.export": "Export",
    "preset.errorUnavailable": "The preset library is not available.",
    "preset.errorName": "The preset needs a name.",
    "preset.errorModule": "The preset needs a module — select one first.",
    "preset.errorPath": "The preset {field} must not contain a path.",
    "preset.errorDot": "The preset {field} must not start with a dot.",
    "preset.errorChars": "The preset {field} contains characters that are not allowed.",
    "preset.errorReserved": "The preset name “{name}” is a reserved device name.",
    "preset.errorPathRequired": "A preset path is required.",
    "preset.errorNoExport": "There is no preset to export.",
    "preset.errorUnreadable": "The file could not be read.",
    "preset.errorUnreachable": "The preset library is not reachable.",
    "preset.errorSaveNotConfirmed": "The server did not confirm the save — the preset was not written.",
    "preset.errorRenameNotConfirmed": "The server did not confirm the rename — the preset was not written.",
    "preset.unsavedChanges": "There are unsaved changes.",
    "preset.unsavedIn": "Unsaved changes in “{name}”.",
    "preset.unsavedTitle": "Save “{name}” — there are unsaved changes",
    "preset.unsavedTitleNoName": "There are unsaved changes",
    "preset.count": "{n} preset(s).",
    "preset.errorNoSave": "There is no preset to save yet — give it a name in the form.",
    "preset.errorNoRename": "There is no preset to rename.",
    "preset.errorNoRemove": "There is no preset to remove.",
    "preset.errorSavedListFailed": "The preset was saved, but the library could not be read again — {message}",
    "preset.removeConfirm": "Remove the preset “{name}”?",
    "preset.errorRenamedNotRemoved": "The preset was renamed to “{name}”, but the old file could not be removed — {message}",
    /* ---- session toolbar ------------------------------------------- */
    "session.label": "Session",
    "session.undo": "Undo",
    "session.undoTitle": "Undo the last change",
    "session.redo": "Redo",
    "session.redoTitle": "Redo the change that was undone",
    "session.saveTitle": "Save the session",
    "session.open": "Open",
    "session.openTitle": "Open a session",
    "session.dirtyTitle": "The session has unsaved changes",
    "session.savedTitle": "The session is saved",
    "session.actionDefault": "Action",
    "session.actionAdd": "Add {name}",
    "session.actionRemove": "Remove {name}",
    "session.actionReorder": "Reorder {name}",
    "session.actionBypass": "Bypass {name}",
    "session.actionEnable": "Enable {name}",
    "session.actionChange": "Change {name}",
    "session.actionClearChain": "Clear Chain",
    "session.actionOpen": "Open {name}",
    "session.actionNew": "New Session",
    "session.hintUndo": "undo: {name}",
    "session.hintRedo": "redo: {name}",
    "session.hintLastChange": "the last change",
    "session.hintNoHistory": "no history",
    "session.titleUndo": "Undo {name}",
    "session.titleRedo": "Redo {name}",
    "session.titleNothingUndo": "Nothing to undo",
    "session.titleNothingRedo": "Nothing to redo",
    "session.titleLastAction": "Last action: {name}",
    "session.titleNoAction": "No action yet",
    /* ---- composite view -------------------------------------------- */
    "view.empty": "Load an audio file to begin",
  },
  "zh-CN": {
    /* ---- application ----------------------------------------------- */
    "app.name": "Auralis",
    "app.tagline": "音频修复工作台",
    /* ---- top bar --------------------------------------------------- */
    "topbar.open": "打开",
    "topbar.open.title": "打开音频文件",
    "topbar.noFile": "未加载文件",
    "topbar.compare": "对比",
    "topbar.compare.title": "对比两条处理链",
    "topbar.render": "渲染",
    "topbar.render.title": "渲染并导出",
    "topbar.learned.title": "渲染的学习阶段",
    "topbar.learned": "学习阶段",
    "topbar.learned.none": "没有学习阶段",
    "topbar.viewMode": "视图模式",
    "topbar.viewMode.composite": "复合视图",
    "topbar.viewMode.waveform": "波形",
    "topbar.viewMode.spectrogram": "频谱图",
    "topbar.language": "语言",
    "topbar.contrast": "高对比度",
    "topbar.contrast.title": "切换到高对比度显示",
    /* ---- transport ------------------------------------------------- */
    "transport.preview": "试听",
    "transport.preview.title": "试听（空格）",
    "transport.stop": "停止",
    "transport.stop.title": "停止试听",
    "transport.loop": "循环试听",
    "transport.loop.on": "循环试听已开启",
    "transport.loop.off": "循环试听已关闭",
    "transport.live": "实时试听",
    "transport.live.title": "通过流式套接字进行实时试听",
    "transport.live.on": "实时试听已开启",
    "transport.live.off": "实时试听已关闭",
    "transport.live.unavailable": "实时试听不可用",
    /* ---- module browser -------------------------------------------- */
    "browser.search": "搜索模块",
    "browser.search.title": "按名称筛选模块列表",
    "browser.category.repair": "修复",
    "browser.category.utility": "工具",
    "browser.category.measurement": "测量",
    "browser.category.system": "系统",
    "browser.category.other": "模块",
    "browser.empty": "没有可用的模块",
    "browser.noMatch": "没有匹配“{query}”的模块",
    "browser.add": "添加到处理链",
    "browser.badge.advanced": "高级版",
    "browser.badge.analysis": "仅输出分析",
    "browser.badge.learn": "支持学习",
    "browser.badge.ml": "使用模型",
    /* ---- inspector ------------------------------------------------- */
    "inspector.tabs": "检查器标签",
    "inspector.tab.module": "模块",
    "inspector.tab.chain": "处理链",
    "inspector.tab.analysis": "分析",
    "inspector.tab.batch": "批处理",
    "inspector.noModule": "尚未选择模块。请从浏览器添加，或点击处理链中的模块卡片。",
    "inspector.chainEmpty": "处理链为空。",
    "inspector.analysisEmpty": "暂无分析数据。打开音频文件后即可显示。",
    "inspector.asset": "素材",
    "inspector.clearChain": "清空处理链",
    "inspector.bypassed": "已旁通",
    "inspector.active": "已启用",
    "inspector.tab.presets": "预设",
    "inspector.presetsEmpty": "预设库不可用。",
    /* ---- chain strip ----------------------------------------------- */
    "chain.label": "信号链",
    "chain.empty": "暂无模块 — 请从浏览器添加",
    "chain.bypass": "旁通模块",
    "chain.enable": "启用模块",
    "chain.remove": "从处理链移除",
    "chain.reorder": "拖动以重新排序",
    "chain.advanced": "高级版",
    /* ---- status bar ------------------------------------------------ */
    "status.cursor": "光标位置",
    "status.noSelection": "无选区",
    "status.selection": "选区",
    "status.latency": "延迟 {n} 采样",
    "status.level": "-inf dBFS",
    "status.engine.idle": "空闲",
    "status.engine.ready": "就绪",
    "status.engine.busy": "忙碌",
    "status.engine.error": "错误",
    "status.engine.rendering": "渲染中",
    "status.engine.playing": "播放中",
    /* ---- 预览的路径 ------------------------------------------------ */
    "status.path.label": "预览路径",
    "status.path.title": "正在播放的内容及该路径的改动",
    "status.path.titleIdle": "下一次预览将使用的路径",
    "status.path.playing": "播放中",
    "status.path.idle": "没有正在播放的预览",
    "status.path.unknown": "服务器尚未报告路径。",
    "status.path.fast": "预览：快速路径",
    "status.path.full": "预览：完整路径",
    "status.path.other": "预览：{name}",
    "status.path.render": "渲染：文件路径",
    "status.path.mode": "模式",
    "status.path.block": "块",
    "status.path.latency": "延迟",
    "status.path.latencyValue": "{n} 采样（{ms} 毫秒）",
    "status.path.changed": "该路径改动了 {n} 个参数：",
    "status.path.unchanged": "该路径没有改动任何参数——预览使用您设置的数值。",
    "status.path.refused": "{name} 无法运行廉价配置——预览保留您的设置。",
    "status.path.file": "文件",
    "status.path.preview": "预览",
    "status.path.choose": "预览路径",
    "status.path.useFast": "快速路径",
    "status.path.useFull": "完整路径",
    "status.path.restart": "预览会在您选择的路径上重新开始。",
    "status.latency.unknown": "延迟 — 采样",
    /* ---- the canvases ---------------------------------------------- */
    "canvas.summary": "{name}, {duration} s, {rate} Hz, {channels} ch, 峰值 {peak} dBFS",
    /* ---- common control labels ------------------------------------- */
    "control.bypass": "旁通",
    "control.enable": "启用",
    "control.learn": "学习",
    "control.learn.title": "从当前信号学习",
    "control.compare": "对比",
    "control.compare.title": "与原始信号对比",
    "control.reset": "恢复默认值",
    "control.savePreset": "保存预设…",
    "control.loadPreset": "载入预设…",
    "control.removeModule": "移除模块",
    "control.moduleMenu": "模块菜单",
    "control.preset": "预设",
    "control.default": "默认",
    "control.modified": "{name}（已修改）",
    "control.close": "关闭",
    "control.closeDialog": "关闭对话框",
    "control.cancel": "取消",
    "control.confirm": "确认",
    "control.yes": "是",
    "control.no": "否",
    "control.save": "保存",
    "control.download": "下载 WAV",
    "control.zoomIn": "放大",
    "control.zoomOut": "缩小",
    "control.spectralEditor": "频谱编辑器",
    "control.fit": "适配",
    "control.frequencySelection": "频率选区",
    "control.timeSelection": "时间选区",
    "control.logScale": "对数",
    "control.frequency": "频率",
    "control.hertz": "赫兹",
    "control.on": "开",
    "control.off": "关",
    "control.none": "无",
    "control.freqLo": "低频截止",
    "control.freqHi": "高频截止",
    /* ---- dialogs --------------------------------------------------- */
    "dialog.loadPreset": "载入预设",
    "dialog.savePreset": "保存预设 — {name}",
    "dialog.presetName": "预设名称",
    "dialog.presetCategory": "类别",
    "dialog.noPresets": "尚未保存任何预设。",
    "dialog.loading": "载入中…",
    "dialog.renderComplete": "渲染完成",
    "dialog.compareChains": "对比处理链",
    "dialog.processed": "已处理（当前处理链）",
    "dialog.original": "原始信号",
    /* ---- keyboard help --------------------------------------------- */
    "help.title": "键盘快捷键",
    "help.close": "关闭键盘快捷键",
    "help.column.key": "按键",
    "help.column.action": "操作",
    "help.column.context": "作用范围",
    "help.context.global": "全局",
    "help.context.view": "视图",
    "help.context.panel": "检查器",
    "help.context.browser": "浏览器",
    /* ---- keyboard commands ----------------------------------------- */
    "key.transport.preview.label": "试听",
    "key.transport.preview.description": "播放处理链输出的一小段试听。",
    "key.transport.stop.label": "停止",
    "key.transport.stop.description": "停止试听。",
    "key.transport.previewPath.label": "预览路径",
    "key.transport.previewPath.description": "在预览的快速路径与完整路径之间切换。",
    "key.module.bypass.label": "旁通模块",
    "key.module.bypass.description": "旁通或重新启用选中的模块。",
    "key.module.remove.label": "移除模块",
    "key.module.remove.description": "从处理链中移除选中的模块。",
    "key.chain.selectPrev.label": "上一个模块",
    "key.chain.selectPrev.description": "选中处理链中的上一个模块。",
    "key.chain.selectNext.label": "下一个模块",
    "key.chain.selectNext.description": "选中处理链中的下一个模块。",
    "key.preset.save.label": "保存预设",
    "key.preset.save.description": "将选中模块的设置保存为预设。",
    "key.dialog.close.label": "关闭对话框",
    "key.dialog.close.description": "关闭当前打开的对话框。",
    "key.help.toggle.label": "键盘帮助",
    "key.help.toggle.description": "显示或隐藏快捷键列表。",
    "key.browser.focusSearch.label": "聚焦模块搜索",
    "key.browser.focusSearch.description": "将光标移入模块搜索框。",
    "key.view.zoomIn.label": "放大",
    "key.view.zoomIn.description": "放大复合视图。",
    "key.view.zoomOut.label": "缩小",
    "key.view.zoomOut.description": "缩小复合视图。",
    "key.view.fit.label": "适配窗口",
    "key.view.fit.description": "将整个文件适配到视口中。",
    "key.view.reset.label": "重置视图",
    "key.view.reset.description": "重置缩放、滚动位置和选区。",
    "key.view.toggleFreqSelection.label": "频率选区",
    "key.view.toggleFreqSelection.description": "打开或关闭频率选区。",
    "key.view.toggleTimeSelection.label": "时间选区",
    "key.view.toggleTimeSelection.description": "打开或关闭时间选区。",
    "key.inspector.cycleTabs.label": "切换检查器标签",
    "key.inspector.cycleTabs.description": "在“模块”“处理链”“分析”标签之间切换。",
    "key.panel.presetMenu.label": "模块菜单",
    "key.panel.presetMenu.description": "打开模块菜单（预设、重置、移除）。",
    "key.browser.addModule.label": "添加模块",
    "key.browser.addModule.description": "将选中的模块添加到处理链。",
    "key.chain.movePrev.label": "向前移动模块",
    "key.chain.movePrev.description": "将选中的模块在处理链中向前移动一位。",
    "key.chain.moveNext.label": "向后移动模块",
    "key.chain.moveNext.description": "将选中的模块在处理链中向后移动一位。",
    "key.session.undo.label": "撤销",
    "key.session.undo.description": "撤销处理链上最近的一次更改。",
    "key.session.redo.label": "重做",
    "key.session.redo.description": "重做上一次撤销的更改。",
    /* ---- messages -------------------------------------------------- */
    "toast.saved": "预设已保存",
    "toast.cleared": "处理链已清空",
    "toast.reordered": "处理链顺序已更改",
    "toast.selectFirst": "请先选择一个模块",
    "toast.actionFailed": "操作失败",
    /* ---- edition tags ---------------------------------------------- */
    "edition.advanced": "ADV",
    /* ---- module inspector panel ------------------------------------ */
    "panel.module": "模块",
    "panel.bypassModule": "旁通 {name}",
    "panel.enableModule": "启用 {name}",
    "panel.presetTitle": "预设：{name}",
    "panel.analysis": "分析",
    "panel.groupGeneral": "常规",
    /* ---- control library ------------------------------------------- */
    "widgets.parameter": "参数",
    /* ---- batch processor ------------------------------------------- */
    "batch.title": "批处理处理器",
    "batch.modeChain": "当前处理链",
    "batch.modePreset": "交付预设",
    "batch.sectionDelivery": "交付",
    "batch.labelTarget": "目标",
    "batch.ariaTarget": "交付目标",
    "batch.sectionChain": "处理链",
    "batch.sectionOutput": "输出",
    "batch.labelFormat": "格式",
    "batch.ariaFormat": "输出格式",
    "batch.labelBitDepth": "位深",
    "batch.ariaBitDepth": "输出位深",
    "batch.labelFolder": "文件夹",
    "batch.placeholderFolder": "服务器默认",
    "batch.ariaFolder": "输出文件夹",
    "batch.sectionFiles": "文件",
    "batch.dropHint": "将音频文件拖到此处，或拖入服务器路径列表",
    "batch.addFiles": "添加文件",
    "batch.placeholderPath": "单个文件的服务器路径",
    "batch.ariaPath": "服务器路径",
    "batch.addPath": "添加路径",
    "batch.clearList": "清空列表",
    "batch.ariaFiles": "选择音频文件",
    "batch.sectionProgress": "进度",
    "batch.start": "开始批处理",
    "batch.stop": "停止",
    "batch.errorNoFiles": "请至少向批处理中添加一个文件。",
    "batch.noTargets": "没有交付目标",
    "batch.factsChainOnly": "会话的处理链将原样发送",
    "batch.chainEmpty": "处理链为空 — 仅发送交付处理阶段。",
    "batch.stageLoudness": "响度控制",
    "batch.stageDither": "抖动",
    "batch.noFiles": "尚无文件。",
    "batch.removeTitle": "从批处理中移除",
    "batch.uploadingFiles": "仍有 {n} 个文件正在上传",
    "batch.stagedLimit": "服务器保留 {n} 个已上传文件 — 请分批运行",
    "batch.noProgress": "尚未处理任何文件。",
    "batch.countDone": "已完成",
    "batch.countFailed": "失败",
    "batch.countTotal": "总计",
    "batch.measuredLoudness": "实测响度 {value} LUFS（{n} 个文件）",
    "batch.noLoudness": "服务器未报告响度测量结果",
    "batch.targetCount": "{n} 个交付目标",
    "batch.noCatalogue": "未提供交付目录",
    "batch.jobRunning": "作业 {id} 正在运行",
    "batch.fileCount": "列表中有 {n} 个文件",
    "batch.bit": "{n} 位",
    "batch.fileName": "文件 {n}",
    "batch.errorNoAssetId": "上传未返回素材 ID",
    "batch.state.idle": "空闲",
    "batch.state.queued": "已排队",
    "batch.state.running": "运行中",
    "batch.state.staging": "上传中",
    "batch.state.staged": "已就绪",
    "batch.state.done": "已完成",
    "batch.state.failed": "失败",
    "batch.state.error": "错误",
    "batch.state.cancelled": "已取消",
    /* ---- analysis tab ---------------------------------------------- */
    "analysis.empty": "暂无测量数据。打开音频文件后即可显示。",
    "analysis.channels": "{n} 声道",
    "analysis.selectionTitle": "选区",
    "analysis.range": "范围",
    "analysis.length": "长度",
    "analysis.samples": "采样数",
    "analysis.wholeFile": "整个文件",
    "analysis.selectionEmpty": "选区为空 — 没有可测量的内容。",
    "analysis.selectionClamped": "请求 {list} 超出音频范围；测得的范围为上方所示。",
    "analysis.noSpectrum": "无频谱",
    "analysis.noHistogram": "无直方图",
    "analysis.spectrumRange": "幅度频谱，{min} 至 {max}",
    "analysis.spectrum": "频谱",
    "analysis.peak": "峰值 {value} @ {hz} Hz",
    "analysis.peakNone": "峰值 —",
    "analysis.loudness": "响度",
    "analysis.integrated": "整体",
    "analysis.shortTerm": "短时",
    "analysis.momentary": "瞬时",
    "analysis.loudnessRange": "响度范围",
    "analysis.truePeak": "真实峰值",
    "analysis.waveformStats": "波形统计",
    "analysis.samplePeak": "采样峰值",
    "analysis.maximum": "最大值",
    "analysis.minimum": "最小值",
    "analysis.mean": "平均值",
    "analysis.rms": "RMS",
    "analysis.totalRms": "整体 RMS",
    "analysis.dcOffset": "直流偏移",
    "analysis.possiblyClipped": "可能已削波",
    "analysis.linearAmplitude": "线性幅度",
    "analysis.linearValue": "线性 {value}",
    "analysis.clipThresholdTitle": "达到或超过削波阈值的采样数",
    "analysis.correlation": "相关性",
    "analysis.correlationInverted": "−1（反相）",
    "analysis.correlationIdentical": "+1（相同）",
    "analysis.peakHistogram": "峰值直方图",
    "analysis.clippedCount": "{n} 个已削波",
    "analysis.clipThreshold": "削波 ≥ {value}",
    /* ---- spectral editor ------------------------------------------- */
    "spectral.regionLabel": "频谱编辑器选区",
    "spectral.mode": "模式",
    "spectral.strength": "强度",
    "spectral.newRegion": "新建选区",
    "spectral.noRegion": "尚无选区 — 在视图中拖动以选择",
    "spectral.applied": "已生效",
    "spectral.notApplied": "未生效",
    "spectral.mode.attenuate": "衰减",
    "spectral.mode.replace": "替换",
    "spectral.mode.gain": "增益",
    "spectral.mode.pattern": "图案",
    "spectral.mode.partials": "分音",
    "spectral.status.applied": "该选区已应用到模块 {index}",
    "spectral.status.appliedToModule": "该选区已应用到模块",
    "spectral.status.pending": "正在将选区发送到模块…",
    "spectral.status.missing": "该选区未生效 — 服务器尚无处理选区的路由",
    "spectral.status.noModule": "该选区未生效 — 处理链中没有频谱编辑器",
    "spectral.status.failed": "该选区未生效 — {reason}",
    "spectral.status.refused": "服务器拒绝了该请求",
    "spectral.status.notSent": "该选区尚未发送到模块",
    "spectral.status.holds": "模块当前值为 {value}",
    "spectral.stale": "{value}（模块仍保存着旧值）",
    /* ---- preset library -------------------------------------------- */
    "preset.search": "搜索预设",
    "preset.category": "分类",
    "preset.saveTitle": "以此名称保存预设",
    "preset.noMatch": "没有匹配“{query}”的预设",
    "preset.loadLabel": "载入预设 {name}",
    "preset.menu.save": "保存",
    "preset.menu.saveAs": "另存为",
    "preset.menu.rename": "重命名",
    "preset.menu.remove": "移除",
    "preset.menu.reload": "重新载入",
    "preset.menu.import": "导入",
    "preset.menu.export": "导出",
    "preset.errorUnavailable": "预设库不可用。",
    "preset.errorName": "预设需要一个名称。",
    "preset.errorModule": "预设需要一个模块 — 请先选择一个。",
    "preset.errorPath": "预设的{field}不能包含路径。",
    "preset.errorDot": "预设的{field}不能以点开头。",
    "preset.errorChars": "预设的{field}包含不允许的字符。",
    "preset.errorReserved": "预设名称“{name}”是保留的设备名称。",
    "preset.errorPathRequired": "需要预设路径。",
    "preset.errorNoExport": "没有可导出的预设。",
    "preset.errorUnreadable": "无法读取该文件。",
    "preset.errorUnreachable": "预设库无法访问。",
    "preset.errorSaveNotConfirmed": "服务器未确认保存 — 预设未写入。",
    "preset.errorRenameNotConfirmed": "服务器未确认重命名 — 预设未写入。",
    "preset.unsavedChanges": "存在未保存的更改。",
    "preset.unsavedIn": "“{name}”中存在未保存的更改。",
    "preset.unsavedTitle": "保存“{name}” — 存在未保存的更改",
    "preset.unsavedTitleNoName": "存在未保存的更改",
    "preset.count": "{n} 个预设。",
    "preset.errorNoSave": "尚无可保存的预设 — 请在表单中填写名称。",
    "preset.errorNoRename": "没有可重命名的预设。",
    "preset.errorNoRemove": "没有可移除的预设。",
    "preset.errorSavedListFailed": "预设已保存，但无法再次读取预设库 — {message}",
    "preset.removeConfirm": "移除预设“{name}”？",
    "preset.errorRenamedNotRemoved": "预设已重命名为“{name}”，但无法删除旧文件 — {message}",
    /* ---- session toolbar ------------------------------------------- */
    "session.label": "会话",
    "session.undo": "撤销",
    "session.undoTitle": "撤销上一步更改",
    "session.redo": "重做",
    "session.redoTitle": "重做被撤销的更改",
    "session.saveTitle": "保存会话",
    "session.open": "打开",
    "session.openTitle": "打开一个会话",
    "session.dirtyTitle": "会话有未保存的更改",
    "session.savedTitle": "会话已保存",
    "session.actionDefault": "操作",
    "session.actionAdd": "添加 {name}",
    "session.actionRemove": "移除 {name}",
    "session.actionReorder": "重新排序 {name}",
    "session.actionBypass": "旁通 {name}",
    "session.actionEnable": "启用 {name}",
    "session.actionChange": "更改 {name}",
    "session.actionClearChain": "清空处理链",
    "session.actionOpen": "打开 {name}",
    "session.actionNew": "新建会话",
    "session.hintUndo": "撤销：{name}",
    "session.hintRedo": "重做：{name}",
    "session.hintLastChange": "上一步更改",
    "session.hintNoHistory": "没有历史记录",
    "session.titleUndo": "撤销 {name}",
    "session.titleRedo": "重做 {name}",
    "session.titleNothingUndo": "没有可撤销的操作",
    "session.titleNothingRedo": "没有可重做的操作",
    "session.titleLastAction": "上一步操作：{name}",
    "session.titleNoAction": "尚无操作",
    /* ---- composite view -------------------------------------------- */
    "view.empty": "请打开音频文件以开始",
  },
  "de": {
    /* ---- application ----------------------------------------------- */
    "app.name": "Auralis",
    "app.tagline": "Audio-Restaurierungswerkbank",
    /* ---- top bar --------------------------------------------------- */
    "topbar.open": "Öffnen",
    "topbar.open.title": "Eine Audiodatei öffnen",
    "topbar.noFile": "Keine Datei geladen",
    "topbar.compare": "Vergleichen",
    "topbar.compare.title": "Zwei Ketten vergleichen",
    "topbar.render": "Rendern",
    "topbar.render.title": "Rendern und exportieren",
    "topbar.learned.title": "Gelernte Stufe des Renders",
    "topbar.learned": "Gelernte Stufe",
    "topbar.learned.none": "Keine gelernte Stufe",
    "topbar.viewMode": "Ansichtsmodus",
    "topbar.viewMode.composite": "Komposit",
    "topbar.viewMode.waveform": "Wellenform",
    "topbar.viewMode.spectrogram": "Spektrogramm",
    "topbar.language": "Sprache",
    "topbar.contrast": "Hoher Kontrast",
    "topbar.contrast.title": "Die Oberfläche auf die kontrastreiche Variante umstellen",
    /* ---- transport ------------------------------------------------- */
    "transport.preview": "Vorschau",
    "transport.preview.title": "Vorschau (Leertaste)",
    "transport.stop": "Stopp",
    "transport.stop.title": "Vorschau anhalten",
    "transport.loop": "Vorschau wiederholen",
    "transport.loop.on": "Vorschauwiederholung aktiv",
    "transport.loop.off": "Vorschauwiederholung inaktiv",
    "transport.live": "Live-Vorschau",
    "transport.live.title": "Live-Vorschau über den Streaming-Socket",
    "transport.live.on": "Live-Vorschau aktiviert",
    "transport.live.off": "Live-Vorschau deaktiviert",
    "transport.live.unavailable": "Die Live-Vorschau ist nicht verfügbar",
    /* ---- module browser -------------------------------------------- */
    "browser.search": "Module suchen",
    "browser.search.title": "Modulliste nach Namen filtern",
    "browser.category.repair": "Reparatur",
    "browser.category.utility": "Werkzeug",
    "browser.category.measurement": "Messung",
    "browser.category.system": "System",
    "browser.category.other": "Module",
    "browser.empty": "Keine Module verfügbar",
    "browser.noMatch": "Keine Module für „{query}“ gefunden",
    "browser.add": "Zur Kette hinzufügen",
    "browser.badge.advanced": "Erweiterte Edition",
    "browser.badge.analysis": "Analysausgabe",
    "browser.badge.learn": "Unterstützt Lernen",
    "browser.badge.ml": "Verwendet ein Modell",
    /* ---- inspector ------------------------------------------------- */
    "inspector.tabs": "Inspektor-Reiter",
    "inspector.tab.module": "Modul",
    "inspector.tab.chain": "Kette",
    "inspector.tab.analysis": "Analyse",
    "inspector.tab.batch": "Stapel",
    "inspector.noModule": "Kein Modul ausgewählt. Fügen Sie eines aus dem Browser hinzu oder klicken Sie auf eine Kachel in der Kettenleiste.",
    "inspector.chainEmpty": "Die Kette ist leer.",
    "inspector.analysisEmpty": "Noch keine Analyse. Öffnen Sie eine Audiodatei, um diesen Reiter zu füllen.",
    "inspector.asset": "Material",
    "inspector.clearChain": "Kette leeren",
    "inspector.bypassed": "überbrückt",
    "inspector.active": "aktiv",
    "inspector.tab.presets": "Voreinstellungen",
    "inspector.presetsEmpty": "Die Bibliothek der Voreinstellungen ist nicht verfügbar.",
    /* ---- chain strip ----------------------------------------------- */
    "chain.label": "Signalkette",
    "chain.empty": "Keine Module — fügen Sie eines aus dem Browser hinzu",
    "chain.bypass": "Modul überbrücken",
    "chain.enable": "Modul aktivieren",
    "chain.remove": "Aus der Kette entfernen",
    "chain.reorder": "Zum Umsortieren ziehen",
    "chain.advanced": "Erweiterte Edition",
    /* ---- status bar ------------------------------------------------ */
    "status.cursor": "Cursorposition",
    "status.noSelection": "keine Auswahl",
    "status.selection": "Auswahl",
    "status.latency": "Latenz {n} Smp",
    "status.level": "-inf dBFS",
    "status.engine.idle": "inaktiv",
    "status.engine.ready": "bereit",
    "status.engine.busy": "beschäftigt",
    "status.engine.error": "Fehler",
    "status.engine.rendering": "rendert",
    "status.engine.playing": "Wiedergabe",
    /* ---- der Pfad der Vorschau ------------------------------------- */
    "status.path.label": "Der Pfad der Vorschau",
    "status.path.title": "Der abgespielte Pfad und die von ihm geänderten Werte",
    "status.path.titleIdle": "Der Pfad, den die nächste Vorschau verwenden wird",
    "status.path.playing": "Wiedergabe",
    "status.path.idle": "Keine Vorschau läuft",
    "status.path.unknown": "Der Server hat noch keinen Pfad gemeldet.",
    "status.path.fast": "Vorschau: schneller Pfad",
    "status.path.full": "Vorschau: vollständiger Pfad",
    "status.path.other": "Vorschau: {name}",
    "status.path.render": "Rendern: Dateipfad",
    "status.path.mode": "Modus",
    "status.path.block": "Blockgröße",
    "status.path.latency": "Latenz",
    "status.path.latencyValue": "{n} Smp, {ms} ms",
    "status.path.changed": "Der Pfad hat {n} Parameter umgestellt:",
    "status.path.unchanged": "Der Pfad hat keinen Parameter umgestellt — die Vorschau verwendet Ihre Werte.",
    "status.path.refused": "Die günstige Konfiguration läuft in {name} nicht — die Vorschau behält Ihren Wert.",
    "status.path.file": "Datei",
    "status.path.preview": "Vorschau",
    "status.path.choose": "Der Pfad der Vorschau",
    "status.path.useFast": "Schneller Pfad",
    "status.path.useFull": "Vollständiger Pfad",
    "status.path.restart": "Die Vorschau beginnt auf dem gewählten Pfad erneut.",
    "status.latency.unknown": "Latenz — Smp",
    /* ---- the canvases ---------------------------------------------- */
    "canvas.summary": "{name}, {duration} s, {rate} Hz, {channels} ch, Spitze {peak} dBFS",
    /* ---- common control labels ------------------------------------- */
    "control.bypass": "Überbrücken",
    "control.enable": "Aktivieren",
    "control.learn": "Lernen",
    "control.learn.title": "Aus dem aktuellen Signal lernen",
    "control.compare": "Vergleichen",
    "control.compare.title": "Mit dem Originalsignal vergleichen",
    "control.reset": "Auf Standard zurücksetzen",
    "control.savePreset": "Voreinstellung speichern…",
    "control.loadPreset": "Voreinstellung laden…",
    "control.removeModule": "Modul entfernen",
    "control.moduleMenu": "Modulmenü",
    "control.preset": "Voreinstellung",
    "control.default": "Standard",
    "control.modified": "{name} (geändert)",
    "control.close": "Schließen",
    "control.closeDialog": "Dialog schließen",
    "control.cancel": "Abbrechen",
    "control.confirm": "Bestätigen",
    "control.yes": "Ja",
    "control.no": "Nein",
    "control.save": "Speichern",
    "control.download": "WAV herunterladen",
    "control.zoomIn": "Vergrößern",
    "control.zoomOut": "Verkleinern",
    "control.spectralEditor": "Spektraleditor",
    "control.fit": "Einpassen",
    "control.frequencySelection": "Frequenzauswahl",
    "control.timeSelection": "Zeitauswahl",
    "control.logScale": "log",
    "control.frequency": "Frequenz",
    "control.hertz": "Hz",
    "control.on": "Ein",
    "control.off": "Aus",
    "control.none": "Keine",
    "control.freqLo": "Untere Grenzfrequenz",
    "control.freqHi": "Obere Grenzfrequenz",
    /* ---- dialogs --------------------------------------------------- */
    "dialog.loadPreset": "Voreinstellung laden",
    "dialog.savePreset": "Voreinstellung speichern — {name}",
    "dialog.presetName": "Name der Voreinstellung",
    "dialog.presetCategory": "Kategorie",
    "dialog.noPresets": "Noch keine Voreinstellungen gespeichert.",
    "dialog.loading": "Wird geladen…",
    "dialog.renderComplete": "Rendern abgeschlossen",
    "dialog.compareChains": "Ketten vergleichen",
    "dialog.processed": "Verarbeitet (aktuelle Kette)",
    "dialog.original": "Original (trocken)",
    /* ---- keyboard help --------------------------------------------- */
    "help.title": "Tastenkürzel",
    "help.close": "Tastenkürzel schließen",
    "help.column.key": "Taste",
    "help.column.action": "Aktion",
    "help.column.context": "Bereich",
    "help.context.global": "Global",
    "help.context.view": "Ansicht",
    "help.context.panel": "Inspektor",
    "help.context.browser": "Modulbrowser",
    /* ---- keyboard commands ----------------------------------------- */
    "key.transport.preview.label": "Vorschau",
    "key.transport.preview.description": "Eine kurze Vorschau der Kettenausgabe abspielen.",
    "key.transport.stop.label": "Stopp",
    "key.transport.stop.description": "Die Vorschau anhalten.",
    "key.transport.previewPath.label": "Vorschaupfad",
    "key.transport.previewPath.description": "Zwischen dem schnellen und dem vollständigen Pfad der Vorschau wechseln.",
    "key.module.bypass.label": "Modul überbrücken",
    "key.module.bypass.description": "Das ausgewählte Modul überbrücken oder wieder aktivieren.",
    "key.module.remove.label": "Modul entfernen",
    "key.module.remove.description": "Das ausgewählte Modul aus der Kette entfernen.",
    "key.chain.selectPrev.label": "Vorheriges Modul",
    "key.chain.selectPrev.description": "Das vorherige Modul in der Kette auswählen.",
    "key.chain.selectNext.label": "Nächstes Modul",
    "key.chain.selectNext.description": "Das nächste Modul in der Kette auswählen.",
    "key.preset.save.label": "Voreinstellung speichern",
    "key.preset.save.description": "Die Einstellungen des ausgewählten Moduls als Voreinstellung speichern.",
    "key.dialog.close.label": "Dialog schließen",
    "key.dialog.close.description": "Den geöffneten Dialog schließen.",
    "key.help.toggle.label": "Tastaturhilfe",
    "key.help.toggle.description": "Diese Liste der Tastenkürzel ein- oder ausblenden.",
    "key.browser.focusSearch.label": "Modulsuche fokussieren",
    "key.browser.focusSearch.description": "Den Cursor in das Suchfeld für Module setzen.",
    "key.view.zoomIn.label": "Vergrößern",
    "key.view.zoomIn.description": "Die Kompositansicht vergrößern.",
    "key.view.zoomOut.label": "Verkleinern",
    "key.view.zoomOut.description": "Die Kompositansicht verkleinern.",
    "key.view.fit.label": "An Fenster anpassen",
    "key.view.fit.description": "Die gesamte Datei in das Sichtfeld einpassen.",
    "key.view.reset.label": "Ansicht zurücksetzen",
    "key.view.reset.description": "Zoom, Scrollposition und Auswahl zurücksetzen.",
    "key.view.toggleFreqSelection.label": "Frequenzauswahl",
    "key.view.toggleFreqSelection.description": "Die Frequenzauswahl ein- oder ausschalten.",
    "key.view.toggleTimeSelection.label": "Zeitauswahl",
    "key.view.toggleTimeSelection.description": "Die Zeitauswahl ein- oder ausschalten.",
    "key.inspector.cycleTabs.label": "Inspektor-Reiter wechseln",
    "key.inspector.cycleTabs.description": "Durch die Reiter Modul, Kette und Analyse blättern.",
    "key.panel.presetMenu.label": "Modulmenü",
    "key.panel.presetMenu.description": "Die Modulmenü öffnen (Voreinstellung, Zurücksetzen, Entfernen).",
    "key.browser.addModule.label": "Modul hinzufügen",
    "key.browser.addModule.description": "Das markierte Modul zur Kette hinzufügen.",
    "key.chain.movePrev.label": "Modul nach vorne",
    "key.chain.movePrev.description": "Das ausgewählte Modul in der Kette eine Stelle nach vorne verschieben.",
    "key.chain.moveNext.label": "Modul nach hinten",
    "key.chain.moveNext.description": "Das ausgewählte Modul in der Kette eine Stelle nach hinten verschieben.",
    "key.session.undo.label": "Rückgängig",
    "key.session.undo.description": "Die letzte Änderung der Kette rückgängig machen.",
    "key.session.redo.label": "Wiederholen",
    "key.session.redo.description": "Die zuletzt rückgängig gemachte Änderung wiederholen.",
    /* ---- messages -------------------------------------------------- */
    "toast.saved": "Voreinstellung gespeichert",
    "toast.cleared": "Kette geleert",
    "toast.reordered": "Kette neu sortiert",
    "toast.selectFirst": "Wählen Sie zuerst ein Modul aus",
    "toast.actionFailed": "Die Aktion ist fehlgeschlagen",
    /* ---- edition tags ---------------------------------------------- */
    "edition.advanced": "ADV",
    /* ---- module inspector panel ------------------------------------ */
    "panel.module": "Modul",
    "panel.bypassModule": "{name} überbrücken",
    "panel.enableModule": "{name} aktivieren",
    "panel.presetTitle": "Voreinstellung: {name}",
    "panel.analysis": "Analyse",
    "panel.groupGeneral": "Allgemein",
    /* ---- control library ------------------------------------------- */
    "widgets.parameter": "Parameter",
    /* ---- batch processor ------------------------------------------- */
    "batch.title": "Stapelverarbeitung",
    "batch.modeChain": "Aktuelle Kette",
    "batch.modePreset": "Auslieferungs-Voreinstellung",
    "batch.sectionDelivery": "Auslieferung",
    "batch.labelTarget": "Ziel",
    "batch.ariaTarget": "Auslieferungsziel",
    "batch.sectionChain": "Kette",
    "batch.sectionOutput": "Ausgabe",
    "batch.labelFormat": "Dateiformat",
    "batch.ariaFormat": "Ausgabeformat",
    "batch.labelBitDepth": "Bittiefe",
    "batch.ariaBitDepth": "Ausgabe-Bittiefe",
    "batch.labelFolder": "Ordner",
    "batch.placeholderFolder": "Serverstandard",
    "batch.ariaFolder": "Ausgabeordner",
    "batch.sectionFiles": "Dateien",
    "batch.dropHint": "Audio-Dateien hierher ziehen oder eine Liste von Serverpfaden ablegen",
    "batch.addFiles": "Dateien hinzufügen",
    "batch.placeholderPath": "Serverpfad einer Datei",
    "batch.ariaPath": "Serverpfad",
    "batch.addPath": "Pfad hinzufügen",
    "batch.clearList": "Liste leeren",
    "batch.ariaFiles": "Audiodateien auswählen",
    "batch.sectionProgress": "Fortschritt",
    "batch.start": "Stapel starten",
    "batch.stop": "Anhalten",
    "batch.errorNoFiles": "Fügen Sie dem Stapel mindestens eine Datei hinzu.",
    "batch.noTargets": "keine Auslieferungsziele",
    "batch.factsChainOnly": "Die Kette der Sitzung wird unverändert gesendet",
    "batch.chainEmpty": "Die Kette ist leer — es werden nur die Auslieferungsstufen gesendet.",
    "batch.stageLoudness": "Lautheitsregelung",
    "batch.stageDither": "Dither",
    "batch.noFiles": "Noch keine Dateien.",
    "batch.removeTitle": "Aus dem Stapel entfernen",
    "batch.uploadingFiles": "{n} Datei(en) werden noch hochgeladen",
    "batch.stagedLimit": "Der Server behält {n} hochgeladene Dateien — den Stapel in kleineren Teilen ausführen",
    "batch.noProgress": "Noch wurde keine Datei verarbeitet.",
    "batch.countDone": "fertig",
    "batch.countFailed": "fehlgeschlagen",
    "batch.countTotal": "gesamt",
    "batch.measuredLoudness": "gemessene Lautheit {value} LUFS ({n} Datei(en))",
    "batch.noLoudness": "Der Server hat keine Lautheitsmessung gemeldet",
    "batch.targetCount": "{n} Auslieferungsziel(e)",
    "batch.noCatalogue": "Es wurde kein Auslieferungskatalog übergeben",
    "batch.jobRunning": "Auftrag {id} läuft",
    "batch.fileCount": "{n} Datei(en) in der Liste",
    "batch.bit": "{n}-Bit",
    "batch.fileName": "Datei {n}",
    "batch.errorNoAssetId": "Der Upload hat keine Materialkennung geliefert",
    "batch.state.idle": "inaktiv",
    "batch.state.queued": "in der Warteschlange",
    "batch.state.running": "läuft",
    "batch.state.staging": "wird geladen",
    "batch.state.staged": "bereitgestellt",
    "batch.state.done": "fertig",
    "batch.state.failed": "fehlgeschlagen",
    "batch.state.error": "Fehler",
    "batch.state.cancelled": "abgebrochen",
    /* ---- analysis tab ---------------------------------------------- */
    "analysis.empty": "Noch keine Messung. Öffnen Sie eine Audiodatei, um diesen Reiter zu füllen.",
    "analysis.channels": "{n} Kanäle",
    "analysis.selectionTitle": "Auswahl",
    "analysis.range": "Bereich",
    "analysis.length": "Länge",
    "analysis.samples": "Abtastwerte",
    "analysis.wholeFile": "die ganze Datei",
    "analysis.selectionEmpty": "Die Auswahl ist leer — es gibt nichts zu messen.",
    "analysis.selectionClamped": "Die Anfrage {list} liegt außerhalb des Audios; der gemessene Bereich ist der oben angegebene.",
    "analysis.noSpectrum": "Kein Spektrum",
    "analysis.noHistogram": "Kein Histogramm",
    "analysis.spectrumRange": "Betragsspektrum, {min} bis {max}",
    "analysis.spectrum": "Spektrum",
    "analysis.peak": "Spitze {value} @ {hz} Hz",
    "analysis.peakNone": "Spitze —",
    "analysis.loudness": "Lautheit",
    "analysis.integrated": "Integriert",
    "analysis.shortTerm": "Kurzzeit",
    "analysis.momentary": "Momentan",
    "analysis.loudnessRange": "Lautheitsbereich",
    "analysis.truePeak": "Wahrer Spitzenwert",
    "analysis.waveformStats": "Wellenformstatistik",
    "analysis.samplePeak": "Abtastspitzenwert",
    "analysis.maximum": "Höchstwert",
    "analysis.minimum": "Tiefstwert",
    "analysis.mean": "Mittelwert",
    "analysis.rms": "RMS",
    "analysis.totalRms": "Gesamt-RMS",
    "analysis.dcOffset": "Gleichanteil",
    "analysis.possiblyClipped": "Möglicherweise übersteuert",
    "analysis.linearAmplitude": "lineare Amplitude",
    "analysis.linearValue": "linearer Wert {value}",
    "analysis.clipThresholdTitle": "Abtastwerte an oder über der Übersteuerungsschwelle",
    "analysis.correlation": "Korrelation",
    "analysis.correlationInverted": "−1 (invertiert)",
    "analysis.correlationIdentical": "+1 (identisch)",
    "analysis.peakHistogram": "Spitzenhistogramm",
    "analysis.clippedCount": "{n} übersteuert",
    "analysis.clipThreshold": "Übersteuern ≥ {value}",
    /* ---- spectral editor ------------------------------------------- */
    "spectral.regionLabel": "Bereich des Spektraleditors",
    "spectral.mode": "Modus",
    "spectral.strength": "Stärke",
    "spectral.newRegion": "Neuer Bereich",
    "spectral.noRegion": "kein Bereich — im Fenster eine Auswahl ziehen",
    "spectral.applied": "übernommen",
    "spectral.notApplied": "nicht übernommen",
    "spectral.mode.attenuate": "Dämpfen",
    "spectral.mode.replace": "Ersetzen",
    "spectral.mode.gain": "Verstärkung",
    "spectral.mode.pattern": "Muster",
    "spectral.mode.partials": "Teiltöne",
    "spectral.status.applied": "der Bereich ist auf Modul {index} angewendet",
    "spectral.status.appliedToModule": "der Bereich ist auf das Modul angewendet",
    "spectral.status.pending": "der Bereich wird an das Modul gesendet…",
    "spectral.status.missing": "der Bereich ist NICHT angewendet — der Server hat noch keine Route für den Bereich",
    "spectral.status.noModule": "der Bereich ist NICHT angewendet — die Kette enthält keinen Spektraleditor",
    "spectral.status.failed": "der Bereich ist NICHT angewendet — {reason}",
    "spectral.status.refused": "der Server hat ihn abgelehnt",
    "spectral.status.notSent": "der Bereich ist noch nicht an das Modul gesendet",
    "spectral.status.holds": "das Modul hält {value}",
    "spectral.stale": "{value} (das Modul hält noch den alten Wert)",
    /* ---- preset library -------------------------------------------- */
    "preset.search": "Voreinstellungen suchen",
    "preset.category": "Kategorie",
    "preset.saveTitle": "Die Voreinstellung unter diesem Namen speichern",
    "preset.noMatch": "Keine Voreinstellungen für „{query}“ gefunden",
    "preset.loadLabel": "Voreinstellung {name} laden",
    "preset.menu.save": "Speichern",
    "preset.menu.saveAs": "Speichern unter",
    "preset.menu.rename": "Umbenennen",
    "preset.menu.remove": "Entfernen",
    "preset.menu.reload": "Neu laden",
    "preset.menu.import": "Importieren",
    "preset.menu.export": "Exportieren",
    "preset.errorUnavailable": "Die Bibliothek der Voreinstellungen ist nicht verfügbar.",
    "preset.errorName": "Die Voreinstellung braucht einen Namen.",
    "preset.errorModule": "Die Voreinstellung braucht ein Modul — wählen Sie zuerst eines aus.",
    "preset.errorPath": "Das Feld {field} der Voreinstellung darf keinen Pfad enthalten.",
    "preset.errorDot": "Das Feld {field} der Voreinstellung darf nicht mit einem Punkt beginnen.",
    "preset.errorChars": "Das Feld {field} der Voreinstellung enthält unzulässige Zeichen.",
    "preset.errorReserved": "Der Name „{name}“ der Voreinstellung ist ein reservierter Gerätename.",
    "preset.errorPathRequired": "Ein Pfad der Voreinstellung wird gebraucht.",
    "preset.errorNoExport": "Es gibt keine Voreinstellung zum Exportieren.",
    "preset.errorUnreadable": "Die Datei konnte nicht gelesen werden.",
    "preset.errorUnreachable": "Die Bibliothek der Voreinstellungen ist nicht erreichbar.",
    "preset.errorSaveNotConfirmed": "Der Server hat das Speichern nicht bestätigt — die Voreinstellung wurde nicht geschrieben.",
    "preset.errorRenameNotConfirmed": "Der Server hat das Umbenennen nicht bestätigt — die Voreinstellung wurde nicht geschrieben.",
    "preset.unsavedChanges": "Es gibt ungespeicherte Änderungen.",
    "preset.unsavedIn": "Ungespeicherte Änderungen in „{name}“.",
    "preset.unsavedTitle": "„{name}“ speichern — es gibt ungespeicherte Änderungen",
    "preset.unsavedTitleNoName": "Es gibt ungespeicherte Änderungen",
    "preset.count": "{n} Voreinstellung(en).",
    "preset.errorNoSave": "Es gibt noch keine Voreinstellung zum Speichern — geben Sie ihr im Formular einen Namen.",
    "preset.errorNoRename": "Es gibt keine Voreinstellung zum Umbenennen.",
    "preset.errorNoRemove": "Es gibt keine Voreinstellung zum Entfernen.",
    "preset.errorSavedListFailed": "Die Voreinstellung wurde gespeichert, die Bibliothek konnte aber nicht erneut gelesen werden — {message}",
    "preset.removeConfirm": "Die Voreinstellung „{name}“ entfernen?",
    "preset.errorRenamedNotRemoved": "Die Voreinstellung wurde in „{name}“ umbenannt, die alte Datei konnte aber nicht entfernt werden — {message}",
    /* ---- session toolbar ------------------------------------------- */
    "session.label": "Sitzung",
    "session.undo": "Rückgängig",
    "session.undoTitle": "Die letzte Änderung rückgängig machen",
    "session.redo": "Wiederholen",
    "session.redoTitle": "Die rückgängig gemachte Änderung wiederholen",
    "session.saveTitle": "Die Sitzung speichern",
    "session.open": "Öffnen",
    "session.openTitle": "Eine Sitzung öffnen",
    "session.dirtyTitle": "Die Sitzung hat ungespeicherte Änderungen",
    "session.savedTitle": "Die Sitzung ist gespeichert",
    "session.actionDefault": "Aktion",
    "session.actionAdd": "{name} hinzufügen",
    "session.actionRemove": "{name} entfernen",
    "session.actionReorder": "{name} umsortieren",
    "session.actionBypass": "{name} überbrücken",
    "session.actionEnable": "{name} aktivieren",
    "session.actionChange": "{name} ändern",
    "session.actionClearChain": "Kette leeren",
    "session.actionOpen": "{name} öffnen",
    "session.actionNew": "Neue Sitzung",
    "session.hintUndo": "rückgängig: {name}",
    "session.hintRedo": "wiederholen: {name}",
    "session.hintLastChange": "die letzte Änderung",
    "session.hintNoHistory": "keine Historie",
    "session.titleUndo": "{name} rückgängig machen",
    "session.titleRedo": "{name} wiederholen",
    "session.titleNothingUndo": "Nichts rückgängig zu machen",
    "session.titleNothingRedo": "Nichts zu wiederholen",
    "session.titleLastAction": "Letzte Aktion: {name}",
    "session.titleNoAction": "Noch keine Aktion",
    /* ---- composite view -------------------------------------------- */
    "view.empty": "Laden Sie eine Audiodatei, um zu beginnen",
  },
};

/* --------------------------------------------------------------- catalogue */

/** The catalogue of a locale, or null when the locale is unknown. */
export function catalogue(code) {
  const key = String(code == null ? "" : code);
  return Object.prototype.hasOwnProperty.call(CATALOGUES, key) ? CATALOGUES[key] : null;
}

/** Every locale code that ships with the build. */
export function availableLocales() {
  return Object.keys(CATALOGUES);
}

/** The human readable name of a locale. */
export function localeName(code) {
  const key = String(code == null ? "" : code);
  if (LOCALE_NAMES[key]) return LOCALE_NAMES[key];
  const table = catalogue(key);
  return table && table["app.name"] ? table["app.name"] : key;
}

/**
 * Resolve a locale code to one of the shipped catalogues.
 *
 * Exact matches win; a bare language code ("zh") resolves to the first
 * catalogue that starts with it.  Returns null when nothing matches.
 */
export function resolveLocale(code) {
  const key = String(code == null ? "" : code).trim();
  if (!key) return null;
  if (Object.prototype.hasOwnProperty.call(CATALOGUES, key)) return key;
  const lower = key.toLowerCase();
  for (const code2 of Object.keys(CATALOGUES)) {
    if (code2.toLowerCase() === lower) return code2;
  }
  const lang = lower.split(/[-_]/)[0];
  for (const code2 of Object.keys(CATALOGUES)) {
    if (code2.toLowerCase().split(/[-_]/)[0] === lang) return code2;
  }
  return null;
}

/* ------------------------------------------------------------------ state */

let activeLocale = DEFAULT_LOCALE;
const listeners = [];

/** The active locale code. */
export function getLocale() {
  return activeLocale;
}

/**
 * Select the active locale.
 *
 * @param {string} code  a locale code such as "en" or "zh-CN"
 * @returns {boolean}    true when the locale was applied
 */
export function setLocale(code) {
  const resolved = resolveLocale(code);
  if (!resolved) return false;
  const changed = resolved !== activeLocale;
  activeLocale = resolved;
  if (typeof document !== "undefined" && document.documentElement) {
    try {
      document.documentElement.setAttribute("lang", resolved);
    } catch (_err) {
      /* the attribute is a nicety, never a requirement */
    }
  }
  if (changed) {
    for (const fn of listeners.slice(0)) {
      try {
        fn(resolved);
      } catch (_err) {
        /* a broken subscriber must not break the switch */
      }
    }
  }
  return true;
}

/**
 * Subscribe to locale changes.
 *
 * @param {Function} callback  called with the new locale code
 * @returns {Function}         an unsubscribe function
 */
export function onLocaleChange(callback) {
  if (typeof callback !== "function") return () => {};
  listeners.push(callback);
  return () => {
    const at = listeners.indexOf(callback);
    if (at >= 0) listeners.splice(at, 1);
  };
}

/* ------------------------------------------------------------- start-up */

/**
 * The locale the browser asks for.
 *
 * The list of preferred languages is walked in order; the first one that
 * resolves to a shipped catalogue wins.  Returns null when the browser asks
 * for nothing we ship.
 *
 * @param {object} [nav]  a `navigator` like object
 * @returns {string|null}
 */
export function detectLocale(nav) {
  const source =
    nav || (typeof navigator !== "undefined" && navigator ? navigator : null);
  if (!source) return null;
  const list = [];
  if (Array.isArray(source.languages)) list.push(...source.languages);
  else if (typeof source.languages === "string") list.push(source.languages);
  if (typeof source.language === "string") list.push(source.language);
  if (typeof source.userLanguage === "string") list.push(source.userLanguage);
  for (const code of list) {
    const resolved = resolveLocale(code);
    if (resolved) return resolved;
  }
  return null;
}

/**
 * Remember the locale so the next session starts in the same language.
 *
 * A browser that refuses to store (private mode, a disabled storage) is not
 * an error: the interface simply starts in the default locale again.
 *
 * @param {string} code
 * @param {Storage} [storage]  a `localStorage` like object
 * @returns {boolean} true when the locale was stored
 */
export function persistLocale(code, storage) {
  const target =
    storage ||
    (typeof localStorage !== "undefined" && localStorage ? localStorage : null);
  if (!target || typeof target.setItem !== "function") return false;
  try {
    target.setItem(LOCALE_STORAGE_KEY, String(code));
    return true;
  } catch (_err) {
    return false;
  }
}

/** Read the stored locale, or null when nothing is stored. */
export function storedLocale(storage) {
  const target =
    storage ||
    (typeof localStorage !== "undefined" && localStorage ? localStorage : null);
  if (!target || typeof target.getItem !== "function") return null;
  try {
    return target.getItem(LOCALE_STORAGE_KEY);
  } catch (_err) {
    return null;
  }
}

/**
 * Start the localisation up.
 *
 * The locale is chosen in this order: the stored one, the one the browser
 * asks for, the default.  The document is translated, the `lang` attribute is
 * set and every later change is stored.
 *
 * @param {object} [options]
 * @param {Storage} [options.storage]   where the choice is remembered
 * @param {object}   [options.navigator] the `navigator` used for detection
 * @param {Element|Document} [options.root]  the subtree that is translated
 * @returns {string} the locale that is now active
 */
export function initLocale(options = {}) {
  const opts = options && typeof options === "object" ? options : {};
  const stored = resolveLocale(opts.storage ? storedLocale(opts.storage) : storedLocale());
  const detected = detectLocale(opts.navigator);
  const chosen = stored || detected || DEFAULT_LOCALE;
  setLocale(chosen);
  const root = opts.root || (typeof document !== "undefined" ? document : null);
  translateDocument(root);
  onLocaleChange((code) => {
    persistLocale(code, opts.storage);
    translateDocument(root);
  });
  return getLocale();
}

/* -------------------------------------------------------------- lookup */

/** Replace `{name}` placeholders with the values of `vars`. */
function interpolate(text, vars) {
  if (!vars || typeof vars !== "object") return text;
  return String(text).replace(/\{([a-zA-Z0-9_]+)\}/g, (match, name) => {
    const value = vars[name];
    return value === undefined || value === null ? match : String(value);
  });
}

/**
 * Look up a string.
 *
 * The active locale is searched first, then English.  A key that exists in
 * neither catalogue falls back to `fallback` (when given) and finally to the
 * key itself — the key is never shown while a catalogue entry exists.
 *
 * @param {string} key      the catalogue key
 * @param {string} [fallback]  text used when the key is unknown
 * @param {object} [vars]   values for `{placeholder}` substitution
 * @returns {string}
 */
export function t(key, fallback, vars) {
  const k = String(key == null ? "" : key);
  if (!k) return typeof fallback === "string" ? fallback : "";
  let value;
  const active = catalogue(activeLocale);
  if (active && Object.prototype.hasOwnProperty.call(active, k)) value = active[k];
  if (value === undefined) {
    const base = catalogue(DEFAULT_LOCALE);
    if (base && Object.prototype.hasOwnProperty.call(base, k)) value = base[k];
  }
  if (value === undefined) {
    value = fallback === undefined || fallback === null ? k : String(fallback);
  }
  return interpolate(value, vars);
}

/** True when the key resolves in the active locale (not only in English). */
export function has(key) {
  const active = catalogue(activeLocale);
  return !!(active && Object.prototype.hasOwnProperty.call(active, String(key)));
}

/* ----------------------------------------------------------- the document */

/** The `data-i18n*` attributes that translateDocument() understands. */
const ATTRIBUTES = [
  ["data-i18n", "textContent"],
  ["data-i18n-title", "title"],
  ["data-i18n-aria", "aria-label"],
  ["data-i18n-placeholder", "placeholder"],
];

/**
 * Apply the catalogue to every element carrying a `data-i18n*` attribute.
 *
 * @param {Element|Document} [root]  the subtree to translate (default: the document)
 * @returns {number}                  the number of elements that were translated
 */
export function translateDocument(root) {
  const doc =
    root || (typeof document !== "undefined" ? document : null);
  if (!doc || typeof doc.querySelectorAll !== "function") return 0;
  let count = 0;
  for (const [attribute, target] of ATTRIBUTES) {
    const nodes = doc.querySelectorAll(`[${attribute}]`);
    for (const node of nodes) {
      const key = node.getAttribute(attribute);
      if (!key) continue;
      const text = t(key, node.getAttribute(target) || key);
      if (target === "textContent") node.textContent = text;
      else node.setAttribute(target, text);
      count += 1;
    }
  }
  return count;
}
