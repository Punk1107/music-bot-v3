# -*- coding: utf-8 -*-
"""
core/stability.py — Stability utilities for Music Bot V3.

Provides:
  ExceptionKind      — Enum: RECOVERABLE | FATAL | EXPECTED
  classify_exception — Map any exception to its ExceptionKind
  with_timeout       — Async wrapper adding a hard timeout + label to any coroutine
  exponential_backoff— Compute retry delay with full-jitter
  retry_with_backoff — Generic retry loop with exponential backoff + classification
  DeadTaskWatchdog   — Monitor discord.ext.tasks loops; restart if crashed
  MemoryLeakDetector — Periodic RSS/thread/cache/task snapshots; log anomalies
"""

from __future__ import annotations

import asyncio
import gc
import logging
import math
import os
import random
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from discord.ext import tasks as ext_tasks

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# 1. Exception Classification
# ══════════════════════════════════════════════════════════════════════════════

class ExceptionKind(str, Enum):
    """
    Three-tier exception taxonomy.

    EXPECTED    — known, user-facing situations (rate limit, bad query, no results)
                  → log at DEBUG; no alert
    RECOVERABLE — transient failures that should be retried (network, timeout, 503)
                  → log at WARNING; retry if context allows
    FATAL       — unrecoverable states that need human attention (missing token,
                  DB corruption, import failure, OOM)
                  → log at ERROR; forward to dev channel
    """
    EXPECTED    = "expected"
    RECOVERABLE = "recoverable"
    FATAL       = "fatal"


# Keyword sets used by classify_exception()
_EXPECTED_PATTERNS: list[str] = [
    "no results",
    "private video",
    "video unavailable",
    "age-restricted",
    "copyright",
    "not found",
    "circuitbreakeropen",
    "user not in voice",
    "permission denied",
    "forbidden",
    "cannot connect",
    "missing permissions",
]

_RECOVERABLE_PATTERNS: list[str] = [
    "timeout",
    "timed out",
    "connection reset",
    "connection refused",
    "network",
    "temporary",
    "503",
    "502",
    "429",
    "rate limit",
    "too many requests",
    "broken pipe",
    "remote end closed",
    "eof",
    "server disconnected",
    "read error",
    "ssl",
]

_FATAL_TYPES: tuple[type, ...] = (
    SystemExit,
    KeyboardInterrupt,
    MemoryError,
    RecursionError,
)


def classify_exception(exc: BaseException) -> ExceptionKind:
    """
    Classify *exc* into EXPECTED / RECOVERABLE / FATAL.

    Priority: type-based FATAL check > keyword match.
    Falls back to RECOVERABLE for any unrecognised generic Exception.
    """
    if isinstance(exc, _FATAL_TYPES):
        return ExceptionKind.FATAL

    exc_str = str(exc).lower()
    exc_type = type(exc).__name__.lower()
    combined = f"{exc_type} {exc_str}"

    for kw in _EXPECTED_PATTERNS:
        if kw in combined:
            return ExceptionKind.EXPECTED

    for kw in _RECOVERABLE_PATTERNS:
        if kw in combined:
            return ExceptionKind.RECOVERABLE

    # ImportError / AttributeError / TypeError at top-level → fatal
    if isinstance(exc, (ImportError, AttributeError, TypeError, ValueError)):
        return ExceptionKind.FATAL

    return ExceptionKind.RECOVERABLE


def log_with_kind(
    exc: BaseException,
    log: logging.Logger,
    *,
    context: str = "",
    include_traceback: bool = False,
) -> ExceptionKind:
    """
    Classify *exc* and log it at the appropriate level.

    Returns the ExceptionKind so callers can branch on it.
    """
    kind   = classify_exception(exc)
    prefix = f"[{context}] " if context else ""

    if kind == ExceptionKind.EXPECTED:
        log.debug("%s%s (expected): %s", prefix, type(exc).__name__, exc)
    elif kind == ExceptionKind.RECOVERABLE:
        log.warning("%s%s (recoverable): %s", prefix, type(exc).__name__, exc)
    else:  # FATAL
        log.error(
            "%s%s (FATAL): %s",
            prefix, type(exc).__name__, exc,
            exc_info=include_traceback,
        )

    return kind


# ══════════════════════════════════════════════════════════════════════════════
# 2. Async Timeout Wrapper
# ══════════════════════════════════════════════════════════════════════════════

async def with_timeout(
    coro: Coroutine[Any, Any, Any],
    seconds: float,
    label: str = "operation",
) -> Any:
    """
    Await *coro* with a hard timeout of *seconds*.

    Raises asyncio.TimeoutError (already a RECOVERABLE kind) on expiry.
    Logs a warning so every timeout is visible in logs.
    """
    try:
        return await asyncio.wait_for(coro, timeout=seconds)
    except asyncio.TimeoutError:
        logger.warning("⏱ Timeout after %.1fs — %s", seconds, label)
        raise


