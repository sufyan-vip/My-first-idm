"""
Configuration management.

Settings are persisted in the SQLite ``settings`` table (key/value), which
keeps the whole application state in a single portable database file.  The
class exposes typed accessors plus convenient boolean/int/float helpers and
fires a Qt signal whenever a value changes so the UI can react (e.g. apply a
new theme immediately).
"""

from __future__ import annotations

import os
from typing import Any, Callable, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from utils.constants import DEFAULTS, IS_WINDOWS, app_data_dir, system_appdata
from utils.logger import get_logger

log = get_logger("config")


class Config(QObject):
    """Thread-safe(ish) key/value configuration backed by SQLite.

    All values are stored as strings in the database; typed getters convert
    on the fly.  A small in-memory cache makes reads cheap.
    """

    changed = pyqtSignal(str, str)  # (key, new_value)

    def __init__(self, db, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._db = db
        self._cache: dict[str, str] = {}
        self._load()

    # ------------------------------------------------------------------ load

    def _load(self) -> None:
        """Populate the cache from the database, seeding defaults."""
        for key, default in DEFAULTS.items():
            stored = self._db.get_setting(key)
            value = stored if stored is not None else default
            self._cache[key] = value
            if stored is None:
                # persist the default so the settings UI shows a concrete value
                self._db.set_setting(key, default)
        log.debug("configuration loaded (%d keys)", len(self._cache))

    # ----------------------------------------------------------------- basic

    def get(self, key: str, default: str = "") -> str:
        if key in self._cache:
            return self._cache[key]
        stored = self._db.get_setting(key)
        value = stored if stored is not None else default
        self._cache[key] = value
        return value

    def set(self, key: str, value: str) -> None:
        value = str(value)
        if self._cache.get(key) == value:
            return
        self._db.set_setting(key, value)
        self._cache[key] = value
        log.info("setting %s = %s", key, value)
        self.changed.emit(key, value)

    # ----------------------------------------------------------------- typed

    def get_bool(self, key: str, default: bool = False) -> bool:
        raw = self.get(key, str(default).lower())
        return raw.strip().lower() in ("1", "true", "yes", "on")

    def get_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self.get(key, str(default)))
        except (TypeError, ValueError):
            return default

    def get_float(self, key: str, default: float = 0.0) -> float:
        try:
            return float(self.get(key, str(default)))
        except (TypeError, ValueError):
            return default

    # --------------------------------------------------------------- folders

    def is_portable(self) -> bool:
        return self.get_bool("portable")

    def data_dir(self) -> str:
        """Directory for the database, logs and other application data."""
        path = app_data_dir() if self.is_portable() else system_appdata()
        os.makedirs(path, exist_ok=True)
        return path

    def log_dir(self) -> str:
        path = os.path.join(self.data_dir(), "logs")
        os.makedirs(path, exist_ok=True)
        return path

    def db_path(self) -> str:
        from utils.constants import DB_FILE_NAME

        return os.path.join(self.data_dir(), DB_FILE_NAME)

    def download_dir(self, category: str = "") -> str:
        """Return the (created) folder where *category* files are saved."""
        from utils.constants import Category

        base = os.path.join(os.path.expanduser("~"), "Downloads")
        raw = self.get("download_folder", base).strip()
        if raw:
            base = raw
        if category and category != Category.AUTO and self.get_bool("categorize", True):
            base = os.path.join(base, category)
        try:
            os.makedirs(base, exist_ok=True)
        except OSError as exc:  # pragma: no cover - depends on user FS
            log.warning("could not create download dir %s: %s", base, exc)
        return base

    # ------------------------------------------------------------- shortcuts

    def speed_limit_bytes(self) -> int:
        """Return the configured bandwidth limit in bytes/second (0 = unlimited)."""
        value = max(0, self.get_int("speed_limit"))
        if value == 0:
            return 0
        unit = self.get("speed_limit_unit", "MB")
        factor = {"KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3}.get(unit, 1024 ** 2)
        return value * factor

    def proxy_dict(self) -> Optional[dict]:
        """Return a requests-style proxies mapping or None when disabled."""
        ptype = self.get("proxy_type", "none")
        if ptype == "none":
            return None
        host = self.get("proxy_host", "").strip()
        port = self.get("proxy_port", "").strip()
        if not host or not port:
            return None
        scheme = "socks5" if ptype == "socks5" else "http"
        uri = f"{scheme}://{host}:{port}"
        return {"http": uri, "https": uri}

    # ----------------------------------------------------------------- system

    def ensure_autostart(self, enable: bool) -> bool:
        """Add/remove the autostart registry entry (Windows only)."""
        if not IS_WINDOWS:
            return False
        try:
            import winreg

            from utils.system_utils import startup_command
        except ImportError:  # pragma: no cover
            return False
        try:
            cmd = startup_command()
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_SET_VALUE,
            )
            try:
                if enable and cmd:
                    winreg.SetValueEx(key, "IDMPro", 0, winreg.REG_SZ, cmd)
                    return True
                if not enable:
                    try:
                        winreg.DeleteValue(key, "IDMPro")
                    except FileNotFoundError:
                        pass
                    return True
            finally:
                winreg.CloseKey(key)
        except OSError as exc:
            log.warning("autostart change failed: %s", exc)
        return False
