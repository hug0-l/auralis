"""
tests/test_hygiene.py — the tests of the naming hygiene gate.

The gate (``tools/hygiene_check.py``) enforces CONTRACT §0: no third-party
product, vendor or trademark may appear anywhere in the repository.

Every violation the tests plant is assembled at run time from fragments, so
this file never contains a banned token itself and stays clean under the very
gate it exercises (CONTRACT §0 applies to the tests as well).

The tests work on a temporary tree, never on the repository, so a run of the
suite can neither hide nor invent a finding in the real tree.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "hygiene_check.py"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import hygiene_check  # noqa: E402

# ---------------------------------------------------------------------------
# the planted violations
#
# Every token is spelled in pieces on purpose: this file is part of the
# codebase, and the codebase must not contain a third party trademark even in
# the test that plants one (CONTRACT §0).
# ---------------------------------------------------------------------------
SHORT = "r" + "x"
VENDOR = "izo" + "tope"
PRODUCT = SHORT + " 11"
AUDIO_EDITOR = SHORT + " audio editor"

#: A source file that carries a banned vendor name.
DIRTY_SOURCE = (
    '"""A module that should not exist."""\n'
    f"\n# ported from {VENDOR}\n"
    "def gain() -> float:\n"
    '    """Return the gain."""\n'
    "    return 1.0\n"
)

#: A source file that uses the banned abbreviation as an identifier.
DIRTY_IDENTIFIER = (
    "def build() -> dict:\n"
    f'    """Return the {SHORT} table."""\n'
    f"    {SHORT}_table = {{}}\n"
    f"    return {SHORT}_table\n"
)

#: A source file with a hard-coded absolute path.
_ABSOLUTE_PATH = "C:" + "\\Users\\someone\\clips"
DIRTY_ABSOLUTE = f'AUDIO = "{_ABSOLUTE_PATH}"\n'

#: A clean source file: nothing in it breaks any rule.
CLEAN_SOURCE = (
    '"""A clean module."""\n'
    "\n"
    "from __future__ import annotations\n"
    "\n"
    "\n"
    "def gain(matrix: list[float]) -> float:\n"
    '    """Return the mean of ``matrix``."""\n'
    "    if not matrix:\n"
    "        return 0.0\n"
    "    return sum(matrix) / len(matrix)\n"
)

#: A clean generated artefact: it carries the header that says "do not edit".
CLEAN_GENERATED = (
    "# Generated file - do not edit.\n"
    "# Regenerate with: python tools/codegen_registry.py\n"
    "{}\n"
)

#: A generated artefact without the header.
DIRTY_GENERATED = '{\n  "AUR_DECLICK": {}\n}\n'


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _write(path: Path, text: str) -> Path:
    """Write ``text`` to ``path``, creating the parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _scan(root: Path, **kwargs) -> hygiene_check.Report:
    """Run the gate over ``root`` and return its report."""
    return hygiene_check.scan(root, **kwargs)


def _kinds(report: hygiene_check.Report) -> set[str]:
    """Return the set of kinds the report contains."""
    return set(report.counts_by_kind())


def _findings(report: hygiene_check.Report, path: str) -> list[hygiene_check.Finding]:
    """Return the findings the report holds for ``path``."""
    return [finding for finding in report.findings if finding.path == path]


# ---------------------------------------------------------------------------
# 1. the gate flags a planted violation
# ---------------------------------------------------------------------------
def test_flags_a_planted_vendor_name(tmp_path: Path) -> None:
    """A banned vendor name in a source file is reported with its position."""
    _write(tmp_path / "aurcore" / "modules" / "leaky.py", DIRTY_SOURCE)

    report = _scan(tmp_path)

    assert report.findings, "the gate did not report the planted violation"
    hits = _findings(report, "aurcore/modules/leaky.py")
    assert hits, "the planted violation was not reported"
    assert hits[0].kind == "third-party-mention"
    assert hits[0].token.lower() == VENDOR.lower()
    assert hits[0].line == 3, "the finding does not point at the offending line"
    assert hits[0].column > 1, "the finding does not point at the offending column"
    assert hits[0].message


