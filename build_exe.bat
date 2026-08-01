@echo off
cd /d "%~dp0"
echo Building TwitchRecorder (one-dir — avoids Temp\_MEI unpack that Defender often deletes) ...
python -m pip install -q pyinstaller streamlink pyyaml customtkinter
python -m PyInstaller --noconfirm --clean --windowed --onedir ^
  --name TwitchRecorder ^
  --version-file file_version_info.txt ^
  --collect-all customtkinter ^
  --collect-all streamlink ^
  --hidden-import yaml ^
  --hidden-import recorder_core ^
  --hidden-import deps ^
  --hidden-import updater ^
  --hidden-import version ^
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
echo.
echo Done: dist\TwitchRecorder\TwitchRecorder.exe
echo Next: build_installer.bat
pause
