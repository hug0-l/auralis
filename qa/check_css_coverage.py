#!/usr/bin/env python3
"""
qa/check_css_coverage.py -- verify that every CSS class referenced by the UI
JavaScript is actually styled by app.css / tokens.css.

The UI was built by several agents in parallel; a class-name mismatch is the
classic integration break (elements render unstyled).  This tool finds them.
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS_DIR = os.path.join(ROOT, "aurui", "js")
CSS_DIR = os.path.join(ROOT, "aurui", "css")

# class names assigned via class="..." or classList.add/remove/toggle
RE_CLASS_ATTR = re.compile(r"""class\s*[:=]\s*["'`]([^"'`$]*)["'`]""")
RE_CLASSLIST = re.compile(r"""classList\.(?:add|remove|toggle)\(\s*["'`]([^"'`$]+)["'`]""")
RE_CSS_CLASS = re.compile(r"\.([A-Za-z_][A-Za-z0-9_-]*)")


def js_classes() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for fn in sorted(os.listdir(JS_DIR)):
        if not fn.endswith(".js"):
            continue
        text = open(os.path.join(JS_DIR, fn), encoding="utf-8").read()
        found: set[str] = set()
        for pattern in (RE_CLASS_ATTR, RE_CLASSLIST):
            for m in pattern.finditer(text):
                for tok in m.group(1).split():
                    if tok and not tok.startswith("{") and "$" not in tok:
                        found.add(tok)
        out[fn] = found
    return out


def css_classes() -> set[str]:
    found: set[str] = set()
    for fn in sorted(os.listdir(CSS_DIR)):
        if not fn.endswith(".css"):
            continue
        text = open(os.path.join(CSS_DIR, fn), encoding="utf-8").read()
        for m in RE_CSS_CLASS.finditer(text):
            found.add(m.group(1))
    # some UI modules ship their own scoped stylesheet injected at runtime
    for fn in sorted(os.listdir(JS_DIR)):
        if not fn.endswith(".js"):
            continue
        text = open(os.path.join(JS_DIR, fn), encoding="utf-8").read()
        for m in RE_CSS_CLASS.finditer(text):
            found.add(m.group(1))
    return found


def main() -> int:
    js = js_classes()
    css = css_classes()
    print(f"css files: {len(os.listdir(CSS_DIR))}  styled classes: {len(css)}\n")
    missing_total = 0
    for fn, classes in js.items():
        if not classes:
            continue
        missing = sorted(c for c in classes if c not in css)
        missing_total += len(missing)
        status = "OK  " if not missing else "GAP "
        print(f"  [{status}] {fn:<14} classes used: {len(classes):>3}   unstyled: {len(missing)}")
        for c in missing:
            print(f"          - .{c}")
    print()
    if missing_total:
        print(f"{missing_total} class(es) referenced by JS but not styled.")
        print("Fix: add rules to aurui/css/app.css (or a new components.css linked from "
              "index.html).")
        return 1
    print("All JS-referenced classes are styled.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
