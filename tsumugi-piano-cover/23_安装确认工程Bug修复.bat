@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Cover Studio - Install confirmed engineering fixes
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "INSTALL_CONFIRMED_ENGINE_FIXES.py"
) else (
  py -3.12 "INSTALL_CONFIRMED_ENGINE_FIXES.py"
)
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" echo Installation complete. Restart 02_start_gpu.bat.
if not "%RC%"=="0" echo Not installed. Please send the error text.
pause
exit /b %RC%