def test_flags_the_short_abbreviation_in_an_identifier(tmp_path: Path) -> None:
    """The banned abbreviation inside an identifier is reported."""
    _write(tmp_path / "aurcore" / "modules" / "leaky.py", DIRTY_IDENTIFIER)

    report = _scan(tmp_path)

    hits = _findings(report, "aurcore/modules/leaky.py")
    assert hits, "the identifier was not reported"
    assert hits[0].kind == "banned-abbreviation"
    assert hits[0].rule in {"word", "identifier-segment"}


def test_flags_a_planted_absolute_path(tmp_path: Path) -> None:
    """A hard-coded absolute path in a source file is reported."""
    _write(tmp_path / "aurcore" / "audio.py", DIRTY_ABSOLUTE)

    report = _scan(tmp_path)

    hits = _findings(report, "aurcore/audio.py")
    assert hits, "the absolute path was not reported"
    assert hits[0].kind == "absolute-path"
    assert hits[0].rule == "drive-letter"


def test_flags_a_generated_file_without_a_header(tmp_path: Path) -> None:
    """A generated artefact without a 'do not edit' header is reported."""
    _write(tmp_path / "aurcore" / "schema" / "modules.json", DIRTY_GENERATED)

    report = _scan(tmp_path)

    hits = _findings(report, "aurcore/schema/modules.json")
    assert hits, "the generated file was not reported"
    assert hits[0].kind == "generated-without-header"


def test_flags_a_banned_name_in_a_file_name(tmp_path: Path) -> None:
    """A banned token used as a file name is reported."""
    _write(tmp_path / "aurcore" / f"{SHORT}_notes.py", "# scratch\n")

    report = _scan(tmp_path)

    hits = _findings(report, f"aurcore/{SHORT}_notes.py")
    assert hits, "the file name was not reported"
    assert hits[0].kind == "banned-abbreviation"
    assert hits[0].rule == "path-component"


def test_flags_a_stray_binary(tmp_path: Path) -> None:
    """A binary blob over the size limit is reported."""
    blob = tmp_path / "aurcore" / "leaky.bin"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(b"\x00\x01\x02\x03" * 400_000)

    report = _scan(tmp_path, max_binary_bytes=1_000_000)

    hits = _findings(report, "aurcore/leaky.bin")
    assert hits, "the stray binary was not reported"
    assert hits[0].kind == "stray-binary"
    assert hits[0].rule == "size-limit"


def test_an_ignored_file_is_not_reported(tmp_path: Path) -> None:
    """A file that .gitignore excludes is not committed and is not reported."""
    _write(tmp_path / ".gitignore", "*.log\nbuild/\n")
    _write(tmp_path / "noise.log", DIRTY_SOURCE)
    _write(tmp_path / "aurcore" / "modules" / "leaky.py", DIRTY_SOURCE)

    report = _scan(tmp_path)

    assert _findings(report, "noise.log") == []
    assert _findings(report, "aurcore/modules/leaky.py")


def test_an_allow_listed_binary_is_accepted(tmp_path: Path) -> None:
    """A binary on the allow list (golden corpus clips, artwork) is accepted."""
    blob = tmp_path / "assets" / "artwork.bin"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(b"\x00\x01\x02\x03" * 400_000)

    report = _scan(tmp_path, max_binary_bytes=1_000_000)

    assert report.findings == [], report.findings


# ---------------------------------------------------------------------------
# 2. the gate passes a clean tree
# ---------------------------------------------------------------------------
def test_passes_a_clean_tree(tmp_path: Path) -> None:
    """A tree without any violation produces no finding at all."""
    _write(tmp_path / "aurcore" / "modules" / "clean.py", CLEAN_SOURCE)
    _write(tmp_path / "aurcore" / "schema" / "generated.py", CLEAN_GENERATED)
    _write(tmp_path / "README.md", "# Auralis\n\nA clean project.\n")

    report = _scan(tmp_path)

    assert report.findings == [], report.findings
    assert report.files_scanned == 3


