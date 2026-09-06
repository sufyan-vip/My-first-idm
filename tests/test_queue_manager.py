"""Unit tests for core.queue_manager (priority + scheduling logic)."""

from datetime import datetime, timedelta

from core import queue_manager
from database.models import Download
from utils.constants import Status


def _dl(id_, priority="medium", status=Status.QUEUED,
        created="2026-01-01T00:00:00", scheduled=None):
    return Download(
        id=id_, url=f"http://x/{id_}", file_name=f"f{id_}.bin",
        save_path=f"/tmp/f{id_}.bin", priority=priority, status=status,
        created_at=created, scheduled_time=scheduled,
    )


def test_priority_ordering():
    low = _dl(1, "low")
    high = _dl(2, "high")
    medium = _dl(3, "medium")
    assert queue_manager.pick_next([low, high, medium]).id == 2
    assert queue_manager.pick_next([low, medium]).id == 3
    assert queue_manager.pick_next([low]).id == 1


def test_fifo_within_same_priority():
    old = _dl(1, created="2026-01-01T00:00:00")
    new = _dl(2, created="2026-01-02T00:00:00")
    assert queue_manager.pick_next([new, old]).id == 1


def test_paused_never_selected():
    paused = _dl(1, status=Status.PAUSED)
    downloading = _dl(2, status=Status.DOWNLOADING)
    assert queue_manager.pick_next([paused, downloading]) is None


def test_scheduled_respects_time():
    future = _dl(1, status=Status.SCHEDULED,
                 scheduled=(datetime.utcnow() + timedelta(hours=1))
                 .strftime("%Y-%m-%dT%H:%M:%S"))
    past = _dl(2, status=Status.SCHEDULED,
               scheduled=(datetime.utcnow() - timedelta(hours=1))
               .strftime("%Y-%m-%dT%H:%M:%S"))
    assert queue_manager.pick_next([future]) is None
    assert queue_manager.pick_next([past]).id == 2
    assert queue_manager.pick_next([future, past]).id == 2


def test_counts():
    dl = [
        _dl(1, status=Status.DOWNLOADING),
        _dl(2, status=Status.PAUSED),
        _dl(3, status=Status.QUEUED),
        _dl(4, status=Status.SCHEDULED),
        _dl(5, status=Status.COMPLETED),
    ]
    assert queue_manager.active_count(dl) == 1
    assert queue_manager.queued_count(dl) == 2
