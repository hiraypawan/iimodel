@echo off
cd /d "%~dp0"
echo.
echo ========================================
echo   II v3.0 - Install and Run
echo ========================================
echo.
echo This installs the lean core dependencies (requirements.txt) into .venv\
echo Heavy extras (torch, transformers, playwright) are NOT installed;
echo see requirements-optional.txt if you need them.
echo.
echo II tools need no GGUF download or LM Studio server.
echo General chat supports optional OpenAI-compatible BYOK in the web UI.
echo.

if not exist ".venv\Scripts\python.exe" (
    echo Creating Python 3.11 virtual environment at .venv\ ...
    py -3.11 -m venv .venv
    if errorlevel 1 (
        echo ERROR: Python 3.11 was not found. Install it from python.org first.
        pause
        exit /b 1
    )
)

echo Installing core dependencies...
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo ERROR: dependency installation failed.
    pause
    exit /b 1
)

echo Starting II v3.0 server...
echo   Open http://127.0.0.1:5000 in your browser
echo.
.venv\Scripts\python.exe app.py
echo.
echo Server stopped. Press any key to close...
pause
