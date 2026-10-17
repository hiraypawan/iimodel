@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0"

title II v12 - Setup Helper
echo.
echo ========================================
echo   II v12 - Setup Helper
echo   Auto-downloads LM Studio + Model
echo ========================================
echo.

:: --- Check Python ---
echo [1/4] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3.11+ is not installed or not on PATH.
    echo         Install from https://python.org/downloads/release/python-3119/
    pause
    exit /b 1
)
echo [OK] Python found.

:: --- Check model file ---
echo [2/4] Checking model file...
if exist "models\qwen2.5-1.5b-instruct-q4_0.gguf" (
    echo [OK] Model file already exists in models/ folder.
) else (
    echo [INFO] Model file not found. You have two options:
    echo.
    echo   Option A: Download LM Studio and load the model manually
    echo   Option B: Place qwen2.5-1.5b-instruct-q4_0.gguf in models/ folder
    echo.
    echo [INFO] Recommended: Install LM Studio from https://lmstudio.ai/
    echo         Then load Qwen2.5-1.5B-Instruct GGUF and start Local Server.
    echo.
    pause
)

:: --- Install Python dependencies ---
echo [3/4] Installing Python dependencies...
call venv\Scripts\activate.bat 2>nul || (
    echo [INFO] Creating venv first...
    python -m venv venv
    call venv\Scripts\activate.bat
)
python -m pip install --upgrade pip >nul 2>&1
pip install -r requirements.txt
if errorlevel 1 (
    echo [WARNING] Some dependencies may have failed.
)
echo [OK] Dependencies installed.

:: --- Instructions ---
echo.
echo ========================================
echo   SETUP COMPLETE
echo ========================================
echo.
echo NEXT STEPS:
echo   1. Install LM Studio: https://lmstudio.ai/
echo   2. Load model: qwen2.5-1.5b-instruct-q4_0.gguf
echo   3. Start Local Server at http://127.0.0.1:1234
echo   4. Run: start.bat
echo   5. Open: http://127.0.0.1:5000/
echo.
echo If you prefer llama-cpp-python backend:
echo   pip install llama-cpp-python --prefer-binary --no-cache-dir
echo.
pause
