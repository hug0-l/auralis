"""Localisation and accessibility tests for the Auralis web interface.

What is checked
  1. every locale ships exactly the same key set, and the set covers the
     chrome — including every key the markup and the shell reference;
  2. **the catalogue is honest in both directions**: every key is used by a
     source of the interface, and every string a runtime module shows comes
     out of the catalogue — a dead key and an untranslated string are both
     failures;
  3. no key resolves to its own name in any locale;
  4. the German catalogue is a real translation: nothing is left in English
     except the terms that are intentionally identical, and every one of those
     carries a reason;
  5. the placeholders survive the translation and the substitution works;
  6. the locale is chosen sensibly: the language of the browser on the first
     run, the choice of the user afterwards, remembered between two sessions
     and the `lang` attribute of the document follows it;
  7. the accessibility audit (``qa/a11y/audit_a11y.py``) finds no control
     without an accessible name, no control that the keyboard cannot reach
     and no readout that is not a live region.

The tests need no browser, no server and no network: the catalogue is read out
of ``aurui/js/i18n.js`` and the markup out of ``aurui/index.html``.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

# --------------------------------------------------------------------------
# Locations.
# --------------------------------------------------------------------------

REPO = Path(__file__).resolve().parents[1]
UI_DIR = REPO / "aurui"
JS_DIR = UI_DIR / "js"
CSS_DIR = UI_DIR / "css"
INDEX_HTML = UI_DIR / "index.html"
I18N_JS = JS_DIR / "i18n.js"
KEYMAP_JS = JS_DIR / "keymap.js"
AUDIT_SCRIPT = REPO / "qa" / "a11y" / "audit_a11y.py"
PATCH_DIR = REPO / "var" / "patches"

#: The locale that has to be there besides the reference catalogue.
GERMAN = "de"

#: The reference locale.
REFERENCE = "en"

#: The number of keys the catalogue had when the localisation pass started.
#: The count may grow -- a new control needs a new string -- but it may never
#: shrink: a key that loses its translation is a control that goes blank.
MINIMUM_KEYS = 153

#: The parts of the interface the catalogue has to cover.
REQUIRED_GROUPS = (
    "app.",
    "topbar.",
    "transport.",
    "browser.",
    "inspector.",
    "chain.",
    "status.",
    "control.",
    "dialog.",
    "help.",
    "key.",
    "toast.",
    "edition.",
    "panel.",
    "widgets.",
    "batch.",
    "analysis.",
    "spectral.",
    "preset.",
    "session.",
    "view.",
)

#: The runtime modules that build their text themselves.
RUNTIME_MODULES = (
    "browser.js",
    "chain.js",
    "panel.js",
    "widgets.js",
    "batch.js",
    "analysis.js",
    "spectral_editor.js",
    "preset_manager.js",
    "session.js",
    "view.js",
)

#: German strings that are deliberately identical to the English ones.
#:
#: Every entry needs a reason: the list is the answer to "why is this still
#: English?", and an entry without a reason is a translation that was forgotten.
IDENTICAL_IN_GERMAN: dict[str, str] = {
    "app.name": "Auralis is the product name; a brand name is never translated.",
    "browser.category.system": "'System' is the German word as well, so the category keeps its "
    "name.",
    "control.hertz": "'Hz' is the international SI symbol and is written the same everywhere.",
    "control.logScale": "'log' is the usual abbreviation for the logarithmic scale in an audio "
    "tool.",
    "help.context.global": "'Global' is the German word as well.",
    "status.level": "A technical readout in dBFS; the unit notation is not translated.",
    "edition.advanced": "The edition tag is an abbreviation that reads the same in every language.",
    "widgets.parameter": "'Parameter' is the German word as well.",
    "batch.stageDither": "'Dither' is the German word for the very same process.",
    "analysis.rms": "'RMS' is the international abbreviation of root mean square and is used "
    "unchanged.",
}

#: English words that never occur in a German sentence.  They are used to
#: catch a string that was copied over instead of translated.
ENGLISH_ONLY_WORDS = {
    "after",
    "against",
    "all",
    "also",
    "another",
    "any",
    "are",
    "because",
    "before",
    "between",
    "but",
    "can",
    "cannot",
    "could",
    "current",
    "did",
    "does",
    "done",
    "during",
    "each",
    "even",
    "every",
    "first",
    "for",
    "from",
    "has",
    "have",
    "here",
    "into",
    "is",
    "its",
    "just",
    "last",
    "none",
    "not",
    "now",
    "once",
    "only",
    "other",
    "over",
    "own",
    "please",
    "selected",
    "shall",
    "should",
    "since",
    "some",
    "such",
    "than",
    "that",
    "the",
    "their",
    "then",
    "there",
    "these",
    "this",
    "those",
    "through",
    "to",
    "too",
    "under",
    "until",
    "upon",
    "very",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "with",
    "would",
}

#: The literals a runtime module may show without a catalogue entry.
#:
#: They are symbols and signs, not words: a symbol says the same in every
#: language, so translating it would only make it harder to read.
ALLOWED_LITERALS: dict[str, str] = {
    "×": "The cross that closes a row is a sign, not a word.",
    "+": "The plus that adds a module is a sign, not a word.",
    "→": "The arrow of the chain is a sign, not a word.",
    "…": "The ellipsis of the module menu is a sign, not a word.",
    "—": "The em dash marks an empty value, not a word.",
    "*": "The asterisk marks the unsaved state, not a word.",
    "▾": "The caret of a section is a sign, not a word.",
    "·": "The dot that separates two readouts is a sign, not a word.",
    " · ": "The dot that separates two readouts is a sign, not a word.",
    "  ·  ": "The dot that separates two readouts is a sign, not a word.",
}


# --------------------------------------------------------------------------
# Helpers.
# --------------------------------------------------------------------------


def _read(path: Path) -> str:
    """Read a text file, tolerating a missing file."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _audit_module():
    """Import the accessibility audit of qa/a11y/."""
    spec = importlib.util.spec_from_file_location("auralis_a11y_audit", AUDIT_SCRIPT)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        pytest.fail(f"{AUDIT_SCRIPT} could not be imported")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AUDIT = _audit_module()


