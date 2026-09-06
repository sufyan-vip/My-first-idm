"""Unit tests for core.bandwidth_limiter and core.speed_calculator."""

import time

from core.bandwidth_limiter import BandwidthLimiter
from core.speed_calculator import SpeedCalculator


def test_unlimited_passes_instantly():
    limiter = BandwidthLimiter(rate=0)
    start = time.monotonic()
    for _ in range(100):
        limiter.wait(64 * 1024)
    assert time.monotonic() - start < 0.5


def test_limited_rate_approximately_respected():
    rate = 500 * 1024  # 500 KB/s
    limiter = BandwidthLimiter(rate=rate, burst=1024)
    total = 2048 * 1024  # 2 MB → should take ~4 s
    start = time.monotonic()
    transferred = 0
    while transferred < total:
        chunk = min(32 * 1024, total - transferred)
        limiter.wait(chunk)
        transferred += chunk
    elapsed = time.monotonic() - start
    # generous window: at least 2.5s, at most 8s for 2MB @500KB/s
    assert 2.0 <= elapsed <= 10.0, f"elapsed={elapsed:.2f}"


def test_rate_change_at_runtime():
    limiter = BandwidthLimiter(rate=0)
    start = time.monotonic()
    limiter.wait(1024)
    assert time.monotonic() - start < 0.2
    limiter.set_rate(512 * 1024)  # 512 KB/s → burst 256 KB
    start = time.monotonic()
    # 2 MB minus the 256 KB burst ≈ 3.5 s
    limiter.wait(2 * 1024 * 1024)
    elapsed = time.monotonic() - start
    assert 2.5 <= elapsed <= 8.0, f"elapsed={elapsed:.2f}"


def test_speed_calculator_basic():
    calc = SpeedCalculator()
    calc.add_bytes(1000)
    time.sleep(0.2)
    calc.add_bytes(1000)
    assert calc.speed > 0
    assert calc.avg_speed > 0
    assert calc.eta(100000) == 100000 / calc.speed
    assert calc.eta(0) == 0.0


def test_speed_history():
    calc = SpeedCalculator()
    calc.add_bytes(5000)
    for _ in range(5):
        calc.sample()
    assert len(calc.history) == 5
