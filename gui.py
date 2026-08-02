"""Twitch auto-recorder desktop GUI."""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
import winsound
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
import yaml

from deps import (
    create_start_menu_shortcut,
    ensure_dependencies,
    mark_start_menu_prompted,
    should_ask_start_menu,
    start_menu_shortcut_exists,
    status_summary,
)
from recorder_core import RecorderConfig, RecorderManager, load_history
from updater import (
    download_installer,
    fetch_latest_release,
    is_newer,
    launch_silent_update,
)
from version import APP_VERSION, GITHUB_RELEASES_URL


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = _app_dir()
CONFIG_PATH = APP_DIR / "config.yaml"
DEFAULT_OUTPUT = str(APP_DIR / "recordings")

STATUS_COLORS = {
    "Idle": "#8a8a8a",
    "Waiting": "#d4a017",
    "Recording": "#2ecc71",
    "Remuxing": "#3498db",
}


def _as_bool(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_config() -> dict:
    data: dict = {}
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    return {
        "channels": list(data.get("channels") or []),
        "output_dir": data.get("output_dir") or DEFAULT_OUTPUT,
        "quality": data.get("quality") or "best",
        "poll_interval": _as_int(data.get("poll_interval"), 30),
        "disable_ads": _as_bool(data.get("disable_ads"), True),
        "disable_reruns": _as_bool(data.get("disable_reruns"), True),
        "low_latency": _as_bool(data.get("low_latency"), False),
        "remux_to_mp4": _as_bool(data.get("remux_to_mp4"), True),
        "retry_streams": _as_int(data.get("retry_streams"), 5),
        "retry_max": _as_int(data.get("retry_max"), 6),
        "retry_open": _as_int(data.get("retry_open"), 3),
        "extra_args": str(data.get("extra_args") or ""),
        "include_title": _as_bool(data.get("include_title"), True),
        "beep_on_live": _as_bool(data.get("beep_on_live"), True),
        "keep_awake": _as_bool(data.get("keep_awake"), True),
        "always_on_top": _as_bool(data.get("always_on_top"), False),
        "split_hours": _as_float(data.get("split_hours"), 0.0),
        "min_free_gb": _as_float(data.get("min_free_gb"), 5.0),
    }


def save_config(data: dict) -> None:
    payload = {
        "channels": list(data.get("channels") or []),
        "output_dir": data.get("output_dir") or DEFAULT_OUTPUT,
        "quality": data.get("quality") or "best",
        "poll_interval": _as_int(data.get("poll_interval"), 30),
        "disable_ads": _as_bool(data.get("disable_ads"), True),
        "disable_reruns": _as_bool(data.get("disable_reruns"), True),
        "low_latency": _as_bool(data.get("low_latency"), False),
        "remux_to_mp4": _as_bool(data.get("remux_to_mp4"), True),
        "retry_streams": _as_int(data.get("retry_streams"), 5),
        "retry_max": _as_int(data.get("retry_max"), 6),
        "retry_open": _as_int(data.get("retry_open"), 3),
        "extra_args": str(data.get("extra_args") or ""),
        "include_title": _as_bool(data.get("include_title"), True),
        "beep_on_live": _as_bool(data.get("beep_on_live"), True),
        "keep_awake": _as_bool(data.get("keep_awake"), True),
        "always_on_top": _as_bool(data.get("always_on_top"), False),
        "split_hours": _as_float(data.get("split_hours"), 0.0),
        "min_free_gb": _as_float(data.get("min_free_gb"), 5.0),
    }
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, sort_keys=False)


