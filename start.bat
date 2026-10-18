@echo off
cd /d "%~dp0"
echo.
echo ========================================
echo   II v12 - Local AI Agent (Complete)
echo ========================================
echo.
echo Model: models/qwen2.5-1.5b-instruct-q4_0.gguf
echo Server: http://127.0.0.1:5000
echo.

:: Check if venv exists
if not exist ".venv\Scripts\python.exe" (
    echo ERROR: Virtual environment not found.
    echo Please extract the full package - .venv folder is required.
    pause
    exit /b 1
)

:: Check if model exists
if not exist "models\qwen2.5-1.5b-instruct-q4_0.gguf" (
    echo ERROR: Model file missing: models\qwen2.5-1.5b-instruct-q4_0.gguf
    echo Place the GGUF model in the models/ folder first.
    pause
    exit /b 1
)

echo Starting II v12 server...
echo.
.venv\Scripts\python.exe app.py
echo.
echo Server stopped. Press any key to close...
pause
