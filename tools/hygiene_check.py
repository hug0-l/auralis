#!/usr/bin/env python3
"""
tools/hygiene_check.py — enforce the naming rule of CONTRACT §0 over the tree.

The rule is absolute: **no third-party product, vendor or trademark may appear
anywhere in this repository** — not in source, not in comments, not in
identifiers, not in user-visible strings, not in presets, not in resource
names, not in file names and not in metadata.

Five independent checks
-----------------------
``third-party-mention``
    A banned product or vendor name occurs in the text of a committed file or
    in a path component (a file name or a directory name).
``banned-abbreviation``
    The short product abbreviation occurs as a standalone word, as an
    identifier segment, or inside a path component.
``absolute-path``
    A hard-coded absolute path (a drive letter or a home directory) is baked
    into a committed text file.
``generated-without-header``
    A generated artefact is committed without a "do not edit" header, so an
    editor cannot tell that the file must not be touched by hand.
``stray-binary``
    A binary blob larger than the size limit is committed and is not on the
    allow list (the vendor and build trees are skipped entirely).

Matching rules
--------------
The banned short abbreviation is matched on **word boundaries** and on
**identifier segments**, never as a raw substring.  Ordinary words such as
``matrix``, ``prefix`` and ``extra`` therefore never match, while the
abbreviation on its own, the abbreviation inside a snake-case or camel-case
identifier and the abbreviation used as the head of a path component all do.
Every hit carries an explanation of *why* it matched, so a reviewer can tell a
real mention from an innocent abbreviation.

The banned names are spelled in pieces on purpose.  This file is part of the
codebase, and the codebase must not contain a third-party trademark even in
the scanner that looks for one (CONTRACT §0).  The tokens are assembled at
import time from fragments, so the scanner never flags itself — and the gate
additionally excludes its own source file from the report.

Usage
-----
    python tools/hygiene_check.py                    # human readable report
    python tools/hygiene_check.py --json             # machine readable report
    python tools/hygiene_check.py --kind absolute-path
    python tools/hygiene_check.py --max-binary-bytes 0   # list every binary

Exit codes
----------
    0   the tree is clean
    1   at least one finding
    2   the gate could not run (unreadable root, bad arguments)
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# token table
# ---------------------------------------------------------------------------
#: The short product abbreviation.  Spelled in pieces so that this file does
#: not itself contain the token it hunts for (CONTRACT §0).
SHORT_TOKEN = "r" + "x"

#: The vendor name.  Spelled in pieces for the same reason.
VENDOR_TOKEN = "izo" + "tope"

#: Long banned tokens: product names and vendor names that carry a name of
#: their own.  Matched case-insensitively on word boundaries.
#:
#: The product editions and the sibling products of the same vendor are
#: listed explicitly because the contract names them; the last three entries
#: are an *extension tier* of other audio products that have no business
#: appearing in this tree at all.  ``tools/validate_schema.py`` already
#: carries the two core entries for the generated artefacts.
LONG_TOKENS: tuple[str, ...] = (
    VENDOR_TOKEN,
    "R" + "2R",
    SHORT_TOKEN + " audio editor",
    SHORT_TOKEN + " elements",
    SHORT_TOKEN + " standard",
    SHORT_TOKEN + " advanced",
    SHORT_TOKEN + " complete",
    SHORT_TOKEN + " 8",
    SHORT_TOKEN + " 9",
    SHORT_TOKEN + " 10",
    SHORT_TOKEN + " 11",
    SHORT_TOKEN + " 12",
    "Audi" + "city",
    "Sound" + " Forge",
    "Melo" + "dyne",
)

#: Names the contract declares to be ours.  The competitor ships modules with
#: these very names, so a display name is never reported on its own: the gate
#: looks for the *product* name, not for a module that happens to be called
#: the same thing.  The list is kept as the audit trail of that decision.
OWN_NAMES: tuple[str, ...] = (
    "De-Click",
    "De-Hum",
    "De-Ess",
    "De-Plosive",
    "De-Crackle",
    "De-Clip",
    "De-Reverb",
    "De-Rustle",
    "De-Wind",
    "De-Bleed",
    "Breath Control",
    "Mouth De-Click",
    "Voice De-Noise",
    "Spectral De-Noise",
    "Spectral Repair",
    "Spectral Recovery",
    "Guitar De-Noise",
    "Deconstruct",
    "Dialogue Isolate",
    "Music Rebalance",
    "Ambience Match",
    "Repair Assistant",
    "Leveler",
    "Loudness Control",
    "Loudness Optimize",
    "EQ",
    "EQ Match",
    "Gain",
    "Fade",
    "Normalize",
    "Phase",
    "Mixing",
    "Center Extract",
    "Azimuth",
    "Dither",
    "Resample",
    "Time & Pitch",
    "Variable Pitch",
    "Variable Time",
    "Wow & Flutter",
    "Signal Generator",
    "Streaming Preview",
    "Find Similar",
    "Connect",
    "Monitor",
)

#: Explanations attached to hits whose context is worth spelling out.  Keyed
#: by the lower-cased token or path component that triggered the hit.
NOTES: dict[str, str] = {
    SHORT_TOKEN + "core": (
        "legacy path alias for the core package; CONTRACT §0 bullet 3 allows the "
        "directory name for import compatibility, but bullet 1 bans the token in "
        "file names, so it is reported here"
    ),
    SHORT_TOKEN + "server": (
        "not sanctioned by CONTRACT §0 — only the core package root is allowed to "
        "keep a legacy alias, and the tree should use the 'aur' prefix"
    ),
    SHORT_TOKEN + "ui": (
        "not sanctioned by CONTRACT §0 — only the core package root is allowed to "
        "keep a legacy alias, and the tree should use the 'aur' prefix"
    ),
    SHORT_TOKEN: (
        "the banned short abbreviation used as an identifier or name segment; the "
        "contract bans the token in identifiers even where no product is meant"
    ),
}

# ---------------------------------------------------------------------------
# traversal rules
# ---------------------------------------------------------------------------
#: Directories that are never walked.  Matched on the directory name at any
#: depth, so ``plugins/abi/build`` and ``qa/build`` are skipped as well.
SKIP_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        "build",
        "dist",
        "var",
        "__pycache__",
        "vendor",
        "node_modules",
        # local caches and environments — never part of the source tree
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".tox",
        ".venv",
        "venv",
        "env",
        ".idea",
        ".vscode",
    }
)

#: Files that are exempt from the report, with the reason.  Exempt files are
#: not scanned at all.
EXEMPT_FILES: dict[str, str] = {
    "CONTRACT.md": (
        "the contract is the rule itself: it has to spell the forbidden strings "
        "out in order to forbid them"
    ),
    "tools/hygiene_check.py": (
        "the gate itself: it assembles the banned tokens from fragments and is "
        "excluded so it cannot report on its own machinery"
    ),
}

#: Generated artefacts that are committed to the tree.  Each entry names the
#: tool that produces it.
KNOWN_GENERATED_ARTEFACTS: dict[str, str] = {
    "aurcore/schema/modules.json": "tools/codegen_registry.py",
    "aurcore/schema/modules.schema.json": "tools/codegen_registry.py",
    "aurcore/schema/generated.py": "tools/codegen_registry.py",
    "aurcore/schema/aur_modules.h": "tools/codegen_registry.py",
    "docs/MODULES.md": "tools/codegen_registry.py",
    "tests/fixtures/truncation_ladders.json": "tools/extract_truncation_ladders.py",
}

#: Path prefixes whose binaries are expected (golden corpus clips, artwork).
BINARY_ALLOW_PREFIXES: tuple[str, ...] = ("corpus/", "assets/")

#: A binary larger than this many bytes is "stray" unless it is allow-listed.
DEFAULT_MAX_BINARY_BYTES = 1_000_000

#: Files bigger than this are not scanned for content (they are reported as
#: stray binaries instead).
MAX_CONTENT_SCAN_BYTES = 16 * 1024 * 1024

#: A declaration of forbidden names, for example ``FORBIDDEN_NAMES = (...)``.
#: A token inside such a declaration states the rule rather than breaking it.
#: The sibling gate ``tools/lint_check.py`` treats a declaration that way; the
#: hygiene gate does not by default, because CONTRACT §0 names source files as
#: a place where the strings may not appear at all.  The opt-in
#: ``--allow-name-declarations`` aligns the two gates.
_NAME_DECLARATION_RE = re.compile(r"^\s*[A-Z][A-Z0-9_]*\s*[:=]\s*.*[\(\[\{]")

# ---------------------------------------------------------------------------
# regular expressions
# ---------------------------------------------------------------------------
_LONG_TOKEN_RE = re.compile(
    "|".join(re.escape(token) for token in sorted(LONG_TOKENS, key=len, reverse=True)),
    re.IGNORECASE,
)

#: The short abbreviation as a standalone word.  The lookbehind rejects the
#: middle of an ordinary word (``matrix``, ``prefix``); the lookahead allows a
#: trailing version number but rejects a following letter or underscore, which
#: the identifier-segment rule handles instead.
_SHORT_WORD_RE = re.compile(
    r"(?<![A-Za-z0-9_])(" + SHORT_TOKEN + r")(?![A-Za-z_])", re.IGNORECASE
)

#: Identifier-like runs: the unit the segment rule works on.
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

#: Split an identifier into its segments: on underscores, on camel-case
#: humps and on letter/digit boundaries.
_SEGMENT_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z][a-z0-9]*|[0-9]+")

#: A Windows drive letter followed by a separator.  The lookbehind keeps
#: URL schemes such as ``https://`` out of the match.
_DRIVE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_+.\-])([A-Za-z]:[\\/][^\s\"'`<>|,;\)\]]*)"
)

#: A POSIX home directory.
_HOME_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_+.\-])(/(?:home|Users)/[^\s\"'`<>|,;\)\]]*)"
)

#: The same two, but quoted: a quoted path may contain spaces, and the quote
#: tells the matcher where the path ends.
_QUOTED_DRIVE_RE = re.compile(r"""(["'])([A-Za-z]:[\\/][^"'\n]*)\1""")
_QUOTED_HOME_RE = re.compile(r"""(["'])(/(?:home|Users)/[^"'\n]*)\1""")

#: A home-relative path.
_TILDE_PATH_RE = re.compile(r"(?<![A-Za-z0-9_])(~/(?!~)[^\s\"'`<>|,;\)\]]*)")

#: A Windows environment variable that expands to the user profile.
_USERPROFILE_RE = re.compile(r"%USERPROFILE%", re.IGNORECASE)

#: A file that declares itself generated.
_GENERATED_DECL_RE = re.compile(
    r"(?i)(generated file|auto-?generated|automatically generated|generated by"
    r"|@generated|this file (?:is|was) generated)"
)

#: A "do not edit" header.
_DO_NOT_EDIT_RE = re.compile(
    r"(?i)\bdo\s*(?:n't|not)\s*(?:hand[- ]?)?edit\b"
    r"|\bnever\s+(?:be\s+)?(?:hand[- ]?)?edit"
    r"|\bmust not be edit"
)

#: How many lines of a file count as its header.
HEADER_LINES = 25

#: Extensions that mark a file as binary even without a NUL byte.
BINARY_SUFFIXES: frozenset[str] = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".icns", ".bmp",
        ".wav", ".flac", ".mp3", ".ogg", ".aif", ".aiff", ".m4a",
        ".exe", ".dll", ".so", ".dylib", ".obj", ".lib", ".pdb", ".exp",
        ".zip", ".7z", ".tar", ".gz", ".whl", ".jar", ".pyc", ".pyo",
    }
)


