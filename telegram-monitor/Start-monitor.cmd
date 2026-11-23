@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" automation.py start
if errorlevel 1 pause
