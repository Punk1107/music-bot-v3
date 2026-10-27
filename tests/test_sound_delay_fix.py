# -*- coding: utf-8 -*-
"""
tests/test_sound_delay_fix.py — Tests for audio playback startup latency & stream caching optimizations.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import discord

from core.youtube import YouTubeExtractor
from core.audio import AudioEffectsProcessor
from sources.soundcloud import _entry_to_track as sc_entry_to_track
from sources.bandcamp import _entry_to_track as bc_entry_to_track
from sources.router import MultiSourceRouter
from models.track import Track
import config


class TestPlaybackStartupLatency:
    """Verify stream URL extraction, caching, and FFmpeg options for low latency playback."""

    def test_ffmpeg_before_options_includes_low_latency_probesize(self):
        """AudioEffectsProcessor includes -probesize 65536 and -analyzeduration 0 to prevent network stalls."""
        proc = AudioEffectsProcessor()
        opts = proc.build_ffmpeg_options()
        before = opts["before_options"]
        assert "-probesize 65536" in before
        assert "-analyzeduration 0" in before

        cross_opts = proc.build_crossfade_in_options(crossfade_secs=3)
        cross_before = cross_opts["before_options"]
        assert "-probesize 65536" in cross_before
        assert "-analyzeduration 0" in cross_before

    def test_youtube_entry_to_track_caches_stream_url(self):
        """_entry_to_track extracts formats and populates stream_url_cache on Track."""
        mock_entry = {
            "title": "Podcast Episode 1",
            "webpage_url": "https://www.youtube.com/watch?v=mock123",
            "duration": 3600,
            "thumbnail": "https://i.ytimg.com/vi/mock123/default.jpg",
            "uploader": "Host",
            "formats": [
                {
                    "url": "https://googlevideo.com/videoplayback?id=audio1",
                    "acodec": "opus",
                    "vcodec": "none",
                    "abr": 160,
                },
                {
                    "url": "https://googlevideo.com/videoplayback?id=video1",
                    "acodec": "none",
                    "vcodec": "vp9",
                },
            ],
        }

        track = YouTubeExtractor._entry_to_track(mock_entry)
        assert track is not None
        assert track.title == "Podcast Episode 1"
        assert track.stream_url_cache == "https://googlevideo.com/videoplayback?id=audio1"
        assert track.stream_url_expires is not None
        assert track.stream_url_expires > time.monotonic()

    @pytest.mark.asyncio
    async def test_youtube_get_stream_url_uses_cached_url_instantaneously(self):
        """get_stream_url does not call _run_ytdl if track already has valid cached stream URL."""
        yt = YouTubeExtractor()
        track = Track(
            title="Podcast",
            url="https://www.youtube.com/watch?v=mock123",
            duration=1800,
            stream_url_cache="https://googlevideo.com/videoplayback?id=cached_audio",
            stream_url_expires=time.monotonic() + 3600,
        )

        with patch.object(yt, "_run_ytdl") as mock_run:
            url = await yt.get_stream_url(track.url, track)
            assert url == "https://googlevideo.com/videoplayback?id=cached_audio"
            mock_run.assert_not_called()

    def test_soundcloud_entry_to_track_caches_stream_url(self):
        """SoundCloud _entry_to_track caches direct stream URL from audio format."""
        mock_entry = {
            "title": "SoundCloud Track",
            "webpage_url": "https://soundcloud.com/artist/track",
            "duration": 240,
            "uploader": "Artist",
            "formats": [
                {
                    "url": "https://cf-media.sndcdn.com/stream.mp3",
                    "acodec": "mp3",
                    "abr": 128,
                }
            ],
        }
        track = sc_entry_to_track(mock_entry)
        assert track is not None
        assert track.stream_url_cache == "https://cf-media.sndcdn.com/stream.mp3"
        assert track.stream_url_expires is not None

    def test_bandcamp_entry_to_track_caches_stream_url(self):
        """Bandcamp _entry_to_track caches direct stream URL from audio format."""
        mock_entry = {
            "title": "Bandcamp Song",
            "webpage_url": "https://artist.bandcamp.com/track/song",
            "duration": 300,
            "uploader": "Band",
            "formats": [
                {
                    "url": "https://bandcamp.com/stream.mp3",
                    "acodec": "mp3",
                    "abr": 128,
                }
            ],
        }
        track = bc_entry_to_track(mock_entry)
        assert track is not None
        assert track.stream_url_cache == "https://bandcamp.com/stream.mp3"
        assert track.stream_url_expires is not None

    @pytest.mark.asyncio
    async def test_sources_router_get_stream_url_uses_cache(self):
        """MultiSourceRouter.get_stream_url returns stream_url_cache if valid."""
        router = MultiSourceRouter()
        track = Track(
            title="SC Song",
            url="https://soundcloud.com/artist/song",
            duration=200,
            stream_url_cache="https://cf-media.sndcdn.com/cached.mp3",
            stream_url_expires=time.monotonic() + 3600,
        )
        url = await router.get_stream_url(track)
        assert url == "https://cf-media.sndcdn.com/cached.mp3"
