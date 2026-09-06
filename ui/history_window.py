"""
Download history window: searchable table with re-download, CSV export,
selective/all clearing.
"""

from __future__ import annotations

import csv
import os
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from database.models import HistoryItem
from utils.constants import CATEGORY_INFO, Category, Status
from utils.file_utils import human_size
from utils.logger import get_logger

log = get_logger("ui.history")

COLUMNS = ["Date", "File name", "Size", "Category", "Status", "Location"]


class HistoryWindow(QWidget):
    """Independent top-level window listing the finished downloads."""

    def __init__(self, db, engine, parent=None) -> None:
        super().__init__(parent)
        self.db = db
        self.engine = engine
        self.setWindowTitle("📜 Download History")
        self.resize(900, 560)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # -------------------------------------------------------- toolbar
        top = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("🔍 Search by name or URL…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(lambda _t: self.refresh())

        self.category_combo = QComboBox()
        self.category_combo.addItem("All categories", "")
        for key, info in CATEGORY_INFO.items():
            if key == Category.AUTO:
                continue
            self.category_combo.addItem(f"{info['emoji']} {info['label']}", key)
        self.category_combo.currentIndexChanged.connect(lambda _i: self.refresh())

        self.status_combo = QComboBox()
        self.status_combo.addItem("Any status", "")
        self.status_combo.addItem("Completed", Status.COMPLETED)
        self.status_combo.addItem("Failed", Status.FAILED)
        self.status_combo.currentIndexChanged.connect(lambda _i: self.refresh())

        self.count_label = QLabel("")
        self.count_label.setObjectName("dim")
        top.addWidget(self.search_edit, 1)
        top.addWidget(self.category_combo)
        top.addWidget(self.status_combo)
        top.addWidget(self.count_label)
        root.addLayout(top)

        # ---------------------------------------------------------- table
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(
            QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.doubleClicked.connect(lambda _i: self._open_selected_folder())
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.table, 1)

        # ---------------------------------------------------------- actions
        bottom = QHBoxLayout()
        redl = QPushButton(" Re-download")
        redl.clicked.connect(self._redownload_selected)
        export = QPushButton("📤 Export CSV…")
        export.clicked.connect(self._export_csv)
        del_sel = QPushButton("🗑 Delete selected")
        del_sel.setObjectName("dangerButton")
        del_sel.clicked.connect(self._delete_selected)
        clear = QPushButton("Clear all")
        clear.setObjectName("dangerButton")
        clear.clicked.connect(self._clear_all)
        close_btn = QPushButton("Close")
        close_btn.setObjectName("primaryButton")
        close_btn.clicked.connect(self.close)
        bottom.addWidget(redl)
        bottom.addWidget(export)
        bottom.addWidget(del_sel)
        bottom.addWidget(clear)
        bottom.addStretch(1)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.close)
        self.refresh()

    # ---------------------------------------------------------------- data

    def refresh(self) -> None:
        items = self.db.list_history(
            search=self.search_edit.text().strip(),
            category=self.category_combo.currentData() or "",
            status=self.status_combo.currentData() or "",
        )
        self.table.setRowCount(len(items))
        for row, item in enumerate(items):
            info = CATEGORY_INFO.get(item.category, {})
            status_text = {
                Status.COMPLETED: "✅ Completed",
                Status.FAILED: "✖ Failed",
            }.get(item.status, item.status)
            values = [
                (item.downloaded_at or "").replace("T", " "),
                item.file_name,
                human_size(item.file_size),
                f"{info.get('emoji', '📂')} {info.get('label', item.category)}",
                status_text,
                item.save_path,
            ]
            for col, text in enumerate(values):
                cell = QTableWidgetItem(str(text))
                if col == 1:
                    cell.setToolTip(item.url)
                if col == 4:
                    cell.setForeground(
                        _status_color(item.status))
                self.table.setItem(row, col, cell)
            # stash the history id + url on row 0 for actions
            first = self.table.item(row, 0)
            if first:
                first.setData(Qt.ItemDataRole.UserRole, item.id)
                first.setData(Qt.ItemDataRole.UserRole + 1, item.url)
                first.setData(Qt.ItemDataRole.UserRole + 2, item)
        self.count_label.setText(f"{len(items)} entries")

    # --------------------------------------------------------------- actions

    def _selected_items(self) -> list[HistoryItem]:
        items = []
        for row in sorted({i.row() for i in self.table.selectedIndexes()}):
            cell = self.table.item(row, 0)
            if cell:
                item = cell.data(Qt.ItemDataRole.UserRole + 2)
                if isinstance(item, HistoryItem):
                    items.append(item)
        return items

    def _redownload_selected(self) -> None:
        items = self._selected_items()
        if not items:
            QMessageBox.information(self, "History", "Select an entry first")
            return
        from core.url_parser import url_filename
        from database.models import Download

        for item in items:
            folder = os.path.dirname(item.save_path) or ""
            dl = Download(
                url=item.url,
                file_name=os.path.basename(item.save_path) or url_filename(item.url),
                save_path=item.save_path,
                file_size=item.file_size,
                category=item.category,
            )
            self.engine.re_download(dl)
        QMessageBox.information(
            self, "History",
            f"{len(items)} download(s) added back to the queue")

    def _open_selected_folder(self) -> None:
        items = self._selected_items()
        if not items:
            return
        from utils.system_utils import open_folder

        open_folder(os.path.dirname(items[0].save_path),
                    os.path.basename(items[0].save_path))

    def _delete_selected(self) -> None:
        items = self._selected_items()
        if not items:
            return
        self.db.delete_history([i.id for i in items])
        self.refresh()

    def _clear_all(self) -> None:
        answer = QMessageBox.question(
            self, "Clear history",
            "Delete the ENTIRE download history?\n(This does not delete files.)")
        if answer == QMessageBox.StandardButton.Yes:
            self.db.clear_history()
            self.refresh()

    def _export_csv(self) -> None:
        from PyQt6.QtWidgets import QFileDialog

        default = os.path.join(os.path.expanduser("~"), "idm_history.csv")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export history as CSV", default, "CSV files (*.csv)")
        if not path:
            return
        items = self.db.list_history(limit=100000)
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as fh:
                writer = csv.writer(fh)
                writer.writerow(["date", "file_name", "size_bytes", "category",
                                 "status", "path", "url"])
                for i in items:
                    writer.writerow([i.downloaded_at, i.file_name, i.file_size,
                                     i.category, i.status, i.save_path, i.url])
            QMessageBox.information(self, "Export",
                                    f"Exported {len(items)} entries to\n{path}")
        except OSError as exc:
            QMessageBox.critical(self, "Export", f"Failed: {exc}")


def _status_color(status: str):
    from PyQt6.QtGui import QColor

    return QColor("#00b894") if status == Status.COMPLETED else QColor("#e17055")
