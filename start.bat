@echo off
cd /d "%~dp0"
echo.
echo ========================================
echo   II v3.0 - Pure Internet Intelligence
echo ========================================
echo.
echo Server: http://127.0.0.1:5000
echo.

if not exist ".venv\Scripts\python.exe" (
    echo ERROR: Virtual environment not found at .venv\
    echo Create it first:
    echo   py -3.11 -m venv .venv
    echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo Or run INSTALL_AND_RUN.bat, or: python bootstrap.py
    pause
    exit /b 1
)

echo Engine: II internet/tools + optional OpenAI-compatible BYOK chat
echo.
echo Starting II v3.0 server...
echo.
.venv\Scripts\python.exe app.py
echo.
echo Server stopped. Press any key to close...
pause
