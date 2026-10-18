"""Cross-platform bootstrapper for the II prototype.

Creates a local virtual environment, installs the declared dependencies, and
starts the Flask app. Model weights and llama-cpp-python are not downloaded or
installed automatically; LM Studio is the recommended model backend.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REQUIREMENTS = HERE / "requirements.txt"
VENV = HERE / ".venv"


def run(cmd: list[str]) -> None:
    print(f"[RUN] {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def ensure_python() -> None:
    if sys.version_info < (3, 11):
        raise SystemExit("[ERROR] Python 3.11 or newer is required.")


def venv_python() -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def main() -> None:
    ensure_python()
    if not VENV.exists():
        print("[SETUP] Creating virtual environment...")
        run([sys.executable, "-m", "venv", str(VENV)])

    python_exe = venv_python()
    run([str(python_exe), "-m", "pip", "install", "--upgrade", "pip"])
    run([str(python_exe), "-m", "pip", "install", "-r", str(REQUIREMENTS)])

    print("[INFO] Model weights are not bundled.")
    print("[INFO] For model chat, start LM Studio with a model loaded at http://127.0.0.1:1234.")
    print("[INFO] Otherwise the app will start in demo mode and show setup instructions.")
    print("[LAUNCH] Starting II at http://127.0.0.1:5000/")
    os.chdir(HERE)
    run([str(python_exe), "app.py"])


if __name__ == "__main__":
    main()
