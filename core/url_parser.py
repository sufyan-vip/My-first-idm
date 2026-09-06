"""
URL validation, extraction from arbitrary text and file-name detection.

Used by the UI (add dialog, clipboard, drag & drop, batch import) and by the
engine for fallback naming when the server does not send a file name.
"""

from __future__ import annotations

import os
import re
from typing import Optional
from urllib.parse import parse_qs, quote, unquote, urlparse

from utils.constants import Category
from utils.file_utils import category_for_extension, sanitize_file_name

VALID_SCHEMES = ("http", "https", "ftp")

_URL_RE = re.compile(r"^\s*(https?|ftp)://[^\s\"'<>\|\^]+", re.IGNORECASE)
_ANY_URL_RE = re.compile(r"(?:https?|ftp)://[^\s\"'<>\|\^)\]\}]+", re.IGNORECASE)


def is_valid_url(url: str) -> bool:
    """True when *url* parses and uses http(s)/ftp with a network host."""
    if not url:
        return False
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    if parsed.scheme.lower() not in VALID_SCHEMES:
        return False
    if not parsed.netloc:
        return False
    # reject junk like "http://" with empty host handled above; strip trailing dots
    return bool(parsed.netloc.rstrip("."))


def extract_urls(text: str) -> list[str]:
    """Find all plausible URLs inside *text* (clipboard / batch files).

    Handles line-separated lists, comma-separated values, quotes and
    surrounding sentences.  Duplicates are removed while keeping order.
    """
    if not text:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for raw in _ANY_URL_RE.findall(text):
        url = raw.rstrip(".,;:!?)'\"")
        if not is_valid_url(url):
            continue
        key = url.lower()
        if key in seen:
            continue
        seen.add(key)
        found.append(url)
    return found


def strip_url(url: str) -> str:
    """Trim whitespace and a trailing dot (common copy/paste artifact)."""
    return (url or "").strip().rstrip(".")


def url_filename(url: str) -> str:
    """Derive a candidate file name from the URL path alone.

    Query strings are ignored; the last non-empty path segment is unquoted.
    Falls back to ``download`` when the URL has no usable path.
    """
    parsed = urlparse(strip_url(url))
    path = unquote(parsed.path or "")
    # drop common directory names that indicate the server, not the file
    name = None
    for part in reversed(path.split("/")):
        if part and part not in ("/", ""):
            name = part
            break
    if not name:
        # e.g. "https://host/" ? look at query (some CDNs put name in query)
        qs = parse_qs(parsed.query)
        for key in ("name", "filename", "file", "download"):
            if key in qs and qs[key]:
                name = qs[key][0]
                break
    if not name:
        name = parsed.netloc or "download"
    name = sanitize_file_name(name, fallback="download")
    return name


def url_category(url: str) -> str:
    """Category inferred from the URL's file name (auto-detect helper)."""
    return category_for_extension(url_filename(url))


def normalize_filename(name: str) -> str:
    """Sanitize and guard length/emptiness for a user-provided file name."""
    return sanitize_file_name(name, fallback="download")


def guess_extension(content_type: str) -> str:
    """``'video/mp4'`` -> ``'.mp4'`` (small built-in map, empty if unknown)."""
    table = {
        "application/zip": ".zip", "application/x-rar-compressed": ".rar",
        "application/x-7z-compressed": ".7z", "application/gzip": ".gz",
        "application/x-tar": ".tar", "application/pdf": ".pdf",
        "application/msword": ".doc", "application/vnd.ms-excel": ".xls",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "application/octet-stream": "", "application/x-msdownload": ".exe",
        "text/plain": ".txt", "text/csv": ".csv", "text/html": ".html",
        "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
        "image/webp": ".webp", "image/svg+xml": ".svg", "image/bmp": ".bmp",
        "audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/flac": ".flac",
        "audio/ogg": ".ogg", "audio/aac": ".aac",
        "video/mp4": ".mp4", "video/webm": ".webm", "video/x-matroska": ".mkv",
        "video/x-msvideo": ".avi", "video/quicktime": ".mov",
    }
    if not content_type:
        return ""
    mime = content_type.split(";")[0].strip().lower()
    return table.get(mime, "")


def filename_with_extension(name: str, content_type: str = "") -> str:
    """Make sure *name* has an extension, using *content_type* when missing."""
    root, ext = os.path.splitext(name or "")
    if ext:
        return name
    ext = guess_extension(content_type)
    return f"{name}{ext}" if ext else (name or "download")


def category_for_url(url: str, content_type: str = "") -> str:
    """Best-effort category from URL + content type."""
    name = url_filename(url)
    root, ext = os.path.splitext(name)
    if not ext and content_type:
        ext = guess_extension(content_type)
    if ext:
        return category_for_extension(name + ext)
    return Category.OTHERS


def clean_headers_from_text() -> None:
    """(Kept for API completeness – no state here.)"""
    return None


def quote_for_url(name: str) -> str:
    """Percent-encode a local file name for use inside a URL path."""
    return quote(name, safe="")


def decode_disposition_header(value: str) -> Optional[str]:
    """Extract a file name from a ``Content-Disposition`` header value.

    Supports both RFC 6266 (``filename*=UTF-8''name``) and the classic
    ``filename="name"`` forms.
    """
    if not value:
        return None
    value = value.replace("\\'", "'")
    # RFC 5987 first: filename*=charset''percent-encoded
    m = re.search(r"filename\*\s*=\s*(?:UTF-8|utf-8)?''([^;]+)", value)
    if m:
        return unquote(m.group(1).strip())
    m = re.search(r"filename\s*=\s*\"([^\"]+)\"", value)
    if m:
        return m.group(1).strip()
    m = re.search(r"filename\s*=\s*([^;]+)", value)
    if m:
        return m.group(1).strip().strip("\"'")
    return None