def test_ordinary_words_that_contain_the_letters_are_not_flagged(tmp_path: Path) -> None:
    """The abbreviation is not matched as a raw substring of an ordinary word."""
    _write(
        tmp_path / "aurcore" / "dsp" / "matrix.py",
        '"""Matrix helpers."""\n'
        "\n"
        "PREFIX = 1\n"
        "EXTRA = 2\n"
        "\n"
        "\n"
        "def prefix(matrix):\n"
        '    """Return the prefix of ``matrix``."""\n'
        "    return matrix[:PREFIX] + [EXTRA]\n",
    )

    report = _scan(tmp_path)

    assert report.findings == [], report.findings


def test_the_tool_itself_is_clean(tmp_path: Path) -> None:
    """The gate and its own test file carry no banned token."""
    report = _scan(ROOT, kinds=["third-party-mention", "banned-abbreviation"])

    offenders = [
        finding
        for finding in report.findings
        if finding.path in {"tools/hygiene_check.py", "tests/test_hygiene.py"}
    ]
    assert offenders == [], offenders


# ---------------------------------------------------------------------------
# 3. the command line
# ---------------------------------------------------------------------------
def _run(*args: str) -> subprocess.CompletedProcess:
    """Run the gate as a command and return the finished process."""
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )


def test_a_utf16_log_is_still_read(tmp_path: Path) -> None:
    """A UTF-16 console log is text, not a binary blob, and is scanned."""
    log = tmp_path / "console.log"
    log.write_bytes(
        ('node "C:' + "\\Users\\someone\\out.txt" + '"\n').encode("utf-16")
    )

    report = _scan(tmp_path)

    hits = _findings(report, "console.log")
    assert hits, "the UTF-16 log was not read"
    assert hits[0].kind == "absolute-path"


def test_the_command_line_exits_non_zero_on_a_finding(tmp_path: Path) -> None:
    """A tree with a violation makes the gate exit with 1."""
    _write(tmp_path / "aurcore" / "modules" / "leaky.py", DIRTY_SOURCE)

    result = subprocess.run(
        [sys.executable, str(TOOL), "--root", str(tmp_path)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "third-party-mention" in result.stdout


def test_the_command_line_exits_zero_on_a_clean_tree(tmp_path: Path) -> None:
    """A clean tree makes the gate exit with 0."""
    _write(tmp_path / "aurcore" / "modules" / "clean.py", CLEAN_SOURCE)

    result = subprocess.run(
        [sys.executable, str(TOOL), "--root", str(tmp_path)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_the_json_report_is_valid_json(tmp_path: Path) -> None:
    """The --json output parses and carries the findings."""
    _write(tmp_path / "aurcore" / "modules" / "leaky.py", DIRTY_SOURCE)

    result = subprocess.run(
        [sys.executable, str(TOOL), "--root", str(tmp_path), "--json"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["files_scanned"] == 1
    assert payload["counts_by_kind"]["third-party-mention"] == 1
    finding = payload["findings"][0]
    assert finding["path"] == "aurcore/modules/leaky.py"
    assert finding["line"] == 3


def test_the_gate_skips_the_excluded_directories(tmp_path: Path) -> None:
    """The vendor and build trees are never walked."""
    _write(tmp_path / "vendor" / "sdk" / "leaky.py", DIRTY_SOURCE)
    _write(tmp_path / "build" / "out" / "leaky.py", DIRTY_SOURCE)
    _write(tmp_path / "aurcore" / "modules" / "clean.py", CLEAN_SOURCE)

    report = _scan(tmp_path)

    assert report.findings == [], report.findings
    assert report.files_scanned == 1
    assert "vendor" in report.skipped_directories
    assert "build" in report.skipped_directories


def test_the_exempt_files_are_not_reported(tmp_path: Path) -> None:
    """The contract and the gate itself are exempt from the report."""
    _write(tmp_path / "CONTRACT.md", f"# never write {VENDOR}\n")
    _write(tmp_path / "tools" / "hygiene_check.py", f"TOKEN = {VENDOR!r}\n")

    report = _scan(tmp_path)

    assert report.findings == [], report.findings


if __name__ == "__main__":  # pragma: no cover - manual run
    raise SystemExit(pytest.main([__file__, "-v"]))
