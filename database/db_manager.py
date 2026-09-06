"""
SQLite connection manager.

One connection per process (``check_same_thread=False``) guarded by an
``RLock``: worker threads and the UI thread may both touch the database, and
SQLite with ``WAL`` journaling handles this efficiently for our scale.

All timestamps are stored as ISO-8601 UTC strings so they are sortable and
unambiguous across timezones.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Iterable, Optional, Sequence

from database import migrations
from database.models import Download, HistoryItem, Segment
from utils.constants import Status
from utils.logger import get_logger

log = get_logger("db")

#: columns returned for every download row (order matters – see _row_to_download)
_DOWNLOAD_COLS = (
    "id, url, file_name, save_path, file_size, downloaded_size, status, priority,"
    " segments, speed, progress, category, error_message, scheduled_time, created_at,"
    " completed_at, resume_data, checksum_sha256, checksum_md5, user_agent,"
    " custom_headers, auth_user, auth_pass, referer, post_action, protocol,"
    " ranges_supported"
)


def now_iso() -> str:
    """Current UTC time as ISO-8601 (second precision)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


class Database:
    """Thread-safe wrapper around a single SQLite connection."""

    def __init__(self, db_path: str) -> None:
        self.path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            migrations.migrate(self._conn)
        log.info("database ready at %s (schema v%d)", db_path, migrations.SCHEMA_VERSION)

    # ---------------------------------------------------------------- utils

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.commit()
                self._conn.close()
            except sqlite3.Error:
                pass

    def _execute(self, sql: str, params: Iterable = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur

    def _query(self, sql: str, params: Iterable = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    # -------------------------------------------------------------- settings

    def get_setting(self, key: str) -> Optional[str]:
        rows = self._query("SELECT value FROM settings WHERE key = ?", (key,))
        return rows[0]["value"] if rows else None

    def set_setting(self, key: str, value: str) -> None:
        self._execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )

    # ------------------------------------------------------------ downloads

    @staticmethod
    def _row_to_download(row: sqlite3.Row) -> Download:
        return Download(
            id=row["id"],
            url=row["url"],
            file_name=row["file_name"],
            save_path=row["save_path"],
            file_size=row["file_size"] or 0,
            downloaded_size=row["downloaded_size"] or 0,
            status=row["status"] or Status.QUEUED,
            priority=row["priority"] or "medium",
            segments=row["segments"] or 1,
            speed=row["speed"] or 0.0,
            progress=row["progress"] or 0.0,
            category=row["category"] or "others",
            error_message=row["error_message"] or "",
            scheduled_time=row["scheduled_time"],
            created_at=row["created_at"],
            completed_at=row["completed_at"],
            resume_data=row["resume_data"] or "{}",
            checksum_sha256=row["checksum_sha256"] or "",
            checksum_md5=row["checksum_md5"] or "",
            user_agent=row["user_agent"] or "",
            custom_headers=row["custom_headers"] or "",
            auth_user=row["auth_user"] or "",
            auth_pass=row["auth_pass"] or "",
            referer=row["referer"] or "",
            post_action=row["post_action"] or "none",
            protocol=row["protocol"] or "http",
            ranges_supported=bool(row["ranges_supported"]) if row["ranges_supported"] is not None else True,
        )

    def add_download(self, dl: Download) -> int:
        """Insert a new download; returns its id.  *created_at* is filled in."""
        dl.created_at = dl.created_at or now_iso()
        cur = self._execute(
            "INSERT INTO downloads (url, file_name, save_path, file_size, downloaded_size,"
            " status, priority, segments, speed, progress, category, error_message,"
            " scheduled_time, created_at, completed_at, resume_data, checksum_sha256,"
            " checksum_md5, user_agent, custom_headers, auth_user, auth_pass, referer,"
            " post_action, protocol, ranges_supported)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                dl.url, dl.file_name, dl.save_path, dl.file_size, dl.downloaded_size,
                dl.status, dl.priority, dl.segments, dl.speed, dl.progress, dl.category,
                dl.error_message, dl.scheduled_time, dl.created_at, dl.completed_at,
                dl.resume_data, dl.checksum_sha256, dl.checksum_md5, dl.user_agent,
                dl.custom_headers, dl.auth_user, dl.auth_pass, dl.referer,
                dl.post_action, dl.protocol, 1 if dl.ranges_supported else 0,
            ),
        )
        dl.id = cur.lastrowid
        log.info("download #%d added: %s", dl.id, dl.file_name)
        return int(dl.id)

    def update_download(self, dl: Download) -> None:
        """Persist the full state of a download (used by the engine's 1 Hz tick)."""
        if dl.id is None:
            return
        self._execute(
            "UPDATE downloads SET downloaded_size=?, status=?, speed=?, progress=?,"
            " segments=?, error_message=?, completed_at=?, resume_data=?,"
            " file_size=?, checksum_sha256=?, checksum_md5=?, protocol=?,"
            " ranges_supported=? WHERE id=?",
            (
                dl.downloaded_size, dl.status, dl.speed, dl.progress, dl.segments,
                dl.error_message, dl.completed_at, dl.resume_data, dl.file_size,
                dl.checksum_sha256, dl.checksum_md5, dl.protocol,
                1 if dl.ranges_supported else 0, dl.id,
            ),
        )

    def update_download_fields(self, dl_id: int, **fields) -> None:
        """Update a subset of columns (e.g. status only)."""
        allowed = {
            "file_name", "save_path", "file_size", "downloaded_size", "status",
            "priority", "segments", "speed", "progress", "category", "error_message",
            "scheduled_time", "completed_at", "resume_data", "checksum_sha256",
            "checksum_md5", "user_agent", "custom_headers", "auth_user", "auth_pass",
            "referer", "post_action", "protocol", "ranges_supported",
        }
        sets, params = [], []
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key == "ranges_supported":
                value = 1 if value else 0
            sets.append(f"{key} = ?")
            params.append(value)
        if not sets:
            return
        params.append(dl_id)
        self._execute(f"UPDATE downloads SET {', '.join(sets)} WHERE id = ?", params)

    def get_download(self, dl_id: int) -> Optional[Download]:
        rows = self._query(f"SELECT {_DOWNLOAD_COLS} FROM downloads WHERE id = ?", (dl_id,))
        return self._row_to_download(rows[0]) if rows else None

    def list_downloads(self, status: Optional[str] = None,
                       statuses: Optional[Sequence[str]] = None,
                       search: str = "") -> list[Download]:
        sql = f"SELECT {_DOWNLOAD_COLS} FROM downloads"
        clauses, params = [], []
        if status:
            clauses.append("status = ?")
            params.append(status)
        elif statuses:
            placeholders = ",".join("?" for _ in statuses)
            clauses.append(f"status IN ({placeholders})")
            params.extend(statuses)
        if search:
            clauses.append("(file_name LIKE ? OR url LIKE ?)")
            like = f"%{search}%"
            params.extend([like, like])
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END," \
               " created_at DESC, id DESC"
        return [self._row_to_download(r) for r in self._query(sql, params)]

    def get_next_queued(self) -> Optional[Download]:
        """Highest-priority queued/scheduled-ready download (FIFO inside a priority)."""
        row = self._query(
            "SELECT {_c} FROM downloads WHERE status = 'queued' "
            "ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, "
            "created_at ASC, id ASC LIMIT 1".replace("{_c}", _DOWNLOAD_COLS)
        )
        return self._row_to_download(row[0]) if row else None

    def delete_download(self, dl_id: int, delete_segments: bool = True) -> None:
        if delete_segments:
            self._execute("DELETE FROM segments WHERE download_id = ?", (dl_id,))
        self._execute("DELETE FROM downloads WHERE id = ?", (dl_id,))

    def count_by_status(self) -> dict[str, int]:
        rows = self._query("SELECT status, COUNT(*) AS c FROM downloads GROUP BY status")
        counts = {s: 0 for s in Status.ALL}
        for row in rows:
            counts[row["status"]] = row["c"]
        return counts

    def total_active_speed(self) -> float:
        rows = self._query(
            "SELECT COALESCE(SUM(speed), 0) AS s FROM downloads WHERE status = 'downloading'"
        )
        return float(rows[0]["s"] or 0.0) if rows else 0.0

    def restore_incomplete(self) -> list[Download]:
        """All downloads that were not finished when the app last exited."""
        return self.list_downloads(statuses=(Status.QUEUED, Status.DOWNLOADING,
                                             Status.PAUSED, Status.SCHEDULED))

    def mark_downloading_as_paused(self) -> int:
        """Crash recovery: anything 'downloading' is now 'paused' after a restart."""
        cur = self._execute(
            "UPDATE downloads SET status = ?, speed = 0 WHERE status = ?",
            (Status.PAUSED, Status.DOWNLOADING),
        )
        return cur.rowcount or 0

    # ------------------------------------------------------------- segments

    def replace_segments(self, segments: Iterable[Segment]) -> None:
        segments = list(segments)
        dl_id = segments[0].download_id if segments else 0
        self._execute("DELETE FROM segments WHERE download_id = ?", (dl_id,))
        for seg in segments:
            self._execute(
                "INSERT INTO segments (download_id, segment_index, start_byte, end_byte,"
                " downloaded_bytes, status, temp_file) VALUES (?,?,?,?,?,?,?)",
                (dl_id, seg.segment_index, seg.start_byte, seg.end_byte,
                 seg.downloaded_bytes, seg.status, seg.temp_file),
            )

    def get_segments(self, dl_id: int) -> list[Segment]:
        rows = self._query(
            "SELECT id, download_id, segment_index, start_byte, end_byte,"
            " downloaded_bytes, status, temp_file FROM segments WHERE download_id = ?"
            " ORDER BY segment_index",
            (dl_id,),
        )
        return [
            Segment(
                id=r["id"], download_id=r["download_id"],
                segment_index=r["segment_index"], start_byte=r["start_byte"],
                end_byte=r["end_byte"], downloaded_bytes=r["downloaded_bytes"] or 0,
                status=r["status"] or "pending", temp_file=r["temp_file"] or "",
            )
            for r in rows
        ]

    # -------------------------------------------------------------- history

    def add_history(self, item: HistoryItem) -> int:
        item.downloaded_at = item.downloaded_at or now_iso()
        cur = self._execute(
            "INSERT INTO history (url, file_name, file_size, save_path, category, status, downloaded_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (item.url, item.file_name, item.file_size, item.save_path,
             item.category, item.status, item.downloaded_at),
        )
        item.id = cur.lastrowid
        return int(item.id)

    def list_history(self, search: str = "", category: str = "",
                     status: str = "", limit: int = 500) -> list[HistoryItem]:
        sql = "SELECT id, url, file_name, file_size, save_path, category, status, downloaded_at FROM history"
        clauses, params = [], []
        if search:
            clauses.append("(file_name LIKE ? OR url LIKE ?)")
            like = f"%{search}%"
            params.extend([like, like])
        if category:
            clauses.append("category = ?")
            params.append(category)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY downloaded_at DESC, id DESC LIMIT ?"
        params.append(limit)
        rows = self._query(sql, params)
        return [
            HistoryItem(
                id=r["id"], url=r["url"], file_name=r["file_name"],
                file_size=r["file_size"] or 0, save_path=r["save_path"] or "",
                category=r["category"] or "others", status=r["status"] or Status.COMPLETED,
                downloaded_at=r["downloaded_at"],
            )
            for r in rows
        ]

    def delete_history(self, ids: Sequence[int]) -> int:
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        cur = self._execute(f"DELETE FROM history WHERE id IN ({placeholders})", ids)
        return cur.rowcount or 0

    def clear_history(self) -> int:
        cur = self._execute("DELETE FROM history")
        return cur.rowcount or 0

    # ------------------------------------------------------------- vacuuming

    def cleanup_finished(self, keep: int = 1000) -> None:
        """Keep the downloads table bounded: drop oldest finished rows."""
        rows = self._query(
            "SELECT id FROM downloads WHERE status IN (?, ?, ?) "
            "ORDER BY COALESCE(completed_at, created_at) ASC",
            (Status.COMPLETED, Status.FAILED, Status.CANCELED),
        )
        excess = len(rows) - keep
        if excess <= 0:
            return
        ids = [r["id"] for r in rows[:excess]]
        placeholders = ",".join("?" for _ in ids)
        self._execute(f"DELETE FROM downloads WHERE id IN ({placeholders})", ids)