def _catalogue() -> dict[str, dict[str, str]]:
    """The catalogues of the localisation module."""
    catalogues = AUDIT.load_catalogue()
    assert catalogues, f"{I18N_JS} carries no readable catalogue"
    return catalogues


def _node_catalogue() -> dict[str, dict[str, str]]:
    """The catalogues, read by node out of the module itself."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available on this machine")
    script = (
        "const mod = await import(process.argv[1]);"
        "process.stdout.write(JSON.stringify(mod.CATALOGUES));"
    )
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script, I18N_JS.as_uri()],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        timeout=120,
    )
    if proc.returncode != 0:
        pytest.fail(f"node could not read the catalogue:\n{proc.stderr.strip()}")
    return json.loads(proc.stdout)


def _markup_keys() -> set[str]:
    """Every catalogue key the markup of index.html references."""
    keys = set()
    for match in re.finditer(
        r"""data-i18n(?:-[a-z]+)?\s*=\s*["']([A-Za-z0-9_.]+)["']""", _read(INDEX_HTML)
    ):
        keys.add(match.group(1))
    return keys


def _normalise(text: str) -> str:
    """A text reduced to its words, so that only a real difference counts."""
    return " ".join(str(text).split()).casefold()


def _without_catalogue(text: str) -> str:
    """The source of a module without the catalogue literal in it."""
    start = text.find("export const CATALOGUES")
    if start < 0:
        return text
    end = text.find("\n};", start)
    if end < 0:
        return text
    return text[:start] + text[end:]


# --------------------------------------------------------------------------
# 1. The catalogues.
# --------------------------------------------------------------------------


def test_the_reference_catalogue_is_complete():
    """The reference catalogue covers every part of the chrome."""
    catalogue = _catalogue()
    reference = catalogue[REFERENCE]
    assert len(reference) >= MINIMUM_KEYS, (
        f"the catalogue has {len(reference)} keys, at least {MINIMUM_KEYS} are expected"
    )
    for group in REQUIRED_GROUPS:
        assert any(key.startswith(group) for key in reference), (
            f"the catalogue has no entry for the group {group!r}"
        )


