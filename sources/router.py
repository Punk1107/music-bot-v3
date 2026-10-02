# -*- coding: utf-8 -*-
"""
sources/router.py — Multi-source URL/query router for Music Bot V3 Feature 1.3.

Detects the appropriate extractor based on URL domain or query prefix, then
returns Track(s) and resolves stream URLs consistently.

Supported sources via this router:
  - SoundCloud: URL or scsearch: prefix
  - Bandcamp:   *.bandcamp.com URLs

This module is imported by sources/cog.py and can be called from cogs/music.py
to extend the /play command transparently.
"""

from __future__ import annotations

import logging
from enum import Enum, auto
from typing import Optional, TYPE_CHECKING

from sources.soundcloud import SoundCloudExtractor, is_soundcloud_url, is_soundcloud_query
from sources.bandcamp   import BandcampExtractor,   is_bandcamp_url,   is_bandcamp_album
from models.track import Track

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class SourceKind(Enum):
    SOUNDCLOUD        = auto()
    SOUNDCLOUD_SET    = auto()
    SOUNDCLOUD_SEARCH = auto()
    BANDCAMP_TRACK    = auto()
    BANDCAMP_ALBUM    = auto()
    UNKNOWN           = auto()


def classify(query: str) -> SourceKind:
    """Determine what kind of source a query/URL belongs to."""
    q = query.strip()

    if is_soundcloud_query(q):
        return SourceKind.SOUNDCLOUD_SEARCH

    if is_soundcloud_url(q):
        # Sets are playlist-style: soundcloud.com/artist/sets/name
        if "/sets/" in q:
            return SourceKind.SOUNDCLOUD_SET
        return SourceKind.SOUNDCLOUD

    if is_bandcamp_url(q):
        if is_bandcamp_album(q):
            return SourceKind.BANDCAMP_ALBUM
        return SourceKind.BANDCAMP_TRACK

    return SourceKind.UNKNOWN


class MultiSourceRouter:
    """
    Unified entry point for resolving SoundCloud and Bandcamp content to Track(s).

    Usage:
        router = MultiSourceRouter()
        kind   = router.classify(query)
        tracks = await router.resolve(query, max_tracks=50)
        if tracks:
            stream_url = await router.get_stream_url(tracks[0])
    """

    def __init__(self) -> None:
        self._sc = SoundCloudExtractor()
        self._bc = BandcampExtractor()

    def is_supported(self, query: str) -> bool:
        """Return True if this router can handle the given query/URL."""
        return classify(query) != SourceKind.UNKNOWN

    async def resolve(
        self,
        query:      str,
        max_tracks: int = 50,
    ) -> list[Track]:
        """
        Resolve a SoundCloud or Bandcamp query to one or more Track objects.

        Returns an empty list on failure.
        """
        kind = classify(query)
        logger.info("MultiSourceRouter: kind=%s for %r", kind.name, query[:80])

        try:
            if kind == SourceKind.SOUNDCLOUD_SEARCH:
                return await self._sc.search(query, max_results=5)

            elif kind == SourceKind.SOUNDCLOUD:
                track = await self._sc.get_track(query)
                return [track] if track else []

            elif kind == SourceKind.SOUNDCLOUD_SET:
                return await self._sc.get_set(query, max_tracks=max_tracks)

            elif kind == SourceKind.BANDCAMP_TRACK:
                track = await self._bc.get_track(query)
                return [track] if track else []

            elif kind == SourceKind.BANDCAMP_ALBUM:
                return await self._bc.get_album(query, max_tracks=max_tracks)

        except Exception as exc:
            logger.error("MultiSourceRouter.resolve failed for %r: %s", query[:60], exc)

        return []

    async def get_stream_url(self, track: Track) -> Optional[str]:
        """
        Resolve the direct audio stream URL for a Track that came from
        SoundCloud or Bandcamp.

        Falls back to track.url if the source can't be determined.
        """
        url  = track.url
        kind = classify(url)

        try:
            if kind in (SourceKind.SOUNDCLOUD, SourceKind.SOUNDCLOUD_SET):
                return await self._sc.get_stream_url(url)
            elif kind in (SourceKind.BANDCAMP_TRACK, SourceKind.BANDCAMP_ALBUM):
                return await self._bc.get_stream_url(url)
        except Exception as exc:
            logger.error("MultiSourceRouter.get_stream_url failed for %r: %s", url[:60], exc)

        # Fallback: return the URL as-is (yt-dlp may have already embedded a direct CDN URL)
        return url
