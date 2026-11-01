@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo  Cover Studio - legacy patch installer
echo.
echo  此安装器已弃用，未修改任何文件。
echo  patch_files 是旧版源码快照，不能覆盖当前 Cover Studio。
echo  请直接从 GitHub 拉取当前代码，不要运行旧补丁。
echo.
pause
exit /b 2
