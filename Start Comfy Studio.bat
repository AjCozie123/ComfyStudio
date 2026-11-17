@echo off
setlocal EnableExtensions DisableDelayedExpansion
title Comfy Studio
cd /d "%~dp0"

if not exist "%~dp0app\server.py" goto not_extracted
echo "%~dp0" | findstr /i /c:"\AppData\Local\Temp\" >nul
if not errorlevel 1 goto not_extracted

rem the Python that "Install Comfy Studio.bat" set up
set "PYW="
if exist "%~dp0app\data\python_path.txt" set /p PYW=<"%~dp0app\data\python_path.txt"
if not defined PYW goto not_installed
if not exist "%PYW%" goto not_installed
set "PYC=%PYW:pythonw.exe=python.exe%"
if not exist "%PYC%" set "PYC=%PYW%"

"%PYC%" "%~dp0app\tools\launch.py"
if errorlevel 1 goto failed
exit /b 0

:failed
echo.
pause
exit /b 1

:not_installed
echo.
echo  Comfy Studio isn't set up on this PC yet (or its Python was removed).
echo  Double-click "Install Comfy Studio.bat" first. It sets everything up and starts the app.
echo.
pause
exit /b 1

:not_extracted
echo.
echo  Comfy Studio has to be EXTRACTED from the zip first:
echo  right-click the zip, choose "Extract All...", open the extracted folder,
echo  then double-click "Install Comfy Studio.bat".
echo.
pause
exit /b 1
