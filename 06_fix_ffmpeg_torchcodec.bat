@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
py -3.12 "%~dp0fix_ffmpeg_torchcodec.py"
set "rc=%errorlevel%"
echo.
if "%rc%"=="0" (
    echo [OK] FFmpeg and TorchCodec check passed. Close this window and run 02_start_gpu.bat.
) else (
    echo [ERROR] Repair or TorchCodec test failed with code %rc%. Copy the error above for troubleshooting.
)
echo.
pause
exit /b %rc%
