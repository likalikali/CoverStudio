@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Cover Studio - Boundary correctness hotfix
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "INSTALL_BOUNDARY_FIX.py"
) else (
    py -3.12 "INSTALL_BOUNDARY_FIX.py"
)
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" echo Please restart your original 02_start_gpu.bat
if not "%RC%"=="0" echo No changes or automatic recovery; please copy the error message.
pause
exit /b %RC%
