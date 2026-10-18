@echo off
cd /d "%~dp0"
echo.
echo ========================================
echo   II v12 - Install & Run (Complete)
echo ========================================
echo.
echo This package includes everything pre-installed:
echo   - Python 3.11 virtual environment (.venv)
echo   - All Python dependencies (flask, networkx, playwright, etc.)
echo   - Z3 SMT solver (z3-solver)
echo   - PyTorch + Transformers (MobileCLIP vision)
echo   - Pillow (image processing)
echo   - Tree-sitter (code parsing)
echo   - All II v12 modules
echo.
echo NOTE: Model file (qwen2.5-1.5b-instruct-q4_0.gguf, ~986MB) is NOT included
echo        due to size. Place it in models/ folder before running.
echo.
echo OPTIONAL: For real model chat, install LM Studio (https://lmstudio.ai/)
echo            Load the GGUF model and start Local Server at port 1234.
echo.

echo Checking prerequisites...
echo.

if not exist "models\qwen2.5-1.5b-instruct-q4_0.gguf" (
    echo [WARNING] Model file not found in models/ folder.
    echo           Download from: https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF
    echo           Place as: models\qwen2.5-1.5b-instruct-q4_0.gguf
    echo.
    echo The server will start in demo mode without the model.
    echo.
    pause
)

echo Starting II v12 server...
echo   Open http://127.0.0.1:5000 in your browser
echo.
.venv\Scripts\python.exe app.py
echo.
echo Server stopped. Press any key to close...
pause
