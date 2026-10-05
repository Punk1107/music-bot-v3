# -*- coding: utf-8 -*-
"""
tests/test_perf_stability_i18n.py — Unit tests for Performance, Stability, and Universal 9-Language i18n Parity.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import discord

from core.i18n import (
    t,
    get_locale_sync,
    set_locale_cache,
    supported_locales,
    _STRINGS,
)
from utils.error_handler import (
    classify_error,
    get_error_category,
    playback_error_embed,
    voice_connection_error_embed,
    dj_required_embed,
    rate_limited_embed,
)
from utils.embeds import bookmark_loaded_embed
from utils.color_thief import (
    _COLOR_CACHE,
    _get_cache_lock,
    get_dominant_color,
    color_cache_stats,
)


ALL_LOCALES = ["en", "th", "zh_cn", "ja", "ko", "es", "ru", "fr", "de"]


class TestUniversalI18nParity:
    """Verify that every supported locale has complete error handler and embed coverage."""

    @pytest.mark.parametrize("locale", ALL_LOCALES)
    def test_playback_error_embed_all_locales(self, locale: str):
        error_cases = [
            ("copyright blocked video", "copyright"),
            ("Sign in to confirm your age", "age_restricted"),
            ("Video is private", "private_video"),
            ("This video is unavailable", "video_unavailable"),
            ("HTTP Error 429: Too Many Requests", "rate_limited_yt"),
            ("network connection timeout", "network"),
            ("random unclassified error message", "playback_fallback"),
        ]

        for err_msg, expected_category in error_cases:
            embed = playback_error_embed(err_msg, locale=locale)
            assert embed.title is not None
            assert embed.description is not None
            expected_title = t(f"error.{expected_category}.title", locale)
            expected_desc = t(f"error.{expected_category}.desc", locale)
            assert expected_title in embed.title
            assert embed.description == expected_desc

    @pytest.mark.parametrize("locale", ALL_LOCALES)
    def test_voice_connection_error_embed_all_locales(self, locale: str):
        embed = voice_connection_error_embed("Stage Room", 3, locale=locale)
        assert embed.title == t("error.voice_reconnect_title", locale)
        assert "Stage Room" in embed.description
        assert "3" in embed.description

    @pytest.mark.parametrize("locale", ALL_LOCALES)
    def test_dj_required_embed_all_locales(self, locale: str):
        embed = dj_required_embed(locale=locale)
        assert embed.title == t("error.dj_required_title", locale)
        assert embed.description == t("error.dj_required", locale)

    @pytest.mark.parametrize("locale", ALL_LOCALES)
    def test_rate_limited_embed_all_locales(self, locale: str):
        embed = rate_limited_embed(3.5, locale=locale)
        assert embed.title == t("error.rate_limit_title", locale)
        assert "3.5" in embed.description

    def test_bookmark_loaded_embed_no_raw_english_mode_in_other_languages(self):
        """Ensure {mode} does not inject English words like 'replaced' into Thai/Japanese/German."""
        # Thai check
        th_embed_replace = bookmark_loaded_embed("MyList", 5, mode="replace", locale="th")
        assert "แทนที่" in th_embed_replace.description
        assert "replaced" not in th_embed_replace.description

        th_embed_append = bookmark_loaded_embed("MyList", 5, mode="append", locale="th")
        assert "เพิ่มต่อท้าย" in th_embed_append.description
        assert "appended" not in th_embed_append.description

        # Japanese check
        ja_embed_replace = bookmark_loaded_embed("MyList", 5, mode="replace", locale="ja")
        assert "を置き換えました" in ja_embed_replace.description
        assert "replaced" not in ja_embed_replace.description

        # German check
        de_embed_replace = bookmark_loaded_embed("MyList", 5, mode="replace", locale="de")
        assert "ersetzte" in de_embed_replace.description
        assert "replaced" not in de_embed_replace.description

        # English check
        en_embed_replace = bookmark_loaded_embed("MyList", 5, mode="replace", locale="en")
        assert "replaced" in en_embed_replace.description


class TestConcurrencyAndStability:
    """Stress-test concurrent cache access and task lifecycle."""

    @pytest.mark.asyncio
    async def test_color_thief_concurrency_stress(self):
        """Simulate high concurrency access, eviction, and mutation to _COLOR_CACHE."""
        cache_lock = _get_cache_lock()
        async with cache_lock:
            _COLOR_CACHE.clear()

        # Seed with initial entries
        for i in range(50):
            _COLOR_CACHE[f"http://example.com/img{i}.jpg"] = ((i, i, i), time.monotonic() - 10)

        # Worker to simulate concurrent access and mutations
        async def worker(idx: int):
            for step in range(20):
                url = f"http://example.com/img{(idx + step) % 60}.jpg"
                async with cache_lock:
                    if url in _COLOR_CACHE:
                        # hit or TTL eviction
                        color, ts = _COLOR_CACHE[url]
                        if idx % 5 == 0:
                            del _COLOR_CACHE[url]
                    else:
                        # Insert
                        _COLOR_CACHE[url] = ((idx, step, 100), time.monotonic())
                        if len(_COLOR_CACHE) > 40:
                            oldest = min(_COLOR_CACHE, key=lambda k: _COLOR_CACHE[k][1])
                            _COLOR_CACHE.pop(oldest, None)
                await asyncio.sleep(0.001)

        # Run 25 concurrent workers
        await asyncio.gather(*[worker(w) for w in range(25)])

        stats = color_cache_stats()
        assert stats["size"] <= 50

    @pytest.mark.asyncio
    async def test_musicbot_track_task_lifecycle(self):
        from main import MusicBot

        bot = MagicMock(spec=MusicBot)
        bot._background_tasks = set()
        bot.track_task = MusicBot.track_task.__get__(bot, MusicBot)

        completed = False

        async def sample_coro():
            nonlocal completed
            await asyncio.sleep(0.01)
            completed = True

        task = bot.track_task(sample_coro(), name="test_sample_task")
        assert task in bot._background_tasks
        await task
        assert completed is True
        # After completion done_callback removes it
        assert task not in bot._background_tasks

    @pytest.mark.asyncio
    async def test_musicbot_track_task_exception_handling(self):
        from main import MusicBot

        bot = MagicMock(spec=MusicBot)
        bot._background_tasks = set()
        bot.track_task = MusicBot.track_task.__get__(bot, MusicBot)

        async def failing_coro():
            await asyncio.sleep(0.01)
            raise ValueError("Deliberate background error")

        task = bot.track_task(failing_coro(), name="test_failing_task")
        # Await with return_exceptions or gather
        with pytest.raises(ValueError, match="Deliberate background error"):
            await task
        assert task not in bot._background_tasks

    @pytest.mark.asyncio
    async def test_spotify_auth_header_format(self):
        from core.spotify import SpotifyExtractor
        import base64

        sp = SpotifyExtractor()
        sp._available = True
        sp._token = None
        sp._token_expires_at = 0.0

        mock_resp = MagicMock()
        mock_resp.json = AsyncMock(return_value={"access_token": "valid_token_123", "expires_in": 3600})

        captured_kwargs = {}

        async def mock_post(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return mock_resp

        mock_session = MagicMock()
        mock_session.post = AsyncMock(side_effect=mock_post)

        token = await sp._get_token(mock_session)
        assert token == "valid_token_123"
        assert "headers" in captured_kwargs
        assert "Authorization" in captured_kwargs["headers"]
        auth_val = captured_kwargs["headers"]["Authorization"]
        assert auth_val.startswith("Basic ")
        # auth keyword parameter should NOT be used to avoid aiohttp deprecation
        assert "auth" not in captured_kwargs