# ══════════════════════════════════════════════════════════════════════════════
# 3. Exponential Backoff + Jitter
# ══════════════════════════════════════════════════════════════════════════════

def exponential_backoff(
    attempt: int,
    *,
    base: float   = 1.0,
    cap: float    = 60.0,
    jitter: bool  = True,
) -> float:
    """
    Full-jitter exponential backoff: random(0, min(cap, base * 2^attempt)).

    attempt=0 → random(0, base)
    attempt=1 → random(0, base*2)
    attempt=2 → random(0, base*4)
    …capped at *cap* seconds.

    With jitter=False, returns the deterministic cap value (useful for tests).
    """
    ceiling = min(cap, base * (2 ** attempt))
    if jitter:
        return random.uniform(0.0, ceiling)
    return ceiling


async def retry_with_backoff(
    fn: Callable[..., Coroutine[Any, Any, Any]],
    *args: Any,
    retries:    int   = 3,
    base_delay: float = 1.0,
    cap_delay:  float = 30.0,
    timeout:    Optional[float] = None,
    label:      str   = "operation",
    **kwargs: Any,
) -> Any:
    """
    Call async *fn* up to *retries* times with exponential-backoff + jitter.

    - EXPECTED exceptions → propagate immediately (no retry).
    - FATAL   exceptions → propagate immediately.
    - RECOVERABLE        → sleep with backoff and retry.

    Returns the result of the first successful call.
    Raises the last exception if all attempts are exhausted.
    """
    last_exc: BaseException | None = None

    for attempt in range(retries):
        try:
            if timeout is not None:
                return await with_timeout(fn(*args, **kwargs), timeout, label=label)
            return await fn(*args, **kwargs)

        except Exception as exc:
            kind = classify_exception(exc)
            last_exc = exc

            if kind in (ExceptionKind.EXPECTED, ExceptionKind.FATAL):
                raise

            delay = exponential_backoff(attempt, base=base_delay, cap=cap_delay)
            logger.warning(
                "retry_with_backoff: %s — attempt %d/%d failed (%s). "
                "Retrying in %.2fs…",
                label, attempt + 1, retries, exc, delay,
            )
            if attempt < retries - 1:
                await asyncio.sleep(delay)

    raise last_exc  # type: ignore[misc]


# ══════════════════════════════════════════════════════════════════════════════
# 4. Dead Task Watchdog
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class _WatchedTask:
    loop:           "ext_tasks.Loop"
    name:           str
    restart_count:  int         = 0
    last_restarted: float       = field(default_factory=time.monotonic)
    backoff_idx:    int         = 0


_RESTART_BACKOFFS = (0.0, 30.0, 60.0, 120.0)   # seconds; index capped at len-1


class DeadTaskWatchdog:
    """
    Monitors a set of discord.ext.tasks.Loop instances every *interval* seconds.

    If a loop is found not running (is_running() == False) and is not explicitly
    cancelled, the watchdog attempts to restart it with exponential backoff.

    Usage::

        watchdog = DeadTaskWatchdog(interval=60)
        watchdog.register(bot._idle_check, "idle_check")
        watchdog.register(bot._queue_save, "queue_save")
        # then call watchdog.tick() in a @tasks.loop(seconds=60) background task
    """

    def __init__(self, interval: float = 60.0) -> None:
        self._interval = interval
        self._tasks:  list[_WatchedTask] = []
        self._stopped = False   # set True after bot.close()

    def register(self, loop: "ext_tasks.Loop", name: str) -> None:
        self._tasks.append(_WatchedTask(loop=loop, name=name))

    def stop(self) -> None:
        """Signal that the watchdog should no longer attempt restarts."""
        self._stopped = True

    async def tick(self) -> None:
        """Call this periodically (e.g. every 60 s) to check all watched tasks."""
        if self._stopped:
            return

        now = time.monotonic()
        for wt in self._tasks:
            if wt.loop.is_running():
                # Healthy — reset backoff
                if wt.restart_count > 0:
                    logger.info("🐕 Watchdog: '%s' recovered after %d restart(s).", wt.name, wt.restart_count)
                    wt.restart_count = 0
                    wt.backoff_idx   = 0
                continue

            # Loop is NOT running — check backoff
            backoff_secs = _RESTART_BACKOFFS[min(wt.backoff_idx, len(_RESTART_BACKOFFS) - 1)]
            if now - wt.last_restarted < backoff_secs:
                continue   # still waiting in backoff window

            wt.restart_count  += 1
            wt.last_restarted  = now
            wt.backoff_idx     = min(wt.backoff_idx + 1, len(_RESTART_BACKOFFS) - 1)

            logger.warning(
                "🐕 Watchdog: task '%s' is NOT running — restarting (attempt #%d, next backoff=%.0fs).",
                wt.name, wt.restart_count,
                _RESTART_BACKOFFS[min(wt.backoff_idx, len(_RESTART_BACKOFFS) - 1)],
            )
            try:
                wt.loop.restart()
            except Exception as exc:
                logger.error("🐕 Watchdog: failed to restart '%s': %s", wt.name, exc)


