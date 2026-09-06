# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller specification for InternetDownloadManager.

Builds a single, self-contained, windowed executable:

    pyinstaller idm.spec --noconfirm

The result is a single, portable ``build/dist/InternetDownloadManager.exe``
– run it directly, or wrap it with ``build/installer.nsi`` (NSIS) for a
desktop-shortcut + uninstaller setup package.

Notes
-----
* The app resolves its resources (themes, icons, sounds) through
  ``sys._MEIPASS`` when frozen, so ``ui/themes`` and ``ui/resources`` are
  bundled as data files.
* ``PyQt6.QtMultimedia`` and plyer's Windows notification plugin are imported
  lazily at runtime, so they are listed as hidden imports.
* A console window is suppressed (tray application); the CLI mode documented
  in the README is intended for source runs (``python main.py --url …``).
"""

from PyInstaller.utils.hooks import collect_submodules

APP_NAME = "InternetDownloadManager"

hiddenimports = [
    # lazily imported Qt modules
    "PyQt6.QtMultimedia",
    # plyer discovers its platform plugin dynamically
    "plyer",
    *collect_submodules("plyer.platforms.win"),
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("ui/themes", "ui/themes"),
        ("ui/resources", "ui/resources"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pandas", "pygame"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # tray GUI – no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="build/app_icon.ico",
)
