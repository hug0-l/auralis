#!/usr/bin/env python3
"""
tools/localise_ui.py -- the idempotent migration of the interface modules.

Every module of the runtime of the interface builds its text through the
catalogue of ``aurui/js/i18n.js``, so every one of them imports it::

    import { t } from "./i18n.js";

A migration that *appends* that line to every module inserts it a second
time when the file already has it.  The result is a duplicate declaration, and
a duplicate declaration is a **SyntaxError**: the module does not load at
all, and the feature it carries is gone with it::

    SyntaxError: Identifier 't' has already been declared

The rule of this tool is therefore simple: the block is inserted **once**, and
the tool checks whether it is already there before it touches the file.  It
is idempotent -- running it twice changes nothing the second time.

What is checked

    the catalogue import is there exactly once in every runtime module;
    the helper blocks the migration carries are declared exactly once;
    every module still parses (``node --check`` through a ``.mjs`` copy).

Usage::

    python tools/localise_ui.py            # report, exit 1 when something is wrong
    python tools/localise_ui.py --fix      # repair the modules, then report
    python tools/localise_ui.py --json     # machine readable report

Exit codes::

    0   every runtime module is ready
    1   a module misses the import, carries it twice, or does not parse
    2   the tool could not run (node is missing, the tree is missing, ...)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS_DIR = os.path.join(ROOT, "aurui", "js")

#: The modules of the runtime that build their text through the catalogue.
#:
#: The shell (``app.js``) has its own wrapper around the catalogue, the
#: client (``api.js``) shows no text at all and the catalogue (``i18n.js``)
#: *is* the catalogue: none of the three may import it, and the tool leaves
#: them alone.
CATALOGUE_MODULES = (
    "analysis.js",
    "batch.js",
    "browser.js",
    "chain.js",
    "keymap.js",
    "panel.js",
    "preset_manager.js",
    "session.js",
    "spectral_editor.js",
    "view.js",
    "widgets.js",
)

#: The import of the catalogue.
CATALOGUE_IMPORT = 'import { t } from "./i18n.js";'

#: An import of the catalogue module.
_I18N_IMPORT_RE = re.compile(
    r"""^\s*import\s+(?P<clause>[^"']*?)\s*from\s*["']\./i18n\.js["']"""
)

#: The helper blocks the migration carries next to the import.  Every one of
#: them is a top level declaration, so it may only be there once.
HELPER_BLOCKS = (
    "const CATEGORY_KEYS = {",
    "function stateLabel(",
    "function modeLabel(",
)

#: An import statement of a module.
_IMPORT_RE = re.compile(r"""^\s*import\s+(?P<clause>[^"']*?)\s*from\s*["']""")

#: A declaration of a name at the top level of a module.
_DECLARATION_RE = re.compile(
    r"""^\s*(?:export\s+(?:default\s+)?)?(?:async\s+)?(?:const|let|var|function|class)\s+"""
    r"""([A-Za-z_$][\w$]*)"""
)


def read_text(path: str) -> str:
    """Read a text file, tolerating a missing file."""
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""


def write_text(path: str, text: str) -> None:
    """Write a text file, keeping the line endings of the file."""
    original = read_text(path)
    eol = "\r\n" if "\r\n" in original else "\n"
    if eol == "\r\n":
        text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def top_level_bindings(text: str) -> dict[str, int]:
    """The names a module declares at its top level, and how often."""
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
                    if part:
                        bump(part.split(" as ")[-1].strip())
            continue
        match = _DECLARATION_RE.match(line)
        if match:
            bump(match.group(1))
    return found


def import_count(text: str) -> int:
    """How often the catalogue is imported with the name `t`.

    The import may carry more names (`import { t, onLocaleChange } from
    "./i18n.js";`), so the binding is looked up, not the whole line.
    """
    count = 0
    for line in text.splitlines():
        match = _I18N_IMPORT_RE.match(line)
        if not match:
            continue
        clause = match.group("clause")
        named = re.search(r"\{([^}]*)\}", clause)
        if named:
            for part in named.group(1).split(","):
                if part.strip().split(" as ")[-1].strip() == "t":
                    count += 1
            continue
        default = re.match(r"([A-Za-z_$][\w$]*)", clause)
        if default and default.group(1) == "t":
            count += 1
    return count


def insert_import(text: str) -> str:
    """Put the catalogue import into a module that does not have it.

    The import goes after the last import of the module, so the imports stay
    together at the top of the file.
    """
    lines = text.splitlines(keepends=True)
    last = -1
    for index, line in enumerate(lines):
        if _IMPORT_RE.match(line):
            last = index
    if last < 0:
        # No import at all: the block goes after the header comment.
        for index, line in enumerate(lines):
            if line.strip() and not line.lstrip().startswith(("*", "/", "#")):
                last = index - 1
                break
        else:
            last = len(lines) - 1
    lines.insert(last + 1, CATALOGUE_IMPORT + "\n")
    return "".join(lines)


def repair(text: str) -> str:
    """Make the catalogue import appear exactly once.

    The first copy stays where it is; every further copy is dropped, and a
    module without the import gets one.  A module that already imports the
    catalogue for another name gets the name `t` added to that import.
    """
    if import_count(text) == 0:
        lines = text.splitlines(keepends=True)
        for index, line in enumerate(lines):
            match = _I18N_IMPORT_RE.match(line)
            if not match:
                continue
            clause = match.group("clause")
            named = re.search(r"\{([^}]*)\}", clause)
            if named:
                inner = named.group(1).strip()
                new_clause = "{" + (inner + ", t" if inner else "t") + "}"
            else:
                new_clause = clause.strip() + ", { t }"
            lines[index] = line.replace(clause, new_clause, 1)
            return "".join(lines)
        return insert_import(text)
    first = True
    out: list[str] = []
    for line in text.splitlines(keepends=True):
        match = _I18N_IMPORT_RE.match(line)
        if match:
            clause = match.group("clause")
            named = re.search(r"\{([^}]*)\}", clause)
            binds_t = False
            if named:
                binds_t = any(
                    part.strip().split(" as ")[-1].strip() == "t"
                    for part in named.group(1).split(",")
                    if part.strip()
                )
            else:
                default = re.match(r"([A-Za-z_$][\w$]*)", clause)
                binds_t = bool(default and default.group(1) == "t")
            if binds_t:
                if first:
                    first = False
                    out.append(line)
                continue
        out.append(line)
    return "".join(out)


def parses(path: str) -> tuple[bool, str]:
    """Does the module parse as an ES module?"""
    node = shutil.which("node")
    if node is None:
        return True, "node is not on PATH; the parse was skipped"
    text = read_text(path)
    with tempfile.TemporaryDirectory(prefix="auralis-localise-") as tmp:
        target = os.path.join(tmp, "module.mjs")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(text)
        try:
            completed = subprocess.run(
                [node, "--check", target],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return True, f"node could not be run: {exc}"
    if completed.returncode == 0:
        return True, ""
    message = " ".join((completed.stderr or "").split())[:200]
    return False, message or "the module does not parse"


def inspect(root: str) -> dict:
    """Look at every module of the tree."""
    js_dir = os.path.join(root, "aurui", "js")
    if not os.path.isdir(js_dir):
        js_dir = os.path.join(root, "js")
    modules: list[dict] = []
    names = [name for name in sorted(os.listdir(js_dir)) if name.endswith(".js")]
    for name in names:
        path = os.path.join(js_dir, name)
        text = read_text(path)
        count = import_count(text)
        duplicates = {
            binding: times
            for binding, times in top_level_bindings(text).items()
            if times > 1
        }
        ok, message = parses(path)
        managed = name in CATALOGUE_MODULES
        problems: list[str] = []
        if count > 1:
            problems.append("the catalogue import is there more than once")
        if managed and count == 0:
            problems.append("the catalogue import is missing")
        if duplicates:
            problems.append("a name is declared more than once")
        if not ok:
            problems.append(f"the module does not parse: {message}")
        modules.append(
            {
                "module": name,
                "managed": managed,
                "imports": count,
                "duplicates": duplicates,
                "parses": ok,
                "message": message,
                "problems": problems,
            }
        )
    problems = [item for item in modules if item["problems"]]
    return {"modules": modules, "problems": problems}


def fix(root: str) -> list[str]:
    """Repair the modules of the tree; return the names of the changed ones."""
    js_dir = os.path.join(root, "aurui", "js")
    if not os.path.isdir(js_dir):
        js_dir = os.path.join(root, "js")
    changed: list[str] = []
    for name in sorted(os.listdir(js_dir)):
        if not name.endswith(".js") or name not in CATALOGUE_MODULES:
            continue
        path = os.path.join(js_dir, name)
        text = read_text(path)
        repaired = repair(text)
        if repaired == text:
            continue
        write_text(path, repaired)
        changed.append(name)
    return changed


def main(argv: list[str] | None = None) -> int:
    """Run the tool from the command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--root", default=ROOT, help="the root of the tree")
    parser.add_argument("--fix", action="store_true", help="repair the modules")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)

    root = os.path.abspath(args.root)
    js_dir = os.path.join(root, "aurui", "js")
    if not os.path.isdir(js_dir):
        js_dir = os.path.join(root, "js")
    if not os.path.isdir(js_dir):
        print(f"the interface is missing: {js_dir}", file=sys.stderr)
        return 2

    changed: list[str] = []
    if args.fix:
        changed = fix(root)
        if changed:
            print(f"repaired: {', '.join(changed)}")

    report = inspect(root)
    if args.json:
        payload = dict(report)
        payload["changed"] = changed
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 1 if report["problems"] else 0

    print(f"runtime modules: {len(report['modules'])}")
    for item in report["modules"]:
        state = ", ".join(item["problems"]) if item["problems"] else "ok"
        print(f"  {item['module']:<24} {item['imports']} import(s)  {state}")
    print()
    if report["problems"]:
        print(f"FAIL — {len(report['problems'])} module(s) are not ready")
        return 1
    print("PASS — every runtime module loads")
    return 0


if __name__ == "__main__":
    sys.exit(main())
