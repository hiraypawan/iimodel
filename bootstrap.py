"""Cross-platform bootstrapper for Pure Internet Intelligence.

Creates a local virtual environment, installs the declared dependencies, and
starts II's tool engine. General chat can use an optional OpenAI-compatible
BYOK provider; no bundled model weights or dedicated local server are needed.
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

    print("[INFO] II tools need no GGUF model; general chat supports OpenAI-compatible BYOK.")
    print("[LAUNCH] Starting II at http://127.0.0.1:5000/")
    os.chdir(HERE)
    run([str(python_exe), "app.py"])


if __name__ == "__main__":
    main()
