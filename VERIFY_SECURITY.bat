@echo off
cd /d "%~dp0"
py -3 VERIFY_SECURITY.py 2>nul || python VERIFY_SECURITY.py
pause
