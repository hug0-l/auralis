"""Static contract checks for the Auralis web interface.

These tests need no browser, no server and no network: they read the shipped
sources of ``aurui/`` and assert the contracts the interface has to keep.

What is checked
  1. every element id the JavaScript looks up exists in ``aurui/index.html``;
  2. every id the JavaScript creates at runtime is created exactly once and
     does not collide with an id of the document;
  3. every CSS class the JavaScript puts on a node is styled — either in one of
     the stylesheets of ``aurui/css/`` or in a stylesheet that a JavaScript
     module injects itself;
  4. no stylesheet hard-codes a colour: every colour comes from a variable of
     ``aurui/css/tokens.css`` (the token file itself is the one exception);
  5. the keyboard map has no duplicate default binding inside a context;
  6. every locale of the catalogue covers exactly the same key set, and every
     key resolves to a translation instead of to its own name.

The JavaScript is inspected by importing it with node, so the checks see the
real data instead of a re-implementation of it.  When node is not available
the behavioural checks are skipped; the purely static ones always run.
"""

from __future__ import annotations

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
TOKENS_CSS = CSS_DIR / "tokens.css"
KEYMAP_JS = JS_DIR / "keymap.js"
I18N_JS = JS_DIR / "i18n.js"

#: The stylesheet that defines the design tokens; it is the one file that is
#: allowed to contain colour literals.
TOKEN_FILE = TOKENS_CSS

#: Colours that are not hard-coded values.
ALLOWED_COLOUR_WORDS = {"transparent", "currentcolor", "inherit", "none", "auto"}


# --------------------------------------------------------------------------
# Helpers.
# --------------------------------------------------------------------------