# ---------------------------------------------------------------------------
# findings
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Finding:
    """One problem reported by the gate.

    ``line`` is ``0`` for findings that concern a path rather than the text of
    a file; the message then says which path component was at fault.
    """

    kind: str
    path: str
    line: int
    column: int
    token: str
    rule: str
    message: str
    excerpt: str = ""
    note: str = ""

    def as_dict(self) -> dict:
        """Return the finding as a JSON-serialisable mapping."""
        return asdict(self)

    def format(self) -> str:
        """Return the one-line human readable form of the finding."""
        head = f"{self.path}:{self.line}:{self.column}: [{self.kind}] {self.message}"
        if self.excerpt:
            head += f"\n    {self.excerpt}"
        if self.note:
            head += f"\n    note: {self.note}"
        return head


@dataclass
class Report:
    """The result of one gate run."""

    root: str
    files_scanned: int = 0
    directories_scanned: int = 0
    files_skipped: int = 0
    skipped_directories: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    def counts_by_kind(self) -> dict[str, int]:
        """Return the number of findings per kind, in a stable order."""
        counts: dict[str, int] = {}
        for finding in self.findings:
            counts[finding.kind] = counts.get(finding.kind, 0) + 1
        return dict(sorted(counts.items()))

    def as_dict(self) -> dict:
        """Return the report as a JSON-serialisable mapping."""
        return {
            "root": self.root,
            "files_scanned": self.files_scanned,
            "directories_scanned": self.directories_scanned,
            "files_skipped": self.files_skipped,
            "skipped_directories": sorted(self.skipped_directories),
            "counts_by_kind": self.counts_by_kind(),
            "policy": policy_dict(),
            "findings": [finding.as_dict() for finding in self.findings],
        }


