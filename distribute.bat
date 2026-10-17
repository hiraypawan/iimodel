@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

:: ================================================================
// II v11 — Create Distributable Package
// Run this to create a zip file you can share or sell
// ================================================================

title II - Create Distribution
echo.
echo ========================================
echo   Creating II v11 Distribution Package
echo ========================================
echo.

:: --- Check for zip tool ---
echo [1/3] Checking for zip tool...
where 7z >nul 2>&1
if errorlevel 1 (
    echo [!] 7z not found. Using Windows built-in tar...
    set ZIP_CMD=tar -a -c -f
) else (
    echo [OK] 7z found.
    set ZIP_CMD=7z a
)

:: --- Create dist folder ---
echo [2/3] Creating dist folder...
if exist "dist" rmdir /s /q dist
mkdir dist
mkdir dist\models
mkdir dist\venv

:: --- Copy all source files ---
echo [3/3] Copying files...
copy ii_agent.py dist\
copy ii_web.py dist\
copy ii_state.py dist\
copy ii_constraints.py dist\
copy ii_ui_heuristics.py dist\
copy ii_smt.py dist\
copy ii_synthesis.py dist\
copy ii_vision.py dist\
copy ii_blackboard.py dist\
copy llama_local.py dist\
copy app.py dist\
copy main.py dist\
copy requirements.txt dist\
copy start.bat dist\
copy stop.bat dist\
copy INSTALL_AND_RUN.bat dist\
copy bootstrap.py dist\
copy CUSTOMER_README.txt dist\
copy PLAN.md dist\
copy README.md dist\

:: --- Copy model file ---
copy models\qwen2.5-1.5b-instruct-q4_0.gguf dist\models\

:: --- Create zip ---
echo.
echo Creating zip file...
set ZIPNAME=II_v11_Local_Agent.zip
if exist "%ZIPNAME%" del "%ZIPNAME%"

if "%ZIP_CMD%"=="7z a" (
    7z a "%ZIPNAME%" dist\*
) else (
    tar -a -c -f "%ZIPNAME%" dist\*
)

echo.
echo ========================================
echo   DISTRIBUTION PACKAGE CREATED
echo ========================================
echo.
echo File: %ZIPNAME%
echo Size:
dir "%ZIPNAME%"
echo.
echo To share: Send "%ZIPNAME%" to anyone.
echo They extract it and double-click INSTALL_AND_RUN.bat.
echo.
pause
