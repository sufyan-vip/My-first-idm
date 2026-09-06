"""
One row of the download list.

::

    📄 report.pdf                                  ⬇ Downloading
    ████████████████░░░░░░░░  65.3%
    12.5 MB/s   74.2 / 114 MB   ETA 3:12   Segments 12/16

Double-click opens the file (when completed); right-click gives the full
action menu (pause/resume/cancel/open file/folder/re-download/delete).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction, QCursor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from database.models import EngineSnapshot
from utils.constants import CATEGORY_INFO, Category, Priority, Status
from utils.file_utils import category_emoji, format_eta, human_size, human_speed
from ui.widgets.progress_bar import DownloadProgressBar

_STATUS_CHIP = {
    Status.QUEUED: ("⏳ Queued", "dim"),
    Status.DOWNLOADING: ("⬇ Downloading", "accentText"),
    Status.PAUSED: ("⏸ Paused", "warningText"),
    Status.COMPLETED: ("✅ Done", "successText"),
    Status.FAILED: ("✖ Failed", "errorText"),
    Status.CANCELED: ("⊘ Canceled", "dim"),
    Status.SCHEDULED: ("🕒 Scheduled", "dim"),
}


def _priority_icon(p: str) -> str:
    return {"high": "🔺", "medium": "▶", "low": "🔻"}.get(p, "▶")


class DownloadItemWidget(QWidget):
    """Custom list-item widget for a single download."""

    # actions requested from the context menu
    request_pause = pyqtSignal(int)
    request_resume = pyqtSignal(int)
    request_cancel = pyqtSignal(int)
    request_delete = pyqtSignal(int)
    request_redownload = pyqtSignal(int)
    request_details = pyqtSignal(int)
    open_file = pyqtSignal(int)
    open_folder = pyqtSignal(int)

    def __init__(self, download_id: int, parent=None) -> None:
        super().__init__(parent)
        self.download_id = download_id
        self.setFixedSize(0, 108)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(14)

        # ------------------------------------------------ category icon
        self.icon_label = QLabel("📂")
        self.icon_label.setStyleSheet("font-size: 30px;")
        self.icon_label.setFixedWidth(40)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.icon_label)

        # --------------------------------------------------- main column
        main = QVBoxLayout()
        main.setSpacing(4)

        row1 = QHBoxLayout()
        self.name_label = QLabel("…")
        self.name_label.setStyleSheet("font-weight: 600; font-size: 14px;")
        self.name_label.setToolTip("")
        row1.addWidget(self.name_label, 1)

        self.chip_label = QLabel("")
        self.chip_label.setObjectName("dim")
        self.chip_label.setStyleSheet("font-weight: 600;")
        row1.addWidget(self.chip_label)
        main.addLayout(row1)

        self.progress = DownloadProgressBar()
        self.progress.setCursor(Qt.CursorShape.PointingHandCursor)
        self.progress.clicked.connect(lambda: self.open_file.emit(self.download_id))
        main.addWidget(self.progress)

        row2 = QHBoxLayout()
        row2.setSpacing(16)
        self.speed_label = QLabel("0 B/s")
        self.speed_label.setObjectName("secondaryText")
        self.meta_label = QLabel("")
        self.meta_label.setObjectName("secondaryText")
        self.eta_label = QLabel("")
        self.eta_label.setObjectName("secondaryText")
        self.seg_label = QLabel("")
        self.seg_label.setObjectName("secondaryText")
        self.row2_widgets = [self.speed_label, self.meta_label,
                             self.eta_label, self.seg_label]
        for w in self.row2_widgets:
            w.setMinimumWidth(0)
            w.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            row2.addWidget(w)
        row2.addStretch(1)
        main.addLayout(row2)
        layout.addLayout(main, 1)

    # ----------------------------------------------------------------- API

    def set_size_hint(self, width: int) -> None:
        self.setFixedSize(width, 108)
        self.setMinimumWidth(width)

    def update_snapshot(self, snap: EngineSnapshot) -> None:
        self.icon_label.setText(category_emoji(snap.category))
        self.name_label.setText(snap.file_name or "…")
        self.name_label.setToolTip(f"URL: {snap.url}\nSave to: {snap.save_path}")

        chip_text, chip_obj = _STATUS_CHIP.get(
            snap.status, (snap.status.title(), "dim"))
        if snap.status in (Status.QUEUED, Status.SCHEDULED):
            chip_text += f"  {_priority_icon(snap.priority)}"
        self.chip_label.setText(chip_text)
        self.chip_label.setObjectName(chip_obj)
        self.chip_label.style().unpolish(self.chip_label)
        self.chip_label.style().polish(self.chip_label)

        # progress
        per_mille = int(snap.progress * 10)
        mode = {
            Status.COMPLETED: "completed",
            Status.FAILED: "failed",
            Status.CANCELED: "failed",
            Status.PAUSED: "paused",
            Status.QUEUED: "queued",
            Status.SCHEDULED: "queued",
        }.get(snap.status, "downloading")
        self.progress.setMode(mode)
        if snap.status == Status.COMPLETED:
            self.progress.setFormat("")          # -> uses format() override
            self.progress.setRange(0, 1000)
            self.progress.setProgressPerMille(1000)
        elif snap.file_size > 0:
            self.progress.setFormat("")
            self.progress.setRange(0, 1000)
            self.progress.setProgressPerMille(per_mille)
        else:
            # unknown total size – show bytes transferred instead of a %
            self.progress.setRange(0, 1)
            self.progress.setValue(1)
            self.progress.setFormat(human_size(snap.downloaded_size))

        # meta line
        if snap.status == Status.COMPLETED:
            self.speed_label.setText("✅")
            when = snap.completed_at or ""
            self.meta_label.setText(
                f"{human_size(snap.file_size)}   •   {when[:16].replace('T', ' ')}")
            self.eta_label.setText("")
            self.seg_label.setText(f"SHA256 {snap.checksum_sha256[:12]}…"
                                   if snap.checksum_sha256 else "")
        elif snap.status == Status.FAILED:
            self.speed_label.setText("✖")
            self.meta_label.setText(snap.error_message[:80] or "Failed")
            self.eta_label.setText("")
            self.seg_label.setText("")
        elif snap.status == Status.QUEUED:
            self.speed_label.setText("⏳ waiting")
            self.meta_label.setText(f"{human_size(snap.file_size)}"
                                    if snap.file_size else "size unknown")
            self.eta_label.setText("")
            self.seg_label.setText(f"priority: {snap.priority}")
        elif snap.status == Status.SCHEDULED:
            self.speed_label.setText("🕒 scheduled")
            self.meta_label.setText(snap.scheduled_time or "")
            self.eta_label.setText("")
            self.seg_label.setText(f"priority: {snap.priority}")
        elif snap.status == Status.PAUSED:
            self.speed_label.setText("⏸ paused")
            self.meta_label.setText(
                f"{human_size(snap.downloaded_size)} / "
                f"{human_size(snap.file_size)}" if snap.file_size else
                f"{human_size(snap.downloaded_size)}")
            self.eta_label.setText("paused")
            self.seg_label.setText(
                f"segments {snap.segments_completed}/{snap.segments_total}")
        else:  # downloading
            self.speed_label.setText(
                f"⬇ {human_speed(snap.speed)}  (avg {human_speed(snap.avg_speed)})")
            size_text = (f"{human_size(snap.downloaded_size)} / "
                         f"{human_size(snap.file_size)}") if snap.file_size else \
                human_size(snap.downloaded_size)
            self.meta_label.setText(size_text)
            eta = snap.eta_seconds
            self.eta_label.setText(f"ETA {format_eta(eta)}"
                                   if eta == eta and eta > 0 else "ETA …")
            self.seg_label.setText(
                f"segments {snap.segments_completed}/{snap.segments_total} "
                f"({snap.segments_active} active)")

    # ------------------------------------------------------------ mouse menu

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        snap_id = self.download_id
        menu = QMenu(self)
        m = menu

        a = QAction("⏸ Pause", m)
        a.triggered.connect(lambda: self.request_pause.emit(snap_id))
        m.addAction(a)
        a = QAction("▶ Resume", m)
        a.triggered.connect(lambda: self.request_resume.emit(snap_id))
        m.addAction(a)
        a = QAction("⊘ Cancel download", m)
        a.triggered.connect(lambda: self.request_cancel.emit(snap_id))
        m.addAction(a)
        m.addSeparator()
        a = QAction("📄 Open file", m)
        a.triggered.connect(lambda: self.open_file.emit(snap_id))
        m.addAction(a)
        a = QAction("📁 Open folder", m)
        a.triggered.connect(lambda: self.open_folder.emit(snap_id))
        m.addAction(a)
        a = QAction(" Download again", m)
        a.triggered.connect(lambda: self.request_redownload.emit(snap_id))
        m.addAction(a)
        a = QAction("ℹ Details", m)
        a.triggered.connect(lambda: self.request_details.emit(snap_id))
        m.addAction(a)
        m.addSeparator()
        a = QAction("🗑 Delete from list", m)
        a.triggered.connect(lambda: self.request_delete.emit(snap_id))
        m.addAction(a)
        menu.exec(event.globalPos())

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.open_file.emit(self.download_id)
        super().mouseDoubleClickEvent(event)
