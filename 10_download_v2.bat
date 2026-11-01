@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PY=%~dp0tsumugi-piano-cover\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [ERROR] Cover Studio Python not found.
  echo Copy these files next to 02_start_gpu.bat.
  goto :fail
)
set "HF_HOME=%~dp0cache\huggingface"
set "HF_ENDPOINT=https://huggingface.co"
set "HF_HUB_DISABLE_XET=1"
set "HF_HUB_DOWNLOAD_TIMEOUT=180"
set "HF_HUB_ETAG_TIMEOUT=30"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
echo === Cover Studio V2 model pre-download ===
echo Stop Cover Studio first (Ctrl+C), then run this downloader.
echo HF_HOME=%HF_HOME%
echo.
"%PY%" "%~dp010_download_v2.py"
set "rc=%errorlevel%"
if not "%rc%"=="0" goto :fail
echo.
echo [SUCCESS] V2 files downloaded. Start 09_start_gpu_no_xet.bat.
pause
exit /b 0
:fail
echo.
echo [ERROR] Download not complete. Copy the last error lines.
pause
exit /b 1
