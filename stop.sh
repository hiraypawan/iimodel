#!/usr/bin/env bash
# stop.sh -- stop a locally started II server. Mirrors stop.bat.
#
# The server writes its PID to .ii_server.pid when it starts through
# start.sh; that PID is used first.  As a fallback the script looks for a
# process whose command line contains "app.py" in this directory.
set -uo pipefail

cd "$(dirname "$0")"

PID_FILE=".ii_server.pid"

echo "Stopping II (Pure Internet Intelligence) server..."

if [ -f "${PID_FILE}" ]; then
    PID="$(cat "${PID_FILE}" 2>/dev/null || true)"
    if [ -n "${PID}" ] && kill -0 "${PID}" 2>/dev/null; then
        kill "${PID}" 2>/dev/null || true
        sleep 1
        kill -9 "${PID}" 2>/dev/null || true
        echo "Stopped PID ${PID}."
    fi
    rm -f "${PID_FILE}"
fi

# Fallback: any python process running this repository's app.py.
PIDS="$(pgrep -f "python.*app\.py" 2>/dev/null || true)"
for pid in ${PIDS}; do
    if grep -qa "app.py" "/proc/${pid}/cmdline" 2>/dev/null; then
        kill "${pid}" 2>/dev/null || true
        sleep 1
        kill -9 "${pid}" 2>/dev/null || true
        echo "Stopped PID ${pid}."
    fi
done

echo
echo "II server stopped."
