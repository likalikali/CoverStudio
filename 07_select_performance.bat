@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3.12 "%~dp007_select_performance.py"
set "rc=%errorlevel%"
echo.
pause
exit /b %rc%
