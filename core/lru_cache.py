# -*- coding: utf-8 -*-
"""
core/lru_cache.py — Separate, properly-evicting LRU caches for Music Bot V3.

Tier Performance F31: LRU Cache
  Three independent LRU caches with their own size/TTL limits:
    - MetadataCache  (track URL → Track metadata dict)
    - ThumbnailCache (thumbnail URL → raw bytes)
    - SearchCache    (query string → list[Track])

  Each cache is:
    - OrderedDict-backed (O(1) true LRU eviction on access + insert)
    - Thread-safe via asyncio.Lock
    - TTL-expiring (stale entries evicted on access)
    - Stats-tracked (hits, misses, evictions)

Tier Performance F32: Memory Pressure Handler
  MemoryPressureManager monitors psutil.virtual_memory().percent every 60 s.
  Thresholds:
    70 % → prune expired entries only (light)
    80 % → evict 50 % of each cache (moderate)
    90 % → clear all caches immediately (aggressive)
  All actions are logged and stats updated.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Generic, Optional, TypeVar

logger = logging.getLogger(__name__)

V = TypeVar("V")


# ── Base LRU Cache ─────────────────────────────────────────────────────────────

class LRUCache(Generic[V]):
    """
    Generic async-safe LRU cache backed by OrderedDict.

    - get()  : O(1), moves to MRU end, returns None if missing/expired
    - set()  : O(1), inserts at MRU end, evicts LRU tail if over capacity
    - delete(): O(1)
    - Stats  : hits, misses, evictions, sets tracked
    """

    def __init__(self, name: str, max_size: int, ttl: float) -> None:
        self.name     = name
        self.max_size = max_size
        self.ttl      = ttl   # seconds; 0 = never expire

        self._data: OrderedDict[str, tuple[V, float]] = OrderedDict()
        self._lock = asyncio.Lock()

        # Stats
        self.hits:      int = 0
        self.misses:    int = 0
        self.evictions: int = 0
        self.sets:      int = 0

    async def get(self, key: str) -> Optional[V]:
        """Return cached value or None (expired/missing). Promotes to MRU on hit."""
        async with self._lock:
            if key not in self._data:
                self.misses += 1
                return None

            value, ts = self._data[key]

            # TTL check
            if self.ttl > 0 and time.monotonic() - ts > self.ttl:
                del self._data[key]
                self.misses   += 1
                self.evictions += 1
                return None

            # Move to MRU end (mark recently used)
            self._data.move_to_end(key)
            self.hits += 1
            return value

    async def set(self, key: str, value: V) -> None:
        """Insert or update a key. Evicts LRU entry if over capacity."""
        async with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = (value, time.monotonic())
            self.sets += 1

            # Evict LRU tail while over capacity
            while len(self._data) > self.max_size:
                self._data.popitem(last=False)   # pops LRU (first) item
                self.evictions += 1

    async def delete(self, key: str) -> bool:
        """Remove a key. Returns True if it existed."""
        async with self._lock:
            if key in self._data:
                del self._data[key]
                return True
            return False

    async def clear(self) -> int:
        """Clear all entries. Returns count removed."""
        async with self._lock:
            n = len(self._data)
            self._data.clear()
            return n

    async def prune_expired(self) -> int:
        """Remove all TTL-expired entries. Returns count removed."""
        if self.ttl <= 0:
            return 0
        async with self._lock:
            now    = time.monotonic()
            stale  = [k for k, (_, ts) in self._data.items() if now - ts > self.ttl]
            for k in stale:
                del self._data[k]
            self.evictions += len(stale)
            return len(stale)

    async def evict_fraction(self, fraction: float = 0.5) -> int:
        """Evict oldest `fraction` of entries (LRU side). Returns count removed."""
        async with self._lock:
            n_remove = max(1, int(len(self._data) * fraction))
            removed  = 0
            while removed < n_remove and self._data:
                self._data.popitem(last=False)
                removed += 1
            self.evictions += removed
            return removed

    def __len__(self) -> int:
        return len(self._data)

    def stats(self) -> dict:
        total = self.hits + self.misses
        return {
            "name":       self.name,
            "size":       len(self._data),
            "max_size":   self.max_size,
            "ttl":        self.ttl,
            "hits":       self.hits,
            "misses":     self.misses,
            "hit_rate":   round(self.hits / total, 3) if total else 0.0,
            "evictions":  self.evictions,
            "sets":       self.sets,
        }


# ── Typed cache singletons (F31) ──────────────────────────────────────────────

#: Track URL → metadata dict (30 min TTL, 512 entries)
metadata_cache: LRUCache[dict]       = LRUCache("metadata",  max_size=512, ttl=1800.0)

#: Thumbnail URL → raw bytes (1 h TTL, 256 entries)
thumbnail_cache: LRUCache[bytes]     = LRUCache("thumbnail", max_size=256, ttl=3600.0)

#: Search query → list[Track] (10 min TTL, 256 entries)
search_cache: LRUCache[list]         = LRUCache("search",    max_size=256, ttl=600.0)


def all_caches() -> list[LRUCache]:
    """Return all registered cache instances."""
    return [metadata_cache, thumbnail_cache, search_cache]


async def combined_stats() -> dict:
    """Return stats for all caches."""
    return {c.name: c.stats() for c in all_caches()}


# ── Memory Pressure Handler (F32) ──────────────────────────────────────────────

# Pressure levels (RAM usage %)
_LEVEL_LIGHT      = 70   # prune expired entries only
_LEVEL_MODERATE   = 80   # evict 50 % of each cache
_LEVEL_AGGRESSIVE = 90   # clear all caches immediately

_last_action_level: int = 0   # track last level acted on (avoid storm)


def _try_import_psutil():
    try:
        import psutil
        return psutil
    except ImportError:
        return None


async def check_memory_pressure() -> dict:
    """
    Check current RAM usage and apply cache eviction if thresholds are exceeded.
    Returns a dict with { level, ram_pct, action, freed_entries }.
    Safe to call when psutil is not installed (returns level=0, no action).
    """
    global _last_action_level

    psutil = _try_import_psutil()
    if not psutil:
        return {"level": 0, "ram_pct": 0.0, "action": "psutil_unavailable", "freed": 0}

    ram_pct = psutil.virtual_memory().percent
    result  = {"ram_pct": ram_pct, "freed": 0, "action": "none", "level": 0}

    if ram_pct >= _LEVEL_AGGRESSIVE:
        result["level"]  = 3
        result["action"] = "aggressive_clear"
        freed = 0
        for c in all_caches():
            freed += await c.clear()
        result["freed"]  = freed
        logger.warning(
            "🔴 Memory pressure CRITICAL (%.1f%% RAM) — cleared all caches (%d entries freed)",
            ram_pct, freed,
        )
        _last_action_level = 3

    elif ram_pct >= _LEVEL_MODERATE:
        result["level"]  = 2
        result["action"] = "moderate_evict_50pct"
        freed = 0
        for c in all_caches():
            freed += await c.evict_fraction(0.5)
        result["freed"]  = freed
        logger.warning(
            "🟠 Memory pressure HIGH (%.1f%% RAM) — evicted 50%% of each cache (%d entries freed)",
            ram_pct, freed,
        )
        _last_action_level = 2

    elif ram_pct >= _LEVEL_LIGHT:
        result["level"]  = 1
        result["action"] = "light_prune_expired"
        freed = 0
        for c in all_caches():
            freed += await c.prune_expired()
        result["freed"]  = freed
        if freed:
            logger.info(
                "🟡 Memory pressure ELEVATED (%.1f%% RAM) — pruned %d expired cache entries",
                ram_pct, freed,
            )
        _last_action_level = 1

    else:
        _last_action_level = 0

    return result
