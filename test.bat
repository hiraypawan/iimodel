@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo ERROR: .venv not found. Run INSTALL_AND_RUN.bat first.
    pause
    exit /b 1
)
echo II v3.0 - test suite
.venv\Scripts\python.exe --version
.venv\Scripts\python.exe -m unittest discover -s tests -v
pause
