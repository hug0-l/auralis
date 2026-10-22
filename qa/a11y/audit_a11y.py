#!/usr/bin/env python3
"""
qa/a11y/audit_a11y.py -- accessibility audit of the Auralis web interface.

The interface is built from two sources: the static markup of
``aurui/index.html`` and the accessibility furniture the JavaScript applies at
start-up (``aurui/js/keymap.js`` -> ``applyAccessibility()``).  Auditing only
the markup would report every control the runtime labels as broken; auditing
only the runtime would hide the fact that the markup is not self-describing.

This tool therefore builds the *effective* interface -- the markup plus the
``data-i18n*`` translation of ``aurui/js/i18n.js`` plus the attributes
``applyAccessibility()`` adds -- and audits that.  It is dependency free: no
browser, no server, no network.

What is checked (errors -- these break a commitment of CONTRACT.md section 8
or docs/UI.md section 5)

  E1  every exposed control carries an accessible name;
  E2  every exposed control is reachable with the keyboard;
  E3  the status readouts and the toast are live regions;
  E4  the stylesheets draw a visible focus ring;
  E5  the stylesheets ship the high-contrast variant;
  E6  every ``data-i18n*`` key resolves in the catalogue;
  E7  the runtime model of this tool still matches ``keymap.js``;
  E8  every module of the runtime declares every name of its top level
      once — a duplicate declaration is a SyntaxError, so the module does
      not load at all and the feature it carries is gone.

What is reported as warnings (the interface works, but it can be better)

  W1  an accessible name that consists of a symbol only ("▶" instead of
      "Preview");
  W2  an accessible name that is the placeholder only;
  W3  the canvas carries no textual summary (docs/UI.md section 5.8);
  W4  the chrome has no locale switch;
  W5  a catalogue key that no source file references yet.

Usage

    python qa/a11y/audit_a11y.py            # human readable report
    python qa/a11y/audit_a11y.py --json     # machine readable report
    python qa/a11y/audit_a11y.py --strict   # warnings become failures

Exit code 0 when there is no error, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UI_DIR = os.path.join(ROOT, "aurui")
JS_DIR = os.path.join(UI_DIR, "js")
CSS_DIR = os.path.join(UI_DIR, "css")
INDEX_HTML = os.path.join(UI_DIR, "index.html")
I18N_JS = os.path.join(JS_DIR, "i18n.js")
KEYMAP_JS = os.path.join(JS_DIR, "keymap.js")

#: The elements that are interactive on their own.
INTERACTIVE_TAGS = {"button", "input", "select", "textarea", "a", "area"}

#: The roles that make an element interactive.
INTERACTIVE_ROLES = {
    "button",
    "checkbox",
    "combobox",
    "link",
    "listbox",
    "menuitem",
    "menuitemcheckbox",
    "menuitemradio",
    "option",
    "radio",
    "searchbox",
    "slider",
    "spinbutton",
    "switch",
    "tab",
    "textbox",
    "treeitem",
}

#: The elements the browser focuses without a tabindex.
NATIVELY_FOCUSABLE = {"button", "select", "textarea", "summary", "iframe"}

#: The readouts that have to announce their new value.
LIVE_REGION_IDS = ("#st-cursor", "#st-sel", "#st-level", "#st-latency", "#st-engine", "#toast")

#: An import statement of a module of the runtime.
_IMPORT_RE = re.compile(r"""^\s*import\s+(?P<clause>[^"']*?)\s*from\s*["']""")

#: A declaration of a name at the top level of a module.
_DECLARATION_RE = re.compile(
    r"""^\s*(?:export\s+(?:default\s+)?)?(?:async\s+)?(?:const|let|var|function|class)\s+"""
    r"""([A-Za-z_$][\w$]*)"""
)


# ---------------------------------------------------------------------------
# 1. A very small HTML tree.
# ---------------------------------------------------------------------------

VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


class Element:
    """A node of the parsed document."""

    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict[str, str]) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list = []
        self.parent: Element | None = None

    # -- attributes --------------------------------------------------------
    def get(self, name: str, default: str = "") -> str:
        """The value of an attribute, or `default`."""
        return self.attrs.get(name, default)

    def set(self, name: str, value: str) -> None:
        """Set an attribute."""
        self.attrs[name] = value

    # -- tree --------------------------------------------------------------
    def append(self, node) -> None:
        """Add a child node."""
        if isinstance(node, Element):
            node.parent = self
        self.children.append(node)

    def descendants(self):
        """Every descendant, in document order."""
        for child in self.children:
            if isinstance(child, Element):
                yield child
                yield from child.descendants()

    def find_by_id(self, element_id: str):
        """The element with an id, or None."""
        for node in self.descendants():
            if node.get("id") == element_id:
                return node
        return None

    def closest(self, tag: str):
        """The closest ancestor (or self) with a tag."""
        node = self
        while node is not None:
            if node.tag == tag:
                return node
            node = node.parent
        return None

    def labels(self) -> list["Element"]:
        """The label elements that name this control."""
        found = []
        control_id = self.get("id")
        if control_id:
            for node in self.descendants():
                if node.tag == "label" and node.get("for") == control_id:
                    found.append(node)
        wrapping = self.closest("label")
        if wrapping is not None and wrapping not in found:
            found.append(wrapping)
        return found

    @property
    def text(self) -> str:
        """The concatenated text of the subtree."""
        out: list[str] = []
        for child in self.children:
            if isinstance(child, Element):
                out.append(child.text)
            else:
                out.append(child)
        return " ".join("".join(out).split())

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{self.tag} id={self.get('id')!r}>"


class DomBuilder(HTMLParser):
    """Build an element tree out of an HTML document."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Element("#document", {})
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Element(tag.lower(), {k.lower(): (v or "") for k, v in attrs})
        self.stack[-1].append(node)
        if tag.lower() not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = Element(tag.lower(), {k.lower(): (v or "") for k, v in attrs})
        self.stack[-1].append(node)

    def handle_endtag(self, tag):
        tag = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        self.stack[-1].append(data)


def parse_html(text: str) -> Element:
    """Parse an HTML document into an element tree."""
    builder = DomBuilder()
    builder.feed(text)
    builder.close()
    return builder.root


# ---------------------------------------------------------------------------
# 2. A very small JavaScript literal reader.
# ---------------------------------------------------------------------------


class JsLiteralError(ValueError):
    """Raised when a JavaScript literal cannot be read."""


class JsReader:
    """Read the JSON-like literals that the Auralis sources are built from."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0

    # -- helpers -----------------------------------------------------------
    def _skip(self) -> None:
        """Skip whitespace and comments."""
        while self.pos < len(self.text):
            char = self.text[self.pos]
            if char.isspace():
                self.pos += 1
                continue
            if self.text.startswith("//", self.pos):
                end = self.text.find("\n", self.pos)
                self.pos = len(self.text) if end < 0 else end + 1
                continue
            if self.text.startswith("/*", self.pos):
                end = self.text.find("*/", self.pos)
                if end < 0:
                    raise JsLiteralError("unterminated comment")
                self.pos = end + 2
                continue
            return

    def _string(self) -> str:
        """Read a string literal."""
        quote = self.text[self.pos]
        self.pos += 1
        out: list[str] = []
        while self.pos < len(self.text):
            char = self.text[self.pos]
            if char == "\\":
                self.pos += 1
                escape = self.text[self.pos]
                out.append({"n": "\n", "t": "\t", "r": "\r"}.get(escape, escape))
                self.pos += 1
                continue
            if char == quote:
                self.pos += 1
                return "".join(out)
            out.append(char)
            self.pos += 1
        raise JsLiteralError("unterminated string")

    # -- values ------------------------------------------------------------
    def value(self):
        """Read one value and return it."""
        self._skip()
        if self.pos >= len(self.text):
            raise JsLiteralError("unexpected end of source")
        char = self.text[self.pos]
        if char in "\"'":
            return self._string()
        if char == "{":
            return self._object()
        if char == "[":
            return self._array()
        for word, value in (("true", True), ("false", False), ("null", None)):
            if self.text.startswith(word, self.pos):
                self.pos += len(word)
                return value
        match = re.match(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", self.text[self.pos:])
        if match:
            self.pos += match.end()
            raw = match.group(0)
            return int(raw) if "." not in raw and "e" not in raw.lower() else float(raw)
        raise JsLiteralError(f"cannot read a value at offset {self.pos}")

    def _identifier(self) -> str:
        """Read a bare identifier (an object key such as `en`)."""
        match = re.match(r"[A-Za-z_$][A-Za-z0-9_$]*", self.text[self.pos :])
        if not match:
            raise JsLiteralError(f"cannot read an identifier at offset {self.pos}")
        self.pos += match.end()
        return match.group(0)

    def _object(self) -> dict:
        """Read an object literal."""
        self.pos += 1  # "{"
        out: dict = {}
        while True:
            self._skip()
            if self.pos >= len(self.text):
                raise JsLiteralError("unterminated object")
            if self.text[self.pos] == "}":
                self.pos += 1
                return out
            if self.text[self.pos] in "\"'":
                key = self.value()
            else:
                key = self._identifier()
            self._skip()
            if self.text[self.pos] != ":":
                raise JsLiteralError("expected ':' in an object literal")
            self.pos += 1
            out[key] = self.value()
            self._skip()
            if self.pos < len(self.text) and self.text[self.pos] == ",":
                self.pos += 1

    def _array(self) -> list:
        """Read an array literal."""
        self.pos += 1  # "["
        out: list = []
        while True:
            self._skip()
            if self.pos >= len(self.text):
                raise JsLiteralError("unterminated array")
            if self.text[self.pos] == "]":
                self.pos += 1
                return out
            out.append(self.value())
            self._skip()
            if self.pos < len(self.text) and self.text[self.pos] == ",":
                self.pos += 1


def js_binding(text: str, name: str):
    """Read the value of a top-level `const <name> = ...` binding."""
    match = re.search(rf"\b(?:const|let|var)\s+{re.escape(name)}\s*=", text)
    if not match:
        return None
    reader = JsReader(text)
    reader.pos = match.end()
    try:
        return reader.value()
    except JsLiteralError:
        return None


def read_text(path: str) -> str:
    """Read a text file, tolerating a missing file."""
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# 3. The sources.
# ---------------------------------------------------------------------------


def load_catalogue() -> dict[str, dict[str, str]]:
    """The catalogues of the localisation module."""
    table = js_binding(read_text(I18N_JS), "CATALOGUES")
    if not isinstance(table, dict):
        return {}
    return {code: dict(values) for code, values in table.items() if isinstance(values, dict)}


def load_known_labels() -> list[tuple[str, str]]:
    """The (selector, catalogue key) pairs of the runtime accessible names."""
    table = js_binding(read_text(KEYMAP_JS), "KNOWN_LABELS")
    if not isinstance(table, list):
        return []
    return [(row[0], row[1]) for row in table if isinstance(row, list) and len(row) == 2]


def load_command_ids() -> list[str]:
    """The ids of the keyboard commands."""
    text = read_text(KEYMAP_JS)
    return re.findall(r"\{\s*\n\s*id:\s*\"([a-zA-Z0-9_.]+)\"", text)


def load_css() -> str:
    """Every stylesheet of the interface, concatenated."""
    out = []
    for name in sorted(os.listdir(CSS_DIR)):
        if name.endswith(".css"):
            out.append(read_text(os.path.join(CSS_DIR, name)))
    return "\n".join(out)


def top_level_bindings(text: str) -> dict[str, int]:
    """The names a module declares at its top level, and how often.

    Only the declarations of the top level count: a name inside a function
    or inside a block belongs to that scope.  The imports count as well — the
    binding of an import is a name of the module, exactly like a `const`.
    """
    found: dict[str, int] = {}

    def bump(name: str) -> None:
        name = name.strip()
        if name:
            found[name] = found.get(name, 0) + 1

    for line in text.splitlines():
        if not line.strip() or line[0] in " \t)":
            continue
        match = _IMPORT_RE.match(line)
        if match:
            clause = match.group("clause")
            default = re.match(r"([A-Za-z_$][\w$]*)\s*(?:,|$)", clause)
            if default:
                bump(default.group(1))
            star = re.search(r"\*\s+as\s+([A-Za-z_$][\w$]*)", clause)
            if star:
                bump(star.group(1))
            named = re.search(r"\{([^}]*)\}", clause)
            if named:
                for part in named.group(1).split(","):
                    part = part.strip()
                    if not part:
                        continue
                    bump(part.split(" as ")[-1].strip())
            continue
        match = _DECLARATION_RE.match(line)
        if match:
            bump(match.group(1))
    return found


def load_duplicate_bindings() -> list[tuple[str, str, int]]:
    """Every name a module of the runtime declares more than once."""
    rows: list[tuple[str, str, int]] = []
    for name in sorted(os.listdir(JS_DIR)):
        if not name.endswith(".js"):
            continue
        source = read_text(os.path.join(JS_DIR, name))
        for binding, count in top_level_bindings(source).items():
            if count > 1:
                rows.append((name, binding, count))
    return rows


# ---------------------------------------------------------------------------
# 4. The effective interface.
# ---------------------------------------------------------------------------

#: The attributes `applyAccessibility()` of keymap.js adds.  The audit keeps
#: this table in sync with the source: see `check_runtime_drift`.
RUNTIME_ATTRIBUTES: dict[str, dict[str, str]] = {
    "#toast": {"role": "status", "aria-live": "polite", "aria-atomic": "true"},
    "#st-cursor": {"aria-live": "polite", "aria-atomic": "true"},
    "#st-sel": {"aria-live": "polite", "aria-atomic": "true"},
    "#st-level": {"aria-live": "polite", "aria-atomic": "true"},
    "#st-latency": {"aria-live": "polite", "aria-atomic": "true"},
    "#st-engine": {"role": "status", "aria-live": "polite"},
    "#canvas-wrap": {"tabindex": "0", "role": "group"},
    "#spec-canvas": {"role": "img"},
    "#wave-canvas": {"role": "img"},
    "#view-mode": {"role": "radiogroup"},
    "#inspector-tabs": {"role": "tablist"},
    "#chain-strip": {"role": "list"},
}

#: The catalogue keys the runtime puts on the elements above.
RUNTIME_LABELS: dict[str, str] = {
    "#canvas-wrap": "app.tagline",
    "#spec-canvas": "topbar.viewMode.spectrogram",
    "#wave-canvas": "topbar.viewMode.spectrogram",
    "#view-mode": "topbar.viewMode",
    "#inspector-tabs": "inspector.tabs",
    "#chain-strip": "chain.label",
}


def _accessible_name(node: Element, resolve) -> tuple[str, str]:
    """The accessible name of a node and the source it came from.

    Follows the accessible-name computation: `aria-label`, then
    `aria-labelledby`, then the content of a button or a link, then the
    `title` attribute, then the `placeholder` of a field.
    """
    label = node.get("aria-label").strip()
    if label:
        return label, "aria-label"
    labelled_by = node.get("aria-labelledby").strip()
    if labelled_by:
        parts = []
        for element_id in labelled_by.split():
            target = resolve(element_id)
            if target is not None:
                parts.append(target.text)
        joined = " ".join(p for p in parts if p).strip()
        if joined:
            return joined, "aria-labelledby"
    if node.tag in {"button", "a"}:
        content = node.text.strip()
        if content:
            return content, "content"
    for label_node in node.labels():
        content = label_node.text.strip()
        if content:
            return content, "label"
    title = node.get("title").strip()
    if title:
        return title, "title"
    placeholder = node.get("placeholder").strip()
    if placeholder:
        return placeholder, "placeholder"
    return "", "none"


def _has_name_like_keymap(node: Element) -> bool:
    """The name test of `hasAccessibleName()` in keymap.js."""
    if node.get("aria-label").strip():
        return True
    if node.get("aria-labelledby").strip():
        return True
    if node.get("title").strip():
        return True
    if node.tag in {"button", "a"} and node.text.strip():
        return True
    return node.closest("label") is not None


def build_effective_dom(root: Element, catalogue: dict[str, str], known_labels) -> Element:
    """The markup plus everything the runtime adds to it."""
    def resolve(element_id: str):
        return root.find_by_id(element_id)

    def lookup(key: str) -> str:
        return catalogue.get(key, "")

    # translateDocument(): the data-i18n* attributes.
    for node in root.descendants():
        key = node.get("data-i18n").strip()
        if key:
            node.children = [lookup(key) or key]
        title = node.get("data-i18n-title").strip()
        if title:
            node.set("title", lookup(title) or title)
        aria = node.get("data-i18n-aria").strip()
        if aria:
            node.set("aria-label", lookup(aria) or aria)
        placeholder = node.get("data-i18n-placeholder").strip()
        if placeholder:
            node.set("placeholder", lookup(placeholder) or placeholder)

    # applyAccessibility(): the roles, the live regions and the focus target.
    for selector, attributes in RUNTIME_ATTRIBUTES.items():
        node = resolve(selector.lstrip("#"))
        if node is None:
            continue
        for name, value in attributes.items():
            node.set(name, value)
    for selector, key in RUNTIME_LABELS.items():
        node = resolve(selector.lstrip("#"))
        if node is None:
            continue
        if node.get("aria-label").strip():
            continue
        node.set("aria-label", lookup(key) or key)

    # applyAccessibility(): the names of the controls that carry none.
    for selector, key in known_labels:
        node = resolve(selector.lstrip("#"))
        if node is None:
            continue
        if _has_name_like_keymap(node):
            continue
        node.set("aria-label", lookup(key) or key)
    for node in root.descendants():
        if node.tag not in {"button", "input", "select", "textarea"} and node.tag != "a":
            continue
        if node.get("href") == "" and node.tag == "a":
            continue
        if _has_name_like_keymap(node):
            continue
        title = node.get("title").strip()
        if title:
            node.set("aria-label", title)
    return root


# ---------------------------------------------------------------------------
# 5. The checks.
# ---------------------------------------------------------------------------


def _is_exposed(node: Element) -> bool:
    """True when the node is part of the accessibility tree."""
    if "hidden" in node.attrs:
        return False
    if node.get("aria-hidden").lower() == "true":
        return False
    if node.tag == "input" and node.get("type").lower() == "hidden":
        return False
    return True


def _is_interactive(node: Element) -> bool:
    """True when the node is a control the user can operate."""
    if node.tag in INTERACTIVE_TAGS:
        if node.tag == "a" and not node.get("href"):
            return False
        if node.tag == "input" and node.get("type").lower() == "hidden":
            return False
        return True
    role = node.get("role").lower()
    return role in INTERACTIVE_ROLES


def _is_focusable(node: Element) -> bool:
    """True when the keyboard can reach the node."""
    if node.tag in NATIVELY_FOCUSABLE:
        return True
    if node.tag == "input":
        return True
    if node.tag == "a" and node.get("href"):
        return True
    tabindex = node.get("tabindex").strip()
    if tabindex:
        try:
            return int(tabindex) >= 0
        except ValueError:
            return False
    return False


def _is_symbol_only(name: str) -> bool:
    """True when the name carries no word -- "▶" instead of "Preview"."""
    if not name:
        return False
    letters = [char for char in name if char.isalnum()]
    return not letters


def audit() -> dict:
    """Run the audit and return the report."""
    errors: list[dict] = []
    warnings: list[dict] = []

    catalogue = load_catalogue()
    english = catalogue.get("en", {})
    known_labels = load_known_labels()
    root = parse_html(read_text(INDEX_HTML))
    effective = build_effective_dom(root, english, known_labels)

    def resolve(element_id: str):
        return effective.find_by_id(element_id)

    # -- E6: every data-i18n key resolves ---------------------------------
    for node in effective.descendants():
        for attribute in ("data-i18n", "data-i18n-title", "data-i18n-aria",
                          "data-i18n-placeholder"):
            key = node.get(attribute).strip()
            if key and key not in english:
                errors.append(
                    {
                        "code": "missing-translation",
                        "target": node.get("id") or node.tag,
                        "message": f"{attribute}=\"{key}\" does not resolve in the English "
                        f"catalogue",
                    }
                )

    # -- E1 / E2: every exposed control has a name and is reachable ------
    for node in effective.descendants():
        if not _is_interactive(node):
            continue
        if not _is_exposed(node):
            continue
        target = node.get("id") or node.tag
        name, source = _accessible_name(node, resolve)
        if not name:
            errors.append(
                {
                    "code": "unlabelled-control",
                    "target": target,
                    "message": f"<{node.tag}> has no accessible name",
                    "fix": 'add aria-label="…", a <label for>, or a visible text label',
                }
            )
        else:
            if _is_symbol_only(name):
                warnings.append(
                    {
                        "code": "symbol-name",
                        "target": target,
                        "message": f"<{node.tag}> is named \"{name}\" — a symbol is not a usable "
                        f"name",
                        "fix": 'add an aria-label with the action, e.g. aria-label="Preview"',
                    }
                )
            elif source == "placeholder":
                warnings.append(
                    {
                        "code": "placeholder-name",
                        "target": target,
                        "message": f"<{node.tag}> is named by its placeholder only",
                        "fix": 'add an aria-label; the placeholder disappears while typing',
                    }
                )
        if not _is_focusable(node):
            errors.append(
                {
                    "code": "not-keyboard-reachable",
                    "target": target,
                    "message": f"<{node.tag}> cannot be reached with the keyboard",
                    "fix": 'add tabindex="0"',
                }
            )

    # -- E3: the live regions ---------------------------------------------
    for selector in LIVE_REGION_IDS:
        node = resolve(selector.lstrip("#"))
        if node is None:
            warnings.append(
                {
                    "code": "missing-element",
                    "target": selector,
                    "message": f"{selector} does not exist in the document",
                }
            )
            continue
        if not node.get("aria-live").strip():
            errors.append(
                {
                    "code": "missing-live-region",
                    "target": selector,
                    "message": f"{selector} is not a live region",
                    "fix": 'add aria-live="polite"',
                }
            )

    # -- E4 / E5: the stylesheets -----------------------------------------
    css = load_css()
    if not _has_focus_ring(css):
        errors.append(
            {
                "code": "no-focus-ring",
                "target": "css",
                "message": "no stylesheet draws a visible focus ring",
                "fix": "add a :focus-visible rule with an outline",
            }
        )
    if 'data-contrast="high"' not in css:
        errors.append(
            {
                "code": "no-high-contrast-variant",
                "target": "css",
                "message": "no high-contrast variant of the chrome",
                "fix": 'add a [data-contrast="high"] block to the stylesheets',
            }
        )

    # -- E7: the runtime model is still in sync with keymap.js ------------
    keymap_source = read_text(KEYMAP_JS)
    for selector, attributes in RUNTIME_ATTRIBUTES.items():
        for name in attributes:
            if f'"{name}"' not in keymap_source:
                errors.append(
                    {
                        "code": "runtime-drift",
                        "target": selector,
                        "message": (
                            f"keymap.js no longer sets \"{name}\" on {selector}; "
                            "the audit model has to follow"
                        ),
                    }
                )
    if not known_labels:
        errors.append(
            {
                "code": "runtime-drift",
                "target": "keymap.js",
                "message": "KNOWN_LABELS could not be read; the audit model is stale",
            }
        )

    # -- E8: every module of the runtime loads -----------------------------
    #    A duplicate declaration is a SyntaxError: the module does not load
    #    at all, and the feature it carries is gone.  A migration that adds
    #    the import of the catalogue to every module has to check whether it
    #    is already there, or it breaks the modules it touches.
    for module, binding, count in load_duplicate_bindings():
        errors.append(
            {
                "code": "duplicate-declaration",
                "target": f"{module}: {binding}",
                "message": (
                    f"{module} declares {binding!r} {count} times at its top level; "
                    "the module does not load (a SyntaxError)"
                ),
                "fix": (
                    "remove the duplicate declaration — a migration that inserts a "
                    "block has to check whether the block is already there"
                ),
            }
        )

    # -- W3: the canvas has no textual summary ----------------------------
    for selector in ("#spec-canvas", "#wave-canvas"):
        node = resolve(selector.lstrip("#"))
        if node is None:
            continue
        summary = node.get("aria-label").strip()
        if not summary or not re.search(r"\d", summary):
            warnings.append(
                {
                    "code": "no-canvas-summary",
                    "target": selector,
                    "message": "the canvas carries no textual summary of the signal",
                    "fix": "describe duration, sample rate, channels and peak level in text",
                }
            )

    # -- W4: the chrome has no locale switch ------------------------------
    switch = None
    for node in effective.descendants():
        if node.tag == "select" and "locale" in node.get("id").lower():
            switch = node
            break
    if switch is None:
        warnings.append(
            {
                "code": "no-locale-switch",
                "target": "topbar",
                "message": "the chrome has no control that switches the locale",
                "fix": 'add a <select id="locale-switch"> and call setLocale() from it',
            }
        )

    # -- W5: catalogue keys that nothing uses yet --------------------------
    #   A key counts as used when a source names it in a call to t(), when a
    #   call to t() builds it out of a template (`t(`status.engine.${…}`)`),
    #   when a source holds it as a string literal (the tables of the modules
    #   list their keys that way) or when a pending patch of another agent
    #   will use it.
    used = set()
    prefixes = set()
    without_catalogue = []
    for name in sorted(os.listdir(JS_DIR)):
        if not name.endswith(".js"):
            continue
        source = read_text(os.path.join(JS_DIR, name))
        found = re.findall(r"""\bt\(\s*["'`]([a-zA-Z0-9_.]+)["'`]""", source)
        used.update(found)
        for match in re.finditer(r"""\bt\(\s*`([^`$]*?)\$\{""", source):
            prefixes.add(match.group(1))
        if name != "i18n.js":
            for match in re.finditer(
                r"""["'`]([a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+)+)["'`]""", source
            ):
                if match.group(1) in english:
                    used.add(match.group(1))
        if name not in {"i18n.js", "api.js"} and not found:
            without_catalogue.append(name)
    for command_id in load_command_ids():
        used.add(f"key.{command_id}.label")
        used.add(f"key.{command_id}.description")
    for node in effective.descendants():
        for attribute in ("data-i18n", "data-i18n-title", "data-i18n-aria",
                          "data-i18n-placeholder"):
            key = node.get(attribute).strip()
            if key:
                used.add(key)
    patch_dir = os.path.join(ROOT, "var", "patches")
    if os.path.isdir(patch_dir):
        for name in sorted(os.listdir(patch_dir)):
            source = read_text(os.path.join(patch_dir, name))
            for match in re.finditer(
                r"""["'`]([a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+)+)["'`]""", source
            ):
                if match.group(1) in english:
                    used.add(match.group(1))
    for key in english:
        if any(key.startswith(prefix) for prefix in prefixes):
            used.add(key)
    unused = sorted(set(english) - used)
    if unused:
        warnings.append(
            {
                "code": "unused-catalogue-keys",
                "target": "i18n.js",
                "message": f"{len(unused)} catalogue keys are not referenced yet: {unused}",
            }
        )
    if without_catalogue:
        warnings.append(
            {
                "code": "module-without-catalogue",
                "target": "aurui/js",
                "message": (
                    "these modules render their text directly instead of through the "
                    f"catalogue: {without_catalogue}"
                ),
                "fix": "import t() from ./i18n.js and translate the strings of the module",
            }
        )

    return {
        "errors": errors,
        "warnings": warnings,
        "counts": {
            "locales": sorted(catalogue),
            "catalogue_keys": {code: len(values) for code, values in sorted(catalogue.items())},
            "controls": sum(
                1
                for node in effective.descendants()
                if _is_interactive(node) and _is_exposed(node)
            ),
            "errors": len(errors),
            "warnings": len(warnings),
        },
    }


def _has_focus_ring(css: str) -> bool:
    """True when a stylesheet draws a visible focus ring."""
    for selector, body in _css_rules(css):
        if ":focus-visible" not in selector:
            continue
        for match in re.finditer(r"outline(?:-[a-z]+)?\s*:\s*([^;]+);", body):
            value = match.group(1).strip().lower()
            if value in {"none", "0"}:
                continue
            return True
    return False


def _css_rules(css: str) -> list[tuple[str, str]]:
    """Every (selector, body) pair of a stylesheet, nested blocks included."""
    rules: list[tuple[str, str]] = []
    clean = re.sub(r"/\*.*?\*/", " ", css, flags=re.S)
    stack: list[tuple[str, int]] = []
    start = 0
    index = 0
    while index < len(clean):
        char = clean[index]
        if char == "{":
            selector = clean[start:index].strip()
            stack.append((selector, index))
            start = index + 1
        elif char == "}":
            if stack:
                selector, _ = stack.pop()
                body = clean[start:index]
                rules.append((selector, body))
            start = index + 1
        index += 1
    return rules


# ---------------------------------------------------------------------------
# 6. The report.
# ---------------------------------------------------------------------------


def format_report(report: dict) -> str:
    """The human readable report."""
    lines: list[str] = []
    counts = report["counts"]
    lines.append("Auralis — accessibility audit")
    lines.append("=" * 60)
    lines.append(
        "locales: {locales}".format(
            locales=", ".join(
                f"{code} ({counts['catalogue_keys'][code]})" for code in counts["locales"]
            )
        )
    )
    lines.append(f"controls audited: {counts['controls']}")
    lines.append("")
    lines.append(f"errors: {counts['errors']}")
    for item in report["errors"]:
        lines.append(f"  [{item['code']}] {item['target']}: {item['message']}")
        if item.get("fix"):
            lines.append(f"      fix: {item['fix']}")
    lines.append("")
    lines.append(f"warnings: {counts['warnings']}")
    for item in report["warnings"]:
        lines.append(f"  [{item['code']}] {item['target']}: {item['message']}")
        if item.get("fix"):
            lines.append(f"      fix: {item['fix']}")
    lines.append("")
    lines.append("PASS" if not report["errors"] else "FAIL")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Run the audit from the command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    args = parser.parse_args(argv)

    report = audit()
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(format_report(report))
    if report["errors"]:
        return 1
    if args.strict and report["warnings"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