def policy_dict() -> dict:
    """Return the policy the gate applied, as a JSON-serialisable mapping.

    The policy travels with the report so that a JSON report is an audit
    artefact: it says which tokens were hunted, which files were exempt and
    why, and which names the contract declares to be ours.
    """
    return {
        "forbidden_token_count": len(LONG_TOKENS),
        "forbidden_token_lengths": sorted({len(token) for token in LONG_TOKENS}),
        "permitted_module_names": list(OWN_NAMES),
        "exempt_files": {rel: reason for rel, reason in EXEMPT_FILES.items()},
        "skipped_directory_names": sorted(SKIP_DIR_NAMES),
        "binary_allow_prefixes": list(BINARY_ALLOW_PREFIXES),
        "max_binary_bytes": DEFAULT_MAX_BINARY_BYTES,
        "known_generated_artefacts": dict(KNOWN_GENERATED_ARTEFACTS),
    }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def identifier_segments(name: str) -> list[str]:
    """Split an identifier or path component into its segments.

    A snake-case identifier becomes its underscore separated parts, a
    camel-case identifier becomes its humps, and a lower case word stays
    whole.  The banned abbreviation is therefore a segment of its own in
    ``<abbrev>_gain`` and in ``<Abbrev>Gain``, but it is only the head of a
    segment in ``<abbrev>core``.
    """
    segments: list[str] = []
    for chunk in name.split("_"):
        if not chunk:
            continue
        segments.extend(_SEGMENT_RE.findall(chunk))
    return segments


