"""About dialog with app identity, version and credits."""

from __future__ import annotations

import os
import sys

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from utils.constants import APP_NAME, APP_ORG, APP_VERSION, IS_FROZEN


def _resource(*parts: str) -> str:
    if IS_FROZEN:
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


class AboutDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About")
        self.setFixedSize(460, 420)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 26, 28, 22)
        root.setSpacing(10)

        top = QHBoxLayout()
        top.addStretch(1)

        icon = QLabel()
        pixmap_path = _resource("ui", "resources", "app_icon.png")
        if os.path.isfile(pixmap_path):
            from PyQt6.QtGui import QPixmap

            icon.setPixmap(QPixmap(pixmap_path).scaled(
                84, 84, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        top.addWidget(icon, 0, Qt.AlignmentFlag.AlignCenter)
        top.addStretch(1)
        root.addLayout(top)

        title = QLabel(APP_NAME)
        title.setObjectName("windowTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(title)

        version = QLabel(f"Version {APP_VERSION}  •  {APP_ORG}")
        version.setObjectName("dim")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(version)

        desc = QLabel(
            "High-speed, multi-threaded download manager for Windows.\n"
            "Splits files into up to 32 parallel segments, resumes broken\n"
            "transfers, queues & schedules downloads, and keeps a full\n"
            "history – all in a single portable .exe."
        )
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setWordWrap(True)
        root.addWidget(desc)
        root.addSpacing(6)

        tech = QLabel("Python 3.11  •  PyQt6  •  SQLite  •  PyInstaller")
        tech.setObjectName("dim")
        tech.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(tech)

        license_lbl = QLabel("Licensed under the MIT License.")
        license_lbl.setObjectName("dim")
        license_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(license_lbl)

        # python version + runtime info
        info = QLabel(
            f"Runtime: Python {sys.version.split()[0]}   |   "
            f"Data dir: {self._data_dir_hint()}")
        info.setObjectName("dim")
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info.setWordWrap(True)
        root.addWidget(info)
        root.addStretch(1)

        btns = QHBoxLayout()
        site = QPushButton("Project repository")
        site.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl("https://github.com/sufyan-vip/My-first-idm")))
        close = QPushButton("Close")
        close.setObjectName("primaryButton")
        close.clicked.connect(self.accept)
        btns.addStretch(1)
        btns.addWidget(site)
        btns.addWidget(close)
        btns.addStretch(1)
        root.addLayout(btns)

    def _data_dir_hint(self) -> str:
        try:
            from utils.constants import system_appdata
            return system_appdata()
        except Exception:
            return "—"
