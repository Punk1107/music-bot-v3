# -*- coding: utf-8 -*-
"""
core/gateway_monitor.py — Discord WebSocket Latency & Gateway Lifecycle Monitor.

Tracks:
  - Rolling heartbeat latency (20-sample window)
  - Moving average, min, max, and jitter (standard deviation)
  - Latency quality classification (Optimal, Acceptable, Degraded, Critical)
  - Spike detection with rate-limited warning logs
  - Gateway lifecycle metrics (connects, disconnects, resumes)
"""

from __future__ import annotations

import collections
import logging
import math
import time
from typing import Optional

import config

logger = logging.getLogger(__name__)


class GatewayLatencyMonitor:
    """Monitors Discord gateway WebSocket latency and connection lifecycle."""

    def __init__(self, window_size: int = 20) -> None:
        self.window_size: int = window_size
        self._samples: collections.deque[float] = collections.deque(maxlen=window_size)
        self._last_spike_log_time: float = 0.0

        # Lifecycle metrics
        self.connect_count: int = 0
        self.disconnect_count: int = 0
        self.resume_count: int = 0
        self.last_connect_time: Optional[float] = None
        self.last_disconnect_time: Optional[float] = None
        self.last_resume_time: Optional[float] = None

    def record_latency(self, latency_s: float) -> None:
        """
        Record a latency reading from discord.py bot.latency (in seconds).
        Converts to milliseconds and monitors for spikes.
        """
        if latency_s is None or math.isnan(latency_s) or math.isinf(latency_s) or latency_s < 0:
            return

        latency_ms = latency_s * 1000.0
        self._samples.append(latency_ms)

        # Spike detection with 60-second log throttle
        warn_threshold = getattr(config, "WS_LATENCY_WARN_MS", 250.0)
        now = time.time()
        if latency_ms >= warn_threshold and (now - self._last_spike_log_time >= 60.0):
            logger.warning(
                "⚠️ Discord Gateway latency spike detected: %.1fms (threshold: %.1fms, avg: %.1fms)",
                latency_ms,
                warn_threshold,
                self.avg_latency_ms,
            )
            self._last_spike_log_time = now

    def on_connect(self) -> None:
        """Record gateway connect/reconnect event."""
        self.connect_count += 1
        self.last_connect_time = time.time()
        logger.info("🌐 Discord Gateway connected (session connect #%d)", self.connect_count)

    def on_disconnect(self) -> None:
        """Record gateway disconnect event."""
        self.disconnect_count += 1
        self.last_disconnect_time = time.time()
        logger.warning("⚠️ Discord Gateway disconnected (total disconnects: %d)", self.disconnect_count)

    def on_resumed(self) -> None:
        """Record gateway session resume event."""
        self.resume_count += 1
        self.last_resume_time = time.time()
        logger.info("🔄 Discord Gateway session resumed (total resumes: %d)", self.resume_count)

    @property
    def current_latency_ms(self) -> float:
        """Most recent latency measurement in ms (or 0.0 if no samples)."""
        return self._samples[-1] if self._samples else 0.0

    @property
    def avg_latency_ms(self) -> float:
        """Rolling average latency in ms."""
        if not self._samples:
            return 0.0
        return sum(self._samples) / len(self._samples)

    @property
    def min_latency_ms(self) -> float:
        """Minimum latency in the current window in ms."""
        return min(self._samples) if self._samples else 0.0

    @property
    def max_latency_ms(self) -> float:
        """Maximum latency in the current window in ms."""
        return max(self._samples) if self._samples else 0.0

    @property
    def jitter_ms(self) -> float:
        """Standard deviation of latency in the current window (jitter)."""
        n = len(self._samples)
        if n < 2:
            return 0.0
        avg = self.avg_latency_ms
        variance = sum((x - avg) ** 2 for x in self._samples) / (n - 1)
        return math.sqrt(variance)

    @property
    def quality_grade(self) -> str:
        """
        Human-readable network quality classification:
          - Optimal:    < 80 ms
          - Acceptable: 80 - 150 ms
          - Degraded:   150 - 250 ms
          - Critical:   >= 250 ms (or no samples)
        """
        if not self._samples:
            return "Critical"
        curr = self.current_latency_ms
        if curr < 80.0:
            return "Optimal"
        elif curr < 150.0:
            return "Acceptable"
        elif curr < getattr(config, "WS_LATENCY_WARN_MS", 250.0):
            return "Degraded"
        else:
            return "Critical"

    def stats(self) -> dict:
        """Dictionary of gateway health stats for embeds and telemetry."""
        return {
            "current_ms": round(self.current_latency_ms, 1),
            "avg_ms": round(self.avg_latency_ms, 1),
            "min_ms": round(self.min_latency_ms, 1),
            "max_ms": round(self.max_latency_ms, 1),
            "jitter_ms": round(self.jitter_ms, 1),
            "quality": self.quality_grade,
            "samples_count": len(self._samples),
            "connects": self.connect_count,
            "disconnects": self.disconnect_count,
            "resumes": self.resume_count,
            "last_disconnect_time": self.last_disconnect_time,
            "last_resume_time": self.last_resume_time,
        }

