@echo off
setlocal enabledelayedexpansion

title Stop II Server
cd /d "%~dp0"

echo Stopping II (Internet Intelligence) server...

:: Kill any python process running app.py on port 5000
taskkill /F /IM python.exe /FI "WINDOWTITLE eq II - Local AI Agent" >nul 2>&1
taskkill /F /IM python.exe >nul 2>&1

echo.
echo II server stopped.
pause