def test_every_locale_ships_the_same_key_set():
    """No locale may be missing a key, and none may carry an extra one."""
    catalogue = _catalogue()
    reference = set(catalogue[REFERENCE])
    assert reference, "the reference catalogue is empty"
    for code, table in catalogue.items():
        keys = set(table)
        missing = sorted(reference - keys)
        extra = sorted(keys - reference)
        assert not missing, f"locale {code} is missing: {missing}"
        assert not extra, f"locale {code} has keys the others do not: {extra}"


def test_the_german_catalogue_ships():
    """The third locale is there and it is named."""
    catalogue = _catalogue()
    assert GERMAN in catalogue, (
        f"the German catalogue is missing; the locales are: {sorted(catalogue)}"
    )
    assert AUDIT.js_binding(_read(I18N_JS), "LOCALE_NAMES")[GERMAN] == "Deutsch"


def test_the_catalogue_covers_the_markup():
    """Every key the markup uses has to exist in the catalogue."""
    catalogue = _catalogue()
    reference = set(catalogue[REFERENCE])
    missing = sorted(key for key in _markup_keys() if key not in reference)
    assert not missing, (
        f"aurui/index.html references catalogue keys that do not exist: {missing}"
    )


def test_the_catalogue_is_read_the_same_way_by_node():
    """The audit and the module agree on what the catalogue is."""
    catalogue = _catalogue()
    assert catalogue == _node_catalogue()


# --------------------------------------------------------------------------
# 2. The catalogue is honest: no dead key, no untranslated string.
# --------------------------------------------------------------------------

#: A catalogue key inside a quoted string.
_KEY_LITERAL = re.compile(r"""["'`]([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+)["'`]""")

#: A key prefix inside a template literal of a call to t().
_KEY_PREFIX = re.compile(r"""\bt\(\s*`([^`$]*?)\$\{""")

#: A string literal of a source.
_STRING = re.compile(r"""(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)'|`((?:[^`\\]|\\.)*)`)""", re.S)


def _strings(argument: str) -> list[str]:
    """The string literals of an argument, in order."""
    out = []
    for match in _STRING.finditer(argument):
        out.append(next(group for group in match.groups() if group is not None))
    return out


def _arguments(text: str, start: int) -> list[str]:
    """The arguments of a call, split at the commas of the top level."""
    args: list[str] = []
    depth = 0
    current: list[str] = []
    index = start
    while index < len(text):
        char = text[index]
        if char in "\"'`":
            quote = char
            current.append(char)
            index += 1
            while index < len(text) and text[index] != quote:
                if text[index] == "\\":
                    current.append(text[index])
                    index += 1
                current.append(text[index])
                index += 1
            if index < len(text):
                current.append(text[index])
                index += 1
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif char == "," and depth == 0:
            args.append("".join(current))
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    args.append("".join(current))
    return args


def _calls(text: str) -> list[tuple[str, str]]:
    """Every call to t() with a literal key: (key, english fallback)."""
    calls: list[tuple[str, str]] = []
    for match in re.finditer(r"""\bt\(\s*""", text):
        arguments = _arguments(text, match.end())
        if len(arguments) < 2:
            continue
        keys = _strings(arguments[0])
        if len(keys) != 1:
            continue
        if "$" in keys[0] or "{" in keys[0]:
            # a key that is built out of values: the prefix rule of the
            # dead key check covers it
            continue
        fallback = "".join(_strings(arguments[1]))
        calls.append((keys[0], fallback))
    return calls


def _sources() -> list[Path]:
    """The JavaScript sources of the interface."""
    return sorted(path for path in JS_DIR.glob("*.js") if path.is_file())


