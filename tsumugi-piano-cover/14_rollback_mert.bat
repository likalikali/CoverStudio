@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Cover Studio - Restore separate MERT processing only
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "ROLLBACK_MERT_ONLY.py"
) else (
  py -3.12 "ROLLBACK_MERT_ONLY.py"
)
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
  echo MERT rollback complete. Restart 02_start_gpu.bat.
) else (
  echo Patch was not applied. Please send the error message.
)
pause
exit /b %RC%
