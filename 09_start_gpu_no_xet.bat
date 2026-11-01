@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist "%~dp002_start_gpu.bat" (
  echo [ERROR] 02_start_gpu.bat not found in this folder.
  pause
  exit /b 1
)
rem These variables only affect this launch; no system-wide setting changes.
set "HF_ENDPOINT=https://huggingface.co"
set "HF_HUB_DISABLE_XET=1"
set "HF_HUB_DOWNLOAD_TIMEOUT=120"
set "HF_HUB_ETAG_TIMEOUT=30"
call "%~dp002_start_gpu.bat"
exit /b %errorlevel%