def _used_keys(catalogue: dict[str, dict[str, str]]) -> set[str]:
    """Every key a source of the interface refers to.

    A key counts as used when
      * a call to t() names it,
      * a call to t() builds it out of a template (``t(`status.engine.${…}`)``),
      * the markup names it in a ``data-i18n*`` attribute,
      * the keyboard map owns a command with that id, or
      * a source holds the key as a string literal (the tables of the modules
        list their keys that way).
    A key that is mentioned in a pending patch counts as well: the patch is
    the delivery of a change to a file this project does not own yet.
    """
    reference = set(catalogue[REFERENCE])
    used: set[str] = set()
    prefixes: set[str] = set()
    for path in _sources():
        text = _read(path)
        if path.name == "i18n.js":
            text = _without_catalogue(text)
        for match in _KEY_LITERAL.finditer(text):
            if match.group(1) in reference:
                used.add(match.group(1))
        for match in _KEY_PREFIX.finditer(text):
            prefixes.add(match.group(1))
    for key in reference:
        if any(key.startswith(prefix) for prefix in prefixes):
            used.add(key)
    used |= _markup_keys()
    for command_id in AUDIT.load_command_ids():
        used.add(f"key.{command_id}.label")
        used.add(f"key.{command_id}.description")
    for patch in sorted(PATCH_DIR.glob("*.patch")) if PATCH_DIR.is_dir() else []:
        text = _read(patch)
        for match in _KEY_LITERAL.finditer(text):
            if match.group(1) in reference:
                used.add(match.group(1))
        for match in _KEY_PREFIX.finditer(text):
            prefixes.add(match.group(1))
    for key in reference:
        if any(key.startswith(prefix) for prefix in prefixes):
            used.add(key)
    return used


def test_no_catalogue_key_is_dead():
    """Every key of the catalogue is used by a source of the interface."""
    catalogue = _catalogue()
    reference = set(catalogue[REFERENCE])
    used = _used_keys(catalogue)
    dead = sorted(reference - used)
    assert not dead, (
        "these catalogue keys are not used by any source: "
        f"{dead} — a key nobody reads is a translation nobody sees"
    )


def test_every_runtime_module_reads_the_catalogue():
    """Every module of the runtime builds its text through the catalogue."""
    for name in RUNTIME_MODULES:
        path = JS_DIR / name
        assert path.is_file(), f"aurui/js/{name} is missing"
        text = _read(path)
        assert 'from "./i18n.js"' in text, (
            f"aurui/js/{name} does not import the catalogue; its text stays English"
        )
        assert _calls(text), f"aurui/js/{name} does not call t(); its text stays English"


def test_the_english_fallback_of_every_call_is_the_catalogue_text():
    """The English text of a call and the catalogue must agree."""
    catalogue = _catalogue()
    english = catalogue[REFERENCE]
    problems: list[str] = []
    for name in RUNTIME_MODULES:
        text = _read(JS_DIR / name)
        for key, fallback in _calls(text):
            if key not in english:
                problems.append(f"{name}: the key {key!r} is not in the catalogue")
            elif english[key] != fallback:
                problems.append(
                    f"{name}: the fallback of {key!r} is {fallback!r}, "
                    f"the catalogue says {english[key]!r}"
                )
    assert not problems, (
        "the runtime modules and the catalogue do not agree:\n"
        + "\n".join(f"  - {problem}" for problem in problems)
    )


#: The positions of a source where a literal reaches the user.
_VISIBLE_PATTERNS = (
    ("attribute", re.compile(
        r"""setAttribute\(\s*["'](?:title|aria-label|placeholder)["']\s*,\s*(["'`])([^"'`]*)\1"""
    )),
    ("textContent", re.compile(r"""\.textContent\s*=\s*(["'`])([^"'`]*)\1""")),
    ("option", re.compile(r"""\b(?:text|label|title|placeholder)\s*:\s*(["'`])([^"'`]*)\1""")),
    ("element", re.compile(
        r"""\bel\(\s*["'][a-z0-9]+["']\s*,\s*\{[^}]*\}\s*,\s*(["'`])([^"'`]*)\1"""
    )),
    ("canvas", re.compile(r"""fillText\(\s*(["'`])([^"'`]*)\1""")),
    ("label", re.compile(r"""\b(?:statRow|meterRow|row)\(\s*(["'`])([^"'`]*)\1""")),
)


def _make_texts(text: str) -> list[str]:
    """The text every make() call shows.

    Two shapes are in the tree: ``make(doc, tag, className, text)`` and
    ``make(tag, className, text)``.  The first argument tells them apart.
    The class name is never a text the user reads.
    """
    found: list[str] = []
    for match in re.finditer(r"""\bmake\(\s*""", text):
        arguments = _arguments(text, match.end())
        if not arguments:
            continue
        first = arguments[0].strip()
        index = 3 if re.fullmatch(r"\w*doc\w*", first) else 2
        if len(arguments) <= index:
            continue
        literals = _strings(arguments[index])
        if len(literals) == 1:
            found.append(literals[0])
    return found


