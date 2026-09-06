"""
Status filter tabs: All / Downloading / Completed / Queued / Failed / Scheduled.

Implemented as a QTabBar with flat styling (the QSS themes draw the pills).
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QTabBar

from utils.constants import Status


class FilterTabs(QTabBar):
    """Row of status-filter pills for the download list."""

    filter_changed = pyqtSignal(str)   # Status.ALL sentinel '' or a status

    TABS = (
        ("all", "All"),
        (Status.DOWNLOADING, "Downloading"),
        (Status.COMPLETED, "Completed"),
        (Status.QUEUED, "Queued"),
        (Status.FAILED, "Failed"),
        (Status.SCHEDULED, "Scheduled"),
        (Status.PAUSED, "Paused"),
    )

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setExpanding(False)
        self.setDrawBase(False)
        for _key, label in self.TABS:
            self.addTab(label)
        self.currentChanged.connect(self._on_changed)
        self.setCurrentIndex(0)

    def _on_changed(self, index: int) -> None:
        if index < 0:
            return
        key, _ = self.TABS[index]
        self.filter_changed.emit(key)

    def set_filter(self, key: str) -> None:
        for i, (tab_key, _label) in enumerate(self.TABS):
            if tab_key == key:
                self.setCurrentIndex(i)
                return

    def current_key(self) -> str:
        idx = self.currentIndex()
        if idx < 0:
            return "all"
        return self.TABS[idx][0]

    def count_badges(self, counts: dict[str, int]) -> None:
        """Show live counts inside the tab titles, e.g. 'Downloading (2)'."""
        for i, (key, label) in enumerate(self.TABS):
            if key == "all":
                total = sum(counts.values())
                self.setTabText(i, f"{label}  ({total})")
            else:
                n = counts.get(key, 0)
                self.setTabText(i, f"{label}  ({n})" if n else label)
