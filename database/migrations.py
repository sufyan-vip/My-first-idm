"""
Database schema & migrations.

The schema version is stored in SQLite's ``PRAGMA user_version``.  Each
migration is a plain SQL script run in order; new columns are added
idempotently (``ALTER TABLE ... ADD COLUMN`` guarded by a column check) so
older databases upgrade smoothly.
"""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 2

INITIAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS downloads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    file_name TEXT NOT NULL,
    save_path TEXT NOT NULL,
    file_size INTEGER DEFAULT 0,
    downloaded_size INTEGER DEFAULT 0,
    status TEXT DEFAULT 'queued',
    priority TEXT DEFAULT 'medium',
    segments INTEGER DEFAULT 8,
    speed REAL DEFAULT 0,
    progress REAL DEFAULT 0,
    category TEXT DEFAULT 'others',
    error_message TEXT,
    scheduled_time DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME,
    resume_data TEXT
);

CREATE TABLE IF NOT EXISTS segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    download_id INTEGER,
    segment_index INTEGER,
    start_byte INTEGER,
    end_byte INTEGER,
    downloaded_bytes INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending',
    temp_file TEXT,
    FOREIGN KEY (download_id) REFERENCES downloads(id)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT,
    file_name TEXT,
    file_size INTEGER,
    save_path TEXT,
    category TEXT,
    status TEXT DEFAULT 'completed',
    downloaded_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_downloads_status ON downloads(status);
CREATE INDEX IF NOT EXISTS idx_segments_download ON segments(download_id);
CREATE INDEX IF NOT EXISTS idx_history_date ON history(downloaded_at);
"""

#: version 2: extra columns for auth, headers, checksums, protocol info
MIGRATIONS = {
    2: [
        "ALTER TABLE downloads ADD COLUMN checksum_sha256 TEXT DEFAULT ''",
        "ALTER TABLE downloads ADD COLUMN checksum_md5 TEXT DEFAULT ''",
        "ALTER TABLE downloads ADD COLUMN user_agent TEXT DEFAULT ''",
        "ALTER TABLE downloads ADD COLUMN custom_headers TEXT DEFAULT ''",
        "ALTER TABLE downloads ADD COLUMN auth_user TEXT DEFAULT ''",
        "ALTER TABLE downloads ADD COLUMN auth_pass TEXT DEFAULT ''",
        "ALTER TABLE downloads ADD COLUMN referer TEXT DEFAULT ''",
        "ALTER TABLE downloads ADD COLUMN post_action TEXT DEFAULT 'none'",
        "ALTER TABLE downloads ADD COLUMN protocol TEXT DEFAULT 'http'",
        "ALTER TABLE downloads ADD COLUMN ranges_supported INTEGER DEFAULT 1",
    ],
}


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def migrate(conn: sqlite3.Connection) -> int:
    """Bring *conn* up to ``SCHEMA_VERSION``.  Returns the resulting version."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == 0:
        conn.executescript(INITIAL_SCHEMA)
        version = 1
        conn.execute("PRAGMA user_version = 1")
        conn.commit()

    while version < SCHEMA_VERSION:
        version += 1
        for statement in MIGRATIONS.get(version, []):
            if statement.upper().startswith("ALTER TABLE") and "ADD COLUMN" in statement.upper():
                # "ALTER TABLE downloads ADD COLUMN name TEXT ..."
                parts = statement.replace("ADD COLUMN", " ", 1).split()
                table, column = parts[2], parts[3]
                if _has_column(conn, table, column):
                    continue
            conn.execute(statement)
        conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()

    return version
