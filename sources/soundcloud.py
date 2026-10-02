# -*- coding: utf-8 -*-
"""
sources/soundcloud.py — SoundCloud native extractor for Music Bot V3 Feature 1.3.

Uses yt-dlp's built-in SoundCloud extractor.
Supports:
  - Direct track URLs (soundcloud.com/artist/track, m.soundcloud.com/..., snd.sc/...)
  - Playlist/Sets URLs (soundcloud.com/artist/sets/name)
  - Keyword search via scsearch: prefix
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import yt_dlp

import config
from models.track import Track

logger = logging.getLogger(__name__)

# ── Recognized SoundCloud URL patterns ──────────────────────────────────────

_SC_DOMAINS = frozenset([
    "soundcloud.com",
    "www.soundcloud.com",
    "m.soundcloud.com",
    "on.soundcloud.com",
    "snd.sc",
])


def is_soundcloud_url(url: str) -> bool:
    """Return True if the URL belongs to SoundCloud."""
    try:
        from urllib.parse import urlparse
        netloc = urlparse(url).netloc.lower().lstrip("www.")
        return netloc in _SC_DOMAINS
    except Exception:
        return False


def is_soundcloud_query(query: str) -> bool:
    """Return True if the query is a scsearch: prefixed search."""
    return query.strip().lower().startswith("scsearch:")


# ── yt-dlp option presets ───────────────────────────────────────────────────

def _make_sc_opts(extract_flat: bool = False, max_results: int = 1) -> dict:
    return {
        "format":             "bestaudio/best",
        "quiet":              True,
        "no_warnings":        True,
        "ignoreerrors":       True,
        "nocheckcertificate": True,
        "source_address":     "0.0.0.0",
        "noplaylist":         False,     # allow sets
        "extract_flat":       "in_playlist" if extract_flat else False,
        "geo_bypass":         True,
        "cachedir":           False,
        "retries":            config.YTDL_RETRIES,
        "socket_timeout":     15,
        "skip_download":      True,
        "default_search":     f"scsearch{max_results}",
    }


def _run_sc_ytdl(opts: dict, query: str) -> Optional[dict]:
    """Synchronous yt-dlp call — run via run_in_executor."""
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(query, download=False)
    except Exception as exc:
        logger.warning("SoundCloud yt-dlp error: %s", exc)
        return None


def _entry_to_track(entry: dict) -> Optional[Track]:
    """Convert a yt-dlp info dict entry into a Track model."""
    title = entry.get("title")
    url   = entry.get("webpage_url") or entry.get("url")
    if not title or not url:
        return None
    duration = int(entry.get("duration") or 0)
    if duration > config.MAX_TRACK_LENGTH:
        return None
    return Track(
        title       = title,
        url         = url,
        duration    = duration,
        thumbnail   = entry.get("thumbnail"),
        uploader    = entry.get("uploader") or entry.get("artist") or "Unknown",
        view_count  = entry.get("view_count"),
        upload_date = entry.get("upload_date"),
    )


class SoundCloudExtractor:
    """
    Wraps yt-dlp to extract SoundCloud track/set/search results as Track objects.
    """

    async def get_track(self, url: str) -> Optional[Track]:
        """Resolve a single SoundCloud track URL → Track."""
        loop = asyncio.get_running_loop()
        opts = _make_sc_opts(extract_flat=False)
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, _run_sc_ytdl, opts, url),
                timeout=config.YTDL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.error("SoundCloud get_track timed out: %s", url[:80])
            return None

        if not result:
            return None
        entry = (result.get("entries") or [result])[0]
        return _entry_to_track(entry) if entry else None

    async def get_set(self, url: str, max_tracks: int = 50) -> list[Track]:
        """Resolve a SoundCloud set/playlist URL → list of Tracks."""
        loop = asyncio.get_running_loop()
        opts = _make_sc_opts(extract_flat=True)
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, _run_sc_ytdl, opts, url),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            logger.error("SoundCloud get_set timed out: %s", url[:80])
            return []

        if not result or "entries" not in result:
            return []

        tracks: list[Track] = []
        for entry in (result["entries"] or [])[:max_tracks]:
            if not entry:
                continue
            # Flat entries only have url/id — resolve each individually if needed
            track_url = entry.get("url") or entry.get("webpage_url")
            if not track_url:
                continue
            title = entry.get("title") or "Unknown"
            duration = int(entry.get("duration") or 0)
            tracks.append(Track(
                title       = title,
                url         = track_url,
                duration    = duration,
                thumbnail   = entry.get("thumbnail"),
                uploader    = entry.get("uploader") or "Unknown",
                view_count  = entry.get("view_count"),
                upload_date = entry.get("upload_date"),
            ))

        logger.info("SoundCloud set: extracted %d tracks", len(tracks))
        return tracks

    async def search(self, query: str, max_results: int = 5) -> list[Track]:
        """
        Search SoundCloud for query and return up to max_results Track objects.
        Prefix the query with 'scsearch:' if not already prefixed.
        """
        loop = asyncio.get_running_loop()
        sc_query = query if is_soundcloud_query(query) else f"scsearch{max_results}:{query}"
        opts = _make_sc_opts(extract_flat=False, max_results=max_results)
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, _run_sc_ytdl, opts, sc_query),
                timeout=config.YTDL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.error("SoundCloud search timed out: %r", query[:60])
            return []

        if not result:
            return []

        if "entries" in result:
            tracks = [_entry_to_track(e) for e in (result["entries"] or []) if e]
        else:
            t = _entry_to_track(result)
            tracks = [t] if t else []

        return [t for t in tracks if t is not None]

    async def get_stream_url(self, url: str) -> Optional[str]:
        """Resolve a SoundCloud URL to a direct audio stream URL."""
        loop = asyncio.get_running_loop()
        opts = {**_make_sc_opts(extract_flat=False), "format": "bestaudio"}
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, _run_sc_ytdl, opts, url),
                timeout=config.YTDL_STREAM_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.error("SoundCloud get_stream_url timed out: %s", url[:80])
            return None
        if not result:
            return None
        entry = (result.get("entries") or [result])[0]
        if not entry:
            return None
        # Try formats first
        formats = entry.get("formats") or []
        if formats:
            audio_fmt = sorted(
                [f for f in formats if f.get("url") and f.get("acodec") not in (None, "none", "")],
                key=lambda f: f.get("abr") or 0,
                reverse=True,
            )
            if audio_fmt:
                return audio_fmt[0]["url"]
        return entry.get("url")
