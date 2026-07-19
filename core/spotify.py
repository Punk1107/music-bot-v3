# -*- coding: utf-8 -*-
"""
core/spotify.py — Spotify → YouTube resolver for Music Bot V3.

Converts Spotify track/album/playlist URLs to YouTube search queries
then resolves them via YouTubeExtractor. Graceful no-op if credentials absent.

Uses asyncio.Semaphore(5) to limit concurrent YouTube lookups.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Optional

import aiohttp

import config

if TYPE_CHECKING:
    from core.youtube import YouTubeExtractor
    from models.track import Track

logger = logging.getLogger(__name__)

_SPOTIFY_API = "https://api.spotify.com/v1"
_TOKEN_URL   = "https://accounts.spotify.com/api/token"

_TRACK_RE    = re.compile(r"spotify\.com/track/([A-Za-z0-9]+)")
_ALBUM_RE    = re.compile(r"spotify\.com/album/([A-Za-z0-9]+)")
_PLAYLIST_RE = re.compile(r"spotify\.com/playlist/([A-Za-z0-9]+)")


class SpotifyExtractor:
    """
    Converts Spotify content URLs to a list of Track objects via YouTube.

    If SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET are absent, all methods
    return empty lists silently.
    """

    def __init__(self) -> None:
        self._token:     Optional[str] = None
        self._token_lock: asyncio.Lock = asyncio.Lock()
        self._sem        = asyncio.Semaphore(5)
        self._available  = bool(config.SPOTIFY_CLIENT_ID and config.SPOTIFY_CLIENT_SECRET)

    def is_available(self) -> bool:
        return self._available

    @staticmethod
    def is_spotify_url(url: str) -> bool:
        return "spotify.com/" in url

    # ── Auth ─────────────────────────────────────────────────────────────────

    async def _get_token(self, session: aiohttp.ClientSession) -> Optional[str]:
        async with self._token_lock:
            if self._token:
                return self._token
            try:
                resp = await session.post(
                    _TOKEN_URL,
                    data={"grant_type": "client_credentials"},
                    auth=aiohttp.BasicAuth(config.SPOTIFY_CLIENT_ID, config.SPOTIFY_CLIENT_SECRET),
                    timeout=aiohttp.ClientTimeout(total=10),
                )
                data = await resp.json()
                self._token = data.get("access_token")
                return self._token
            except Exception as exc:
                logger.error("Spotify auth failed: %s", exc)
                return None

    async def _invalidate_token(self) -> None:
        async with self._token_lock:
            self._token = None

    # ── API requests ─────────────────────────────────────────────────────────

    async def _api_get(
        self, session: aiohttp.ClientSession, endpoint: str
    ) -> Optional[dict]:
        token = await self._get_token(session)
        if not token:
            return None
        try:
            resp = await session.get(
                f"{_SPOTIFY_API}/{endpoint}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=15),
            )
            if resp.status == 401:
                await self._invalidate_token()
                return None
            if not resp.ok:
                logger.warning("Spotify API %s returned %d", endpoint, resp.status)
                return None
            return await resp.json()
        except Exception as exc:
            logger.error("Spotify API get '%s' failed: %s", endpoint, exc)
            return None

    # ── Resolvers ─────────────────────────────────────────────────────────────

    async def _resolve_track_item(
        self, item: dict, youtube: "YouTubeExtractor"
    ) -> Optional["Track"]:
        """Convert one Spotify track dict to a Track via YouTube search."""
        name    = item.get("name", "")
        artists = ", ".join(a["name"] for a in item.get("artists", []))
        query   = f"{name} {artists}".strip()
        if not query:
            return None
        async with self._sem:
            results = await youtube.search(query, max_results=1)
            return results[0] if results else None

    async def resolve(
        self,
        url:     str,
        session: aiohttp.ClientSession,
        youtube: "YouTubeExtractor",
        max_tracks: int = 50,
    ) -> list["Track"]:
        """
        Resolve a Spotify URL to a list of Track objects.

        Supports:
          - spotify.com/track/...      → 1 track
          - spotify.com/album/...      → up to max_tracks tracks
          - spotify.com/playlist/...   → up to max_tracks tracks
        """
        if not self._available:
            return []

        track_m    = _TRACK_RE.search(url)
        album_m    = _ALBUM_RE.search(url)
        playlist_m = _PLAYLIST_RE.search(url)

        if track_m:
            data = await self._api_get(session, f"tracks/{track_m.group(1)}")
            if not data:
                return []
            track = await self._resolve_track_item(data, youtube)
            return [track] if track else []

        elif album_m:
            data = await self._api_get(
                session, f"albums/{album_m.group(1)}/tracks?limit={min(max_tracks, 50)}"
            )
            if not data:
                return []
            items = data.get("items", [])

        elif playlist_m:
            data = await self._api_get(
                session, f"playlists/{playlist_m.group(1)}/tracks?limit={min(max_tracks, 50)}"
            )
            if not data:
                return []
            items = [item["track"] for item in data.get("items", []) if item.get("track")]

        else:
            return []

        # Resolve concurrently (semaphore inside _resolve_track_item)
        tasks = [self._resolve_track_item(item, youtube) for item in items[:max_tracks]]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [t for t in results if isinstance(t, object) and t is not None and not isinstance(t, Exception)]
