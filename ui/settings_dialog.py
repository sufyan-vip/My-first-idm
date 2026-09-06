"""
Settings / preferences dialog (General, Connection, Notifications tabs).
All values are written back to the ``Config`` on OK/Close.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.proxy_handler import test_connection
from utils.constants import Category, PostAction, POST_ACTION_LABELS
from utils.logger import get_logger

log = get_logger("ui.settings")


class SettingsDialog(QDialog):
    def __init__(self, config, engine, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.engine = engine
        self.setWindowTitle("⚙ Settings")
        self.resize(640, 540)

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._general_tab(), "General")
        tabs.addTab(self._connection_tab(), "Connection")
        tabs.addTab(self._notifications_tab(), "Notifications")
        layout.addWidget(tabs, 1)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Save")
        ok.setObjectName("primaryButton")
        ok.clicked.connect(self._save)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        layout.addLayout(btns)

    # ---------------------------------------------------------------- tabs

    def _general_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        general = QGroupBox("Downloads")
        g = QFormLayout(general)
        folder_row = QHBoxLayout()
        self.folder_edit = QLineEdit(self.config.get("download_folder"))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_folder)
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(browse)
        g.addRow("Default folder:", self._wrap(folder_row))

        self.categorize_check = QCheckBox("Categorize into subfolders (Documents/Music/…)")
        self.categorize_check.setChecked(self.config.get_bool("categorize"))
        g.addRow(self.categorize_check)

        self.concurrent_spin = QSpinBox()
        self.concurrent_spin.setRange(1, 10)
        self.concurrent_spin.setValue(
            max(1, min(10, self.config.get_int("max_concurrent", 3))))
        g.addRow("Max simultaneous downloads:", self.concurrent_spin)

        self.segments_spin = QSpinBox()
        self.segments_spin.setRange(1, 32)
        self.segments_spin.setValue(
            max(1, min(32, self.config.get_int("max_segments", 8))))
        g.addRow("Default segments per download:", self.segments_spin)

        limit_row = QHBoxLayout()
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(0, 100000)
        self.limit_spin.setSpecialValueText("Unlimited")
        self.limit_spin.setValue(self.config.get_int("speed_limit"))
        self.limit_unit = QComboBox()
        self.limit_unit.addItems(["KB/s", "MB/s", "GB/s"])
        self.limit_unit.setCurrentIndex(
            ["KB", "MB", "GB"].index(self.config.get("speed_limit_unit", "MB")))
        limit_row.addWidget(self.limit_spin, 1)
        limit_row.addWidget(self.limit_unit)
        g.addRow("Global speed limit:", self._wrap(limit_row))
        v.addWidget(general)

        app = QGroupBox("Application")
        a = QFormLayout(app)
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Dark", "dark")
        self.theme_combo.addItem("Light", "light")
        self.theme_combo.addItem("Follow system", "system")
        self.theme_combo.setCurrentIndex(self.theme_combo.findData(
            self.config.get("theme", "dark")))
        a.addRow("Theme:", self.theme_combo)

        self.tray_check = QCheckBox("Minimize to system tray")
        self.tray_check.setChecked(self.config.get_bool("minimize_to_tray"))
        a.addRow(self.tray_check)

        self.autostart_check = QCheckBox("Start with Windows (auto-start)")
        self.autostart_check.setChecked(self.config.get_bool("auto_start"))
        a.addRow(self.autostart_check)

        self.portable_check = QCheckBox("Portable mode (store data next to the app)")
        self.portable_check.setChecked(self.config.get_bool("portable"))
        a.addRow(self.portable_check)

        self.clip_check = QCheckBox("Detect URLs pasted to the clipboard")
        self.clip_check.setChecked(self.config.get_bool("clipboard_detect"))
        a.addRow(self.clip_check)

        self.clip_auto_check = QCheckBox("… and add them to the queue automatically")
        self.clip_auto_check.setChecked(self.config.get_bool("clipboard_auto_add"))
        a.addRow(self.clip_auto_check)

        self.autoresum_check = QCheckBox("Resume incomplete downloads at startup")
        self.autoresum_check.setChecked(self.config.get_bool("auto_resume"))
        a.addRow(self.autoresum_check)
        v.addWidget(app)

        self.duplicate_combo = QComboBox()
        self.duplicate_combo.addItem("Ask nothing – rename automatically (file (1).ext)", "rename")
        self.duplicate_combo.addItem("Overwrite existing file", "overwrite")
        self.duplicate_combo.addItem("Keep existing file (skip)", "skip")
        self.duplicate_combo.setCurrentIndex(self.duplicate_combo.findData(
            self.config.get("duplicate_policy", "rename")))
        row = QHBoxLayout()
        row.addWidget(QLabel("When a file already exists:"))
        row.addWidget(self.duplicate_combo, 1)
        row.addStretch(1)
        v.addLayout(row)
        v.addStretch(1)
        return w

    def _connection_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        conn = QGroupBox("Connection")
        c = QFormLayout(conn)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(5, 300)
        self.timeout_spin.setSuffix(" s")
        self.timeout_spin.setValue(self.config.get_int("timeout", 30))
        c.addRow("Read timeout:", self.timeout_spin)

        self.retries_spin = QSpinBox()
        self.retries_spin.setRange(0, 20)
        self.retries_spin.setValue(self.config.get_int("retries", 5))
        c.addRow("Retries per segment:", self.retries_spin)

        self.ua_edit = QLineEdit(self.config.get("user_agent"))
        self.ua_edit.setPlaceholderText("leave empty for the built-in browser UA")
        c.addRow("Custom User-Agent:", self.ua_edit)
        v.addWidget(conn)

        proxy = QGroupBox("Proxy")
        p = QFormLayout(proxy)
        self.proxy_type = QComboBox()
        self.proxy_type.addItem("No proxy", "none")
        self.proxy_type.addItem("HTTP proxy", "http")
        self.proxy_type.addItem("SOCKS5 proxy", "socks5")
        self.proxy_type.setCurrentIndex(self.proxy_type.findData(
            self.config.get("proxy_type", "none")))
        p.addRow("Type:", self.proxy_type)

        self.proxy_host = QLineEdit(self.config.get("proxy_host"))
        self.proxy_host.setPlaceholderText("192.168.1.1")
        p.addRow("Host:", self.proxy_host)

        self.proxy_port = QLineEdit(str(self.config.get("proxy_port")))
        self.proxy_port.setPlaceholderText("1080")
        p.addRow("Port:", self.proxy_port)

        self.proxy_user = QLineEdit(self.config.get("proxy_user"))
        p.addRow("Username:", self.proxy_user)

        self.proxy_pass = QLineEdit(self.config.get("proxy_password"))
        self.proxy_pass.setEchoMode(QLineEdit.EchoMode.Password)
        p.addRow("Password:", self.proxy_pass)

        test_row = QHBoxLayout()
        self.test_proxy_btn = QPushButton("🔌 Test proxy connection")
        self.test_proxy_btn.clicked.connect(self._test_proxy)
        self.proxy_status = QLabel("")
        self.proxy_status.setObjectName("secondaryText")
        test_row.addWidget(self.test_proxy_btn)
        test_row.addWidget(self.proxy_status, 1)
        p.addRow(self._wrap(test_row))
        v.addWidget(proxy)
        v.addStretch(1)
        return w

    def _notifications_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        box = QGroupBox("Notifications & post-download actions")
        b = QFormLayout(box)
        self.notify_check = QCheckBox("Desktop notification on completion / failure")
        self.notify_check.setChecked(self.config.get_bool("notifications_enabled"))
        b.addRow(self.notify_check)

        self.sound_check = QCheckBox("Play a sound on completion")
        self.sound_check.setChecked(self.config.get_bool("sound_enabled"))
        b.addRow(self.sound_check)

        self.post_action_combo = QComboBox()
        for key, label in POST_ACTION_LABELS.items():
            self.post_action_combo.addItem(label, key)
        self.post_action_combo.setCurrentIndex(self.post_action_combo.findData(
            self.config.get("post_action", PostAction.NONE)))
        b.addRow("After each download:", self.post_action_combo)

        self.av_check = QCheckBox("Run Windows Defender scan on completed files")
        self.av_check.setChecked(self.config.get_bool("antivirus_scan"))
        b.addRow(self.av_check)
        v.addWidget(box)

        note = QLabel(
            "💡 “Shut down PC” triggers when the LAST active download finishes "
            "and the queue is empty (30-second delay, so you can cancel it).")
        note.setObjectName("dim")
        note.setWordWrap(True)
        v.addWidget(note)
        v.addStretch(1)
        return w

    # -------------------------------------------------------------- events

    @staticmethod
    def _wrap(row: QHBoxLayout) -> QWidget:
        w = QWidget()
        w.setLayout(row)
        return w

    def _browse_folder(self) -> None:
        from PyQt6.QtWidgets import QFileDialog

        folder = QFileDialog.getExistingDirectory(self, "Choose default folder")
        if folder:
            self.folder_edit.setText(folder)

    def _test_proxy(self) -> None:
        self.proxy_status.setText("Testing…")
        ok, message = test_connection(
            self.proxy_type.currentData(),
            self.proxy_host.text().strip(),
            self.proxy_port.text().strip(),
            self.proxy_user.text().strip(),
            self.proxy_pass.text().strip(),
        )
        self.proxy_status.setObjectName("successText" if ok else "errorText")
        self.proxy_status.setText(("✅ " if ok else "✖ ") + message)

    # ---------------------------------------------------------------- save

    def _save(self) -> None:
        c = self.config
        c.set("download_folder", self.folder_edit.text().strip())
        c.set("categorize", "true" if self.categorize_check.isChecked() else "false")
        c.set("max_concurrent", str(self.concurrent_spin.value()))
        c.set("max_segments", str(self.segments_spin.value()))
        c.set("speed_limit", str(self.limit_spin.value()))
        c.set("speed_limit_unit", ["KB", "MB", "GB"][self.limit_unit.currentIndex()])
        c.set("theme", self.theme_combo.currentData())
        c.set("minimize_to_tray", "true" if self.tray_check.isChecked() else "false")
        c.set("auto_start", "true" if self.autostart_check.isChecked() else "false")
        c.set("portable", "true" if self.portable_check.isChecked() else "false")
        c.set("clipboard_detect", "true" if self.clip_check.isChecked() else "false")
        c.set("clipboard_auto_add", "true" if self.clip_auto_check.isChecked() else "false")
        c.set("auto_resume", "true" if self.autoresum_check.isChecked() else "false")
        c.set("duplicate_policy", self.duplicate_combo.currentData())

        c.set("timeout", str(self.timeout_spin.value()))
        c.set("retries", str(self.retries_spin.value()))
        c.set("user_agent", self.ua_edit.text().strip())

        c.set("proxy_type", self.proxy_type.currentData())
        c.set("proxy_host", self.proxy_host.text().strip())
        c.set("proxy_port", self.proxy_port.text().strip())
        c.set("proxy_user", self.proxy_user.text().strip())
        c.set("proxy_password", self.proxy_pass.text())

        c.set("notifications_enabled",
              "true" if self.notify_check.isChecked() else "false")
        c.set("sound_enabled", "true" if self.sound_check.isChecked() else "false")
        c.set("post_action", self.post_action_combo.currentData())
        c.set("antivirus_scan", "true" if self.av_check.isChecked() else "false")

        # apply runtime-affecting settings immediately
        self.engine.on_settings_changed("speed_limit", "")
        try:
            self.config.ensure_autostart(self.autostart_check.isChecked())
        except Exception as exc:  # noqa: BLE001
            log.warning("autostart setting failed: %s", exc)

        log.info("settings saved")
        self.accept()
