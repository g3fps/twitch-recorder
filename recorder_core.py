"""Multi-channel Twitch wait-and-record workers backed by Streamlink."""

from __future__ import annotations

import json
import logging
import re
import shlex
import shutil
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from deps import (
    ffmpeg_cmd,
    find_streamlink,
    streamlink_available,
    streamlink_cmd,
    subprocess_hidden_kwargs,
)

StatusCallback = Callable[[str, str, str], None]
LogCallback = Callable[[str], None]
EventCallback = Callable[[str, dict], None]


def find_streamlink_safe() -> Path | None:
    return find_streamlink()


def _cli_module_works() -> bool:
    """True when `python -m streamlink` is usable (dev installs, not frozen GUI exe)."""
    import sys

    if getattr(sys, "frozen", False):
        return False
    return streamlink_available()
LogCallback = Callable[[str], None]
EventCallback = Callable[[str, dict], None]

def _app_dir() -> Path:
    import sys

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = _app_dir()
HISTORY_PATH = APP_DIR / "history.json"
LOG_PATH = APP_DIR / "recorder.log"

_file_logger = logging.getLogger("twitch_recorder")
if not _file_logger.handlers:
    _file_logger.setLevel(logging.INFO)
    _handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    _handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    _file_logger.addHandler(_handler)


def _sanitize_title(title: str, max_len: int = 60) -> str:
    cleaned = re.sub(r"[^\w\s\-]+", "", title, flags=re.UNICODE)
    cleaned = re.sub(r"\s+", "_", cleaned.strip())
    return cleaned[:max_len].strip("_") or "stream"


def append_history(entry: dict) -> None:
    history: list = []
    if HISTORY_PATH.exists():
        try:
            history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
            if not isinstance(history, list):
                history = []
        except (OSError, json.JSONDecodeError):
            history = []
    history.insert(0, entry)
    history = history[:100]
    try:
        HISTORY_PATH.write_text(json.dumps(history, indent=2), encoding="utf-8")
    except OSError:
        pass


def load_history(limit: int = 30) -> list[dict]:
    if not HISTORY_PATH.exists():
        return []
    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data[:limit]
    except (OSError, json.JSONDecodeError):
        pass
    return []


@dataclass
class RecorderConfig:
    channels: list[str] = field(default_factory=list)
    output_dir: str = "recordings"
    quality: str = "best"
    poll_interval: int = 30
    disable_ads: bool = True
    disable_reruns: bool = True
    low_latency: bool = False
    remux_to_mp4: bool = True
    retry_streams: int = 5
    retry_max: int = 6
    retry_open: int = 3
    extra_args: str = ""
    include_title: bool = True
    beep_on_live: bool = True
    keep_awake: bool = True
    split_hours: float = 0.0  # 0 = disabled
    min_free_gb: float = 5.0


