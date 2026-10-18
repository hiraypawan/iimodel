@echo off
setlocal
cd /d "%~dp0"
title II - Local Tool-Augmented Agent

echo.
echo ========================================
echo   II local agent prototype
echo ========================================
echo.
echo Model weights are not included. Start LM Studio with a model loaded at
echo http://127.0.0.1:1234 for real model chat. The app can still start in demo mode.
echo.

where py >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python Launcher 'py' was not found. Install Python 3.11 or newer.
    goto error_exit
)

py -3.11 --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3.11 was not found. Install Python 3.11 or newer.
    goto error_exit
)

if not exist ".venv\Scripts\python.exe" (
    echo [SETUP] Creating virtual environment...
    py -3.11 -m venv .venv
    if errorlevel 1 goto error_exit
)

set "PYTHON=.venv\Scripts\python.exe"
"%PYTHON%" -m pip install --upgrade pip
if errorlevel 1 goto error_exit

"%PYTHON%" -m pip install -r requirements.txt
if errorlevel 1 goto error_exit

echo.
echo [START] Open http://127.0.0.1:5000/ after the server starts.
echo.
"%PYTHON%" app.py
if errorlevel 1 goto error_exit
goto success_exit

:error_exit
echo.
echo [ERROR] Setup or startup failed. Review the message above.
pause
exit /b 1

:success_exit
echo.
echo II server stopped.
pause
exit /b 0
