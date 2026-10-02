# -*- coding: utf-8 -*-
"""
chapters/detector.py — Chapter detection from yt-dlp metadata for Music Bot V3 Feature 1.2.

Strategy:
  1. Primary: read `chapters` list from yt-dlp info dict
     (each entry has "title", "start_time", "end_time")
  2. Fallback: scan the video description for common "timestamp list" patterns
     e.g.  "0:00 Intro"  "01:23 Song Title"  "1:23:45 Outro"

Returns a list of Chapter dataclasses.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Optional

import yt_dlp

logger = logging.getLogger(__name__)


@dataclass
class Chapter:
    """Represents a single chapter in a video."""
    index:        int      # 1-based display index
    title:        str
    start_sec:    float
    end_sec:      float    # 0.0 if unknown
    duration_sec: float    # end_sec - start_sec  (0.0 if unknown)

    @property
    def start_str(self) -> str:
        """Format start time as MM:SS or HH:MM:SS."""
        return _format_ts(self.start_sec)

    @property
    def end_str(self) -> str:
        """Format end time as MM:SS or HH:MM:SS."""
        if self.end_sec > 0:
            return _format_ts(self.end_sec)
        return "?"

    @property
    def duration_str(self) -> str:
        if self.duration_sec > 0:
            return _format_ts(self.duration_sec)
        return "?"

    @property
    def label(self) -> str:
        """Short label for Dropdown: '[MM:SS] Title'."""
        return f"[{self.start_str}] {self.title}"[:100]


def _format_ts(total_sec: float) -> str:
    sec  = int(total_sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


# ── Description timestamp Regex ──────────────────────────────────────────────
# Matches lines like:
#   "0:00 Intro"
#   "01:23 - Song Name"
#   "1:23:45 Outro"
#   "(2:30) Chapter 3"
_DESC_TIMESTAMP_RE = re.compile(
    r"^\s*[\[(]?(\d{1,2}):(\d{2})(?::(\d{2}))?[\])]?\s*[-–—]?\s*(.+)$",
    re.MULTILINE,
)


def _chapters_from_metadata(info: dict, video_duration: float) -> list[Chapter]:
    """Build Chapter list from yt-dlp chapters list."""
    raw_chapters = info.get("chapters") or []
    if not raw_chapters:
        return []

    chapters: list[Chapter] = []
    for i, ch in enumerate(raw_chapters, start=1):
        start = float(ch.get("start_time") or 0)
        end   = float(ch.get("end_time")   or 0)
        title = (ch.get("title") or f"Chapter {i}").strip()
        chapters.append(Chapter(
            index        = i,
            title        = title,
            start_sec    = start,
            end_sec      = end,
            duration_sec = max(0.0, end - start),
        ))
    return chapters


def _chapters_from_description(description: str, video_duration: float) -> list[Chapter]:
    """
    Fallback: parse timestamp list from the video description.
    Returns an empty list if no recognisable timestamps are found.
    Requires at least 2 matches to avoid false positives.
    """
    matches = _DESC_TIMESTAMP_RE.findall(description or "")
    if len(matches) < 2:
        return []

    raw: list[tuple[float, str]] = []
    for h_or_m, m_or_s, s_part, title in matches:
        if s_part:
            # HH:MM:SS form
            sec = int(h_or_m) * 3600 + int(m_or_s) * 60 + int(s_part)
        else:
            # MM:SS form
            sec = int(h_or_m) * 60 + int(m_or_s)
        raw.append((float(sec), title.strip()))

    # Sort by time ascending (descriptions aren't always ordered)
    raw.sort(key=lambda x: x[0])

    chapters: list[Chapter] = []
    for i, (start, title) in enumerate(raw, start=1):
        # end_sec is the start of the next chapter (or video end)
        if i < len(raw):
            end = raw[i][0]       # raw is 0-indexed, next entry is index i
        else:
            end = video_duration if video_duration > 0 else 0.0

        chapters.append(Chapter(
            index        = i,
            title        = title,
            start_sec    = start,
            end_sec      = end,
            duration_sec = max(0.0, end - start),
        ))

    return chapters


# ── yt-dlp extraction ────────────────────────────────────────────────────────

_CHAPTER_OPTS: dict = {
    "quiet":              True,
    "no_warnings":        True,
    "ignoreerrors":       True,
    "nocheckcertificate": True,
    "skip_download":      True,
    "extract_flat":       False,
    "noplaylist":         True,
    "extractor_args": {
        "youtube": {"player_client": ["android", "mweb", "web"]},
    },
}


def _fetch_chapter_metadata(video_url: str) -> dict:
    """Synchronous yt-dlp call — run via run_in_executor."""
    try:
        with yt_dlp.YoutubeDL(_CHAPTER_OPTS) as ydl:
            info = ydl.extract_info(video_url, download=False)
            if not info:
                return {}
            return {
                "chapters":    info.get("chapters") or [],
                "description": info.get("description") or "",
                "duration":    info.get("duration") or 0,
            }
    except Exception as exc:
        logger.warning("Chapter metadata extraction failed: %s", exc)
        return {}


class ChapterDetector:
    """
    Detect chapters for a YouTube video URL.

    Usage:
        detector = ChapterDetector()
        chapters = await detector.get_chapters(track.url, fallback_duration=track.duration)
    """

    async def get_chapters(
        self,
        video_url:         str,
        fallback_duration: float = 0.0,
    ) -> list[Chapter]:
        """
        Return the chapter list for a video, or an empty list if none are found.

        Tries yt-dlp metadata first, then description timestamp fallback.
        """
        loop = asyncio.get_running_loop()

        try:
            meta = await asyncio.wait_for(
                loop.run_in_executor(None, _fetch_chapter_metadata, video_url),
                timeout=20.0,
            )
        except asyncio.TimeoutError:
            logger.warning("Chapter metadata fetch timed out for %s", video_url[:80])
            return []

        if not meta:
            return []

        duration = float(meta.get("duration") or fallback_duration or 0)

        # 1. Try structured chapters from metadata
        chapters = _chapters_from_metadata(meta, duration)
        if chapters:
            logger.info("Found %d structured chapters for %s", len(chapters), video_url[:60])
            return chapters

        # 2. Fallback: scan description
        chapters = _chapters_from_description(meta.get("description", ""), duration)
        if chapters:
            logger.info("Found %d description-parsed chapters for %s", len(chapters), video_url[:60])
        else:
            logger.info("No chapters found for %s", video_url[:60])

        return chapters
