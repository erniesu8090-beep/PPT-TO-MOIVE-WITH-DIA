@echo off
cd /d "%~dp0gemini_studio"
echo ===================================================
echo   Video Workshop - Gemini 3.8 Edition (Port: 8001)
echo ===================================================
echo Starting browser: http://localhost:8001/ ...
start http://localhost:8001/
echo Starting Python server...
python app.py
if errorlevel 1 (
    echo.
    echo Server encountered an error.
    pause
)