@echo off
cd /d "%~dp0"
if not exist "%~dp0TwitchRecorder.exe" (
  echo TwitchRecorder.exe missing. Run build_exe.bat first.
  pause
  exit /b 1
)

set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
  echo Inno Setup 6 not found. Install with:
  echo   winget install JRSoftware.InnoSetup
  pause
  exit /b 1
)
echo Building TwitchRecorderSetup.exe ...
"%ISCC%" TwitchRecorder.iss
if errorlevel 1 (
  echo Installer build failed.
  pause
  exit /b 1
)
copy /Y dist_installer\TwitchRecorderSetup.exe TwitchRecorderSetup.exe >nul
echo.
echo Done: TwitchRecorderSetup.exe
pause