class ChannelWorker(threading.Thread):
    def __init__(
        self,
        channel: str,
        config: RecorderConfig,
        stop_event: threading.Event,
        on_status: StatusCallback,
        on_log: LogCallback,
        on_event: EventCallback | None = None,
    ) -> None:
        super().__init__(daemon=True, name=f"recorder-{channel}")
        self.channel = channel.lower().strip()
        self.config = config
        self.stop_event = stop_event
        self.on_status = on_status
        self.on_log = on_log
        self.on_event = on_event or (lambda *_: None)
        self._proc: subprocess.Popen[str] | None = None
        self._proc_lock = threading.Lock()
        self._stats_stop = threading.Event()
        self._stream_meta: dict = {}

    def run(self) -> None:
        self._log(f"[{self.channel}] Monitoring started")
        while not self.stop_event.is_set():
            if not self._disk_ok():
                self.on_status(self.channel, "Waiting", "Low disk space — paused")
                self._interruptible_sleep(60)
                continue

            self.on_status(self.channel, "Waiting", "Checking for live stream…")
            if not self._wait_until_live():
                break

            if self.stop_event.is_set():
                break

            # One live session may produce multiple files if split_hours is set
            while not self.stop_event.is_set() and self._is_live():
                if not self._disk_ok():
                    self._log(f"[{self.channel}] Stopping segment — low disk space")
                    break

                out_path = self._output_path(self._stream_meta.get("title", ""))
                self.on_status(self.channel, "Recording", out_path.name)
                self._log(f"[{self.channel}] LIVE — recording to {out_path}")
                title = self._stream_meta.get("title") or ""
                if title:
                    self._log(f"[{self.channel}] Title: {title}")
                self.on_event(
                    "live",
                    {"channel": self.channel, "title": title, "path": str(out_path)},
                )

                started = time.time()
                ok = self._record_to(out_path)
                elapsed = time.time() - started

                if self.stop_event.is_set():
                    self.on_status(self.channel, "Idle", "Stopped")
                    self.on_event("idle", {"channel": self.channel})
                    return

                if ok and out_path.exists() and out_path.stat().st_size > 0:
                    if self.config.remux_to_mp4:
                        mp4 = self._remux(out_path)
                        final = mp4 if mp4 else out_path
                    else:
                        final = out_path
                    size_mb = final.stat().st_size / (1024 * 1024)
                    self._log(f"[{self.channel}] Saved {final} ({size_mb:.1f} MB)")
                    self.on_status(self.channel, "Waiting", f"Last: {final.name}")
                    entry = {
                        "channel": self.channel,
                        "path": str(final),
                        "size_mb": round(size_mb, 1),
                        "seconds": int(elapsed),
                        "title": title,
                        "saved_at": datetime.now().isoformat(timespec="seconds"),
                    }
                    append_history(entry)
                    self.on_event("saved", entry)
                else:
                    self._log(f"[{self.channel}] Recording ended with no usable file")
                    self.on_status(self.channel, "Waiting", "Waiting for next live…")

                # If stream still live and split enabled, start next segment immediately
                if self.config.split_hours and self.config.split_hours > 0 and self._is_live():
                    self._log(f"[{self.channel}] Starting next segment…")
                    continue
                break

            self._interruptible_sleep(5)

        self.on_status(self.channel, "Idle", "Stopped")
        self._log(f"[{self.channel}] Monitoring stopped")
        self.on_event("idle", {"channel": self.channel})

    def _log(self, message: str) -> None:
        self.on_log(message)
        _file_logger.info(message)

    def stop_process(self) -> None:
        self._stats_stop.set()
        with self._proc_lock:
            proc = self._proc
        if proc and proc.poll() is None:
            self._log(f"[{self.channel}] Stopping streamlink…")
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()

    def _disk_ok(self) -> bool:
        min_gb = float(self.config.min_free_gb or 0)
        if min_gb <= 0:
            return True
        try:
            base = Path(self.config.output_dir)
            if not base.is_absolute():
                base = APP_DIR / base
            base.mkdir(parents=True, exist_ok=True)
            usage = shutil.disk_usage(base)
            free_gb = usage.free / (1024**3)
            if free_gb < min_gb:
                self._log(
                    f"[{self.channel}] Low disk space: {free_gb:.1f} GB free "
                    f"(need {min_gb:.1f} GB)"
                )
                return False
        except OSError as exc:
            self._log(f"[{self.channel}] Disk check failed: {exc}")
        return True

    def _wait_until_live(self) -> bool:
        interval = max(5, int(self.config.poll_interval))
        while not self.stop_event.is_set():
            try:
                meta = self._fetch_stream_info()
                if meta.get("live"):
                    self._stream_meta = meta
                    return True
            except Exception as exc:  # noqa: BLE001
                self._log(f"[{self.channel}] Live check error: {exc}")
            self.on_status(self.channel, "Waiting", f"Offline — retry in {interval}s")
            self._interruptible_sleep(interval)
        return False

    def _is_live(self) -> bool:
        try:
            meta = self._fetch_stream_info()
            if meta.get("live"):
                self._stream_meta = meta
                return True
        except Exception:  # noqa: BLE001
            return False
        return False

    def _fetch_stream_info(self) -> dict:
        url = f"https://www.twitch.tv/{self.channel}"

        # Prefer in-process Streamlink API — no console windows while polling
        try:
            from streamlink import Streamlink

            session = Streamlink()
            if self.config.disable_reruns:
                session.set_option("twitch-disable-reruns", True)
            streams = session.streams(url)
            return {
                "live": bool(streams),
                "title": "",
                "author": "",
                "category": "",
            }
        except Exception:  # noqa: BLE001
            pass

        # CLI fallback (hidden console)
        if find_streamlink_safe():
            result = subprocess.run(
                [*streamlink_cmd(), "--json", url],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
                **subprocess_hidden_kwargs(),
            )
            if result.returncode == 0:
                try:
                    data = json.loads(result.stdout or "{}")
                    streams = data.get("streams") or {}
                    metadata = data.get("metadata") or {}
                    return {
                        "live": bool(streams),
                        "title": str(metadata.get("title") or ""),
                        "author": str(metadata.get("author") or ""),
                        "category": str(metadata.get("category") or ""),
                    }
                except json.JSONDecodeError:
                    pass
        return {"live": False}

    def _build_streamlink_cmd(self, out_path: Path, url: str) -> list[str]:
        cmd = [*streamlink_cmd()]
        if self.config.disable_ads:
            cmd.append("--twitch-disable-ads")
        if self.config.disable_reruns:
            cmd.append("--twitch-disable-reruns")
        if self.config.low_latency:
            cmd.append("--twitch-low-latency")

        retry_streams = max(0, int(self.config.retry_streams))
        retry_max = max(0, int(self.config.retry_max))
        retry_open = max(1, int(self.config.retry_open))
        if retry_streams > 0:
            cmd.extend(["--retry-streams", str(retry_streams)])
            cmd.extend(["--retry-max", str(retry_max)])
        cmd.extend(["--retry-open", str(retry_open)])

        # Soft split: stop this streamlink after N hours; outer loop starts a new file
        if self.config.split_hours and self.config.split_hours > 0:
            seconds = int(float(self.config.split_hours) * 3600)
            if seconds > 0:
                cmd.extend(["--hls-duration", str(seconds)])

        extra = (self.config.extra_args or "").strip()
        if extra:
            try:
                cmd.extend(shlex.split(extra, posix=False))
            except ValueError as exc:
                self._log(f"[{self.channel}] Ignoring bad extra args: {exc}")

        cmd.extend(["-o", str(out_path), url, self.config.quality or "best"])
        return cmd

    def _record_to(self, out_path: Path) -> bool:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        url = f"https://www.twitch.tv/{self.channel}"

        # Prefer in-process API so recording never opens a console window
        try:
            import streamlink  # noqa: F401

            return self._record_with_api(out_path, url)
        except ImportError:
            pass

        if find_streamlink_safe() or _cli_module_works():
            return self._record_with_cli(out_path, url)
        self._log("[error] streamlink not available — use Setup tools in the app")
        return False

    def _record_with_cli(self, out_path: Path, url: str) -> bool:
        cmd = self._build_streamlink_cmd(out_path, url)
        self._log(f"[{self.channel}] $ {' '.join(cmd)}")
        self._stats_stop.clear()
        stats_thread = threading.Thread(
            target=self._stats_loop, args=(out_path, time.time()), daemon=True
        )
        stats_thread.start()
        try:
            with self._proc_lock:
                self._proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    **subprocess_hidden_kwargs(),
                )
                proc = self._proc

            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.strip()
                if line:
                    self._log(f"[{self.channel}] {line}")
                if self.stop_event.is_set():
                    self.stop_process()
                    break

            code = proc.wait()
            return code == 0 or (out_path.exists() and out_path.stat().st_size > 0)
        except FileNotFoundError:
            self._log("[error] streamlink executable not found — trying bundled API")
            return self._record_with_api(out_path, url)
        except Exception as exc:  # noqa: BLE001
            self._log(f"[{self.channel}] Record error: {exc}")
            return False
        finally:
            self._stats_stop.set()
            with self._proc_lock:
                self._proc = None
    def _record_with_api(self, out_path: Path, url: str) -> bool:
        try:
            from streamlink import Streamlink
        except ImportError:
            self._log("[error] streamlink package not installed")
            return False

        self._log(f"[{self.channel}] Recording via bundled Streamlink API")
        session = Streamlink()
        if self.config.disable_ads:
            session.set_option("twitch-disable-ads", True)
        if self.config.disable_reruns:
            session.set_option("twitch-disable-reruns", True)
        if self.config.low_latency:
            session.set_option("twitch-low-latency", True)

        split_deadline = None
        if self.config.split_hours and self.config.split_hours > 0:
            split_deadline = time.time() + float(self.config.split_hours) * 3600

        self._stats_stop.clear()
        stats_thread = threading.Thread(
            target=self._stats_loop, args=(out_path, time.time()), daemon=True
        )
        stats_thread.start()
        try:
            streams = session.streams(url)
            if not streams:
                self._log(f"[{self.channel}] No streams available")
                return False
            quality = self.config.quality or "best"
            stream = streams.get(quality) or streams.get("best") or next(iter(streams.values()))
            fd = stream.open()
            with out_path.open("wb") as out:
                while not self.stop_event.is_set():
                    if split_deadline and time.time() >= split_deadline:
                        self._log(f"[{self.channel}] Split duration reached")
                        break
                    data = fd.read(64 * 1024)
                    if not data:
                        break
                    out.write(data)
            try:
                fd.close()
            except Exception:  # noqa: BLE001
                pass
            return out_path.exists() and out_path.stat().st_size > 0
        except Exception as exc:  # noqa: BLE001
            self._log(f"[{self.channel}] API record error: {exc}")
            return False
        finally:
            self._stats_stop.set()

    def _stats_loop(self, out_path: Path, started: float) -> None:
        while not self._stats_stop.is_set() and not self.stop_event.is_set():
            size_mb = 0.0
            try:
                if out_path.exists():
                    size_mb = out_path.stat().st_size / (1024 * 1024)
            except OSError:
                pass
            elapsed = int(time.time() - started)
            hh, rem = divmod(elapsed, 3600)
            mm, ss = divmod(rem, 60)
            clock = f"{hh:02d}:{mm:02d}:{ss:02d}"
            self.on_status(
                self.channel,
                "Recording",
                f"{out_path.name}  ·  {clock}  ·  {size_mb:.1f} MB",
            )
            self._stats_stop.wait(2.0)

    def _remux(self, ts_path: Path) -> Path | None:
        ff = ffmpeg_cmd()
        if not shutil.which(ff[0]) and not Path(ff[0]).is_file():
            self._log(f"[{self.channel}] ffmpeg not found — keeping .ts")
            return None

        mp4_path = ts_path.with_suffix(".mp4")
        self.on_status(self.channel, "Remuxing", mp4_path.name)
        self._log(f"[{self.channel}] Remuxing to {mp4_path.name}")
        try:
            result = subprocess.run(
                [
                    *ff,
                    "-y",
                    "-i",
                    str(ts_path),
                    "-c",
                    "copy",
                    "-movflags",
                    "+faststart",
                    str(mp4_path),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3600,
                check=False,
                **subprocess_hidden_kwargs(),
            )
            if result.returncode == 0 and mp4_path.exists() and mp4_path.stat().st_size > 0:
                try:
                    ts_path.unlink(missing_ok=True)
                except OSError:
                    pass
                return mp4_path
            self._log(f"[{self.channel}] Remux failed — keeping .ts")
            return None
        except Exception as exc:  # noqa: BLE001
            self._log(f"[{self.channel}] Remux error: {exc}")
            return None

    def _output_path(self, title: str = "") -> Path:
        base = Path(self.config.output_dir)
        if not base.is_absolute():
            base = APP_DIR / base
        channel_dir = base / self.channel
        channel_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        parts = [self.channel, stamp]
        if self.config.include_title and title:
            parts.append(_sanitize_title(title))
        return channel_dir / ("_".join(parts) + ".ts")

    def _interruptible_sleep(self, seconds: float) -> None:
        end = time.time() + seconds
        while not self.stop_event.is_set() and time.time() < end:
            time.sleep(min(0.25, end - time.time()))


class KeepAwake:
    """Prevent Windows sleep while recordings are active."""

    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001

    def __init__(self) -> None:
        self._active = False

    def set(self, enabled: bool) -> None:
        if not sys_platform_is_windows():
            return
        try:
            import ctypes

            if enabled and not self._active:
                ctypes.windll.kernel32.SetThreadExecutionState(  # type: ignore[attr-defined]
                    self.ES_CONTINUOUS | self.ES_SYSTEM_REQUIRED
                )
                self._active = True
            elif not enabled and self._active:
                ctypes.windll.kernel32.SetThreadExecutionState(self.ES_CONTINUOUS)  # type: ignore[attr-defined]
                self._active = False
        except Exception:  # noqa: BLE001
            pass


def sys_platform_is_windows() -> bool:
    import sys

    return sys.platform.startswith("win")


class RecorderManager:
    def __init__(
        self,
        on_status: StatusCallback | None = None,
        on_log: LogCallback | None = None,
        on_event: EventCallback | None = None,
    ) -> None:
        self.on_status = on_status or (lambda *_: None)
        self.on_log = on_log or (lambda *_: None)
        self.on_event = on_event or (lambda *_: None)
        self._stop_event = threading.Event()
        self._workers: dict[str, ChannelWorker] = {}
        self._lock = threading.Lock()
        self._keep_awake = KeepAwake()
        self._recording_channels: set[str] = set()
        self._want_keep_awake = True

    @property
    def running(self) -> bool:
        with self._lock:
            return any(w.is_alive() for w in self._workers.values())

    def start(self, config: RecorderConfig) -> None:
        channels = [c.strip().lower() for c in config.channels if c and c.strip()]
        seen: set[str] = set()
        unique: list[str] = []
        for c in channels:
            if c not in seen:
                seen.add(c)
                unique.append(c)

        if not unique:
            self.on_log("Add at least one channel before starting")
            return

        self.stop()
        self._stop_event = threading.Event()
        self._want_keep_awake = bool(config.keep_awake)
        self._recording_channels.clear()
        cfg = RecorderConfig(**{**asdict(config), "channels": unique, "quality": config.quality or "best"})

        def _event(kind: str, payload: dict) -> None:
            channel = str(payload.get("channel") or "")
            if kind == "live" and channel:
                self._recording_channels.add(channel)
                self._refresh_awake()
            elif kind in {"saved", "idle"} and channel:
                if kind == "idle":
                    self._recording_channels.discard(channel)
                self._refresh_awake()
            self.on_event(kind, payload)

        with self._lock:
            self._workers.clear()
            for channel in unique:
                worker = ChannelWorker(
                    channel=channel,
                    config=cfg,
                    stop_event=self._stop_event,
                    on_status=self.on_status,
                    on_log=self.on_log,
                    on_event=_event,
                )
                self._workers[channel] = worker
                worker.start()

        self.on_log(f"Started monitoring: {', '.join(unique)}")

    def _refresh_awake(self) -> None:
        self._keep_awake.set(self._want_keep_awake and bool(self._recording_channels))

    def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            workers = list(self._workers.values())
        for worker in workers:
            worker.stop_process()
        for worker in workers:
            worker.join(timeout=12)
        with self._lock:
            self._workers.clear()
        self._recording_channels.clear()
        self._keep_awake.set(False)
        self.on_log("All monitors stopped")
