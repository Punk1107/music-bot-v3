# -*- coding: utf-8 -*-
"""models/track.py — Track dataclass for Music Bot V3."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Track:
    """
    Immutable-ish data class representing one audio track.

    V3 additions:
      - requested_by_id / requested_by_name for attribution
      - is_favorite flag for favorites system
      - stream_url_cache / stream_url_expires for predictive prefetch
    """

    title:       str
    url:         str
    duration:    int = 0         # seconds
    thumbnail:   Optional[str] = None
    uploader:    str = "Unknown"
    view_count:  Optional[int] = None
    upload_date: Optional[str] = None

    # Request attribution
    requested_by_id:   Optional[int] = None
    requested_by_name: Optional[str] = None

    # Favorites flag (set by FavoritesManager at enqueue time)
    is_favorite: bool = False

    # Predictive pre-fetch cache (not serialised to DB)
    stream_url_cache:   Optional[str]   = field(default=None, repr=False)
    stream_url_expires: Optional[float] = field(default=None, repr=False)

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Serialise to a JSON-safe dict (for DB storage)."""
        return {
            "title":             self.title,
            "url":               self.url,
            "duration":          self.duration,
            "thumbnail":         self.thumbnail,
            "uploader":          self.uploader,
            "view_count":        self.view_count,
            "upload_date":       self.upload_date,
            "requested_by_id":   self.requested_by_id,
            "requested_by_name": self.requested_by_name,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict) -> "Track":
        return cls(
            title             = data.get("title", "Unknown"),
            url               = data.get("url", ""),
            duration          = int(data.get("duration") or 0),
            thumbnail         = data.get("thumbnail"),
            uploader          = data.get("uploader", "Unknown"),
            view_count        = data.get("view_count"),
            upload_date       = data.get("upload_date"),
            requested_by_id   = data.get("requested_by_id"),
            requested_by_name = data.get("requested_by_name"),
        )

    @classmethod
    def from_json(cls, s: str) -> "Track":
        return cls.from_dict(json.loads(s))

    # ── Helpers ───────────────────────────────────────────────────────────────

    @property
    def duration_str(self) -> str:
        """HH:MM:SS or MM:SS formatted duration."""
        secs = max(0, int(self.duration))
        h, rem = divmod(secs, 3600)
        m, s   = divmod(rem, 60)
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    @property
    def short_title(self) -> str:
        """Title truncated to 60 chars."""
        return self.title[:60] + ("…" if len(self.title) > 60 else "")

    def __repr__(self) -> str:
        return f"<Track title={self.title!r} duration={self.duration_str}>"
