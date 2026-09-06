"""
"Add New Download" dialog.

Probes the URL in a background thread (file name, size, ranges support),
auto-detects the category, and offers advanced options: segments, priority,
schedule, authentication, referer, custom headers and user-agent.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Optional

from PyQt6.QtCore import QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.protocol_handler import ProbeError, ProbeResult, probe_url
from core.scheduler import to_db_time
from core.url_parser import (
    category_for_url,
    filename_with_extension,
    is_valid_url,
    normalize_filename,
    strip_url,
    url_filename,
)
from database.models import Download
from utils.constants import (
    CATEGORY_INFO,
    Category,
    Priority,
    Status,
    MAX_SEGMENTS,
)
from utils.file_utils import human_size
from utils.logger import get_logger

log = get_logger("ui.add")


class _ProbeWorker(QThread):
    finished = pyqtSignal(object)  # ProbeResult | str (error)

    def __init__(self, url: str, user_agent: str, parent=None) -> None:
        super().__init__(parent)
        self._url = url
        self._user_agent = user_agent

    def run(self) -> None:
        try:
            result = probe_url(self._url, timeout=20, user_agent=self._user_agent)
            self.finished.emit(result)
        except ProbeError as exc:
            self.finished.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.finished.emit(f"Could not reach the server: {exc.__class__.__name__}")


class AddDownloadDialog(QDialog):
    """Collects everything needed to create a new download."""

    download_requested = pyqtSignal(object, bool)  # (Download, start_now)

    def __init__(self, config, default_folder: str, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self._probe: Optional[ProbeResult] = None
        self._probe_worker: Optional[_ProbeWorker] = None
        self._probe_for_url: str = ""
        self._name_autofilled: bool = True
        self.setWindowTitle("➕ Add New Download")
        self.setMinimumWidth(620)
        self._build_ui(default_folder)
        self._probe_timer = QTimer(self)
        self._probe_timer.setSingleShot(True)
        self._probe_timer.setInterval(800)
        self._probe_timer.timeout.connect(self._maybe_probe)

    # ------------------------------------------------------------------- UI

    def _build_ui(self, default_folder: str) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        # ------------------------------------------------------------- URL
        url_row = QHBoxLayout()
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText(
            "https://example.com/files/big-file.zip  (or FTP: ftp://…)")
        self.url_edit.textChanged.connect(lambda _t: self._url_changed())
        paste_btn = QPushButtonFactory(self._paste_from_clipboard, "📋 Paste")
        url_row.addWidget(self.url_edit, 1)
        url_row.addWidget(paste_btn)
        root.addLayout(url_row)

        self.status_label = QLabel("")
        self.status_label.setObjectName("secondaryText")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        # --------------------------------------------------- file & folder
        form = QFormLayout()
        form.setSpacing(10)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("auto-detected from URL")
        form.addRow("File name:", self.name_edit)

        save_row = QHBoxLayout()
        self.folder_edit = QLineEdit(default_folder)
        self.folder_edit.setReadOnly(True)
        browse_btn = QPushButtonFactory(self._choose_folder, "📁 Browse…")
        save_row.addWidget(self.folder_edit, 1)
        save_row.addWidget(browse_btn)
        form.addRow("Save to:", self._wrap(save_row))

        self.category_combo = QComboBox()
        self.category_combo.addItem("Auto-detect", Category.AUTO)
        for key, info in CATEGORY_INFO.items():
            if key == Category.AUTO:
                continue
            self.category_combo.addItem(f"{info['emoji']}  {info['label']}", key)
        self.category_combo.currentIndexChanged.connect(self._category_changed)
        form.addRow("Category:", self.category_combo)
        root.addLayout(form)

        # ------------------------------------------------------ advanced
        self.advanced_box = QGroupBox("Advanced Options")
        adv_layout = QFormLayout(self.advanced_box)
        adv_layout.setSpacing(10)

        seg_row = QHBoxLayout()
        self.segments_spin = QSpinBox()
        self.segments_spin.setRange(1, MAX_SEGMENTS)
        self.segments_spin.setValue(
            max(1, self.config.get_int("max_segments", 8)))
        seg_hint = QLabel("auto: chosen by file size when 0")
        seg_hint.setObjectName("dim")
        seg_row.addWidget(self.segments_spin)
        seg_row.addWidget(seg_hint, 1)
        adv_layout.addRow("Segments:", self._wrap(seg_row))

        prio_row = QHBoxLayout()
        self._prio_group = QButtonGroup(self)
        for key, label in ((Priority.HIGH, "High"),
                           (Priority.MEDIUM, "Medium"),
                           (Priority.LOW, "Low")):
            rb = QRadioButton(label)
            rb.setChecked(key == Priority.MEDIUM)
            self._prio_group.addButton(rb)
            prio_row.addWidget(rb)
        prio_row.addStretch(1)
        adv_layout.addRow("Priority:", self._wrap(prio_row))

        sched_row = QHBoxLayout()
        self.now_radio = QRadioButton("Now")
        self.later_radio = QRadioButton("Later")
        sched_row.addWidget(self.now_radio)
        sched_row.addWidget(self.later_radio)
        self.date_edit = QDateEdit(datetime.now().date())
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setEnabled(False)
        self.time_edit = QTimeEdit(datetime.now().time())
        self.time_edit.setEnabled(False)
        self.now_radio.toggled.connect(self._schedule_mode)
        self.later_radio.toggled.connect(self._schedule_mode)
        sched_row.addWidget(self.date_edit)
        sched_row.addWidget(self.time_edit)
        sched_row.addStretch(1)
        self.now_radio.setChecked(True)
        adv_layout.addRow("Schedule:", self._wrap(sched_row))

        auth_row = QHBoxLayout()
        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText("username (for 401 pages)")
        self.pass_edit = QLineEdit()
        self.pass_edit.setPlaceholderText("password")
        self.pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        auth_row.addWidget(self.user_edit, 1)
        auth_row.addWidget(self.pass_edit, 1)
        adv_layout.addRow("Auth:", self._wrap(auth_row))

        self.referer_edit = QLineEdit()
        self.referer_edit.setPlaceholderText("https://origin-site/page (optional)")
        adv_layout.addRow("Referer:", self.referer_edit)

        self.ua_edit = QLineEdit()
        self.ua_edit.setPlaceholderText("custom User-Agent (optional)")
        adv_layout.addRow("User-Agent:", self.ua_edit)

        self.headers_edit = QPlainTextEdit()
        self.headers_edit.setFixedHeight(64)
        self.headers_edit.setPlaceholderText(
            "Custom headers, one per line:\nCookie: session=abc123\nX-Token: xyz")
        adv_layout.addRow("Headers:", self.headers_edit)

        self.advanced_box.setCheckable(True)
        self.advanced_box.setChecked(False)
        root.addWidget(self.advanced_box)

        # ---------------------------------------------------------- buttons
        buttons = QHBoxLayout()
        cancel = QPushButtonFactory(self.reject, "Cancel")
        queue_btn = QPushButtonFactory(self._add_to_queue, "⏳ Add to Queue")
        start_btn = QPushButtonFactory(self._start_now, "⬇ Start Download")
        start_btn.setObjectName("primaryButton")
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(queue_btn)
        buttons.addWidget(start_btn)
        root.addLayout(buttons)

    @staticmethod
    def _wrap(row: QHBoxLayout) -> QWidget:
        w = QWidget()
        w.setLayout(row)
        return w

    # ------------------------------------------------------------ events

    def _url_changed(self) -> None:
        self.status_label.setText("")
        self._probe_timer.start()

    def _maybe_probe(self) -> None:
        url = strip_url(self.url_edit.text())
        if not is_valid_url(url) or url == self._probe_for_url:
            return
        self._probe_for_url = url
        if self._probe_worker and self._probe_worker.isRunning():
            self._probe_worker.wait(3000)
        self._probe = None
        self.status_label.setText("🔎 Probing URL…")
        self._probe_worker = _ProbeWorker(
            url,
            self.config.get("user_agent") or "",
            self,
        )
        self._probe_worker.finished.connect(self._on_probed)
        self._probe_worker.start()

    def _on_probed(self, result) -> None:
        url = strip_url(self.url_edit.text())
        if isinstance(result, ProbeResult):
            self._probe = result
            # only auto-fill the name field when the user left it empty
            if not self.name_edit.text().strip():
                candidate = filename_with_extension(
                    result.filename or url_filename(url), result.content_type)
                self.name_edit.setText(candidate)
            self._auto_category()
            size = f"{human_size(result.size)}" if result.size > 0 else "unknown size"
            ranges = "multi-segment OK" if result.accept_ranges else "single stream"
            self.status_label.setObjectName("successText")
            self.status_label.setText(
                f"✅ {result.url}\n   {size}  •  {ranges}"
                f"  •  {result.content_type or 'content-type unknown'}")
        else:
            self._probe = None
            self.status_label.setObjectName("errorText")
            self.status_label.setText(f"⚠ {result}")

    def _auto_category(self) -> None:
        url = strip_url(self.url_edit.text())
        name = self.name_edit.text().strip() or url_filename(url)
        ctype = self._probe.content_type if self._probe else ""
        key = category_for_url(url, ctype) if not os.path.splitext(name)[1] else \
            category_from_name(name)
        idx = self.category_combo.findData(key)
        if idx >= 0:
            self.category_combo.blockSignals(True)
            self.category_combo.setCurrentIndex(idx)
            self.category_combo.blockSignals(False)

    def _category_changed(self, _index: int) -> None:
        # switching away from auto doesn't change anything else; kept for API
        pass

    def _schedule_mode(self, later: bool) -> None:
        self.date_edit.setEnabled(later)
        self.time_edit.setEnabled(later)

    def _choose_folder(self) -> None:
        from PyQt6.QtWidgets import QFileDialog

        folder = QFileDialog.getExistingDirectory(
            self, "Choose download folder", self.folder_edit.text())
        if folder:
            self.folder_edit.setText(folder)

    def _paste_from_clipboard(self) -> None:
        from PyQt6.QtWidgets import QApplication

        text = QApplication.clipboard().text().strip()
        if text:
            from core.url_parser import extract_urls
            urls = extract_urls(text)
            if urls:
                self.url_edit.setText(urls[0])
                self._paste_urls = urls  # used by callers via urls property
            else:
                self.url_edit.setText(text)
                self.status_label.setObjectName("errorText")
                self.status_label.setText(
                    "⚠ Could not find a valid http/https/ftp URL in clipboard")

    # ------------------------------------------------------------ actions

    def _collect_headers(self) -> str:
        lines = self.headers_edit.toPlainText().strip()
        if not lines:
            return ""
        data = {}
        for line in lines.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                data[k.strip()] = v.strip()
        return json.dumps(data) if data else ""

    def _selected_priority(self) -> str:
        for btn in self._prio_group.buttons():
            if btn.isChecked():
                return {
                    "High": Priority.HIGH,
                    "Medium": Priority.MEDIUM,
                    "Low": Priority.LOW,
                }[btn.text()]
        return Priority.MEDIUM

    def _build_download(self, status: str) -> Download:
        url = strip_url(self.url_edit.text())
        name = normalize_filename(self.name_edit.text() or url_filename(url))
        folder = self.folder_edit.text().strip() or self.config.download_dir()
        if self.category_combo.currentData() == Category.AUTO:
            category = category_from_name(name)
        else:
            category = self.category_combo.currentData()
        final_path = os.path.join(folder, name)

        scheduled = None
        if self.later_radio.isChecked():
            when = datetime.combine(self.date_edit.date().toPyDateTime().date(),
                                    self.time_edit.time().toPyDateTime().time())
            if when < datetime.now():
                when += timedelta(hours=1)
            scheduled = to_db_time(when)
            status = Status.SCHEDULED

        segments = self.segments_spin.value()
        if self._probe and not self._probe.accept_ranges:
            segments = 1

        return Download(
            url=url,
            file_name=name,
            save_path=final_path,
            file_size=self._probe.size if (self._probe and self._probe.size > 0) else 0,
            status=status,
            priority=self._selected_priority(),
            segments=segments,
            category=category,
            scheduled_time=scheduled,
            user_agent=self.ua_edit.text().strip(),
            custom_headers=self._collect_headers(),
            auth_user=self.user_edit.text().strip(),
            auth_pass=self.pass_edit.text().strip(),
            referer=self.referer_edit.text().strip(),
            post_action=self.config.get("post_action", "none"),
            protocol=self._probe.protocol if self._probe else "http",
            ranges_supported=bool(self._probe.accept_ranges) if self._probe else True,
        )

    def _validate(self) -> Optional[str]:
        url = strip_url(self.url_edit.text())
        if not is_valid_url(url):
            return "Please enter a valid http://, https:// or ftp:// URL"
        if not self.name_edit.text().strip():
            self.name_edit.setText(url_filename(url))
        if not os.path.isdir(self.folder_edit.text().strip()):
            return "The save folder does not exist – choose a valid folder"
        return None

    def _start_now(self) -> None:
        error = self._validate()
        if error:
            self.status_label.setObjectName("errorText")
            self.status_label.setText(f"⚠ {error}")
            return
        self.download_requested.emit(self._build_download(Status.QUEUED), True)
        self.accept()

    def _add_to_queue(self) -> None:
        error = self._validate()
        if error:
            self.status_label.setObjectName("errorText")
            self.status_label.setText(f"⚠ {error}")
            return
        self.download_requested.emit(self._build_download(Status.QUEUED), False)
        self.accept()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._probe_worker and self._probe_worker.isRunning():
            self._probe_worker.wait(3000)
        super().closeEvent(event)


def category_from_name(name: str) -> str:
    from utils.file_utils import category_for_extension

    return category_for_extension(name)


def QPushButtonFactory(callback, text: str):
    """Tiny helper: a QPushButton wired to *callback* (keeps layout code tidy)."""
    from PyQt6.QtWidgets import QPushButton

    btn = QPushButton(text)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.clicked.connect(callback)
    return btn
