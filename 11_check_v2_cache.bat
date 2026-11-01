@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PY=%~dp0tsumugi-piano-cover\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [ERROR] Cover Studio Python not found. Copy files next to 02_start_gpu.bat.
  pause
  exit /b 2
)
set "HF_HOME=%~dp0cache\huggingface"
set "HF_ENDPOINT=https://huggingface.co"
set "HF_HUB_DISABLE_XET=1"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
echo === V2 model download state: OFFLINE, no network ===
"%PY%" "%~dp010_download_v2.py" --check
if errorlevel 1 (
  echo.
  echo [INFO] Missing cached files. If the server is downloading, check again shortly.
  echo [INFO] If no progress, close server and run 10_download_v2.bat.
)
echo.
pause
