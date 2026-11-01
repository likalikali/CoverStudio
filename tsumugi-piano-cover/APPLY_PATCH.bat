@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo  Cover Studio - RTX 3060 reliability patch
echo  请先确认 02_start_gpu.bat 已经关闭。
echo.
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "apply_coverstudio_patch.py"
) else (
    py -3.12 "apply_coverstudio_patch.py"
)
set "PATCH_RC=%ERRORLEVEL%"
echo.
if "%PATCH_RC%"=="0" (
    echo 补丁已成功应用。现在可以重新启动 Cover Studio。
) else (
    echo 补丁未成功应用，请把上面的错误信息发给我。
)
echo.
pause
exit /b %PATCH_RC%
