@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "16_回滚边界修复.py"
) else (
    py -3.12 "16_回滚边界修复.py"
)
pause
