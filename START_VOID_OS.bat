@echo off
setlocal
cd /d "%~dp0"
set "PY="
py -3 -c "import sys,tkinter; assert sys.version_info >= (3,10)" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys,tkinter; assert sys.version_info >= (3,10)" >nul 2>&1 && set "PY=python"
if not defined PY python3 -c "import sys,tkinter; assert sys.version_info >= (3,10)" >nul 2>&1 && set "PY=python3"
if not defined PY (echo Python 3.10+ with Tkinter was not found.& echo Install Python from python.org and enable Add Python to PATH.& pause& exit /b 1)
%PY% boot.py
if errorlevel 1 pause
