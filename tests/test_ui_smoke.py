"""
Offscreen UI smoke tests: the main window must boot, render rows for a real
download, update the status bar, and survive close.  Runs headless with
QT_QPA_PLATFORM=offscreen.
"""

from __future__ import annotations

import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conftest import wait_until  # noqa: E402


@pytest.fixture()
def full_ui(env, http_server):
    """Main window + engine + tray-less config, ready with a live download."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    from core.download_engine import DownloadEngine
    from ui.main_window import MainWindow

    engine = DownloadEngine(env["db"], env["config"])
    engine.start()
    window = MainWindow(env["db"], env["config"], engine)
    window.show()
    app.processEvents()
    yield {"app": app, "window": window, "engine": engine,
           "http_server": http_server}
    window._quitting = True
    window.close()
    engine.stop()


def test_window_boots_with_all_regions(full_ui):
    w = full_ui["window"]
    assert w.menuBar() is not None
    assert w.centralWidget() is not None
    assert w.statusBar() is not None
    assert w.list is not None
    assert w.tabs is not None
    # menu must contain the expected top-level items
    titles = [a.text() for a in w.menuBar().actions()]
    assert any("File" in t for t in titles)
    assert any("Downloads" in t for t in titles)
    assert any("Help" in t for t in titles)


def test_live_download_renders_row(full_ui):
    """Add a real download and verify a row appears + completes."""
    from database.models import Download
    from utils.constants import Status
    from conftest import FILE_NAME

    app, window, engine = full_ui["app"], full_ui["window"], full_ui["engine"]
    url = full_ui["http_server"] + "/files/" + FILE_NAME

    dl = Download(url=url, file_name=FILE_NAME,
                  save_path=os.path.join(str(full_ui["window"].config
                                             .download_dir()), FILE_NAME))
    engine.add(dl)

    ok = wait_until(lambda: len(window._rows) >= 1, timeout=10)
    assert ok, "no download row appeared in the list"

    def done():
        return all(
            s.status in (Status.COMPLETED, Status.FAILED)
            for s in engine.all_snapshots())
    assert wait_until(done, timeout=30), "download never finished"

    # row widget should show completion state
    snap = engine.all_snapshots()[0]
    assert snap.status == Status.COMPLETED
    row = list(window._rows.values())[0]
    assert "Done" in row.chip_label.text()

    # status bar updated with counts
    assert "done" in window.status_counts.text()


def test_tabs_filtering(full_ui):
    from database.models import Download
    from utils.constants import Status
    from conftest import FILE_NAME

    app, window, engine = full_ui["app"], full_ui["window"], full_ui["engine"]
    url = full_ui["http_server"] + "/files/" + FILE_NAME
    dl = Download(url=url, file_name=FILE_NAME,
                  save_path=os.path.join(window.config.download_dir(), FILE_NAME))
    engine.add(dl)

    # switch to Failed tab → nothing visible (download will succeed)
    window.tabs.set_filter("failed")
    window.refresh()
    assert window.list.count() == 0

    window.tabs.set_filter("all")
    ok = wait_until(lambda: window.list.count() >= 1, timeout=10)
    assert ok


def test_search_filter(full_ui):
    from database.models import Download
    from conftest import FILE_NAME

    app, window, engine = full_ui["app"], full_ui["window"], full_ui["engine"]
    url = full_ui["http_server"] + "/files/" + FILE_NAME
    dl = Download(url=url, file_name=FILE_NAME,
                  save_path=os.path.join(window.config.download_dir(), FILE_NAME))
    engine.add(dl)
    assert wait_until(lambda: window.list.count() >= 1, timeout=10)

    window.search_edit.setText("nonexistent-file-zzz")
    window.refresh()
    assert window.list.count() == 0
    window.search_edit.setText("")
    window.refresh()
    assert window.list.count() >= 1


def test_history_window_opens(full_ui):
    app, window = full_ui["app"], full_ui["window"]
    window.open_history()
    assert window._history_window is not None
    assert window._history_window.isVisible()
    window._history_window.close()


def test_settings_dialog_opens(full_ui):
    app, window = full_ui["app"], full_ui["window"]
    from ui.settings_dialog import SettingsDialog

    dlg = SettingsDialog(window.config, window.engine, window)
    assert dlg.theme_combo is not None
    assert dlg.concurrent_spin.value() >= 1
    dlg.reject()


def test_details_dialog(full_ui):
    from conftest import FILE_NAME
    from database.models import Download
    app, window, engine = full_ui["app"], full_ui["window"], full_ui["engine"]
    url = full_ui["http_server"] + "/files/" + FILE_NAME
    dl = Download(url=url, file_name=FILE_NAME,
                  save_path=os.path.join(window.config.download_dir(), FILE_NAME))
    dl_id = engine.add(dl)
    assert wait_until(lambda: window.list.count() >= 1, timeout=10)

    from ui.main_window import DetailsDialog
    snap = engine.snapshot(dl_id)
    dlg = DetailsDialog(snap, window)
    assert dlg is not None
    dlg.close()


def test_speed_graph_receives_data(full_ui):
    from conftest import FILE_NAME
    from database.models import Download
    app, window, engine = full_ui["app"], full_ui["window"], full_ui["engine"]
    url = full_ui["http_server"] + "/files/" + FILE_NAME
    dl = Download(url=url, file_name=FILE_NAME,
                  save_path=os.path.join(window.config.download_dir(), FILE_NAME))
    engine.add(dl)
    assert wait_until(
        lambda: len(window.graph._values) > 0, timeout=10)


def test_window_close_without_tray(full_ui):
    """Close event must accept when no tray (real cleanup path)."""
    window = full_ui["window"]
    window._quitting = True
    window.close()
    assert not window.isVisible()
