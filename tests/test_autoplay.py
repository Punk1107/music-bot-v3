# -*- coding: utf-8 -*-
"""
tests/test_autoplay.py — Unit tests for Feature 1.4: Smart Autoplay & Feature 1.6: Cookie/PO-Token.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from models.track import Track
from models.server_config import ServerConfig
from autoplay.service import (
    AutoplayService,
    extract_video_id,
    _extract_video_id,
    _radio_url,
    _entry_to_track,
)
from autoplay import extract_video_id as pkg_extract_video_id
from utils.embeds import smart_autoplay_embed, autoplay_embed
import config


# ── 1. Video ID Extraction ───────────────────────────────────────────────────

class TestVideoIdExtraction:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("http://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDdQw4w9WgXcQ&index=2", "dQw4w9WgXcQ"),
            ("dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("oHg5SJYRHA0", "oHg5SJYRHA0"),
        ],
    )
    def test_extract_video_id_valid(self, url: str, expected: str):
        assert extract_video_id(url) == expected
        assert _extract_video_id(url) == expected
        assert pkg_extract_video_id(url) == expected
        assert AutoplayService.extract_video_id(url) == expected

    @pytest.mark.parametrize(
        "url",
        [
            "https://soundcloud.com/artist/track-name",
            "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT",
            "https://bandcamp.com/album/something",
            "https://example.com",
            "",
            "   ",
            "short",
        ],
    )
    def test_extract_video_id_invalid(self, url: str):
        assert extract_video_id(url) is None


# ── 2. Radio URL & Track Parsing ─────────────────────────────────────────────

class TestRadioUrlAndParsing:
    def test_radio_url(self):
        url = _radio_url("dQw4w9WgXcQ")
        assert url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDdQw4w9WgXcQ"

    def test_entry_to_track_valid(self):
        entry = {
            "id": "dQw4w9WgXcQ",
            "title": "Never Gonna Give You Up",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "duration": 213,
            "uploader": "Rick Astley",
            "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/default.jpg",
        }
        track = _entry_to_track(entry)
        assert track is not None
        assert track.title == "Never Gonna Give You Up"
        assert track.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert track.duration == 213
        assert track.uploader == "Rick Astley"

    def test_entry_to_track_too_long(self, monkeypatch):
        monkeypatch.setattr(config, "MAX_TRACK_LENGTH", 300)
        entry = {
            "id": "dQw4w9WgXcQ",
            "title": "Long Track",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "duration": 600,
        }
        assert _entry_to_track(entry) is None

    def test_entry_to_track_missing_title(self):
        entry = {
            "id": "dQw4w9WgXcQ",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        }
        assert _entry_to_track(entry) is None


# ── 3. AutoplayService Dedup & Next Track ─────────────────────────────────────

@pytest.fixture
def mock_youtube():
    yt = MagicMock()
    yt.search = AsyncMock()
    return yt


@pytest.fixture
def autoplay_service(mock_youtube):
    return AutoplayService(mock_youtube)


class TestAutoplayService:
    @pytest.mark.asyncio
    async def test_history_dedup_and_reset(self, autoplay_service):
        guild_id = 12345
        autoplay_service._mark_seen(guild_id, "vid1")
        autoplay_service._mark_seen(guild_id, "vid2")

        assert autoplay_service._is_seen(guild_id, "vid1") is True
        assert autoplay_service._is_seen(guild_id, "vid2") is True
        assert autoplay_service._is_seen(guild_id, "vid3") is False

        await autoplay_service.reset_history(guild_id)
        assert autoplay_service._is_seen(guild_id, "vid1") is False

    @pytest.mark.asyncio
    async def test_get_next_track_none_seed(self, autoplay_service):
        track = await autoplay_service.get_next_track(12345, None)
        assert track is None

    @pytest.mark.asyncio
    async def test_get_next_track_youtube_seed(self, autoplay_service):
        guild_id = 999
        seed = Track(
            title="Seed Song",
            url="https://www.youtube.com/watch?v=seed1234567",
            duration=180,
        )

        fake_related = [
            Track(title="Related 1", url="https://www.youtube.com/watch?v=rel11111111", duration=200),
            Track(title="Related 2", url="https://www.youtube.com/watch?v=rel22222222", duration=210),
        ]

        with patch.object(autoplay_service, "fetch_related_tracks", AsyncMock(return_value=fake_related)):
            # First pick
            next1 = await autoplay_service.get_next_track(guild_id, seed)
            assert next1 is not None
            assert next1.title == "Related 1"

            # Second pick with same seed should pick next unplayed
            next2 = await autoplay_service.get_next_track(guild_id, seed)
            assert next2 is not None
            assert next2.title == "Related 2"

            # Third pick: both are seen, returns None
            next3 = await autoplay_service.get_next_track(guild_id, seed)
            assert next3 is None

    @pytest.mark.asyncio
    async def test_get_next_track_spotify_seed_searches_youtube(self, autoplay_service, mock_youtube):
        guild_id = 888
        seed = Track(
            title="Blinding Lights",
            uploader="The Weeknd",
            url="https://open.spotify.com/track/0VjIjW4GlUZAMYd2vXMi3b",
            duration=200,
        )

        mock_youtube.search.return_value = [
            Track(
                title="The Weeknd - Blinding Lights (Official Video)",
                url="https://www.youtube.com/watch?v=4NRXx6U8ABQ",
                duration=200,
            )
        ]

        fake_related = [
            Track(title="Save Your Tears", url="https://www.youtube.com/watch?v=XXYlFuWEuKI", duration=215),
        ]

        with patch.object(autoplay_service, "fetch_related_tracks", AsyncMock(return_value=fake_related)):
            res = await autoplay_service.get_next_track(guild_id, seed)
            assert res is not None
            assert res.title == "Save Your Tears"
            mock_youtube.search.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fetch_related_tracks_handles_error(self, autoplay_service):
        with patch.object(autoplay_service, "_run_ytdl", side_effect=Exception("Network error")):
            tracks = await autoplay_service.fetch_related_tracks("dQw4w9WgXcQ", limit=5)
            assert tracks == []


# ── 4. Embeds & Model Verification ───────────────────────────────────────────

class TestAutoplayEmbedAndConfig:
    def test_smart_autoplay_embed(self):
        next_track = Track(
            title="Next Up Track",
            url="https://www.youtube.com/watch?v=rel11111111",
            thumbnail="https://example.com/thumb.jpg",
        )
        seed_track = Track(
            title="Previous Seed Track",
            url="https://www.youtube.com/watch?v=seed1234567",
        )

        embed = smart_autoplay_embed(next_track, seed_track)
        assert "Next Up Track" in embed.description
        assert "Previous Seed Track" in embed.description
        assert embed.title == "📻  Smart Autoplay"
        assert autoplay_embed == smart_autoplay_embed

    def test_server_config_smart_autoplay(self):
        cfg = ServerConfig(guild_id=123)
        assert cfg.smart_autoplay is False

        cfg.smart_autoplay = True
        d = cfg.to_dict()
        assert d["smart_autoplay"] is True

        restored = ServerConfig.from_dict(d)
        assert restored.smart_autoplay is True


# ── 5. Feature 1.6: Cookie & PO-Token Configuration ───────────────────────────

class TestCookieAndPoToken:
    def test_cookie_file_injection(self, tmp_path):
        cookie_file = tmp_path / "dummy_cookies.txt"
        cookie_file.write_text("# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t2147483647\tSID\tdummy_val\n")

        opts = {}
        if cookie_file.is_file():
            opts["cookiefile"] = str(cookie_file)
        assert opts.get("cookiefile") == str(cookie_file)

    def test_cookie_file_fallback_when_missing(self, tmp_path):
        missing_file = tmp_path / "nonexistent_cookies.txt"
        opts = {}
        if missing_file.is_file():
            opts["cookiefile"] = str(missing_file)
        assert "cookiefile" not in opts

    def test_po_token_injection(self):
        opts = {}
        po_token = "dummy_po_token_abc123"
        opts.setdefault("extractor_args", {}).setdefault("youtube", {})["po_token"] = [po_token]
        assert opts["extractor_args"]["youtube"]["po_token"] == ["dummy_po_token_abc123"]

