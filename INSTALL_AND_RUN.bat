@echo off
cd /d "%~dp0"
echo.
echo ========================================
echo   II v12 - Install & Run
echo ========================================
echo.
echo 1. Checking Python...
python --version 2>&1
if errorlevel 1 (
    echo ERROR: Python not found on PATH.
    echo Install Python 3.11 from https://python.org/downloads/release/python-3119/
    pause
    exit /b 1
)
echo.

echo 2. Checking model file...
if not exist "models\qwen2.5-1.5b-instruct-q4_0.gguf" (
    echo ERROR: Model file missing: models\qwen2.5-1.5b-instruct-q4_0.gguf
    echo Place the GGUF model in the models/ folder first.
    pause
    exit /b 1
)
echo [OK] Model file found.
echo.

echo 3. Checking virtual environment...
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -3.11 -m venv .venv
    if errorlevel 1 (
        echo ERROR: Failed to create venv.
        pause
        exit /b 1
    )
    echo [OK] Venv created.
) else (
    echo [OK] Venv already exists.
)
echo.

echo 4. Activating venv and installing dependencies...
call .venv\Scripts\activate.bat
.venv\Scripts\python.exe -m pip install --upgrade pip >nul 2>&1
.venv\Scripts\python.exe -m pip install -r requirements.txt
echo [OK] Dependencies installed.
echo.

echo 5. Installing llama-cpp-python (optional)...
.venv\Scripts\python.exe -m pip install llama-cpp-python --prefer-binary --no-cache-dir 2>nul
if errorlevel 1 (
    echo [INFO] llama-cpp-python not installed (will use LM Studio or demo mode).
)
echo.

echo 6. Starting II v12 server...
echo   Open http://127.0.0.1:5000 in your browser
echo.
.venv\Scripts\python.exe app.py
echo.
echo Server stopped. Press any key to close...
pause
