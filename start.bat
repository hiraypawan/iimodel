@echo off
cd /d "%~dp0"
echo.
echo ========================================
echo   II v3.0 - Pure Internet Intelligence
echo ========================================
echo.
echo Server: http://127.0.0.1:5000
echo.

if exist ".venv311\Scripts\python.exe" (
    set PYTHON=.venv311\Scripts\python.exe
) else if exist ".venv\Scripts\python.exe" (
    set PYTHON=.venv\Scripts\python.exe
) else (
    echo ERROR: No virtual environment found. Create .venv311:
    echo   py -3.11 -m venv .venv311
    echo   .venv311\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

if not exist "models\qwen2.5-1.5b-instruct-q4_0.gguf" (
    echo [INFO] No GGUF model in models\ - the app will use LM Studio if
    echo        running, otherwise agentic/demo mode. See README.md.
    echo.
)

echo Starting II v3.0 server...
echo.
%PYTHON% app.py
echo.
echo Server stopped. Press any key to close...
pause