# ══════════════════════════════════════════════════════════════════════════════
# 5. Memory Leak Detector
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class _MemSnapshot:
    ts:           float   # monotonic
    rss_mb:       float   # Resident Set Size in MiB
    vms_mb:       float   # Virtual Memory Size in MiB
    thread_count: int
    task_count:   int     # asyncio tasks
    gc_objects:   int     # len(gc.get_objects())


_LEAK_RSS_GROWTH_MB   = 50.0   # warn if RSS grew by this many MiB in one window
_LEAK_THREAD_GROWTH   = 20     # warn if thread count grew by this many
_LEAK_TASK_GROWTH     = 50     # warn if asyncio task count grew by this many


class MemoryLeakDetector:
    """
    Periodically records process memory / thread / task snapshots and compares
    them to detect abnormal growth trends.

    Call tick() every ~30 minutes from a background task.
    """

    def __init__(self, history_size: int = 6) -> None:
        self._history: list[_MemSnapshot] = []
        self._history_size = history_size

    def _take_snapshot(self) -> _MemSnapshot:
        rss_mb = vms_mb = 0.0
        try:
            import psutil
            proc = psutil.Process(os.getpid())
            mi   = proc.memory_info()
            rss_mb = mi.rss / (1024 * 1024)
            vms_mb = mi.vms / (1024 * 1024)
        except Exception:
            pass

        return _MemSnapshot(
            ts           = time.monotonic(),
            rss_mb       = rss_mb,
            vms_mb       = vms_mb,
            thread_count = threading.active_count(),
            task_count   = len(asyncio.all_tasks()),
            gc_objects   = len(gc.get_objects()),
        )

    async def tick(self, extra_stats: dict | None = None) -> _MemSnapshot:
        """
        Take a snapshot, store it, compare with previous, and log a report.

        *extra_stats* is an optional dict of {"label": value} pairs appended to the log.
        Returns the current snapshot.
        """
        snap = self._take_snapshot()
        self._history.append(snap)
        if len(self._history) > self._history_size:
            self._history.pop(0)

        # Build log message
        extra_str = ""
        if extra_stats:
            extra_str = "  |  " + "  ".join(f"{k}={v}" for k, v in extra_stats.items())

        logger.info(
            "🧠 MemLeak snapshot — RSS=%.1fMiB  VMS=%.1fMiB  "
            "threads=%d  tasks=%d  gc_objs=%d%s",
            snap.rss_mb, snap.vms_mb,
            snap.thread_count, snap.task_count, snap.gc_objects,
            extra_str,
        )

        # Compare with earliest snapshot in history
        if len(self._history) >= 2:
            oldest = self._history[0]
            rss_delta    = snap.rss_mb       - oldest.rss_mb
            thread_delta = snap.thread_count - oldest.thread_count
            task_delta   = snap.task_count   - oldest.task_count
            elapsed_min  = (snap.ts - oldest.ts) / 60.0

            anomalies: list[str] = []
            if rss_delta > _LEAK_RSS_GROWTH_MB:
                anomalies.append(f"RSS +{rss_delta:.1f}MiB in {elapsed_min:.0f}min")
            if thread_delta > _LEAK_THREAD_GROWTH:
                anomalies.append(f"threads +{thread_delta} in {elapsed_min:.0f}min")
            if task_delta > _LEAK_TASK_GROWTH:
                anomalies.append(f"tasks +{task_delta} in {elapsed_min:.0f}min")

            if anomalies:
                logger.warning(
                    "🚨 MemLeak ANOMALY detected — %s",
                    " | ".join(anomalies),
                )
            else:
                logger.debug(
                    "🧠 MemLeak trend OK — ΔRSS=%.1fMiB  Δthreads=%+d  Δtasks=%+d  "
                    "over %.0f min",
                    rss_delta, thread_delta, task_delta, elapsed_min,
                )

        return snap

    @property
    def latest(self) -> Optional[_MemSnapshot]:
        return self._history[-1] if self._history else None

    @property
    def history(self) -> list[_MemSnapshot]:
        return list(self._history)
