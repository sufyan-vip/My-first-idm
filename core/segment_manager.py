"""
File segmentation: how many chunks, which byte ranges, and merge/verify.

The engine writes every segment directly into ONE pre-allocated ``.part``
file at its byte offset (disjoint ranges), so "merging" is just a rename –
O(1) even for multi-GB files.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from utils.constants import MAX_SEGMENTS, SEGMENT_THRESHOLDS
from utils.logger import get_logger

log = get_logger("segments")


@dataclass(frozen=True)
class SegmentRange:
    """Inclusive byte range ``[start, end]`` plus its index."""

    index: int
    start: int
    end: int

    @property
    def size(self) -> int:
        return self.end - self.start + 1


def optimal_segment_count(file_size: int, server_max: int = 0) -> int:
    """Pick the segment count for *file_size* using the threshold table.

    ``server_max`` lets the server (``Server`` header heuristics or user
    setting) cap the value; 0 means "no cap".  Always clamped to 1..32.
    """
    if file_size <= 0:
        return 1
    count = 1
    for threshold, segments in SEGMENT_THRESHOLDS:
        if file_size < threshold:
            count = segments
            break
    if server_max > 0:
        count = min(count, server_max)
    return max(1, min(count, MAX_SEGMENTS))


def split_ranges(total_size: int, segment_count: int) -> list[SegmentRange]:
    """Split ``[0, total_size-1]`` into *segment_count* near-equal ranges.

    The last segment absorbs the rounding remainder so the ranges tile the
    file exactly.  For files smaller than the segment count, the count is
    reduced to the file size (a byte is the smallest unit).
    """
    if total_size <= 0:
        return []
    n = max(1, min(segment_count, total_size))
    base = total_size // n
    remainder = total_size % n
    ranges: list[SegmentRange] = []
    start = 0
    for i in range(n):
        # give one extra byte to the first `remainder` segments
        size = base + (1 if i < remainder else 0)
        if size <= 0:
            break
        ranges.append(SegmentRange(index=i, start=start, end=start + size - 1))
        start += size
    return ranges


def merge_status(ranges: list[SegmentRange],
                 downloaded: list[int]) -> dict:
    """Aggregate per-segment progress (UI + completion detection).

    Parameters
    ----------
    ranges:
        the planned ranges.
    downloaded:
        bytes downloaded per segment index (len must equal len(ranges)).

    Returns a dict with ``completed`` (bool), ``done``/``total`` bytes and
    ``completed_segments`` count.
    """
    total = sum(r.size for r in ranges)
    done = 0
    completed_segments = 0
    for r, d in zip(ranges, downloaded):
        d = min(d, r.size)
        done += d
        if d >= r.size:
            completed_segments += 1
    return {
        "total": total,
        "done": done,
        "completed": done >= total and total > 0,
        "completed_segments": completed_segments,
        "ranges": len(ranges),
    }


def allocate_part_file(part_path: str, total_size: int) -> None:
    """Create/sparse-allocate *part_path* at *total_size* bytes.

    Existing part files (resume case) are left untouched.  Sparse files use
    no real disk space on NTFS until bytes are written.
    """
    if os.path.exists(part_path):
        try:
            if os.path.getsize(part_path) >= total_size:
                return
        except OSError:
            pass
        # wrong size (server-side file changed?) – start fresh
        try:
            os.remove(part_path)
        except OSError:
            pass
    if total_size <= 0:
        open(part_path, "wb").close()
        return
    with open(part_path, "wb") as fh:
        fh.seek(total_size - 1)
        fh.write(b"\0")


def finalize(part_path: str, final_path: str) -> None:
    """Rename the finished part file to its final destination."""
    if not os.path.exists(part_path):
        raise FileNotFoundError(part_path)
    if os.path.exists(final_path):
        os.remove(final_path)
    os.replace(part_path, final_path)


def verify_size(final_path: str, expected: int) -> bool:
    """Size integrity check after finalization (expected <= 0 skips)."""
    if expected <= 0:
        return os.path.isfile(final_path)
    try:
        return os.path.getsize(final_path) == expected
    except OSError:
        return False


def free_space_ok(part_dir: str, needed: int) -> bool:
    """Heuristic disk-space guard (needs 1.05x the file size)."""
    if needed <= 0:
        return True
    try:
        import shutil
        usage = shutil.disk_usage(part_dir)
        return usage.free >= needed * 1.05
    except OSError:
        return True
