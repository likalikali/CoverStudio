@echo off
chcp 65001 >nul
cd /d "%~dp0"
winget install --id Gyan.FFmpeg.Shared -e --accept-source-agreements --accept-package-agreements
set "rc=%errorlevel%"
if not "%rc%"=="0" echo. & echo ERROR: command failed, exit code %rc%.
echo.
pause
exit /b %rc%
