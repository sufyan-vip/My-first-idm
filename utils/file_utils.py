"""
File-system helpers: human sizes, category detection, unique names,
checksums and safe moves.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from typing import Optional

from utils.constants import CATEGORY_EXTENSIONS, Category


# ---------------------------------------------------------------------------
# Human readable formatting
# ---------------------------------------------------------------------------

_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def human_size(num: float | int, precision: int = 1) -> str:
    """``1536`` -> ``'1.5 KB'``.  Negative/zero values become ``'0 B'``."""
    try:
        num = float(num)
    except (TypeError, ValueError):
        return "0 B"
    if num <= 0:
        return "0 B"
    idx = 0
    value = num
    while value >= 1024 and idx < len(_UNITS) - 1:
        value /= 1024
        idx += 1
    if idx == 0:
        return f"{int(value)} {_UNITS[0]}"
    return f"{value:.{precision}f} {_UNITS[idx]}"


def human_speed(num: float) -> str:
    """``2621440`` -> ``'2.5 MB/s'``."""
    return f"{human_size(num)}/s"


def format_eta(seconds: float) -> str:
    """Seconds -> ``'2:30'`` / ``'1:02:03'`` style string.

    Returns ``'--:--'`` for unknown/infinitive values and ``'Done'`` for
    non-positive ones.
    """
    import math

    if seconds is None:
        return "--:--"
    try:
        if math.isinf(seconds) or math.isnan(seconds) or seconds < 0:
            return "--:--"
    except (TypeError, ValueError):
        return "--:--"
    if seconds <= 1:
        return "Done"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def parse_size(text: str) -> int:
    """Parse ``'1.5 GB'`` / ``'500MB'`` / ``'2048'`` into bytes."""
    text = (text or "").strip().upper().replace(" ", "")
    if not text:
        return 0
    match = re.match(r"^([0-9]*\.?[0-9]+)\s*([KMGT]?I?B?)$", text)
    if not match:
        return 0
    value = float(match.group(1))
    unit = match.group(2)
    factor = {
        "B": 1, "": 1, "KB": 1024, "KIB": 1024, "K": 1024,
        "MB": 1024 ** 2, "MIB": 1024 ** 2, "M": 1024 ** 2,
        "GB": 1024 ** 3, "GIB": 1024 ** 3, "G": 1024 ** 3,
        "TB": 1024 ** 4, "TIB": 1024 ** 4, "T": 1024 ** 4,
    }.get(unit, 1)
    return int(value * factor)


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

def category_for_extension(file_name: str) -> str:
    """Map a file name to a category via its extension (case-insensitive)."""
    name = (file_name or "").lower()
    for ext in (".tar.gz", ".tar.bz2", ".tar.xz"):  # multi-part extensions first
        if name.endswith(ext):
            return Category.ARCHIVES
    _, ext = os.path.splitext(name)
    if not ext:
        return Category.OTHERS
    for category, extensions in CATEGORY_EXTENSIONS.items():
        if ext in extensions:
            return category
    return Category.OTHERS


def category_emoji(category: str) -> str:
    from utils.constants import CATEGORY_INFO

    info = CATEGORY_INFO.get(category)
    return info["emoji"] if info else CATEGORY_INFO[Category.OTHERS]["emoji"]


# ---------------------------------------------------------------------------
# Name handling
# ---------------------------------------------------------------------------

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_file_name(name: str, fallback: str = "download") -> str:
    """Strip characters that are illegal on Windows/NTFS file names."""
    name = (name or "").strip().strip(".")
    name = _INVALID.sub("_", name)
    name = re.sub(r"\s+", " ", name)
    name = name.strip()
    if not name or name in (".", "..") or len(name) > 200:
        name = (name or "")[:200] or fallback
    return name or fallback


def split_name(file_name: str) -> tuple[str, str]:
    """``'movie (1).mp4'`` -> ``('movie (1)', '.mp4')``."""
    root, ext = os.path.splitext(file_name or "")
    return root or file_name, ext


def unique_path(folder: str, file_name: str, policy: str = "rename",
                expected_size: int = 0) -> tuple[str, bool]:
    """Resolve the final path for *file_name* inside *folder*.

    Parameters
    ----------
    policy:
        ``rename``     – append `` (1)``, `` (2)`` … until free.
        ``overwrite``  – keep the name (existing file is replaced).
        ``skip``       – keep the name; caller decides what "skip" means.

    Returns
    -------
    (path, existed) – the concrete path and whether it already existed.
    """
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, file_name)
    if not os.path.exists(path):
        return path, False
    if policy in ("overwrite", "skip"):
        return path, True
    root, ext = split_name(file_name)
    counter = 1
    while True:
        candidate = os.path.join(folder, f"{root} ({counter}){ext}")
        if not os.path.exists(candidate):
            return candidate, True
        counter += 1


def move_or_replace(src: str, dst: str) -> str:
    """Move *src* to *dst*, replacing an existing destination file.

    Works across drives (Windows) via a copy+delete fallback.
    """
    try:
        shutil.move(src, dst)
        return dst
    except (OSError, shutil.Error):
        # cross-device or locked destination: copy over, then delete source
        if os.path.exists(dst):
            os.replace(src, dst)
        else:
            shutil.copy2(src, dst)
            os.remove(src)
        return dst


def directory_size(path: str) -> int:
    total = 0
    for _root, _dirs, files in os.walk(path):
        for f in files:
            fp = os.path.join(_root, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


# ---------------------------------------------------------------------------
# Checksums
# ---------------------------------------------------------------------------

def file_checksums(path: str,
                   chunk_size: int = 8 * 1024 * 1024) -> dict[str, str]:
    """Return ``{'sha256': …, 'md5': …}`` for *path* (streaming, constant mem)."""
    sha = hashlib.sha256()
    md5 = hashlib.md5(usedforsecurity=False)
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            sha.update(chunk)
            md5.update(chunk)
    return {"sha256": sha.hexdigest(), "md5": md5.hexdigest()}


def free_space(path: str) -> int:
    """Free disk space in bytes for the volume containing *path*."""
    try:
        usage = shutil.disk_usage(os.path.abspath(path))
        return usage.free
    except OSError:
        return 0


def exists_with_size(path: str, size: int) -> bool:
    """True when *path* exists and (for size > 0) matches the expected size."""
    if not os.path.isfile(path):
        return False
    if size <= 0:
        return True
    try:
        return os.path.getsize(path) == size
    except OSError:
        return False


def temp_part_path(final_path: str) -> str:
    """Part (work-in-progress) file name for *final_path*."""
    return final_path + ".part"


def clean_stale_parts(folder: str, keep: Optional[set[str]] = None) -> int:
    """Remove orphan ``*.part`` files (downloads no longer tracked).

    Returns the number of files removed.
    """
    keep = {os.path.abspath(p) for p in keep or set()}
    removed = 0
    try:
        entries = os.listdir(folder)
    except OSError:
        return 0
    for name in entries:
        if not name.endswith(".part"):
            continue
        full = os.path.join(folder, name)
        if os.path.abspath(full) in keep:
            continue
        try:
            os.remove(full)
            removed += 1
        except OSError:
            pass
    return removed
