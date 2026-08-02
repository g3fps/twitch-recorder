# Twitch Auto Recorder

Desktop app that waits for any Twitch streamers you add to go live, then records each stream to a local folder.

## Quick start

1. Download **`TwitchRecorderSetup.exe`** from [Releases](https://github.com/g3fps/twitch-recorder/releases)
2. Run it — installer wizard (progress, shortcuts, finish)
3. Optionally **Launch** when setup finishes

That is the only distributed build. It installs to `%LOCALAPPDATA%\TwitchRecorder` (no admin) and places `TwitchRecorder.exe` there for you.

### Updating

On launch the app checks GitHub Releases. If a newer version exists:

- An **Update to v…** button appears in the header
- When you're not monitoring, a one-time popup asks whether to install now

Choosing update downloads `TwitchRecorderSetup.exe`, runs it **silently**, and **relaunches** the app. Your `config.yaml` is kept.

No Python install required on the target PC. Streamlink is bundled. FFmpeg is auto-installed on first run if needed (winget, or portable under `%LOCALAPPDATA%\TwitchRecorder\tools`).

### Blocked as a virus? (false positive)

Windows Defender and some browsers often flag **unsigned PyInstaller** builds (same pattern malware uses). This project’s source is public; the detection is almost always a heuristic, not a real Trojan.

From **v1.2.7** the installer ships a **folder build** (not a one-file exe that unpacks to `%TEMP%\_MEI…`). That avoids the common “Failed to load Python DLL” crash when Defender deletes `python312.dll` from Temp mid-launch.

**To download / run anyway on your PC:**

1. Download **`TwitchRecorderSetup.exe`**, then in Explorer: right‑click → **Properties** → check **Unblock** → OK  
2. Windows Security → **Virus & threat protection** → **Protection history** → find the block → **Actions → Allow / Restore**  
3. Optional exclusion (PowerShell as Admin):

```powershell
Add-MpPreference -ExclusionPath "$env:LOCALAPPDATA\TwitchRecorder"
Add-MpPreference -ExclusionPath "$env:USERPROFILE\Downloads"
```

4. Or skip the installer: clone the repo and run `python gui.py` after `pip install -r requirements.txt`

**Report the false positive to Microsoft** (helps everyone, takes a few days):  
https://www.microsoft.com/en-us/wdsi/filesubmission — choose *Software developer* / *Should not be detected (false positive)*, upload the setup from Releases, note it’s open source at https://github.com/g3fps/twitch-recorder

**Real long-term fix:** a paid **code-signing certificate** so SmartScreen/Defender trust the publisher. Without that, each new build can get re-flagged until reputation builds.

Windows may also show a SmartScreen warning — **More info → Run anyway** if you trust the build.

**VirusTotal (v1.0.0 build):** [scan report](https://www.virustotal.com/gui/file/8fcb0209912e87e58b0319feb2132105de79bad47017aa29728b361511d5172c) — **3/70** (false positives; see below)  
v1.0.0 SHA256: `8fcb0209912e87e58b0319feb2132105de79bad47017aa29728b361511d5172c`  
v1.1.0 SHA256: `e9d80dc96bd282bb3eff79bf46fd84436b12d5309a6962094e098f3b328dc0b6`  
v1.2.0 Setup SHA256: `08a084b445657e3c565060143037f368c38e40a822830557d797dee4c6dd9154` (re-upload to VirusTotal after each new build)

### Why VirusTotal shows a few detections

This build is an **unsigned PyInstaller** exe (Python + libraries packed into one file). Many AVs treat that packing style as suspicious even when the app is clean. The source for this project is public in this repo — you can also run `python gui.py` instead of the exe.

| Vendor | Label | Why it’s a false positive |
|--------|--------|---------------------------|
| **Microsoft** | `Trojan:Win32/Wacatac.B!ml` | Machine-learning heuristic. `Wacatac` is commonly raised on new/unsigned PyInstaller and similar packed exes that don’t have an established reputation yet — not a known malware family match for this app. |
| **Bkav Pro** | `W32.Malware.C712EF2E` | Generic packed-executable / heuristic hit (hash-style label). Typical for one-file Python builds with an overlay; not a specific Trojan identification. |
| **Cynet** / **SecureAge** | `Malicious` | Broad behavioral/ML “unknown packed binary” verdict with no concrete family name — same class of false positive as other unsigned distributors. |

The other ~67 engines on that report mark the file **Undetected**. A proper **code-signing certificate** (and rebuilding/signing the exe) is the usual long-term fix for SmartScreen and these heuristics.

## Develop from source

Requirements:

- Python 3.12+
- (Optional) system [Streamlink](https://streamlink.github.io/) / FFmpeg on PATH — or use **Setup tools** in the app

```bat
pip install -r requirements.txt
python gui.py
```

Or double-click `run.bat`.

### Build the exe + installer

```bat
build_exe.bat
```

Then (requires [Inno Setup 6](https://jrsoftware.org/isinfo.php)):

```bat
build_installer.bat
```

Outputs: `TwitchRecorderSetup.exe` (for Releases). The app folder is built under `dist\TwitchRecorder\` and packaged by the installer.
## Usage

1. Add one or more Twitch usernames (or paste a channel URL)
2. Pick an output folder (default: `recordings\`)
3. Click **Options** for Streamlink flags plus extras (title in filename, beep, keep awake, split hours, min free disk)
4. Click **Start monitoring**
5. Leave the window open — when a channel goes live, recording starts automatically
6. When the stream ends, the file is saved and that channel goes back to waiting

Files land in:

```text
recordings\<channel>\<channel>_YYYY-MM-DD_HH-MM-SS_<title>.mp4
```

(`.ts` is kept if remux fails.)

Copy `config.example.yaml` to `config.yaml` to start from defaults (the app also creates settings as you use it).

## Notes

- Ads are skipped when Streamlink can (`--twitch-disable-ads`)
- Reruns are ignored
- Live status shows elapsed time + file size while recording
- PC sleep is blocked while a recording is active (optional)
- Recording pauses if free disk space drops below the minimum
- History is stored in `history.json`; full log in `recorder.log`
- Channel list and settings are saved in `config.yaml`
