# -*- coding: utf-8 -*-
"""utils/rate_limiter.py — Sliding-window per-(guild, user) rate limiter."""

from __future__ import annotations

import time
from collections import defaultdict, deque


class RateLimiter:
    """
    Sliding-window rate limiter.

    Default: max 5 commands per 10 seconds per (guild_id, user_id) pair.
    """

    def __init__(self, max_calls: int = 5, window: float = 10.0) -> None:
        self.max_calls = max_calls
        self.window    = window
        self._windows: dict[tuple[int, int], deque[float]] = defaultdict(deque)
        self._last_cleanup: float = 0.0
        self._cleanup_interval: float = 60.0

    def is_rate_limited(self, guild_id: int, user_id: int) -> bool:
        """
        Return True if the (guild_id, user_id) pair has exceeded the rate limit.
        Also registers the current call.
        """
        key = (guild_id, user_id)
        now = time.monotonic()
        dq  = self._windows[key]

        # Evict expired entries
        while dq and now - dq[0] > self.window:
            dq.popleft()

        # Throttled cleanup when tracking table grows large (at most once every 60s)
        if len(self._windows) > 1000 and (now - self._last_cleanup) > self._cleanup_interval:
            self.cleanup()

        if len(dq) >= self.max_calls:
            return True

        dq.append(now)
        return False

    def remaining(self, guild_id: int, user_id: int) -> float:
        """Seconds until rate limit resets for this (guild, user)."""
        key = (guild_id, user_id)
        dq  = self._windows.get(key)
        if not dq:
            return 0.0
        now = time.monotonic()
        while dq and now - dq[0] > self.window:
            dq.popleft()
        if not dq:
            self._windows.pop(key, None)
            return 0.0
        return max(0.0, self.window - (now - dq[0]))

    def cleanup(self) -> None:
        """Evict keys that have no active timestamps in their window."""
        now = time.monotonic()
        self._last_cleanup = now
        expired_keys = [
            k for k, dq in self._windows.items()
            if not dq or now - dq[-1] > self.window
        ]
        for k in expired_keys:
            self._windows.pop(k, None)

    def reset(self, guild_id: int, user_id: int) -> None:
        """Manually clear rate limit for a (guild, user)."""
        self._windows.pop((guild_id, user_id), None)
