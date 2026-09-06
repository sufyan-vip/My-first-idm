"""
Shared fixtures: a real HTTP server that honours Range requests (so the
multi-segment engine can be exercised end-to-end locally), a scratch
database, and a Qt app instance for engine/UI tests.
"""

from __future__ import annotations

import io
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------
# Fake file content (deterministic, 1 MB)
# ---------------------------------------------------------------------------

FILE_CONTENT = bytes((i * 7 + 13) % 256 for i in range(1024 * 1024))
FILE_NAME = "testfile.bin"


class _Handler(BaseHTTPRequestHandler):
    """Serves FILE_CONTENT with full Range support + special routes."""

    def log_message(self, *args):  # silence
        pass

    def _send(self, code, body=b"", extra=None):
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    #: write delay applied to the /slow route (per 32 KiB) – makes
    #: pause/resume/cancel tests deterministic on a fast localhost
    SLOW_DELAY = 0.3

    def _route(self):
        path = self.path.split("?")[0]
        if path.startswith("/slow/files/" + FILE_NAME):
            return FILE_CONTENT, {"Content-Type": "application/octet-stream",
                                  "Content-Disposition":
                                  f'attachment; filename="{FILE_NAME}"',
                                  "slow": True}
        if path.startswith("/files/" + FILE_NAME) or path == "/default":
            return FILE_CONTENT, {"Content-Type": "application/octet-stream",
                                  "Content-Disposition":
                                  f'attachment; filename="{FILE_NAME}"'}
        if path == "/noranges/file.dat":
            return FILE_CONTENT, {"Content-Type": "application/octet-stream"}
        if path == "/flaky":
            return FILE_CONTENT, {}
        if path == "/404":
            return None, {"status": 404}
        if path == "/403":
            return None, {"status": 403}
        return None, {"status": 404}

    def _write_body(self, body, slow=False):
        if not slow:
            self.wfile.write(body)
            return
        step = 32 * 1024
        for i in range(0, len(body), step):
            self.wfile.write(body[i:i + step])
            self.wfile.flush()
            time.sleep(self.SLOW_DELAY)

    def do_HEAD(self):
        body, extra = self._route()
        if body is None:
            self.send_error(extra.get("status", 404))
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        path = self.path.split("?")[0]
        if not path.startswith("/noranges"):  # that route refuses ranges
            self.send_header("Accept-Ranges", "bytes")
        for k, v in extra.items():
            if k != "status":
                self.send_header(k, v)
        self.end_headers()

    def do_GET(self):
        body, extra = self._route()
        if body is None:
            self.send_error(extra.get("status", 404))
            return
        path = self.path.split("?")[0]

        if path == "/flaky":
            # fail the first N requests with a 503, then succeed
            state = self.server.flaky_hits
            with self.server.flaky_lock:
                self.server.flaky_hits += 1
                hits = self.server.flaky_hits
            if hits <= 1:
                self.send_error(503, "simulated outage")
                return

        if path == "/noranges/file.dat":
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            for k, v in extra.items():
                if k != "status":
                    self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
            return

        slow = extra.pop("slow", False)
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            try:
                start_s, end_s = rng[6:].split("-", 1)
                start = int(start_s)
                end = int(end_s) if end_s else len(body) - 1
                end = min(end, len(body) - 1)
                if start >= len(body):
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{len(body)}")
                    self.end_headers()
                    return
                chunk = body[start:end + 1]
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(body)}")
                self.send_header("Content-Length", str(len(chunk)))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                self._write_body(chunk, slow=slow)
                return
            except (ValueError, IndexError):
                pass
        # full body
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        for k, v in extra.items():
            if k != "status":
                self.send_header(k, v)
        self.end_headers()
        self._write_body(body, slow=slow)


def _find_free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def http_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.flaky_hits = 0
    server.flaky_lock = threading.Lock()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture()
def qt_app():
    """A single QApplication for the whole test session (offscreen).

    QApplication (not QCoreApplication) so that later UI fixtures can reuse
    it – Qt forbids creating a QGuiApplication after a bare
    QCoreApplication exists.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def env(tmp_path, qt_app):
    """Fresh database + config in a temp dir."""
    from database.db_manager import Database
    from utils.config import Config
    from utils.logger import setup_logging
    import logging

    db = Database(str(tmp_path / "test.db"))
    config = Config(db)
    config.set("download_folder", str(tmp_path / "downloads"))
    config.set("notifications_enabled", "false")
    config.set("sound_enabled", "false")
    config.set("clipboard_detect", "false")
    setup_logging(str(tmp_path / "logs"), logging.DEBUG, console=False)
    yield {"db": db, "config": config, "tmp": tmp_path, "app": qt_app}
    db.close()


def wait_until(predicate, timeout: float = 45.0, interval: float = 0.05) -> bool:
    """Spin the Qt event loop until *predicate* is true (or timeout)."""
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if app is not None:
            app.processEvents()
        if predicate():
            return True
        time.sleep(interval)
    return False
