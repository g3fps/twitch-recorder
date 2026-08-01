@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================
echo  Twitch Auto Recorder - Install
echo ============================================
echo.

if not exist "%~dp0TwitchRecorder.exe" (
  echo TwitchRecorder.exe not found in this folder.
  echo Download it from GitHub Releases into this folder, then run Install.bat again.
  pause
  exit /b 1
)

set "INSTALL_DIR=%LOCALAPPDATA%\TwitchRecorder"
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"

echo Installing to:
echo   %INSTALL_DIR%
echo.
copy /Y "%~dp0TwitchRecorder.exe" "%INSTALL_DIR%\TwitchRecorder.exe" >nul
if errorlevel 1 (
  echo Failed to copy TwitchRecorder.exe
  pause
  exit /b 1
)

if exist "%~dp0config.example.yaml" (
  if not exist "%INSTALL_DIR%\config.yaml" (
    copy /Y "%~dp0config.example.yaml" "%INSTALL_DIR%\config.yaml" >nul
  )
)

echo.
set "ADD_START=Y"
set /p ADD_START="Add Start Menu shortcut? [Y/n]: "
if /I "%ADD_START%"=="n" goto :skip_start

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$w = New-Object -ComObject WScript.Shell; " ^
  "$dir = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'; " ^
  "$lnk = Join-Path $dir 'Twitch Auto Recorder.lnk'; " ^
  "$s = $w.CreateShortcut($lnk); " ^
  "$s.TargetPath = Join-Path $env:LOCALAPPDATA 'TwitchRecorder\TwitchRecorder.exe'; " ^
  "$s.WorkingDirectory = Join-Path $env:LOCALAPPDATA 'TwitchRecorder'; " ^
  "$s.Description = 'Twitch Auto Recorder'; " ^
  "$s.Save(); " ^
  "Write-Host ('Shortcut: ' + $lnk)"
if errorlevel 1 (
  echo Warning: could not create Start Menu shortcut.
) else (
  echo Start Menu shortcut created.
)

:skip_start
echo.
echo Done. Launching app...
start "" "%INSTALL_DIR%\TwitchRecorder.exe"
echo.
pause
