# -*- coding: utf-8 -*-
"""
autoplay/service.py — Smart Autoplay service for Music Bot V3 (Feature 1.4).

Strategy:
  1. When the queue empties, inspect the seed track (last-played YouTube video).
  2. Build a YouTube "Radio Mix" URL:  watch?v={id}&list=RD{id}
  3. Extract the next N related tracks with yt-dlp (extract_flat, ~1-2 s).
  4. Deduplicate against a per-guild rolling history deque (maxlen configurable).
  5. Return the first unseen track; caller enqueues it via player.enqueue().

For non-YouTube seeds (Spotify, SoundCloud, Bandcamp):
  The seed title/artist string is used to run a YouTube search first, then we
  derive the Radio Mix URL from the top search result's video ID.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import re
import time
from collections import deque
from typing import TYPE_CHECKING, Optional
from urllib.parse import urlparse, parse_qs

import yt_dlp

import config
from models.track import Track

if TYPE_CHECKING:
    from core.youtube import YouTubeExtractor

logger = logging.getLogger(__name__)

# ── yt-dlp options for Radio Mix extraction ───────────────────────────────────

_RELATED_OPTS: dict = {
    # Inherit common opts (cookies, PO-token, extractor_args) from youtube.py
    # We replicate only what's needed here to keep the service self-contained.
    "js_runtimes":       {"node": {}},
    "remote_components": ["ejs:github"],
    "extractor_args": {
        "youtube": {
            "player_client": ["android", "mweb", "web"],
        }
    },
    "format":        config.YTDL_AUDIO_FORMAT,
    "quiet":         True,
    "no_warnings":   True,
    "ignoreerrors":  True,
    "nocheckcertificate": True,
    "source_address": "0.0.0.0",
    "geo_bypass":    True,
    "cachedir":      False,
    "skip_download": True,
    "extract_flat":  "in_playlist",
    # Skip item 1 (the seed video itself); grab the next FETCH_SIZE tracks.
    "playlist_items": f"2-{config.SMART_AUTOPLAY_FETCH_SIZE + 1}",
    "socket_timeout": 15,
}

# Inject cookies / PO-Token if configured (mirrors core/youtube.py)
from pathlib import Path as _Path
_cookie_path = _Path(config.YTDL_COOKIE_FILE)
if _cookie_path.is_file():
    _RELATED_OPTS["cookiefile"] = str(_cookie_path)
if config.YTDL_PO_TOKEN:
    _RELATED_OPTS.setdefault("extractor_args", {}) \
                 .setdefault("youtube", {})["po_token"] = [config.YTDL_PO_TOKEN]

# ── Helpers ───────────────────────────────────────────────────────────────────

_YT_ID_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?.*v=|shorts/)|youtu\.be/)([A-Za-z0-9_-]{11})"
)


def _extract_video_id(url: str) -> Optional[str]:
    """Parse a YouTube video ID from a watch/shorts/youtu.be URL or raw ID."""
    if not url:
        return None
    url = url.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url):
        return url
    m = _YT_ID_RE.search(url)
    if m:
        return m.group(1)
    # Fallback: check query string directly
    try:
        qs = parse_qs(urlparse(url).query)
        v = qs.get("v", [])
        if v and len(v[0]) == 11:
            return v[0]
    except Exception:
        pass
    return None


extract_video_id = _extract_video_id



def _radio_url(video_id: str) -> str:
    """Build the YouTube Radio Mix URL for a given video ID."""
    return f"https://www.youtube.com/watch?v={video_id}&list=RD{video_id}"


def _entry_to_track(entry: dict) -> Optional[Track]:
    """Convert a flat yt-dlp playlist entry to a Track, applying duration guard."""
    video_id = entry.get("id") or entry.get("url", "")
    title    = entry.get("title")
    url      = entry.get("url") or (
        f"https://www.youtube.com/watch?v={video_id}" if video_id else None
    )
    if not title or not url:
        return None
    duration = int(entry.get("duration") or 0)
    if duration and duration > config.MAX_TRACK_LENGTH:
        return None
    return Track(
        title       = title,
        url         = url,
        duration    = duration,
        thumbnail   = entry.get("thumbnail"),
        uploader    = entry.get("uploader") or entry.get("channel", "Unknown"),
        view_count  = entry.get("view_count"),
        upload_date = entry.get("upload_date"),
    )


# ── AutoplayService ───────────────────────────────────────────────────────────

class AutoplayService:
    """
    Manages Smart Autoplay per guild.

    Public API (all async):
      get_next_track(guild_id, seed_track) -> Optional[Track]
      reset_history(guild_id) -> None
    """

    def __init__(self, youtube: "YouTubeExtractor") -> None:
        self._youtube = youtube
        # Per-guild rolling dedup history: {guild_id: deque[video_id or title]}
        self._history: dict[int, deque[str]] = {}
        self._lock:    asyncio.Lock = asyncio.Lock()
        # Perf-2: In-memory recommendation cache {video_id: (tracks, timestamp)}
        self._recommendation_cache: dict[str, tuple[list[Track], float]] = {}
        self._cache_ttl: float = 3600.0  # 1 hour TTL
        self._cache_max: int = 256
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(2)

    def clear_cache(self) -> None:
        """Clear recommendation cache (useful for testing or memory reclaim)."""
        self._recommendation_cache.clear()

    # ── History helpers ───────────────────────────────────────────────────────

    def _get_history(self, guild_id: int) -> deque[str]:
        if guild_id not in self._history:
            self._history[guild_id] = deque(maxlen=config.SMART_AUTOPLAY_HISTORY_SIZE)
        return self._history[guild_id]

    async def reset_history(self, guild_id: int) -> None:
        """Clear the autoplay dedup history for a guild (called on /stop or /leave)."""
        async with self._lock:
            self._history.pop(guild_id, None)

    def _is_seen(self, guild_id: int, track_key: str) -> bool:
        return track_key in self._get_history(guild_id)

    def _mark_seen(self, guild_id: int, track_key: str) -> None:
        self._get_history(guild_id).append(track_key)

    @staticmethod
    def _track_key(track: Track) -> str:
        """Stable dedup key: video ID when available, else normalised title."""
        vid = _extract_video_id(track.url)
        return vid if vid else track.title.strip().lower()

    extract_video_id = staticmethod(_extract_video_id)

    # ── Core extraction ───────────────────────────────────────────────────────

    async def fetch_related_tracks(self, video_id: str, limit: int = 8) -> list[Track]:
        """Fetch up to limit related tracks for a YouTube video using Radio Mix."""
        now = time.monotonic()
        if video_id in self._recommendation_cache:
            cached_tracks, ts = self._recommendation_cache[video_id]
            if now - ts < self._cache_ttl:
                logger.debug("Smart Autoplay: cache HIT for video_id=%s", video_id)
                return [copy.copy(t) for t in cached_tracks[:limit]]
            else:
                self._recommendation_cache.pop(video_id, None)

        opts = dict(_RELATED_OPTS)
        opts["playlist_items"] = f"2-{limit + 1}"
        url  = _radio_url(video_id)
        loop = asyncio.get_running_loop()
        try:
            async with self._semaphore:
                result = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: self._run_ytdl(opts, url),
                    ),
                    timeout=25.0,
                )
        except asyncio.TimeoutError:
            logger.warning("Smart Autoplay: yt-dlp timed out for video_id=%s", video_id)
            return []
        except Exception as exc:
            logger.warning("Smart Autoplay: fetch error for video_id=%s: %s", video_id, exc)
            return []

        if not result or "entries" not in result:
            return []

        tracks: list[Track] = []
        for entry in result["entries"]:
            if not entry:
                continue
            track = _entry_to_track(entry)
            if track:
                tracks.append(track)
                if len(tracks) >= limit:
                    break
        logger.debug("Smart Autoplay: fetched %d related tracks for %s", len(tracks), video_id)
        if tracks:
            if len(self._recommendation_cache) >= self._cache_max:
                oldest_k = next(iter(self._recommendation_cache))
                self._recommendation_cache.pop(oldest_k, None)
            self._recommendation_cache[video_id] = ([copy.copy(t) for t in tracks], time.monotonic())
        return tracks

    async def _fetch_related(self, video_id: str) -> list[Track]:
        """Run yt-dlp against the YouTube Radio Mix for video_id."""
        return await self.fetch_related_tracks(video_id, limit=config.SMART_AUTOPLAY_FETCH_SIZE)

    @staticmethod
    def _run_ytdl(opts: dict, url: str) -> dict | None:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)

    async def _resolve_seed_video_id(self, seed: Track) -> Optional[str]:
        """
        Extract a YouTube video ID from seed.url.
        Falls back to a YouTube text search when seed is from Spotify / SoundCloud.
        """
        vid = _extract_video_id(seed.url)
        if vid:
            return vid

        # Non-YouTube seed: search YouTube for artist + title
        query = f"{seed.uploader or ''} {seed.title}".strip()
        if not query:
            return None
        logger.debug("Smart Autoplay: seed not YouTube, searching for '%s'", query[:60])
        try:
            results = await self._youtube.search(query, max_results=1)
        except Exception:
            return None
        if not results:
            return None
        return _extract_video_id(results[0].url)

    # ── Public API ────────────────────────────────────────────────────────────

    async def get_next_track(
        self,
        guild_id: int,
        seed_track: Optional[Track],
    ) -> Optional[Track]:
        """
        Return the next related track for the guild, or None if unavailable.

        Deduplication ensures a track is never returned twice within the rolling
        SMART_AUTOPLAY_HISTORY_SIZE window.
        """
        if not seed_track:
            logger.debug("Smart Autoplay: no seed track — skipping")
            return None

        video_id = await self._resolve_seed_video_id(seed_track)
        if not video_id:
            logger.debug("Smart Autoplay: could not resolve video ID for '%s'", seed_track.title[:50])
            return None

        # Mark the seed as seen so we don't autoplay it again immediately.
        async with self._lock:
            self._mark_seen(guild_id, video_id)

        related = await self._fetch_related(video_id)
        if not related:
            return None

        async with self._lock:
            for track in related:
                key = self._track_key(track)
                if not self._is_seen(guild_id, key):
                    self._mark_seen(guild_id, key)
                    logger.info(
                        "Smart Autoplay [guild %d]: → '%s'  (seed: '%s')",
                        guild_id, track.title[:50], seed_track.title[:50],
                    )
                    return track

        logger.debug(
            "Smart Autoplay [guild %d]: all %d related tracks already seen",
            guild_id, len(related),
        )
        return None
