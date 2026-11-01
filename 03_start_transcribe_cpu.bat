@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3.12 "%~dp0windows_cover_studio.py" start --mode transcribe-cpu
set "rc=%errorlevel%"
if not "%rc%"=="0" (
    echo.
    echo ERROR: command failed, exit code %rc%.
)
echo.
pause
exit /b %rc%
