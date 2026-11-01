@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Cover Studio - Remove unverified tail heuristic
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "INSTALL_REMOVE_TAIL_GUARD.py"
) else (
  py -3.12 "INSTALL_REMOVE_TAIL_GUARD.py"
)
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
