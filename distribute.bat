@echo off
setlocal
cd /d "%~dp0"
title Package II prototype

set "DISTROOT=dist"
set "PACKAGE=II-local-agent"
set "ZIPNAME=II-local-agent.zip"

if exist "%DISTROOT%" rmdir /s /q "%DISTROOT%"
mkdir "%DISTROOT%\%PACKAGE%"
if errorlevel 1 goto error_exit

for %%F in (*.py) do copy /y "%%F" "%DISTROOT%\%PACKAGE%\" >nul
if errorlevel 1 goto error_exit

copy /y README.md "%DISTROOT%\%PACKAGE%\" >nul
copy /y CUSTOMER_README.txt "%DISTROOT%\%PACKAGE%\" >nul
copy /y requirements.txt "%DISTROOT%\%PACKAGE%\" >nul
copy /y requirements-optional.txt "%DISTROOT%\%PACKAGE%\" >nul
copy /y requirements-dev.txt "%DISTROOT%\%PACKAGE%\" >nul
copy /y LICENSE "%DISTROOT%\%PACKAGE%\" >nul
copy /y start.bat "%DISTROOT%\%PACKAGE%\" >nul
copy /y setup_ii.bat "%DISTROOT%\%PACKAGE%\" >nul
copy /y INSTALL_AND_RUN.bat "%DISTROOT%\%PACKAGE%\" >nul
copy /y stop.bat "%DISTROOT%\%PACKAGE%\" >nul
xcopy /e /i /y templates "%DISTROOT%\%PACKAGE%\templates" >nul
xcopy /e /i /y core "%DISTROOT%\%PACKAGE%\core" >nul
xcopy /e /i /y tools "%DISTROOT%\%PACKAGE%\tools" >nul
xcopy /e /i /y server "%DISTROOT%\%PACKAGE%\server" >nul
xcopy /e /i /y cli "%DISTROOT%\%PACKAGE%\cli" >nul
xcopy /e /i /y vendor "%DISTROOT%\%PACKAGE%\vendor" >nul
xcopy /e /i /y vibe "%DISTROOT%\%PACKAGE%\vibe" >nul
xcopy /e /i /y ingestion "%DISTROOT%\%PACKAGE%\ingestion" >nul
xcopy /e /i /y graph "%DISTROOT%\%PACKAGE%\graph" >nul
xcopy /e /i /y reasoning "%DISTROOT%\%PACKAGE%\reasoning" >nul
xcopy /e /i /y blackboard "%DISTROOT%\%PACKAGE%\blackboard" >nul
xcopy /e /i /y docs "%DISTROOT%\%PACKAGE%\docs" >nul
xcopy /e /i /y tests "%DISTROOT%\%PACKAGE%\tests" >nul
copy /y PLAN.md "%DISTROOT%\%PACKAGE%\" >nul
mkdir "%DISTROOT%\%PACKAGE%\models" 2>nul
echo Model weights are not included. See README.md for the official download page.>"%DISTROOT%\%PACKAGE%\models\README.txt"

if exist "%ZIPNAME%" del "%ZIPNAME%"
where 7z >nul 2>&1
if not errorlevel 1 (
    7z a "%ZIPNAME%" "%DISTROOT%\%PACKAGE%\*"
) else (
    tar -a -c -f "%ZIPNAME%" -C "%DISTROOT%" "%PACKAGE%"
)
if errorlevel 1 goto error_exit

echo Created %ZIPNAME%. Model weights are not bundled.
goto success_exit

:error_exit
echo Package creation failed. Check the errors above.
pause
exit /b 1

:success_exit
pause
exit /b 0
