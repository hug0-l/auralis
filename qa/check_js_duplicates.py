"""Report the JavaScript files that declare the same name twice.

The localisation migration inserted an import into files that already had
one, and a file that declares a name twice does not parse — the page then
loses the feature, and with the old static imports it lost the whole page.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "aurui" / "js"

DECLARATION = re.compile(
    r"^\s*(?:import\s*\{([^}]*)\}|const\s+([A-Za-z_$][\w$]*)\s*=|let\s+([A-Za-z_$][\w$]*)\s*=|"
    r"function\s+([A-Za-z_$][\w$]*)\s*\(|class\s+([A-Za-z_$][\w$]*)\b)"
)


def names_of(line: str) -> list[str]:
    """The names a line declares.

    Only the module scope counts: a function may declare the same name
    twice in two different functions, and that is legal.  A module scope
    declaration that appears twice is a syntax error.
    """
    if line[:1].isspace():
        return []

    match = DECLARATION.match(line)
    if not match:
        return []

    imported, const, let, function, klass = match.groups()

    if imported:
        return [part.strip().split(" as ")[-1].strip()
                for part in imported.split(",") if part.strip()]

    return [name for name in (const, let, function, klass) if name]


def main() -> int:
    broken = []

    for path in sorted(JS.glob("*.js")):
        seen: dict[str, int] = {}
        for number, line in enumerate(path.read_text(encoding="utf-8",
                                                     errors="replace").splitlines(), 1):
            for name in names_of(line):
                seen.setdefault(name, number)

        for name, first in seen.items():
            # A second declaration of the same name is only a problem for
            # the module scope; a function may be re-declared in a block.
            occurrences = [
                number for number, line in enumerate(
                    path.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
                if any(n == name for n in names_of(line))
            ]
            if len(occurrences) > 1:
                broken.append((path.name, name, occurrences))

    if not broken:
        print("no file declares a name twice")
        return 0

    for name, symbol, occurrences in broken:
        print(f"{name}: {symbol} declared at lines {occurrences}")

    print(f"{len(broken)} duplicate declaration(s)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
