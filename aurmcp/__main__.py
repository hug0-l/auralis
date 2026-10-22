"""Run the Auralis model context server:  python -m aurmcp

The server speaks JSON-RPC 2.0 over the standard input and the standard
output, one message per line.  The standard output carries the protocol and
nothing else; the standard error carries the diagnostics.  The end of the
input ends the session with the status 0.

``--version`` is the one exception, and only because no session is running:
it prints the version of the server and the revision it speaks, which is
what the caller asked for.
"""
from __future__ import annotations

import argparse
import sys

from . import build_server
from .protocol import PROTOCOL_VERSION, SUPPORTED_PROTOCOL_VERSIONS
from .server import DEFAULT_VERSION


def build_parser() -> argparse.ArgumentParser:
    """Return the command line parser of the server."""
    parser = argparse.ArgumentParser(
        prog="aurmcp",
        description=(
            "The Auralis model context server: JSON-RPC 2.0 over the standard "
            "input and the standard output, and no dependency beyond the "
            "standard library."
        ),
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="print the version of the server and the protocol revision and exit",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Start the server on the standard input and the standard output."""
    args = build_parser().parse_args(argv)
    if args.version:
        print(f"aurmcp {DEFAULT_VERSION} (protocol {PROTOCOL_VERSION})")
        print(f"known revisions: {', '.join(SUPPORTED_PROTOCOL_VERSIONS)}")
        return 0
    server = build_server()
    return server.serve()


if __name__ == "__main__":
    sys.exit(main())
