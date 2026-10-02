# -*- coding: utf-8 -*-
"""
lyrics/service.py — Subtitle fetching and lyrics resolution for Music Bot V3 Feature 1.1.

Workflow:
  1. Extract subtitle/caption metadata from yt-dlp (no re-download of stream)
  2. Choose best subtitle by language priority: th → th-* → en → en-* → first available
  3. Prefer VTT; fall back to SRT; then auto_captions if no manual subtitles
  4. Download the subtitle text via aiohttp (no local file write)
  5. Parse and return list[LyricLine] via lyrics.parser
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional, TYPE_CHECKING

import yt_dlp

from lyrics.parser import LyricLine, parse_vtt, parse_srt

if TYPE_CHECKING:
    import aiohttp
    from models.track import Track

logger = logging.getLogger(__name__)

# ── Language preference order ──────────────────────────────────────────────

_LANG_PRIORITY = ["th", "th-TH", "en", "en-US", "en-GB", "en-AU"]


# ── yt-dlp options for subtitle metadata only (no download) ───────────────

_SUBTITLE_OPTS: dict = {
    "quiet":              True,
    "no_warnings":        True,
    "ignoreerrors":       True,
    "nocheckcertificate": True,
    "skip_download":      True,
    "extract_flat":       False,
    "noplaylist":         True,
    "writesubtitles":     False,   # we fetch ourselves
    "writeautomaticsub":  False,
    "subtitlesformat":    "vtt",
}


def _pick_subtitle_url(
    subs_dict: dict,
    auto_dict: dict,
) -> tuple[Optional[str], bool]:
    """
    Choose the best subtitle URL from available manual and auto-caption dicts.

    Returns (url, is_auto_caption).
    Prefers manual subtitles; falls back to auto-captions.
    Both dicts have the structure: {lang_code: [{"url": str, "ext": str}, ...]}
    """
    def _best_url_from_lang(lang_entries: list[dict]) -> Optional[str]:
        # Prefer VTT, then any ext
        for fmt in lang_entries:
            if fmt.get("ext") == "vtt":
                return fmt.get("url")
        # Fallback to first available URL
        for fmt in lang_entries:
            url = fmt.get("url")
            if url:
                return url
        return None

    # Try manual subtitles first with priority languages
    for lang in _LANG_PRIORITY:
        if lang in subs_dict:
            url = _best_url_from_lang(subs_dict[lang])
            if url:
                return url, False

    # Try any manual subtitle language
    for lang, entries in subs_dict.items():
        url = _best_url_from_lang(entries)
        if url:
            return url, False

    # Fall back to auto-captions
    for lang in _LANG_PRIORITY:
        if lang in auto_dict:
            url = _best_url_from_lang(auto_dict[lang])
            if url:
                return url, True

    for lang, entries in auto_dict.items():
        url = _best_url_from_lang(entries)
        if url:
            return url, True

    return None, False


async def _fetch_subtitle_text(url: str, session: "aiohttp.ClientSession") -> Optional[str]:
    """Download the subtitle file content as a string."""
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                logger.warning("Subtitle fetch returned HTTP %d for %s", resp.status, url[:80])
                return None
            return await resp.text(encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.warning("Subtitle download failed: %s", exc)
        return None


def _extract_subtitle_metadata(video_url: str) -> dict:
    """
    Run yt-dlp synchronously to get subtitle/caption metadata.
    Called via run_in_executor.
    """
    opts = {
        **_SUBTITLE_OPTS,
        "extractor_args": {
            "youtube": {"player_client": ["android", "mweb", "web"]},
        },
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            if not info:
                return {}
            return {
                "subtitles":         info.get("subtitles", {}),
                "automatic_captions": info.get("automatic_captions", {}),
            }
    except Exception as exc:
        logger.warning("yt-dlp subtitle metadata extraction failed: %s", exc)
        return {}


class LyricsService:
    """
    Fetches and parses subtitles for a given YouTube URL.

    Usage:
        service = LyricsService()
        result  = await service.get_lyrics(track.url, http_session)
        if result:
            lines, is_auto = result
    """

    async def get_lyrics(
        self,
        video_url: str,
        session:   "aiohttp.ClientSession",
    ) -> Optional[tuple[list[LyricLine], bool]]:
        """
        Resolve subtitles for a YouTube video URL.

        Returns:
            (lines, is_auto_caption) on success.
            None if no subtitles are available or fetching fails.
        """
        loop = asyncio.get_running_loop()

        # Step 1: extract subtitle metadata (blocking yt-dlp in thread)
        logger.debug("Fetching subtitle metadata for %s", video_url[:80])
        meta = await asyncio.wait_for(
            loop.run_in_executor(None, _extract_subtitle_metadata, video_url),
            timeout=20.0,
        )

        subs_dict  = meta.get("subtitles", {})
        auto_dict  = meta.get("automatic_captions", {})

        if not subs_dict and not auto_dict:
            logger.info("No subtitles available for %s", video_url[:80])
            return None

        # Step 2: pick the best subtitle URL
        sub_url, is_auto = _pick_subtitle_url(subs_dict, auto_dict)
        if not sub_url:
            return None

        logger.debug("Downloading subtitle from %s (auto=%s)", sub_url[:80], is_auto)

        # Step 3: download subtitle file content
        raw = await _fetch_subtitle_text(sub_url, session)
        if not raw:
            return None

        # Step 4: parse based on format
        raw_lower = raw.lstrip().lower()
        if raw_lower.startswith("webvtt"):
            lines = parse_vtt(raw)
        else:
            # Try SRT fallback
            lines = parse_srt(raw)

        if not lines:
            logger.info("Subtitle parsed but produced 0 lines for %s", video_url[:80])
            return None

        logger.info("Parsed %d lyric lines for %s (auto=%s)", len(lines), video_url[:80], is_auto)
        return lines, is_auto
