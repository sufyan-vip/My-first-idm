"""Shared application icons.

Uses Qt's native icon theme / standard pixmaps instead of emoji text so the
UI looks consistent on Windows, Linux and macOS.
"""

from __future__ import annotations

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QStyle


_STANDARD_ICONS = {
    "add": "SP_FileDialogNewFolder",
    "paste": "SP_DialogOpenButton",
    "file": "SP_FileIcon",
    "folder": "SP_DirIcon",
    "pause": "SP_MediaPause",
    "resume": "SP_MediaPlay",
    "cancel": "SP_DialogCancelButton",
    "delete": "SP_TrashIcon",
    "history": "SP_FileDialogDetailedView",
    "settings": "SP_FileDialogInfoView",
    "info": "SP_MessageBoxInformation",
    "warning": "SP_MessageBoxWarning",
    "error": "SP_MessageBoxCritical",
    "ok": "SP_DialogApplyButton",
    "search": "SP_FileDialogContentsView",
    "network": "SP_DriveNetIcon",
    "speed": "SP_ComputerIcon",
    "download": "SP_DialogSaveButton",
    "upload": "SP_ArrowUp",
    "refresh": "SP_BrowserReload",
    "close": "SP_DialogCloseButton",
}

_CATEGORY_ICONS = {
    "documents": "file",
    "music": "file",
    "videos": "file",
    "images": "file",
    "compressed": "folder",
    "programs": "settings",
    "others": "folder",
}


def icon(name: str) -> QIcon:
    """Return a native Qt icon by semantic name."""
    app = QApplication.instance()
    if app is None:
        return QIcon()
    style = app.style()
    attr = _STANDARD_ICONS.get(name, "SP_FileIcon")
    pixmap = getattr(QStyle.StandardPixmap, attr, QStyle.StandardPixmap.SP_FileIcon)
    return style.standardIcon(pixmap)


def category_icon(category: str) -> QIcon:
    return icon(_CATEGORY_ICONS.get(category, "folder"))
