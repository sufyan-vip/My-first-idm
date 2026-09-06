"""
Global bandwidth limiter (token bucket).

All active downloads share ONE limiter so the user's "max speed" setting
caps the *total* transfer rate – exactly like IDM.  Workers call
:meth:`BandwidthLimiter.wait` after reading each chunk; the call blocks
just long enough to respect the configured rate.

The bucket burst equals half a second of traffic (min 64 KiB) – small
enough that even tiny files are throttled, large enough to keep normal
transfers smooth.
"""

from __future__ import annotations

import threading
import time
from typing import Optional


class BandwidthLimiter:
    """Thread-safe token bucket shared by every download thread.

    Parameters
    ----------
    rate:
        bytes/second; ``0`` means unlimited.
    burst:
        optional explicit bucket size in bytes (defaults to half a second
        of traffic at *rate*).
    """

    QUANTUM = 0.05  # min sleep slice

    @staticmethod
    def _burst_for(rate: int) -> int:
        """Burst = half a second of traffic (min 64 KiB)."""
        return max(64 * 1024, rate // 2)

    def __init__(self, rate: int = 0, burst: Optional[int] = None) -> None:
        self._lock = threading.Lock()
        self._rate = max(0, int(rate))
        self._burst = max(1024, int(burst)) if burst else self._burst_for(self._rate)
        self._tokens = float(self._burst)
        self._last_refill = time.monotonic()

    # -------------------------------------------------------------- control

    def set_rate(self, rate: int) -> None:
        """Change the limit at runtime (settings dialog)."""
        with self._lock:
            self._rate = max(0, int(rate))
            self._burst = self._burst_for(self._rate)
            self._tokens = float(self._burst)
            self._last_refill = time.monotonic()

    @property
    def rate(self) -> int:
        with self._lock:
            return self._rate

    @property
    def burst(self) -> int:
        with self._lock:
            return self._burst

    # ---------------------------------------------------------------- core

    def _refill_locked(self, cap: int) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._last_refill = now
        if elapsed > 0 and self._rate > 0:
            # the bucket may grow up to the bigger of its burst size and the
            # request being waited on – otherwise large chunks would deadlock
            self._tokens = min(float(cap), self._tokens + elapsed * self._rate)

    def wait(self, n_bytes: int) -> None:
        """Block until *n_bytes* of transfer budget is available."""
        n_bytes = max(0, int(n_bytes))
        if n_bytes == 0:
            return
        while True:
            with self._lock:
                if self._rate <= 0:
                    return
                self._refill_locked(max(self._burst, n_bytes))
                if self._tokens >= n_bytes:
                    self._tokens -= n_bytes
                    return
                deficit = n_bytes - self._tokens
                # how long until the deficit is earned at current rate
                delay = deficit / self._rate
            # cap each sleep so a pause request is noticed quickly
            time.sleep(min(max(delay, self.QUANTUM), 0.5))
