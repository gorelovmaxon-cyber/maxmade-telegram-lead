@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
".venv\Scripts\python.exe" reader.py configure
if errorlevel 1 goto done
".venv\Scripts\python.exe" reader.py login
:done
pause