def _visible_literals(text: str) -> list[tuple[str, str]]:
    """Every literal of a source that reaches the user."""
    found: list[tuple[str, str]] = []
    for kind, pattern in _VISIBLE_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(2)
            if not value.strip():
                continue
            if value.startswith("${") or "${" in value:
                # a template that is built out of values is not a fixed text
                continue
            found.append((kind, value))
    for value in _make_texts(text):
        if not value.strip() or "${" in value:
            continue
        found.append(("make", value))
    return found


def test_every_string_of_a_runtime_module_comes_out_of_the_catalogue():
    """A module may only show what the catalogue holds."""
    catalogue = _catalogue()
    english = set(catalogue[REFERENCE].values())
    offenders: list[str] = []
    for name in RUNTIME_MODULES:
        text = _read(JS_DIR / name)
        for kind, value in _visible_literals(text):
            if value in english or value in ALLOWED_LITERALS:
                continue
            offenders.append(f"{name}: {kind} {value!r}")
    assert not offenders, (
        "these strings of the runtime modules are not in the catalogue:\n"
        + "\n".join(f"  - {item}" for item in offenders)
        + "\n  — add a key for every one of them, or an entry to ALLOWED_LITERALS."
    )


# --------------------------------------------------------------------------
# 3. No key resolves to itself.
# --------------------------------------------------------------------------


def test_no_key_resolves_to_its_own_name():
    """A key must never be shown to the user."""
    catalogue = _catalogue()
    for code, table in catalogue.items():
        unresolved = sorted(key for key, value in table.items() if value == key)
        assert not unresolved, f"locale {code} does not translate: {unresolved}"


def test_no_catalogue_entry_is_empty():
    """An empty string is as bad as the key itself."""
    catalogue = _catalogue()
    for code, table in catalogue.items():
        empty = sorted(key for key, value in table.items() if not str(value).strip())
        assert not empty, f"locale {code} has empty strings: {empty}"


def test_the_placeholders_survive_the_translation():
    """A translation keeps the placeholders of the English string."""
    catalogue = _catalogue()
    reference = catalogue[REFERENCE]
    for code, table in catalogue.items():
        for key, value in table.items():
            expected = set(re.findall(r"\{([a-zA-Z0-9_]+)\}", reference[key]))
            found = set(re.findall(r"\{([a-zA-Z0-9_]+)\}", value))
            assert found == expected, (
                f"locale {code} has the placeholders {sorted(found)} in {key}, "
                f"the English string has {sorted(expected)}"
            )


# --------------------------------------------------------------------------
# 4. The German catalogue is a translation.
# --------------------------------------------------------------------------


def test_the_german_catalogue_has_no_untranslated_english():
    """Only the terms that are intentionally identical may stay English."""
    catalogue = _catalogue()
    english = catalogue[REFERENCE]
    german = catalogue[GERMAN]
    untranslated = sorted(
        key
        for key in english
        if _normalise(german[key]) == _normalise(english[key])
        and key not in IDENTICAL_IN_GERMAN
    )
    assert not untranslated, (
        "these German strings are still the English ones; either translate them "
        f"or add them to IDENTICAL_IN_GERMAN with a reason: {untranslated}"
    )


def test_the_whitelist_is_still_needed():
    """A whitelisted term that was translated in the meantime leaves the list."""
    catalogue = _catalogue()
    english = catalogue[REFERENCE]
    german = catalogue[GERMAN]
    stale = sorted(
        key
        for key in IDENTICAL_IN_GERMAN
        if _normalise(german.get(key, "")) != _normalise(english.get(key, ""))
    )
    assert not stale, (
        f"these terms are translated now and do not belong in the whitelist: {stale}"
    )


def test_every_whitelisted_term_carries_a_reason():
    """A whitelist entry without a reason is a forgotten translation."""
    for key, reason in IDENTICAL_IN_GERMAN.items():
        assert reason.strip(), f"{key} is whitelisted without a reason"
        assert len(reason.split()) >= 4, f"the reason for {key} is too terse: {reason!r}"


def test_the_german_catalogue_has_no_residual_english_words():
    """A sentence that was copied over is caught by its English words."""
    catalogue = _catalogue()
    german = catalogue[GERMAN]
    offenders: dict[str, list[str]] = {}
    for key, value in german.items():
        words = set(re.findall(r"[A-Za-z]+", value.casefold()))
        found = sorted(words & ENGLISH_ONLY_WORDS)
        if found:
            offenders[key] = found
    assert not offenders, (
        "these German strings still carry English words: " f"{offenders}"
    )


