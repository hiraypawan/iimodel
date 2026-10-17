@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

title II v11 Install Run
echo.
echo ========================================
echo   II v11 Install Run Launcher
echo   Using Python 3.11 for llama-cpp-python wheel
echo ========================================
echo.
echo NOTE: If this window closes, look at the last message above it.
echo       The window stays open on errors now.
echo.

:: --- Clear pip target that may redirect installs to D:\pip-packages ---
set PIP_TARGET=
set PIP_PREFIX=

:: ================================================================
:: Common error handler - always pauses before closing
:: ================================================================
:error_exit
echo.
echo ========================================
echo   Press any key to close this window...
echo ========================================
pause
exit /b 1

:success_exit
echo.
echo ========================================
echo   Press any key to close this window...
echo ========================================
pause
exit /b 0

:: --- STEP 1: Check Python 3.11 ---
echo [STEP 1/8] Checking Python 3.11...
py -3.11 --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] Python 3.11 not found via 'py -3.11'.
    echo         Install Python 3.11.9 from: https://www.python.org/downloads/release/python-3119/
    echo         CHECK "Add Python to PATH" during installation.
    echo.
    goto error_exit
)
for /f "tokens=*" %%a in ('py -3.11 --version 2^>nul') do set PY_VER=%%a
echo [OK] %PY_VER% found.
echo.
pause

:: --- STEP 2: Check model file ---
echo [STEP 2/8] Checking model file...
if not exist "models\qwen2.5-1.5b-instruct-q4_0.gguf" (
    echo.
    echo [ERROR] Model file missing: models\qwen2.5-1.5b-instruct-q4_0.gguf
    echo         Place the GGUF model in the models/ folder first.
    echo.
    goto error_exit
)
echo [OK] Model file found.
echo.
pause

:: --- STEP 3: Remove old venv ---
echo [STEP 3/8] Removing old virtual environment...
if exist "venv" (
    rd /s /q "venv"
    if errorlevel 1 (
        echo.
        echo [WARNING] Could not delete old venv. Try closing any open Python processes.
        echo.
    )
)
echo [OK] Old venv removed (or did not exist).
echo.
pause

:: --- STEP 4: Create virtual environment with Python 3.11 ---
echo [STEP 4/8] Creating virtual environment with Python 3.11...
py -3.11 -m venv venv
if errorlevel 1 (
    echo.
    echo [ERROR] Failed to create venv with Python 3.11.
    echo.
    goto error_exit
)
echo [OK] Venv created with Python 3.11.
echo.
pause

:: --- STEP 5: Activate virtual environment ---
echo [STEP 5/8] Activating virtual environment...
call venv\Scripts\activate.bat
if errorlevel 1 (
    echo.
    echo [ERROR] Failed to activate venv.
    echo.
    goto error_exit
)
echo [OK] Venv activated.
echo.
pause

:: --- STEP 6: Upgrade pip ---
echo [STEP 6/8] Upgrading pip...
py -3.11 -m pip install --upgrade pip
if errorlevel 1 (
    echo.
    echo [WARNING] Pip upgrade failed (non-critical, continuing).
    echo.
)
echo [OK] Pip step done.
echo.
pause

:: --- STEP 7: Install core dependencies ---
echo [STEP 7/8] Installing core dependencies (flask, networkx, playwright, etc.)...
echo          This may take a few minutes...
pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [WARNING] Some core packages may have failed (non-critical, continuing).
    echo.
)
echo [OK] Core dependencies step done.
echo.
pause

:: --- STEP 8: Install llama-cpp-python ---
echo [STEP 8/8] Installing llama-cpp-python (real model backend)...
echo          Using pre-built wheel for Python 3.11...
echo          This may take 2-5 minutes. DO NOT CLOSE.
pip install --upgrade pip >nul 2>&1
pip install llama-cpp-python --prefer-binary --no-cache-dir
if errorlevel 1 (
    echo.
    echo [WARNING] llama-cpp-python could not be installed from pre-built wheel.
    echo         The system will start in demo mode (tools work, no real model chat).
    echo         You can install Visual Studio C++ Build Tools to fix this.
    echo.
    pause
    echo [OK] Continuing without llama-cpp-python.
) else (
    echo [OK] llama-cpp-python installed.
)
echo.
pause

:: --- LAUNCH ---
echo.
echo ========================================
echo   Setup complete. Launching II v11...
echo   Open http://127.0.0.1:5000 in your browser
echo ========================================
echo.

python app.py
if errorlevel 1 (
    echo.
    echo [ERROR] II server crashed. See the error message above.
    echo.
    goto error_exit
)

echo.
echo [INFO] II server stopped normally.
goto success_exit
