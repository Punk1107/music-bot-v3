# -*- coding: utf-8 -*-
"""
core/circuit_breaker.py — 3-state circuit breaker for Music Bot V3.

States:
  CLOSED    → Normal operation. Calls pass through.
  OPEN      → Failure threshold exceeded. All calls rejected immediately.
  HALF_OPEN → Recovery probe. One test call allowed; success closes, failure re-opens.

V3 additions:
  - metrics: total_calls, total_failures, total_opens (for analytics/webserver)
  - reset() method for manual recovery
  - thread-safe via asyncio.Lock
"""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class BreakerState(str, Enum):
    CLOSED    = "closed"
    OPEN      = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpen(Exception):
    """Raised when a call is rejected because the circuit breaker is OPEN."""
    def __init__(self, name: str) -> None:
        super().__init__(f"Circuit breaker '{name}' is OPEN — service temporarily unavailable.")
        self.breaker_name = name


class CircuitBreaker:
    """
    Async circuit breaker.

    Usage:
        result = await breaker.call(my_async_func, arg1, arg2)
    """

    def __init__(
        self,
        name:              str,
        failure_threshold: int   = 5,
        recovery_window:   float = 60.0,
    ) -> None:
        self.name               = name
        self.failure_threshold  = failure_threshold
        self.recovery_window    = recovery_window

        self._state:         BreakerState = BreakerState.CLOSED
        self._failure_count: int          = 0
        self._last_failure:  float        = 0.0
        self._lock:          asyncio.Lock = asyncio.Lock()

        # V3 metrics
        self.total_calls:    int = 0
        self.total_failures: int = 0
        self.total_opens:    int = 0

    # ── State accessors ───────────────────────────────────────────────────────

    @property
    def state(self) -> BreakerState:
        return self._state

    @property
    def is_open(self) -> bool:
        return self._state == BreakerState.OPEN

    def status_dict(self) -> dict:
        """Return a JSON-serialisable status snapshot for the dashboard."""
        return {
            "name":              self.name,
            "state":             self._state.value,
            "failure_count":     self._failure_count,
            "failure_threshold": self.failure_threshold,
            "recovery_window":   self.recovery_window,
            "total_calls":       self.total_calls,
            "total_failures":    self.total_failures,
            "total_opens":       self.total_opens,
        }

    def reset(self) -> None:
        """Manually reset to CLOSED state (e.g., admin command)."""
        self._state         = BreakerState.CLOSED
        self._failure_count = 0
        self._last_failure  = 0.0
        logger.info("CircuitBreaker '%s' manually reset to CLOSED.", self.name)

    # ── Core call logic ───────────────────────────────────────────────────────

    async def call(self, func: Callable, *args: Any, **kwargs: Any) -> Any:
        """
        Execute *func* guarded by the circuit breaker.

        Raises CircuitBreakerOpen if the breaker is OPEN and the recovery window
        has not elapsed. Raises the original exception on actual function failure.
        """
        async with self._lock:
            self.total_calls += 1

            if self._state == BreakerState.OPEN:
                if time.monotonic() - self._last_failure >= self.recovery_window:
                    self._state = BreakerState.HALF_OPEN
                    logger.info("CircuitBreaker '%s' → HALF_OPEN (probe).", self.name)
                else:
                    raise CircuitBreakerOpen(self.name)

        # ── Execute the actual call (outside lock to avoid blocking other calls)
        try:
            result = await func(*args, **kwargs)
            async with self._lock:
                self._on_success()
            return result
        except Exception as exc:
            async with self._lock:
                self._on_failure(exc)
            raise

    def _on_success(self) -> None:
        if self._state in (BreakerState.HALF_OPEN, BreakerState.OPEN):
            logger.info("CircuitBreaker '%s' → CLOSED (recovered).", self.name)
        self._state         = BreakerState.CLOSED
        self._failure_count = 0

    def _on_failure(self, exc: Exception) -> None:
        self.total_failures += 1
        self._failure_count += 1
        self._last_failure   = time.monotonic()

        if self._state == BreakerState.HALF_OPEN or self._failure_count >= self.failure_threshold:
            if self._state != BreakerState.OPEN:
                self.total_opens += 1
                logger.warning(
                    "CircuitBreaker '%s' → OPEN (failures=%d, exc=%s).",
                    self.name, self._failure_count, exc,
                )
            self._state         = BreakerState.OPEN
            self._failure_count = 0   # reset counter for next window
