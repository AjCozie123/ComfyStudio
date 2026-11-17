@echo off
setlocal EnableExtensions DisableDelayedExpansion
title Comfy Studio setup
cd /d "%~dp0"
echo.
echo  Comfy Studio setup
echo  ==================
echo.

rem ---- 1. The whole Comfy Studio folder must be here (not one file opened from inside the zip) ----
if not exist "%~dp0app\server.py" goto not_extracted
if not exist "%~dp0app\tools\setup.py" goto not_extracted
echo "%~dp0" | findstr /i /c:"\AppData\Local\Temp\" >nul
if not errorlevel 1 goto not_extracted

rem ---- 2. Find a 64-bit Python 3.10 - 3.13 (3.12 first: everything Comfy Studio uses supports it) ----
set "PYEXE="
call :try_launcher 3.12
if not defined PYEXE call :try_launcher 3.11
if not defined PYEXE call :try_launcher 3.13
if not defined PYEXE call :try_launcher 3.10
if not defined PYEXE call :try_command python
if not defined PYEXE call :try_command python3
if not defined PYEXE call :try_folders
if not defined PYEXE goto no_python

:run_setup
echo  Using Python: %PYEXE%
"%PYEXE%" "%~dp0app\tools\setup.py"
if errorlevel 1 goto failed
echo.
echo  This window closes by itself in 15 seconds.
timeout /t 15 >nul
exit /b 0

:failed
echo.
echo  Setup stopped. The message above says what to do. Press any key to close this window.
pause >nul
exit /b 1

rem ---------------------------------------------------------------- helpers
:try_launcher
rem the "py" launcher that comes with Python from python.org
for /f "usebackq delims=" %%P in (`py -%1 -c "import sys,struct;print(sys.executable) if struct.calcsize(chr(80))==8 else None" 2^>nul`) do set "PYEXE=%%P"
exit /b 0

:try_command
rem "python" on the PATH (the Microsoft Store shortcut that only opens the Store prints nothing, so it is skipped)
for /f "usebackq delims=" %%P in (`%1 -c "import sys,struct;v=sys.version_info[:2];print(sys.executable) if struct.calcsize(chr(80))==8 and (3,10)<=v<=(3,13) else None" 2^>nul`) do set "PYEXE=%%P"
exit /b 0

:try_folders
rem a Python that is installed but not on the PATH
for %%V in (312 311 313 310) do (
  if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
  if not defined PYEXE if exist "%ProgramFiles%\Python%%V\python.exe" set "PYEXE=%ProgramFiles%\Python%%V\python.exe"
)
exit /b 0

rem ---------------------------------------------------------------- problems
:not_extracted
echo  ------------------------------------------------------------------
echo  Comfy Studio has to be EXTRACTED from the zip before you install it.
echo.
echo  This file was opened from inside the zip (or a temporary folder),
echo  so the rest of Comfy Studio isn't next to it.
echo.
echo   1. Close this window.
echo   2. Find the zip you downloaded (usually in Downloads).
echo   3. Right-click it and choose "Extract All..."
echo      (with 7-Zip: 7-Zip ^> "Extract to ComfyStudio\").
echo      A good place is C:\ComfyStudio or your Documents folder.
echo   4. Open the EXTRACTED folder and double-click "Install Comfy Studio.bat".
echo  ------------------------------------------------------------------
echo.
pause
exit /b 1

:no_python
echo  ------------------------------------------------------------------
echo  Python (64-bit, version 3.10 to 3.13) is needed and wasn't found.
echo  ------------------------------------------------------------------
where winget >nul 2>&1
if errorlevel 1 goto python_by_hand
echo.
choice /c YN /m "  Install Python 3.12 now (free, from python.org through Windows' winget)"
if errorlevel 2 goto python_by_hand
winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
call :try_folders
if not defined PYEXE call :try_launcher 3.12
if defined PYEXE goto run_setup
echo.
echo  Python was installed. Close this window and double-click "Install Comfy Studio.bat" again.
pause
exit /b 1

:python_by_hand
echo.
echo   1. Go to https://www.python.org/downloads/ and download Python 3.12.
echo   2. Run it and TICK "Add python.exe to PATH" at the bottom of the first screen.
echo   3. Click "Install Now". When it's done, double-click "Install Comfy Studio.bat" again.
echo.
pause
exit /b 1
