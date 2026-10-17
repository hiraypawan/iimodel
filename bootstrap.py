"""
bootstrap.py — II v11 Auto-Bootstrapper

Runs before app.py to ensure every dependency is installed,
including llama-cpp-python with a pre-built wheel (no build tools needed).

Usage:
    python bootstrap.py
"""

from __future__ import annotations

import subprocess
import sys
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
REQUIREMENTS = HERE / "requirements.txt"


def run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    print(f"[RUN] {' '.join(cmd)}")
    return subprocess.run(cmd, check=check)


def ensure_python() -> None:
    if sys.version_info < (3, 11):
        print("[ERROR] Python 3.11+ is required.")
        sys.exit(1)


def ensure_venv() -> None:
    venv = HERE / "venv"
    if not venv.exists():
        print("[INSTALL] Creating virtual environment...")
        run([sys.executable, "-m", "venv", str(venv)], check=True)


def get_venv_python() -> Path:
    if os.name == "nt":
        return HERE / "venv" / "Scripts" / "python.exe"
    return HERE / "venv" / "bin" / "python"


def install_deps(python_exe: Path) -> None:
    print("[INSTALL] Upgrading pip...")
    run([str(python_exe), "-m", "pip", "install", "--upgrade", "pip"], check=False)

    if REQUIREMENTS.exists():
        print(f"[INSTALL] Installing from {REQUIREMENTS.name}...")
        run([str(python_exe), "-m", "pip", "install", "-r", str(REQUIREMENTS)], check=False)

    # CRITICAL: llama-cpp-python with pre-built wheel — no Visual Studio needed
    print("[INSTALL] Installing llama-cpp-python (pre-built wheel)...")
    run(
        [
            str(python_exe),
            "-m",
            "pip",
            "install",
            "llama-cpp-python",
            "--only-binary=:all:",
        ],
        check=True,
    )

    # Playwright browser for UI heuristics
    print("[INSTALL] Installing Playwright Chromium...")
    run([str(python_exe), "-m", "playwright", "install", "chromium"], check=False)


def launch_app(python_exe: Path) -> None:
    print("[LAUNCH] Starting II v11 server at http://127.0.0.1:5000/")
    os.chdir(HERE)
    run([str(python_exe), "app.py"], check=True)


def main() -> None:
    ensure_python()
    ensure_venv()
    python_exe = get_venv_python()
    install_deps(python_exe)
    launch_app(python_exe)


if __name__ == "__main__":
    main()
