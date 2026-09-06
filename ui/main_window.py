"""
Main application window.

Owns the menu bar, toolbar, search/filter row, category tabs, the download
list (custom row widgets), the speed graph, the status bar, drag & drop,
clipboard URL detection and all engine → UI signal wiring.
"""

from __future__ import annotations

import os
import sys
from typing import Optional

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QAction,
    QCloseEvent,
    QDragEnterEvent,
    QDropEvent,
    QKeySequence,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFormLayout,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from core.url_parser import extract_urls
from database.models import Download
from ui.add_download_dialog import AddDownloadDialog
from ui.about_dialog import AboutDialog
from ui.history_window import HistoryWindow
from ui.settings_dialog import SettingsDialog
from ui.widgets.category_tabs import FilterTabs
from ui.widgets.download_item import DownloadItemWidget
from ui.widgets.speed_graph import SpeedGraph
from utils.constants import (
    APP_NAME,
    APP_VERSION,
    SPEED_TEST_URL,
    UPDATE_REPO,
    Category,
    CATEGORY_INFO,
    Status,
)
from utils.file_utils import human_size, human_speed
from utils.logger import get_logger
from utils.notifications import notify
from utils.system_utils import open_file, open_folder

log = get_logger("ui.main")

SORT_OPTIONS = {
    "Newest first": 0,
    "Oldest first": 1,
    "Name (A-Z)": 2,
    "Size (large first)": 3,
    "Speed (fast first)": 4,
    "Priority (high first)": 5,
}


# ---------------------------------------------------------------------------
# Background workers (kept out of the UI thread)
# ---------------------------------------------------------------------------

class _SpeedTestWorker(QThread):
    done = pyqtSignal(float, bool, str)  # mbps, ok, message

    def __init__(self, url: str = SPEED_TEST_URL, parent=None) -> None:
        super().__init__(parent)
        self._url = url

    def run(self) -> None:
        import time

        try:
            import requests

            start = time.monotonic()
            total = 0
            with requests.get(self._url, stream=True, timeout=15) as resp:
                resp.raise_for_status()
                for chunk in resp.iter_content(chunk_size=256 * 1024):
                    total += len(chunk)
                    if time.monotonic() - start > 8:
                        break
            elapsed = max(0.001, time.monotonic() - start)
            mbps = (total * 8) / elapsed / 1_000_000
            self.done.emit(round(mbps, 2), True,
                           f"Downloaded {human_size(total)} in {elapsed:.1f}s")
        except Exception as exc:  # noqa: BLE001
            self.done.emit(0.0, False, f"{exc.__class__.__name__}: {str(exc)[:160]}")


class _UpdateWorker(QThread):
    done = pyqtSignal(bool, str)  # ok, message

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

    @staticmethod
    def _version_tuple(text: str) -> tuple:
        parts = []
        for piece in text.split(".")[:3]:
            digits = "".join(ch for ch in piece if ch.isdigit())
            parts.append(int(digits) if digits else 0)
        while len(parts) < 3:
            parts.append(0)
        return tuple(parts)

    def run(self) -> None:
        try:
            import requests

            resp = requests.get(
                f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest",
                timeout=10, headers={"User-Agent": f"idm-pro/{APP_VERSION}"})
            if resp.status_code != 200:
                self.done.emit(False, f"GitHub replied HTTP {resp.status_code}")
                return
            latest = (resp.json().get("tag_name") or "").lstrip("v")
            if not latest:
                self.done.emit(True,
                               f"You have the latest version (v{APP_VERSION})")
                return
            if self._version_tuple(latest) > self._version_tuple(APP_VERSION):
                self.done.emit(
                    False,
                    f"New version available: v{latest}\n"
                    f"You have v{APP_VERSION} – download it from the "
                    "project releases page.")
            else:
                self.done.emit(True,
                               f"You have the latest version (v{APP_VERSION})")
        except Exception as exc:  # noqa: BLE001
            self.done.emit(False, f"Update check failed: {str(exc)[:160]}")


# ---------------------------------------------------------------------------
# Details dialog
# ---------------------------------------------------------------------------