def test_the_german_catalogue_is_not_a_copy_of_another_locale():
    """A copy-paste error must not pass as a translation."""
    catalogue = _catalogue()
    german = catalogue[GERMAN]
    for code, table in catalogue.items():
        if code == GERMAN:
            continue
        shared = sum(1 for key in german if german[key] == table.get(key))
        ratio = shared / max(1, len(german))
        assert ratio < 0.5, (
            f"the German catalogue shares {shared} of {len(german)} strings with "
            f"the {code} catalogue -- it looks like a copy"
        )


# --------------------------------------------------------------------------
# 5. The accessibility audit.
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def report() -> dict:
    """The report of the accessibility audit."""
    return AUDIT.audit()


def test_the_audit_script_exists():
    """The audit is part of the quality gate, so it has to be there."""
    assert AUDIT_SCRIPT.is_file(), f"{AUDIT_SCRIPT} is missing"


def test_the_audit_finds_no_unlabelled_control(report: dict):
    """Every control the user can operate has to say what it is."""
    unlabelled = [item for item in report["errors"] if item["code"] == "unlabelled-control"]
    assert not unlabelled, (
        "these controls have no accessible name: " f"{unlabelled}"
    )


def test_the_audit_finds_no_control_the_keyboard_cannot_reach(report: dict):
    """Every control has to be reachable without a pointer."""
    unreachable = [
        item for item in report["errors"] if item["code"] == "not-keyboard-reachable"
    ]
    assert not unreachable, f"these controls are not keyboard reachable: {unreachable}"


def test_the_audit_finds_no_error_at_all(report: dict):
    """The audit as a whole is clean."""
    assert not report["errors"], (
        "the accessibility audit reports errors:\n"
        + "\n".join(
            f"  [{item['code']}] {item['target']}: {item['message']}"
            for item in report["errors"]
        )
    )


def test_the_status_readouts_and_the_toast_are_live_regions(report: dict):
    """The readouts announce their new value without stealing the focus."""
    missing = [
        item for item in report["errors"] if item["code"] == "missing-live-region"
    ]
    assert not missing, f"these readouts are not live regions: {missing}"


def test_every_locale_is_offered_by_the_interface():
    """The switch of the shell lists every locale the module ships."""
    markup = _read(INDEX_HTML)
    assert 'id="locale-select"' in markup, (
        "the chrome has no locale switch; add <select id=\"locale-select\">"
    )
    shell = _read(JS_DIR / "app.js")
    assert "availableLocales" in shell, (
        "the shell does not read availableLocales(); the switch would stay empty"
    )
    assert "setLocale" in shell, "the shell does not call setLocale()"
    locales = _catalogue()
    assert GERMAN in locales, "the German catalogue is not offered"


def test_the_focus_ring_is_styled():
    """The keyboard focus has to be visible."""
    css = "\n".join(_read(path) for path in sorted(CSS_DIR.glob("*.css")))
    assert ":focus-visible" in css, "no stylesheet knows the :focus-visible ring"
    assert re.search(r":focus-visible[^{]*\{[^}]*outline\s*:\s*(?!none)", css), (
        "no :focus-visible rule draws an outline"
    )


def test_the_high_contrast_variant_ships():
    """The chrome has a high-contrast variant."""
    css = "\n".join(_read(path) for path in sorted(CSS_DIR.glob("*.css")))
    assert '[data-contrast="high"]' in css, (
        "no high-contrast variant of the chrome in the stylesheets"
    )


def test_the_shell_and_the_module_agree_on_the_storage_key():
    """The chosen locale has to survive a reload of the interface."""
    module_key = AUDIT.js_binding(_read(I18N_JS), "LOCALE_STORAGE_KEY")
    assert module_key, "aurui/js/i18n.js exports no LOCALE_STORAGE_KEY"
    shell = _read(JS_DIR / "app.js")
    assert module_key in shell, (
        f"the shell does not use the storage key {module_key!r} of the module"
    )


