#!/usr/bin/env python3
"""
tools/lint_check.py - the style gate of the Auralis repository.

The gate is deliberately small, fast and dependency free: it uses nothing
but the Python standard library, so it runs on every machine that can run
the test suite.  Four families of checks are performed:

    python-style   Python sources: line length, no tab characters, no
                   trailing whitespace, a newline at the end of the file.
    trademark      No third-party product, vendor or trademark token in any
                   text file of the repository (CONTRACT.md, section 0).
    js-syntax      Every JavaScript source parses: ``node --check`` is run
                   on it.  Skipped, with a message, when node is missing.
    css            Every stylesheet has balanced braces and hard-codes no
                   colour outside ``aurui/css/tokens.css``.

Every finding is reported as ``file:line: severity [rule] message`` so an
editor or a pull request comment can jump straight to it.

Usage::

    python tools/lint_check.py                  # the whole gate
    python tools/lint_check.py --json           # machine readable report
    python tools/lint_check.py --only css,js-syntax
    python tools/lint_check.py --max-line-length 100 --max-line-length-error 150
    python tools/lint_check.py --require js-syntax
    python tools/lint_check.py --warning-budget 150

Exit codes::

    0   no error level finding, the warning budget is not exceeded
    1   at least one error level finding, the warning budget is exceeded, or
        a required check was skipped
    2   the tool could not run (the repository root is missing, ...)

The warnings are counted.  A warning that is always a warning teaches
nobody to read the report, so the number of warnings is a budget and the
gate fails when there are more of them than the budget allows.  The budget
may only go down: it is the number of long lines the tree carries today.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Sequence

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------

#: Directories that never hold first party source.  They are either build
#: output, a cache, a third party SDK or the runtime scratch.
SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".idea",
        ".vscode",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".tox",
        ".venv",
        "venv",
        "env",
        "ENV",
        "__pycache__",
        "node_modules",
        "build",
        "dist",
        "var",
        "vendor",
        "htmlcov",
    }
)

#: Directory name prefixes that mark a build output.  A release build writes
#: into "dist", and a test run may write into "dist-noplugins" or the like.
SKIP_DIR_PREFIXES: tuple[str, ...] = ("build", "dist", "out", "target", "release-")

#: The suffixes that are read as text.  A file with another suffix is either
#: binary or is not part of the source tree.
TEXT_SUFFIXES = frozenset(
    {
        ".bat",
        ".c",
        ".cc",
        ".cfg",
        ".cmd",
        ".cpp",
        ".css",
        ".csv",
        ".def",
        ".h",
        ".hpp",
        ".htm",
        ".html",
        ".ini",
        ".iss",
        ".js",
        ".json",
        ".jsonc",
        ".jsx",
        ".md",
        ".mjs",
        ".ps1",
        ".py",
        ".pyi",
        ".rc",
        ".rst",
        ".sh",
        ".sql",
        ".svg",
        ".toml",
        ".tsv",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
    }
)

#: Files whose suffix says nothing about their content.
TEXTLESS_SUFFIXES = frozenset({""})

#: The stylesheet that owns the design tokens.  It is the one file that may
#: contain colour literals.
TOKENS_CSS = "aurui/css/tokens.css"

#: The name of that stylesheet.  The exemption follows the name, so a copy
#: of the token file inside a build output is exempt as well.
TOKENS_CSS_NAME = "tokens.css"

#: Third party tokens that must never appear in the repository.  They are
#: spelled in pieces on purpose: this file is part of the codebase, and the
#: codebase must not contain a third party trademark - not even in the
#: scanner that looks for one (CONTRACT.md, section 0).
FORBIDDEN_TOKENS: tuple[str, ...] = ("r" + "x", "izo" + "tope", "r" + "2" + "r")

#: The files the trademark scan does not apply to.  Each entry is a glob
#: pattern against the repository relative path.
#:
#: A file belongs here when it has to name the tokens in order to forbid
#: them.  Every entry carries its reason; the list is the audit trail of the
#: decision, not a way around the rule.
TRADEMARK_EXEMPT: tuple[str, ...] = (
    # The contract names the tokens in order to forbid them; it is the one
    # document that has to spell them out.
    "CONTRACT.md",
    # The other gate of the house rules names them as well.
    "tools/hygiene_check.py",
    # This gate: the tokens are spelled in pieces, and the file is excluded
    # so it cannot report on its own machinery.
    "tools/lint_check.py",
    # One-off probe scripts of the qa folder.  They are scratch work, they
    # are never shipped, and they use a two letter variable name that
    # happens to collide with a token.
    "qa/_*.py",
)

#: A declaration of forbidden names, for example
#: ``FORBIDDEN_NAMES = ("...", "...")``.  A token inside such a declaration
#: is a rule, not a mention, so it does not count as a hit.
_NAME_LIST_DECLARATION = re.compile(r"^\s*[A-Z][A-Z0-9_]*\s*[:=]\s*.*[\(\[\{]")

#: A quoted string on a line.
_QUOTED = re.compile(r"""(["'])(?:\\.|(?!\1).)*\1""")

#: The soft line length limit.  It matches the line length of ruff and black
#: in pyproject.toml.  A line above it is reported as a warning.
DEFAULT_MAX_LINE_LENGTH = 100

#: The hard line length limit.  A line above it is reported as an error and
#: fails the run.  It is the longest line a hand written file may carry.
DEFAULT_MAX_LINE_LENGTH_ERROR = 150

#: The budget of the warning level findings.
#:
#: A warning that is always a warning teaches nobody to read the report, so
#: the gate counts the warnings and fails when there are more of them than
#: the budget allows.  The budget is the number of long lines the tree
#: carries today, and it may only go down: every long line that is fixed
#: lowers it.  The findings of a generated file do not count, because they
#: are written by the generator and not by a hand; they are still reported.
#:
#: The tree carried 163 long lines when this was written (2026-10-21), in 67
#: files, and 57 more in the generated file, which did not count.  The
#: generator now wraps what it writes, so the generated file carries none:
#: the tree carried 176 long lines in 75 files on 2026-10-21, and 0 in the
#: generated file.  The budget was 200, so the gate failed when the debt
#: grew by 24 lines; the slack was for the work that was in flight.
#:
#: The catalogue then grew, and the new modules and the new tests carried
#: long lines with them: the tree carried 212 long lines in 78 files when
#: the lint wave started on 2026-10-21, and 217 by the time it measured
#: (the measurement is in ``var/lint_wave.md``).  The gate was red: the
#: budget was below the debt.  The long lines were then wrapped - a call
#: broken over several lines, a string split into adjacent literals, a
#: table row filled - and the tree carried 0 long lines in 0 files on
#: 2026-10-21, and 0 in the generated file.  The budget is 0, so the gate
#: fails on the first long line that comes back.
#:
#: The number is measured with:
#:
#:     python tools/measure_long_lines.py
#:     python tools/lint_check.py --json
#:
#: and the fields "warnings_counted" and "warnings_exempt" carry it.
DEFAULT_WARNING_BUDGET = 0

#: The rules whose findings do not count towards the warning budget.
BUDGET_EXEMPT_RULES = frozenset({"py-line-length-generated"})

#: The colour functions a stylesheet may not use outside the token file.
COLOUR_FUNCTIONS = (
    "rgb",
    "rgba",
    "hsl",
    "hsla",
    "hwb",
    "lab",
    "lch",
    "oklab",
    "oklch",
)

#: The named colours a stylesheet may not use outside the token file.
NAMED_COLOURS = frozenset(
    {
        "aqua",
        "black",
        "blue",
        "fuchsia",
        "gray",
        "green",
        "grey",
        "lime",
        "maroon",
        "navy",
        "olive",
        "purple",
        "red",
        "silver",
        "teal",
        "white",
        "yellow",
    }
)

#: The words that look like a colour but are not one.
COLOUR_WORDS_ALLOWED = frozenset(
    {"auto", "currentcolor", "inherit", "none", "transparent", "unset", "initial"}
)

#: The suffix of the temporary copy of an ES module.  Node always parses a
#: ``.mjs`` file as a module, whatever the node version is.
ESM_SUFFIX = ".mjs"

#: How many warnings of one rule the text report shows.  The JSON report
#: always carries every finding.
MAX_PRINTED_WARNINGS = 10


# ---------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Finding:
    """One problem the gate found."""

    rule: str
    path: str
    line: int
    severity: str
    message: str

    def render(self) -> str:
        """Render the finding as ``file:line: severity [rule] message``."""
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"{where}: {self.severity} [{self.rule}] {self.message}"

    def to_dict(self) -> dict[str, object]:
        """Return the JSON form of the finding."""
        return {
            "rule": self.rule,
            "path": self.path,
            "line": self.line,
            "severity": self.severity,
            "message": self.message,
        }


@dataclass
class CheckResult:
    """The outcome of one family of checks."""

    name: str
    status: str = "pass"
    detail: str = ""
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        """The error level findings of the check."""
        return [item for item in self.findings if item.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        """The warning level findings of the check."""
        return [item for item in self.findings if item.severity == "warning"]

    def to_dict(self) -> dict[str, object]:
        """Return the JSON form of the check."""
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "errors": len(self.errors),
            "warnings": len(self.warnings),
        }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _relative(root: Path, path: Path) -> str:
    """Return the path relative to the repository root, in posix form."""
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _is_binary(data: bytes) -> bool:
    """Does the data look like a binary blob?"""
    return b"\x00" in data[:8192]


def _read_text(path: Path) -> str | None:
    """Read a file as text, or return ``None`` when it is not text."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if _is_binary(data):
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _is_generated(text: str) -> bool:
    """Does the file say that it is generated?"""
    head = "\n".join(text.splitlines()[:20]).upper()
    return "GENERATED" in head and "DO NOT EDIT" in head


def _can_be_split(line: str, limit: int) -> bool:
    """Can the line be broken into lines of at most ``limit`` characters?

    A line can be split when it carries a whitespace inside the limit and
    when no single token is longer than the limit.  A token that is longer
    than the limit cannot be broken up, so the line that carries it can
    never reach the limit.
    """
    if not any(char.isspace() for char in line[:limit]):
        return False
    return all(len(token) <= limit for token in line.split())


def _exempt_from_trademark(relative: str) -> bool:
    """Is the file exempt from the trademark scan?"""
    return any(fnmatch.fnmatch(relative, pattern) for pattern in TRADEMARK_EXEMPT)


def _is_a_name_declaration(line: str) -> bool:
    """Is the line a declaration of a list of names?

    Such a line states a rule instead of using a product name, so a token
    inside it is not a mention.
    """
    if _NAME_LIST_DECLARATION.match(line) is None:
        return False
    return _QUOTED.search(line) is not None


def _token_pattern(token: str) -> re.Pattern[str]:
    """Return a pattern that matches the token as a standalone word."""
    return re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])",
        re.IGNORECASE,
    )


def _skip_dir(name: str) -> bool:
    """Is the directory a build output that is never scanned?"""
    if name in SKIP_DIRS:
        return True
    return name.startswith(SKIP_DIR_PREFIXES)


def iter_text_files(root: Path) -> Iterator[Path]:
    """Yield every text file of the repository, in a stable order."""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(name for name in dirnames if not _skip_dir(name))
        for name in sorted(filenames):
            path = Path(dirpath) / name
            suffix = path.suffix.lower()
            if suffix not in TEXT_SUFFIXES and suffix not in TEXTLESS_SUFFIXES:
                continue
            if suffix in TEXTLESS_SUFFIXES and not path.is_file():
                continue
            yield path


# ---------------------------------------------------------------------------
# check 1 - python style
# ---------------------------------------------------------------------------
def check_python_style(
    root: Path,
    max_line_length: int,
    max_line_length_error: int,
) -> CheckResult:
    """Check the line length, the tabs, the trailing whitespace and the EOF."""
    result = CheckResult(name="python-style")
    files = 0
    for path in iter_text_files(root):
        if path.suffix.lower() not in {".py", ".pyi"}:
            continue
        text = _read_text(path)
        if text is None:
            continue
        files += 1
        relative = _relative(root, path)
        generated = _is_generated(text)
        for number, line in enumerate(text.splitlines(), start=1):
            stripped = line.rstrip("\r")
            if "\t" in stripped:
                result.findings.append(
                    Finding(
                        rule="py-tab",
                        path=relative,
                        line=number,
                        severity="error",
                        message="the line carries a tab character",
                    )
                )
            if stripped != stripped.rstrip(" \t"):
                result.findings.append(
                    Finding(
                        rule="py-trailing-whitespace",
                        path=relative,
                        line=number,
                        severity="error",
                        message="the line carries trailing whitespace",
                    )
                )
            length = len(stripped)
            if length <= max_line_length:
                continue
            message = f"the line is {length} characters long (limit {max_line_length})"
            if generated:
                result.findings.append(
                    Finding(
                        rule="py-line-length-generated",
                        path=relative,
                        line=number,
                        severity="warning",
                        message=message + " (generated file)",
                    )
                )
                continue
            if not _can_be_split(stripped, max_line_length):
                # One long token: the line cannot be split, so it stays a
                # warning even when it is above the hard limit.
                result.findings.append(
                    Finding(
                        rule="py-line-length",
                        path=relative,
                        line=number,
                        severity="warning",
                        message=message + " (the line cannot be split)",
                    )
                )
                continue
            if length > max_line_length_error:
                result.findings.append(
                    Finding(
                        rule="py-line-length",
                        path=relative,
                        line=number,
                        severity="error",
                        message=(
                            f"the line is {length} characters long "
                            f"(hard limit {max_line_length_error})"
                        ),
                    )
                )
                continue
            result.findings.append(
                Finding(
                    rule="py-line-length",
                    path=relative,
                    line=number,
                    severity="warning",
                    message=message,
                )
            )
        if text and not text.endswith("\n"):
            result.findings.append(
                Finding(
                    rule="py-missing-newline",
                    path=relative,
                    line=len(text.splitlines()) or 1,
                    severity="error",
                    message="the file does not end with a newline",
                )
            )
    result.status = "fail" if result.errors else "pass"
    result.detail = f"{files} python file(s)"
    return result


# ---------------------------------------------------------------------------
# check 2 - trademarks
# ---------------------------------------------------------------------------
def check_trademark(root: Path) -> CheckResult:
    """Check that no third party trademark token is used."""
    result = CheckResult(name="trademark")
    patterns = [(token, _token_pattern(token)) for token in FORBIDDEN_TOKENS]
    files = 0
    for path in iter_text_files(root):
        relative = _relative(root, path)
        if _exempt_from_trademark(relative):
            continue
        text = _read_text(path)
        if text is None:
            continue
        files += 1
        for number, line in enumerate(text.splitlines(), start=1):
            if _is_a_name_declaration(line):
                continue
            for token, pattern in patterns:
                match = pattern.search(line)
                if match is None:
                    continue
                result.findings.append(
                    Finding(
                        rule="trademark",
                        path=relative,
                        line=number,
                        severity="error",
                        message=(
                            f"the line mentions a third party trademark "
                            f"({match.group(0)!r})"
                        ),
                    )
                )
                break
    result.status = "fail" if result.errors else "pass"
    result.detail = f"{files} text file(s)"
    return result


# ---------------------------------------------------------------------------
# check 3 - javascript syntax
# ---------------------------------------------------------------------------
def _is_es_module(text: str) -> bool:
    """Does the source use the module syntax?"""
    return bool(
        re.search(
            r"^\s*(?:export\s|import\s+[^;(]|import\s*['\"])",
            text,
            re.MULTILINE,
        )
    )


def _node_error(output: str) -> tuple[int, str]:
    """Extract the line number and the message of a node error."""
    number = 0
    message = ""
    for line in output.splitlines():
        if not number:
            found = re.match(r"^(?:.*?):(\d+)\s*$", line)
            if found:
                number = int(found.group(1))
                continue
        if line.startswith("SyntaxError") or line.startswith("Error"):
            message = line.strip()
            break
    if not message:
        message = " ".join(output.split())[:200] or "the file does not parse"
    return number, message


def check_js_syntax(root: Path) -> CheckResult:
    """Check that every JavaScript source parses."""
    result = CheckResult(name="js-syntax")
    node = shutil.which("node")
    if node is None:
        result.status = "skip"
        result.detail = "node is not on PATH, the syntax check was skipped"
        return result

    files = [path for path in iter_text_files(root) if path.suffix.lower() == ".js"]
    with tempfile.TemporaryDirectory(prefix="auralis-lint-") as tmp:
        for path in files:
            text = _read_text(path)
            if text is None:
                continue
            relative = _relative(root, path)
            target = path
            if _is_es_module(text):
                # A ".mjs" copy is always parsed as a module, so the check
                # does not depend on the module detection of the node version.
                target = Path(tmp) / (path.stem + ESM_SUFFIX)
                target.write_text(text, encoding="utf-8")
            try:
                completed = subprocess.run(
                    [node, "--check", str(target)],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                result.findings.append(
                    Finding(
                        rule="js-syntax",
                        path=relative,
                        line=0,
                        severity="error",
                        message=f"node could not be run: {exc}",
                    )
                )
                continue
            if completed.returncode != 0:
                number, message = _node_error(completed.stderr or completed.stdout or "")
                result.findings.append(
                    Finding(
                        rule="js-syntax",
                        path=relative,
                        line=number,
                        severity="error",
                        message=f"the file does not parse: {message}",
                    )
                )
    result.status = "fail" if result.errors else "pass"
    result.detail = f"{len(files)} javascript file(s), node {node}"
    return result


# ---------------------------------------------------------------------------
# check 4 - css
# ---------------------------------------------------------------------------
def _mask_css(text: str) -> str:
    """Blank out the comments and the strings of a stylesheet.

    The length of the text does not change, so an offset in the result is
    still the offset in the source.
    """
    out = list(text)
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == "/" and text.startswith("/*", index):
            end = text.find("*/", index + 2)
            end = length if end < 0 else end + 2
            for position in range(index, end):
                if out[position] != "\n":
                    out[position] = " "
            index = end
            continue
        if char in "\"'":
            quote = char
            index += 1
            while index < length:
                if text[index] == "\\":
                    index += 2
                    continue
                if text[index] == quote:
                    index += 1
                    break
                if out[index] != "\n":
                    out[index] = " "
                index += 1
            continue
        index += 1
    return "".join(out)


def _line_of(text: str, offset: int) -> int:
    """Return the one based line number of an offset."""
    return text.count("\n", 0, offset) + 1


_HEX_COLOUR = re.compile(
    r"(?<![\w-])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{4}|[0-9a-fA-F]{3})\b"
)
_COLOUR_FUNCTION = re.compile(
    r"\b(?:" + "|".join(COLOUR_FUNCTIONS) + r")\s*\(", re.IGNORECASE
)
_DECLARATION = re.compile(r"(?:^|[;{])\s*[-a-zA-Z]+\s*:\s*([^;{}]+)")


def _colour_offenders(text: str, masked: str) -> list[tuple[int, str]]:
    """Return the colour literals of a stylesheet as ``(offset, literal)``."""
    offenders: list[tuple[int, str]] = []
    for match in _DECLARATION.finditer(masked):
        value = match.group(1)
        start = match.start(1)
        for literal in _HEX_COLOUR.finditer(value):
            # "url(#name)" is a fragment reference, not a colour.
            if masked[max(0, start + literal.start() - 4) : start + literal.start()].endswith(
                "url("
            ):
                continue
            offenders.append((start + literal.start(), literal.group(0)))
        for literal in _COLOUR_FUNCTION.finditer(value):
            offenders.append((start + literal.start(), literal.group(0).strip()))
        for word in re.finditer(r"[a-zA-Z]+", value):
            name = word.group(0).lower()
            if name in NAMED_COLOURS and name not in COLOUR_WORDS_ALLOWED:
                offenders.append((start + word.start(), word.group(0)))
    return offenders


def check_css(root: Path) -> CheckResult:
    """Check the braces and the colours of every stylesheet."""
    result = CheckResult(name="css")
    files = 0
    for path in iter_text_files(root):
        if path.suffix.lower() != ".css":
            continue
        text = _read_text(path)
        if text is None:
            continue
        files += 1
        relative = _relative(root, path)
        masked = _mask_css(text)

        depth = 0
        opened = 0
        for offset, char in enumerate(masked):
            if char == "{":
                depth += 1
                opened += 1
            elif char == "}":
                depth -= 1
                if depth < 0:
                    result.findings.append(
                        Finding(
                            rule="css-braces",
                            path=relative,
                            line=_line_of(text, offset),
                            severity="error",
                            message="a closing brace without an opening one",
                        )
                    )
                    depth = 0
        if depth != 0:
            result.findings.append(
                Finding(
                    rule="css-braces",
                    path=relative,
                    line=len(text.splitlines()) or 1,
                    severity="error",
                    message=(
                        f"the braces are not balanced: {opened} opening, "
                        f"{opened - depth} closing"
                    ),
                )
            )

        if path.name == TOKENS_CSS_NAME:
            continue
        for offset, literal in _colour_offenders(text, masked):
            result.findings.append(
                Finding(
                    rule="css-hardcoded-colour",
                    path=relative,
                    line=_line_of(text, offset),
                    severity="error",
                    message=(
                        f"the colour {literal!r} is hard coded; use a variable "
                        f"of {TOKENS_CSS} instead"
                    ),
                )
            )
    result.status = "fail" if result.errors else "pass"
    result.detail = f"{files} stylesheet(s)"
    return result


# ---------------------------------------------------------------------------
# the runner
# ---------------------------------------------------------------------------
#: The families of checks, in the order they are run.
CHECKS = ("python-style", "trademark", "js-syntax", "css")


def run_checks(
    root: Path,
    names: Sequence[str],
    max_line_length: int,
    max_line_length_error: int,
) -> list[CheckResult]:
    """Run the selected families of checks."""
    results: list[CheckResult] = []
    for name in names:
        if name == "python-style":
            results.append(check_python_style(root, max_line_length, max_line_length_error))
        elif name == "trademark":
            results.append(check_trademark(root))
        elif name == "js-syntax":
            results.append(check_js_syntax(root))
        elif name == "css":
            results.append(check_css(root))
    return results


def build_parser() -> argparse.ArgumentParser:
    """Return the command line parser."""
    parser = argparse.ArgumentParser(
        prog="lint_check",
        description="The Auralis style gate: python style, trademarks, js, css.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="the repository root (default: the root of this checkout)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="write the report as JSON instead of text",
    )
    parser.add_argument(
        "--only",
        default="",
        help="the checks to run, comma separated (" + ", ".join(CHECKS) + ")",
    )
    parser.add_argument(
        "--max-line-length",
        type=int,
        default=DEFAULT_MAX_LINE_LENGTH,
        help="the soft line length limit (default: %(default)s)",
    )
    parser.add_argument(
        "--max-line-length-error",
        type=int,
        default=DEFAULT_MAX_LINE_LENGTH_ERROR,
        help="the hard line length limit (default: %(default)s)",
    )
    parser.add_argument(
        "--warning-budget",
        type=int,
        default=DEFAULT_WARNING_BUDGET,
        help="the number of warnings the gate allows (default: %(default)s)",
    )
    parser.add_argument(
        "--require",
        action="append",
        default=[],
        metavar="CHECK",
        help=(
            "a check that must not be skipped; a skipped required check is "
            "a failure (repeatable)"
        ),
    )
    return parser


def _selected(names: str) -> list[str]:
    """Return the checks the user asked for."""
    wanted = [item.strip() for item in names.split(",") if item.strip()]
    if not wanted:
        return list(CHECKS)
    unknown = [item for item in wanted if item not in CHECKS]
    if unknown:
        raise SystemExit(f"unknown check(s): {', '.join(unknown)}")
    return [name for name in CHECKS if name in wanted]


def _required(names: list[str]) -> list[str]:
    """Return the checks that must not be skipped."""
    unknown = [item for item in names if item not in CHECKS]
    if unknown:
        raise SystemExit(f"unknown check(s): {', '.join(unknown)}")
    return names


def main(argv: list[str] | None = None) -> int:
    """Run the gate and report the result."""
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"the repository root is missing: {root}", file=sys.stderr)
        return 2

    names = _selected(args.only)
    required = _required(args.require)
    results = run_checks(root, names, args.max_line_length, args.max_line_length_error)

    errors = [item for result in results for item in result.errors]
    warnings = [item for result in results for item in result.warnings]
    counted = [item for item in warnings if item.rule not in BUDGET_EXEMPT_RULES]
    exempt = [item for item in warnings if item.rule in BUDGET_EXEMPT_RULES]

    # A warning that is always a warning teaches nobody to read the report,
    # so the number of warnings is a budget: above it, the gate fails.
    over_budget = len(counted) > args.warning_budget

    # A check that had to be skipped must not pass quietly when the caller
    # says the check is required.
    skipped_required = [
        result for result in results if result.status == "skip" and result.name in required
    ]

    if args.as_json:
        payload = {
            "root": str(root),
            "ok": not errors and not over_budget and not skipped_required,
            "errors": len(errors),
            "warnings": len(warnings),
            "warnings_counted": len(counted),
            "warnings_exempt": len(exempt),
            "warning_budget": args.warning_budget,
            "over_budget": over_budget,
            "required": required,
            "skipped_required": [result.name for result in skipped_required],
            "checks": [result.to_dict() for result in results],
            "findings": [
                item.to_dict()
                for result in results
                for item in sorted(
                    result.findings, key=lambda entry: (entry.path, entry.line, entry.rule)
                )
            ],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 1 if payload["ok"] is False else 0

    print("=" * 78)
    print(" AURALIS LINT")
    print(f" root: {root}")
    print("=" * 78)
    print()
    for result in results:
        state = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP"}[result.status]
        print(f"  {result.name:<14} {state:<5} {result.detail}")
    print()

    all_findings = [
        finding
        for result in results
        for finding in sorted(
            result.findings, key=lambda entry: (entry.path, entry.line, entry.rule)
        )
    ]
    for finding in all_findings:
        if finding.severity == "error":
            print(f"  {finding.render()}")

    by_rule: dict[str, list[Finding]] = {}
    for finding in all_findings:
        if finding.severity != "error":
            by_rule.setdefault(finding.rule, []).append(finding)
    for rule, findings in sorted(by_rule.items()):
        files = len({finding.path for finding in findings})
        print(f"  {rule}: {len(findings)} warning(s) in {files} file(s)")
        for finding in findings[:MAX_PRINTED_WARNINGS]:
            print(f"    {finding.render()}")
        if len(findings) > MAX_PRINTED_WARNINGS:
            print(f"    ... and {len(findings) - MAX_PRINTED_WARNINGS} more")

    print()
    print(
        f"  warnings: {len(counted)} counted, {len(exempt)} not counted "
        f"(a generated file), budget {args.warning_budget}"
    )
    if over_budget:
        print(
            f"  the warning budget is exceeded: {len(counted)} warning(s), "
            f"the budget is {args.warning_budget}."
        )
        print("  Fix the long lines below, or lower the budget when they are fixed.")
        for finding in counted[:MAX_PRINTED_WARNINGS]:
            print(f"    {finding.render()}")
        if len(counted) > MAX_PRINTED_WARNINGS:
            print(f"    ... and {len(counted) - MAX_PRINTED_WARNINGS} more")

    for result in skipped_required:
        print(f"  the check '{result.name}' was skipped and it is required: {result.detail}")

    print()
    print("-" * 78)
    if errors:
        print(f"RESULT: FAIL ({len(errors)} error(s), {len(warnings)} warning(s))")
        return 1
    if over_budget:
        print(
            f"RESULT: FAIL ({len(counted)} warning(s), the budget is "
            f"{args.warning_budget})"
        )
        return 1
    if skipped_required:
        names = ", ".join(result.name for result in skipped_required)
        print(f"RESULT: FAIL (the required check(s) {names} were skipped)")
        return 1
    print(f"RESULT: PASS ({len(warnings)} warning(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
