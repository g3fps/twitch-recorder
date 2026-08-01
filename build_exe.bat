@echo off
cd /d "%~dp0"
echo Building TwitchRecorder.exe (one-file, with streamlink bundled) ...
python -m pip install -q pyinstaller streamlink pyyaml customtkinter
python -m PyInstaller --noconfirm --clean --windowed --onefile ^
  --name TwitchRecorder ^
  --collect-all customtkinter ^
  --collect-all streamlink ^
  --hidden-import yaml ^
  --hidden-import recorder_core ^
  --hidden-import deps ^
  --hidden-import streamlink ^
  --hidden-import streamlink.plugins.twitch ^
  --distpath dist ^
  --workpath build ^
  --specpath . ^
  gui.py
if errorlevel 1 (
  echo Build failed.
  pause
  exit /b 1
)
copy /Y dist\TwitchRecorder.exe TwitchRecorder.exe >nul
echo.
echo Done: TwitchRecorder.exe
echo On first run, missing FFmpeg/Streamlink CLI can be auto-installed.
pause
