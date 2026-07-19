# -*- coding: utf-8 -*-
"""
core/youtube.py — YouTube data extraction via yt-dlp for Music Bot V3.

V3 Changes:
  - Periodic cache cleanup: prune_cache() called by background task (every 30 min)
  - Semaphore is now instance-level (not module-level) for cleaner lifecycle
  - All public methods fully type-annotated

Provides:
  - Single track extraction from URL
  - Text search with Track-level LRU cache
  - YouTube playlist extraction
  - Predictive stream prefetch (stores on Track object)
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Optional
from urllib.parse import urlparse

import yt_dlp

import config
from models.track import Track

logger = logging.getLogger(__name__)

_YOUTUBE_DOMAINS = frozenset([
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "music.youtube.com", "youtu.be",
])

# ── yt-dlp option presets ─────────────────────────────────────────────────────

_META_OPTS: dict = {
    "format":             config.YTDL_AUDIO_FORMAT,
    "quiet":              True,
    "no_warnings":        True,
    "ignoreerrors":       True,
    "default_search":     "ytsearch",
    "nocheckcertificate": True,
    "source_address":     "0.0.0.0",
    "noplaylist":         True,
    "extract_flat":       True,
    "geo_bypass":         True,
    "cachedir":           False,
    "retries":            config.YTDL_RETRIES,
    "socket_timeout":     10,
    "skip_download":      True,
}

_STREAM_OPTS: dict = {
    "format":                        config.YTDL_AUDIO_FORMAT,
    "quiet":                         True,
    "no_warnings":                   True,
    "ignoreerrors":                  False,
    "nocheckcertificate":            True,
    "source_address":                "0.0.0.0",
    "noplaylist":                    True,
    "extract_flat":                  False,
    "geo_bypass":                    True,
    "cachedir":                      False,
    "retries":                       2,
    "socket_timeout":                10,
    "skip_download":                 True,
    "youtube_include_dash_manifest": False,
}

_PLAYLIST_OPTS: dict = {
    "format":             config.YTDL_AUDIO_FORMAT,
    "quiet":              True,
    "no_warnings":        True,
    "ignoreerrors":       True,
    "nocheckcertificate": True,
    "source_address":     "0.0.0.0",
    "noplaylist":         False,
    "extract_flat":       "in_playlist",
    "geo_bypass":         True,
    "cachedir":           False,
    "retries":            config.YTDL_RETRIES,
    "socket_timeout":     20,
    "skip_download":      True,
}

import pathlib
if pathlib.Path("cookies.txt").exists():
    for _opts in (_META_OPTS, _STREAM_OPTS, _PLAYLIST_OPTS):
        _opts["cookiefile"] = "cookies.txt"


class YouTubeExtractor:
    """Thread-safe, cached YouTube extractor wrapping yt-dlp."""

    def __init__(self) -> None:
        # Raw metadata cache: {cache_key: (data, timestamp)}
        self._cache:      dict[str, tuple[dict, float]] = {}
        self._cache_lock: asyncio.Lock = asyncio.Lock()

        # Search track cache: {query::maxresults: (tracks, timestamp)}
        self._search_cache:      dict[str, tuple[list[Track], float]] = {}
        self._search_cache_lock: asyncio.Lock = asyncio.Lock()

        # Instance-level semaphore for extraction concurrency
        self._extract_sem: asyncio.Semaphore | None = None

    def _get_sem(self) -> asyncio.Semaphore:
        if self._extract_sem is None:
            self._extract_sem = asyncio.Semaphore(config.EXTRACT_CONCURRENCY)
        return self._extract_sem

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _clean_url(url: str) -> str:
        if "youtube.com" in url or "youtu.be" in url:
            url = re.sub(r"[&?]list=[^&]*", "", url)
            url = re.sub(r"[&?]index=[^&]*", "", url)
            url = re.sub(r"[&?]start_radio=[^&]*", "", url)
        return url

    @staticmethod
    def _run_ytdl(opts: dict, query: str) -> dict | None:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(query, download=False)

    async def _extract(
        self,
        query:     str,
        *,
        opts:      dict | None = None,
        use_cache: bool         = True,
        timeout:   float | None = None,
    ) -> dict | None:
        """
        Extract info with optional caching, retry, and concurrency control.
        Exponential backoff: 1s → 2s → 4s (capped at 8s).
        """
        if opts is None:
            opts = _META_OPTS
        if timeout is None:
            timeout = config.YTDL_TIMEOUT

        cache_key = f"{query}::{id(opts)}"

        if use_cache:
            async with self._cache_lock:
                if cache_key in self._cache:
                    data, ts = self._cache[cache_key]
                    if time.monotonic() - ts < config.YTDL_CACHE_TIMEOUT:
                        return data
                    del self._cache[cache_key]

        loop = asyncio.get_running_loop()
        last_exc: Exception | None = None

        async with self._get_sem():
            for attempt in range(1, config.YTDL_RETRIES + 1):
                try:
                    result = await asyncio.wait_for(
                        loop.run_in_executor(None, self._run_ytdl, opts, query),
                        timeout=timeout,
                    )
                    if result and use_cache:
                        async with self._cache_lock:
                            self._cache[cache_key] = (result, time.monotonic())
                            if len(self._cache) > config.YTDL_CACHE_MAX_SIZE:
                                oldest = min(self._cache, key=lambda k: self._cache[k][1])
                                del self._cache[oldest]
                    return result

                except asyncio.TimeoutError:
                    backoff = min(2 ** (attempt - 1), 8)
                    logger.warning("yt-dlp timeout attempt %d/%d for %r — backoff %ds",
                                   attempt, config.YTDL_RETRIES, query[:60], backoff)
                    last_exc = asyncio.TimeoutError()

                except Exception as exc:
                    backoff = min(2 ** (attempt - 1), 8)
                    logger.warning("yt-dlp error attempt %d/%d: %s", attempt, config.YTDL_RETRIES, exc)
                    last_exc = exc

                if attempt < config.YTDL_RETRIES:
                    await asyncio.sleep(min(2 ** (attempt - 1), 8))

        logger.error("yt-dlp failed after %d attempts for %r: %s",
                     config.YTDL_RETRIES, query[:60], last_exc)
        return None

    @staticmethod
    def _entry_to_track(entry: dict) -> Track | None:
        title = entry.get("title")
        url   = entry.get("webpage_url") or entry.get("url")
        if not title or not url:
            return None
        duration = entry.get("duration") or 0
        if duration > config.MAX_TRACK_LENGTH:
            return None
        return Track(
            title       = title,
            url         = url,
            duration    = int(duration),
            thumbnail   = entry.get("thumbnail"),
            uploader    = entry.get("uploader", "Unknown"),
            view_count  = entry.get("view_count"),
            upload_date = entry.get("upload_date"),
        )

    @staticmethod
    def _extract_stream_url(entry: dict) -> str | None:
        formats = entry.get("formats") or []
        if formats:
            audio_only = [
                f for f in formats
                if f.get("vcodec") in ("none", None, "") and f.get("url")
            ]
            candidates = audio_only or [f for f in formats if f.get("url")]
            if candidates:
                best = max(candidates, key=lambda f: (f.get("abr") or f.get("tbr") or 0))
                stream = best.get("url")
                if stream:
                    return stream
        stream = entry.get("url")
        if (
            stream
            and not stream.startswith("http://www.youtube")
            and not stream.startswith("https://www.youtube")
            and "youtu" not in stream
        ):
            return stream
        return None

    # ── Cache maintenance (V3 NEW) ────────────────────────────────────────────

    async def prune_cache(self) -> tuple[int, int]:
        """
        Remove expired entries from both caches.
        Called by a background task every 30 minutes.
        Returns (raw_pruned, search_pruned) counts.
        """
        now = time.monotonic()
        raw_pruned = 0
        search_pruned = 0

        async with self._cache_lock:
            expired = [k for k, (_, ts) in self._cache.items()
                       if now - ts >= config.YTDL_CACHE_TIMEOUT]
            for k in expired:
                del self._cache[k]
                raw_pruned += 1

        async with self._search_cache_lock:
            expired = [k for k, (_, ts) in self._search_cache.items()
                       if now - ts >= config.SEARCH_CACHE_TTL]
            for k in expired:
                del self._search_cache[k]
                search_pruned += 1

        if raw_pruned or search_pruned:
            logger.debug("Cache prune: %d raw, %d search entries removed.", raw_pruned, search_pruned)
        return raw_pruned, search_pruned

    # ── Public API ────────────────────────────────────────────────────────────

    def is_youtube_url(self, url: str) -> bool:
        try:
            return urlparse(url).netloc.lower().lstrip("www.") in _YOUTUBE_DOMAINS
        except Exception:
            return False

    def is_playlist_url(self, url: str) -> bool:
        return "list=" in url and "youtube.com" in url

    async def get_track(self, url: str) -> Optional[Track]:
        url  = self._clean_url(url)
        info = await self._extract(url, opts={**_META_OPTS, "extract_flat": False})
        if not info:
            return None
        entry = (info.get("entries") or [info])[0]
        if not entry:
            return None
        return self._entry_to_track(entry)

    async def get_stream_url(self, url: str, track: Optional[Track] = None) -> str | None:
        """
        Resolve a YouTube webpage URL to a direct CDN audio-stream URL.
        Uses pre-fetch cache on Track if available (instant, zero yt-dlp cost).
        """
        if track and track.stream_url_cache and track.stream_url_expires:
            if time.monotonic() < track.stream_url_expires:
                logger.debug("Pre-fetch cache hit for '%s'", url[:60])
                return track.stream_url_cache

        url  = self._clean_url(url)
        loop = asyncio.get_running_loop()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, self._run_ytdl, _STREAM_OPTS, url),
                timeout=config.YTDL_STREAM_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.error("get_stream_url timed out for '%s'", url[:60])
            raise
        except Exception as exc:
            logger.error("get_stream_url failed for '%s': %s", url[:60], exc)
            raise

        if not result:
            return None
        entry = (result.get("entries") or [result])[0]
        if not entry:
            return None
        return self._extract_stream_url(entry)

    async def prefetch_stream_url(self, track: Track) -> None:
        """
        Background prefetch — stores CDN URL on track.stream_url_cache.
        Silently swallows all exceptions (non-fatal).
        """
        try:
            url  = self._clean_url(track.url)
            loop = asyncio.get_running_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(None, self._run_ytdl, _STREAM_OPTS, url),
                timeout=config.YTDL_STREAM_TIMEOUT + 5,
            )
            if not result:
                return
            entry = (result.get("entries") or [result])[0]
            if not entry:
                return
            stream_url = self._extract_stream_url(entry)
            if stream_url:
                track.stream_url_cache   = stream_url
                track.stream_url_expires = time.monotonic() + config.STREAM_URL_TTL
                logger.debug("Pre-fetched stream URL for '%s'", track.title[:50])
        except Exception as exc:
            logger.debug("Pre-fetch failed for '%s': %s", track.url[:60], exc)

    async def search(self, query: str, max_results: int = 10) -> list[Track]:
        """Search YouTube and return up to max_results Track objects."""
        if not query or not query.strip():
            return []

        cache_key = f"{query.strip()}::{max_results}"
        now = time.monotonic()

        async with self._search_cache_lock:
            entry = self._search_cache.get(cache_key)
            if entry:
                tracks, ts = entry
                if now - ts < config.SEARCH_CACHE_TTL:
                    logger.debug("Search cache hit for '%s'", query)
                    return tracks
                del self._search_cache[cache_key]

        info = await self._extract(
            f"ytsearch{max_results}:{query.strip()}",
            opts={**_META_OPTS, "extract_flat": False},
        )
        if not info or "entries" not in info:
            return []

        tracks: list[Track] = []
        for entry_item in info["entries"]:
            if not entry_item:
                continue
            track = self._entry_to_track(entry_item)
            if track:
                tracks.append(track)

        if tracks:
            async with self._search_cache_lock:
                self._search_cache[cache_key] = (tracks, time.monotonic())
                if len(self._search_cache) > config.SEARCH_CACHE_MAX_SIZE:
                    oldest = min(self._search_cache, key=lambda k: self._search_cache[k][1])
                    del self._search_cache[oldest]

        return tracks

    async def get_playlist(self, url: str, max_tracks: int = 50) -> list[Track]:
        """Extract up to max_tracks tracks from a YouTube playlist (flat)."""
        logger.info("Extracting playlist (max %d): %s", max_tracks, url)
        loop = asyncio.get_running_loop()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, self._run_ytdl, _PLAYLIST_OPTS, url),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            logger.error("Playlist extraction timed out: %s", url)
            return []
        except Exception as exc:
            logger.error("Playlist extraction failed: %s", exc)
            return []

        if not result or "entries" not in result:
            return []

        tracks: list[Track] = []
        for entry in result["entries"][:max_tracks]:
            if not entry:
                continue
            title     = entry.get("title") or entry.get("ie_key", "Unknown")
            video_id  = entry.get("id", "")
            video_url = (
                entry.get("url")
                or entry.get("webpage_url")
                or (f"https://www.youtube.com/watch?v={video_id}" if video_id else None)
            )
            if not video_url or not title:
                continue
            duration = int(entry.get("duration") or 0)
            if duration > config.MAX_TRACK_LENGTH:
                continue
            tracks.append(Track(
                title       = title,
                url         = video_url,
                duration    = duration,
                thumbnail   = entry.get("thumbnail"),
                uploader    = entry.get("uploader") or entry.get("channel", "Unknown"),
                view_count  = entry.get("view_count"),
                upload_date = entry.get("upload_date"),
            ))

        logger.info("Extracted %d/%d tracks from playlist", len(tracks), max_tracks)
        return tracks
