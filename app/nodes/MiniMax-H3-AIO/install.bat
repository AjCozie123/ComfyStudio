@echo off
title MiniMax H3 AIO installer
rem Installs what the MiniMax H3 AIO node needs into the ComfyUI this folder sits in
rem (ComfyUI\custom_nodes\MiniMax-H3-AIO). Safe to run again: finished parts are skipped, nothing is deleted.
cd /d "%~dp0"

set "PY="
if exist "..\..\..\python_embeded\python.exe" set "PY=..\..\..\python_embeded\python.exe"
if not defined PY if exist "..\..\.venv\Scripts\python.exe" set "PY=..\..\.venv\Scripts\python.exe"
if not defined PY if exist "..\..\venv\Scripts\python.exe" set "PY=..\..\venv\Scripts\python.exe"
if not defined PY (
  echo Could not find ComfyUI's Python ^(python_embeded or .venv^).
  echo Put this folder in ComfyUI\custom_nodes and run this again.
  pause
  exit /b 1
)

echo MiniMax H3 AIO installer
echo.
echo Installs up to 5 node packs and about 44 GB of models (two H3 models, text encoder, VAEs,
echo speed LoRA, latent upscaler). Files you already have are skipped, even if renamed.
echo Close ComfyUI before you carry on.
echo.
pause
"%PY%" -s install.py
echo.
pause
