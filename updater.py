"""Check GitHub Releases and download the Windows installer for in-place updates."""

from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from version import (
    APP_VERSION,
    GITHUB_OWNER,
    GITHUB_REPO,
    SETUP_ASSET_NAME,
)

ProgressCb = Callable[[str], None]

_USER_AGENT = f"TwitchRecorder/{APP_VERSION} (+https://github.com/{GITHUB_OWNER}/{GITHUB_REPO})"


@dataclass(frozen=True)
class ReleaseInfo:
    tag: str
    version: str
    setup_url: str
    html_url: str
    notes: str


def parse_version(text: str) -> tuple[int, ...]:
    cleaned = text.strip().lstrip("vV")
    parts = re.findall(r"\d+", cleaned)
    if not parts:
        return (0,)
    return tuple(int(p) for p in parts)


def is_newer(candidate: str, current: str = APP_VERSION) -> bool:
    a = parse_version(candidate)
    b = parse_version(current)
    # Pad so (1,2) < (1,2,3)
    n = max(len(a), len(b))
    a = a + (0,) * (n - len(a))
    b = b + (0,) * (n - len(b))
    return a > b


def fetch_latest_release(timeout: float = 20.0) -> ReleaseInfo:
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": _USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    tag = str(data.get("tag_name") or "").strip()
    version = tag.lstrip("vV") or tag
    html_url = str(data.get("html_url") or "")
    notes = str(data.get("body") or "")
    setup_url = ""
    for asset in data.get("assets") or []:
        name = str(asset.get("name") or "")
        if name.lower() == SETUP_ASSET_NAME.lower():
            setup_url = str(asset.get("browser_download_url") or "")
            break
    if not tag:
        raise RuntimeError("Latest release has no tag")
    if not setup_url:
        raise RuntimeError(
            f"Latest release {tag} has no {SETUP_ASSET_NAME} asset — "
            "download manually from GitHub Releases."
        )
    return ReleaseInfo(
        tag=tag,
        version=version,
        setup_url=setup_url,
        html_url=html_url,
        notes=notes,
    )


def download_installer(
    url: str,
    dest: Path | None = None,
    log: ProgressCb | None = None,
    timeout: float = 120.0,
) -> Path:
    if dest is None:
        dest = Path(tempfile.gettempdir()) / SETUP_ASSET_NAME
    if log:
        log(f"Downloading update to {dest}…")
    req = urllib.request.Request(
        url,
        headers={"User-Agent": _USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        total = resp.headers.get("Content-Length")
        total_n = int(total) if total and total.isdigit() else 0
        chunk = 256 * 1024
        got = 0
        with dest.open("wb") as out:
            while True:
                block = resp.read(chunk)
                if not block:
                    break
                out.write(block)
                got += len(block)
                if log and total_n:
                    pct = min(100, int(got * 100 / total_n))
                    if got == len(block) or got >= total_n or pct % 10 == 0:
                        log(f"Download {pct}% ({got // (1024 * 1024)} MB)")
    if log:
        log(f"Download complete: {dest}")
    return dest


def launch_installer(setup_path: Path) -> None:
    path = str(setup_path.resolve())
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    # Normal UI installer — user must approve; do not use silent flags by default
    os.startfile(path)  # type: ignore[attr-defined]
