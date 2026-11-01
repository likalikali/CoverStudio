@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PY=%~dp0tsumugi-piano-cover\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [ERROR] Cover Studio Python was not found.
  echo Put these files in the SAME folder as 02_start_gpu.bat.
  goto :fail
)
set "HF_HOME=%~dp0cache\huggingface"
set "HF_ENDPOINT=https://huggingface.co"
set "HF_HUB_DISABLE_XET=1"
set "HF_HUB_DOWNLOAD_TIMEOUT=120"
set "HF_HUB_ETAG_TIMEOUT=30"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
echo ==========================================================
echo   Cover Studio - SheetSage2 download (non-Xet fallback)
echo ==========================================================
echo Cache: %HF_HOME%
echo NOTE: Close 02_start_gpu.bat before running this downloader.
echo NOTE: Downloading ~2.7 GB can take time; no CUDA activity is expected.
echo.
"%PY%" "%~dp008_download_sheetsage2.py"
set "rc=%errorlevel%"
if not "%rc%"=="0" goto :fail
echo.
echo [OK] Prefetch finished. Start using 09_start_gpu_no_xet.bat.
echo.
pause
exit /b 0
:fail
echo.
echo [ERROR] Model download failed. Please copy the last 30 lines of this window.
echo.
pause
exit /b 1
