@echo off
setlocal
title Comfy Studio setup
cd /d "%~dp0"
echo.
echo  Comfy Studio setup
echo  ------------------
rem 1) Python 3.10 or newer
set "PYW="
for /f "delims=" %%P in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%P"
if not defined PYEXE for /f "delims=" %%P in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%P"
if not defined PYEXE (
  echo  Python was not found. Install Python 3.12 from https://www.python.org/downloads/
  echo  ^(tick "Add python.exe to PATH"^), then run this setup again.
  pause & exit /b 1
)
"%PYEXE%" -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" || (echo  Python 3.10 or newer is needed. & pause & exit /b 1)
for %%D in ("%PYEXE%") do set "PYW=%%~dpDpythonw.exe"
if not exist "%PYW%" set "PYW=%PYEXE%"
if not exist data mkdir data
> data\python_path.txt echo %PYW%
"%PYEXE%" -m pip install --quiet --upgrade pywebview pillow >nul 2>&1
rem 2) ComfyUI
"%PYEXE%" -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8188/system_stats',timeout=3)" >nul 2>&1
if errorlevel 1 (
  echo  ComfyUI is not running. Start ComfyUI first ^(Comfy Studio finds its folders by itself^),
  echo  then run this setup again. Get ComfyUI from https://www.comfy.org/download
  pause & exit /b 1
)
rem 3) desktop shortcut
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Comfy Studio.lnk');$s.TargetPath='%PYW%';$s.Arguments='\"%~dp0ComfyStudio.pyw\"';$s.WorkingDirectory='%~dp0';$s.IconLocation='%~dp0comfy_studio.ico';$s.Save()"
echo  Done. Starting Comfy Studio...
wscript.exe "%~dp0start_app.vbs"
echo  Open the Workflows page: each engine shows what it needs and a Download button for missing files.
timeout /t 6 >nul