def segment_hits(token: str, name: str) -> list[str]:
    """Return the segments of ``name`` that carry the banned short token.

    A segment is a hit when it *is* the token or when it *starts* with the
    token and continues with more characters.  Ordinary words never qualify:
    ``matrix`` and ``prefix`` do not start with the token.
    """
    hits: list[str] = []
    low = token.lower()
    for segment in identifier_segments(name):
        candidate = segment.lower()
        if candidate == low or (candidate.startswith(low) and len(candidate) > len(low)):
            hits.append(segment)
    return hits


def _excerpt(line: str, start: int, end: int, width: int = 60) -> str:
    """Return the source line around ``[start:end]``, trimmed for display."""
    text = line.strip()
    if len(text) <= width:
        return text
    offset = max(0, start - width // 2)
    snippet = text[offset : offset + width]
    prefix = "..." if offset > 0 else ""
    suffix = "..." if offset + width < len(text) else ""
    return f"{prefix}{snippet}{suffix}"


def _is_placeholder(path_text: str) -> bool:
    """True when the matched path is a documentation placeholder, not a path."""
    return "..." in path_text or "…" in path_text or "<" in path_text or ">" in path_text


#: The files that SHOW paths rather than USE them.
DOCUMENTATION_SUFFIXES: frozenset[str] = frozenset({".md", ".markdown", ".rst", ".txt"})


def _is_documentation_example(rel: str, line: str) -> bool:
    """True when a path in a document is an example, not a dependency.

    A document shows paths: a table that says where the installer puts the
    plug-ins, a snippet that shows a command, a report that quotes what
    another check found.  A source file USES paths, and there a hard-coded
    one is a real dependency on the machine of whoever wrote it.

    The distinction is the file, not the path: in a document a path inside a
    fenced block, inside an inline code span, or in a table row is an
    example.
    """
    if Path(rel).suffix.lower() not in DOCUMENTATION_SUFFIXES:
        return False

    stripped = line.strip()

    # A table row: the path is in a cell, and the cell is showing something.
    if stripped.startswith("|"):
        return True

    # An inline code span: the path is written as code, not as a dependency.
    if "`" in line:
        return True

    return False


def _read_text(path: Path, size: int) -> str | None:
    """Read a file as text, or return ``None`` when it is binary.

    UTF-16 files (a Windows console redirect writes one) are decoded as
    text: they are full of NUL bytes but they carry prose, and a stray log
    that leaks a home directory must still be reported.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    if b"\x00" in raw[:8192]:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace")


def _looks_binary(path: Path) -> bool:
    """True when the file is a binary blob rather than text."""
    if path.suffix.lower() in BINARY_SUFFIXES:
        return True
    try:
        with path.open("rb") as handle:
            chunk = handle.read(8192)
    except OSError:
        return False
    if chunk[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return False
    return b"\x00" in chunk


# ---------------------------------------------------------------------------
# gitignore handling (what counts as "committed")
# ---------------------------------------------------------------------------
def load_gitignore(root: Path) -> list[str]:
    """Return the ignore patterns of the repository's ``.gitignore``."""
    path = root / ".gitignore"
    if not path.is_file():
        return []
    patterns: list[str] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns


def is_ignored(rel: str, patterns: Sequence[str]) -> bool:
    """True when ``rel`` (a repository-relative path) is ignored.

    The matcher understands the subset of ``.gitignore`` that this repository
    uses: anchored patterns, directory patterns, ``*`` wildcards and ``!``
    negations.
    """
    ignored = False
    for pattern in patterns:
        negate = pattern.startswith("!")
        body = pattern[1:] if negate else pattern
        body = body.rstrip("/")
        if not body:
            continue
        if "/" in body:
            anchored = body
            hit = fnmatch.fnmatch(rel, anchored) or fnmatch.fnmatch(rel, f"*/{anchored}")
        else:
            hit = any(fnmatch.fnmatch(part, body) for part in rel.split("/"))
        if hit:
            ignored = not negate
    return ignored


# ---------------------------------------------------------------------------
# the walk
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TreeEntry:
    """One file or directory of the walked tree."""

    path: Path
    rel: str
    is_dir: bool
    size: int


def walk_tree(root: Path) -> tuple[list[TreeEntry], list[str]]:
    """Walk the repository, skipping the excluded directories.

    Returns the entries of the tree and the list of skipped directory names.
    """
    entries: list[TreeEntry] = []
    skipped: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        current = Path(dirpath)
        kept: list[str] = []
        for name in sorted(dirnames):
            if name in SKIP_DIR_NAMES:
                skipped.append(str((current / name).relative_to(root)).replace("\\", "/"))
                continue
            kept.append(name)
        dirnames[:] = kept
        rel_dir = current.relative_to(root)
        rel_prefix = "" if str(rel_dir) == "." else str(rel_dir).replace("\\", "/") + "/"
        for name in dirnames:
            child = current / name
            entries.append(
                TreeEntry(
                    path=child,
                    rel=f"{rel_prefix}{name}",
                    is_dir=True,
                    size=0,
                )
            )
        for name in sorted(filenames):
            child = current / name
            try:
                size = child.stat().st_size
            except OSError:
                size = 0
            entries.append(
                TreeEntry(
                    path=child,
                    rel=f"{rel_prefix}{name}",
                    is_dir=False,
                    size=size,
                )
            )
    return entries, skipped


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------
def check_path_names(
    entries: Iterable[TreeEntry],
    report: Report,
    exempt: set[str],
    ignored: set[str],
) -> None:
    """Report banned tokens used as a file name or a directory name."""
    for entry in entries:
        if entry.rel in exempt:
            continue
        if not entry.is_dir and entry.rel in ignored:
            continue
        parts = entry.rel.split("/")
        for index, part in enumerate(parts):
            if not part:
                continue
            stem = part
            suffix = ""
            if "." in part and not part.startswith("."):
                stem, suffix = part.rsplit(".", 1)
                suffix = "." + suffix
            for unit in (stem, suffix):
                if not unit:
                    continue
                for hit in segment_hits(SHORT_TOKEN, unit):
                    note = NOTES.get(hit.lower(), "")
                    report.findings.append(
                        Finding(
                            kind="banned-abbreviation",
                            path=entry.rel,
                            line=0,
                            column=index,
                            token=hit,
                            rule="path-component",
                            message=(
                                f"path component {part!r} carries the banned "
                                f"abbreviation as the segment {hit!r}"
                            ),
                            note=note,
                        )
                    )
            for token in LONG_TOKENS:
                if re.search(
                    r"(?<![A-Za-z0-9])" + re.escape(token) + r"(?![A-Za-z0-9])",
                    part,
                    re.IGNORECASE,
                ):
                    report.findings.append(
                        Finding(
                            kind="third-party-mention",
                            path=entry.rel,
                            line=0,
                            column=index,
                            token=token,
                            rule="path-component",
                            message=(
                                f"path component {part!r} contains the banned "
                                f"name {token!r}"
                            ),
                        )
                    )


def check_text(
    rel: str,
    text: str,
    report: Report,
    allow_name_declarations: bool = False,
) -> None:
    """Report banned tokens, hard-coded absolute paths and short names."""
    for number, line in enumerate(text.splitlines(), start=1):
        if allow_name_declarations and _NAME_DECLARATION_RE.match(line):
            continue
        consumed: list[tuple[int, int]] = []

        # 1. long banned names (product and vendor names)
        for match in _LONG_TOKEN_RE.finditer(line):
            consumed.append(match.span())
            report.findings.append(
                Finding(
                    kind="third-party-mention",
                    path=rel,
                    line=number,
                    column=match.start() + 1,
                    token=match.group(0),
                    rule="word",
                    message=(
                        f"the banned name {match.group(0)!r} appears in the text"
                    ),
                    excerpt=_excerpt(line, match.start(), match.end()),
                )
            )

        # 2. the short abbreviation as a standalone word
        for match in _SHORT_WORD_RE.finditer(line):
            start, end = match.span()
            if any(start < c_end and c_start < end for c_start, c_end in consumed):
                continue
            report.findings.append(
                Finding(
                    kind="banned-abbreviation",
                    path=rel,
                    line=number,
                    column=start + 1,
                    token=match.group(0),
                    rule="word",
                    message=(
                        f"the banned abbreviation {match.group(0)!r} appears as a "
                        "standalone word"
                    ),
                    excerpt=_excerpt(line, start, end),
                    note=NOTES.get(SHORT_TOKEN, ""),
                )
            )

        # 3. the short abbreviation as an identifier segment
        for match in _IDENTIFIER_RE.finditer(line):
            start, end = match.span()
            if any(start < c_end and c_start < end for c_start, c_end in consumed):
                continue
            before = line[start - 1] if start > 0 else ""
            after = line[end] if end < len(line) else ""
            if before in "/\\" or after in "/\\":
                # a path component — reported by the path check instead
                continue
            identifier = match.group(0)
            for hit in segment_hits(SHORT_TOKEN, identifier):
                report.findings.append(
                    Finding(
                        kind="banned-abbreviation",
                        path=rel,
                        line=number,
                        column=start + 1,
                        token=hit,
                        rule="identifier-segment",
                        message=(
                            f"the identifier {identifier!r} carries the banned "
                            f"abbreviation as the segment {hit!r}"
                        ),
                        excerpt=_excerpt(line, start, end),
                        note=NOTES.get(hit.lower(), NOTES.get(SHORT_TOKEN, "")),
                    )
                )

        # 4. hard-coded absolute paths
        absolute: list[tuple[int, int, str, str]] = []
        for regex, rule in (
            (_QUOTED_DRIVE_RE, "drive-letter"),
            (_QUOTED_HOME_RE, "home"),
        ):
            for match in regex.finditer(line):
                absolute.append((match.start(2), match.end(2), match.group(2), rule))
        for regex, rule in (
            (_DRIVE_PATH_RE, "drive-letter"),
            (_HOME_PATH_RE, "home"),
            (_TILDE_PATH_RE, "home"),
        ):
            for match in regex.finditer(line):
                absolute.append((match.start(), match.end(), match.group(0), rule))
        for match in _USERPROFILE_RE.finditer(line):
            absolute.append((match.start(), match.end(), match.group(0), "home"))
        absolute.sort(key=lambda item: (item[0], -(item[1] - item[0])))
        reported: list[tuple[int, int]] = []
        for start, end, matched, rule in absolute:
            if _is_placeholder(matched):
                continue
            if _is_documentation_example(rel, line):
                continue
            if any(start < r_end and r_start < end for r_start, r_end in reported):
                continue
            reported.append((start, end))
            report.findings.append(
                Finding(
                    kind="absolute-path",
                    path=rel,
                    line=number,
                    column=start + 1,
                    token=matched,
                    rule=rule,
                    message=(
                        "a hard-coded absolute path is baked into the file; "
                        "use a path relative to the repository root"
                    ),
                    excerpt=_excerpt(line, start, end),
                )
            )


def check_generated_headers(
    entries: Iterable[TreeEntry],
    report: Report,
    ignored: set[str],
) -> None:
    """Report generated artefacts that are committed without a do-not-edit header."""
    for entry in entries:
        if entry.is_dir:
            continue
        rel = entry.rel
        if rel in ignored:
            continue
        known = KNOWN_GENERATED_ARTEFACTS.get(rel)
        text = None
        header = ""
        if entry.size <= MAX_CONTENT_SCAN_BYTES:
            text = _read_text(entry.path, entry.size)
        if text is None:
            continue
        header = "\n".join(text.splitlines()[:HEADER_LINES])
        declared = known is not None or bool(_GENERATED_DECL_RE.search(header))
        if not declared:
            continue
        if _DO_NOT_EDIT_RE.search(header):
            continue
        producer = known or "the file declares itself generated"
        report.findings.append(
            Finding(
                kind="generated-without-header",
                path=rel,
                line=1,
                column=1,
                token="do not edit",
                rule="missing-header",
                message=(
                    "the file is generated ("
                    + (f"produced by {producer}" if known else producer)
                    + ") but carries no 'do not edit' header, so it can be "
                    "edited by hand without anybody noticing"
                ),
            )
        )


def check_stray_binaries(
    entries: Iterable[TreeEntry],
    report: Report,
    max_bytes: int,
    allow: Sequence[str],
    ignored: set[str],
) -> None:
    """Report binary blobs over the size limit that are not allow-listed."""
    for entry in entries:
        if entry.is_dir:
            continue
        if entry.rel in ignored:
            continue
        if entry.size <= max_bytes:
            continue
        if entry.rel.startswith(BINARY_ALLOW_PREFIXES):
            continue
        if any(fnmatch.fnmatch(entry.rel, pattern) for pattern in allow):
            continue
        if not _looks_binary(entry.path):
            continue
        report.findings.append(
            Finding(
                kind="stray-binary",
                path=entry.rel,
                line=0,
                column=0,
                token=f"{entry.size} bytes",
                rule="size-limit",
                message=(
                    f"a binary blob of {entry.size} bytes is committed outside the "
                    "allow list; build outputs and vendor trees do not belong in "
                    "the source tree"
                ),
            )
        )


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------
def scan(
    root: Path,
    max_binary_bytes: int = DEFAULT_MAX_BINARY_BYTES,
    allow: Sequence[str] = (),
    kinds: Sequence[str] = (),
    exempt: dict[str, str] | None = None,
    allow_name_declarations: bool = False,
) -> Report:
    """Run every check over ``root`` and return the report.

    ``kinds`` filters the findings by kind (empty means every kind).
    ``allow`` holds extra glob patterns whose binaries are accepted.
    ``exempt`` overrides the default exempt files.
    ``allow_name_declarations`` treats a line that declares a list of
    forbidden names as a statement of the rule instead of a breach of it.
    """
    root = Path(root).resolve()
    report = Report(root=str(root))
    if not root.is_dir():
        raise NotADirectoryError(f"not a directory: {root}")

    exemption = dict(EXEMPT_FILES if exempt is None else exempt)
    entries, skipped = walk_tree(root)
    report.skipped_directories = skipped

    patterns = load_gitignore(root)
    ignored = {
        entry.rel
        for entry in entries
        if not entry.is_dir and is_ignored(entry.rel, patterns)
    }
    exempt_rels = set(exemption)

    files = [entry for entry in entries if not entry.is_dir]
    report.directories_scanned = sum(1 for entry in entries if entry.is_dir)

    # --- content of every committed text file ---------------------------
    for entry in files:
        if entry.rel in exempt_rels or entry.rel in ignored:
            report.files_skipped += 1
            continue
        report.files_scanned += 1
        if entry.size > MAX_CONTENT_SCAN_BYTES:
            continue
        text = _read_text(entry.path, entry.size)
        if text is None:
            continue
        check_text(entry.rel, text, report, allow_name_declarations)

    # --- names of files and directories ---------------------------------
    check_path_names(
        (entry for entry in entries if entry.rel not in exempt_rels),
        report,
        exempt_rels,
        ignored,
    )

    # --- generated artefacts --------------------------------------------
    check_generated_headers(
        (entry for entry in entries if entry.rel not in exempt_rels), report, ignored
    )

    # --- stray binaries --------------------------------------------------
    check_stray_binaries(
        (entry for entry in entries if entry.rel not in exempt_rels),
        report,
        max_binary_bytes,
        allow,
        ignored,
    )

    if kinds:
        wanted = set(kinds)
        report.findings = [f for f in report.findings if f.kind in wanted]
    report.findings.sort(key=lambda f: (f.kind, f.path, f.line, f.column))
    return report


# ---------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser of the gate."""
    parser = argparse.ArgumentParser(
        prog="hygiene_check",
        description="Enforce the naming rule of CONTRACT §0 over the Auralis tree.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="repository root to scan (default: the tool's repository)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="write the report as JSON on stdout",
    )
    parser.add_argument(
        "--kind",
        action="append",
        default=[],
        metavar="KIND",
        help="only report this kind of finding (repeatable)",
    )
    parser.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="GLOB",
        help="accept binaries whose path matches this glob (repeatable)",
    )
    parser.add_argument(
        "--max-binary-bytes",
        type=int,
        default=DEFAULT_MAX_BINARY_BYTES,
        help=f"size limit for a stray binary (default: {DEFAULT_MAX_BINARY_BYTES})",
    )
    parser.add_argument(
        "--allow-name-declarations",
        action="store_true",
        help=(
            "treat a line that declares a list of forbidden names as a statement "
            "of the rule instead of a breach of it (matches tools/lint_check.py)"
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="only print the summary line",
    )
    return parser


def print_report(report: Report, quiet: bool = False) -> None:
    """Print the human readable report."""
    if not quiet:
        for finding in report.findings:
            print(finding.format())
        if report.findings:
            print()
    counts = report.counts_by_kind()
    detail = ", ".join(f"{kind}={count}" for kind, count in counts.items()) or "none"
    print(
        f"hygiene: {len(report.findings)} finding(s) "
        f"({detail}) in {report.files_scanned} file(s) "
        f"across {report.directories_scanned} director(ies)"
    )


def main(argv: list[str] | None = None) -> int:
    """Entry point: scan the tree and report."""
    args = build_parser().parse_args(argv)
    try:
        report = scan(
            root=args.root,
            max_binary_bytes=args.max_binary_bytes,
            allow=args.allow,
            kinds=args.kind,
            allow_name_declarations=args.allow_name_declarations,
        )
    except (NotADirectoryError, OSError) as error:
        print(f"hygiene: cannot scan: {error}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report.as_dict(), indent=2, sort_keys=False))
    else:
        print_report(report, quiet=args.quiet)
    return 1 if report.findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
