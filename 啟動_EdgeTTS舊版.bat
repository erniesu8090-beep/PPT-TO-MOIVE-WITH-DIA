@echo off
cd /d "%~dp0"
echo ===================================================
echo   Video Workshop - EdgeTTS Edition (Port: 8000)
echo ===================================================
echo Starting browser: http://localhost:8000/ ...
start http://localhost:8000/
echo Starting Python server...
python app.py
if errorlevel 1 (
    echo.
    echo Server encountered an error.
    pause
)