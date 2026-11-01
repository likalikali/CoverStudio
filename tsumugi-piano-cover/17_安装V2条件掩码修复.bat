@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Cover Studio - V2 BeatGrid Planner mask fix
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "INSTALL_BEATGRID_MASK.py"
) else (
  py -3.12 "INSTALL_BEATGRID_MASK.py"
)
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (echo Install complete. Restart 02_start_gpu.bat.) else (echo Not installed. Send the error text.)
pause
exit /b %RC%
