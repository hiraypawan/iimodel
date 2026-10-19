#!/usr/bin/env bash
# setup.sh -- create the virtual environment and install the core dependencies.
# Mirrors setup_ii.bat / INSTALL_AND_RUN.bat. Tools need no model weights;
# general chat can use an optional OpenAI-compatible BYOK provider.
set -euo pipefail

cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-.venv}"

echo "========================================"
echo "  II v3.0 - Pure Internet Intelligence"
echo "========================================"
echo
echo "This installs the lean core dependencies (requirements.txt) into ${VENV_DIR}/."
echo "Heavy extras (torch, transformers, playwright) are NOT installed;"
echo "see requirements-optional.txt if you need them."
echo
echo "II tools require no local model weights. General chat supports optional"
echo "OpenAI-compatible BYOK configured in the UI or II_CHAT_* environment."
echo

if [ ! -x "${VENV_DIR}/bin/python" ]; then
    echo "Creating virtual environment at ${VENV_DIR}/ ..."
    "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

echo "Installing core dependencies..."
"${VENV_DIR}/bin/python" -m pip install --upgrade pip
"${VENV_DIR}/bin/python" -m pip install -r requirements.txt

echo
echo "Setup complete. Start the server with: ./start.sh"
