# Twitch Auto Recorder

Desktop app that waits for any Twitch streamers you add to go live, then records each stream to a local folder.

## Quick start (distributed `.exe`)

1. Download **`TwitchRecorder.exe`** from [Releases](https://github.com/g3fps/twitch-recorder/releases)
2. Double-click it
3. If FFmpeg is missing, click **Yes** when asked to auto-install (needs internet once)
4. Add streamer usernames → **Start monitoring**

No Python install required on the target PC. Streamlink is bundled in the exe. FFmpeg is auto-installed on first run if needed (uses winget, or a portable download to `%LOCALAPPDATA%\TwitchRecorder\tools`).

Windows may show a SmartScreen warning for an unsigned exe — choose **More info → Run anyway** if you trust the build.

**VirusTotal (v1.0.0):** [scan report](https://www.virustotal.com/gui/file/8fcb0209912e87e58b0319feb2132105de79bad47017aa29728b361511d5172c) — **3/70** (false positives; see below)  
SHA256: `8fcb0209912e87e58b0319feb2132105de79bad47017aa29728b361511d5172c`

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

### Build the exe

```bat
build_exe.bat
```

Output: `TwitchRecorder.exe` in the project folder (and `dist\`).

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
