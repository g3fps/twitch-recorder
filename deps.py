"""Locate or auto-install streamlink / ffmpeg for distribution."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

ProgressCb = Callable[[str], None]


def subprocess_hidden_kwargs() -> dict:
    """kwargs so Windows does not flash console windows for child processes."""
    if not sys.platform.startswith("win"):
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0  # SW_HIDE
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return {
        "startupinfo": startupinfo,
        "creationflags": flags,
    }


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def tools_dir() -> Path:
    # Prefer a writable user tools dir (exe folder may be Program Files)
    base = Path(os.environ.get("LOCALAPPDATA") or str(app_dir())) / "TwitchRecorder" / "tools"
    base.mkdir(parents=True, exist_ok=True)
    local = app_dir() / "tools"
    if local.is_dir():
        return local
    return base


def _which(name: str) -> Path | None:
    found = shutil.which(name)
    return Path(found) if found else None


def find_streamlink() -> Path | None:
    td = tools_dir()
    candidates = [
        td / "streamlink" / "streamlink.exe",
        td / "streamlink.exe",
        app_dir() / "tools" / "streamlink.exe",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return _which("streamlink")


def find_ffmpeg() -> Path | None:
    td = tools_dir()
    candidates = [
        td / "ffmpeg" / "bin" / "ffmpeg.exe",
        td / "ffmpeg" / "ffmpeg.exe",
        td / "ffmpeg.exe",
        app_dir() / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe",
        app_dir() / "tools" / "ffmpeg.exe",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return _which("ffmpeg")


def streamlink_available() -> bool:
    if find_streamlink():
        return True
    try:
        import streamlink  # noqa: F401

        return True
    except ImportError:
        return False


def ffmpeg_available() -> bool:
    return find_ffmpeg() is not None


def status_summary() -> dict[str, str]:
    sl = find_streamlink()
    ff = find_ffmpeg()
    bundled = False
    try:
        import streamlink  # noqa: F401

        bundled = True
    except ImportError:
        pass

    if sl:
        streamlink_status = f"OK ({sl})"
    elif bundled:
        streamlink_status = "OK (bundled in app)"
    else:
        streamlink_status = "MISSING"

    return {
        "streamlink": streamlink_status,
        "ffmpeg": f"OK ({ff})" if ff else "MISSING",
    }


def _winget_available() -> bool:
    return shutil.which("winget") is not None


def _winget_install(package_id: str, log: ProgressCb) -> bool:
    if not _winget_available():
        return False
    log(f"Installing {package_id} via winget…")
    try:
        result = subprocess.run(
            [
                "winget",
                "install",
                "--id",
                package_id,
                "-e",
                "--accept-package-agreements",
                "--accept-source-agreements",
                "--disable-interactivity",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            check=False,
            **subprocess_hidden_kwargs(),
        )
        # 0 success, -1978335189 already installed (sometimes)
        if result.returncode == 0:
            log(f"winget installed {package_id}")
            return True
        out = (result.stdout or "") + (result.stderr or "")
        if "already installed" in out.lower():
            log(f"{package_id} already installed")
            return True
        log(f"winget failed for {package_id} (code {result.returncode})")
        return False
    except Exception as exc:  # noqa: BLE001
        log(f"winget error: {exc}")
        return False


def _download(url: str, dest: Path, log: ProgressCb) -> None:
    log(f"Downloading {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)

    def _reporthook(block_num: int, block_size: int, total: int) -> None:
        if total <= 0:
            return
        downloaded = block_num * block_size
        pct = min(100, int(downloaded * 100 / total))
        if block_num % 50 == 0:
            log(f"Download {pct}%")

    urllib.request.urlretrieve(url, dest, reporthook=_reporthook)  # noqa: S310


def install_ffmpeg_portable(log: ProgressCb) -> Path | None:
    """Download Gyan essentials build into tools/ffmpeg."""
    td = tools_dir()
    target_root = td / "ffmpeg"
    existing = find_ffmpeg()
    if existing:
        return existing

    url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    with tempfile.TemporaryDirectory() as tmp:
        zpath = Path(tmp) / "ffmpeg.zip"
        try:
            _download(url, zpath, log)
        except Exception as exc:  # noqa: BLE001
            log(f"FFmpeg download failed: {exc}")
            return None
        log("Extracting ffmpeg…")
        try:
            with zipfile.ZipFile(zpath, "r") as zf:
                zf.extractall(tmp)
        except Exception as exc:  # noqa: BLE001
            log(f"FFmpeg extract failed: {exc}")
            return None

        extracted = None
        for root, _dirs, files in os.walk(tmp):
            if "ffmpeg.exe" in files and root.replace("\\", "/").endswith("/bin"):
                extracted = Path(root).parent
                break
            if "ffmpeg.exe" in files and extracted is None:
                extracted = Path(root)
        if not extracted:
            log("Could not find ffmpeg.exe in archive")
            return None

        if target_root.exists():
            shutil.rmtree(target_root, ignore_errors=True)
        shutil.copytree(extracted, target_root)

    found = find_ffmpeg()
    if found:
        log(f"FFmpeg ready: {found}")
    return found


def install_streamlink(log: ProgressCb) -> bool:
    if streamlink_available():
        return True
    if _winget_install("Streamlink.Streamlink", log):
        # Refresh PATH for this process from machine+user env
        _refresh_path()
        return find_streamlink() is not None or streamlink_available()

    # Last resort: pip into current interpreter (dev machines)
    pip = shutil.which("pip") or shutil.which("pip3")
    if pip:
        log("Installing streamlink via pip…")
        try:
            result = subprocess.run(
                [pip, "install", "--user", "streamlink"],
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
                **subprocess_hidden_kwargs(),
            )
            if result.returncode == 0:
                _refresh_path()
                return streamlink_available()
        except Exception as exc:  # noqa: BLE001
            log(f"pip install failed: {exc}")
    log("Could not install streamlink automatically")
    return False


def _refresh_path() -> None:
    """Reload PATH from Windows registry into this process."""
    if not sys.platform.startswith("win"):
        return
    try:
        import winreg

        paths: list[str] = []
        for root, sub in (
            (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
            (winreg.HKEY_CURRENT_USER, r"Environment"),
        ):
            try:
                with winreg.OpenKey(root, sub) as key:
                    val, _ = winreg.QueryValueEx(key, "Path")
                    paths.append(val)
            except OSError:
                pass
        if paths:
            os.environ["PATH"] = ";".join(paths) + ";" + os.environ.get("PATH", "")
    except Exception:  # noqa: BLE001
        pass


def ensure_dependencies(
    log: ProgressCb | None = None,
    *,
    install_ffmpeg: bool = True,
    install_streamlink_tool: bool = True,
) -> tuple[bool, dict[str, str]]:
    """
    Make sure recording tools are available.
    Returns (ok_for_recording, status_dict).
    Recording can work with bundled streamlink even if CLI is missing.
    FFmpeg is optional (remux), but we try to install it.
    """
    log = log or (lambda _m: None)
    log("Checking dependencies…")

    # Streamlink: bundled module OR CLI
    if not streamlink_available():
        if install_streamlink_tool:
            log("Streamlink not found — installing…")
            install_streamlink(log)
        else:
            log("Streamlink missing")
    else:
        log(f"Streamlink: {status_summary()['streamlink']}")

    # FFmpeg: PATH / tools / winget / portable download
    if not ffmpeg_available() and install_ffmpeg:
        log("FFmpeg not found — installing…")
        if _winget_install("Gyan.FFmpeg", log):
            _refresh_path()
        if not ffmpeg_available():
            install_ffmpeg_portable(log)
    elif ffmpeg_available():
        log(f"FFmpeg: {status_summary()['ffmpeg']}")
    else:
        log("FFmpeg missing (remux to MP4 will be skipped)")

    summary = status_summary()
    ok = streamlink_available()
    log("Dependency check done")
    return ok, summary


def streamlink_cmd() -> list[str]:
    """Command prefix to invoke streamlink CLI."""
    exe = find_streamlink()
    if exe:
        return [str(exe)]
    # Frozen / venv: python -m streamlink
    return [sys.executable, "-m", "streamlink"]


def ffmpeg_cmd() -> list[str]:
    exe = find_ffmpeg()
    if exe:
        return [str(exe)]
    return ["ffmpeg"]


def target_exe_path() -> Path:
    """Path to the app executable (frozen) or python running gui.py."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return app_dir() / "TwitchRecorder.exe"


