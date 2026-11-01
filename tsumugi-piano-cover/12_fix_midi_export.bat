@echo off
setlocal
cd /d "%~dp0"
set "PYTHON="
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYTHON (
  where py >nul 2>nul
  if not errorlevel 1 set "PYTHON=py -3.12"
)
if not defined PYTHON set "PYTHON=python"
echo Cover Studio MIDI export patch
echo Project root: %CD%
echo.
%PYTHON% "apply_midi_export_fix.py"
echo.
pause
