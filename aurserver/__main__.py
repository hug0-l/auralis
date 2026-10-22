"""Run the Auralis server:  python -m aurserver [--port 8731] [--reload]"""
from __future__ import annotations

import argparse
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser(prog="aurserver", description="Auralis application server")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8731)
    ap.add_argument("--reload", action="store_true", help="auto-reload on source changes")
    ap.add_argument("--log-level", default="info")
    args = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)
    os.chdir(root)

    import uvicorn

    print(f"  Auralis  ->  http://{args.host}:{args.port}/")
    uvicorn.run(
        "aurserver.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
