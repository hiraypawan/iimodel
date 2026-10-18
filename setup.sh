#!/usr/bin/env bash
# setup.sh -- create the virtual environment and install the core dependencies.
# Mirrors setup_ii.bat / INSTALL_AND_RUN.bat.  Model weights are NOT
# downloaded: II works without a model backend (it then renders structured
# results deterministically instead of through the LLM).
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
echo "NOTE: Model weights are NOT included and are never downloaded here."
echo "For local GGUF chat, put a compatible GGUF file in models/ and install"
echo "llama-cpp-python, or start LM Studio at http://127.0.0.1:1234."
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
