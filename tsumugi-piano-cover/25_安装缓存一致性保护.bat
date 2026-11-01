@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "INSTALL_CACHE_INTEGRITY.py"
) else (
  py -3.12 "INSTALL_CACHE_INTEGRITY.py"
)
echo.
pause
