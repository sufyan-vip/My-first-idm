"""
Pause/resume state management.

Every download keeps a compact JSON blob (``downloads.resume_data``) with
the per-segment offsets, the planned ranges and the part-file location.
The blob is persisted:

* on every pause / stop,
* every few seconds while downloading (crash safety),
* on completion (so a re-download of the same URL can verify itself).

Because the ``.part`` file itself is the source of truth for the bytes
already on disk, resume = "reopen part file + restart each segment thread
from its recorded offset".
"""

from __future__ import annotations

import json
import os
from typing import Optional

from core.segment_manager import SegmentRange, split_ranges
from database.models import Download, Segment
from utils.file_utils import temp_part_path
from utils.logger import get_logger

log = get_logger("resume")

RESUME_VERSION = 1


def part_file_for(download: Download) -> str:
    """Absolute path of the in-progress ``.part`` file."""
    return temp_part_path(download.save_path)


def build_resume_data(download: Download,
                      segments: Optional[list[Segment]] = None,
                      final_url: str = "") -> str:
    """Serialize the current segment state into the resume JSON blob."""
    segs = []
    for seg in segments or []:
        segs.append({
            "index": seg.segment_index,
            "start": seg.start_byte,
            "end": seg.end_byte,
            "downloaded": seg.downloaded_bytes,
            "status": seg.status,
        })
    data = {
        "version": RESUME_VERSION,
        "file_size": download.file_size,
        "ranges_supported": bool(download.ranges_supported),
        "part_file": part_file_for(download),
        "final_url": final_url or download.url,
        "segments": segs,
    }
    return json.dumps(data)


def parse_resume_data(blob: str) -> dict:
    """Parse the JSON blob defensively (corrupt data -> empty dict)."""
    if not blob:
        return {}
    try:
        data = json.loads(blob)
        if isinstance(data, dict) and data.get("version") == RESUME_VERSION:
            return data
    except (ValueError, TypeError):
        pass
    return {}


def plan_segments(download: Download) -> list[SegmentRange]:
    """(Re)compute the segment plan for a download from its stored metadata."""
    if download.file_size <= 0 or not download.ranges_supported:
        return [SegmentRange(0, 0, max(0, download.file_size - 1))] if download.file_size > 0 else []
    return split_ranges(download.file_size, download.segments)


def segments_from_resume(download: Download) -> list[Segment]:
    """Rebuild :class:`Segment` records from the resume blob (or from scratch).

    A segment is trusted only when:

    * its recorded progress fits inside its range, and
    * the part file still exists on disk.
    """
    blob = parse_resume_data(download.resume_data)
    part = part_file_for(download)
    part_ok = os.path.exists(part)
    segments: list[Segment] = []

    if blob and part_ok:
        for raw in blob.get("segments", []):
            try:
                idx = int(raw["index"])
                start = int(raw["start"])
                end = int(raw["end"])
                done = int(raw.get("downloaded", 0))
            except (KeyError, TypeError, ValueError):
                continue
            if end < start or done < 0 or done > end - start + 1:
                continue
            status = "completed" if done >= end - start + 1 else "pending"
            segments.append(Segment(
                download_id=download.id or 0,
                segment_index=idx,
                start_byte=start,
                end_byte=end,
                downloaded_bytes=done,
                status=status,
                temp_file=part,
            ))
        if segments and _blob_consistent(blob, download):
            log.info("download #%d: restored %d segments from resume data",
                     download.id, len(segments))
            return segments

    # fresh plan (first start, or part file missing/inconsistent)
    for rng in plan_segments(download):
        segments.append(Segment(
            download_id=download.id or 0,
            segment_index=rng.index,
            start_byte=rng.start,
            end_byte=rng.end,
            downloaded_bytes=0,
            status="pending",
            temp_file=part,
        ))
    return segments


def _blob_consistent(blob: dict, download: Download) -> bool:
    """Sanity-check stored metadata against the current record."""
    try:
        stored_size = int(blob.get("file_size", -1))
        if stored_size > 0 and download.file_size > 0 and stored_size != download.file_size:
            return False
    except (TypeError, ValueError):
        return False
    return True


def downloaded_total(segments: list[Segment]) -> int:
    return sum(s.downloaded_bytes for s in segments)


def all_completed(segments: list[Segment]) -> bool:
    if not segments:
        return False
    return all(s.downloaded_bytes >= s.total for s in segments)
