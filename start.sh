#!/usr/bin/env bash
# start.sh -- start the II server. Mirrors start.bat.
# The server binds to 127.0.0.1 by default.  Pass --host/--port to change it:
# exposing it beyond localhost prints a loud warning.
set -euo pipefail

cd "$(dirname "$0")"

VENV_DIR="${VENV_DIR:-.venv}"
HOST="${II_HOST:-127.0.0.1}"
PORT="${II_PORT:-5000}"

echo
echo "========================================"
echo "  II v3.0 - Pure Internet Intelligence"
echo "========================================"
echo
echo "Server: http://${HOST}:${PORT}"
echo

if [ ! -x "${VENV_DIR}/bin/python" ]; then
    echo "ERROR: virtual environment not found at ${VENV_DIR}/"
    echo "Create it first:"
    echo "   python3 -m venv ${VENV_DIR}"
    echo "   ${VENV_DIR}/bin/python -m pip install -r requirements.txt"
    echo "Or run ./setup.sh, or: python3 bootstrap.py"
    exit 1
fi

echo "Engine: II internet/tools + optional OpenAI-compatible BYOK chat"
echo
echo "Starting II v3.0 server..."
echo
# Record the PID so that ./stop.sh can stop this instance.
echo $$ > .ii_server.pid
exec "${VENV_DIR}/bin/python" app.py --host "${HOST}" --port "${PORT}"
