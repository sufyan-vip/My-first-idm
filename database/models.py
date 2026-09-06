"""
Plain dataclasses mirroring the database rows.

Keeping these separate from the DB code lets the core engine work with
in-memory objects and only talk to SQLite through the manager.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from utils.constants import Priority, Status


@dataclass
class Download:
    """One download record (row of the ``downloads`` table)."""

    url: str
    file_name: str
    save_path: str
    file_size: int = 0
    downloaded_size: int = 0
    status: str = Status.QUEUED
    priority: str = Priority.MEDIUM
    segments: int = 8
    speed: float = 0.0
    progress: float = 0.0
    category: str = "others"
    error_message: str = ""
    scheduled_time: Optional[str] = None  # ISO-8601 "YYYY-MM-DDTHH:MM:SS"
    created_at: Optional[str] = None
    completed_at: Optional[str] = None
    resume_data: str = "{}"                # JSON blob (see resume_manager)
    checksum_sha256: str = ""
    checksum_md5: str = ""
    user_agent: str = ""
    custom_headers: str = ""               # JSON object string
    auth_user: str = ""
    auth_pass: str = ""
    referer: str = ""
    post_action: str = "none"
    protocol: str = "http"
    ranges_supported: bool = True
    id: Optional[int] = None

    # ------------------------------------------------------------ derived

    @property
    def is_active(self) -> bool:
        return self.status in Status.ACTIVE

    @property
    def is_finished(self) -> bool:
        return self.status in Status.FINISHED

    @property
    def percent(self) -> float:
        if self.file_size <= 0:
            return self.progress
        return min(100.0, 100.0 * self.downloaded_size / self.file_size)

    # ------------------------------------------------------------- helpers

    def custom_headers_dict(self) -> dict[str, str]:
        if not self.custom_headers:
            return {}
        try:
            data = json.loads(self.custom_headers)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except (ValueError, TypeError):
            pass
        return {}

    def resume_dict(self) -> dict:
        if not self.resume_data or self.resume_data == "{}":
            return {}
        try:
            data = json.loads(self.resume_data)
            return data if isinstance(data, dict) else {}
        except (ValueError, TypeError):
            return {}


@dataclass
class Segment:
    """One byte-range of a multi-segment download."""

    download_id: int
    segment_index: int
    start_byte: int
    end_byte: int            # inclusive
    downloaded_bytes: int = 0
    status: str = "pending"  # pending | downloading | completed | failed
    temp_file: str = ""
    id: Optional[int] = None

    @property
    def total(self) -> int:
        return self.end_byte - self.start_byte + 1

    @property
    def remaining(self) -> int:
        return max(0, self.total - self.downloaded_bytes)


@dataclass
class HistoryItem:
    """Completed/finished download kept for the history view."""

    url: str
    file_name: str
    file_size: int = 0
    save_path: str = ""
    category: str = "others"
    status: str = Status.COMPLETED
    downloaded_at: Optional[str] = None
    id: Optional[int] = None


@dataclass
class Settings:
    key: str = ""
    value: str = ""


@dataclass
class EngineSnapshot:
    """Light-weight per-download view handed from engine -> UI.

    The UI list is refreshed from these snapshots on a 1 Hz tick so the UI
    never blocks on the engine or the database.
    """

    id: int
    status: str
    file_name: str
    url: str
    save_path: str
    category: str
    priority: str
    file_size: int
    downloaded_size: int
    progress: float
    speed: float
    avg_speed: float
    eta_seconds: float
    segments_total: int
    segments_active: int
    segments_completed: int
    error_message: str = ""
    scheduled_time: Optional[str] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None
    checksum_sha256: str = ""
    checksum_md5: str = ""
    speed_history: list[float] = field(default_factory=list)
