"""
Custom progress bar with status-aware coloring.

The bar carries a ``mode`` dynamic property (``downloading`` / ``paused`` /
``completed`` / ``failed`` / ``queued``); the QSS themes color the chunk
per mode, so a single stylesheet handles every state.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtProperty, pyqtSignal
from PyQt6.QtWidgets import QProgressBar


class DownloadProgressBar(QProgressBar):
    """Progress bar that recolors itself based on download state."""

    clicked = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setRange(0, 1000)          # per-mille for smooth small files
        self.setTextVisible(True)
        self.setMode("queued")

    # ----------------------------------------------------------------- API

    def setMode(self, mode: str) -> None:
        """Change the status color (see QSS ``[mode=...]`` selectors)."""
        if self.property("mode") == mode:
            return
        self.setProperty("mode", mode)
        self.style().unpolish(self)
        self.style().polish(self)

    def setProgressPerMille(self, per_mille: int) -> None:
        self.setValue(int(max(0, min(1000, per_mille))))

    def format(self, value: int) -> str:
        return f"{value / 10:.1f}%"

    # ---------------------------------------------------------- paint hook

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)
