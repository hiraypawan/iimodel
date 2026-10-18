@echo off
cd /d "%~dp0"
echo Starting II v12...
echo Model: models/qwen2.5-1.5b-instruct-q4_0.gguf
echo Server: http://127.0.0.1:5000
echo.
.venv\Scripts\python.exe app.py
echo.
echo Server stopped. Press any key to close...
pause