def open_path(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Twitch Auto Recorder")
        self.geometry("960x820")
        self.minsize(800, 660)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.cfg = load_config()
        self.channels: list[str] = [c.lower() for c in self.cfg["channels"]]
        self.row_widgets: dict[str, dict] = {}
        self.event_queue: queue.Queue = queue.Queue()
        self.manager = RecorderManager(
            on_status=self._queue_status,
            on_log=self._queue_log,
            on_event=self._queue_event,
        )
        self._monitoring = False
        self._options_visible = False
        self._pending_update = None
        self._update_popup_shown = False

        self._build_ui()
        self._refresh_channel_rows()
        self._refresh_history()
        self._apply_always_on_top()
        self.after(100, self._drain_queue)
        self.after(2000, self._tick_disk)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._append_log(f"Ready (v{APP_VERSION}). Add a Twitch channel, then Start monitoring.")
        self.after(300, self._check_deps_on_startup)
        self.after(600, self._maybe_ask_start_menu)
        self.after(2500, self._quiet_update_check)
        if "--start" in sys.argv and self.channels:
            self.after(800, self._start)

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        header_row = ctk.CTkFrame(self, fg_color="transparent")
        header_row.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 4))
        header_row.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header_row,
            text="Twitch Auto Recorder",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).grid(row=0, column=0, sticky="w")

        self.update_btn = ctk.CTkButton(
            header_row,
            text="Update available",
            width=150,
            fg_color="#2d6a4f",
            hover_color="#1b4332",
            command=self._install_pending_update,
        )
        # Only shown when a newer release exists
        self.update_btn.grid(row=0, column=1, sticky="e", padx=(8, 8))
        self.update_btn.grid_remove()

        self.version_label = ctk.CTkLabel(
            header_row, text=f"v{APP_VERSION}", text_color="#888888"
        )
        self.version_label.grid(row=0, column=2, sticky="e", padx=(8, 12))

        self.disk_label = ctk.CTkLabel(header_row, text="", text_color="#888888")
        self.disk_label.grid(row=0, column=3, sticky="e")

        controls = ctk.CTkFrame(self)
        controls.grid(row=1, column=0, sticky="ew", padx=16, pady=8)
        controls.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(controls, text="Channel").grid(row=0, column=0, padx=(12, 6), pady=10)
        self.channel_entry = ctk.CTkEntry(controls, placeholder_text="twitch username")
        self.channel_entry.grid(row=0, column=1, sticky="ew", padx=6, pady=10)
        self.channel_entry.bind("<Return>", lambda _e: self._add_channel())

        self.add_btn = ctk.CTkButton(controls, text="Add", width=80, command=self._add_channel)
        self.add_btn.grid(row=0, column=2, padx=6, pady=10)

        self.remove_btn = ctk.CTkButton(
            controls, text="Remove selected", width=130, command=self._remove_selected
        )
        self.remove_btn.grid(row=0, column=3, padx=(6, 12), pady=10)

        settings = ctk.CTkFrame(self)
        settings.grid(row=2, column=0, sticky="ew", padx=16, pady=4)
        settings.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(settings, text="Output").grid(row=0, column=0, padx=(12, 6), pady=10)
        self.output_entry = ctk.CTkEntry(settings)
        self.output_entry.insert(0, self.cfg["output_dir"])
        self.output_entry.grid(row=0, column=1, sticky="ew", padx=6, pady=10)

        self.browse_btn = ctk.CTkButton(settings, text="Browse", width=90, command=self._browse)
        self.browse_btn.grid(row=0, column=2, padx=6, pady=10)

        ctk.CTkLabel(settings, text="Quality").grid(row=0, column=3, padx=(12, 6), pady=10)
        self.quality_menu = ctk.CTkOptionMenu(
            settings,
            values=["best", "1080p60", "1080p", "720p60", "720p", "480p", "worst"],
            width=110,
            command=lambda _v: self._persist(),
        )
        self.quality_menu.set(self.cfg.get("quality") or "best")
        self.quality_menu.grid(row=0, column=4, padx=6, pady=10)

        self.options_toggle = ctk.CTkButton(
            settings, text="Options ▾", width=100, command=self._toggle_options
        )
        self.options_toggle.grid(row=0, column=5, padx=(6, 12), pady=10)

        self.options = ctk.CTkFrame(self)
        self.options.grid(row=3, column=0, sticky="ew", padx=16, pady=4)
        for col in (1, 3, 5):
            self.options.grid_columnconfigure(col, weight=1)
        self.options.grid_remove()

        self.disable_ads_var = ctk.BooleanVar(value=self.cfg["disable_ads"])
        self.disable_reruns_var = ctk.BooleanVar(value=self.cfg["disable_reruns"])
        self.low_latency_var = ctk.BooleanVar(value=self.cfg["low_latency"])
        self.remux_var = ctk.BooleanVar(value=self.cfg["remux_to_mp4"])
        self.include_title_var = ctk.BooleanVar(value=self.cfg["include_title"])
        self.beep_var = ctk.BooleanVar(value=self.cfg["beep_on_live"])
        self.keep_awake_var = ctk.BooleanVar(value=self.cfg["keep_awake"])
        self.always_on_top_var = ctk.BooleanVar(value=self.cfg["always_on_top"])

        checks = [
            (0, 0, "Disable ads (--twitch-disable-ads)", self.disable_ads_var),
            (0, 2, "Disable reruns (--twitch-disable-reruns)", self.disable_reruns_var),
            (1, 0, "Low latency (--twitch-low-latency)", self.low_latency_var),
            (1, 2, "Remux to MP4 after stream (ffmpeg)", self.remux_var),
            (2, 0, "Include stream title in filename", self.include_title_var),
            (2, 2, "Beep when a channel goes live", self.beep_var),
            (3, 0, "Keep PC awake while recording", self.keep_awake_var),
            (3, 2, "Always on top", self.always_on_top_var, self._on_always_on_top),
        ]
        for item in checks:
            row, col, text, var = item[0], item[1], item[2], item[3]
            cmd = item[4] if len(item) > 4 else self._persist
            ctk.CTkCheckBox(self.options, text=text, variable=var, command=cmd).grid(
                row=row, column=col, columnspan=2, sticky="w", padx=12, pady=4
            )

        ctk.CTkLabel(self.options, text="Poll (s)").grid(
            row=4, column=0, sticky="w", padx=(12, 4), pady=8
        )
        self.poll_entry = ctk.CTkEntry(self.options, width=70)
        self.poll_entry.insert(0, str(self.cfg["poll_interval"]))
        self.poll_entry.grid(row=4, column=1, sticky="w", padx=4, pady=8)
        self.poll_entry.bind("<FocusOut>", lambda _e: self._persist())

        ctk.CTkLabel(self.options, text="--retry-streams").grid(
            row=4, column=2, sticky="w", padx=(12, 4), pady=8
        )
        self.retry_streams_entry = ctk.CTkEntry(self.options, width=70)
        self.retry_streams_entry.insert(0, str(self.cfg["retry_streams"]))
        self.retry_streams_entry.grid(row=4, column=3, sticky="w", padx=4, pady=8)
        self.retry_streams_entry.bind("<FocusOut>", lambda _e: self._persist())

        ctk.CTkLabel(self.options, text="--retry-max").grid(
            row=4, column=4, sticky="w", padx=(12, 4), pady=8
        )
        self.retry_max_entry = ctk.CTkEntry(self.options, width=70)
        self.retry_max_entry.insert(0, str(self.cfg["retry_max"]))
        self.retry_max_entry.grid(row=4, column=5, sticky="w", padx=(4, 12), pady=8)
        self.retry_max_entry.bind("<FocusOut>", lambda _e: self._persist())

        ctk.CTkLabel(self.options, text="--retry-open").grid(
            row=5, column=0, sticky="w", padx=(12, 4), pady=8
        )
        self.retry_open_entry = ctk.CTkEntry(self.options, width=70)
        self.retry_open_entry.insert(0, str(self.cfg["retry_open"]))
        self.retry_open_entry.grid(row=5, column=1, sticky="w", padx=4, pady=8)
        self.retry_open_entry.bind("<FocusOut>", lambda _e: self._persist())

        ctk.CTkLabel(self.options, text="Split hours (0=off)").grid(
            row=5, column=2, sticky="w", padx=(12, 4), pady=8
        )
        self.split_entry = ctk.CTkEntry(self.options, width=70)
        self.split_entry.insert(0, str(self.cfg["split_hours"]))
        self.split_entry.grid(row=5, column=3, sticky="w", padx=4, pady=8)
        self.split_entry.bind("<FocusOut>", lambda _e: self._persist())

        ctk.CTkLabel(self.options, text="Min free GB").grid(
            row=5, column=4, sticky="w", padx=(12, 4), pady=8
        )
        self.min_free_entry = ctk.CTkEntry(self.options, width=70)
        self.min_free_entry.insert(0, str(self.cfg["min_free_gb"]))
        self.min_free_entry.grid(row=5, column=5, sticky="w", padx=(4, 12), pady=8)
        self.min_free_entry.bind("<FocusOut>", lambda _e: self._persist())

        ctk.CTkLabel(self.options, text="Extra streamlink args").grid(
            row=6, column=0, sticky="w", padx=(12, 4), pady=(8, 12)
        )
        self.extra_entry = ctk.CTkEntry(
            self.options, placeholder_text="e.g. --hls-live-edge 3 --force"
        )
        self.extra_entry.insert(0, self.cfg.get("extra_args") or "")
        self.extra_entry.grid(
            row=6, column=1, columnspan=5, sticky="ew", padx=(4, 12), pady=(8, 12)
        )
        self.extra_entry.bind("<FocusOut>", lambda _e: self._persist())

        body = ctk.CTkFrame(self)
        body.grid(row=4, column=0, sticky="nsew", padx=16, pady=8)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=2)
        body.grid_rowconfigure(3, weight=2)
        body.grid_rowconfigure(5, weight=1)

        ctk.CTkLabel(body, text="Streamers", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, sticky="w", padx=12, pady=(10, 4)
        )
        self.list_frame = ctk.CTkScrollableFrame(body, height=140)
        self.list_frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=4)
        self.list_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(body, text="Log", font=ctk.CTkFont(weight="bold")).grid(
            row=2, column=0, sticky="w", padx=12, pady=(10, 4)
        )
        self.log_box = ctk.CTkTextbox(body, wrap="word", height=140)
        self.log_box.grid(row=3, column=0, sticky="nsew", padx=12, pady=4)
        self.log_box.configure(state="disabled")

        hist_header = ctk.CTkFrame(body, fg_color="transparent")
        hist_header.grid(row=4, column=0, sticky="ew", padx=12, pady=(8, 4))
        hist_header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(hist_header, text="Recent recordings", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, sticky="w"
        )
        ctk.CTkButton(hist_header, text="Refresh", width=80, command=self._refresh_history).grid(
            row=0, column=1, sticky="e"
        )

        self.history_box = ctk.CTkTextbox(body, wrap="word", height=90)
        self.history_box.grid(row=5, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.history_box.configure(state="disabled")

        footer = ctk.CTkFrame(self)
        footer.grid(row=5, column=0, sticky="ew", padx=16, pady=(4, 16))
        footer.grid_columnconfigure(2, weight=1)

        self.start_btn = ctk.CTkButton(
            footer, text="Start monitoring", width=150, command=self._start
        )
        self.start_btn.grid(row=0, column=0, padx=(12, 6), pady=12)

        self.stop_btn = ctk.CTkButton(
            footer,
            text="Stop",
            width=100,
            fg_color="#8b2e2e",
            hover_color="#a33a3a",
            command=self._stop,
            state="disabled",
        )
        self.stop_btn.grid(row=0, column=1, padx=6, pady=12)

        self.open_btn = ctk.CTkButton(
            footer, text="Open recordings folder", width=170, command=self._open_folder
        )
        self.open_btn.grid(row=0, column=3, padx=6, pady=12)

        self.setup_btn = ctk.CTkButton(
            footer, text="Setup tools", width=100, command=self._setup_tools
        )
        self.setup_btn.grid(row=0, column=4, padx=6, pady=12)

        self.start_menu_btn = ctk.CTkButton(
            footer, text="Add to Start Menu", width=130, command=self._add_start_menu
        )
        self.start_menu_btn.grid(row=0, column=5, padx=6, pady=12)

        self.open_log_btn = ctk.CTkButton(
            footer, text="Open log", width=90, command=self._open_log
        )
        self.open_log_btn.grid(row=0, column=6, padx=(6, 12), pady=12)

    def _add_start_menu(self) -> None:
        try:
            path = create_start_menu_shortcut()
            mark_start_menu_prompted()
            self._append_log(f"Start Menu shortcut created: {path}")
            if not self._monitoring:
                messagebox.showinfo(
                    "Start Menu",
                    "Added to the Start Menu as “Twitch Auto Recorder”.\n"
                    "Search for it in Start, or pin it from there.",
                )
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"Start Menu shortcut failed: {exc}")
            if not self._monitoring:
                messagebox.showerror("Start Menu", f"Could not create shortcut:\n{exc}")

    def _maybe_ask_start_menu(self) -> None:
        if self._monitoring:
            return
        if not should_ask_start_menu():
            return
        mark_start_menu_prompted()
        if start_menu_shortcut_exists():
            return
        if messagebox.askyesno(
            "Start Menu",
            "Add Twitch Auto Recorder to the Start Menu?\n\n"
            "You can also do this later with the Add to Start Menu button.",
        ):
            self._add_start_menu()

    def _check_deps_on_startup(self) -> None:
        if self._monitoring:
            return
        summary = status_summary()
        self._append_log(f"Tools — streamlink: {summary['streamlink']}")
        self._append_log(f"Tools — ffmpeg: {summary['ffmpeg']}")
        needs = "MISSING" in summary["streamlink"] or "MISSING" in summary["ffmpeg"]
        if needs:
            if messagebox.askyesno(
                "Setup recording tools",
                "Streamlink and/or FFmpeg are missing.\n\n"
                "Install them automatically now?\n"
                "(Uses winget when available, otherwise downloads a portable FFmpeg.)",
            ):
                self._setup_tools()

    def _setup_tools(self) -> None:
        if self._monitoring:
            self._append_log("Stop monitoring before running Setup tools")
            return
        self.setup_btn.configure(state="disabled")
        self._append_log("Setting up tools…")

        def work() -> None:
            messages: list[str] = []

            def log(msg: str) -> None:
                messages.append(msg)
                self.event_queue.put(("log", None, msg))

            ok, summary = ensure_dependencies(log)
            self.event_queue.put(
                (
                    "deps_done",
                    None,
                    {"ok": ok, "summary": summary},
                )
            )

        threading.Thread(target=work, daemon=True).start()

    def _quiet_update_check(self) -> None:
        """Background check — show UI only when a newer release exists."""

        def work() -> None:
            try:
                info = fetch_latest_release()
                if is_newer(info.version):
                    self.event_queue.put(("update_available", None, info))
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=work, daemon=True).start()

    def _show_update_available(self, info) -> None:
        first = self._pending_update is None
        self._pending_update = info
        self.update_btn.configure(text=f"Update to v{info.version}", state="normal")
        self.update_btn.grid()
        if first:
            self._append_log(
                f"Update available: v{info.version} (you have v{APP_VERSION}). "
                "Use the Update button when idle, or download from "
                f"{info.html_url or GITHUB_RELEASES_URL}"
            )
        # Popup only when idle — never steal focus while monitoring/recording
        if self._monitoring or self._update_popup_shown:
            return
        self._update_popup_shown = True
        prompt = (
            f"Version {info.version} is available (you have {APP_VERSION}).\n\n"
            "Download, install silently, and relaunch now?\n"
            "Your settings are kept."
        )
        if messagebox.askyesno("Update available", prompt):
            self._install_pending_update()

    def _install_pending_update(self) -> None:
        info = self._pending_update
        if info is None:
            return
        if self._monitoring:
            self._append_log("Stop monitoring before installing an update")
            return
        self.update_btn.configure(state="disabled", text="Updating…")
        self._append_log(f"Downloading {info.tag}…")

        def work() -> None:
            try:

                def log(msg: str) -> None:
                    self.event_queue.put(("log", None, msg))

                path = download_installer(info.setup_url, log=log)
                self.event_queue.put(("update_ready", None, {"ok": True, "path": str(path)}))
            except Exception as exc:  # noqa: BLE001
                self.event_queue.put(
                    ("update_ready", None, {"ok": False, "error": str(exc)})
                )

        threading.Thread(target=work, daemon=True).start()

    def _apply_downloaded_update(self, setup_path: str) -> None:
        self._append_log("Installing update silently and relaunching…")
        self.update_btn.configure(text="Installing…")
        try:
            launch_silent_update(Path(setup_path))
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"Could not start update: {exc}")
            if not self._monitoring:
                messagebox.showerror("Update", f"Could not start update:\n{exc}")
            if self._pending_update is not None:
                self.update_btn.configure(
                    state="normal",
                    text=f"Update to v{self._pending_update.version}",
                )
            return
        try:
            self._persist()
        except Exception:  # noqa: BLE001
            pass
        if self._monitoring:
            self.manager.stop()
        self.destroy()

    def _toggle_options(self) -> None:
        self._options_visible = not self._options_visible
        if self._options_visible:
            self.options.grid()
            self.options_toggle.configure(text="Options ▴")
        else:
            self.options.grid_remove()
            self.options_toggle.configure(text="Options ▾")

    def _on_always_on_top(self) -> None:
        self._persist()
        self._apply_always_on_top()

    def _apply_always_on_top(self) -> None:
        self.attributes("-topmost", bool(self.always_on_top_var.get()))

    def _refresh_channel_rows(self) -> None:
        for child in self.list_frame.winfo_children():
            child.destroy()
        self.row_widgets.clear()

        if not self.channels:
            empty = ctk.CTkLabel(
                self.list_frame,
                text="No streamers yet — add a Twitch username above.",
                text_color="#888888",
            )
            empty.grid(row=0, column=0, sticky="w", padx=8, pady=12)
            return

        for i, channel in enumerate(self.channels):
            row = ctk.CTkFrame(self.list_frame)
            row.grid(row=i, column=0, sticky="ew", padx=4, pady=4)
            row.grid_columnconfigure(1, weight=1)

            var = ctk.BooleanVar(value=False)
            ctk.CTkCheckBox(row, text="", variable=var, width=24).grid(
                row=0, column=0, padx=(8, 4), pady=8
            )
            ctk.CTkLabel(
                row, text=channel, width=140, anchor="w", font=ctk.CTkFont(weight="bold")
            ).grid(row=0, column=1, sticky="w", padx=4, pady=8)

            badge = ctk.CTkLabel(
                row, text="Idle", text_color=STATUS_COLORS["Idle"], width=90, anchor="w"
            )
            badge.grid(row=0, column=2, padx=4, pady=8)

            detail = ctk.CTkLabel(row, text="", anchor="w", text_color="#aaaaaa")
            detail.grid(row=0, column=3, sticky="ew", padx=4, pady=8)

            ctk.CTkButton(
                row,
                text="Folder",
                width=70,
                command=lambda c=channel: self._open_channel_folder(c),
            ).grid(row=0, column=4, padx=(4, 12), pady=8)

            self.row_widgets[channel] = {"selected": var, "badge": badge, "detail": detail}

    def _refresh_history(self) -> None:
        lines = []
        for item in load_history(25):
            channel = item.get("channel", "?")
            size = item.get("size_mb", "?")
            saved = item.get("saved_at", "")
            title = item.get("title") or ""
            path = item.get("path", "")
            name = Path(path).name if path else ""
            bit = f"{saved}  ·  {channel}  ·  {size} MB  ·  {name}"
            if title:
                bit += f"  ·  {title}"
            lines.append(bit)
        self.history_box.configure(state="normal")
        self.history_box.delete("1.0", "end")
        self.history_box.insert("end", "\n".join(lines) if lines else "No recordings yet.")
        self.history_box.configure(state="disabled")

    def _add_channel(self) -> None:
        raw = self.channel_entry.get().strip()
        if not raw:
            return
        channel = raw.replace("https://", "").replace("http://", "")
        channel = channel.replace("www.twitch.tv/", "").replace("twitch.tv/", "")
        channel = channel.strip("/").split("/")[0].split("?")[0].lower()
        if not channel:
            return
        if channel in self.channels:
            self._append_log(f"{channel} is already in the list")
            return
        self.channels.append(channel)
        self.channel_entry.delete(0, "end")
        self._persist()
        self._refresh_channel_rows()
        self._append_log(f"Added {channel}")

    def _remove_selected(self) -> None:
        if self._monitoring:
            messagebox.showinfo("Busy", "Stop monitoring before removing channels.")
            return
        to_remove = [c for c, w in self.row_widgets.items() if w["selected"].get()]
        if not to_remove:
            return
        self.channels = [c for c in self.channels if c not in to_remove]
        self._persist()
        self._refresh_channel_rows()
        self._append_log("Removed: " + ", ".join(to_remove))

    def _browse(self) -> None:
        path = filedialog.askdirectory(initialdir=self.output_entry.get() or DEFAULT_OUTPUT)
        if path:
            self.output_entry.delete(0, "end")
            self.output_entry.insert(0, path)
            self._persist()
            self._tick_disk()

    def _read_options(self) -> dict:
        return {
            "channels": list(self.channels),
            "output_dir": self.output_entry.get().strip() or DEFAULT_OUTPUT,
            "quality": self.quality_menu.get(),
            "poll_interval": _as_int(self.poll_entry.get(), 30),
            "disable_ads": bool(self.disable_ads_var.get()),
            "disable_reruns": bool(self.disable_reruns_var.get()),
            "low_latency": bool(self.low_latency_var.get()),
            "remux_to_mp4": bool(self.remux_var.get()),
            "retry_streams": _as_int(self.retry_streams_entry.get(), 5),
            "retry_max": _as_int(self.retry_max_entry.get(), 6),
            "retry_open": _as_int(self.retry_open_entry.get(), 3),
            "extra_args": self.extra_entry.get().strip(),
            "include_title": bool(self.include_title_var.get()),
            "beep_on_live": bool(self.beep_var.get()),
            "keep_awake": bool(self.keep_awake_var.get()),
            "always_on_top": bool(self.always_on_top_var.get()),
            "split_hours": _as_float(self.split_entry.get(), 0.0),
            "min_free_gb": _as_float(self.min_free_entry.get(), 5.0),
        }

    def _persist(self) -> None:
        self.cfg = self._read_options()
        save_config(self.cfg)

    def _make_recorder_config(self) -> RecorderConfig:
        c = self.cfg
        return RecorderConfig(
            channels=list(self.channels),
            output_dir=c["output_dir"],
            quality=c["quality"],
            poll_interval=int(c["poll_interval"]),
            disable_ads=bool(c["disable_ads"]),
            disable_reruns=bool(c["disable_reruns"]),
            low_latency=bool(c["low_latency"]),
            remux_to_mp4=bool(c["remux_to_mp4"]),
            retry_streams=int(c["retry_streams"]),
            retry_max=int(c["retry_max"]),
            retry_open=int(c["retry_open"]),
            extra_args=str(c.get("extra_args") or ""),
            include_title=bool(c["include_title"]),
            beep_on_live=bool(c["beep_on_live"]),
            keep_awake=bool(c["keep_awake"]),
            split_hours=float(c["split_hours"]),
            min_free_gb=float(c["min_free_gb"]),
        )

    def _start(self) -> None:
        if not self.channels:
            messagebox.showinfo("No channels", "Add at least one Twitch channel first.")
            return
        self._persist()
        out = self.cfg["output_dir"]
        Path(out).mkdir(parents=True, exist_ok=True)

        for channel in self.channels:
            widgets = self.row_widgets.get(channel)
            if widgets:
                widgets["badge"].configure(text="Waiting", text_color=STATUS_COLORS["Waiting"])
                widgets["detail"].configure(text="Starting…")

        self._monitoring = True
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.add_btn.configure(state="disabled")
        self.remove_btn.configure(state="disabled")
        self.channel_entry.configure(state="disabled")
        self.setup_btn.configure(state="disabled")
        self.start_menu_btn.configure(state="disabled")
        self.title("Twitch Auto Recorder — monitoring")

        threading.Thread(
            target=self.manager.start, args=(self._make_recorder_config(),), daemon=True
        ).start()

    def _stop(self) -> None:
        self.stop_btn.configure(state="disabled")
        self._append_log("Stopping…")
        threading.Thread(target=self._stop_worker, daemon=True).start()

    def _stop_worker(self) -> None:
        self.manager.stop()
        self.event_queue.put(("stopped", None, None))

    def _open_folder(self) -> None:
        open_path(Path(self.output_entry.get().strip() or DEFAULT_OUTPUT))

    def _open_channel_folder(self, channel: str) -> None:
        open_path(Path(self.output_entry.get().strip() or DEFAULT_OUTPUT) / channel)

    def _open_log(self) -> None:
        log_path = APP_DIR / "recorder.log"
        if not log_path.exists():
            log_path.write_text("", encoding="utf-8")
        if sys.platform.startswith("win"):
            os.startfile(log_path)  # type: ignore[attr-defined]
        else:
            open_path(log_path.parent)

    def _tick_disk(self) -> None:
        try:
            base = Path(self.output_entry.get().strip() or DEFAULT_OUTPUT)
            if not base.exists():
                base.mkdir(parents=True, exist_ok=True)
            usage = shutil.disk_usage(base)
            free_gb = usage.free / (1024**3)
            total_gb = usage.total / (1024**3)
            color = "#e74c3c" if free_gb < float(self.cfg.get("min_free_gb") or 5) else "#888888"
            self.disk_label.configure(
                text=f"Free on output drive: {free_gb:.1f} / {total_gb:.0f} GB",
                text_color=color,
            )
        except OSError:
            self.disk_label.configure(text="")
        self.after(15000, self._tick_disk)

    def _queue_status(self, channel: str, status: str, detail: str) -> None:
        self.event_queue.put(("status", channel, (status, detail)))

    def _queue_log(self, message: str) -> None:
        self.event_queue.put(("log", None, message))

    def _queue_event(self, kind: str, payload: dict) -> None:
        self.event_queue.put(("event", kind, payload))

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, channel, payload = self.event_queue.get_nowait()
                if kind == "log":
                    self._append_log(str(payload))
                elif kind == "status" and channel:
                    status, detail = payload
                    widgets = self.row_widgets.get(channel)
                    if widgets:
                        color = STATUS_COLORS.get(status, "#cccccc")
                        widgets["badge"].configure(text=status, text_color=color)
                        widgets["detail"].configure(text=detail)
                    if status == "Recording":
                        self.title(f"Twitch Auto Recorder — REC {channel}")
                elif kind == "event":
                    event_kind, data = channel, payload
                    if event_kind == "live":
                        ch = data.get("channel", "")
                        title = data.get("title") or ""
                        self._append_log(f"[{ch}] went live" + (f": {title}" if title else ""))
                        # Never steal focus / pop windows while running — optional beep + log only
                        if self.beep_var.get():
                            try:
                                winsound.MessageBeep(winsound.MB_ICONASTERISK)
                            except Exception:  # noqa: BLE001
                                pass
                    elif event_kind == "saved":
                        self._refresh_history()
                elif kind == "deps_done":
                    self.setup_btn.configure(state="normal")
                    summary = payload.get("summary") or {}
                    self._append_log(
                        f"Setup finished — streamlink: {summary.get('streamlink')} | "
                        f"ffmpeg: {summary.get('ffmpeg')}"
                    )
                    if not payload.get("ok"):
                        msg = (
                            "Streamlink is still missing. Recording may not work. "
                            "Try Setup tools, or install from https://streamlink.github.io/"
                        )
                        self._append_log(f"Setup incomplete: {msg}")
                        if not self._monitoring:
                            messagebox.showwarning("Setup incomplete", msg)
                elif kind == "update_available":
                    self._show_update_available(payload)
                elif kind == "update_ready":
                    if not payload.get("ok"):
                        err = payload.get("error") or "unknown error"
                        self._append_log(f"Update download failed: {err}")
                        if self._pending_update is not None:
                            self.update_btn.configure(
                                state="normal",
                                text=f"Update to v{self._pending_update.version}",
                            )
                        if not self._monitoring:
                            messagebox.showerror("Update download failed", err)
                    else:
                        self._apply_downloaded_update(str(payload["path"]))
                elif kind == "stopped":
                    self._monitoring = False
                    self.start_btn.configure(state="normal")
                    self.stop_btn.configure(state="disabled")
                    self.add_btn.configure(state="normal")
                    self.remove_btn.configure(state="normal")
                    self.channel_entry.configure(state="normal")
                    self.setup_btn.configure(state="normal")
                    self.start_menu_btn.configure(state="normal")
                    self.title("Twitch Auto Recorder")
                    # Offer update popup once idle if we deferred it during monitoring
                    if (
                        self._pending_update is not None
                        and not self._update_popup_shown
                    ):
                        self._show_update_available(self._pending_update)
                    for widgets in self.row_widgets.values():
                        if widgets["badge"].cget("text") != "Idle":
                            widgets["badge"].configure(
                                text="Idle", text_color=STATUS_COLORS["Idle"]
                            )
                            widgets["detail"].configure(text="Stopped")
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)

    def _append_log(self, message: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", message + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _on_close(self) -> None:
        if self._monitoring:
            if not messagebox.askyesno(
                "Recording in progress",
                "Monitoring/recording is active. Stop and quit?",
            ):
                return
            self.manager.stop()
        try:
            self._persist()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
