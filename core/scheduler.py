"""
Download scheduler.

A lightweight timer-based scheduler (no external dependency): every few
seconds it promotes ``scheduled`` downloads whose time has arrived into
``queued`` so the normal queue logic can pick them up, and it exposes the
"starts in …" countdown used by the UI.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Iterable, Optional

from core import queue_manager
from database.models import Download
from utils.constants import Status
from utils.logger import get_logger

log = get_logger("scheduler")

CHECK_INTERVAL_MS = 5000


def to_db_time(when: datetime) -> str:
    """Local datetime -> ISO string stored in the database."""
    return when.strftime("%Y-%m-%dT%H:%M:%S")


def parse_db_time(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S")
    except (TypeError, ValueError):
        return None


class Scheduler:
    """Drives scheduled downloads.  Call :meth:`tick` from a Qt timer."""

    def __init__(self,
                 list_downloads: Callable[[], list[Download]],
                 promote: Callable[[Download], None],
                 check_interval_ms: int = CHECK_INTERVAL_MS) -> None:
        self._list_downloads = list_downloads
        self._promote = promote
        self.check_interval_ms = check_interval_ms
        self._promoted: set[int] = set()

    def tick(self) -> list[Download]:
        """Check once; returns the list of just-promoted downloads."""
        now = datetime.now()
        promoted: list[Download] = []
        for dl in self._list_downloads():
            if dl.status != Status.SCHEDULED or dl.id in self._promoted:
                continue
            if queue_manager.is_due(dl, now):
                self._promoted.add(dl.id or 0)
                log.info("schedule time reached for download #%d (%s)",
                         dl.id, dl.file_name)
                self._promote(dl)
                promoted.append(dl)
        return promoted

    def untrack(self, dl_id: int) -> None:
        """Forget a download (deleted/canceled) so it can never re-promote."""
        self._promoted.discard(dl_id)

    def describe(self, download: Download,
                 now: Optional[datetime] = None) -> str:
        """Human-friendly countdown string for the UI."""
        if download.status != Status.SCHEDULED or not download.scheduled_time:
            return ""
        remaining = queue_manager.seconds_until(download, now or datetime.now())
        if remaining <= 0:
            return "starting…"
        return f"in {remaining:,.0f}s"