# --------------------------------------------------------------------------
# 6. The behaviour of the module (read out of the module itself).
# --------------------------------------------------------------------------

#: Exercises the locale helpers of the module.
LOCALE_BEHAVIOUR = """
const mod = await import("__URL__");

/* A document that remembers the language attribute it is given. */
const attributes = {};
const element = {
  setAttribute: (name, value) => { attributes[name] = value; },
  getAttribute: (name) => (name in attributes ? attributes[name] : null),
};
globalThis.document = {
  documentElement: element,
  querySelectorAll: () => [],
  title: "",
};

/* A storage that remembers what is written into it. */
function makeStorage(initial) {
  const data = Object.assign({}, initial || {});
  return {
    data,
    getItem: (key) => (key in data ? data[key] : null),
    setItem: (key, value) => { data[key] = String(value); },
  };
}

const names = mod.availableLocales();
const detected = (nav) => mod.detectLocale(nav);
const storage = makeStorage();
const first = mod.initLocale({ storage, navigator: { languages: ["de-AT", "en"] } });
const afterFirst = {
  locale: mod.getLocale(),
  lang: attributes.lang,
  stored: storage.data[mod.LOCALE_STORAGE_KEY],
};

/* The choice of the user wins over the language of the browser. */
mod.setLocale("zh-CN");
const afterChoice = {
  locale: mod.getLocale(),
  lang: attributes.lang,
  stored: storage.data[mod.LOCALE_STORAGE_KEY],
};

/* A stored choice wins over the language of the browser. */
const remembered = makeStorage({ "auralis.locale": "de" });
const second = mod.initLocale({
  storage: remembered,
  navigator: { languages: ["en-US"] },
});
const afterStored = {
  locale: mod.getLocale(),
  lang: attributes.lang,
  stored: remembered.data[mod.LOCALE_STORAGE_KEY],
};

/* Without a stored choice and without a language the browser asks for,
   the default locale is used. */
const empty = makeStorage();
const third = mod.initLocale({ storage: empty, navigator: {} });
const afterDefault = { locale: mod.getLocale(), lang: attributes.lang };

process.stdout.write(JSON.stringify({
  locales: names,
  names: mod.LOCALE_NAMES,
  default: mod.DEFAULT_LOCALE,
  storageKey: mod.LOCALE_STORAGE_KEY,
  resolveDe: mod.resolveLocale("de"),
  resolveDeDE: mod.resolveLocale("de-DE"),
  resolveDeAT: mod.resolveLocale("de-AT"),
  resolveZh: mod.resolveLocale("zh"),
  resolveZhCN: mod.resolveLocale("zh-CN"),
  resolveUnknown: mod.resolveLocale("xx-XX"),
  resolveEmpty: mod.resolveLocale(""),
  detectDe: detected({ languages: ["fr-FR", "de-AT"] }),
  detectZh: detected({ language: "zh-CN" }),
  detectNone: detected({}),
  localeNameDe: mod.localeName("de"),
  localeNameUnknown: mod.localeName("xx"),
  germanPreview: (mod.setLocale("de"), mod.t("transport.preview")),
  germanNoMatch: mod.t("browser.noMatch", null, { query: "Klick" }),
  germanInterpolated: mod.t("control.modified", null, { name: "Preset" }),
  englishFallback: (mod.setLocale("de"), mod.t("no.such.key", "fallback text")),
  unknownKey: (mod.setLocale("de"), mod.t("no.such.key")),
  first: first,
  afterFirst: afterFirst,
  afterChoice: afterChoice,
  second: second,
  afterStored: afterStored,
  third: third,
  afterDefault: afterDefault,
  placeholders: (() => {
    const out = {};
    for (const code of mod.availableLocales()) {
      mod.setLocale(code);
      const table = mod.catalogue(code) || {};
      const rows = [];
      for (const key of Object.keys(table)) {
        const names = (table[key].match(/\\{([a-zA-Z0-9_]+)\\}/g) || []).map(
          (m) => m.slice(1, -1)
        );
        if (!names.length) continue;
        const vars = {};
        for (const name of names) vars[name] = "X";
        const text = mod.t(key, null, vars);
        const left = (text.match(/\\{[a-zA-Z0-9_]+\\}/g) || []);
        if (left.length) rows.push(key + ": " + left.join(","));
      }
      out[code] = rows;
    }
    mod.setLocale("en");
    return out;
  })(),
}));
"""


