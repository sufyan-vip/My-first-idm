"""
Speed & ETA calculations.

Uses an exponential moving average over 1-second samples so the displayed
speed is stable (no per-chunk jitter) while still tracking real changes.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Optional

MAX_HISTORY = 60  # keep the last minute of samples for the speed graph


class SpeedCalculator:
    """Per-download speed/ETA state.

    Call :meth:`add_bytes` whenever bytes land on disk; read ``speed``,
    ``avg_speed`` and ``eta`` from the UI tick (1 Hz).
    """

    def __init__(self, smoothing: float = 0.35) -> None:
        self._alpha = smoothing          # EMA weight of the newest sample
        self._speed = 0.0                # EMA bytes/sec
        self._last_bytes = 0             # cumulative bytes at last sample
        self._last_time: Optional[float] = None
        self._total_bytes = 0            # lifetime (incl. resumed parts)
        self._running_time = 0.0         # seconds actually transferring
        self._history: deque[float] = deque(maxlen=MAX_HISTORY)

    # --------------------------------------------------------------- input

    def add_bytes(self, n: int) -> None:
        if n <= 0:
            return
        self._total_bytes += n
        now = time.monotonic()
        if self._last_time is not None:
            dt = now - self._last_time
            if dt > 0:
                self._running_time += dt
                instant = n / dt
                # EMA, but the first sample is taken verbatim
                if self._speed == 0.0:
                    self._speed = instant
                else:
                    self._speed += self._alpha * (instant - self._speed)
        self._last_bytes += n
        self._last_time = now

    def sample(self) -> float:
        """Publish one per-second sample (called by the engine tick)."""
        value = round(self._speed, 1)
        self._history.append(value)
        return value

    def reset_window(self) -> None:
        """Call when a download (re)starts so stale speed does not leak."""
        self._speed = 0.0
        self._last_time = None

    # ---------------------------------------------------------------- read

    @property
    def speed(self) -> float:
        """Current (EMA) speed in bytes/second."""
        return self._speed

    @property
    def avg_speed(self) -> float:
        """Average speed over the whole lifetime of this download."""
        if self._running_time <= 0.01:
            return 0.0
        return self._total_bytes / self._running_time

    def eta(self, remaining_bytes: int) -> float:
        """Seconds until completion at the current speed (inf if stalled)."""
        if remaining_bytes <= 0:
            return 0.0
        if self._speed <= 1.0:
            return float("inf")
        return remaining_bytes / self._speed

    @property
    def history(self) -> list[float]:
        return list(self._history)

    @property
    def total_bytes(self) -> int:
        return self._total_bytes
