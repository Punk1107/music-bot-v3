# -*- coding: utf-8 -*-
"""
core/metrics.py — Runtime metrics snapshot collector for Music Bot V3.

Collects and logs a structured snapshot every 5 minutes:
  - Guild count, active players, total queue length
  - CPU %, RAM % and RSS MiB
  - DB round-trip latency (ms)
  - WebSocket latency to Discord (ms)
  - Circuit breaker states (YouTube, Spotify)
  - Active voice connections

The latest snapshot is available via get_latest_snapshot() for use by
/health and other diagnostics commands.

Usage (in main.py setup_hook):
    from core.metrics import MetricsCollector
    bot.metrics = MetricsCollector(bot)
    # then in a @tasks.loop(minutes=5) task:
    await bot.metrics.collect()
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from main import MusicBot

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Data model
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class MetricsSnapshot:
    """One point-in-time snapshot of bot health metrics."""
    # Identity
    ts:                   float   = field(default_factory=time.time)

    # Discord
    guild_count:          int     = 0
    active_players:       int     = 0   # guilds with now_playing set
    total_queue_length:   int     = 0   # sum of all queue lengths
    voice_connections:    int     = 0   # guilds with connected voice_client
    voice_playing:        int     = 0   # guilds currently playing

    # System
    cpu_pct:              float   = 0.0
    ram_pct:              float   = 0.0
    ram_rss_mb:           float   = 0.0

    # Latencies
    db_latency_ms:        float   = 0.0   # 0 = not measured / error
    ws_latency_ms:        float   = 0.0   # discord.py WebSocket latency

    # Circuit breakers
    yt_breaker_state:     str     = "unknown"
    sp_breaker_state:     str     = "unknown"

    # Miscellaneous
    asyncio_task_count:   int     = 0

    def timestamp_iso(self) -> str:
        return datetime.fromtimestamp(self.ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def log_line(self) -> str:
        return (
            f"📊 Metrics — guilds={self.guild_count}  players={self.active_players}/"
            f"{self.voice_connections}  queue={self.total_queue_length}  "
            f"CPU={self.cpu_pct:.1f}%  RAM={self.ram_pct:.1f}%({self.ram_rss_mb:.0f}MiB)  "
            f"DB={self.db_latency_ms:.0f}ms  WS={self.ws_latency_ms:.0f}ms  "
            f"YT={self.yt_breaker_state}  SP={self.sp_breaker_state}  "
            f"tasks={self.asyncio_task_count}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Collector
# ══════════════════════════════════════════════════════════════════════════════

class MetricsCollector:
    """
    Async metrics collector.

    Call ``await collect()`` from a periodic background task (every 5 minutes).
    Stores the last N snapshots; the most recent is available via ``latest``.
    """

    MAX_HISTORY = 12   # 12 × 5 min = 1 hour of history

    def __init__(self, bot: "MusicBot") -> None:
        self._bot     = bot
        self._history: list[MetricsSnapshot] = []

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def latest(self) -> Optional[MetricsSnapshot]:
        return self._history[-1] if self._history else None

    @property
    def history(self) -> list[MetricsSnapshot]:
        return list(self._history)

    async def collect(self) -> MetricsSnapshot:
        """
        Collect a snapshot, store it, log it, and return it.
        Never raises — all errors are caught and set to sentinel values.
        """
        snap = MetricsSnapshot()

        # Discord metrics
        try:
            snap.guild_count = len(self._bot.guilds)
            snap.voice_connections = sum(
                1 for g in self._bot.guilds
                if g.voice_client and g.voice_client.is_connected()
            )
            snap.voice_playing = sum(
                1 for g in self._bot.guilds
                if g.voice_client and g.voice_client.is_playing()
            )
            snap.active_players = sum(
                1 for p in self._bot._players.values() if p.now_playing
            )
            snap.total_queue_length = sum(
                len(p) for p in self._bot._players.values()
            )
        except Exception as exc:
            logger.debug("Metrics: discord stats error: %s", exc)

        # System resources
        try:
            import psutil, os
            proc = psutil.Process(os.getpid())
            snap.cpu_pct    = psutil.cpu_percent(interval=None)
            vm              = psutil.virtual_memory()
            snap.ram_pct    = vm.percent
            snap.ram_rss_mb = proc.memory_info().rss / (1024 * 1024)
        except Exception as exc:
            logger.debug("Metrics: psutil error: %s", exc)

        # WebSocket latency
        try:
            if self._bot.latency and self._bot.latency < 1000:
                snap.ws_latency_ms = self._bot.latency * 1000
        except Exception:
            pass

        # DB latency
        try:
            t0 = time.monotonic()
            await asyncio.wait_for(
                self._bot.db.get_server_config(0),
                timeout=5.0,
            )
            snap.db_latency_ms = (time.monotonic() - t0) * 1000
        except Exception as exc:
            logger.debug("Metrics: DB latency probe error: %s", exc)
            snap.db_latency_ms = -1.0   # -1 indicates failure

        # Circuit breakers
        try:
            snap.yt_breaker_state = self._bot.yt_breaker.state.value
            snap.sp_breaker_state = self._bot.sp_breaker.state.value
        except Exception:
            pass

        # Asyncio tasks
        try:
            snap.asyncio_task_count = len(asyncio.all_tasks())
        except Exception:
            pass

        # Store
        self._history.append(snap)
        if len(self._history) > self.MAX_HISTORY:
            self._history.pop(0)

        # Log
        logger.info(snap.log_line())

        # Warn on elevated latencies
        if snap.db_latency_ms > 500 and snap.db_latency_ms > 0:
            logger.warning("⚠️  DB latency high: %.0fms (threshold 500ms)", snap.db_latency_ms)
        if snap.ws_latency_ms > 500:
            logger.warning("⚠️  WebSocket latency high: %.0fms (threshold 500ms)", snap.ws_latency_ms)
        if snap.cpu_pct > 85:
            logger.warning("⚠️  CPU high: %.1f%%", snap.cpu_pct)
        if snap.ram_pct > 85:
            logger.warning("⚠️  RAM high: %.1f%%", snap.ram_pct)

        return snap


# ── Module-level singleton accessor ──────────────────────────────────────────
# (populated by MusicBot after construction)

_collector: Optional[MetricsCollector] = None


def set_collector(collector: MetricsCollector) -> None:
    global _collector
    _collector = collector


def get_latest_snapshot() -> Optional[MetricsSnapshot]:
    """Return the most recent metrics snapshot, or None if not yet collected."""
    return _collector.latest if _collector else None


def get_history() -> list[MetricsSnapshot]:
    """Return all stored snapshots (most recent last)."""
    return _collector.history if _collector else []