def _read(path: Path) -> str:
    """Read a text file, tolerating a missing file."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _js_files() -> list[Path]:
    """Every JavaScript source of the interface."""
    return sorted(p for p in JS_DIR.glob("*.js") if p.is_file())


def _css_files() -> list[Path]:
    """Every stylesheet of the interface."""
    return sorted(p for p in CSS_DIR.glob("*.css") if p.is_file())


def _strip_css_comments(text: str) -> str:
    """Remove the comments of a stylesheet."""
    return re.sub(r"/\*.*?\*/", " ", text, flags=re.S)


def _embedded_css_blocks() -> list[tuple[Path, str]]:
    """The stylesheets that a JavaScript module injects into the document.

    A JavaScript module may carry its own component styles in a template
    literal; those styles count as "styled in one of the CSS files".
    """
    blocks: list[tuple[Path, str]] = []
    literal_re = re.compile(r"`((?:[^`\\]|\\.)*)`", re.S)
    looks_like_css = re.compile(r"\{[^}]*[a-zA-Z-]+\s*:\s*[^;{}]+;")
    for path in _js_files():
        text = _read(path)
        for match in literal_re.finditer(text):
            body = match.group(1)
            if not looks_like_css.search(body):
                continue
            blocks.append((path, body))
    return blocks


def _styled_classes() -> set[str]:
    """Every class name that some stylesheet has a rule for."""
    names: set[str] = set()
    pattern = re.compile(r"\.(-?[A-Za-z_][A-Za-z0-9_-]*)")
    sources = [(p, _read(p)) for p in _css_files()]
    sources += _embedded_css_blocks()
    for _path, text in sources:
        clean = _strip_css_comments(text)
        for match in pattern.finditer(clean):
            names.add(match.group(1))
    return names


#: The ways a JavaScript source names a class on an element.
_CLASS_PATTERNS = (
    re.compile(r"""\bclass\s*:\s*["']([^"']*)["']"""),
    re.compile(r"""\bclassName\s*=\s*["']([^"']*)["']"""),
    re.compile(r"""\bclass\s*=\s*["']([^"']*)["']"""),
    re.compile(r"""\bclassList\.(?:add|remove|toggle)\(\s*["']([^"']*)["']"""),
    re.compile(r"""setAttribute\(\s*["']class["']\s*,\s*["']([^"']*)["']"""),
    re.compile(r"""\bmake\(\s*\w+\s*,\s*["'][^"']*["']\s*,\s*["']([^"']*)["']"""),
)

#: A class name: a letter followed by letters, digits, dashes or underscores.
_CLASS_TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def _classes_used_by_js() -> dict[str, set[str]]:
    """The classes the JavaScript puts on a node, per source file."""
    used: dict[str, set[str]] = {}
    for path in _js_files():
        text = _read(path)
        found: set[str] = set()
        for pattern in _CLASS_PATTERNS:
            for match in pattern.finditer(text):
                for token in match.group(1).split():
                    if _CLASS_TOKEN.match(token):
                        found.add(token)
        if found:
            used[path.name] = found
    return used


def _ids_looked_up() -> dict[str, set[str]]:
    """The element ids the JavaScript resolves to a node, per source file."""
    patterns = (
        re.compile(r"""\$\(\s*["']#([A-Za-z][\w-]*)["']"""),
        re.compile(r"""getElementById\(\s*["']([A-Za-z][\w-]*)["']"""),
        re.compile(r"""querySelector\(\s*["']#([A-Za-z][\w-]*)["']"""),
    )
    found: dict[str, set[str]] = {}
    for path in _js_files():
        text = _read(path)
        ids: set[str] = set()
        for pattern in patterns:
            for match in pattern.finditer(text):
                ids.add(match.group(1))
        if ids:
            found[path.name] = ids
    return found


def _ids_created() -> dict[str, list[tuple[str, str]]]:
    """The element ids the JavaScript creates, per source file."""
    patterns = (
        re.compile(r"""\bid\s*:\s*["']([A-Za-z][\w-]*)["']"""),
        re.compile(r"""setAttribute\(\s*["']id["']\s*,\s*["']([A-Za-z][\w-]*)["']"""),
    )
    found: dict[str, list[tuple[str, str]]] = {}
    for path in _js_files():
        text = _read(path)
        for pattern in patterns:
            for match in pattern.finditer(text):
                found.setdefault(path.name, []).append((match.group(1), path.name))
    return found


def _document_ids() -> set[str]:
    """The element ids of the document."""
    html = _read(INDEX_HTML)
    return set(re.findall(r"""\bid\s*=\s*["']([A-Za-z][\w-]*)["']""", html))


def _node_json(script: str) -> object:
    """Run an ES module with node and return what it printed."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available on this machine")
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        timeout=120,
    )
    if proc.returncode != 0:
        pytest.fail(f"node script failed:\n{proc.stderr.strip()}")
    try:
        return json.loads(proc.stdout or "null")
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        pytest.fail(f"node printed no JSON ({exc}):\n{proc.stdout[:400]}")


#: Imports the keyboard map and dumps the command table.
KEYMAP_MODULE = """
const mod = await import("__URL__");
const canon = (spec) => {
  const p = mod.parseBinding(spec);
  if (!p) return null;
  const parts = [];
  if (p.ctrl) parts.push("ctrl");
  if (p.alt) parts.push("alt");
  if (p.meta) parts.push("meta");
  if (p.shift) parts.push("shift");
  return parts.join("+") + "|" + p.key.toLowerCase();
};
const commands = mod.COMMANDS.map((c) => {
  const specs = Array.isArray(c.key) ? c.key : [c.key];
  return {
    id: c.id,
    context: c.context,
    label: c.label,
    description: c.description,
    canon: specs.map(canon).filter((x) => x !== null),
  };
});
process.stdout.write(JSON.stringify({
  commands: commands,
  contexts: mod.CONTEXTS,
  rows: mod.describeRows(),
  table: mod.describe(),
}));
"""

#: Exercises the matcher of the keyboard map.
KEYMAP_BEHAVIOUR = """
const mod = await import("__URL__");
const input = { tagName: "INPUT", type: "text" };
const body = { tagName: "BODY" };
const ev = (key, extra) => Object.assign({ key: key, target: body }, extra || {});
const id = (cmd) => {
  const found = mod.resolve(cmd.event, cmd.context);
  return found ? found.id : null;
};
process.stdout.write(JSON.stringify({
  plainB: mod.matches(ev("b"), "module.bypass"),
  ctrlB: mod.matches(ev("b", { ctrlKey: true }), "module.bypass"),
  altB: mod.matches(ev("b", { altKey: true }), "module.bypass"),
  metaB: mod.matches(ev("b", { metaKey: true }), "module.bypass"),
  shiftB: mod.matches(ev("B"), "module.bypass"),
  typingB: mod.matches(ev("b", { target: input }), "module.bypass"),
  typingSpace: mod.matches(ev(" ", { target: input }), "transport.preview"),
  ctrlS: mod.matches(ev("s", { ctrlKey: true }), "preset.save"),
  plainS: mod.matches(ev("s"), "preset.save"),
  space: mod.matches(ev(" "), "transport.preview"),
  escape: mod.matches(ev("Escape"), "dialog.close"),
  deleteKey: mod.matches(ev("Delete"), "module.remove"),
  unknown: mod.matches(ev("q"), "transport.preview"),
  unknownCommand: mod.matches(ev("b"), "does.not.exist"),
  viewF: id({ event: ev("f"), context: "view" }),
  globalF: id({ event: ev("f"), context: "global" }),
  panelT: id({ event: ev("t"), context: "panel" }),
  viewT: id({ event: ev("t"), context: "view" }),
  globalT: id({ event: ev("t"), context: "global" }),
  typingResolve: id({ event: ev("b", { target: input }), context: "global" }),
}));
"""

#: Imports the catalogue and dumps the key set of every locale.
I18N_MODULE = """
const mod = await import("__URL__");
const locales = mod.availableLocales();
const keys = {};
const unresolved = {};
for (const code of locales) {
  const table = mod.catalogue(code) || {};
  keys[code] = Object.keys(table).sort();
  mod.setLocale(code);
  const bad = [];
  for (const key of keys[code]) {
    if (mod.t(key) === key) bad.push(key);
  }
  unresolved[code] = bad;
}
mod.setLocale("en");
process.stdout.write(JSON.stringify({
  locales: locales,
  keys: keys,
  unresolved: unresolved,
  default: mod.DEFAULT_LOCALE,
  names: mod.LOCALE_NAMES,
  english: mod.t("transport.preview"),
  other: mod.t("transport.preview", null, null),
  fallback: mod.t("no.such.key", "fallback text"),
}));
"""


def _keymap_data() -> dict:
    """The command table of the keyboard map."""
    data = _node_json(KEYMAP_MODULE.replace("__URL__", KEYMAP_JS.as_uri()))
    assert isinstance(data, dict) and "commands" in data
    return data


def _i18n_data() -> dict:
    """The catalogues of the localisation module."""
    data = _node_json(I18N_MODULE.replace("__URL__", I18N_JS.as_uri()))
    assert isinstance(data, dict) and "locales" in data
    return data


# --------------------------------------------------------------------------
# 1. Element ids.
# --------------------------------------------------------------------------


def test_every_looked_up_element_id_exists_in_the_document():
    """Every id the JavaScript resolves must be part of index.html."""
    document_ids = _document_ids()
    missing: dict[str, list[str]] = {}
    for name, ids in _ids_looked_up().items():
        gap = sorted(i for i in ids if i not in document_ids)
        if gap:
            missing[name] = gap
    assert not missing, (
        "these element ids are resolved by the JavaScript but do not exist in "
        f"aurui/index.html: {missing}"
    )


def test_the_document_is_not_empty():
    """Sanity check: index.html was read at all."""
    assert _document_ids(), "aurui/index.html declares no element ids"


def test_runtime_created_ids_are_unique_and_do_not_collide():
    """An id the JavaScript creates must not exist in the document as well."""
    document_ids = _document_ids()
    seen: dict[str, str] = {}
    collisions: list[str] = []
    duplicated: list[str] = []
    for _name, entries in _ids_created().items():
        for element_id, source in entries:
            if element_id in document_ids:
                collisions.append(f"{element_id} (created in {source})")
            if element_id in seen:
                duplicated.append(f"{element_id} ({seen[element_id]} and {source})")
            else:
                seen[element_id] = source
    assert not collisions, (
        "these ids are created by the JavaScript and also exist in "
        f"aurui/index.html: {sorted(set(collisions))}"
    )
    assert not duplicated, (
        f"these ids are created more than once by the JavaScript: {sorted(set(duplicated))}"
    )


# --------------------------------------------------------------------------
# 2. CSS classes.
# --------------------------------------------------------------------------


def test_every_class_used_by_the_javascript_is_styled():
    """A class the JavaScript puts on a node must have a rule somewhere."""
    styled = _styled_classes()
    unstyled: dict[str, list[str]] = {}
    for name, classes in _classes_used_by_js().items():
        gap = sorted(c for c in classes if c not in styled)
        if gap:
            unstyled[name] = gap
    assert not unstyled, (
        "these CSS classes are used by the JavaScript but have no rule in "
        f"aurui/css/ nor in a stylesheet a JavaScript module injects: {unstyled}"
    )


def test_the_stylesheets_are_found():
    """Sanity check: the stylesheets were read at all."""
    assert len(_css_files()) >= 2
    assert _styled_classes()


# --------------------------------------------------------------------------
# 3. Colours.
# --------------------------------------------------------------------------

#: A colour literal inside a declaration value.
_COLOUR_IN_VALUE = re.compile(
    r"""(?<![\w-])#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b"""
)
_COLOUR_FUNCTION = re.compile(r"""\b(?:rgb|rgba|hsl|hsla|hwb|lab|lch|oklab|oklch)\s*\(""")

#: A declaration of a stylesheet: "property: value".
_DECLARATION = re.compile(r"""(?:^|[;{{])\s*[-a-zA-Z]+\s*:\s*([^;{}]+)""")


def _colour_offenders(text: str) -> list[str]:
    """The colour literals of a stylesheet."""
    offenders: list[str] = []
    clean = _strip_css_comments(text)
    for match in _DECLARATION.finditer(clean):
        value = match.group(1)
        for literal in _COLOUR_IN_VALUE.findall(value):
            offenders.append(literal)
        for literal in _COLOUR_FUNCTION.findall(value):
            offenders.append(literal.strip())
        for word in re.findall(r"[a-zA-Z]+", value):
            if word.lower() in ALLOWED_COLOUR_WORDS:
                continue
    return offenders


def test_no_stylesheet_hard_codes_a_colour():
    """Every colour of the interface comes from a token."""
    offenders: dict[str, list[str]] = {}
    for path in _css_files():
        if path.resolve() == TOKEN_FILE.resolve():
            continue
        found = _colour_offenders(_read(path))
        if found:
            offenders[path.name] = found
    assert not offenders, (
        "these stylesheets hard-code colours instead of using a variable of "
        f"css/tokens.css: {offenders}"
    )


def test_the_injected_stylesheets_do_not_hard_code_a_colour():
    """The stylesheets a JavaScript module injects follow the same rule."""
    offenders: dict[str, list[str]] = {}
    for path, block in _embedded_css_blocks():
        found = _colour_offenders(block)
        if found:
            offenders.setdefault(path.name, []).extend(found)
    assert not offenders, (
        "these injected stylesheets hard-code colours instead of using a "
        f"variable of css/tokens.css: {offenders}"
    )


def test_the_token_file_defines_the_colours():
    """The token file is the one place where colours are allowed."""
    text = _read(TOKEN_FILE)
    assert "--bg-0" in text and "--txt-0" in text and "--accent" in text


def test_components_css_only_uses_known_tokens():
    """Every variable components.css refers to is defined by tokens.css."""
    tokens = set(re.findall(r"(--[A-Za-z0-9_-]+)\s*:", _read(TOKEN_FILE)))
    used = set(re.findall(r"var\(\s*(--[A-Za-z0-9_-]+)", _read(CSS_DIR / "components.css")))
    unknown = sorted(used - tokens)
    assert not unknown, (
        f"css/components.css uses variables that css/tokens.css does not "
        f"define: {unknown}"
    )


# --------------------------------------------------------------------------
# 4. The keyboard map.
# --------------------------------------------------------------------------

#: The commands the interface has to offer.
REQUIRED_COMMANDS = {
    "transport.preview",
    "transport.stop",
    "module.bypass",
    "module.remove",
    "view.zoomIn",
    "view.zoomOut",
    "view.fit",
    "view.reset",
    "browser.focusSearch",
    "dialog.close",
    "chain.selectPrev",
    "chain.selectNext",
    "view.toggleFreqSelection",
    "view.toggleTimeSelection",
    "inspector.cycleTabs",
    "preset.save",
    "panel.presetMenu",
}


def test_the_keymap_offers_every_required_command():
    """Every command of the specification is bound."""
    data = _keymap_data()
    ids = {command["id"] for command in data["commands"]}
    missing = sorted(REQUIRED_COMMANDS - ids)
    assert not missing, f"the keyboard map does not bind: {missing}"


def test_every_command_is_described():
    """A command carries a description and a context."""
    data = _keymap_data()
    for command in data["commands"]:
        assert command["description"], f"{command['id']} has no description"
        assert command["label"], f"{command['id']} has no label"
        assert command["context"] in data["contexts"], (
            f"{command['id']} has the unknown context {command['context']!r}"
        )


def test_the_keymap_has_no_duplicate_binding_inside_a_context():
    """Two commands of the same context may not share a binding."""
    data = _keymap_data()
    clashes: list[str] = []
    for context in data["contexts"]:
        taken: dict[str, str] = {}
        for command in data["commands"]:
            if command["context"] != context:
                continue
            for binding in command["canon"]:
                if binding in taken:
                    clashes.append(
                        f"{context}: {taken[binding]} and {command['id']} both "
                        f"use {binding!r}"
                    )
                else:
                    taken[binding] = command["id"]
    assert not clashes, "duplicate keyboard bindings: " + "; ".join(clashes)


def test_the_keymap_ignores_typing_and_foreign_modifiers():
    """matches() respects the modifiers and never swallows a typed key."""
    data = _node_json(KEYMAP_BEHAVIOUR.replace("__URL__", KEYMAP_JS.as_uri()))
    assert data["plainB"] is True, "a plain letter must fire its command"
    assert data["shiftB"] is True, "the case of a letter must not matter"
    assert data["ctrlB"] is False, "ctrl must not fire an unmodified command"
    assert data["altB"] is False, "alt must not fire an unmodified command"
    assert data["metaB"] is False, "meta must not fire an unmodified command"
    assert data["typingB"] is False, "a key typed in a field must be ignored"
    assert data["typingSpace"] is False, "space must not be swallowed in a field"
    assert data["ctrlS"] is True, "ctrl+s must reach its command"
    assert data["plainS"] is False, "s alone must not save a preset"
    assert data["space"] is True
    assert data["escape"] is True
    assert data["deleteKey"] is True
    assert data["unknown"] is False
    assert data["unknownCommand"] is False
    assert data["typingResolve"] is None, "a typed key must not resolve"


def test_the_keymap_resolves_the_command_of_the_focused_region():
    """A context command wins over the global ones."""
    data = _node_json(KEYMAP_BEHAVIOUR.replace("__URL__", KEYMAP_JS.as_uri()))
    assert data["viewF"] == "view.toggleFreqSelection"
    assert data["globalF"] is None
    assert data["panelT"] == "inspector.cycleTabs"
    assert data["viewT"] == "view.toggleTimeSelection"
    assert data["globalT"] is None


def test_the_keymap_prints_a_table():
    """describe() produces the table the help overlay shows."""
    data = _keymap_data()
    table = data["table"]
    assert table.strip(), "the printable table is empty"
    assert len(data["rows"]) == len(data["commands"])
    for row in data["rows"]:
        assert row["keys"], f"{row['id']} prints no key"
        assert row["description"], f"{row['id']} prints no description"


# --------------------------------------------------------------------------
# 5. Localisation.
# --------------------------------------------------------------------------

#: The parts of the interface the catalogue has to cover.
REQUIRED_CATALOGUE_GROUPS = (
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
    "toast.",
)


def test_at_least_two_locales_ship():
    """The interface ships English and at least one other language."""
    data = _i18n_data()
    assert len(data["locales"]) >= 2, f"only one locale ships: {data['locales']}"
    assert data["default"] in data["locales"]


def test_every_locale_covers_the_same_keys():
    """No locale may be missing a key another locale has."""
    data = _i18n_data()
    reference = set(data["keys"][data["default"]])
    assert reference, "the reference catalogue is empty"
    for locale in data["locales"]:
        keys = set(data["keys"][locale])
        missing = sorted(reference - keys)
        extra = sorted(keys - reference)
        assert not missing, f"locale {locale} is missing: {missing}"
        assert not extra, f"locale {locale} has keys the others do not: {extra}"


def test_every_key_resolves_to_a_translation():
    """A key must never fall back to its own name."""
    data = _i18n_data()
    for locale, bad in data["unresolved"].items():
        assert not bad, f"locale {locale} does not translate: {bad}"


def test_the_catalogue_covers_the_interface_chrome():
    """Every part of the interface has its strings."""
    data = _i18n_data()
    keys = set(data["keys"][data["default"]])
    for group in REQUIRED_CATALOGUE_GROUPS:
        assert any(k.startswith(group) for k in keys), (
            f"the catalogue has no entry for the group {group!r}"
        )


def test_missing_keys_fall_back_to_english():
    """A key that is unknown everywhere falls back to the given text."""
    data = _i18n_data()
    assert data["fallback"] == "fallback text"


def test_the_catalogue_is_a_plain_dictionary():
    """The catalogue is data, so a translator can replace it."""
    text = _read(I18N_JS)
    assert "CATALOGUES" in text
    assert "export function t(" in text
    assert "export function setLocale(" in text
    assert "export function availableLocales(" in text


def test_every_command_has_a_translated_label_and_description():
    """The help table follows the locale, so every command needs its text."""
    keymap = _keymap_data()
    catalogue = _i18n_data()
    reference = set(catalogue["keys"][catalogue["default"]])
    missing: dict[str, list[str]] = {}
    for command in keymap["commands"]:
        gap = [
            key
            for key in (f"key.{command['id']}.label", f"key.{command['id']}.description")
            if key not in reference
        ]
        if gap:
            missing[command["id"]] = gap
    assert not missing, (
        "these keyboard commands have no text in the catalogue: "
        f"{missing} — the help overlay would show the English fallback only"
    )
