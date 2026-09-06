"""
Download queue & priority system.

Pure logic (no Qt) so it is trivially unit-testable.  The engine owns the
actual "start when a slot frees up" timing; this module answers *"which
download should run next?"*.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional

from database.models import Download
from utils.constants import Priority, Status


def priority_rank(priority: str) -> int:
    return Priority.RANK.get(priority, Priority.RANK[Priority.MEDIUM])


def sort_key(download: Download):
    """Ordering: priority (high first), then creation time (FIFO), then id."""
    return (priority_rank(download.priority), download.created_at or "", download.id or 0)


def pick_next(pending: Iterable[Download],
              now: Optional[datetime] = None) -> Optional[Download]:
    """Choose the best download to start next.

    * ``queued`` downloads are always eligible.
    * ``scheduled`` downloads are eligible only when their time has arrived.
    * ``downloading``/``paused`` are never eligible.

    Returns None when nothing is eligible.
    """
    now = now or datetime.utcnow()
    eligible = []
    for dl in pending:
        if dl.status == Status.QUEUED:
            eligible.append(dl)
        elif dl.status == Status.SCHEDULED:
            if _is_due(dl.scheduled_time, now):
                eligible.append(dl)
    if not eligible:
        return None
    eligible.sort(key=sort_key)
    return eligible[0]


def _is_due(scheduled_time: Optional[str], now: datetime) -> bool:
    if not scheduled_time:
        return True
    try:
        when = datetime.strptime(scheduled_time, "%Y-%m-%dT%H:%M:%S")
    except (TypeError, ValueError):
        return True
    return when <= now


def is_due(download: Download, now: Optional[datetime] = None) -> bool:
    """Public helper for the scheduler/UI ("starts in …" labels)."""
    if download.status != Status.SCHEDULED:
        return False
    return _is_due(download.scheduled_time, now or datetime.utcnow())


def seconds_until(download: Download, now: Optional[datetime] = None) -> float:
    """Seconds until a scheduled download is due (negative == already due)."""
    now = now or datetime.utcnow()
    if not download.scheduled_time:
        return 0.0
    try:
        when = datetime.strptime(download.scheduled_time, "%Y-%m-%dT%H:%M:%S")
    except (TypeError, ValueError):
        return 0.0
    return (when - now).total_seconds()


def active_count(downloads: Iterable[Download]) -> int:
    return sum(1 for d in downloads if d.status == Status.DOWNLOADING)


def queued_count(downloads: Iterable[Download]) -> int:
    return sum(1 for d in downloads
               if d.status in (Status.QUEUED, Status.SCHEDULED))