def start_menu_shortcut_path() -> Path:
    programs = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    return programs / "Twitch Auto Recorder.lnk"


def start_menu_shortcut_exists() -> bool:
    return start_menu_shortcut_path().is_file()


def create_start_menu_shortcut(target: Path | None = None) -> Path:
    """Create/update a Start Menu shortcut. Returns the .lnk path."""
    if not sys.platform.startswith("win"):
        raise OSError("Start Menu shortcuts are only supported on Windows")

    exe = (target or target_exe_path()).resolve()
    if not exe.is_file():
        raise FileNotFoundError(f"App executable not found: {exe}")

    shortcut = start_menu_shortcut_path()
    shortcut.parent.mkdir(parents=True, exist_ok=True)
    workdir = str(exe.parent)

    # Escape for PowerShell single-quoted strings
    def _ps_quote(value: str) -> str:
        return value.replace("'", "''")

    script = (
        f"$w = New-Object -ComObject WScript.Shell; "
        f"$s = $w.CreateShortcut('{_ps_quote(str(shortcut))}'); "
        f"$s.TargetPath = '{_ps_quote(str(exe))}'; "
        f"$s.WorkingDirectory = '{_ps_quote(workdir)}'; "
        f"$s.Description = 'Twitch Auto Recorder'; "
        f"$s.Save()"
    )
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-WindowStyle",
            "Hidden",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
        **subprocess_hidden_kwargs(),
    )
    if result.returncode != 0 or not shortcut.is_file():
        err = (result.stderr or result.stdout or "unknown error").strip()
        raise RuntimeError(f"Failed to create Start Menu shortcut: {err}")
    return shortcut


def prompt_marker_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or str(app_dir())) / "TwitchRecorder"
    base.mkdir(parents=True, exist_ok=True)
    return base / "start_menu_prompted"


def should_ask_start_menu() -> bool:
    return sys.platform.startswith("win") and not prompt_marker_path().is_file()


def mark_start_menu_prompted() -> None:
    prompt_marker_path().write_text("1\n", encoding="utf-8")
