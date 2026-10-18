@echo off
setlocal
cd /d "%~dp0"
echo II does not bundle or automatically download model weights.
echo Start LM Studio separately with a compatible model, then continue.
echo.
call "%~dp0start.bat"
exit /b %errorlevel%
