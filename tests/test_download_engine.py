"""
END-TO-END tests for the multi-threaded download engine against the local
Range-capable HTTP server (see conftest.py).

Covers: simple download, multi-segment download, pause/resume,
resume-after-restart, 404 handling, bandwidth limiting and retry logic.
"""

from __future__ import annotations

import hashlib
import os
import time

import pytest

from core.download_engine import DownloadEngine
from database.models import Download
from utils.constants import Status

from conftest import FILE_CONTENT, FILE_NAME, wait_until

EXPECTED_SHA256 = hashlib.sha256(FILE_CONTENT).hexdigest()


def _make_engine(env):
    engine = DownloadEngine(env["db"], env["config"])
    engine.start()
    return engine


def _add(env, engine, url, segments=8, folder=None):
    name = os.path.basename(url) or FILE_NAME
    folder = folder or str(env["tmp"] / "downloads")
    dl = Download(url=url, file_name=name,
                  save_path=os.path.join(folder, name), segments=segments)
    return engine.add(dl)


def _status(env, dl_id):
    return env["db"].get_download(dl_id).status


def test_simple_download_completes_with_correct_data(http_server, env):
    engine = _make_engine(env)
    dl_id = _add(env, engine, http_server + "/default", segments=1)
    ok = wait_until(lambda: _status(env, dl_id) == Status.COMPLETED)
    assert ok, f"download did not complete (status={_status(env, dl_id)}, " \
               f"err={env['db'].get_download(dl_id).error_message})"
    path = env["db"].get_download(dl_id).save_path
    with open(path, "rb") as fh:
        data = fh.read()
    assert data == FILE_CONTENT
    assert env["db"].get_download(dl_id).checksum_sha256 == EXPECTED_SHA256
    # no leftover part file
    assert not os.path.exists(path + ".part")
    engine.stop()


def test_multi_segment_download_8_segments(http_server, env):
    engine = _make_engine(env)
    dl_id = _add(env, engine, http_server + "/files/" + FILE_NAME, segments=8)
    ok = wait_until(lambda: _status(env, dl_id) == Status.COMPLETED, timeout=60)
    assert ok, env["db"].get_download(dl_id).error_message
    dl = env["db"].get_download(dl_id)
    with open(dl.save_path, "rb") as fh:
        data = fh.read()
    assert data == FILE_CONTENT
    assert dl.file_size == len(FILE_CONTENT)
    assert dl.segments == 8
    # segments table shows 8 completed
    segs = env["db"].get_segments(dl_id)
    assert len(segs) == 8
    assert all(s.status == "completed" for s in segs)
    engine.stop()


def test_pause_and_resume(http_server, env):
    engine = _make_engine(env)
    url = http_server + "/slow/files/" + FILE_NAME  # ~2.5 s transfer
    dl_id = _add(env, engine, url, segments=4)

    # let it progress a little
    def started():
        dl = env["db"].get_download(dl_id)
        return _status(env, dl_id) == Status.DOWNLOADING \
            and dl.downloaded_size > 0
    assert wait_until(started), "download never started"
    env["engine"] = engine  # for pause via engine

    engine.pause_download(dl_id)
    ok = wait_until(lambda: _status(env, dl_id) == Status.PAUSED)
    assert ok

    # let it sit paused; the part file must exist and stop growing
    time.sleep(0.6)
    part = env["db"].get_download(dl_id).save_path + ".part"
    assert os.path.exists(part)
    size_during_pause = os.path.getsize(part)
    time.sleep(0.5)
    assert os.path.getsize(part) <= size_during_pause

    engine.resume_download(dl_id)
    ok = wait_until(lambda: _status(env, dl_id) == Status.COMPLETED, timeout=60)
    assert ok, env["db"].get_download(dl_id).error_message
    with open(env["db"].get_download(dl_id).save_path, "rb") as fh:
        assert fh.read() == FILE_CONTENT
    engine.stop()


