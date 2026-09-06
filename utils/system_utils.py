"""
OS integration helpers: opening files/folders, PC shutdown, autostart
command construction and platform niceties that differ between Windows and
the development platforms.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Optional

from utils.constants import IS_FROZEN, IS_WINDOWS
from utils.logger import get_logger

log = get_logger("system")


def open_file(path: str) -> bool:
    """Open *path* with the OS default application."""
    try:
        if IS_WINDOWS:
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return True
    except OSError as exc:
        log.warning("could not open file %s: %s", path, exc)
        return False


def open_folder(path: str, select: Optional[str] = None) -> bool:
    """Open *path* in the file manager, optionally selecting *select*.

    ``select`` may be a file name or full path; on Windows Explorer will
    highlight it, on XDG the manager best-effort.
    """
    try:
        if not os.path.isdir(path):
            path = os.path.dirname(path) or os.path.expanduser("~")
        if IS_WINDOWS:
            if select:
                full = select if os.path.isabs(select) else os.path.join(path, select)
                subprocess.Popen(["explorer", "/select,", full])
            else:
                os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            if select and os.path.isfile(select):
                subprocess.Popen(["open", "-R", select])
            else:
                subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return True
    except OSError as exc:
        log.warning("could not open folder %s: %s", path, exc)
        return False


def shutdown_pc(delay_seconds: int = 0) -> bool:
    """Schedule a system shutdown (power off / reboot depending on OS)."""
    try:
        if IS_WINDOWS:
            # /t delay, /f force apps closed, /p power off (not hibernate)
            args = ["shutdown", "/p", "/t", str(max(0, delay_seconds))]
            subprocess.Popen(args, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        elif sys.platform == "darwin":
            subprocess.Popen(["osascript", "-e", 'tell app "System Events" to shut down'])
        else:
            subprocess.Popen(["shutdown", "-h", "now"])
        log.info("system shutdown requested")
        return True
    except OSError as exc:
        log.warning("shutdown failed: %s", exc)
        return False


def restart_pc() -> bool:
    try:
        if IS_WINDOWS:
            subprocess.Popen(["shutdown", "/r", "/t", "0"],
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        elif sys.platform == "darwin":
            subprocess.Popen(["osascript", "-e", 'tell app "System Events" to restart'])
        else:
            subprocess.Popen(["shutdown", "-r", "now"])
        return True
    except OSError as exc:  # pragma: no cover
        log.warning("reboot failed: %s", exc)
        return False


def startup_command() -> str:
    """Command line that re-launches the application at logon."""
    if IS_FROZEN:
        exe = sys.executable
        return f'"{exe}" --minimized' if exe else ""
    python = sys.executable
    script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
    return f'"{python}" "{script}" --minimized'


def run_antivirus_scan(file_path: str) -> bool:
    """Run a Windows Defender scan on *file_path* (best effort).

    Uses the built-in ``MpCmdRun.exe``.  Returns True when the scan command
    could be launched; the actual scan result is asynchronous.
    """
    if not IS_WINDOWS:
        log.info("antivirus scan requested but not supported on this platform")
        return False
    candidates = [
        r"C:\Program Files\Windows Defender\MpCmdRun.exe",
        r"C:\Program Files (x86)\Windows Defender\MpCmdRun.exe",
    ]
    for cmd in candidates:
        if os.path.isfile(cmd):
            try:
                subprocess.Popen(
                    [cmd, "-Scan", "-ScanType", "3", "-File", file_path],
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                return True
            except OSError as exc:
                log.warning("antivirus scan launch failed: %s", exc)
                return False
    log.warning("Windows Defender MpCmdRun.exe not found")
    return False


def is_admin() -> bool:
    """Best-effort check whether the current process runs elevated."""
    try:
        if IS_WINDOWS:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        return os.geteuid() == 0
    except (AttributeError, OSError):  # pragma: no cover
        return False
