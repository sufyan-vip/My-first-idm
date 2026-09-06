"""
Internet Download Manager – application entry point.

GUI mode (default):
    python main.py                 start the desktop application
    python main.py --minimized     start hidden in the system tray

CLI mode (no window, console progress):
    python main.py --url "https://example.com/file.zip" [--output DIR]

The same file is the PyInstaller entry point – it doubles as the source of
the shipped InternetDownloadManager.exe.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Optional


def _repo_root() -> str:
    """Repository / bundle root (so resources resolve in both modes)."""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def setup_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="idm", description="Internet Download Manager – multi-threaded "
                                "download manager for Windows")
    parser.add_argument("--url", help="download this URL in CLI mode (no GUI)")
    parser.add_argument("--output", default="", help="output folder (CLI mode)")
    parser.add_argument("--minimized", action="store_true",
                        help="start hidden in the system tray")
    parser.add_argument("--version", action="store_true",
                        help="print version and exit")
    parser.add_argument("--no-tray", action="store_true",
                        help="disable the system tray icon")
    return parser


# ---------------------------------------------------------------------------
# Theming
# ---------------------------------------------------------------------------

def theme_path(theme: str) -> str:
    return os.path.join(_repo_root(), "ui", "themes", f"{theme}_theme.qss")


def load_theme(app, theme: str) -> None:
    """Apply a QSS theme (``dark`` / ``light``)."""
    path = theme_path(theme)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            app.setStyleSheet(fh.read())
    except OSError as exc:
        print(f"[idm] could not load theme {path}: {exc}", file=sys.stderr)


def system_theme() -> str:
    """Best-effort detection of the Windows system theme (light/dark)."""
    if sys.platform.startswith("win"):
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            winreg.CloseKey(key)
            return "light" if int(value) == 1 else "dark"
        except OSError:
            pass
    return "dark"


def resolve_theme(config) -> str:
    want = config.get("theme", "dark")
    if want == "system":
        return system_theme()
    return want if want in ("dark", "light") else "dark"


# ---------------------------------------------------------------------------
# Application bootstrap
# ---------------------------------------------------------------------------

def _bootstrap() -> tuple:
    """Create config + database + logger (works for GUI and CLI modes)."""
    from database.db_manager import Database
    from utils.config import Config
    from utils.logger import setup_logging
    import logging

    db_path = _bootstrap_db_path()
    data_dir = os.path.dirname(db_path)
    setup_logging(os.path.join(data_dir, "logs"), logging.INFO, console=False)

    db = Database(db_path)
    config = Config(db)
    _mark_portable(config)
    return db, config


def _bootstrap_db_path() -> str:
    """Database path – portable if the last run used portable mode.

    We read a tiny marker file to survive the chicken-and-egg problem
    (config lives inside the database).
    """
    from utils.constants import DB_FILE_NAME, app_data_dir, system_appdata

    portable_marker = os.path.join(app_data_dir(), "portable.flag")
    if os.path.isfile(portable_marker):
        return os.path.join(app_data_dir(), DB_FILE_NAME)
    return os.path.join(system_appdata(), DB_FILE_NAME)


def _mark_portable(config) -> None:
    from utils.constants import app_data_dir

    try:
        os.makedirs(app_data_dir(), exist_ok=True)
        if config.is_portable():
            with open(os.path.join(app_data_dir(), "portable.flag"), "w") as fh:
                fh.write("1")
        else:
            flag = os.path.join(app_data_dir(), "portable.flag")
            if os.path.isfile(flag):
                os.remove(flag)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# GUI mode
# ---------------------------------------------------------------------------

def run_gui(args) -> int:
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import QApplication

    # Qt6 always enables high-DPI scaling and high-DPI pixmaps; the old
    # AA_EnableHighDpiScaling / AA_UseHighDpiPixmaps attributes were removed
    # from Qt, so referencing them crashes startup with
    # "AttributeError: AA_EnableHighDpiScaling" under PyQt6.
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

    app = QApplication(sys.argv)
    app.setApplicationName("Internet Download Manager")
    app.setOrganizationName("IDM Pro")

    db, config = _bootstrap()

    theme = resolve_theme(config)
    load_theme(app, theme)

    icon_path = os.path.join(_repo_root(), "ui", "resources", "app_icon.png")
    if os.path.isfile(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    from core.download_engine import DownloadEngine
    engine = DownloadEngine(db, config)
    engine.start()

    # re-apply theme when the user changes it in settings; the engine reacts
    # to speed-limit changes on its own
    def _on_setting_changed(key: str, _value: str) -> None:
        if key == "theme":
            load_theme(app, resolve_theme(config))

    config.changed.connect(_on_setting_changed)
    config.changed.connect(engine.on_settings_changed)

    from ui.main_window import MainWindow
    window = MainWindow(db, config, engine)

    tray = None
    if not args.no_tray and config.get_bool("minimize_to_tray"):
        try:
            from ui.system_tray import SystemTray
            tray = SystemTray(window)
            window.set_tray(tray)
            if not args.minimized:
                tray.show()
        except Exception as exc:  # noqa: BLE001 – tray is optional
            print(f"[idm] system tray unavailable: {exc}", file=sys.stderr)
            tray = None

    if args.minimized and tray is not None:
        window.hide()
    else:
        window.show()

    code = app.exec()
    db.close()
    return code


# ---------------------------------------------------------------------------
# CLI mode
# ---------------------------------------------------------------------------

def _print_progress(snap) -> None:
    bar_w = 28
    filled = int(bar_w * min(100.0, snap.progress) / 100)
    bar = "█" * filled + "░" * (bar_w - filled)
    size = f"{snap.downloaded_size / 1048576:8.1f} / {snap.file_size / 1048576:8.1f} MB" \
        if snap.file_size else f"{snap.downloaded_size / 1048576:8.1f} MB"
    sys.stdout.write(
        f"\r  [{bar}] {snap.progress:5.1f}%  {size}  "
        f"{snap.speed / 1048576:7.2f} MB/s   "
        f"eta {int(snap.eta_seconds) if snap.eta_seconds == snap.eta_seconds else '?':>4}   ")
    sys.stdout.flush()


def run_cli(args) -> int:
    import logging

    from database.db_manager import Database
    from utils.config import Config
    from utils.logger import get_logger, setup_logging
    from utils.constants import system_appdata

    setup_logging(system_appdata() + os.sep + "logs", logging.INFO, console=True)
    log = get_logger("cli")
    db = Database(_bootstrap_db_path())
    config = Config(db)

    from core.download_engine import DownloadEngine
    from core.url_parser import category_for_url, is_valid_url, url_filename
    from database.models import Download
    from utils.constants import Status

    url = (args.url or "").strip()
    if not is_valid_url(url):
        print("error: --url must be a valid http(s)/ftp URL", file=sys.stderr)
        return 2

    name = url_filename(url)
    folder = os.path.abspath(args.output or config.download_dir())
    os.makedirs(folder, exist_ok=True)

    dl = Download(url=url, file_name=name,
                  save_path=os.path.join(folder, name),
                  category=category_for_url(url))
    dl_id = None

    engine = DownloadEngine(db, config)
    finished = {"done": False, "ok": False, "path": ""}

    def on_completed(id_, path):
        if id_ == dl_id:
            finished.update(done=True, ok=True, path=path)

    def on_failed(id_, err):
        if id_ == dl_id:
            finished.update(done=True, ok=False, path=err)

    engine.download_completed.connect(on_completed)
    engine.download_failed.connect(on_failed)
    engine.stats_updated.connect(lambda: None)
    engine.start()
    dl_id = engine.add(dl)

    print(f"⬇ Downloading {name}")
    print(f"  url:    {url}")
    print(f"  output: {folder}")
    try:
        while not finished["done"]:
            time.sleep(0.4)
            snap = engine.snapshot(dl_id)
            if snap:
                _print_progress(snap)
    except KeyboardInterrupt:
        print("\ninterrupted – cancelling")
        engine.cancel_download(dl_id)
        return 130
    finally:
        engine.stop()
        db.close()

    if finished["ok"]:
        print(f"\n✅ Saved to: {finished['path']}")
        return 0
    print(f"\n✖ Failed: {finished['path'] or 'unknown error'}")
    return 1


# ---------------------------------------------------------------------------

def main() -> int:
    args = setup_arg_parser().parse_args()
    if args.version:
        from utils.constants import APP_NAME, APP_VERSION
        print(f"{APP_NAME} v{APP_VERSION}")
        return 0
    if args.url:
        return run_cli(args)
    return run_gui(args)


if __name__ == "__main__":
    sys.exit(main())
