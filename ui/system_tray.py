"""
System tray icon with a context menu and balloon notifications.

The icon swaps between idle / downloading / paused frames – the "animated"
download state simply alternates the two active frames on a timer while
transfers are running.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Callable, Optional

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

from utils.constants import APP_NAME, IS_FROZEN
from utils.logger import get_logger
from ui.icons import icon

log = get_logger("tray")


def _resource(*parts: str) -> str:
    if IS_FROZEN:
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


class SystemTray(QSystemTrayIcon):
    """Tray icon with status-aware frames."""

    def __init__(self, parent_window) -> None:
        super().__init__(parent_window)
        self._parent = parent_window
        self._frames: dict[str, QIcon] = {}
        self._current = "idle"
        self._animate_timer = QTimer(self)
        self._animate_timer.setInterval(900)
        self._animate_timer.timeout.connect(self._flip_frame)
        self._animating = False
        self._last_balloon: dict[tuple[str, str], float] = {}

        for name in ("tray_idle", "tray_downloading", "tray_paused"):
            path = _resource("ui", "resources", f"{name}.png")
            if os.path.isfile(path):
                self._frames[name] = QIcon(path)

        if "idle" in self._frames or "tray_idle" in self._frames:
            self.setIcon(self._icon_for("idle"))

        self.setToolTip(APP_NAME)
        self.activated.connect(self._on_activated)
        self._build_menu()

    # ----------------------------------------------------------------- menu

    def _build_menu(self) -> None:
        menu = QMenu(self._parent)
        show = menu.addAction("Show main window")
        show.triggered.connect(self._parent.show_from_tray)
        menu.addSeparator()
        add = menu.addAction(icon("add"), "Add download...")
        add.triggered.connect(self._parent.open_add_dialog)
        menu.addSeparator()
        pause = menu.addAction(icon("pause"), "Pause all")
        pause.triggered.connect(lambda: self._parent.engine.pause_all())
        resume = menu.addAction(icon("resume"), "Resume all")
        resume.triggered.connect(lambda: self._parent.engine.resume_all())
        menu.addSeparator()
        history = menu.addAction(icon("history"), "History")
        history.triggered.connect(self._parent.open_history)
        quit_act = menu.addAction(icon("close"), "Exit")
        quit_act.triggered.connect(self._parent.real_quit)
        self.setContextMenu(menu)

    # ----------------------------------------------------------------- API

    def _icon_for(self, state: str) -> QIcon:
        key = f"tray_{state}"
        return self._frames.get(key) or self._frames.get("tray_idle") or QIcon()

    def set_state(self, state: str) -> None:
        """state: idle | downloading | paused"""
        if state == self._current and state != "downloading":
            return
        self._current = state
        if state == "downloading":
            if not self._animating:
                self._animating = True
                self._flip_frame()
                self._animate_timer.start()
        else:
            if self._animating:
                self._animating = False
                self._animate_timer.stop()
            self.setIcon(self._icon_for(state))

    def _flip_frame(self) -> None:
        """Alternate the two download frames for a pulsing effect."""
        active = self._frames.get("tray_downloading")
        idle = self._frames.get("tray_idle")
        if not active:
            return
        if idle is None:
            self.setIcon(active)
            return
        if self.icon().cacheKey() == active.cacheKey():
            self.setIcon(idle)
        else:
            self.setIcon(active)

    def notify_balloon(self, title: str, message: str) -> None:
        """Balloon / toast notification through the tray icon."""
        try:
            now = time.monotonic()
            key = (title, message)
            if now - self._last_balloon.get(key, 0.0) < 20.0:
                return
            self._last_balloon[key] = now
            icon_obj = self.icon() if self.icon().isNull() is False else \
                self._icon_for("idle")
            self.showMessage(title, message, icon_obj, 6000)
        except Exception as exc:  # noqa: BLE001
            log.debug("balloon failed: %s", exc)

    # --------------------------------------------------------------- events

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self._parent.show_from_tray()
