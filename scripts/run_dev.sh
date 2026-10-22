#!/usr/bin/env bash
# Auralis — development server launcher (Linux / macOS).
#
# Starts uvicorn with auto-reload on port 8731 and serves the web UI plus the
# JSON / WebSocket API.  The repository root is put on PYTHONPATH so that
# "aurserver" and "aurcore" import without an editable install.
#
# Usage:
#   ./scripts/run_dev.sh                 # default 127.0.0.1:8731
#   AURALIS_PORT=9000 ./scripts/run_dev.sh
#   ./scripts/run_dev.sh --no-access-log # extra flags are passed to uvicorn
#
# If the file is not executable yet:  chmod +x scripts/run_dev.sh

set -euo pipefail

PORT="${AURALIS_PORT:-8731}"
BIND="${AURALIS_HOST:-127.0.0.1}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# --- interpreter ------------------------------------------------------------
if command -v python3 >/dev/null 2>&1; then
    PY="python3"
elif command -v python >/dev/null 2>&1; then
    PY="python"
else
    echo "error: no python interpreter found on PATH" >&2
    exit 1
fi

if ! "${PY}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
    echo "error: ${PY} is older than 3.11" >&2
    exit 1
fi

# --- make the repository importable -----------------------------------------
if [ -n "${PYTHONPATH:-}" ]; then
    export PYTHONPATH="${REPO_ROOT}:${PYTHONPATH}"
else
    export PYTHONPATH="${REPO_ROOT}"
fi

# --- warn early if the API entry point is missing ---------------------------
if [ ! -f "${REPO_ROOT}/aurserver/app.py" ]; then
    echo "warning: aurserver/app.py is not present yet - the API entry point is still being written." >&2
fi

URL="http://${BIND}:${PORT}/"
echo
echo "  Auralis - development server"
echo "  repo   : ${REPO_ROOT}"
echo "  UI     : ${URL}"
echo "  API    : ${URL}api/"
echo "  reload : on"
echo "  stop   : Ctrl+C"
echo

exec "${PY}" -m uvicorn aurserver.app:app \
    --host "${BIND}" \
    --port "${PORT}" \
    --app-dir "${REPO_ROOT}" \
    --log-level info \
    --reload ${@+"$@"}
