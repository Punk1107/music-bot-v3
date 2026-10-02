# -*- coding: utf-8 -*-
"""
sources/bandcamp.py — Bandcamp native extractor for Music Bot V3 Feature 1.3.

Uses yt-dlp's built-in Bandcamp extractor.
Supports:
  - Single track URLs: artist.bandcamp.com/track/track-name
  - Album URLs:        artist.bandcamp.com/album/album-name  (up to 50 tracks)
  - Non-subdomain:     bandcamp.com/... (rare but supported)
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

import yt_dlp

import config
from models.track import Track

logger = logging.getLogger(__name__)

# ── URL detection ────────────────────────────────────────────────────────────

# Matches both artist.bandcamp.com/... and bandcamp.com/...
_BC_DOMAIN_RE = re.compile(
    r"https?://(?:[a-z0-9-]+\.)?bandcamp\.com",
    re.IGNORECASE,
)


def is_bandcamp_url(url: str) -> bool:
    """Return True if the URL is a Bandcamp domain."""
    return bool(_BC_DOMAIN_RE.match(url.strip()))


def is_bandcamp_album(url: str) -> bool:
    """Return True if the URL points to an album page."""
    return "/album/" in url.lower()


# ── yt-dlp options ───────────────────────────────────────────────────────────

def _make_bc_opts(extract_flat: bool = False) -> dict:
    return {
        "format":             "bestaudio/best",
        "quiet":              True,
        "no_warnings":        True,
        "ignoreerrors":       True,
        "nocheckcertificate": True,
        "source_address":     "0.0.0.0",
        "noplaylist":         not extract_flat,
        "extract_flat":       "in_playlist" if extract_flat else False,
        "geo_bypass":         True,
        "cachedir":           False,
        "retries":            config.YTDL_RETRIES,
        "socket_timeout":     15,
        "skip_download":      True,
    }


def _run_bc_ytdl(opts: dict, url: str) -> Optional[dict]:
    """Synchronous yt-dlp call — run via run_in_executor."""
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as exc:
        logger.warning("Bandcamp yt-dlp error: %s", exc)
        return None


def _entry_to_track(entry: dict) -> Optional[Track]:
    """Convert a yt-dlp Bandcamp entry dict into a Track model."""
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


class BandcampExtractor:
    """
    Wraps yt-dlp to extract Bandcamp tracks and albums as Track objects.
    """

    async def get_track(self, url: str) -> Optional[Track]:
        """Resolve a single Bandcamp track URL → Track."""
        loop = asyncio.get_running_loop()
        opts = _make_bc_opts(extract_flat=False)
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, _run_bc_ytdl, opts, url),
                timeout=config.YTDL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.error("Bandcamp get_track timed out: %s", url[:80])
            return None

        if not result:
            return None
        entry = (result.get("entries") or [result])[0]
        return _entry_to_track(entry) if entry else None

    async def get_album(self, url: str, max_tracks: int = 50) -> list[Track]:
        """Resolve a Bandcamp album URL → list of Tracks."""
        loop = asyncio.get_running_loop()
        opts = _make_bc_opts(extract_flat=True)
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, _run_bc_ytdl, opts, url),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            logger.error("Bandcamp get_album timed out: %s", url[:80])
            return []

        if not result:
            return []

        if "entries" in result:
            raw_entries = (result["entries"] or [])[:max_tracks]
        else:
            raw_entries = [result]

        tracks: list[Track] = []
        for entry in raw_entries:
            if not entry:
                continue
            track = _entry_to_track(entry)
            if track:
                tracks.append(track)

        logger.info("Bandcamp album: extracted %d tracks from %s", len(tracks), url[:60])
        return tracks

    async def get_stream_url(self, url: str) -> Optional[str]:
        """Resolve a Bandcamp URL to a direct audio stream URL."""
        loop = asyncio.get_running_loop()
        opts = {**_make_bc_opts(extract_flat=False), "format": "bestaudio"}
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, _run_bc_ytdl, opts, url),
                timeout=config.YTDL_STREAM_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.error("Bandcamp get_stream_url timed out: %s", url[:80])
            return None
        if not result:
            return None
        entry = (result.get("entries") or [result])[0]
        if not entry:
            return None
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
