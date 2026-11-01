@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Cover Studio - Roll back tail heuristic removal
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "ROLLBACK_REMOVE_TAIL_GUARD.py"
) else (
  py -3.12 "ROLLBACK_REMOVE_TAIL_GUARD.py"
)
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
