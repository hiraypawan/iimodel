@echo off
cd /d "%~dp0"
echo Test 1: Python version
python --version
echo Test 2: Check venv
if exist ".venv\Scripts\python.exe" (
    echo Venv exists
    .venv\Scripts\python.exe --version
) else (
    echo Venv NOT found
)
echo Test 3: Run app
.venv\Scripts\python.exe -c "print('Hello from II v12')"
echo All tests done.
pause