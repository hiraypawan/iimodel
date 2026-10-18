@echo off
cd /d "%~dp0"
echo.
echo ========================================
echo   II v2.0 - Install and Run
echo ========================================
echo.
echo This installs the lean core dependencies (requirements.txt) into .venv\
echo Heavy extras (torch, transformers, playwright) are NOT installed;
echo see requirements-optional.txt if you need them.
echo.
echo NOTE: Model weights are NOT included. For local GGUF chat download
echo qwen2.5-1.5b-instruct-q4_0.gguf (~986 MB) into models\ from
echo https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF
echo or start LM Studio at http://127.0.0.1:1234 with a model loaded.
echo Without either, II runs in agentic/demo mode (web search synthesis).
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
    echo WARNING: dependency install failed. If llama-cpp-python has no
    echo prebuilt wheel for your machine, install it separately with:
    echo   .venv\Scripts\python.exe -m pip install llama-cpp-python --only-binary=:all:
    echo or use LM Studio as the model backend instead.
    pause
    exit /b 1
)

if not exist "models\qwen2.5-1.5b-instruct-q4_0.gguf" (
    echo [WARNING] No model file in models\ - starting in agentic/demo mode.
    echo.
)

echo Starting II v2.0 server...
echo   Open http://127.0.0.1:5000 in your browser
echo.
.venv\Scripts\python.exe app.py
echo.
echo Server stopped. Press any key to close...
pause