class DetailsDialog(QDialog):
    """All information about one download (read-only)."""

    def __init__(self, snap, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Details – {snap.file_name}")
        self.setMinimumWidth(560)
        form = QFormLayout(self)
        form.setSpacing(8)

        rows = [
            ("Status", _status_text(snap)),
            ("URL", snap.url),
            ("File name", snap.file_name),
            ("Save location", snap.save_path),
            ("Size", f"{human_size(snap.downloaded_size)} / {human_size(snap.file_size)}"
                     if snap.file_size else human_size(snap.downloaded_size)),
            ("Progress", f"{snap.progress:.1f}%"),
            ("Current speed", human_speed(snap.speed)),
            ("Average speed", human_speed(snap.avg_speed)),
            ("Segments", f"{snap.segments_completed}/{snap.segments_total} done"
                         if snap.segments_total else "–"),
            ("Priority", snap.priority),
            ("Category", CATEGORY_INFO.get(snap.category, {}).get(
                "label", snap.category)),
            ("Created", snap.created_at or "–"),
            ("Completed", snap.completed_at or "–"),
            ("Scheduled", snap.scheduled_time or "–"),
            ("SHA-256", snap.checksum_sha256 or "–"),
            ("MD5", snap.checksum_md5 or "–"),
        ]
        if snap.error_message:
            rows.append(("Error", snap.error_message))
        for label, value in rows:
            edit = QLineEdit(str(value))
            edit.setReadOnly(True)
            form.addRow(label, edit)


def _status_text(snap) -> str:
    return {
        Status.COMPLETED: "✅ Completed",
        Status.FAILED: "✖ Failed",
        Status.PAUSED: "⏸ Paused",
        Status.QUEUED: "⏳ Queued",
        Status.SCHEDULED: "🕒 Scheduled",
        Status.CANCELED: "⊘ Canceled",
    }.get(snap.status, snap.status)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, db, config, engine, parent=None) -> None:
        super().__init__(parent)
        self.db = db
        self.config = config
        self.engine = engine
        self._rows: dict[int, DownloadItemWidget] = {}
        self._list_items: dict[int, QListWidgetItem] = {}
        self._current_order: list[int] = []
        self._history_window: Optional[HistoryWindow] = None
        self._details_dialog: Optional[DetailsDialog] = None
        self._tray = None
        self._hidden_to_tray = False
        self._quitting = False
        self._speed_test_worker: Optional[_SpeedTestWorker] = None

        self.setWindowTitle(APP_NAME)
        self.resize(1060, 700)
        self.setMinimumSize(760, 480)
        self.setAcceptDrops(True)

        self._build_menu()
        self._build_toolbar()
        self._build_central()
        self._build_statusbar()

        # engine wiring
        engine.stats_updated.connect(self.refresh)
        engine.download_completed.connect(self._on_completed)
        engine.download_failed.connect(self._on_failed)
        engine.network_changed.connect(self._on_network_changed)

        # clipboard URL detection
        from utils.clipboard_monitor import ClipboardMonitor
        self._clipboard_monitor = ClipboardMonitor(
            enabled=config.get_bool("clipboard_detect"))
        self._clipboard_monitor.url_detected.connect(self._on_clipboard_url)
        self._clipboard_monitor.urls_detected.connect(
            lambda urls: [self._on_clipboard_url(u) for u in urls])

        config.changed.connect(self._on_config_changed)

        # restore incomplete downloads
        recovered = engine.restore_incomplete(
            auto_resume=config.get_bool("auto_resume"))
        if recovered:
            QTimer.singleShot(400, lambda: QMessageBox.information(
                self, "Restore downloads",
                f"{len(recovered)} incomplete download(s) were found from the "
                "last session.\nThey are being resumed automatically."))
        self.refresh()

    # ------------------------------------------------------------ structure

    def _build_menu(self) -> None:
        m = self.menuBar()

        file_menu = m.addMenu("&File")
        self._add(file_menu, "➕  Add URL…", self.open_add_dialog, "Ctrl+T")
        self._add(file_menu, "📋  Paste from clipboard",
                  self.paste_from_clipboard, "Ctrl+V")
        self._add(file_menu, "📄  Add from file…", self.add_from_file)
        file_menu.addSeparator()
        self._add(file_menu, "Exit", self.close)

        dl_menu = m.addMenu("&Downloads")
        self._add(dl_menu, "⏸  Pause all", self.engine.pause_all)
        self._add(dl_menu, "▶  Resume all", self.engine.resume_all)
        self._add(dl_menu, "⊘  Cancel all active", self._cancel_all)
        dl_menu.addSeparator()
        self._add(dl_menu, "📁  Open download folder", self._open_download_folder)
        self._add(dl_menu, "🧹  Clear completed", self._clear_completed)

        queue_menu = m.addMenu("&Queue")
        self._add(queue_menu, "▶  Start queue", self.engine.resume_all)
        self._add(queue_menu, "⏸  Pause queue", self.engine.pause_all)
        queue_menu.addSeparator()
        self._add(queue_menu, "📜  History", self.open_history)

        tools_menu = m.addMenu("&Tools")
        self._add(tools_menu, "🚀  Internet speed test", self.run_speed_test)
        self._add(tools_menu, "🔄  Check for updates", self.check_updates)
        tools_menu.addSeparator()
        self._add(tools_menu, "⚙  Settings…", self.open_settings)

        help_menu = m.addMenu("&Help")
        self._add(help_menu, "ℹ  About", lambda: AboutDialog(self).exec())

    def _add(self, menu, text: str, callback, shortcut: str = "") -> QAction:
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(callback)
        menu.addAction(action)
        return action

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main toolbar")
        tb.setMovable(False)
        tb.setFloatable(False)
        self.addToolBar(tb)

        def tool(text, tip, callback):
            a = QAction(text, self)
            a.setToolTip(tip)
            a.triggered.connect(callback)
            tb.addAction(a)
            return a

        tool("➕", "Add URL (Ctrl+T)", self.open_add_dialog)
        tool("📋", "Paste from clipboard", self.paste_from_clipboard)
        tb.addSeparator()
        tool("⏸", "Pause all", self.engine.pause_all)
        tool("▶", "Resume all", self.engine.resume_all)
        tool("", "Cancel selected", self._cancel_selected)
        tool("🗑", "Delete selected", self._delete_selected)
        tb.addSeparator()
        tool("⏬", "Start queue", self.engine.resume_all)
        tb.addSeparator()
        tool("📜", "History", self.open_history)
        tool("⚙", "Settings", self.open_settings)

    def _build_central(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 8, 10, 6)
        root.setSpacing(8)

        # search / filter / sort row
        top = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("🔍  Search downloads…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(lambda _t: self.refresh())
        top.addWidget(self.search_edit, 1)

        self.category_filter = QComboBox()
        self.category_filter.addItem("All categories", "")
        for key, info in CATEGORY_INFO.items():
            if key == Category.AUTO:
                continue
            self.category_filter.addItem(f"{info['emoji']} {info['label']}", key)
        self.category_filter.currentIndexChanged.connect(lambda _i: self.refresh())
        top.addWidget(self.category_filter)

        self.sort_combo = QComboBox()
        for label in SORT_OPTIONS:
            self.sort_combo.addItem(label)
        self.sort_combo.currentIndexChanged.connect(lambda _i: self.refresh())
        top.addWidget(self.sort_combo)
        root.addLayout(top)

        self.tabs = FilterTabs()
        self.tabs.filter_changed.connect(lambda _k: self.refresh())
        root.addWidget(self.tabs)

        # list + speed graph inside a vertical splitter
        splitter = QSplitter(Qt.Orientation.Vertical)

        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list.setDragDropMode(QListWidget.DragDropMode.NoDragDrop)
        self.list.setUniformItemSizes(True)
        self.list.setWordWrap(False)
        splitter.addWidget(self.list)

        graph_container = QWidget()
        gv = QVBoxLayout(graph_container)
        gv.setContentsMargins(0, 0, 0, 0)
        self.graph = SpeedGraph()
        gv.addWidget(self.graph)
        splitter.addWidget(graph_container)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([520, 150])
        root.addWidget(splitter, 1)

        # row widget actions
        self._wire_row_signals()

    def _wire_row_signals(self) -> None:
        # connect signals of every future row via event filter is overkill;
        # rows are created in refresh() where we connect immediately.
        pass

    def _connect_row(self, row: DownloadItemWidget) -> None:
        row.request_pause.connect(lambda i: self.engine.pause_download(i))
        row.request_resume.connect(lambda i: self.engine.resume_download(i))
        row.request_cancel.connect(self._confirm_cancel)
        row.request_delete.connect(self._confirm_delete)
        row.request_redownload.connect(self._redownload)
        row.request_details.connect(self._show_details)
        row.open_file.connect(self._open_download_file)
        row.open_folder.connect(self._open_download_folder_for)

    def _build_statusbar(self) -> None:
        bar = QStatusBar()
        self.setStatusBar(bar)
        self.status_counts = QLabel("⬇ 0 active")
        self.status_speed = QLabel("Total: 0 B/s")
        self.status_queue = QLabel("Queue: 0")
        self.status_net = QLabel("🌐 online")
        bar.addWidget(self.status_counts)
        bar.addPermanentWidget(self.status_queue)
        bar.addPermanentWidget(self.status_speed)
        bar.addPermanentWidget(self.status_net)

    # ------------------------------------------------------------ refresh

    def refresh(self) -> None:
        """Rebuild the visible row order and update all widgets (1 Hz)."""
        snaps = self.engine.all_snapshots()
        tab = self.tabs.current_key()
        search = self.search_edit.text().strip().lower()
        category = self.category_filter.currentData() or ""
        sort_index = self.sort_combo.currentIndex()

        visible = []
        for s in snaps:
            if tab != "all" and s.status != tab:
                continue
            if category and s.category != category:
                continue
            if search and search not in s.file_name.lower() \
                    and search not in s.url.lower():
                continue
            visible.append(s)

        prio_rank = {"high": 0, "medium": 1, "low": 2}
        if sort_index == 0:
            visible.sort(key=lambda s: s.created_at or "", reverse=True)
        elif sort_index == 1:
            visible.sort(key=lambda s: s.created_at or "")
        elif sort_index == 2:
            visible.sort(key=lambda s: s.file_name.lower())
        elif sort_index == 3:
            visible.sort(key=lambda s: s.file_size, reverse=True)
        elif sort_index == 4:
            visible.sort(key=lambda s: s.speed, reverse=True)
        elif sort_index == 5:
            visible.sort(key=lambda s: prio_rank.get(s.priority, 1))

        new_order = [s.id for s in visible]
        by_id = {s.id: s for s in visible}

        # destroy row widgets that are no longer visible
        for dl_id in list(self._rows):
            if dl_id not in by_id:
                self._destroy_row(dl_id)

        # when the visible set or its order changes, rebuild the list from
        # scratch; otherwise update the existing rows in place (no list
        # mutations, so no Qt model reset is involved)
        width = max(600, self.list.width())
        if new_order != self._current_order:
            self._rebuild_list(new_order, by_id, width)
        else:
            for dl_id in new_order:
                row = self._rows.get(dl_id)
                if row is None:            # defensive – should not happen
                    self._create_row(dl_id, by_id[dl_id], width)
                else:
                    row.update_snapshot(by_id[dl_id])
                    row.set_size_hint(width)
        self._current_order = new_order

        # tab badges
        counts = self.db.count_by_status()
        self.tabs.count_badges(counts)

        # status bar
        active = counts.get(Status.DOWNLOADING, 0)
        paused = counts.get(Status.PAUSED, 0)
        done = counts.get(Status.COMPLETED, 0)
        self.status_counts.setText(
            f"⬇ {active} active   |   ⏸ {paused} paused   |   ✅ {done} done")
        total_speed = self.engine.total_speed
        self.status_speed.setText(f"Total speed: {human_speed(total_speed)}")
        self.status_queue.setText(
            f"Queue: {counts.get(Status.QUEUED, 0) + counts.get(Status.SCHEDULED, 0)}")

        # speed graph
        self.graph.push(total_speed)

        # tray state
        if self._tray is not None:
            if active:
                self._tray.set_state("downloading")
            elif paused:
                self._tray.set_state("paused")
            else:
                self._tray.set_state("idle")

    def _create_row(self, dl_id: int, snap, width: int) -> DownloadItemWidget:
        """Create, wire and fill a row widget (not yet in the list)."""
        row = DownloadItemWidget(dl_id, parent=self)
        self._connect_row(row)
        row.update_snapshot(snap)
        row.set_size_hint(width)
        self._rows[dl_id] = row
        return row

    def _destroy_row(self, dl_id: int) -> None:
        """Drop our references to a row and defer its C++ deletion."""
        row = self._rows.pop(dl_id, None)
        self._list_items.pop(dl_id, None)
        if row is not None:
            try:
                row.deleteLater()
            except RuntimeError:
                # Qt already destroyed the C++ object (this happens as part
                # of the model reset triggered by list.clear()); the Python
                # wrapper is inert and will be garbage-collected.
                pass

    def _rebuild_list(self, order: list[int], by_id: dict, width: int) -> None:
        """Rebuild every list item and row widget for ``order``.

        Important: Qt 6 deletes the widgets of a ``QListWidget`` as part of
        the model reset that ``clear()`` triggers (the deletion is processed
        by the event loop).  Re-attaching the *same* widget to a fresh item
        after ``clear()`` therefore leaves dangling pointers in the view's
        internal editor tracking and segfaults on the next layout pass.
        We never reuse rows across a ``clear()`` – the old ones are deleted
        and brand-new widgets are created for the whole visible set.
        """
        self.list.clear()
        for dl_id in list(self._rows):
            self._destroy_row(dl_id)
        for dl_id in order:
            row = self._create_row(dl_id, by_id[dl_id], width)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, dl_id)
            item.setSizeHint(row.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
            self._list_items[dl_id] = item

    def selected_ids(self) -> list[int]:
        ids = []
        for item in self.list.selectedItems():
            dl_id = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(dl_id, int):
                ids.append(dl_id)
        return ids

    # -------------------------------------------------------- row actions

    def _confirm_cancel(self, dl_id: int) -> None:
        answer = QMessageBox.question(
            self, "Cancel download",
            "Cancel this download?\n(The partial file is kept – you can "
            "resume it later from history.)")
        if answer == QMessageBox.StandardButton.Yes:
            self.engine.cancel_download(dl_id, delete_files=False)

    def _cancel_selected(self) -> None:
        ids = self.selected_ids()
        for dl_id in ids:
            self.engine.cancel_download(dl_id, delete_files=False)

    def _cancel_all(self) -> None:
        self.engine.pause_all()

    def _confirm_delete(self, dl_id: int) -> None:
        answer = QMessageBox.question(
            self, "Delete",
            "Remove this entry from the list?\n"
            "(Completed files on disk are NOT deleted.)")
        if answer == QMessageBox.StandardButton.Yes:
            self.engine.delete_download(dl_id, delete_files=True)

    def _delete_selected(self) -> None:
        ids = self.selected_ids()
        if not ids:
            return
        if QMessageBox.question(
                self, "Delete",
                f"Remove {len(ids)} entr{'y' if len(ids) == 1 else 'ies'} "
                "from the list?\n(Completed files on disk are NOT deleted.)"
        ) == QMessageBox.StandardButton.Yes:
            for dl_id in ids:
                self.engine.delete_download(dl_id, delete_files=True)

    def _redownload(self, dl_id: int) -> None:
        dl = self.db.get_download(dl_id)
        if dl:
            self.engine.re_download(dl)

    def _show_details(self, dl_id: int) -> None:
        snap = self.engine.snapshot(dl_id)
        if snap is None:
            return
        if self._details_dialog and self._details_dialog.isVisible():
            self._details_dialog.close()
        self._details_dialog = DetailsDialog(snap, self)
        self._details_dialog.show()

    def _open_download_file(self, dl_id: int) -> None:
        dl = self.db.get_download(dl_id)
        if dl and os.path.isfile(dl.save_path):
            open_file(dl.save_path)
        else:
            QMessageBox.information(self, "Open file",
                                    "The file does not exist (yet).")

    def _open_download_folder_for(self, dl_id: int) -> None:
        dl = self.db.get_download(dl_id)
        if dl:
            open_folder(os.path.dirname(dl.save_path),
                        os.path.basename(dl.save_path))

    def _open_download_folder(self) -> None:
        open_folder(self.config.download_dir())

    def _clear_completed(self) -> None:
        if QMessageBox.question(
                self, "Clear completed",
                "Remove all COMPLETED entries from the list?\n"
                "(Files on disk are not touched.)"
        ) == QMessageBox.StandardButton.Yes:
            for dl in self.db.list_downloads(status=Status.COMPLETED):
                self.engine.delete_download(dl.id, delete_files=False)

    # -------------------------------------------------------- add actions

    def open_add_dialog(self, url: str = "") -> None:
        dlg = AddDownloadDialog(self.config, self.config.download_dir(), self)
        dlg.download_requested.connect(self._accept_download)
        if url:
            dlg.url_edit.setText(url)
        dlg.exec()

    def _accept_download(self, dl: Download, start_now: bool) -> None:
        self.engine.add(dl)
        log.info("UI: download added (%s, start_now=%s)", dl.file_name, start_now)

    def paste_from_clipboard(self) -> None:
        text = QApplication.clipboard().text().strip()
        urls = extract_urls(text)
        if not urls:
            QMessageBox.information(self, "Paste",
                                    "No valid URL found in the clipboard.")
            return
        if len(urls) == 1:
            self.open_add_dialog(urls[0])
        else:
            if QMessageBox.question(
                    self, "Batch download",
                    f"{len(urls)} URLs found in the clipboard.\n"
                    "Add them all to the queue?"
            ) == QMessageBox.StandardButton.Yes:
                from core.url_parser import category_for_url, url_filename
                for url in urls:
                    name = url_filename(url)
                    dl = Download(
                        url=url, file_name=name,
                        save_path=os.path.join(self.config.download_dir(), name),
                        category=category_for_url(url),
                    )
                    self.engine.add(dl)

    def add_from_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open URL list file", os.path.expanduser("~"),
            "Text files (*.txt *.url *.list);;All files (*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                urls = extract_urls(fh.read())
        except OSError as exc:
            QMessageBox.critical(self, "Batch import", f"Could not read file: {exc}")
            return
        if not urls:
            QMessageBox.information(self, "Batch import",
                                    "No valid URLs found in that file.")
            return
        if QMessageBox.question(
                self, "Batch download",
                f"{len(urls)} URLs found.\nAdd them all to the queue?"
        ) != QMessageBox.StandardButton.Yes:
            return
        from core.url_parser import category_for_url, url_filename
        for url in urls:
            name = url_filename(url)
            self.engine.add(Download(
                url=url, file_name=name,
                save_path=os.path.join(self.config.download_dir(), name),
                category=category_for_url(url)))

    # ------------------------------------------------- windows & dialogs

    def open_history(self) -> None:
        if self._history_window is None or not self._history_window.isVisible():
            self._history_window = HistoryWindow(self.db, self.engine, self)
        self._history_window.show()
        self._history_window.raise_()
        self._history_window.refresh()

    def open_settings(self) -> None:
        dlg = SettingsDialog(self.config, self.engine, self)
        dlg.exec()

    def run_speed_test(self) -> None:
        if self._speed_test_worker and self._speed_test_worker.isRunning():
            QMessageBox.information(self, "Speed test", "A speed test is already running.")
            return
        box = QDialog(self)
        box.setWindowTitle("🚀 Internet Speed Test")
        box.setFixedSize(460, 240)
        v = QVBoxLayout(box)
        title = QLabel("🚀 Internet Speed Test")
        title.setObjectName("windowTitle")
        v.addWidget(title)
        info = QLabel(f"Streaming {human_size(10 * 1024 ** 2)} from "
                      f"{SPEED_TEST_URL.split('/')[2]} …")
        info.setObjectName("dim")
        v.addWidget(info)
        bar = QProgressBar()
        bar.setRange(0, 0)
        v.addWidget(bar)
        result = QLabel("Starting…")
        v.addWidget(result)
        cancel = QPushButton("Cancel")
        v.addWidget(cancel, 0, Qt.AlignmentFlag.AlignRight)

        worker = _SpeedTestWorker()
        self._speed_test_worker = worker

        def on_done(mbps: float, ok: bool, message: str) -> None:
            bar.setRange(0, 100)
            bar.setValue(100)
            result.setText(("✅ " if ok else "✖ ") +
                           (f"{mbps:.2f} Mbps   •   " if ok else "") + message)
            box.deleteLater()

        worker.done.connect(on_done)
        cancel.clicked.connect(lambda: (worker.wait(2000), box.reject()))
        worker.start()
        box.exec()

    def check_updates(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Check for updates")
        box.setText("🔄 Checking for updates…")
        box.setIcon(QMessageBox.Icon.Information)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        worker = _UpdateWorker(self)

        def on_done(ok: bool, message: str) -> None:
            box.setIcon(QMessageBox.Icon.Information if ok
                        else QMessageBox.Icon.Warning)
            box.setText(message)
            box.exec()

        worker.done.connect(on_done)
        worker.start()

    # ------------------------------------------------------ notifications

    def _on_completed(self, dl_id: int, path: str) -> None:
        if self.config.get_bool("notifications_enabled", True):
            title = "Download complete"
            name = os.path.basename(path)
            notify(title, name)
            if self._tray is not None:
                self._tray.notify_balloon(title, name)
        if self.isMinimized() or self._hidden_to_tray:
            pass  # tray balloon already sent

    def _on_failed(self, dl_id: int, error: str) -> None:
        if self.config.get_bool("notifications_enabled", True):
            notify("Download failed", error[:180])
            if self._tray is not None:
                self._tray.notify_balloon("Download failed", error[:180])

    def _on_network_changed(self, online: bool) -> None:
        self.status_net.setText("🌐 online" if online else "🌐 OFFLINE")
        if not online:
            QMessageBox.warning(self, "Network",
                                "Internet connection lost.\n"
                                "Active downloads were paused and will "
                                "resume automatically.")

    def _on_config_changed(self, key: str, value: str) -> None:
        if key == "clipboard_detect":
            self._clipboard_monitor.set_enabled(value == "true")
        # theme changes are applied by main.py (it owns the QApplication)

    def _on_clipboard_url(self, url: str) -> None:
        if self.config.get_bool("clipboard_auto_add"):
            from core.url_parser import category_for_url, url_filename
            name = url_filename(url)
            self.engine.add(Download(
                url=url, file_name=name,
                save_path=os.path.join(self.config.download_dir(), name),
                category=category_for_url(url)))
            log.info("clipboard URL auto-added: %s", url)
        else:
            if self._tray is not None:
                self._tray.notify_balloon("URL detected in clipboard",
                                          f"{url}\nOpen the Add dialog?")
            else:
                if QMessageBox.question(
                        self, "Clipboard URL",
                        f"A download URL was pasted:\n{url}\n\nAdd it?"
                ) == QMessageBox.StandardButton.Yes:
                    self.open_add_dialog(url)

    # ------------------------------------------------------------- window

    def set_tray(self, tray) -> None:
        self._tray = tray

    def show_from_tray(self) -> None:
        self.showNormal()
        self._hidden_to_tray = False
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._tray is not None and self.config.get_bool("minimize_to_tray") \
                and not self._quitting:
            self._hidden_to_tray = True
            self.hide()
            event.ignore()
            if self._tray is not None:
                self._tray.notify_balloon(
                    APP_NAME,
                    "IDM Pro is still running in the tray."
                    if self.engine.is_busy() else "Minimized to tray.")
            return
        self._real_cleanup(event)

    def real_quit(self) -> None:
        self._quitting = True
        self.close()

    def _real_cleanup(self, event: QCloseEvent) -> None:
        try:
            self.engine.stop()
        except Exception:  # noqa: BLE001
            pass
        event.accept()

    # --------------------------------------------------------- drag & drop

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        urls = []
        for u in event.mimeData().urls():
            if u.scheme() in ("http", "https", "ftp"):
                urls.append(u.toString())
        if not urls and event.mimeData().hasText():
            urls = extract_urls(event.mimeData().text())
        if not urls:
            return
        if len(urls) == 1:
            self.open_add_dialog(urls[0])
        else:
            self.paste_many(urls)
        event.acceptProposedAction()

    def paste_many(self, urls: list[str]) -> None:
        from core.url_parser import category_for_url, url_filename
        if QMessageBox.question(
                self, "Batch download",
                f"{len(urls)} URLs dropped.\nAdd them all to the queue?"
        ) != QMessageBox.StandardButton.Yes:
            return
        for url in urls:
            name = url_filename(url)
            self.engine.add(Download(
                url=url, file_name=name,
                save_path=os.path.join(self.config.download_dir(), name),
                category=category_for_url(url)))