@pytest.fixture(scope="module")
def behaviour() -> dict:
    """The behaviour of the localisation module."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available on this machine")
    script = LOCALE_BEHAVIOUR.replace("__URL__", I18N_JS.as_uri())
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        timeout=120,
    )
    if proc.returncode != 0:
        pytest.fail(f"node could not exercise the module:\n{proc.stderr.strip()}")
    return json.loads(proc.stdout)


def test_the_module_ships_three_locales(behaviour: dict):
    """English, Chinese and German."""
    assert behaviour["locales"] == ["en", "zh-CN", "de"], behaviour["locales"]
    assert behaviour["default"] == "en"
    assert behaviour["names"]["de"] == "Deutsch"


def test_a_locale_code_resolves_to_its_catalogue(behaviour: dict):
    """A regional code finds the language it belongs to."""
    assert behaviour["resolveDe"] == "de"
    assert behaviour["resolveDeDE"] == "de"
    assert behaviour["resolveDeAT"] == "de"
    assert behaviour["resolveZh"] == "zh-CN"
    assert behaviour["resolveZhCN"] == "zh-CN"
    assert behaviour["resolveUnknown"] is None
    assert behaviour["resolveEmpty"] is None


def test_the_browser_language_is_detected(behaviour: dict):
    """The interface starts in the language the browser asks for."""
    assert behaviour["detectDe"] == "de"
    assert behaviour["detectZh"] == "zh-CN"
    assert behaviour["detectNone"] is None


def test_the_german_strings_reach_the_interface(behaviour: dict):
    """The catalogue is what `t()` returns."""
    assert behaviour["localeNameDe"] == "Deutsch"
    assert behaviour["germanPreview"] == "Vorschau"
    assert behaviour["germanNoMatch"] == "Keine Module für „Klick“ gefunden"
    assert behaviour["germanInterpolated"] == "Preset (geändert)"


def test_an_unknown_key_falls_back_to_the_given_text(behaviour: dict):
    """The interface never shows a raw key."""
    assert behaviour["englishFallback"] == "fallback text"
    assert behaviour["unknownKey"] == "no.such.key"


# --------------------------------------------------------------------------
# 7. The locale is chosen sensibly.
# --------------------------------------------------------------------------


def test_the_first_run_follows_the_browser(behaviour: dict):
    """Without a stored choice the language of the browser decides."""
    assert behaviour["first"] == "de", behaviour["first"]
    assert behaviour["afterFirst"]["locale"] == "de"
    assert behaviour["afterFirst"]["lang"] == "de", (
        "the lang attribute of the document does not follow the locale"
    )


def test_the_choice_of_the_user_wins(behaviour: dict):
    """A locale the user picks is applied and remembered."""
    assert behaviour["afterChoice"]["locale"] == "zh-CN"
    assert behaviour["afterChoice"]["lang"] == "zh-CN"
    assert behaviour["afterChoice"]["stored"] == "zh-CN", (
        "the choice of the user is not remembered"
    )


def test_a_stored_choice_wins_over_the_browser(behaviour: dict):
    """The second session starts in the language of the first one."""
    assert behaviour["second"] == "de", behaviour["second"]
    assert behaviour["afterStored"]["locale"] == "de"
    assert behaviour["afterStored"]["lang"] == "de"


def test_without_a_language_the_default_is_used(behaviour: dict):
    """A browser that asks for nothing we ship gets the default locale."""
    assert behaviour["third"] == "en", behaviour["third"]
    assert behaviour["afterDefault"]["lang"] == "en"


def test_every_placeholder_is_substituted_in_every_locale(behaviour: dict):
    """A translated string keeps its placeholders and fills them in."""
    for code, rows in behaviour["placeholders"].items():
        assert not rows, f"locale {code} left placeholders unfilled: {rows}"


def test_the_catalogue_carries_placeholders(behaviour: dict):
    """The catalogue really uses placeholders, so the check above has teeth."""
    catalogue = _catalogue()
    total = 0
    for code, table in catalogue.items():
        for key, value in table.items():
            total += len(re.findall(r"\{([a-zA-Z0-9_]+)\}", value))
    assert total > 0, "no string of the catalogue carries a placeholder"
