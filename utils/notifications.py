"""
Desktop notifications (plyer with graceful fallbacks) and completion sounds.

The Qt side keeps a ``QSoundEffect`` per sound so no external audio backend
is required on Windows.  Everything degrades silently: on a machine without
notifications the app still works.
"""

from __future__ import annotations

import os
from typing import Optional

import sys

from utils.constants import APP_NAME, IS_FROZEN, IS_WINDOWS
from utils.logger import get_logger

log = get_logger("notify")

_sounds: dict[str, object] = {}  # name -> QSoundEffect


def _resource_path(*parts: str) -> str:
    """Locate a bundled resource in both source and frozen layouts."""
    if IS_FROZEN:
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))  # noqa: F821
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def notify(title: str, message: str, image: Optional[str] = None) -> bool:
    """Show a desktop notification.  Returns True on success.

    Tries plyer first (native toasts on Windows 10/11); falls back to a Qt
    tray balloon when the tray icon was supplied to the caller – the caller
    is responsible for that second chance, we only do the plyer attempt here.
    """
    try:
        from plyer import notification

        notification.notify(
            title=title,
            message=message,
            app_name=APP_NAME,
            app_icon=image or _resource_path("ui", "resources", "app_icon.png"),
            timeout=6,
        )
        return True
    except Exception as exc:  # plyer missing, headless session, etc.
        log.debug("plyer notification failed (%s); falling back", exc)
        return False


def _sound_path(name: str) -> str:
    return _resource_path("ui", "resources", "sounds", f"{name}.wav")


def play_sound(name: str, app: Optional["object"] = None) -> bool:  # type: ignore[name-defined]
    """Play a bundled sound effect (``complete`` / ``fail`` / ``start``).

    ``app`` is the ``QApplication`` instance; Qt sound needs a running app.
    """
    try:
        from PyQt6.QtMultimedia import QSoundEffect

        effect = _sounds.get(name)
        if effect is None:
            path = _sound_path(name)
            if not os.path.isfile(path):
                log.debug("sound file missing: %s", path)
                return False
            effect = QSoundEffect()
            from PyQt6.QtCore import QUrl

            effect.setSource(QUrl.fromLocalFile(path))
            effect.setVolume(0.8)
            _sounds[name] = effect
        effect.setVolume(0.8)
        effect.play()
        return True
    except Exception as exc:
        log.debug("sound playback failed: %s", exc)
        return False


def stop_all_sounds() -> None:
    for effect in _sounds.values():
        try:
            effect.stop()
        except Exception:
            pass