def test_resume_after_app_restart(http_server, env):
    """Crash-recovery: pause → close engine → brand new engine → auto-resume."""
    engine = _make_engine(env)
    dl_id = _add(env, engine, http_server + "/slow/files/" + FILE_NAME, segments=4)

    def started():
        dl = env["db"].get_download(dl_id)
        return _status(env, dl_id) == Status.DOWNLOADING and dl.downloaded_size > 0
    assert wait_until(started)

    engine.pause_download(dl_id)
    assert wait_until(lambda: _status(env, dl_id) == Status.PAUSED)
    engine.stop()

    # ---- "restart": fresh engine on the SAME database ----
    engine2 = DownloadEngine(env["db"], env["config"])
    engine2.start()
    recovered = engine2.restore_incomplete(auto_resume=True)
    assert any(d.id == dl_id for d in recovered)

    ok = wait_until(lambda: _status(env, dl_id) == Status.COMPLETED, timeout=60)
    assert ok, env["db"].get_download(dl_id).error_message
    with open(env["db"].get_download(dl_id).save_path, "rb") as fh:
        assert fh.read() == FILE_CONTENT
    engine2.stop()


def test_404_marks_failed(http_server, env):
    engine = _make_engine(env)
    dl_id = _add(env, engine, http_server + "/404/missing.bin", segments=2)
    ok = wait_until(lambda: _status(env, dl_id) == Status.FAILED)
    assert ok
    assert "404" in env["db"].get_download(dl_id).error_message
    engine.stop()


def test_flaky_server_retries_and_succeeds(http_server, env):
    engine = _make_engine(env)
    env["config"].set("retries", "4")
    # reset flaky counter
    dl_id = _add(env, engine, http_server + "/flaky", segments=1)
    ok = wait_until(lambda: _status(env, dl_id) == Status.COMPLETED, timeout=90)
    assert ok, env["db"].get_download(dl_id).error_message
    engine.stop()


def test_no_ranges_single_stream(http_server, env):
    engine = _make_engine(env)
    dl_id = _add(env, engine, http_server + "/noranges/file.dat", segments=8)
    ok = wait_until(lambda: _status(env, dl_id) == Status.COMPLETED, timeout=60)
    assert ok, env["db"].get_download(dl_id).error_message
    dl = env["db"].get_download(dl_id)
    assert dl.ranges_supported is False
    assert dl.segments == 1
    with open(dl.save_path, "rb") as fh:
        assert fh.read() == FILE_CONTENT
    engine.stop()


def test_cancel_stops_download(http_server, env):
    engine = _make_engine(env)
    dl_id = _add(env, engine, http_server + "/slow/files/" + FILE_NAME, segments=4)
    assert wait_until(lambda: _status(env, dl_id) == Status.DOWNLOADING)
    engine.cancel_download(dl_id, delete_files=True)
    ok = wait_until(lambda: _status(env, dl_id) == Status.CANCELED)
    assert ok
    time.sleep(1.2)  # let the engine tick process it
    part = env["db"].get_download(dl_id).save_path + ".part"
    assert not os.path.exists(part)
    engine.stop()


def test_speed_limit_is_enforced(http_server, env):
    """1 MB file @ 256 KB/s must take >= ~2.5 s (unthrottled it is < 0.5 s)."""
    env["config"].set("speed_limit", "256")   # 256 KB/s
    env["config"].set("speed_limit_unit", "KB")
    engine = _make_engine(env)
    dl_id = _add(env, engine, http_server + "/files/" + FILE_NAME, segments=2)
    start = time.monotonic()
    ok = wait_until(lambda: _status(env, dl_id) == Status.COMPLETED, timeout=60)
    elapsed = time.monotonic() - start
    assert ok, env["db"].get_download(dl_id).error_message
    assert elapsed >= 2.5, \
        f"download finished in {elapsed:.2f}s – throttle was not enforced"
    engine.stop()


def test_scheduler_promotes_due_download(http_server, env):
    from datetime import datetime, timedelta

    engine = _make_engine(env)
    when = (datetime.now() - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%S")
    dl = Download(url=http_server + "/default", file_name=FILE_NAME,
                  save_path=str(env["tmp"] / "downloads" / FILE_NAME),
                  status=Status.SCHEDULED, scheduled_time=when)
    dl_id = engine.add(dl)
    ok = wait_until(lambda: _status(env, dl_id) in (
        Status.COMPLETED, Status.FAILED, Status.DOWNLOADING), timeout=30)
    assert ok
    if _status(env, dl_id) == Status.DOWNLOADING:
        wait_until(lambda: _status(env, dl_id) == Status.COMPLETED, timeout=30)
    assert _status(env, dl_id) == Status.COMPLETED
    engine.stop()
