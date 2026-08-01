@echo off
cd /d "%~dp0"
if exist "%~dp0TwitchRecorder.exe" (
  start "" "%~dp0TwitchRecorder.exe"
  exit /b 0
)
if exist "%~dp0dist\TwitchRecorder.exe" (
  start "" "%~dp0dist\TwitchRecorder.exe"
  exit /b 0
)
python gui.py
if errorlevel 1 pause
