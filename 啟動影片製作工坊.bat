@chcp 65001 >nul
@echo off
title Video Workshop - Launcher

cd /d "%~dp0core"

set "PYTHONUTF8=1"
set "PY_CMD=python"
if exist "%~dp0.venv\Scripts\python.exe" (
    set "PY_CMD=%~dp0.venv\Scripts\python.exe"
)

echo ===================================================
echo   Video Workshop (Web GUI)
echo ===================================================
echo.
echo Starting backend server: http://localhost:8000/ ...
start http://localhost:8000/

%PY_CMD% app.py
if errorlevel 1 (
    echo.
    echo [ERROR] Server encountered an error.
    pause
)