# -*- coding: utf-8 -*-
"""
core/media_cache.py — Predictive thumbnail and metadata cache for Music Bot V3.

Tier-S+ Feature 14: Predictive Thumbnail Cache
  - thumbnail_cache: URL → raw bytes (avoids re-downloading on np refresh)
  - metadata_cache:  track URL → Track metadata dict (avoids re-extracting)

Both caches are LRU-evicting in-memory stores with configurable TTL.
Pre-warming happens when tracks are enqueued (background task).

Feature 15: Background Metadata Refresh
  - bg_refresh_metadata(): re-fetches views/uploader/duration for a track
    after REFRESH_AFTER_SECS and updates the Track object in-place.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional, TYPE_CHECKING

import aiohttp

if TYPE_CHECKING:
    from models.track import Track
    from core.youtube import YouTubeExtractor

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

THUMBNAIL_CACHE_TTL  = 3600.0   # 1 hour
THUMBNAIL_CACHE_MAX  = 256      # max entries
METADATA_CACHE_TTL   = 1800.0   # 30 minutes
METADATA_CACHE_MAX   = 512
REFRESH_AFTER_SECS   = 3600     # refresh metadata if older than this
_PREFETCH_CONCURRENCY = 4       # simultaneous thumbnail pre-fetches


# ── In-memory stores ──────────────────────────────────────────────────────────

# thumbnail_url → (raw_bytes, timestamp)
_THUMB_CACHE: dict[str, tuple[bytes, float]] = {}
_THUMB_LOCK = asyncio.Lock()

# track_url → (metadata_dict, timestamp)
_META_CACHE: dict[str, tuple[dict, float]] = {}
_META_LOCK = asyncio.Lock()

_PREFETCH_SEM: asyncio.Semaphore | None = None


def _get_prefetch_sem() -> asyncio.Semaphore:
    global _PREFETCH_SEM
    if _PREFETCH_SEM is None:
        _PREFETCH_SEM = asyncio.Semaphore(_PREFETCH_CONCURRENCY)
    return _PREFETCH_SEM


# ── Thumbnail cache ───────────────────────────────────────────────────────────

async def get_thumbnail_bytes(
    url: str,
    session: Optional[aiohttp.ClientSession] = None,
) -> Optional[bytes]:
    """
    Return raw thumbnail bytes, fetching and caching if needed.
    Returns None on failure.
    """
    if not url:
        return None

    now = time.monotonic()

    async with _THUMB_LOCK:
        if url in _THUMB_CACHE:
            raw, ts = _THUMB_CACHE[url]
            if now - ts < THUMBNAIL_CACHE_TTL:
                return raw
            del _THUMB_CACHE[url]

    async with _get_prefetch_sem():
        try:
            close_session = session is None
            if close_session:
                session = aiohttp.ClientSession()
            try:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=8),
                    headers={"User-Agent": "MusicBotV3/1.0"},
                ) as resp:
                    if not resp.ok:
                        return None
                    raw = await resp.read()
            finally:
                if close_session and session:
                    await session.close()

            async with _THUMB_LOCK:
                _THUMB_CACHE[url] = (raw, time.monotonic())
                _evict(_THUMB_CACHE, THUMBNAIL_CACHE_MAX)

            return raw

        except Exception as exc:
            logger.debug("Thumbnail fetch failed for %s: %s", url[:60], exc)
            return None


def has_thumbnail(url: str) -> bool:
    """Check if thumbnail is already cached (no async needed)."""
    if url not in _THUMB_CACHE:
        return False
    _, ts = _THUMB_CACHE[url]
    return time.monotonic() - ts < THUMBNAIL_CACHE_TTL


# ── Metadata cache ────────────────────────────────────────────────────────────

async def get_cached_metadata(url: str) -> Optional[dict]:
    """Return cached metadata dict for a track URL, or None if expired/missing."""
    async with _META_LOCK:
        if url in _META_CACHE:
            data, ts = _META_CACHE[url]
            if time.monotonic() - ts < METADATA_CACHE_TTL:
                return data
            del _META_CACHE[url]
    return None


async def set_cached_metadata(url: str, data: dict) -> None:
    async with _META_LOCK:
        _META_CACHE[url] = (data, time.monotonic())
        _evict(_META_CACHE, METADATA_CACHE_MAX)


def _evict(cache: dict, max_size: int) -> None:
    """LRU eviction: remove oldest entry if over limit."""
    if len(cache) > max_size:
        oldest = min(cache, key=lambda k: cache[k][1])
        del cache[oldest]


# ── Predictive pre-warm ───────────────────────────────────────────────────────

async def prewarm_thumbnail(
    url: str,
    session: Optional[aiohttp.ClientSession] = None,
) -> None:
    """
    Background pre-warm: fetch + cache a thumbnail URL without blocking.
    No-op if already cached.
    """
    if not url or has_thumbnail(url):
        return
    await get_thumbnail_bytes(url, session)


async def prewarm_queue_thumbnails(
    tracks: list["Track"],
    session: Optional[aiohttp.ClientSession] = None,
    limit: int = 5,
) -> None:
    """
    Pre-warm thumbnails for the next `limit` tracks in queue.
    Called after each track change via create_task().
    """
    tasks = []
    for track in tracks[:limit]:
        if track.thumbnail and not has_thumbnail(track.thumbnail):
            tasks.append(asyncio.create_task(
                prewarm_thumbnail(track.thumbnail, session)
            ))
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


# ── Background Metadata Refresh (Feature 15) ─────────────────────────────────

async def bg_refresh_metadata(
    track:     "Track",
    extractor: "YouTubeExtractor",
) -> None:
    """
    Re-fetch view count, uploader, duration for a track in the background.
    Updates the Track object in-place if data has changed.

    Called via asyncio.create_task() — never blocks playback.
    Safe to call multiple times; uses metadata cache TTL to avoid hammering.
    """
    url = track.url
    if not url:
        return

    # Check if we already refreshed recently
    cached = await get_cached_metadata(url)
    if cached:
        # Already fresh — update track in-place from cache
        _apply_metadata(track, cached)
        return

    try:
        # Use extractor's get_track to re-fetch metadata
        fresh = await extractor.get_track(url)
        if not fresh:
            return

        data = {
            "view_count":  fresh.view_count,
            "uploader":    fresh.uploader,
            "duration":    fresh.duration,
            "thumbnail":   fresh.thumbnail,
            "upload_date": fresh.upload_date,
        }
        await set_cached_metadata(url, data)
        _apply_metadata(track, data)
        logger.debug("Metadata refreshed for: %s", track.title[:50])

    except Exception as exc:
        logger.debug("Metadata refresh failed for %s: %s", url[:60], exc)


def _apply_metadata(track: "Track", data: dict) -> None:
    """Apply fetched metadata to a Track object in-place."""
    if data.get("view_count") is not None:
        track.view_count = data["view_count"]
    if data.get("uploader"):
        track.uploader = data["uploader"]
    if data.get("duration"):
        track.duration = data["duration"]
    if data.get("thumbnail"):
        track.thumbnail = data["thumbnail"]
    if data.get("upload_date"):
        track.upload_date = data["upload_date"]


# ── Cache stats ───────────────────────────────────────────────────────────────

def cache_stats() -> dict:
    """Return cache size stats for monitoring."""
    return {
        "thumbnail_entries": len(_THUMB_CACHE),
        "metadata_entries":  len(_META_CACHE),
    }


# ── Prune expired entries ─────────────────────────────────────────────────────

def prune_caches() -> tuple[int, int]:
    """
    Remove expired entries from both caches.
    Returns (thumb_pruned, meta_pruned).
    Called from the existing _cache_prune background task.
    """
    now = time.monotonic()
    thumb_pruned = 0
    meta_pruned  = 0

    expired_thumb = [k for k, (_, ts) in _THUMB_CACHE.items()
                     if now - ts >= THUMBNAIL_CACHE_TTL]
    for k in expired_thumb:
        del _THUMB_CACHE[k]
        thumb_pruned += 1

    expired_meta = [k for k, (_, ts) in _META_CACHE.items()
                    if now - ts >= METADATA_CACHE_TTL]
    for k in expired_meta:
        del _META_CACHE[k]
        meta_pruned += 1

    return thumb_pruned, meta_pruned
