"""
Clipboard URL detection.

A Qt timer polls the system clipboard (once per second) and emits a signal
whenever a *new* URL appears.  The main window decides what to do with it –
notify the user or auto-add the download, depending on settings.
"""

from __future__ import annotations

import re
from typing import Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from core.url_parser import extract_urls
from utils.logger import get_logger

log = get_logger("clipboard")

#: matches a bare URL somewhere in arbitrary clipboard text
_URL_RE = re.compile(r"https?://[^\s\"'<>\)\]\}]+", re.IGNORECASE)


class ClipboardMonitor(QObject):
    """Watches the clipboard for pasted URLs."""

    url_detected = pyqtSignal(str)  # a single new URL
    urls_detected = pyqtSignal(list)  # multiple new URLs

    def __init__(self, enabled: bool = True, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._last_text: Optional[str] = ""
        self._timer: Optional[QTimer] = None
        self._enabled = False
        self.set_enabled(enabled)

    # ------------------------------------------------------------- lifecycle

    def set_enabled(self, enabled: bool) -> None:
        """Start/stop the polling timer."""
        self._enabled = enabled
        if enabled and self._timer is None:
            self._timer = QTimer(self)
            self._timer.setInterval(1000)
            self._timer.timeout.connect(self._poll)
            self._timer.start()
            # reset baseline so the current clipboard is not announced
            self._last_text = ""
            self._poll()
        elif not enabled and self._timer is not None:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None

    def is_enabled(self) -> bool:
        return self._enabled

    # ----------------------------------------------------------------- poll

    def _poll(self) -> None:
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None or not self._enabled:
            return
        text = app.clipboard().text()
        if not text or text == self._last_text:
            return
        self._last_text = text
        urls = extract_urls(text)
        if not urls:
            return
        if len(urls) == 1:
            log.info("clipboard URL detected: %s", urls[0])
            self.url_detected.emit(urls[0])
        else:
            log.info("%d clipboard URLs detected", len(urls))
            self.urls_detected.emit(urls)
