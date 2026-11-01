@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Cover Studio - Roll back V2 BeatGrid Planner mask fix
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "ROLLBACK_BEATGRID_MASK.py"
) else (
  py -3.12 "ROLLBACK_BEATGRID_MASK.py"
)
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
