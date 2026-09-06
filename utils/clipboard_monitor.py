"""
Clipboard URL detection.

A Qt timer polls the clipboard and emits only genuinely new URLs.  It keeps a
startup baseline plus an in-memory cooldown so copying the same link, clipboard
manager rewrites, or huge URL lists cannot create notification storms.
"""

from __future__ import annotations

import time
from typing import Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from core.url_parser import extract_urls
from utils.logger import get_logger

log = get_logger("clipboard")

_DETECT_INTERVAL_MS = 1500
_URL_COOLDOWN_SECONDS = 120.0
_MAX_URLS_PER_EVENT = 25


class ClipboardMonitor(QObject):
    """Watches the clipboard for pasted URLs without bothering the user."""

    url_detected = pyqtSignal(str)       # a single new URL
    urls_detected = pyqtSignal(list)     # multiple new URLs, one event only

    def __init__(self, enabled: bool = True, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._last_text: Optional[str] = ""
        self._seen_urls: dict[str, float] = {}
        self._timer: Optional[QTimer] = None
        self._enabled = False
        self.set_enabled(enabled)

    # ------------------------------------------------------------- lifecycle

    def set_enabled(self, enabled: bool) -> None:
        """Start/stop the polling timer."""
        self._enabled = enabled
        if enabled and self._timer is None:
            self._timer = QTimer(self)
            self._timer.setInterval(_DETECT_INTERVAL_MS)
            self._timer.timeout.connect(self._poll)
            # Baseline the current clipboard. Opening the app with a URL
            # already copied should pre-fill the main paste box, not fire a
            # desktop notification before the user does anything.
            self._last_text = self._clipboard_text()
            self._timer.start()
        elif not enabled and self._timer is not None:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None

    def is_enabled(self) -> bool:
        return self._enabled

    # ----------------------------------------------------------------- poll

    @staticmethod
    def _clipboard_text() -> str:
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            return ""
        try:
            return app.clipboard().text() or ""
        except RuntimeError:
            return ""

    def _poll(self) -> None:
        if not self._enabled:
            return
        text = self._clipboard_text()
        if not text or text == self._last_text:
            return
        self._last_text = text

        now = time.monotonic()
        self._seen_urls = {
            url: ts for url, ts in self._seen_urls.items()
            if now - ts < _URL_COOLDOWN_SECONDS
        }

        urls = []
        for url in extract_urls(text):
            if url in self._seen_urls:
                continue
            self._seen_urls[url] = now
            urls.append(url)
            if len(urls) >= _MAX_URLS_PER_EVENT:
                break
        if not urls:
            return
        if len(urls) == 1:
            log.info("clipboard URL detected: %s", urls[0])
            self.url_detected.emit(urls[0])
        else:
            log.info("%d clipboard URLs detected", len(urls))
            self.urls_detected.emit(urls)
