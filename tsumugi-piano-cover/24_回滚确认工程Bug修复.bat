@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Cover Studio - Roll back confirmed engineering fixes
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "ROLLBACK_CONFIRMED_ENGINE_FIXES.py"
) else (
  py -3.12 "ROLLBACK_CONFIRMED_ENGINE_FIXES.py"
)
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
