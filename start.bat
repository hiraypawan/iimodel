@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0"

title II v12 - Local AI Agent
echo.
echo ========================================
echo   II v12 - Neuro-Symbolic Agentic OS
echo   System-Level Mixture of Experts
echo ========================================
echo.

:: --- Clear pip target redirect ---
set PIP_TARGET=
set PIP_PREFIX=

:: ================================================================
:: Error handler - always pauses before closing
:: ================================================================
:error_exit
echo.
echo ========================================
echo   ERROR: See message above.
echo   Press any key to close this window...
echo ========================================
pause
exit /b 1

:success_exit
echo.
echo ========================================
echo   II v12 server stopped.
echo   Press any key to close this window...
echo ========================================
pause
exit /b 0

:: --- STEP 1: Check Python ---
echo [1/6] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found on PATH.
    echo         Install Python 3.11 from https://python.org/downloads/release/python-3119/
    echo         CHECK "Add Python to PATH" during installation.
    goto error_exit
)
for /f "tokens=2 delims=. " %%a in ('python --version 2^>nul') do set PY_MINOR=%%a
echo [OK] Python found.

:: --- STEP 2: Check model file ---
echo [2/6] Checking model file...
if not exist "models\qwen2.5-1.5b-instruct-q4_0.gguf" (
    echo [ERROR] Model file missing: models\qwen2.5-1.5b-instruct-q4_0.gguf
    echo         Place the GGUF model in the models/ folder first.
    goto error_exit
)
echo [OK] Model file found.

:: --- STEP 3: Create venv ---
echo [3/6] Creating virtual environment...
if exist "venv" (
    rd /s /q "venv"
)
python -m venv venv
if errorlevel 1 (
    echo [ERROR] Failed to create venv.
    goto error_exit
)
echo [OK] Venv created.

:: --- STEP 4: Activate venv ---
echo [4/6] Activating virtual environment...
call venv\Scripts\activate.bat
if errorlevel 1 (
    echo [ERROR] Failed to activate venv.
    goto error_exit
)
echo [OK] Venv activated.

:: --- STEP 5: Install dependencies ---
echo [5/6] Installing dependencies (flask, networkx, playwright, etc.)...
python -m pip install --upgrade pip >nul 2>&1
pip install -r requirements.txt
if errorlevel 1 (
    echo [WARNING] Some core packages failed (non-critical, continuing).
)
echo [OK] Installing llama-cpp-python (pre-built wheel for Python 3.11)...
pip install llama-cpp-python --prefer-binary --no-cache-dir 2>nul
if errorlevel 1 (
    echo [WARNING] llama-cpp-python not installed.
    echo         System will use LM Studio backend at http://127.0.0.1:1234 or demo mode.
)
echo [OK] Installing Playwright Chromium...
playwright install chromium >nul 2>&1
echo [OK] Dependencies ready.

:: --- STEP 6: Launch ---
echo [6/6] Starting II v12 server...
echo   Open http://127.0.0.1:5000 in your browser
echo.

python app.py
if errorlevel 1 (
    echo.
    echo [ERROR] II server crashed. See message above.
    goto error_exit
)

goto success_exit
