"""
Application-wide constants.

Central place for the application name, version, download states,
priorities, categories and sensible default values.  Keeping these in one
module guarantees that every layer (core, database, ui) talks about the
exact same strings/numbers.
"""

from __future__ import annotations

import os
import sys

# ---------------------------------------------------------------------------
# Application identity
# ---------------------------------------------------------------------------

APP_NAME = "TurboFetch Download Manager"
APP_SHORT_NAME = "TurboFetch"
APP_VERSION = "1.0.0"
APP_ORG = "TurboFetch"
APP_ID = "turbofetch"

# ---------------------------------------------------------------------------
# Download lifecycle states
# ---------------------------------------------------------------------------

class Status:
    QUEUED = "queued"            # waiting for a free slot
    DOWNLOADING = "downloading"  # actively transferring
    PAUSED = "paused"            # user/network paused, resumable
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    SCHEDULED = "scheduled"      # waiting for its scheduled time

    ALL = (QUEUED, DOWNLOADING, PAUSED, COMPLETED, FAILED, CANCELED, SCHEDULED)

    #: statuses that still occupy a "life" (not finished)
    ACTIVE = (QUEUED, DOWNLOADING, PAUSED, SCHEDULED)
    FINISHED = (COMPLETED, FAILED, CANCELED)


class Priority:
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    ALL = (HIGH, MEDIUM, LOW)

    #: lower number == higher priority
    RANK = {HIGH: 0, MEDIUM: 1, LOW: 2}


# ---------------------------------------------------------------------------
# Categories (auto-detection of download folders)
# ---------------------------------------------------------------------------

class Category:
    AUTO = "auto"
    DOCUMENTS = "documents"
    MUSIC = "music"
    VIDEOS = "videos"
    IMAGES = "images"
    ARCHIVES = "compressed"
    PROGRAMS = "programs"
    OTHERS = "others"

    ALL = (AUTO, DOCUMENTS, MUSIC, VIDEOS, IMAGES, ARCHIVES, PROGRAMS, OTHERS)


CATEGORY_INFO = {
    Category.DOCUMENTS: {"label": "Documents", "emoji": "\U0001F4C4"},
    Category.MUSIC: {"label": "Music", "emoji": "\U0001F3B5"},
    Category.VIDEOS: {"label": "Videos", "emoji": "\U0001F3AC"},
    Category.IMAGES: {"label": "Images", "emoji": "\U0001F5BC\uFE0F"},
    Category.ARCHIVES: {"label": "Compressed", "emoji": "\U0001F4E6"},
    Category.PROGRAMS: {"label": "Programs", "emoji": "\U0001F4BF"},
    Category.OTHERS: {"label": "Others", "emoji": "\U0001F4C2"},
}

#: extension -> category mapping used by ``file_utils.category_for_extension``
CATEGORY_EXTENSIONS = {
    Category.DOCUMENTS: {
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt",
        ".rtf", ".odt", ".ods", ".odp", ".csv", ".epub", ".mobi", ".md", ".tex",
    },
    Category.MUSIC: {
        ".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a", ".opus",
        ".aiff", ".mid", ".midi",
    },
    Category.VIDEOS: {
        ".mp4", ".mkv", ".avi", ".mov", ".webm", ".wmv", ".flv", ".m4v",
        ".mpg", ".mpeg", ".3gp", ".ts",
    },
    Category.IMAGES: {
        ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".bmp", ".ico",
        ".tif", ".tiff", ".heic",
    },
    Category.ARCHIVES: {
        ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso",
        ".tar.gz", ".tgz", ".cab",
    },
    Category.PROGRAMS: {
        ".exe", ".msi", ".dmg", ".apk", ".appx", ".deb", ".rpm", ".jar",
        ".bat", ".cmd",
    },
}


# ---------------------------------------------------------------------------
# Segment / chunk strategy
# ---------------------------------------------------------------------------

#: file-size based default segment count (see engine algorithm)
SEGMENT_THRESHOLDS = (
    (1 * 1024 ** 2, 1),      # < 1 MB      -> 1
    (10 * 1024 ** 2, 4),     # < 10 MB     -> 4
    (100 * 1024 ** 2, 8),    # < 100 MB    -> 8
    (1024 ** 3, 16),         # < 1 GB      -> 16
    (float("inf"), 32),      # >= 1 GB     -> 32
)

MAX_SEGMENTS = 32
MIN_SEGMENTS = 1

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULTS = {
    "download_folder": os.path.join(os.path.expanduser("~"), "Downloads"),
    "categorize": "true",
    "max_concurrent": "3",
    "max_segments": "8",
    "speed_limit": "0",            # 0 == unlimited (bytes/second when > 0)
    "speed_limit_unit": "MB",
    "timeout": "30",               # seconds per request chunk
    "retries": "5",
    "theme": "dark",               # dark | light | system
    "minimize_to_tray": "false",
    "auto_start": "false",
    "auto_resume": "true",         # resume incomplete downloads on startup
    "notifications_enabled": "true",
    "sound_enabled": "true",
    "post_action": "none",         # none | open_file | open_folder | shutdown
    "antivirus_scan": "false",
    "clipboard_detect": "true",
    "clipboard_auto_add": "false",
    "proxy_type": "none",          # none | http | socks5
    "proxy_host": "",
    "proxy_port": "",
    "proxy_user": "",
    "proxy_password": "",
    "user_agent": "",
    "duplicate_policy": "rename",  # rename | overwrite | skip
    "portable": "false",
    "language": "en",
    "update_channel": "stable",
}


# ---------------------------------------------------------------------------
# Post-download actions
# ---------------------------------------------------------------------------

class PostAction:
    NONE = "none"
    OPEN_FILE = "open_file"
    OPEN_FOLDER = "open_folder"
    SHUTDOWN = "shutdown"
    ALL = (NONE, OPEN_FILE, OPEN_FOLDER, SHUTDOWN)


POST_ACTION_LABELS = {
    PostAction.NONE: "Do nothing",
    PostAction.OPEN_FILE: "Open file",
    PostAction.OPEN_FOLDER: "Open containing folder",
    PostAction.SHUTDOWN: "Shut down PC (after all downloads complete)",
}


# ---------------------------------------------------------------------------
# Paths & environment
# ---------------------------------------------------------------------------

IS_WINDOWS = sys.platform.startswith("win")
IS_FROZEN = getattr(sys, "frozen", False)  # running as PyInstaller bundle


def app_data_dir() -> str:
    """Return the writable application data directory.

    In *portable mode* (config flag) everything lives next to the executable
    so the whole application can move to a USB stick.
    """
    return _data_dir()


def _data_dir() -> str:
    if IS_FROZEN:
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "portable_data")


def system_appdata() -> str:
    """Platform default application-data location (e.g. %APPDATA%)."""
    if IS_WINDOWS:
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME", os.path.join(os.path.expanduser("~"), ".config"))
    return os.path.join(base, APP_ID)


LOG_DIR_NAME = "logs"
DB_FILE_NAME = "idm.db"

#: default user agent (modern Chromium on Windows)
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

#: URL used by the built-in speed test (Cloudflare streams exactly N bytes)
SPEED_TEST_URL = "https://speed.cloudflare.com/__down?bytes=10000000"

#: GitHub repository used for update checks
UPDATE_REPO = "sufyan-vip/My-first-idm"
