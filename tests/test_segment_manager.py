"""Unit tests for core.segment_manager."""

from core.segment_manager import (
    merge_status,
    optimal_segment_count,
    split_ranges,
)


def test_optimal_segment_count():
    MB = 1024 ** 2
    assert optimal_segment_count(0) == 1
    assert optimal_segment_count(512 * 1024) == 1            # 512 KB
    assert optimal_segment_count(5 * MB) == 4
    assert optimal_segment_count(50 * MB) == 8
    assert optimal_segment_count(500 * MB) == 16
    assert optimal_segment_count(2 * 1024 ** 3) == 32
    # server cap
    assert optimal_segment_count(500 * MB, server_max=4) == 4
    assert optimal_segment_count(50 * MB, server_max=0) == 8


def test_split_ranges_tiles_exactly():
    for total in (1, 2, 7, 100, 1024, 10 ** 6):
        for n in (1, 2, 3, 8, 32):
            ranges = split_ranges(total, n)
            assert ranges[0].start == 0
            assert ranges[-1].end == total - 1
            # no gaps / overlaps
            for a, b in zip(ranges, ranges[1:]):
                assert b.start == a.end + 1
            assert sum(r.size for r in ranges) == total
            assert len(ranges) == max(1, min(n, total))


def test_split_ranges_small_file():
    ranges = split_ranges(10, 32)  # file smaller than count
    assert len(ranges) == 10
    assert all(r.size == 1 for r in ranges)


def test_merge_status():
    ranges = split_ranges(1000, 4)
    downloaded = [0, 0, 0, 0]
    st = merge_status(ranges, downloaded)
    assert st["completed"] is False
    assert st["done"] == 0
    downloaded = [250, 250, 250, 250]
    st = merge_status(ranges, downloaded)
    assert st["completed"] is True
    assert st["completed_segments"] == 4
    downloaded = [250, 250, 100, 0]
    st = merge_status(ranges, downloaded)
    assert st["completed"] is False
    assert st["completed_segments"] == 2
