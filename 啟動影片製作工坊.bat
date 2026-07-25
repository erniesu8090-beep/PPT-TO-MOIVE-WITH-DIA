@echo off
title 影片製作工坊 - 啟動器
chcp 65001 > nul

echo ===================================================
echo   影片製作工坊 (Traditional Chinese GUI)
echo ===================================================
echo.
echo 正在啟動後端服務...
echo.

:: 設定 Python 的 UTF-8 模式以避免編碼問題
set PYTHONUTF8=1

:: 切換到批次檔所在的目錄
cd /d "%~dp0"

:: 在預設瀏覽器中開啟網頁
echo 正在自動開啟瀏覽器: http://localhost:8000/ ...
start http://localhost:8000/

:: 執行 Python 後端服務
python app.py

echo.
echo 服務已關閉。
pause
